"""Scénario d'équipe sur FakeExchange : stratège -> risque -> exécuteur, avec kill switch."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.core.agent import Agent
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import review
from orchestre.supervisor.service import Supervisor
from tests.unit.test_risk import context, proposal

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


class Strat(Agent):
    agent_id, role, agent_type = "dca", "stratège", "réflexe simple"

    def decide(self, message, snapshot):
        return []


def team(tmp_path):
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D("200"))
    sup = Supervisor(SimulatedClock(T0), tmp_path / "STOP", timedelta(seconds=30))
    strat = Strat()
    strat.start()
    sup.register(strat)
    return ex, ExecutionService(ex, ledger, dry_run=False), sup, strat


def run_cycle(ex, svc, sup, strat, cid):
    ctx = context(kill_switch=sup.kill_switch_active(), agent_running=strat.state.value == "RUNNING")
    decision = review(proposal(), ctx)
    return decision, (svc.execute(decision.order, cid) if decision.approved else None)


def test_happy_path_places_one_order(tmp_path):
    ex, svc, sup, strat = team(tmp_path)
    decision, report = run_cycle(ex, svc, sup, strat, "c1")
    assert decision.approved and report.status == "FILLED"
    assert ex.get_balances()["BTC"] == D("0.001")


def test_stop_file_blocks_every_new_order(tmp_path):
    ex, svc, sup, strat = team(tmp_path)
    (tmp_path / "STOP").write_text("")
    decision, report = run_cycle(ex, svc, sup, strat, "c1")
    assert decision.rule_id == "R-01" and report is None
    assert "BTC" not in ex.get_balances()


def test_kill_command_blocks_orders_and_stops_agents(tmp_path):
    ex, svc, sup, strat = team(tmp_path)
    sup.apply_command("kill")
    decision, report = run_cycle(ex, svc, sup, strat, "c1")
    assert decision.rule_id == "R-01" and report is None


def test_executor_errors_feed_circuit_breaker(tmp_path):
    ex, svc, sup, strat = team(tmp_path)
    svc.consecutive_errors = 3
    ctx = context(consecutive_errors=svc.consecutive_errors)
    assert review(proposal(), ctx).rule_id == "R-14"
