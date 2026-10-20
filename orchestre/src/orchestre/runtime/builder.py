"""Construit l'équipe depuis la configuration (A-8, A-10). Filtres lus sur l'exchange, jamais dans le fichier."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from orchestre.config import ConfigError, Env
from orchestre.exchange.base import Exchange
from orchestre.runtime.orchestrator import Orchestrator
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.breakout import DonchianBreakout
from orchestre.strategies.grid import GridConfig
from orchestre.strategies.meanreversion import MeanReversion
from orchestre.strategies.rotation import ConstantMix, Rotation
from orchestre.strategies.trend import TrendFollowing

KNOWN = ("dca", "grid", "trend", "breakout", "meanreversion", "rotation", "rebalance")
EXPOSURE = ("trend", "breakout", "meanreversion", "rotation", "rebalance")   # stratèges à exposition cible (§14)


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


def _params(section: dict, names: dict) -> dict:
    """Paramètres facultatifs d'une stratégie, convertis dans leur type."""
    return {name: kind(section[name]) for name, kind in names.items() if name in section}


def _add_exposure_strategy(name: str, section: dict, orch: Orchestrator) -> None:
    if orch.settings.env is Env.LIVE and not section.get("promoted", False):
        raise ConfigError(
            f"strategies.{name} : refusée en réel sans `promoted = true` (décision humaine de promotion, cycle de vie §14)"
        )
    budget = _dec(section, "budget_quote", name)
    if name == "rotation":
        symbols = [str(x) for x in _field(section, "symbols", name)]
        top_k = int(section.get("top_k", 3))
        strategy = Rotation(top_k=top_k, **_params(section, {"lookback": int, "rebalance_days": int}))
        orch.add_portfolio("rotation", "S-6", strategy, symbols, budget, positions=top_k,
                           default_stop_pct=_dec(section, "default_stop_pct", name, "0.25"))
    elif name == "rebalance":
        weights = {str(k): Decimal(str(v)) for k, v in _field(section, "weights", name).items()}
        strategy = ConstantMix(weights, band=_dec(section, "band", name, "0.05"))
        orch.add_portfolio("rebalance", "S-7", strategy, list(weights), budget, positions=len(weights),
                           default_stop_pct=_dec(section, "default_stop_pct", name, "0.25"))
    else:
        symbol = str(_field(section, "symbol", name))
        if name == "trend":
            strategy = TrendFollowing(**_params(section, {"lookback": int, "sma_len": int, "atr_mult": float, "vol_target": float, "use_vol_target": bool}))
            orch.add_exposure("trend", "S-3", strategy, symbol, budget, "1d")
        elif name == "breakout":
            strategy = DonchianBreakout(**_params(section, {"entry_len": int, "exit_len": int, "atr_mult": float}))
            orch.add_exposure("breakout", "S-4", strategy, symbol, budget, "1d")
        else:
            strategy = MeanReversion(**_params(section, {"bb_k": float, "rsi_entry": float, "max_bars": int}))
            orch.add_exposure("meanreversion", "S-5", strategy, symbol, budget, "4h")


def strategy_symbols(raw: dict) -> set[str]:
    """Paires utilisées par les stratégies activées (pour préparer l'exchange paper)."""
    symbols: set[str] = set()
    for name, section in raw.get("strategies", {}).items():
        if not isinstance(section, dict) or not section.get("enabled", False):
            continue
        if "symbol" in section:
            symbols.add(str(section["symbol"]))
        symbols.update(str(x) for x in section.get("symbols", []))
        symbols.update(str(k) for k in section.get("weights", {}))
    return symbols


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
        if name in EXPOSURE:
            try:
                _add_exposure_strategy(name, section, orch)
            except ConfigError:
                raise
            except (ValueError, TypeError) as exc:
                raise ConfigError(f"strategies.{name} : {exc}") from exc
            added.append(name)
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
