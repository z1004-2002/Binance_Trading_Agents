from datetime import datetime, timedelta, timezone

from orchestre.core.bus import MessageBus
from orchestre.core.clock import SimulatedClock
from orchestre.core.messages import Message, Performative

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


def msg(topic="prices", ttl=10):
    return Message("md", topic, Performative.INFORM, {}, "test", T0, timedelta(seconds=ttl))


def test_delivers_to_subscribers_of_topic_only():
    bus = MessageBus(SimulatedClock(T0))
    got, other = [], []
    bus.subscribe("prices", got.append)
    bus.subscribe("risk", other.append)
    bus.publish(msg())
    assert len(got) == 1 and other == []


def test_expired_messages_are_not_delivered_but_are_logged():
    clock = SimulatedClock(T0)
    bus = MessageBus(clock)
    got = []
    bus.subscribe("prices", got.append)
    clock.advance(timedelta(seconds=11))
    bus.publish(msg())
    assert got == [] and len(bus.log) == 1


def test_all_messages_are_journaled_in_order():
    bus = MessageBus(SimulatedClock(T0))
    a, b = msg(), msg("risk")
    bus.publish(a)
    bus.publish(b)
    assert bus.log == [a, b]


def test_a_failing_handler_does_not_block_others():
    bus = MessageBus(SimulatedClock(T0))
    got = []

    def boom(_):
        raise RuntimeError("x")

    bus.subscribe("prices", boom)
    bus.subscribe("prices", got.append)
    bus.publish(msg())
    assert len(got) == 1 and len(bus.handler_errors) == 1
