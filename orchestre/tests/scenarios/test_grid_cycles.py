"""Scénario : Grid complet sur FakeExchange (ordres initiaux, remplissages, réinvestissement, profit)."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.strategies.grid import GridConfig, build_levels
from orchestre.strategies.grid_runner import GridRunner
from tests.unit.test_risk import context

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
CFG = GridConfig(
    agent_id="grid", symbol="BTCUSDT", lower=D("55000"), upper=D("65000"), intervals=20,
    budget_quote=D("200"), fee_rate=D("0.001"), min_notional=D("5"),
    tick_size=D("0.01"), step_size=D("0.00001"),
)
LEVELS = build_levels(CFG)


def build(price="60000"):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D(price)}, {"USDT": D("1000")})
    ledger = Ledger(D("500"))
    ledger.allocate("grid", D("200"))
    svc = ExecutionService(ex, ledger, dry_run=False)

    def ctx(_proposal):
        return replace(
            context(),
            agent_budget_available=ledger.account("grid").available,
            balance_quote=ex.get_balances()["USDT"],
            open_orders_symbol=len(ex.get_open_orders("BTCUSDT")),
            open_orders_agent=len(ex.get_open_orders("BTCUSDT")),
            max_open_orders_agent=100,
            max_orders_per_day=1000,
            max_price_deviation_pct=D("0.25"),
        )

    return ex, ledger, svc, GridRunner(CFG, svc, ctx)


def move(ex, runner, price):
    ex.set_price("BTCUSDT", price)
    runner.step(price)


def test_start_places_one_buy_per_level_below_price():
    ex, ledger, _, runner = build()
    runner.start(D("60000"))
    open_orders = ex.get_open_orders("BTCUSDT")
    assert open_orders and all(o.side is Side.BUY for o in open_orders)
    assert all(o.avg_price < D("60000") for o in open_orders)
    assert runner.rejections == []
    assert ledger.account("grid").committed > 0


def test_buy_fill_triggers_sell_one_level_up():
    ex, _, _, runner = build()
    runner.start(D("60000"))
    k = max(i for i, p in enumerate(LEVELS) if p < D("60000"))
    move(ex, runner, LEVELS[k] - D("1"))
    sells = [o for o in ex.get_open_orders("BTCUSDT") if o.side is Side.SELL]
    assert any(o.avg_price == LEVELS[k + 1] for o in sells)


def test_full_cycle_realizes_positive_net_profit():
    ex, _, _, runner = build()
    runner.start(D("60000"))
    k = max(i for i, p in enumerate(LEVELS) if p < D("60000"))
    move(ex, runner, LEVELS[k] - D("1"))   # l'achat du niveau k est exécuté
    move(ex, runner, LEVELS[k + 1] + D("1"))  # la vente du niveau k+1 est exécutée
    assert runner.cycles_completed >= 1 and runner.realized_profit > 0
    # profit net par cycle ~ 10 USDT x 0,64 % ~ 0,06
    assert D("0.03") < runner.realized_profit / runner.cycles_completed < D("0.10")


def test_oscillations_accumulate_profit_without_errors():
    ex, ledger, svc, runner = build()
    runner.start(D("60000"))
    k = max(i for i, p in enumerate(LEVELS) if p < D("60000"))
    for _ in range(3):
        move(ex, runner, LEVELS[k] - D("1"))
        move(ex, runner, LEVELS[k + 1] + D("1"))
    assert runner.cycles_completed >= 3
    assert svc.consecutive_errors == 0 and runner.rejections == []
    total = ledger.unallocated + sum(a.allocated for a in ledger.accounts())
    assert total == D("500")
    assert svc.reconcile("BTCUSDT") == []


def test_cancel_all_leaves_no_orphan_orders():
    ex, _, svc, runner = build()
    runner.start(D("60000"))
    svc.cancel_all_open("BTCUSDT")
    assert ex.get_open_orders("BTCUSDT") == [] and svc.reconcile("BTCUSDT") == []


def test_price_below_stop_halts_grid_and_cancels_orders():
    ex, _, _, runner = build()
    runner.start(D("60000"))
    move(ex, runner, D("53000"))  # < 55000 x 0,98
    assert runner.halted and ex.get_open_orders("BTCUSDT") == []
    runner.step(D("60000"))
    assert ex.get_open_orders("BTCUSDT") == []


def test_rejections_are_recorded_when_budget_too_small():
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})
    ledger = Ledger(D("500"))
    ledger.allocate("grid", D("30"))  # largement sous le besoin
    svc = ExecutionService(ex, ledger, dry_run=False)
    runner = GridRunner(
        CFG, svc,
        lambda p: replace(
            context(),
            agent_budget_available=ledger.account("grid").available,
            max_open_orders_agent=100, max_price_deviation_pct=D("0.25"),
        ),
    )
    runner.start(D("60000"))
    assert runner.rejections and runner.rejections[0].rule_id == "R-03"
