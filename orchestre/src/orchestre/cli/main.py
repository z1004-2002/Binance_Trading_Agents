"""CLI : check-config, check-keys, kill, status. Aucune commande n'envoie d'ordre.

Codes de sortie : 0 ok, 2 configuration/clés invalides, 3 clé live dangereuse, 4 erreur réseau/API.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, MutableMapping, TextIO

from orchestre.config import ConfigError, Env, Settings, load_settings
from orchestre.core.secrets import mask_secrets
from orchestre.exchange.binance import BinanceExchange, create_client
from orchestre.exchange.credentials import load_credentials
from orchestre.exchange.key_restrictions import check_key_restrictions
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store

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
    for name in ("check-config", "check-keys"):
        s = sub.add_parser(name)
        s.add_argument("--config", default="config/settings.toml")
        s.add_argument("--env-file", default=".env")
        s.add_argument("--live", action="store_true", help="seconde confirmation du mode réel (EX-SEC-01)")
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
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    environ = environ if environ is not None else dict(os.environ)
    factory = client_factory or create_client
    args = build_parser().parse_args(argv)

    try:
        if args.command == "kill":
            return _cmd_kill(args, out)
        if args.command == "status":
            return _cmd_status(args, out)
        load_env_file(Path(args.env_file), environ)
        settings = load_settings(args.config, live_flag=args.live)
        handler = _cmd_check_config if args.command == "check-config" else _cmd_check_keys
        return handler(args, settings, environ, out, err, factory)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Erreur de configuration : {mask_secrets(str(exc))}", file=err)
        return 2
    except Exception as exc:  # réseau/API : jamais de secret dans le message (EX-SEC-08)
        print(f"Erreur réseau ou API : {mask_secrets(str(exc))}", file=err)
        return 4
