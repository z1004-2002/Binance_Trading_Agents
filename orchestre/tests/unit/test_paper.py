import json
from decimal import Decimal as D

import pytest

from orchestre.exchange.base import OrderBook, OrderRequest
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.exchange.paper import PaperExchange
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))


class Market(FakeExchange):
    """Marché « réel » simulé : toute tentative d'écriture est une erreur."""

    def place_order(self, request):
        raise AssertionError("le mode paper ne doit JAMAIS envoyer d'ordre au marché")

    def cancel_order(self, symbol, client_order_id):
        raise AssertionError("le mode paper ne doit JAMAIS annuler d'ordre sur le marché")


def paper(price="60000", slippage="0"):
    market = Market({"BTCUSDT": F}, {"BTCUSDT": D(price)}, {})
    return market, PaperExchange(market, ["BTCUSDT"], {"USDT": D("1000")}, slippage=D(slippage))


def test_prices_filters_candles_and_book_come_from_the_real_market():
    market, ex = paper()
    market.set_candles("BTCUSDT", "1d", make_candles([100.0] * 5))
    assert ex.get_price("BTCUSDT") == D("60000") and ex.get_filters("BTCUSDT") == F
    assert len(ex.get_candles("BTCUSDT", "1d", 10)) == 5
    book = OrderBook([(D("1"), D("1"))], [(D("2"), D("1"))])
    market.set_order_book("BTCUSDT", book)
    assert ex.get_order_book("BTCUSDT") is book


def test_orders_and_balances_are_simulated_and_never_reach_the_market():
    market, ex = paper()
    r = ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))     # la Market lèverait une erreur
    assert r.status == "FILLED" and ex.get_balances()["BTC"] == D("0.001")


def test_market_orders_pay_the_configured_slippage():
    _, ex = paper(slippage="0.001")
    r = ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
    assert r.avg_price == D("60000") * D("1.001")
    s = ex.place_order(OrderRequest("BTCUSDT", Side.SELL, D("0.001"), None, "c2"))
    assert s.avg_price == D("60000") * D("0.999")


def test_resting_orders_fill_when_the_real_price_crosses_them_at_the_next_poll():
    market, ex = paper()
    ex.get_price("BTCUSDT")
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), D("59000.00"), "L1"))
    market.set_price("BTCUSDT", D("58900"))
    assert ex.get_order("BTCUSDT", "L1").status == "NEW"       # rien ne bouge tant qu'on n'interroge pas le prix
    ex.get_price("BTCUSDT")
    assert ex.get_order("BTCUSDT", "L1").status == "FILLED"


def test_protective_stops_trigger_on_real_prices():
    market, ex = paper()
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.01"), None, "b"))
    ex.place_order(OrderRequest("BTCUSDT", Side.SELL, D("0.01"), D("56800.00"), "s", D("57000.00")))
    market.set_price("BTCUSDT", D("56900"))
    ex.get_price("BTCUSDT")
    assert ex.get_order("BTCUSDT", "s").status == "FILLED"


def test_state_survives_a_json_roundtrip_including_resting_and_stop_orders():
    market, ex = paper()
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.01"), None, "b"))
    ex.place_order(OrderRequest("BTCUSDT", Side.SELL, D("0.01"), D("56800.00"), "s", D("57000.00")))
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), D("59000.00"), "L1"))
    state = json.loads(json.dumps(ex.export_state()))

    _, ex2 = paper()
    ex2.import_state(state)
    assert ex2.get_balances() == ex.get_balances()
    assert sorted(o.client_order_id for o in ex2.get_open_orders("BTCUSDT")) == ["L1", "s"]
    market.set_price("BTCUSDT", D("56900"))
    # le marché de ex2 est un autre objet : on avance son prix
    ex2._market.set_price("BTCUSDT", D("56900"))
    ex2.get_price("BTCUSDT")
    assert ex2.get_order("BTCUSDT", "s").status == "FILLED" and ex2.get_order("BTCUSDT", "L1").status == "FILLED"


def test_idempotence_survives_the_roundtrip():
    _, ex = paper()
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
    _, ex2 = paper()
    ex2.import_state(json.loads(json.dumps(ex.export_state())))
    again = ex2.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
    assert again.status == "FILLED" and ex2.get_balances() == ex.get_balances()          # pas de second achat
