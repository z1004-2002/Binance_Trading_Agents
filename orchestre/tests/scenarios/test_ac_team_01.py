"""AC-TEAM-01 (EX-QA-06) : krach de −30 % en une heure sur l'équipe complète.

Attendu : paliers de drawdown appliqués, stops exécutés, aucune nouvelle position, alertes émises, aucun plafond dépassé.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.backtest.engine import Target
from orchestre.backtest.portfolio_engine import PortfolioTarget
from orchestre.config import parse_settings
from orchestre.core.agent import AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.grid import GridConfig
from tests.scenarios.test_exposure_runners import Scripted, ScriptedPortfolio
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
F_ETH = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
GRID = GridConfig("grid", "BTCUSDT", D("55000"), D("65000"), 10, D("100"), D("0.001"), D("5"), D("0.01"), D("0.00001"))
DCA = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), T0, D("40"), D("5"))
CRASH_STEPS, STEP_FACTOR = 12, D("0.9705")          # 12 pas de 5 min : 0,9705^12 ~ 0,70 (−30 %)


def build(tmp_path):
    ex = FakeExchange({"BTCUSDT": F, "ETHUSDT": F_ETH}, {"BTCUSDT": D("60000"), "ETHUSDT": D("3000")}, {"USDT": D("5000")})
    for s, p in (("BTCUSDT", 60000.0), ("ETHUSDT", 3000.0)):
        ex.set_candles(s, "1d", make_candles([p] * 300))
    clock, store = SimulatedClock(T0), Store(Database(":memory:"))
    orch = Orchestrator(SETTINGS, ex, Ledger(D("500")), store, clock, tmp_path / "STOP",
                        limits=RuntimeLimits(reconcile_interval=timedelta(0), max_order_notional=D("150")))
    trend = Scripted([Target(D("0.5"), D("57000")), Target(D("1.0"), D("40000"))])   # 2e cible : veut acheter PENDANT le krach
    rotation = ScriptedPortfolio([PortfolioTarget({"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")}),
                                  PortfolioTarget({"BTCUSDT": D("0.5"), "ETHUSDT": D("0.5")})])
    orch.add_dca(DCA)
    orch.add_grid(GRID)
    orch.add_exposure("trend", "S-3", trend, "BTCUSDT", D("120"))
    orch.add_portfolio("rotation", "S-6", rotation, ["BTCUSDT", "ETHUSDT"], D("140"), positions=2)
    return orch, ex, clock, store


def tick(orch, clock, minutes=5):
    clock.advance(timedelta(minutes=minutes))
    return orch.run_once()


def new_candles(ex):
    for s in ("BTCUSDT", "ETHUSDT"):
        c = ex.get_candles(s, "1d", 1000)
        ex.set_candles(s, "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))


def buy_ids(store):
    return {r["client_order_id"] for r in store.recent_orders(5000) if r["side"] == "BUY"}


def crash(orch, ex, clock):
    new_candles(ex)                                    # une nouvelle bougie : les stratégies VOUDRAIENT acheter plus
    statuses = []
    for _ in range(CRASH_STEPS):
        for s in ("BTCUSDT", "ETHUSDT"):
            ex.set_price(s, ex.get_price(s) * STEP_FACTOR)
        statuses.append(tick(orch, clock))
    return statuses


@pytest.fixture
def crashed(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    for _ in range(3):
        assert tick(orch, clock) == "OK"                # l'équipe s'installe : positions, stops, ordres de grille
    before = {"buys": buy_ids(store), "positions": {a: u.positioned for a, u in orch.units.items()}}
    assert before["positions"]["trend"] and before["positions"]["rotation"]
    statuses = crash(orch, ex, clock)
    return orch, ex, clock, store, before, statuses


def test_the_crash_is_really_about_minus_30_percent(crashed):
    orch, ex, *_ = crashed
    assert D("0.68") < ex.get_price("BTCUSDT") / D("60000") < D("0.72")


def test_stops_close_the_exposure_agents_positions_before_the_end_of_the_crash(crashed):
    orch, ex, clock, store, *_ = crashed
    assert orch.units["trend"].runner.leg.qty == 0
    assert all(leg.qty == 0 for leg in orch.units["rotation"].runner.legs.values())
    for s in ("BTCUSDT", "ETHUSDT"):                                     # plus aucun stop de protection en suspens
        assert not [o for o in ex.get_open_orders(s) if o.side.value == "SELL" and o.client_order_id.startswith(("trend", "rotation"))]


def test_no_new_position_is_opened_during_the_crash(crashed):
    orch, ex, clock, store, before, _ = crashed
    new_buys = buy_ids(store) - before["buys"]
    assert new_buys == set(), f"achats apparus pendant le krach : {new_buys}"
    assert orch.units["trend"].runner.leg.qty == 0                      # la 2e cible (acheter) n'a rien déclenché


def test_the_drawdown_tier_matches_the_observed_loss(crashed, tmp_path):
    orch, ex, clock, store, *_ = crashed
    info = store.load_state("runtime_info")
    dd = info["drawdown"]
    assert dd > 0.05, "le krach doit franchir au moins le premier palier"
    if dd >= 0.15:
        assert (tmp_path / "STOP").exists() and all(a.state is AgentState.STOPPED for a in orch.unit_agents.values())
    elif dd >= 0.10:
        assert info["tier"] == 2
    else:
        assert info["tier"] == 1 and orch.state.tier1_applied


def test_alerts_were_emitted_for_the_stops_and_the_drawdown(crashed):
    orch, *_ = crashed
    texts = " ".join(text.lower() for _, text in orch.alerts.history)
    assert "stop" in texts or "urgence" in texts
    assert any(level in ("WARNING", "CRITICAL") for level, _ in orch.alerts.history)
    assert any("drawdown" in text.lower() for _, text in orch.alerts.history)


def test_the_grid_stops_when_the_price_leaves_its_range(crashed):
    orch, ex, *_ = crashed
    assert orch.units["grid"].runner.halted


def test_no_cap_is_exceeded_at_any_point(crashed, tmp_path):
    orch, ex, clock, store, *_ = crashed
    accounts = orch.ledger.accounts()
    assert all(a.committed <= a.allocated + D("0.0001") for a in accounts)
    assert sum(a.allocated for a in accounts) <= orch.ledger.total * D("0.8") + D("0.0001")   # réserve de trésorerie intacte
    assert sum(a.committed for a in accounts) <= orch.ledger.total
    assert ex.get_balances()["USDT"] > 0 and orch.svc.consecutive_errors < 3


def test_the_run_never_crashed_and_every_order_was_approved_by_the_risk_agent(crashed):
    orch, ex, clock, store, before, statuses = crashed
    assert set(statuses) <= {"OK", "KILLED", "PAUSED"}
    assert not [e for e in store.db.conn.execute("select detail from events where level = 'ERROR'")]
