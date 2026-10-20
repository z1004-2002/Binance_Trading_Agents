"""Construit l'équipe depuis la configuration (A-8, A-10). Filtres lus sur l'exchange, jamais dans le fichier."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from orchestre.config import ConfigError
from orchestre.exchange.base import Exchange
from orchestre.runtime.orchestrator import Orchestrator
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.grid import GridConfig

KNOWN = ("dca", "grid")


def _field(section: dict, name: str, strategy: str):
    if name not in section:
        raise ConfigError(f"strategies.{strategy} : champ obligatoire manquant : {name}")
    return section[name]


def _dec(section: dict, name: str, strategy: str, default: str | None = None) -> Decimal:
    raw = section.get(name, default) if default is not None else _field(section, name, strategy)
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ConfigError(f"strategies.{strategy}.{name} : décimal invalide : {raw!r}") from exc


def build_team(raw: dict, orch: Orchestrator, exchange: Exchange, now: datetime) -> list[str]:
    sections = raw.get("strategies", {})
    for name in sections:
        if name not in KNOWN:
            raise ConfigError(f"stratégie inconnue : {name!r}")
    added: list[str] = []
    for name in KNOWN:
        section = sections.get(name)
        if not section or not section.get("enabled", False):
            continue
        symbol = str(_field(section, "symbol", name))
        filters = exchange.get_filters(symbol)
        try:
            if name == "dca":
                start = datetime.fromisoformat(section["start"]) if "start" in section else now
                orch.add_dca(
                    DcaConfig(
                        agent_id="dca", symbol=symbol, amount_quote=_dec(section, "amount_quote", name),
                        interval=timedelta(days=int(_field(section, "interval_days", name))), start=start,
                        cumulative_cap=_dec(section, "cumulative_cap", name), min_notional=filters.min_notional,
                        missed_policy=section.get("missed_policy", "skip"), modulated=bool(section.get("modulated", False)),
                    )
                )
            else:
                orch.add_grid(
                    GridConfig(
                        agent_id="grid", symbol=symbol, lower=_dec(section, "lower", name),
                        upper=_dec(section, "upper", name), intervals=int(_field(section, "intervals", name)),
                        budget_quote=_dec(section, "budget_quote", name), fee_rate=_dec(section, "fee_rate", name, "0.001"),
                        min_notional=filters.min_notional, tick_size=filters.tick_size, step_size=filters.step_size,
                    ),
                    exit_policy=section.get("exit_policy", "pause"),
                )
        except ValueError as exc:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"strategies.{name} : {exc}") from exc
        added.append(name)
    return added
