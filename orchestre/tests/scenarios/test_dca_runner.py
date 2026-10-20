from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.exchange.base import IpBannedError
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.dca_runner import DcaRunner
from tests.unit.test_risk import context

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
START = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
CFG = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), START, D("100"), D("5"))


def build(dry_run=False, budget="100"):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("50000")}, {"USDT": D("1000")})
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D(budget))
    svc = ExecutionService(ex, ledger, dry_run=dry_run)
    ctx = lambda p: replace(context(), agent_budget_available=ledger.account("dca").available)
    return ex, ledger, svc, DcaRunner(CFG, svc, ctx)


def test_buys_on_schedule_and_updates_ledger():
    ex, ledger, _, runner = build()
    r = runner.step(START, D("50000"))
    assert r.status == "FILLED" and ex.get_balances()["BTC"] == D("0.0004")
    assert ledger.account("dca").committed == D("20.02")
    assert runner.state.total_spent == D("20")


def test_one_purchase_per_slot_even_when_ticked_repeatedly():
    ex, _, _, runner = build()
    runner.step(START, D("50000"))
    for minutes in (1, 30, 600):
        assert runner.step(START + timedelta(minutes=minutes), D("50000")) is None
    assert ex.get_balances()["BTC"] == D("0.0004")


def test_next_week_buys_again():
    ex, _, _, runner = build()
    runner.step(START, D("50000"))
    runner.step(START + timedelta(days=7), D("40000"))
    assert ex.get_balances()["BTC"] == D("0.0004") + D("0.0005")


def test_dry_run_advances_schedule_without_touching_ledger():
    ex, ledger, _, runner = build(dry_run=True)
    assert runner.step(START, D("50000")).status == "DRY_RUN"
    assert ledger.account("dca").committed == D("0") and "BTC" not in ex.get_balances()
    assert runner.step(START + timedelta(hours=1), D("50000")) is None


def test_risk_rejection_is_recorded_and_slot_is_retried():
    ex, _, _, runner = build(budget="10")
    assert runner.step(START, D("50000")) is None
    assert runner.rejections[0].rule_id == "R-03" and "BTC" not in ex.get_balances()
    assert runner.state.last_slot is None


def test_state_roundtrip():
    _, _, _, runner = build()
    runner.step(START, D("50000"))
    state = runner.export_state()
    _, _, _, fresh = build()
    fresh.import_state(state)
    assert fresh.state == runner.state
    assert fresh.step(START + timedelta(hours=2), D("50000")) is None


def test_ip_ban_propagates_to_the_orchestrator():
    ex, _, svc, runner = build()

    def banned(_request):
        raise IpBannedError("418")

    ex.place_order = banned
    with pytest.raises(IpBannedError):
        runner.step(START, D("50000"))
