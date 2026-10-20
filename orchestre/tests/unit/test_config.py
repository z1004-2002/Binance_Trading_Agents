from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestre.config import ConfigError, Env, load_settings, parse_settings

EXAMPLE = Path(__file__).parents[2] / "config" / "settings.example.toml"


def test_example_config_loads_as_testnet():
    s = load_settings(EXAMPLE)
    assert s.env is Env.TESTNET
    assert s.dry_run is True
    assert not any(s.instruments.values())


def test_default_env_is_testnet():
    assert parse_settings({}).env is Env.TESTNET


def test_live_requires_cli_flag():
    with pytest.raises(ConfigError):
        parse_settings({"env": "live", "max_total_capital_quote": "100"})


def test_live_flag_without_live_env_refused():
    with pytest.raises(ConfigError):
        parse_settings({"env": "testnet"}, live_flag=True)


def test_live_with_both_confirmations_and_cap():
    s = parse_settings({"env": "live", "max_total_capital_quote": "100"}, live_flag=True)
    assert s.env is Env.LIVE and s.max_total_capital_quote == D("100")


def test_live_requires_positive_cap():
    with pytest.raises(ConfigError):
        parse_settings({"env": "live"}, live_flag=True)


def test_advanced_instruments_refused_in_live():
    raw = {
        "env": "live",
        "max_total_capital_quote": "100",
        "instruments": {"futures": {"enabled": True}},
    }
    with pytest.raises(ConfigError):
        parse_settings(raw, live_flag=True)


def test_leverage_requires_leveraged_instrument():
    with pytest.raises(ConfigError):
        parse_settings({"risk": {"max_leverage": "2"}})


def test_leverage_allowed_with_futures_on_testnet():
    s = parse_settings({"risk": {"max_leverage": "2"}, "instruments": {"futures": {"enabled": True}}})
    assert s.risk.max_leverage == D("2")


def test_unknown_instrument_and_bad_values_refused():
    with pytest.raises(ConfigError):
        parse_settings({"instruments": {"options": {"enabled": True}}})
    with pytest.raises(ConfigError):
        parse_settings({"max_total_capital_quote": "abc"})
    with pytest.raises(ConfigError):
        parse_settings({"treasury_reserve_pct": "1.5"})
