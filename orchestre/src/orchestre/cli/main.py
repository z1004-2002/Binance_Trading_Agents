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


def load_env_file(path: Path, environ: MutableMapping[str, str]) -> None:
    """Lit un fichier .env sans écraser l'environnement réel ; ne journalise aucune valeur."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
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
    alerts = Alerts(clock, store, notifier=lambda level, text: print(f"[{level}] {text}", file=err))
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
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    environ = environ if environ is not None else dict(os.environ)
    factory = client_factory or create_client
    args = build_parser().parse_args(argv)
    args.fetch_fng = fetch_fng or default_fetch
    args.public_client_factory = public_client_factory or create_public_client

    try:
        if args.command == "kill":
            return _cmd_kill(args, out)
        backtests = {
            "backtest": cmd_backtest, "walkforward": cmd_walkforward,
            "portfolio-backtest": cmd_portfolio_backtest, "team-backtest": cmd_team_backtest,
        }
        if args.command in backtests:
            return backtests[args.command](args, out, err, public_client_factory or create_public_client)
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
