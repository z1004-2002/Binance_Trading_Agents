from datetime import datetime, timedelta, timezone

import pytest

from orchestre.core.agent import Agent, AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.core.messages import Message, Performative

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


def msg(**kw):
    base = dict(
        sender="a", topic="t", performative=Performative.INFORM, content={},
        reason="test", timestamp=T0, ttl=timedelta(seconds=10),
    )
    base.update(kw)
    return Message(**base)


def test_message_requires_reason():
    with pytest.raises(ValueError):
        msg(reason="  ")


def test_message_requires_tz():
    with pytest.raises(ValueError):
        msg(timestamp=datetime(2026, 10, 20))


def test_message_expiry():
    m = msg()
    assert not m.is_expired(T0 + timedelta(seconds=9))
    assert m.is_expired(T0 + timedelta(seconds=10))


def test_message_ids_unique():
    assert msg().message_id != msg().message_id


def test_simulated_clock_advances():
    c = SimulatedClock(T0)
    c.advance(timedelta(hours=1))
    assert c.now() == T0 + timedelta(hours=1)


def test_simulated_clock_requires_tz():
    with pytest.raises(ValueError):
        SimulatedClock(datetime(2026, 1, 1))


class Echo(Agent):
    agent_id = "echo"
    role = "test"
    agent_type = "réflexe simple"

    def decide(self, message, snapshot):
        return [message]


def test_agent_silent_unless_running():
    a = Echo()
    assert a.handle(msg(), {}) == []
    a.start()
    assert len(a.handle(msg(), {})) == 1
    a.degrade()
    assert a.state is AgentState.DEGRADED and a.handle(msg(), {}) == []
