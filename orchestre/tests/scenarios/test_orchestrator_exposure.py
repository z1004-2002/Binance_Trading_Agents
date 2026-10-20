"""Orchestrateur + stratèges à exposition cible : positions, stops de protection, arrêts, reprise, régimes."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.analysis.regimes import Regime
from orchestre.backtest.engine import Target
from orchestre.backtest.portfolio_engine import PortfolioTarget
from orchestre.config import parse_settings
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from orchestre.strategies.grid import GridConfig
from tests.scenarios.test_exposure_runners import Scripted, ScriptedPortfolio
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
F_ETH = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
GRID = GridConfig("grid", "BTCUSDT", D("55000"), D("65000"), 10, D("100"), D("0.001"), D("5"), D("0.01"), D("0.00001"))
ENTRY = Target(D("0.5"), D("57000"))


def build(tmp_path, ex=None, clock=None, store=None, ledger=None):
    ex = ex or FakeExchange({"BTCUSDT": F, "ETHUSDT": F_ETH}, {"BTCUSDT": D("60000"), "ETHUSDT": D("3000")}, {"USDT": D("2000")})
    for symbol, price in (("BTCUSDT", 60000.0), ("ETHUSDT", 3000.0)):
        if not ex.get_candles(symbol, "1d", 1):
            ex.set_candles(symbol, "1d", make_candles([price] * 300))
    clock = clock or SimulatedClock(T0)
    store = store or Store(Database(":memory:"))
    orch = Orchestrator(
        SETTINGS, ex, ledger or Ledger(D("500")), store, clock, tmp_path / "STOP",
        limits=RuntimeLimits(reconcile_interval=timedelta(0), max_order_notional=D("150")),
    )
    return orch, ex, clock, store


def tick(orch, clock, minutes=20):
    clock.advance(timedelta(minutes=minutes))
    return orch.run_once()


def stops(ex):
    return [o for o in ex.get_open_orders("BTCUSDT") if o.side.value == "SELL"]


def new_candle(ex, symbol="BTCUSDT"):
    c = ex.get_candles(symbol, "1d", 1000)
    ex.set_candles(symbol, "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))


def test_an_exposure_agent_opens_a_position_and_protects_it(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    s = Scripted([ENTRY])
    orch.add_exposure("trend", "S-3", s, "BTCUSDT", D("200"))
    assert tick(orch, clock) == "OK"
    leg = orch.units["trend"].runner.leg
    assert leg.qty > 0 and len(stops(ex)) == 1
    assert store.load_state("trend")["leg"]["stop_order_id"] == stops(ex)[0].client_order_id
    tick(orch, clock)
    assert len(s.calls) == 1 and len(stops(ex)) == 1          # même bougie : ni décision ni ordre en plus


def test_emergency_stop_keeps_the_protective_stop_and_cancels_the_rest(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    orch.add_exposure("trend", "S-3", Scripted([ENTRY]), "BTCUSDT", D("200"))
    orch.add_grid(GRID)
    tick(orch, clock)
    assert len(ex.get_open_orders("BTCUSDT")) > 2
    (tmp_path / "STOP").write_text("")
    assert tick(orch, clock) == "KILLED"
    remaining = ex.get_open_orders("BTCUSDT")
    assert len(remaining) == 1 and remaining[0].side.value == "SELL"        # seul le stop de protection reste (EX-SEC-14)


def test_restart_restores_the_position_the_stop_and_the_strategy_without_duplicates(tmp_path):
    db = Database(tmp_path / "o.db")
    orch, ex, clock, store = build(tmp_path, store=Store(db))
    orch.add_exposure("trend", "S-3", Scripted([ENTRY]), "BTCUSDT", D("200"))
    tick(orch, clock)
    before = sorted(o.client_order_id for o in ex.get_open_orders("BTCUSDT"))
    qty = orch.units["trend"].runner.leg.qty

    store2 = Store(Database(tmp_path / "o.db"))
    orch2, _, _, _ = build(tmp_path, ex=ex, clock=clock, store=store2, ledger=store2.load_ledger())
    s2 = Scripted([])
    orch2.add_exposure("trend", "S-3", s2, "BTCUSDT", D("200"))
    orch2.restore()
    assert tick(orch2, clock) == "OK"
    assert orch2.units["trend"].runner.leg.qty == qty and s2.calls == []
    assert sorted(o.client_order_id for o in ex.get_open_orders("BTCUSDT")) == before


def test_a_triggered_stop_raises_an_alert_and_closes_the_leg(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    orch.add_exposure("trend", "S-3", Scripted([ENTRY]), "BTCUSDT", D("200"))
    tick(orch, clock)
    ex.set_price("BTCUSDT", D("56900"))
    tick(orch, clock)
    assert orch.units["trend"].runner.leg.qty == 0 and stops(ex) == []
    assert any("stop déclenché" in text for _, text in orch.alerts.history)


def test_a_gap_below_the_stop_limit_triggers_a_critical_alert_and_a_market_exit(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    orch.add_exposure("trend", "S-3", Scripted([ENTRY]), "BTCUSDT", D("200"))
    tick(orch, clock)
    ex.set_price("BTCUSDT", D("50000"))
    tick(orch, clock)
    assert orch.units["trend"].runner.leg.qty == 0 and stops(ex) == []
    assert any(level == "CRITICAL" and "urgence" in text for level, text in orch.alerts.history)


def test_an_unfavourable_regime_exits_positions_frees_the_budget_and_resumes_later(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    orch.add_exposure("trend", "S-3", Scripted([ENTRY]), "BTCUSDT", D("200"))
    tick(orch, clock)
    assert orch.units["trend"].runner.leg.qty > 0

    orch._apply_regime(Regime.TENDANCE_BAISSE)             # S-3 : multiplicateur 0
    assert orch.units["trend"].runner.suspended
    tick(orch, clock)
    assert orch.units["trend"].runner.leg.qty == 0 and stops(ex) == []
    acc = orch.ledger.account("trend")
    assert acc.allocated == acc.committed < D("1")              # budget libéré, au reliquat de frais payés près

    orch._apply_regime(Regime.TENDANCE_HAUSSE)
    assert not orch.units["trend"].runner.suspended and orch.ledger.account("trend").allocated == D("200")


def test_a_portfolio_agent_trades_several_pairs_each_with_its_own_stop(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    strategy = ScriptedPortfolio([PortfolioTarget({"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")})])
    orch.add_portfolio("rotation", "S-6", strategy, ["BTCUSDT", "ETHUSDT"], D("200"), positions=2)
    assert tick(orch, clock) == "OK"
    legs = orch.units["rotation"].runner.legs
    assert legs["BTCUSDT"].qty > 0 and legs["ETHUSDT"].qty > 0
    assert len(ex.get_open_orders("BTCUSDT")) == 1 and len(ex.get_open_orders("ETHUSDT")) == 1


def test_budget_of_exposure_agents_counts_against_the_investable_capital(tmp_path):
    orch, *_ = build(tmp_path)
    orch.add_exposure("a", "S-3", Scripted([]), "BTCUSDT", D("250"))
    with pytest.raises(ValueError, match="investissable"):
        orch.add_exposure("b", "S-4", Scripted([]), "BTCUSDT", D("200"))


def test_the_agent_stays_within_its_budget_when_risk_refuses(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    orch.add_exposure("trend", "S-3", Scripted([Target(D("1"), D("57000"))]), "BTCUSDT", D("200"))
    orch.ledger.commit("trend", D("195"))
    tick(orch, clock)
    assert orch.units["trend"].runner.leg.qty == 0 and orch.units["trend"].runner.rejections
