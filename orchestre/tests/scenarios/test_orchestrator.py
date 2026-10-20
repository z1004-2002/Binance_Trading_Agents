"""Orchestrateur : équipe complète (DCA + Grid) sur FakeExchange, horloge simulée."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.config import parse_settings
from orchestre.core.agent import AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.base import IpBannedError
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.grid import GridConfig, build_levels
from orchestre.exchange.base import OrderRequest

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
GRID = GridConfig("grid", "BTCUSDT", D("55000"), D("65000"), 20, D("200"), D("0.001"), D("5"), D("0.01"), D("0.00001"))
DCA = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), T0, D("100"), D("5"))
LEVELS = build_levels(GRID)
K = max(i for i, p in enumerate(LEVELS) if p < D("60000"))


class World:
    def __init__(self, tmp_path, limits=None, with_grid=True, with_dca=True, db=None, ex=None, clock=None, ledger=None):
        self.ex = ex or FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
        self.clock = clock or SimulatedClock(T0)
        self.db_path = tmp_path / "o.db"
        self.store = Store(Database(self.db_path))
        self.orch = Orchestrator(
            SETTINGS, self.ex, ledger or Ledger(D("500")), self.store, self.clock,
            stop_file=tmp_path / "STOP", limits=limits or RuntimeLimits(reconcile_interval=timedelta(0)),
        )
        if with_grid:
            self.orch.add_grid(GRID)
        if with_dca:
            self.orch.add_dca(DCA)

    def tick(self, price=None, advance=timedelta(minutes=1)):
        if price is not None:
            self.ex.set_price("BTCUSDT", D(str(price)))
        self.clock.advance(advance)
        return self.orch.run_once()

    @property
    def alerts(self):
        return self.orch.alerts.history


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_first_tick_places_grid_buys_and_dca_purchase(w):
    assert w.tick() == "OK"
    assert len(w.ex.get_open_orders("BTCUSDT")) > 5
    assert w.ex.get_balances()["BTC"] == D("0.00033")  # DCA : 20 USDT à 60 000, arrondi au stepSize
    assert w.orch.svc.consecutive_errors == 0


def test_state_is_persisted_every_tick(w):
    w.tick()
    assert w.store.load_state("grid") is not None and w.store.load_state("dca") is not None
    assert w.store.load_ledger().account("grid").allocated == D("200")


def test_grid_cycles_run_through_the_orchestrator(w):
    w.tick()
    for _ in range(3):
        w.tick(LEVELS[K] - D("1"))
        w.tick(LEVELS[K + 1] + D("1"))
    assert w.orch.units["grid"].runner.cycles_completed >= 3
    assert not any(level == "CRITICAL" for level, _ in w.alerts)


def test_stop_file_cancels_orders_and_stops_everything(w, tmp_path):
    w.tick()
    (tmp_path / "STOP").write_text("")
    assert w.tick() == "KILLED"
    assert w.ex.get_open_orders("BTCUSDT") == []
    assert all(a.state is AgentState.STOPPED for a in w.orch.unit_agents.values())
    sent = w.orch.svc.orders_sent
    assert w.tick(61000) == "KILLED" and w.orch.svc.orders_sent == sent


def test_drawdown_15_percent_triggers_global_stop(w, tmp_path):
    w.tick()
    w.orch.state.peak_equity = D("1000")  # équipe à ~500 => drawdown ~50 %
    assert w.tick() == "KILLED"
    assert (tmp_path / "STOP").exists()  # l'arrêt survit à un redémarrage
    assert any("drawdown" in text.lower() for _, text in w.alerts)
    assert w.ex.get_open_orders("BTCUSDT") == []


def test_drawdown_5_percent_halves_budgets_once(w):
    w.tick()
    before = w.orch.ledger.account("grid").allocated
    w.orch.state.peak_equity = D("535")  # ~6,5 %
    w.tick()
    after = w.orch.ledger.account("grid").allocated
    assert after < before
    w.tick()
    assert w.orch.ledger.account("grid").allocated == after


def test_drawdown_10_percent_freezes_new_positions(w):
    w.tick()
    w.orch.state.peak_equity = D("570")  # ~12 %
    sent = w.orch.svc.orders_sent
    w.tick(LEVELS[K] - D("1"))
    w.tick(LEVELS[K + 1] + D("1"))
    buys_after = [d for d in w.orch.units["grid"].runner.rejections if d.rule_id == "R-13"]
    assert buys_after  # le risque refuse les nouveaux achats ; seules les ventes de réduction passent


def test_ip_ban_pauses_the_whole_team(w):
    w.tick()

    def banned(symbol):
        raise IpBannedError("418")

    w.ex.get_price = banned
    assert w.tick() == "PAUSED"
    assert all(a.state is AgentState.PAUSED for a in w.orch.unit_agents.values())
    assert any(level == "CRITICAL" and "418" in text for level, text in w.alerts)


def test_a_failing_unit_does_not_stop_the_others(w):
    w.tick()

    def boom(now, price):
        raise RuntimeError("bug dans la stratégie")

    w.orch.units["grid"].tick = boom
    for _ in range(3):
        w.tick()
    assert w.orch.unit_agents["grid"].state is AgentState.DEGRADED
    assert w.orch.unit_agents["dca"].state is AgentState.RUNNING


def test_market_data_outage_pauses_strategists_via_supervisor(w):
    w.tick()
    w.ex.get_price = lambda symbol: (_ for _ in ()).throw(ConnectionError("hors ligne"))
    for _ in range(7):
        w.tick(advance=timedelta(minutes=1))
    assert all(a.state is AgentState.PAUSED for a in w.orch.unit_agents.values() if a.agent_id in ("grid", "dca"))


def test_startup_reconciliation_pauses_on_unknown_orders(tmp_path):
    w = World(tmp_path)
    w.ex.set_price("BTCUSDT", D("60000"))
    w.ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), D("58000.00"), "mystery"))
    assert w.tick() == "PAUSED"
    assert any("orphelin" in text for _, text in w.alerts)
    assert w.orch.svc.orders_sent == 0  # rien n'a été envoyé avant la réconciliation


def test_restart_restores_state_without_duplicating_orders(tmp_path):
    w = World(tmp_path)
    w.tick()
    w.tick(LEVELS[K] - D("1"))
    open_before = sorted(o.client_order_id for o in w.ex.get_open_orders("BTCUSDT"))
    btc_before = w.ex.get_balances()["BTC"]

    ledger = w.store.load_ledger()
    w2 = World(tmp_path, ex=w.ex, clock=w.clock, ledger=ledger)
    w2.orch.restore()
    assert w2.tick() == "OK"
    assert sorted(o.client_order_id for o in w2.ex.get_open_orders("BTCUSDT")) == open_before
    assert w2.ex.get_balances()["BTC"] == btc_before


def test_one_grid_per_pair(tmp_path):
    w = World(tmp_path, with_dca=False)
    with pytest.raises(ValueError, match="un seul Grid"):
        w.orch.add_grid(GridConfig("grid2", "BTCUSDT", D("50000"), D("70000"), 10, D("60"), D("0.001"), D("5"), D("0.01"), D("0.00001")))


def test_budgets_cannot_exceed_investable_capital(tmp_path):
    w = World(tmp_path, with_grid=False, with_dca=False)
    w.orch.add_grid(GRID)  # 200
    with pytest.raises(ValueError, match="investissable"):
        w.orch.add_grid(GridConfig("g2", "ETHUSDT", D("2000"), D("3000"), 20, D("300"), D("0.001"), D("5"), D("0.01"), D("0.0001")))


def test_daily_order_cap_is_enforced_by_risk(tmp_path):
    w = World(tmp_path, limits=RuntimeLimits(max_orders_per_day=3, reconcile_interval=timedelta(0)))
    w.tick()
    assert w.orch.svc.orders_sent <= 3
    assert any(d.rule_id == "R-15" for u in w.orch.units.values() for d in u.runner.rejections)


def test_live_loop_runs_a_bounded_number_of_ticks(w):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        w.clock.advance(timedelta(seconds=s))

    ticks = asyncio.run(w.orch.run(interval=30, max_ticks=3, sleep=fake_sleep))
    assert ticks == 3 and sleeps == [30, 30]


def test_loop_exits_after_kill(w, tmp_path):
    (tmp_path / "STOP").write_text("")

    async def nosleep(s):
        pass

    assert asyncio.run(w.orch.run(interval=1, max_ticks=10, sleep=nosleep)) == 1
