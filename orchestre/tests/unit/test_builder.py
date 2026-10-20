from decimal import Decimal as D
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestre.config import ConfigError, parse_settings
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.builder import build_team
from orchestre.runtime.orchestrator import Orchestrator

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500"})
EXAMPLE = Path(__file__).parents[2] / "config" / "strategies.example.toml"


def orch(tmp_path):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})
    o = Orchestrator(SETTINGS, ex, Ledger(D("500")), Store(Database(":memory:")), SimulatedClock(T0), tmp_path / "STOP")
    return o, ex


def test_example_config_builds_dca_and_grid(tmp_path):
    import tomllib
    o, ex = orch(tmp_path)
    ids = build_team(tomllib.loads(EXAMPLE.read_text()), o, ex, T0)
    assert ids == ["dca", "grid"] and set(o.units) == {"dca", "grid"}
    grid = o.units["grid"].runner
    assert grid._cfg.tick_size == D("0.01") and grid._cfg.min_notional == D("5")  # lus depuis l'exchange


def test_disabled_and_missing_sections_are_skipped(tmp_path):
    o, ex = orch(tmp_path)
    assert build_team({}, o, ex, T0) == []
    assert build_team({"strategies": {"dca": {"enabled": False}}}, o, ex, T0) == []


def test_unknown_strategy_is_refused(tmp_path):
    o, ex = orch(tmp_path)
    with pytest.raises(ConfigError, match="inconnue"):
        build_team({"strategies": {"martingale": {"enabled": True}}}, o, ex, T0)


def test_missing_field_is_reported(tmp_path):
    o, ex = orch(tmp_path)
    with pytest.raises(ConfigError, match="symbol"):
        build_team({"strategies": {"dca": {"enabled": True}}}, o, ex, T0)


def test_invalid_strategy_values_are_reported_as_config_errors(tmp_path):
    o, ex = orch(tmp_path)
    raw = {"strategies": {"grid": {"enabled": True, "symbol": "BTCUSDT", "lower": "55000", "upper": "65000",
                                   "intervals": 400, "budget_quote": "200"}}}
    with pytest.raises(ConfigError, match="grid"):
        build_team(raw, o, ex, T0)
