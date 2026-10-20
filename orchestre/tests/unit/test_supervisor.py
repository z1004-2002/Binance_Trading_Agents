from datetime import datetime, timedelta, timezone

import pytest

from orchestre.core.agent import Agent, AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.supervisor.service import Supervisor

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


class Dummy(Agent):
    role = "test"
    agent_type = "réflexe simple"

    def __init__(self, agent_id, critical=False):
        super().__init__()
        self.agent_id = agent_id
        self.critical = critical

    def decide(self, message, snapshot):
        return []


@pytest.fixture
def setup(tmp_path):
    clock = SimulatedClock(T0)
    sup = Supervisor(clock, stop_file=tmp_path / "STOP", heartbeat_timeout=timedelta(seconds=30))
    risk = Dummy("risk", critical=True)
    dca = Dummy("dca")
    for a in (risk, dca):
        a.start()
        sup.register(a, critical=a.critical)
    return clock, sup, risk, dca


def test_silent_critical_agent_pauses_strategists(setup):
    clock, sup, risk, dca = setup
    sup.heartbeat("risk")
    sup.heartbeat("dca")
    clock.advance(timedelta(seconds=31))
    sup.heartbeat("dca")
    alerts = sup.check_health()
    assert dca.state is AgentState.PAUSED
    assert any("risk" in a for a in alerts)


def test_silent_noncritical_agent_is_degraded_only(setup):
    clock, sup, risk, dca = setup
    sup.heartbeat("risk")
    clock.advance(timedelta(seconds=31))
    sup.heartbeat("risk")
    sup.check_health()
    assert dca.state is AgentState.DEGRADED and risk.state is AgentState.RUNNING


def test_healthy_agents_stay_running(setup):
    clock, sup, risk, dca = setup
    sup.heartbeat("risk")
    sup.heartbeat("dca")
    clock.advance(timedelta(seconds=10))
    assert sup.check_health() == []
    assert risk.state is dca.state is AgentState.RUNNING


def test_stop_file_activates_kill_switch(setup, tmp_path):
    _, sup, _, _ = setup
    assert not sup.kill_switch_active()
    (tmp_path / "STOP").write_text("")
    assert sup.kill_switch_active()


def test_kill_switch_command_stops_all_agents(setup):
    _, sup, risk, dca = setup
    sup.apply_command("kill")
    assert sup.kill_switch_active()
    assert risk.state is dca.state is AgentState.STOPPED


def test_pause_and_resume_commands(setup):
    _, sup, _, dca = setup
    sup.apply_command("pause", "dca")
    assert dca.state is AgentState.PAUSED
    sup.apply_command("resume", "dca")
    assert dca.state is AgentState.RUNNING


def test_resume_refused_while_kill_switch_active(setup):
    _, sup, _, dca = setup
    sup.apply_command("kill")
    with pytest.raises(PermissionError):
        sup.apply_command("resume", "dca")


def test_unknown_command_refused(setup):
    _, sup, _, _ = setup
    with pytest.raises(ValueError):
        sup.apply_command("withdraw")
