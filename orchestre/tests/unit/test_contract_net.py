import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.core.bus import MessageBus
from orchestre.core.clock import SimulatedClock
from orchestre.core.messages import Performative
from orchestre.portfolio.allocator_v2 import AllocatorConfig
from orchestre.portfolio.contract_net import Bidder, ContractNet

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)
CFG = AllocatorConfig(max_weight=1.0, max_step=1.0)


def noisy(mean, vol, seed=1, n=60):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, vol) for _ in range(n)]


def setup(bidders):
    clock = SimulatedClock(T0)
    bus = MessageBus(clock)
    for b in bidders:
        b.attach(bus, clock)
    return bus, ContractNet(bus, clock, CFG)


def bidder(agent, min_viable, mean=0.003, vol=0.01, multiplier=1.0, seed=1):
    return Bidder(agent, D(min_viable), lambda: noisy(mean, vol, seed), lambda: multiplier)


def performatives(bus):
    return [(m.performative, m.sender) for m in bus.log]


def test_protocol_sequence_cfp_then_proposals_then_decisions():
    bus, cnp = setup([bidder("dca", "10"), bidder("grid", "60")])  # historiques identiques : poids égaux
    result = cnp.run(D("300"))
    kinds = [p for p, _ in performatives(bus)]
    assert kinds[0] is Performative.CFP
    assert kinds[1:3] == [Performative.PROPOSE] * 2
    assert sorted(kinds[3:]) == sorted([Performative.ACCEPT_PROPOSAL] * 2)
    assert set(result.allocations) == {"dca", "grid"}


def test_messages_are_linked_by_conversation_and_replies():
    bus, cnp = setup([bidder("dca", "10"), bidder("grid", "60")])
    cnp.run(D("300"))
    cfp = bus.log[0]
    proposals = [m for m in bus.log if m.performative is Performative.PROPOSE]
    decisions = [m for m in bus.log if m.performative in (Performative.ACCEPT_PROPOSAL, Performative.REJECT_PROPOSAL)]
    assert {m.conversation_id for m in bus.log} == {cfp.conversation_id}
    assert all(p.in_reply_to == cfp.message_id for p in proposals)
    assert {d.in_reply_to for d in decisions} == {p.message_id for p in proposals}
    assert all(m.reason for m in bus.log)


def test_allocations_never_exceed_the_budget_and_follow_scores():
    bus, cnp = setup([bidder("a", "1", mean=0.004, seed=1), bidder("b", "1", mean=0.003, seed=1)])
    result = cnp.run(D("300"))
    assert sum(result.allocations.values()) <= D("300")
    assert result.allocations["a"] > result.allocations["b"] > 0


def test_a_bid_below_its_minimum_viable_budget_is_rejected_and_the_budget_redistributed():
    strong = bidder("a", "10", mean=0.004, vol=0.01, seed=1)
    weak = bidder("b", "100", mean=0.001, vol=0.02, seed=2)   # n'obtiendrait qu'une petite part, sous son minimum
    bus, cnp = setup([strong, weak])
    result = cnp.run(D("300"))
    assert result.allocations == {"a": D("300.00")}                 # le budget est revenu au stratège retenu
    assert "minimum viable" in result.rejected["b"]
    rejects = [m for m in bus.log if m.performative is Performative.REJECT_PROPOSAL]
    assert len(rejects) == 1 and "minimum viable" in rejects[0].reason


def test_zero_multiplier_means_a_reasoned_rejection():
    bus, cnp = setup([bidder("grid", "60", multiplier=0.0), bidder("dca", "10", seed=2)])
    result = cnp.run(D("300"))
    assert "grid" not in result.allocations
    reject = [m for m in bus.log if m.performative is Performative.REJECT_PROPOSAL][0]
    assert "régime" in reject.reason


def test_no_bidder_means_no_allocation_and_no_crash():
    bus, cnp = setup([])
    assert cnp.run(D("300")).allocations == {}
    assert [m.performative for m in bus.log] == [Performative.CFP]


def test_accepted_messages_carry_the_amount():
    bus, cnp = setup([bidder("dca", "10")])
    result = cnp.run(D("300"))
    accept = [m for m in bus.log if m.performative is Performative.ACCEPT_PROPOSAL][0]
    assert D(accept.content["budget"]) == result.allocations["dca"]


def test_previous_weights_limit_the_weekly_change():
    bus, cnp = setup([bidder("a", "10", seed=1), bidder("b", "10", seed=2)])
    cnp._cfg = AllocatorConfig(max_weight=1.0, max_step=0.10)
    result = cnp.run(D("1000"), previous={"a": 0.2, "b": 0.2})
    assert all(abs(float(v) / 1000 - 0.2) <= 0.10 + 1e-9 for v in result.allocations.values())
