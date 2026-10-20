from decimal import Decimal as D

import pytest

from orchestre.exchange.base import OrderRequest
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


@pytest.fixture
def ex():
    return FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("1000")})


def buy(qty, cid="c1"):
    return OrderRequest("BTCUSDT", Side.BUY, D(qty), None, cid)


def test_buy_fills_and_updates_balances(ex):
    r = ex.place_order(buy("0.001"))
    assert r.status == "FILLED" and r.fee_quote == D("0.06")
    b = ex.get_balances()
    assert b["BTC"] == D("0.001") and b["USDT"] == D("1000") - D("60") - D("0.06")


def test_idempotent_by_client_order_id(ex):
    ex.place_order(buy("0.001"))
    ex.place_order(buy("0.001"))
    assert ex.get_balances()["BTC"] == D("0.001")


def test_rejects_insufficient_funds(ex):
    assert ex.place_order(buy("1")).status == "REJECTED"
    assert ex.get_balances() == {"USDT": D("1000")}


def test_rejects_filter_violation(ex):
    assert ex.place_order(buy("0.000011")).status == "REJECTED"


def test_sell_requires_holdings(ex):
    r = ex.place_order(OrderRequest("BTCUSDT", Side.SELL, D("0.001"), None, "s1"))
    assert r.status == "REJECTED"


def test_market_orders_do_not_need_a_tick_aligned_reference_price(ex):
    ex.set_price("BTCUSDT", D("59876.123456789"))
    assert ex.place_order(buy("0.001", "m1")).status == "FILLED"
