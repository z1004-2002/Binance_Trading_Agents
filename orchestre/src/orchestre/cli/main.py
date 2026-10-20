"""CLI : check-config, check-keys, kill, status. Aucune commande n'envoie d'ordre.

Codes de sortie : 0 ok, 2 configuration/clés invalides, 3 clé live dangereuse, 4 erreur réseau/API,
5 passage au réel refusé (décision écrite absente, §20).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import asyncio
import os
import sys
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Callable, MutableMapping, TextIO

from orchestre.assistant.agent import AssistantAgent, BudgetExceeded
from orchestre.assistant.groq import GroqClient, GroqError, default_post as groq_default_post
from orchestre.assistant.news import default_fetch as news_default_fetch, pairs_to_pause, parse_rss
from orchestre.assistant.tools import ToolBox
from orchestre.assistant.worker import run_worker
from orchestre.dashboard.data import DashboardData
from orchestre.backup import BackupError, make_backup
from orchestre.lifecycle import CODES, STAGES, LifecycleError, current_stage, history as lifecycle_history, set_stage
from orchestre.launcher import cmd_down, cmd_ps, cmd_up
from orchestre.experiment.protocol import ProtocolError, evaluate, load_protocol, start_experiment, verify_experiment
from orchestre.audit.legacy import SEVERITIES, render_markdown, scan
from orchestre.dashboard.server import DashboardServer
from orchestre.cli.backtest_cmds import (
    DEFAULT_UNIVERSE, WALKFORWARD, cmd_backtest, cmd_portfolio_backtest, cmd_team_backtest, cmd_walkforward,
)
from orchestre.analysis.sentiment import ATTRIBUTION, default_fetch, sentiment_multiplier
from orchestre.analysis.service import AnalysisService
from orchestre.analysis.volatility import vol_target_factor
from orchestre.config import ConfigError, Env, Settings, load_settings
from orchestre.core.clock import SystemClock
from orchestre.core.secrets import mask_secrets
from orchestre.exchange.paper import PaperExchange
from orchestre.notify.telegram import default_post, from_env as telegram_from_env
from orchestre.exchange.binance import BinanceExchange, create_client, create_public_client
from orchestre.exchange.credentials import load_credentials
from orchestre.exchange.key_restrictions import check_key_restrictions
from orchestre.execution.service import ExecutionService
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.alerts import Alerts
from orchestre.runtime.builder import build_team, strategy_symbols
from orchestre.runtime.orchestrator import Orchestrator

SYMBOL = "BTCUSDT"


def load_env_file(path: Path, environ: MutableMapping[str, str], only: set[str] | None = None) -> None:
    """Lit un fichier .env sans écraser l'environnement réel ; ne journalise aucune valeur.

    `only` limite les variables lues (le tableau de bord ne charge jamais de clé d'exchange ni de LLM, EX-UI-08)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if only is None or key.strip() in only:
            environ.setdefault(key.strip(), value.strip().strip("'\""))


def _banner(s: Settings) -> str:
    active = [name for name, on in s.instruments.items() if on] or ["aucun"]
    lines = [
        f"ENVIRONNEMENT : {s.env.name} | dry_run={s.dry_run} | "
        f"plafond={s.max_total_capital_quote} {s.quote_currency} | instruments avancés : {', '.join(active)}"
    ]
    if s.env is Env.LIVE:
        lines.append("!!! ARGENT RÉEL !!! Double confirmation reçue (config + --live).")
    return "\n".join(lines)


def _cmd_check_config(args, settings: Settings, environ, out, err, factory) -> int:
    print(_banner(settings), file=out)
    print("Configuration valide.", file=out)
    return 0


def _cmd_check_keys(args, settings: Settings, environ, out, err, factory) -> int:
    key, secret = load_credentials(settings.env, environ)
    client = factory(settings.env, key, secret, live_confirmed=settings.env is Env.LIVE)
    exchange = BinanceExchange(client, quote=settings.quote_currency)
    print(_banner(settings), file=out)
    if settings.env is Env.LIVE:
        violations = check_key_restrictions(exchange.fetch_key_restrictions(), settings.instruments)
        if violations:
            for v in violations:
                print(f"REFUS (EX-SEC-05) : {v}", file=err)
            return 3
        print("Clé live : restrictions conformes (EX-SEC-05).", file=out)
        return 0
    filters = exchange.get_filters(SYMBOL)
    print(
        f"Connexion OK. {SYMBOL} : prix {exchange.get_price(SYMBOL)}, tickSize {filters.tick_size}, "
        f"stepSize {filters.step_size}, minNotional {filters.min_notional}. "
        f"Actifs avec solde : {len(exchange.get_balances())}.",
        file=out,
    )
    return 0


def _live_gate(args, exchange: BinanceExchange, settings: Settings, err) -> int | None:
    """Porte du réel (EX-SEC-19, §20) : décision écrite et clé conforme. None = feu vert."""
    if settings.env is not Env.LIVE:
        return None
    if not Path(args.decision_file).exists():
        print(f"REFUS (§20.1) : décision écrite absente ({args.decision_file}, docs/decision-live.md).", file=err)
        return 5
    violations = check_key_restrictions(exchange.fetch_key_restrictions(), settings.instruments)
    for v in violations:
        print(f"REFUS (EX-SEC-05) : {v}", file=err)
    return 3 if violations else None


def _paper_exchange(args, settings: Settings, raw: dict) -> PaperExchange:
    """Données de marché réelles (sans clé), ordres et soldes simulés (L0, EX-BT-07)."""
    symbols = sorted(strategy_symbols(raw))
    if not symbols:
        raise ConfigError("aucune stratégie activée (strategies.<nom>.enabled = true)")
    market = BinanceExchange(args.public_client_factory(), quote=settings.quote_currency)
    cash = Decimal(str(args.paper_cash)) if args.paper_cash is not None else settings.max_total_capital_quote
    return PaperExchange(market, symbols, {settings.quote_currency: cash}, quote=settings.quote_currency)


def _cmd_run(args, settings: Settings, environ, out, err, factory) -> int:
    with open(args.config, "rb") as fh:
        raw = tomllib.load(fh)
    if args.paper:
        if args.live:
            raise ConfigError("--paper et --live sont incompatibles")
        exchange = _paper_exchange(args, settings, raw)
        settings = replace(settings, dry_run=False)        # paper : les ordres sont exécutés... dans la simulation
        print("MODE PAPER : données de marché réelles, ordres et soldes SIMULÉS (aucun ordre n'atteint Binance).", file=out)
    else:
        key, secret = load_credentials(settings.env, environ)
        client = factory(settings.env, key, secret, live_confirmed=settings.env is Env.LIVE)
        exchange = BinanceExchange(client, quote=settings.quote_currency)
        refused = _live_gate(args, exchange, settings, err)
        if refused is not None:
            return refused
        exchange.sync_time()
    print(_banner(settings), file=out)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(Database(args.db))
    mode = "paper" if args.paper else "exchange"
    previous = store.load_state("run_mode")
    if previous is not None and previous["mode"] != mode:
        raise ConfigError(f"cette base a été utilisée en mode '{previous['mode']}', pas '{mode}' : utiliser un autre fichier --db")
    ledger = store.load_ledger()
    if ledger is None:
        ledger = Ledger(settings.max_total_capital_quote)
    elif ledger.total != settings.max_total_capital_quote:
        raise ConfigError(
            f"plafond modifié ({ledger.total} en base, {settings.max_total_capital_quote} en configuration) : "
            "toute modification du plafond exige une validation humaine (C-08), repartir d'une base neuve"
        )
    clock = SystemClock()
    telegram = telegram_from_env(environ, post=args.telegram_post)

    def notify(level: str, text: str) -> None:
        print(f"[{level}] {text}", file=err)
        if telegram is not None:
            telegram(level, text)       # alertes hors machine (EX-NOT-01)

    alerts = Alerts(clock, store, notifier=notify)
    analysis = None if args.no_analysis else AnalysisService(exchange, clock, store, fetch_fng=args.fetch_fng)
    try:
        orch = Orchestrator(
            settings, exchange, ledger, store, clock, Path(args.stop_file), alerts=alerts, analysis=analysis
        )
        added = build_team(raw, orch, exchange, clock.now())
    except ValueError as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(str(exc)) from exc
    if not added:
        raise ConfigError("aucune stratégie activée (strategies.<nom>.enabled = true)")
    orch.restore()
    orch.run_mode = mode
    print(f"Équipe : {', '.join(added)}. Un tick toutes les {args.interval} s (Ctrl+C pour arrêter).", file=out)
    try:
        ticks = asyncio.run(orch.run(args.interval, args.ticks))
    except KeyboardInterrupt:
        ticks = -1
    print("Équipe arrêtée proprement." if ticks < 0 else f"Équipe arrêtée après {ticks} tick(s).", file=out)
    return 0


def _cmd_analyze(args, settings: Settings, environ, out, err, factory) -> int:
    """Vue de marché en lecture seule : données publiques, aucune clé nécessaire."""
    try:
        key, secret = load_credentials(settings.env, environ)
    except ConfigError:
        key, secret = "", ""
    client = factory(settings.env, key, secret, live_confirmed=settings.env is Env.LIVE)
    exchange = BinanceExchange(client, quote=settings.quote_currency)
    service = AnalysisService(exchange, SystemClock(), None, fetch_fng=args.fetch_fng)
    snap = service.refresh(args.symbol)
    print(f"{_banner(settings)}\nANALYSE {args.symbol}", file=out)
    if snap.regime is None:
        print("Régime : indéterminé (historique insuffisant : au moins 230 bougies journalières requises)", file=out)
    else:
        i = snap.reading.indicators
        print(f"Régime : {snap.regime.value} (confiance {snap.reading.confidence:.0%}, ADX {i['adx']:.1f}, "
              f"prix {i['close']:.2f}, moyenne 200 j {i['sma_slow']:.2f})", file=out)
    print("Données : " + ("cohérentes" if snap.healthy else "SUSPECTES : " + "; ".join(snap.problems)), file=out)
    if snap.volatility is not None:
        v = snap.volatility
        print(f"Volatilité : ATR(14) {v.atr:.2f}, réalisée 30 j {v.realized:.0%}, prévue {v.forecast:.0%}, "
              f"facteur de ciblage (cible 40 %) {vol_target_factor(0.40, v.forecast):.2f}", file=out)
    if snap.liquidity is not None:
        li = snap.liquidity
        spread = f"{li.spread_pct:.3%}" if li.spread_pct is not None else "n/a"
        slip = f"{li.slippage_pct:.3%}" if li.slippage_pct is not None else "n/a"
        print(f"Liquidité : écart {spread}, glissement estimé {slip} pour 50 {settings.quote_currency} -> {li.reason}", file=out)
    fng = service.refresh_sentiment()
    if fng is None:
        print("Sentiment : indisponible", file=out)
    else:
        print(f"Sentiment : {fng.value}/100 ({fng.classification}), multiplicateur d'exposition "
              f"{sentiment_multiplier(fng.value)}\n{ATTRIBUTION}", file=out)
    return 0


def _cmd_cancel_all(args, settings: Settings, environ, out, err, factory) -> int:
    key, secret = load_credentials(settings.env, environ)
    client = factory(settings.env, key, secret, live_confirmed=settings.env is Env.LIVE)
    exchange = BinanceExchange(client, quote=settings.quote_currency)
    service = ExecutionService(exchange, Ledger(Decimal("0")), dry_run=False)
    reports = service.cancel_all_open(args.symbol, keep_protective=False)
    print(f"{len(reports)} ordre(s) annulé(s) sur {args.symbol}.", file=out)
    return 0


def _cmd_audit_legacy(args, out, err) -> int:
    """Phase 0 : audit statique de l'existant (EX-ST-07). N'affiche et n'écrit jamais de secret."""
    root = Path(args.path)
    if not root.is_dir():
        print(f"Dossier introuvable : {root}", file=err)
        return 2
    report = scan(root)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(report, SystemClock().now()), encoding="utf-8")
    counts = {s: sum(f.severity == s for f in report.findings) for s in SEVERITIES}
    print(f"Audit de {root} : {len(report.files)} fichier(s) Python, {report.loc} ligne(s), "
          f"{len(report.agent_classes)} classe(s) d'agent.", file=out)
    print("Constats : " + ", ".join(f"{counts[s]} {s}" for s in SEVERITIES) + f". Rapport : {target}", file=out)
    if counts["CRITIQUE"]:
        print("ATTENTION : constat(s) CRITIQUE(S) (clé en clair ou retrait) : voir le rapport, révoquer les clés concernées.", file=out)
    return 0


def _cmd_dashboard(args, environ, out, err) -> int:
    """Tableau de bord local (127.0.0.1) : lecture seule de la base, aucune clé d'exchange ni de LLM (EX-UI-08)."""
    secrets_env: dict[str, str] = {k: v for k, v in environ.items() if k == "DASHBOARD_PASSWORD"}
    load_env_file(Path(args.env_file), secrets_env, only={"DASHBOARD_PASSWORD"})
    password = secrets_env.get("DASHBOARD_PASSWORD", "")
    if not password:
        raise ConfigError("DASHBOARD_PASSWORD manquant : le définir dans .env (8 caractères minimum)")
    if not Path(args.db).exists():
        raise ConfigError(f"base introuvable : {args.db} (lancer d'abord `run` ou `run --paper`)")
    try:
        server = DashboardServer(args.db, args.stop_file, password, port=args.port, twin_db=args.twin_db)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    url = f"http://127.0.0.1:{server.port}"
    if args.check:
        server.httpd.server_close()
        print(f"Tableau de bord prêt : {url} (vérification seulement, non démarré).", file=out)
        return 0
    print(f"Tableau de bord : {url}  (Ctrl+C pour arrêter). Accès distant : tunnel SSH, jamais d'exposition publique.", file=out)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.httpd.server_close()
    return 0


ASSISTANT_ENV = {"GROQ_API_KEY", "GROQ_MODEL", "GROQ_MONTHLY_TOKEN_BUDGET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def _build_assistant(args, environ) -> tuple[AssistantAgent, Store, dict]:
    """L'assistant est le seul à détenir la clé Groq (EX-UI-08) ; on ne charge que les variables qui le concernent."""
    env = {k: v for k, v in environ.items() if k in ASSISTANT_ENV}
    load_env_file(Path(args.env_file), env, only=ASSISTANT_ENV)
    if not Path(args.db).exists():
        raise ConfigError(f"base introuvable : {args.db}")
    try:
        client = GroqClient(env.get("GROQ_API_KEY", ""), env.get("GROQ_MODEL", ""), post=args.groq_post)
    except GroqError as exc:
        raise ConfigError(str(exc)) from exc
    budget = int(env.get("GROQ_MONTHLY_TOKEN_BUDGET", "200000"))
    clock = SystemClock()
    store = Store(Database(args.db))
    tools = ToolBox(DashboardData(args.db, getattr(args, "stop_file", "var/STOP")), store, clock.now, budget)
    return AssistantAgent(client, tools, store, clock.now, budget, env["GROQ_MODEL"]), store, env


def _cmd_assistant(args, environ, out, err) -> int:
    assistant, store, env = _build_assistant(args, environ)
    if args.assistant_command == "ask":
        try:
            print(assistant.ask(args.question), file=out)
        except (GroqError, BudgetExceeded) as exc:
            print(f"Assistant indisponible : {mask_secrets(str(exc))}", file=err)
            return 4
        return 0
    if args.assistant_command == "report":
        report = assistant.daily_report()
        print(report, file=out)
        telegram = telegram_from_env(env, post=args.telegram_post) if args.telegram else None
        if telegram is not None:
            telegram("REPORT", report)
        return 0
    telegram = telegram_from_env(env, post=args.telegram_post)
    print("Worker de l'assistant démarré (Ctrl+C pour arrêter).", file=out)
    try:
        run_worker(store, assistant, SystemClock().now, notifier=telegram, interval=args.interval, max_loops=args.max_loops,
                   report_hour=args.report_hour)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_news(args, environ, out, err) -> int:
    assistant, store, _ = _build_assistant(args, environ)
    assets = {a.strip().upper() for a in args.assets.split(",") if a.strip()}
    titles: list[str] = []
    for url in args.rss:
        try:
            titles += parse_rss(args.news_fetch(url))
        except Exception as exc:
            print(f"Flux {url} illisible : {mask_secrets(str(exc))}", file=err)
    if not titles:
        print("Aucun titre exploitable : rien à classer.", file=out)
        return 0
    items = assistant.classify_news(titles, assets)
    pairs = pairs_to_pause(items)
    now = SystemClock().now()
    for pair, reason in pairs.items():
        store.enqueue_command(now, "pause_pair", f"{pair}|{reason}")
    print(f"{len(titles)} titre(s) classé(s), {len(items)} valide(s). Paires mises en pause : "
          f"{', '.join(pairs) if pairs else 'aucune'}.", file=out)
    return 0


def _cmd_experiment(args, out, err) -> int:
    """Protocole d'expérience (EX-XP-01) : figer les critères avant de commencer, puis juger sans les changer."""
    try:
        if not Path(args.db).exists():
            raise ConfigError(f"base introuvable : {args.db}")
        store = Store(Database(args.db))
        now = SystemClock().now()
        if args.experiment_command == "start":
            state = start_experiment(store, args.protocol, now)
            print(f"Expérience « {state['name']} » démarrée. Empreinte du protocole (SHA-256) : {state['sha256']}", file=out)
            print("Ne modifie plus ce fichier : toute modification invaliderait l'expérience.", file=out)
            return 0
        protocol = load_protocol(args.protocol)
        if not verify_experiment(store, args.protocol):
            print("PROTOCOLE MODIFIÉ APRÈS LE DÉBUT (ou expérience non démarrée) : résultats invalides (EX-XP-01).", file=err)
            return 3
        result = evaluate(protocol, store, now)
        print(f"{protocol.name} ({protocol.level}) : {result.days} jour(s) observé(s) sur {protocol.min_days}.", file=out)
        for c in result.criteria:
            print(f"  [{c.status:<8}] {c.name} : {c.detail}", file=out)
        if result.stop_reason:
            print(f"RÈGLE D'ARRÊT ATTEINTE : {result.stop_reason}", file=out)
        print("Verdict : " + ("critères satisfaits (indicatif : période courte, EX-XP-05)" if result.passed else "non validé à ce stade"), file=out)
        return 0 if result.passed else 1
    except (ProtocolError, ConfigError) as exc:
        print(f"Erreur : {mask_secrets(str(exc))}", file=err)
        return 2


def _cmd_backup(args, out, err) -> int:
    try:
        path = make_backup(args.db, args.dest, args.keep, SystemClock().now())
    except BackupError as exc:
        print(f"Erreur : {exc}", file=err)
        return 2
    print(f"Sauvegarde cohérente créée : {path} ({args.keep} conservées). Pense à la copier HORS du serveur (ENF-05).", file=out)
    return 0


def _cmd_lifecycle(args, out, err) -> int:
    if not Path(args.db).exists():
        print(f"Erreur : base introuvable : {args.db}", file=err)
        return 2
    store = Store(Database(args.db))
    if args.lifecycle_command == "show":
        for code in CODES:
            events = lifecycle_history(store, code)
            last = f" (depuis {events[0]['ts'][:10]} : {events[0]['reason']})" if events else " (étape par défaut : aucune stratégie n'a passé la simulation)"
            print(f"{code}  {current_stage(store, code):<12}{last}", file=out)
        return 0
    try:
        set_stage(store, args.code, args.stage, args.reason, args.by, SystemClock().now(),
                  confirm=args.confirm, decision_exists=Path(args.decision_file).exists())
    except LifecycleError as exc:
        print(f"Erreur : {exc}", file=err)
        return 2
    print(f"{args.code} : {args.stage} (enregistré avec sa raison).", file=out)
    return 0


def _cmd_kill(args, out) -> int:
    stop = Path(args.stop_file)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.touch()
    print(f"Kill switch activé : fichier STOP créé ({stop}).", file=out)
    return 0


def _cmd_status(args, out) -> int:
    db = Path(args.db)
    if not db.exists():
        print("aucune base de données trouvée.", file=out)
        return 0
    store = Store(Database(db))
    ledger = store.load_ledger()
    if ledger is None:
        print("base vide : aucun registre enregistré.", file=out)
        return 0
    print(f"Capital total {ledger.total} | non alloué {ledger.unallocated}", file=out)
    for a in ledger.accounts():
        print(f"  {a.agent_id}: alloué {a.allocated}, engagé {a.committed}, disponible {a.available}", file=out)
    print(f"Ordres suivis : {len(store.load_tracked())}", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestre")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("check-config", "check-keys", "run", "cancel-all", "analyze"):
        s = sub.add_parser(name)
        s.add_argument("--config", default="config/settings.toml")
        s.add_argument("--env-file", default=".env")
        s.add_argument("--live", action="store_true", help="seconde confirmation du mode réel (EX-SEC-01)")
        if name == "run":
            s.add_argument("--db", default="var/orchestre.db")
            s.add_argument("--stop-file", default="var/STOP")
            s.add_argument("--decision-file", default="docs/decision-live.md")
            s.add_argument("--ticks", type=int, default=None, help="nombre de ticks puis arrêt (défaut : sans limite)")
            s.add_argument("--interval", type=float, default=30.0, help="secondes entre deux ticks")
            s.add_argument("--no-analysis", action="store_true", help="désactive les agents d'analyse")
            s.add_argument("--paper", action="store_true", help="simulation : marché réel en lecture, ordres simulés")
            s.add_argument("--paper-cash", type=float, default=None, help="capital de départ simulé (défaut : plafond)")
        if name in ("cancel-all", "analyze"):
            s.add_argument("--symbol", default=SYMBOL)
    def market_args(p):
        p.add_argument("--cash", type=float, default=1000.0)
        p.add_argument("--fee", type=float, default=0.001)
        p.add_argument("--slippage", type=float, default=0.0005)
        p.add_argument("--data-dir", default="data")
        p.add_argument("--warmup", type=int, default=None, help="bougies de préchauffage (défaut : 120 en 1d, 200 en 4h)")

    bt = sub.add_parser("backtest")
    bt.add_argument("--symbol", default=SYMBOL)
    bt.add_argument("--interval", choices=["1d", "4h"], default="1d")
    bt.add_argument("--days", type=int, default=1000, help="jours d'historique")
    market_args(bt)
    wf = sub.add_parser("walkforward")
    wf.add_argument("--strategy", choices=sorted(WALKFORWARD), default="trend")
    wf.add_argument("--symbol", default=SYMBOL)
    wf.add_argument("--interval", choices=["1d", "4h"], default="1d")
    wf.add_argument("--days", type=int, default=1000)
    wf.add_argument("--train", type=int, default=365, help="jours d'entraînement par fenêtre")
    wf.add_argument("--test", type=int, default=90, help="jours de test (et pas de glissement)")
    market_args(wf)
    pb = sub.add_parser("portfolio-backtest")
    pb.add_argument("--symbols", default=DEFAULT_UNIVERSE)
    pb.add_argument("--days", type=int, default=1000)
    pb.add_argument("--null-runs", type=int, default=0, help="nombre de rotations aléatoires pour le modèle nul")
    market_args(pb)
    tb = sub.add_parser("team-backtest")
    tb.add_argument("--symbols", default=DEFAULT_UNIVERSE)
    tb.add_argument("--days", type=int, default=1000)
    market_args(tb)
    au = sub.add_parser("audit-legacy")
    au.add_argument("--path", required=True, help="dossier du code existant (copié dans legacy/, sans clé)")
    au.add_argument("--output", default="docs/audit-existant.md")
    asst = sub.add_parser("assistant")
    asst_sub = asst.add_subparsers(dest="assistant_command", required=True)
    ask = asst_sub.add_parser("ask")
    ask.add_argument("question")
    rep = asst_sub.add_parser("report")
    rep.add_argument("--telegram", action="store_true", help="envoie aussi le rapport par Telegram")
    wk = asst_sub.add_parser("worker")
    wk.add_argument("--interval", type=float, default=5.0)
    wk.add_argument("--report-hour", type=int, default=7, help="heure UTC du rapport quotidien")
    wk.add_argument("--max-loops", type=int, default=None, help=argparse.SUPPRESS)
    for sp in (ask, rep, wk):
        sp.add_argument("--db", default="var/orchestre.db")
        sp.add_argument("--env-file", default=".env")
    nw = sub.add_parser("news")
    nw.add_argument("--db", default="var/orchestre.db")
    nw.add_argument("--env-file", default=".env")
    nw.add_argument("--rss", action="append", required=True, help="URL d'un flux RSS ou Atom (répétable)")
    nw.add_argument("--assets", default="BTC,ETH,SOL,BNB,XRP")
    up = sub.add_parser("up", help="lance tout le système : moteur + tableau de bord + assistant")
    up.add_argument("--mode", default="paper", help="paper (défaut) ou testnet ; jamais le réel")
    up.add_argument("--config", default="config/settings.toml")
    up.add_argument("--db", default=None, help="défaut : var/paper.db ou var/testnet.db")
    up.add_argument("--stop-file", default="var/STOP")
    up.add_argument("--port", type=int, default=8765)
    up.add_argument("--interval", type=float, default=30.0)
    up.add_argument("--env-file", default=".env")
    up.add_argument("--paper-cash", type=float, default=None)
    up.add_argument("--twin", action="store_true", help="lance aussi un jumeau numérique : réplique papier de la même configuration")
    up.add_argument("--no-dashboard", action="store_true")
    up.add_argument("--no-assistant", action="store_true")
    up.add_argument("--no-analysis", action="store_true")
    up.add_argument("--detach", action="store_true", help="lance en arrière-plan")
    up.add_argument("--check", action="store_true", help="affiche le plan et s'arrête")
    up.add_argument("--ticks", type=int, default=None, help=argparse.SUPPRESS)
    for name in ("down", "ps"):
        sp = sub.add_parser(name)
        sp.add_argument("--run-dir", default="var/run")
        if name == "down":
            sp.add_argument("--timeout", type=float, default=30.0)
    bk = sub.add_parser("backup")
    bk.add_argument("--db", default="var/orchestre.db")
    bk.add_argument("--dest", default="backups")
    bk.add_argument("--keep", type=int, default=14)
    lc = sub.add_parser("lifecycle")
    lc_sub = lc.add_subparsers(dest="lifecycle_command", required=True)
    lc_show = lc_sub.add_parser("show")
    lc_set = lc_sub.add_parser("set")
    lc_set.add_argument("code", choices=CODES)
    lc_set.add_argument("stage")
    lc_set.add_argument("--reason", required=True)
    lc_set.add_argument("--by", default="porteur")
    lc_set.add_argument("--confirm", action="store_true")
    lc_set.add_argument("--decision-file", default="docs/decision-live.md")
    for sp in (lc_show, lc_set):
        sp.add_argument("--db", default="var/orchestre.db")
    ex = sub.add_parser("experiment")
    ex_sub = ex.add_subparsers(dest="experiment_command", required=True)
    for name in ("start", "status"):
        sp = ex_sub.add_parser(name)
        sp.add_argument("--protocol", required=True)
        sp.add_argument("--db", default="var/orchestre.db")
    ds = sub.add_parser("dashboard")
    ds.add_argument("--db", default="var/orchestre.db")
    ds.add_argument("--stop-file", default="var/STOP")
    ds.add_argument("--twin-db", default=None, help="base du jumeau numérique (réplique papier), lue en lecture seule")
    ds.add_argument("--port", type=int, default=8765)
    ds.add_argument("--env-file", default=".env")
    ds.add_argument("--check", action="store_true", help="vérifie la configuration et s'arrête")
    k = sub.add_parser("kill")
    k.add_argument("--stop-file", default="var/STOP")
    st = sub.add_parser("status")
    st.add_argument("--db", default="var/orchestre.db")
    return p


def main(
    argv: list[str] | None = None,
    environ: MutableMapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    client_factory: Callable | None = None,
    fetch_fng: Callable | None = None,
    public_client_factory: Callable | None = None,
    telegram_post: Callable | None = None,
    groq_post: Callable | None = None,
    news_fetch: Callable | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    environ = environ if environ is not None else dict(os.environ)
    factory = client_factory or create_client
    args = build_parser().parse_args(argv)
    args.fetch_fng = fetch_fng or default_fetch
    args.public_client_factory = public_client_factory or create_public_client
    args.telegram_post = telegram_post or default_post
    args.groq_post = groq_post or groq_default_post
    args.news_fetch = news_fetch or news_default_fetch

    try:
        if args.command == "kill":
            return _cmd_kill(args, out)
        backtests = {
            "backtest": cmd_backtest, "walkforward": cmd_walkforward,
            "portfolio-backtest": cmd_portfolio_backtest, "team-backtest": cmd_team_backtest,
        }
        if args.command in backtests:
            return backtests[args.command](args, out, err, public_client_factory or create_public_client)
        if args.command == "backup":
            return _cmd_backup(args, out, err)
        if args.command == "lifecycle":
            return _cmd_lifecycle(args, out, err)
        if args.command == "up":
            return cmd_up(args, out, err)
        if args.command == "down":
            return cmd_down(args, out, err)
        if args.command == "ps":
            return cmd_ps(args, out, err)
        if args.command == "experiment":
            return _cmd_experiment(args, out, err)
        if args.command == "assistant":
            return _cmd_assistant(args, environ, out, err)
        if args.command == "news":
            return _cmd_news(args, environ, out, err)
        if args.command == "dashboard":
            return _cmd_dashboard(args, environ, out, err)
        if args.command == "audit-legacy":
            return _cmd_audit_legacy(args, out, err)
        if args.command == "status":
            return _cmd_status(args, out)
        load_env_file(Path(args.env_file), environ)
        settings = load_settings(args.config, live_flag=args.live)
        handlers = {
            "check-config": _cmd_check_config, "check-keys": _cmd_check_keys,
            "run": _cmd_run, "cancel-all": _cmd_cancel_all, "analyze": _cmd_analyze,
        }
        return handlers[args.command](args, settings, environ, out, err, factory)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Erreur de configuration : {mask_secrets(str(exc))}", file=err)
        return 2
    except Exception as exc:  # réseau/API : jamais de secret dans le message (EX-SEC-08)
        print(f"Erreur réseau ou API : {mask_secrets(str(exc))}", file=err)
        return 4
