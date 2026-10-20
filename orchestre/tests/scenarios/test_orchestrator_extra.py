"""Orchestrateur + agents supplémentaires : observateur d'arbitrage, tenue de marché (simulation), agents existants."""

import tomllib
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest

from orchestre.analysis.regimes import Regime
from orchestre.config import ConfigError, parse_settings
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.base import OrderBook
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.exchange.paper import PaperExchange
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.builder import build_team, strategy_symbols
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
F_ETH = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
F_X = SymbolFilters("ETHBTC", D("0.00001"), D("0.0001"), D("0.0001"), D("1000"), D("0.0001"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
LEGACY = '''
class OldAgent:
    def decide(self, snapshot):
        if not snapshot["positions"].get("BTCUSDT"):
            return [{"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.001", "reason": "signal ancien"}]
        return []
'''


def market():
    ex = FakeExchange({"BTCUSDT": F, "ETHUSDT": F_ETH, "ETHBTC": F_X},
                      {"BTCUSDT": D("60000"), "ETHUSDT": D("3000"), "ETHBTC": D("0.05")}, {})
    ex.set_order_book("BTCUSDT", OrderBook([(D("59990"), D("100"))], [(D("60010"), D("100"))]))
    ex.set_order_book("ETHUSDT", OrderBook([(D("2999"), D("100"))], [(D("3001"), D("100"))]))
    ex.set_order_book("ETHBTC", OrderBook([(D("0.0480"), D("100"))], [(D("0.0482"), D("100"))]))     # mal coté : opportunité
    ex.set_candles("BTCUSDT", "1d", make_candles([60000.0] * 300))
    return ex


def team(tmp_path, paper=True, env="testnet"):
    ex = PaperExchange(market(), ["BTCUSDT", "ETHUSDT", "ETHBTC"], {"USDT": D("2000")}) if paper else market()
    clock = SimulatedClock(T0)
    store = Store(Database(":memory:"))
    settings = parse_settings({"env": env, "max_total_capital_quote": "500", "dry_run": False}, live_flag=env == "live")
    orch = Orchestrator(settings, ex, Ledger(D("500")), store, clock, tmp_path / "STOP",
                        limits=RuntimeLimits(reconcile_interval=timedelta(0), max_order_notional=D("150")))
    return orch, ex, clock, store


def tick(orch, clock):
    clock.advance(timedelta(minutes=2))
    return orch.run_once()


def cfg(text):
    return tomllib.loads(text)


def test_the_arbitrage_observer_runs_without_a_budget_and_reports_information_only(tmp_path):
    orch, ex, clock, store = team(tmp_path)
    build_team(cfg('[strategies.triangular]\nenabled = true\na = "BTC"\nb = "ETH"\nquote = "USDT"\nnotional = "100"\n'), orch, ex, T0)
    assert tick(orch, clock) == "OK"
    assert "triangular" in orch.units and orch.ledger.account("triangular").allocated == 0
    stats = orch.units["triangular"].runner.stats
    assert stats["scans"] == 1 and stats["opportunities"] == 1
    assert ("INFO", ) == tuple({lvl for lvl, txt in orch.alerts.history if "opportunité" in txt})      # information, jamais une alerte
    assert store.recent_orders(10) == []                                                                # aucun ordre


def test_the_market_maker_quotes_in_simulation_and_keeps_its_budget(tmp_path):
    orch, ex, clock, store = team(tmp_path)
    build_team(cfg('[strategies.marketmaker]\nenabled = true\nsymbol = "BTCUSDT"\nbudget_quote = "100"\n'), orch, ex, T0)
    tick(orch, clock)
    assert orch.ledger.account("marketmaker").allocated == D("100")
    bids = [o for o in ex.get_open_orders("BTCUSDT") if o.side.value == "BUY"]
    assert len(bids) == 1 and bids[0].avg_price < D("60000")


def test_the_market_maker_is_refused_outside_the_paper_simulation(tmp_path):
    orch, ex, *_ = team(tmp_path, paper=False)
    with pytest.raises(ConfigError, match="simulation"):
        build_team(cfg('[strategies.marketmaker]\nenabled = true\nsymbol = "BTCUSDT"\nbudget_quote = "100"\n'), orch, ex, T0)


def test_a_legacy_agent_trades_only_through_risk_and_execution(tmp_path):
    f = tmp_path / "legacy" / "old.py"
    f.parent.mkdir()
    f.write_text(LEGACY)
    orch, ex, clock, store = team(tmp_path)
    build_team(cfg(f'[strategies.legacy]\nenabled = true\npath = "{f}"\nclass = "OldAgent"\nsymbols = ["BTCUSDT"]\nbudget_quote = "150"\n'), orch, ex, T0)
    tick(orch, clock)
    assert orch.units["legacy"].runner.positions["BTCUSDT"] == D("0.001")
    assert any(r["agent_id"] == "legacy" and r["status"] == "FILLED" for r in store.recent_orders(10))
    assert any("agent existant" in text for _, text in orch.alerts.history)


def test_a_legacy_agent_with_plain_keys_is_refused_by_the_audit(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text('api_key = "ABCDEFGHIJKLMNOPQRSTUVWX1234567890"\nclass OldAgent:\n    def decide(self, s):\n        return []\n')
    orch, ex, *_ = team(tmp_path)
    with pytest.raises(ConfigError, match="clé en clair"):
        build_team(cfg(f'[strategies.legacy]\nenabled = true\npath = "{f}"\nclass = "OldAgent"\nsymbols = ["BTCUSDT"]\nbudget_quote = "100"\n'), orch, ex, T0)


def test_live_refuses_a_legacy_agent_that_was_not_promoted(tmp_path):
    f = tmp_path / "old.py"
    f.write_text(LEGACY)
    orch, ex, *_ = team(tmp_path, paper=False, env="live")
    raw = f'[strategies.legacy]\nenabled = true\npath = "{f}"\nclass = "OldAgent"\nsymbols = ["BTCUSDT"]\nbudget_quote = "100"\n'
    with pytest.raises(ConfigError, match="promoted"):
        build_team(cfg(raw), orch, ex, T0)


def test_regime_governance_covers_the_new_agents_without_errors(tmp_path):
    f = tmp_path / "old.py"
    f.write_text(LEGACY)
    orch, ex, clock, _ = team(tmp_path)
    build_team(cfg(f'[strategies.triangular]\nenabled = true\na = "BTC"\nb = "ETH"\nquote = "USDT"\nnotional = "100"\n'
                   f'[strategies.marketmaker]\nenabled = true\nsymbol = "BTCUSDT"\nbudget_quote = "100"\n'
                   f'[strategies.legacy]\nenabled = true\npath = "{f}"\nclass = "OldAgent"\nsymbols = ["BTCUSDT"]\nbudget_quote = "100"\n'), orch, ex, T0)
    tick(orch, clock)
    orch._apply_regime(Regime.TENDANCE_BAISSE)
    assert orch.ledger.account("marketmaker").allocated <= D("100")
    orch._apply_regime(Regime.RANGE)
    assert orch.ledger.account("marketmaker").allocated == D("100")


def test_strategy_symbols_include_the_triangle_and_the_legacy_symbols():
    raw = cfg('[strategies.triangular]\nenabled = true\na = "BTC"\nb = "ETH"\nquote = "USDT"\nnotional = "100"\n'
              '[strategies.legacy]\nenabled = true\npath = "x.py"\nclass = "A"\nsymbols = ["SOLUSDT"]\nbudget_quote = "10"\n')
    assert strategy_symbols(raw) == {"BTCUSDT", "ETHUSDT", "ETHBTC", "SOLUSDT"}


def test_the_state_of_the_new_agents_survives_a_restart(tmp_path):
    orch, ex, clock, store = team(tmp_path)
    build_team(cfg('[strategies.triangular]\nenabled = true\na = "BTC"\nb = "ETH"\nquote = "USDT"\nnotional = "100"\n'), orch, ex, T0)
    tick(orch, clock)
    saved = store.load_state("triangular")["stats"]
    assert saved["scans"] == 1
