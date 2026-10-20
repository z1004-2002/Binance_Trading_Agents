"""Configuration déclarative validée au démarrage (A-8, EX-SEC-01, EX-PER-01)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


class ConfigError(ValueError):
    pass


class Env(str, Enum):
    TESTNET = "testnet"
    DEMO = "demo"
    LIVE = "live"


ADVANCED_INSTRUMENTS = ("futures", "margin", "earn")


@dataclass(frozen=True)
class RiskSettings:
    max_leverage: Decimal = Decimal("1")
    max_exposure_per_asset_pct: Decimal = Decimal("0.40")
    max_crypto_exposure_pct: Decimal = Decimal("0.80")


@dataclass(frozen=True)
class Settings:
    env: Env = Env.TESTNET
    quote_currency: str = "USDT"
    max_total_capital_quote: Decimal = Decimal("0")
    treasury_reserve_pct: Decimal = Decimal("0.20")
    dry_run: bool = True
    risk: RiskSettings = field(default_factory=RiskSettings)
    instruments: dict[str, bool] = field(
        default_factory=lambda: {name: False for name in ADVANCED_INSTRUMENTS}
    )


def _dec(raw: dict, key: str, default: Decimal) -> Decimal:
    value = raw.get(key, default)
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError(f"{key}: valeur décimale invalide: {value!r}") from exc


def parse_settings(raw: dict, live_flag: bool = False) -> Settings:
    """Valide la configuration. `live_flag` = option explicite en ligne de commande."""
    try:
        env = Env(raw.get("env", "testnet"))
    except ValueError as exc:
        raise ConfigError(f"env invalide: {raw.get('env')!r}") from exc

    if env is Env.LIVE and not live_flag:
        raise ConfigError("env=live exige aussi l'option explicite --live (EX-SEC-01)")
    if env is not Env.LIVE and live_flag:
        raise ConfigError("--live fourni mais env != live (EX-SEC-01)")

    cap = _dec(raw, "max_total_capital_quote", Decimal("0"))
    if cap < 0:
        raise ConfigError("max_total_capital_quote doit être >= 0")
    if env is Env.LIVE and cap <= 0:
        raise ConfigError("en live, max_total_capital_quote doit être > 0 (EX-SEC-11)")

    reserve = _dec(raw, "treasury_reserve_pct", Decimal("0.20"))
    if not Decimal("0") <= reserve < Decimal("1"):
        raise ConfigError("treasury_reserve_pct doit être dans [0 ; 1[")

    r = raw.get("risk", {})
    risk = RiskSettings(
        max_leverage=_dec(r, "max_leverage", Decimal("1")),
        max_exposure_per_asset_pct=_dec(r, "max_exposure_per_asset_pct", Decimal("0.40")),
        max_crypto_exposure_pct=_dec(r, "max_crypto_exposure_pct", Decimal("0.80")),
    )
    if risk.max_leverage < 1:
        raise ConfigError("max_leverage doit être >= 1")

    instruments = {name: False for name in ADVANCED_INSTRUMENTS}
    for name, section in raw.get("instruments", {}).items():
        if name not in instruments:
            raise ConfigError(f"instrument inconnu: {name!r}")
        instruments[name] = bool(section.get("enabled", False))
    if risk.max_leverage > 1 and not (instruments["futures"] or instruments["margin"]):
        raise ConfigError("max_leverage > 1 sans futures ni marge activés")
    if env is Env.LIVE and any(instruments.values()):
        raise ConfigError(
            "instruments avancés en live: refusés tant que la porte §20.3 n'est pas franchie"
        )

    return Settings(
        env=env,
        quote_currency=str(raw.get("quote_currency", "USDT")),
        max_total_capital_quote=cap,
        treasury_reserve_pct=reserve,
        dry_run=bool(raw.get("dry_run", True)),
        risk=risk,
        instruments=instruments,
    )


def load_settings(path: str | Path, live_flag: bool = False) -> Settings:
    with open(path, "rb") as fh:
        return parse_settings(tomllib.load(fh), live_flag=live_flag)
