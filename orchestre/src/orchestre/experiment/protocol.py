"""Protocole d'expérience (§16.4, EX-XP-01 à EX-XP-05) : critères et règles d'arrêt figés AVANT de commencer.

L'empreinte (SHA-256) du fichier est enregistrée au démarrage ; si le fichier change ensuite, l'expérience est invalide :
on ne change pas les critères en cours de route. Ce module juge ; il ne décide jamais d'un passage au réel (porte du §20).
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from orchestre.metrics.performance import max_drawdown, sortino, total_return
from orchestre.persistence.store import Store


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Protocol:
    name: str
    level: str
    min_days: int
    max_drawdown: float
    drawdown_below_reference: bool
    sortino_at_least_reference: bool
    max_incidents: int
    max_fees_pct: float
    stop_drawdown: float
    stop_incidents: int


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_protocol(path: str | Path) -> Protocol:
    path = Path(path)
    if not path.exists():
        raise ProtocolError(f"protocole introuvable : {path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        success, stop = raw["success"], raw["stop"]
        p = Protocol(
            raw["name"], raw["level"], int(raw["min_days"]), float(success["max_drawdown"]),
            bool(success["drawdown_below_reference"]), bool(success["sortino_at_least_reference"]),
            int(success["max_critical_incidents"]), float(success["max_fees_pct_of_capital"]),
            float(stop["drawdown"]), int(stop["critical_incidents"]),
        )
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ProtocolError(f"protocole invalide ({path.name}) : {exc!r}") from exc
    if not (0 < p.max_drawdown < 1 and 0 < p.stop_drawdown < 1 and 0 <= p.max_fees_pct < 1) or p.min_days < 1:
        raise ProtocolError("protocole invalide : drawdowns et frais doivent être des fractions entre 0 et 1, min_days >= 1")
    return p


def start_experiment(store: Store, protocol_path: str | Path, now: datetime) -> dict:
    load_protocol(protocol_path)  # valide avant de figer
    if store.load_state("experiment") is not None:
        raise ProtocolError("expérience déjà démarrée sur cette base : les critères ne se changent pas en cours de route")
    state = {"name": load_protocol(protocol_path).name, "sha256": file_sha256(Path(protocol_path)),
             "file": str(protocol_path), "started_at": now.isoformat()}
    store.save_state("experiment", state, now)
    return state


def verify_experiment(store: Store, protocol_path: str | Path) -> bool:
    state = store.load_state("experiment")
    return state is not None and state["sha256"] == file_sha256(Path(protocol_path))


@dataclass(frozen=True)
class Criterion:
    name: str
    status: str   # OK | NON | TROP TÔT | INCONNU
    detail: str


@dataclass
class Evaluation:
    criteria: list[Criterion] = field(default_factory=list)
    stop_reason: str | None = None
    incidents: int = 0
    days: int = 0

    @property
    def passed(self) -> bool:
        return self.stop_reason is None and bool(self.criteria) and all(c.status == "OK" for c in self.criteria)


def _daily(points: list[tuple[str, float]]) -> list[float]:
    return list({ts[:10]: value for ts, value in points}.values())


def count_incidents(store: Store) -> int:
    """Incidents critiques mesurables : ordre exécuté deux fois sous le même identifiant, registre incohérent."""
    conn = store.db.conn
    duplicates = conn.execute(
        "select count(*) from (select client_order_id from order_log where status = 'FILLED' "
        "group by client_order_id having count(*) > 1)"
    ).fetchone()[0]
    inconsistent = conn.execute("select count(*) from events where kind = 'self_check' and detail like '%registre incohérent%'").fetchone()[0]
    declared = conn.execute("select count(*) from events where kind = 'incident'").fetchone()[0]
    return int(duplicates) + int(inconsistent) + int(declared)


def evaluate(protocol: Protocol, store: Store, now: datetime) -> Evaluation:
    team, ref = _daily(store.equity_series("TEAM")), _daily(store.equity_series("REF_BTCUSDT"))
    out = Evaluation(days=max(len(team) - 1, 0), incidents=count_incidents(store))
    add = out.criteria.append
    enough = out.days >= protocol.min_days
    add(Criterion("durée minimale", "OK" if enough else "TROP TÔT", f"{out.days} jour(s) sur {protocol.min_days} requis"))

    if len(team) >= 3:
        dd = max_drawdown(team)
        add(Criterion("drawdown maximal", "OK" if dd <= protocol.max_drawdown else "NON", f"{dd:.1%} (limite {protocol.max_drawdown:.0%})"))
        if dd >= protocol.stop_drawdown:
            out.stop_reason = f"drawdown {dd:.1%} >= règle d'arrêt {protocol.stop_drawdown:.0%} : retour en L0"
        if protocol.drawdown_below_reference:
            if len(ref) >= 3:
                rdd = max_drawdown(ref)
                add(Criterion("drawdown < référence", "OK" if dd < rdd else "NON", f"{dd:.1%} contre {rdd:.1%} pour la détention de BTC"))
            else:
                add(Criterion("drawdown < référence", "INCONNU", "série de référence insuffisante"))
        if protocol.sortino_at_least_reference:
            if len(ref) >= 3:
                s, r = sortino(team), sortino(ref)
                add(Criterion("Sortino >= référence", "OK" if s >= r else "NON", f"{s:.2f} contre {r:.2f}"))
            else:
                add(Criterion("Sortino >= référence", "INCONNU", "série de référence insuffisante"))
        add(Criterion("rendement (information)", "OK", f"{total_return(team):+.1%}"))
    else:
        add(Criterion("drawdown maximal", "TROP TÔT", "moins de 3 jours de valeurs"))

    add(Criterion("incidents critiques", "OK" if out.incidents <= protocol.max_incidents else "NON", f"{out.incidents} (limite {protocol.max_incidents})"))
    if out.incidents >= protocol.stop_incidents and out.stop_reason is None:
        out.stop_reason = f"{out.incidents} incidents critiques >= règle d'arrêt {protocol.stop_incidents} : retour en L0"
    row = store.db.conn.execute("select coalesce(sum(cast(fee as real)), 0) from order_log where status = 'FILLED'").fetchone()
    capital = store.load_ledger().total if store.load_ledger() else Decimal(0)
    if capital > 0:
        pct = float(row[0]) / float(capital)
        add(Criterion("frais", "OK" if pct <= protocol.max_fees_pct else "NON", f"{pct:.2%} du capital (limite {protocol.max_fees_pct:.0%})"))
    return out
