from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.strategies.dca import DcaConfig, DcaState, decide, record

START = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # lundi
CFG = DcaConfig(
    agent_id="dca", symbol="BTCUSDT", amount_quote=D("20"), interval=timedelta(days=7),
    start=START, cumulative_cap=D("100"), min_notional=D("5"),
)


def test_amount_below_min_notional_refused():
    with pytest.raises(ValueError):
        DcaConfig("dca", "BTCUSDT", D("4"), timedelta(days=7), START, D("100"), D("5"))


def test_nothing_before_first_slot():
    assert decide(CFG, DcaState(), START - timedelta(seconds=1), D("60000")) is None


def test_buys_on_due_slot():
    o = decide(CFG, DcaState(), START, D("50000"))
    assert o.amount_quote == D("20") and o.quantity == D("0.0004")
    assert o.slot == START and "dca" in o.client_order_id


def test_never_two_buys_for_the_same_slot():
    o = decide(CFG, DcaState(), START, D("50000"))
    st = record(DcaState(), o)
    assert decide(CFG, st, START + timedelta(hours=3), D("50000")) is None


def test_next_slot_a_week_later():
    st = record(DcaState(), decide(CFG, DcaState(), START, D("50000")))
    o = decide(CFG, st, START + timedelta(days=7), D("40000"))
    assert o.slot == START + timedelta(days=7) and o.quantity == D("0.0005")


def test_client_order_id_is_deterministic_per_slot():
    a = decide(CFG, DcaState(), START, D("50000"))
    b = decide(CFG, DcaState(), START + timedelta(hours=1), D("51000"))
    assert a.client_order_id == b.client_order_id


def test_missed_slots_skip_policy_buys_once():
    o = decide(CFG, DcaState(), START + timedelta(days=21), D("50000"))
    assert o.amount_quote == D("20")


def test_missed_slots_single_catchup_policy():
    cfg = DcaConfig(**{**CFG.__dict__, "missed_policy": "catchup_once"})
    o = decide(cfg, DcaState(), START + timedelta(days=21), D("50000"))
    assert o.amount_quote == D("40")


def test_cumulative_cap_reduces_then_stops():
    st = DcaState(total_spent=D("90"))
    o = decide(CFG, st, START, D("50000"))
    assert o.amount_quote == D("10")
    assert decide(CFG, DcaState(total_spent=D("98")), START, D("50000")) is None


def test_sentiment_modulation_within_budget():
    cfg = DcaConfig(**{**CFG.__dict__, "modulated": True})
    assert decide(cfg, DcaState(), START, D("50000"), fear_greed=20).amount_quote == D("30")
    assert decide(cfg, DcaState(), START, D("50000"), fear_greed=80).amount_quote == D("10")
    assert decide(cfg, DcaState(), START, D("50000"), fear_greed=50).amount_quote == D("20")
    capped = decide(cfg, DcaState(), START, D("50000"), fear_greed=20, budget_available=D("25"))
    assert capped.amount_quote == D("25")


def test_spec_example_average_price_below_mean_price():
    st, qty, spent = DcaState(), D("0"), D("0")
    for k, price in enumerate([D("60000"), D("50000"), D("40000"), D("50000")]):
        o = decide(CFG, st, START + timedelta(days=7 * k), price)
        st = record(st, o)
        qty += o.quantity
        spent += o.amount_quote
    assert spent == D("80") and round(spent / qty) == 48980
