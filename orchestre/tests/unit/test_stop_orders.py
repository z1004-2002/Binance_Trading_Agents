from dataclasses import replace
from decimal import Decimal as D

import pytest

from orchestre.exchange.base import OrderRequest
from orchestre.exchange.binance import BinanceExchange
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.execution.service import ExecutionService
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import review
from tests.unit.test_binance_adapter import StubClient, order
from tests.unit.test_risk import context, proposal

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


def fake(price="60000", btc="0.01"):
    return FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D(price)}, {"USDT": D("1000"), "BTC": D(btc)})


def stop(qty="0.01", stop_price="57000.00", limit="56800.00", side=Side.SELL, cid="S1"):
    return OrderRequest("BTCUSDT", side, D(qty), D(limit), cid, D(stop_price))


# --- exchange factice ---

def test_stop_sell_rests_and_locks_the_base_asset():
    ex = fake()
    r = ex.place_order(stop())
    assert r.status == "NEW" and ex.get_balances()["BTC"] == D("0")
    assert [o.client_order_id for o in ex.get_open_orders("BTCUSDT")] == ["S1"]


def test_stop_that_would_trigger_immediately_or_a_buy_stop_is_rejected():
    ex = fake()
    assert ex.place_order(stop(stop_price="61000.00", limit="60900.00")).status == "REJECTED"
    assert ex.place_order(stop(side=Side.BUY, cid="S2")).status == "REJECTED"


def test_stop_triggered_with_the_market_above_the_limit_fills_at_the_market():
    ex = fake()
    ex.place_order(stop())
    fills = ex.set_price("BTCUSDT", D("56900"))
    assert fills[0].status == "FILLED" and fills[0].avg_price == D("56900")
    assert ex.get_balances()["USDT"] > D("1000") + D("560")


def test_gap_below_the_limit_leaves_a_resting_limit_that_fills_when_price_recovers():
    ex = fake()
    ex.place_order(stop())
    assert ex.set_price("BTCUSDT", D("50000")) == []           # déclenché mais non exécuté : trou sous la limite
    assert ex.get_order("BTCUSDT", "S1").status == "NEW"
    fills = ex.set_price("BTCUSDT", D("56900"))
    assert fills[0].status == "FILLED" and fills[0].avg_price == D("56800.00")


def test_cancelling_a_stop_unlocks_the_base_asset():
    ex = fake()
    ex.place_order(stop())
    assert ex.cancel_order("BTCUSDT", "S1").status == "CANCELED"
    assert ex.get_balances()["BTC"] == D("0.01")


# --- adaptateur Binance ---

def test_binance_stop_limit_params():
    stub = StubClient()
    BinanceExchange(stub).place_order(stop())
    _, symbol, type_, side, amount, price, params = stub.calls[-1]
    assert (type_, side, price) == ("limit", "sell", 56800.0)
    assert params["stopLossPrice"] == 57000.0 and "postOnly" not in params
    assert params["newClientOrderId"] == "S1"


# --- risque et exécuteur ---

def stop_proposal(**kw):
    base = dict(side=Side.SELL, quantity=D("0.01"), limit_price=D("56800"), stop_price=D("57000"),
                kind="stop", reduces_risk=True)
    base.update(kw)
    return proposal(**base)


def test_stop_orders_are_exempt_from_the_price_deviation_rule_but_not_from_the_kill_switch():
    far = stop_proposal(limit_price=D("48000"), stop_price=D("48500"))   # très loin du prix : un stop est fait pour ça
    assert review(far, context()).approved
    assert review(far, context(kill_switch=True)).rule_id == "R-01"


def make():
    ex = fake()
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D("200"))
    return ex, ledger, ExecutionService(ex, ledger, dry_run=False)


def test_executor_places_a_protective_stop_and_tracks_it_without_touching_the_budget():
    ex, ledger, svc = make()
    r = svc.execute(review(stop_proposal(), context()).order, "S1")
    assert r.status == "OPEN" and ledger.account("dca").committed == D("0")
    assert svc.open_order_ids("BTCUSDT") == ["S1"]


def test_sync_can_be_scoped_to_one_agent():
    ex, ledger, svc = make()
    ledger.allocate("other", D("100"))
    svc.execute(review(stop_proposal(), context()).order, "S1")
    ex.set_price("BTCUSDT", D("56900"))
    assert svc.sync("BTCUSDT", agent_id="other") == []              # l'ordre n'est pas le sien : rien n'est consommé
    assert [r.status for r in svc.sync("BTCUSDT", agent_id="dca")] == ["FILLED"]


def test_cancel_all_keeps_protective_stops_by_default_and_can_cancel_everything():
    ex, ledger, svc = make()
    svc.execute(review(stop_proposal(), context()).order, "S1")
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), D("59000.00"), "L1"))   # ordre limite inconnu
    svc.cancel_all_open("BTCUSDT")
    assert [o.client_order_id for o in ex.get_open_orders("BTCUSDT")] == ["S1"]
    svc.cancel_all_open("BTCUSDT", keep_protective=False)
    assert ex.get_open_orders("BTCUSDT") == []


def test_cancel_all_can_be_scoped_to_one_agent():
    ex, ledger, svc = make()
    ledger.allocate("grid", D("100"))
    d1 = review(proposal(side=Side.BUY, limit_price=D("59500"), agent_id="dca"), context()).order
    d2 = review(proposal(side=Side.BUY, limit_price=D("59400"), agent_id="grid"), context()).order
    svc.execute(d1, "A1")
    svc.execute(d2, "B1")
    svc.cancel_all_open("BTCUSDT", agent_id="grid")
    assert svc.open_order_ids("BTCUSDT") == ["A1"]


def test_protective_flag_survives_a_restart():
    ex, ledger, svc = make()
    svc.execute(review(stop_proposal(), context()).order, "S1")
    rows = svc.export_tracked()
    assert rows[0]["protective"] is True
    svc2 = ExecutionService(ex, ledger, dry_run=False)
    svc2.import_tracked(rows)
    svc2.cancel_all_open("BTCUSDT")
    assert svc2.open_order_ids("BTCUSDT") == ["S1"]
