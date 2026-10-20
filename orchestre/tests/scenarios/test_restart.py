"""Reprise après arrêt brutal (A-7, EX-ST-06) : état restauré, réconciliation, cycles poursuivis."""

from dataclasses import replace
from decimal import Decimal as D
from datetime import datetime, timezone

from orchestre.exchange.fake import FakeExchange
from orchestre.execution.service import ExecutionService
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.strategies.grid import build_levels
from orchestre.strategies.grid_runner import GridRunner
from tests.scenarios.test_grid_cycles import CFG, F
from tests.unit.test_risk import context

LEVELS = build_levels(CFG)
T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)
K = max(i for i, p in enumerate(LEVELS) if p < D("60000"))


def wire(ex, ledger):
    svc = ExecutionService(ex, ledger, dry_run=False)

    def ctx(_p):
        return replace(
            context(),
            agent_budget_available=ledger.account("grid").available,
            balance_quote=ex.get_balances()["USDT"],
            max_open_orders_agent=100, max_orders_per_day=1000,
            max_price_deviation_pct=D("0.25"),
        )

    return svc, GridRunner(CFG, svc, ctx)


def save(store, ledger, svc, runner):
    store.save_all(ledger, svc.export_tracked(), {"grid": runner.export_state()}, T0)


def restore(store, ex):
    ledger = store.load_ledger()
    svc, runner = wire(ex, ledger)
    svc.import_tracked(store.load_tracked())
    runner.import_state(store.load_state("grid"))
    return ledger, svc, runner


def first_generation(tmp_path):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})
    ledger = Ledger(D("500"))
    ledger.allocate("grid", D("200"))
    svc, runner = wire(ex, ledger)
    runner.start(D("60000"))
    ex.set_price("BTCUSDT", LEVELS[K] - D("1"))
    runner.step(LEVELS[K] - D("1"))  # un achat exécuté, sa vente placée
    store = Store(Database(tmp_path / "o.db"))
    save(store, ledger, svc, runner)
    return ex, ledger, svc, runner, store


def test_state_is_restored_exactly(tmp_path):
    ex, ledger, svc, runner, store = first_generation(tmp_path)
    ledger2, svc2, runner2 = restore(Store(Database(tmp_path / "o.db")), ex)
    assert ledger2.account("grid").committed == ledger.account("grid").committed
    assert ledger2.unallocated == ledger.unallocated
    assert svc2.open_order_ids("BTCUSDT") == svc.open_order_ids("BTCUSDT")
    assert runner2.export_state() == runner.export_state()
    assert svc2.reconcile("BTCUSDT") == []


def test_cycle_completes_after_restart(tmp_path):
    ex, _, _, _, _ = first_generation(tmp_path)
    _, svc2, runner2 = restore(Store(Database(tmp_path / "o.db")), ex)
    ex.set_price("BTCUSDT", LEVELS[K + 1] + D("1"))
    runner2.step(LEVELS[K + 1] + D("1"))
    assert runner2.cycles_completed == 1 and runner2.realized_profit > 0
    assert svc2.consecutive_errors == 0


def test_fill_during_downtime_is_settled_on_restart(tmp_path):
    ex, _, _, _, _ = first_generation(tmp_path)
    ex.set_price("BTCUSDT", LEVELS[K + 1] + D("1"))  # la vente s'exécute pendant l'arrêt
    _, svc2, runner2 = restore(Store(Database(tmp_path / "o.db")), ex)
    runner2.step(LEVELS[K + 1] + D("1"))
    assert runner2.cycles_completed == 1
    assert svc2.reconcile("BTCUSDT") == []


def test_restart_does_not_duplicate_orders(tmp_path):
    ex, _, _, _, _ = first_generation(tmp_path)
    before = len(ex.get_open_orders("BTCUSDT"))
    _, _, runner2 = restore(Store(Database(tmp_path / "o.db")), ex)
    runner2.step(D("60000"))
    assert len(ex.get_open_orders("BTCUSDT")) == before
