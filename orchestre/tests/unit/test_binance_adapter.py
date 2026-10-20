from decimal import Decimal as D

import ccxt
import pytest

from orchestre.config import ConfigError, Env
from orchestre.exchange.base import OrderRequest
from orchestre.exchange.binance import BinanceExchange, create_client
from orchestre.exchange.credentials import load_credentials
from orchestre.exchange.filters import Side
from orchestre.exchange.key_restrictions import check_key_restrictions

MARKET = {
    "info": {
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": "0.00001000"},
            {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
        ]
    }
}


def order(status="FILLED", side="BUY", executed="0.001", cum="60.0", orig="0.001",
          price="0", cid="c1", fills=None, extra=None):
    info = {
        "status": status, "side": side, "executedQty": executed, "cummulativeQuoteQty": cum,
        "origQty": orig, "price": price, "clientOrderId": cid,
    }
    if fills is not None:
        info["fills"] = fills
    info.update(extra or {})
    return {"info": info}


class StubClient:
    def __init__(self):
        self.calls = []
        self.next_order = order()
        self.open_orders = []
        self.raise_on_fetch = None
        self.cancel_error = None

    def load_markets(self):
        self.calls.append(("load_markets",))

    def market(self, symbol):
        return MARKET

    def fetch_ticker(self, symbol):
        return {"last": 60000.1, "info": {"lastPrice": "60000.10000000"}}

    def fetch_balance(self):
        return {"info": {"balances": [
            {"asset": "USDT", "free": "1000.50000000", "locked": "10.0"},
            {"asset": "BTC", "free": "0.00000000", "locked": "0"},
            {"asset": "ETH", "free": "0.12345678", "locked": "0"},
        ]}}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create_order", symbol, type_, side, amount, price, params))
        return self.next_order

    def fetch_order(self, id_, symbol, params=None):
        self.calls.append(("fetch_order", id_, symbol, params))
        if self.raise_on_fetch:
            raise self.raise_on_fetch
        return self.next_order

    def fetch_open_orders(self, symbol):
        return self.open_orders

    def cancel_order(self, id_, symbol, params=None):
        self.calls.append(("cancel_order", id_, symbol, params))
        if self.cancel_error:
            raise self.cancel_error
        return self.next_order


@pytest.fixture
def stub():
    return StubClient()


@pytest.fixture
def ex(stub):
    return BinanceExchange(stub)


def test_filters_parsed_as_exact_decimals(ex):
    f = ex.get_filters("BTCUSDT")
    assert (f.tick_size, f.step_size, f.min_qty, f.max_qty, f.min_notional) == (
        D("0.01"), D("0.00001"), D("0.00001"), D("9000"), D("5")
    )


def test_price_and_free_balances_use_raw_strings(ex):
    assert ex.get_price("BTCUSDT") == D("60000.1")
    assert ex.get_balances() == {"USDT": D("1000.5"), "ETH": D("0.12345678")}


def test_symbol_conversion_to_ccxt_format(ex, stub):
    ex.get_price("BTCUSDT")
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
    assert stub.calls[-1][1] == "BTC/USDT"


def test_market_order_params(ex, stub):
    ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
    _, _, type_, side, amount, price, params = stub.calls[-1]
    assert (type_, side, price) == ("market", "buy", None)
    assert params["newClientOrderId"] == "c1"
    assert params["selfTradePreventionMode"] == "EXPIRE_TAKER"  # EX-EX-04
    assert "postOnly" not in params


def test_limit_order_is_post_only_maker(ex, stub):
    ex.place_order(OrderRequest("BTCUSDT", Side.SELL, D("0.001"), D("61000.00"), "c2"))
    _, _, type_, side, amount, price, params = stub.calls[-1]
    assert (type_, side, price) == ("limit", "sell", 61000.0)
    assert params["postOnly"] is True  # LIMIT_MAKER (EX-ST-04)


def test_status_mapping(ex, stub):
    cases = {"FILLED": "FILLED", "NEW": "NEW", "PARTIALLY_FILLED": "NEW",
             "CANCELED": "CANCELED", "EXPIRED": "CANCELED", "REJECTED": "REJECTED"}
    for binance, ours in cases.items():
        stub.next_order = order(status=binance)
        r = ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1"))
        assert r.status == ours


def test_average_price_from_cumulative_quote(ex, stub):
    stub.next_order = order(executed="0.002", cum="120.2")
    r = ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.002"), None, "c1"))
    assert r.avg_price == D("60100") and r.filled_qty == D("0.002")


def test_open_order_keeps_limit_price_and_original_qty(ex, stub):
    stub.next_order = order(status="NEW", executed="0", cum="0", price="59000.00", orig="0.001")
    r = ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), D("59000"), "c1"))
    assert r.avg_price == D("59000") and r.orig_qty == D("0.001") and r.filled_qty == D("0")


def test_fee_from_fills_in_quote_and_base(ex, stub):
    stub.next_order = order(fills=[{"commission": "0.06", "commissionAsset": "USDT"}])
    assert ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1")).fee_quote == D("0.06")
    stub.next_order = order(fills=[{"commission": "0.000001", "commissionAsset": "BTC"}])
    assert ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1")).fee_quote == D("0.06")


def test_fee_estimated_when_fills_absent(stub):
    ex = BinanceExchange(stub, fee_rate=D("0.001"))
    stub.next_order = order()  # cum = 60.0, pas de fills
    assert ex.place_order(OrderRequest("BTCUSDT", Side.BUY, D("0.001"), None, "c1")).fee_quote == D("0.06")


def test_get_order_by_client_id_and_none_when_unknown(ex, stub):
    assert ex.get_order("BTCUSDT", "c1").client_order_id == "c1"
    assert stub.calls[-1][3] == {"origClientOrderId": "c1"}
    stub.raise_on_fetch = ccxt.OrderNotFound("inconnu")
    assert ex.get_order("BTCUSDT", "zzz") is None


def test_open_orders_listed(ex, stub):
    stub.open_orders = [order(status="NEW", cid="a", price="59000"), order(status="NEW", cid="b", price="58000")]
    assert [o.client_order_id for o in ex.get_open_orders("BTCUSDT")] == ["a", "b"]


def test_cancel_uses_client_id(ex, stub):
    stub.next_order = order(status="CANCELED", cid="cancel-1", extra={"origClientOrderId": "c1"})
    r = ex.cancel_order("BTCUSDT", "c1")
    assert r.status == "CANCELED" and r.client_order_id == "c1"
    assert stub.calls[-1][3] == {"origClientOrderId": "c1"}


def test_cancel_of_already_filled_returns_filled_order(ex, stub):
    stub.cancel_error = ccxt.OrderNotFound("Unknown order")
    stub.next_order = order(status="FILLED")
    assert ex.cancel_order("BTCUSDT", "c1").status == "FILLED"


# --- clés et environnements ---

def test_credentials_use_environment_prefix():
    env = {"BINANCE_TESTNET_API_KEY": "k1", "BINANCE_TESTNET_API_SECRET": "s1"}
    assert load_credentials(Env.TESTNET, env) == ("k1", "s1")


def test_missing_credentials_refused():
    with pytest.raises(ConfigError):
        load_credentials(Env.TESTNET, {"BINANCE_TESTNET_API_KEY": "k1"})


def test_same_key_reused_across_environments_refused():
    env = {
        "BINANCE_TESTNET_API_KEY": "same", "BINANCE_TESTNET_API_SECRET": "s",
        "BINANCE_LIVE_API_KEY": "same", "BINANCE_LIVE_API_SECRET": "s2",
    }
    with pytest.raises(ConfigError, match="mélange"):
        load_credentials(Env.TESTNET, env)


def test_testnet_client_points_to_testnet():
    client = create_client(Env.TESTNET, "k", "s")
    assert "testnet.binance.vision" in str(client.urls["api"])


def test_live_client_needs_explicit_confirmation():
    with pytest.raises(ConfigError):
        create_client(Env.LIVE, "k", "s")
    assert "testnet" not in str(create_client(Env.LIVE, "k", "s", live_confirmed=True).urls["api"])


# --- EX-SEC-05 ---

GOOD = {
    "ipRestrict": True, "enableReading": True, "enableWithdrawals": False,
    "enableInternalTransfer": False, "permitsUniversalTransfer": False,
    "enableMargin": False, "enableFutures": False, "enableSpotAndMarginTrading": True,
}


def test_good_key_has_no_violation():
    assert check_key_restrictions(GOOD) == []


def test_withdrawals_ip_and_transfers_are_blocking():
    bad = {**GOOD, "ipRestrict": False, "enableWithdrawals": True, "permitsUniversalTransfer": True}
    v = check_key_restrictions(bad)
    assert len(v) == 3 and any("restriction IP" in x for x in v)


def test_margin_and_futures_permissions_refused_unless_instrument_enabled():
    bad = {**GOOD, "enableMargin": True, "enableFutures": True}
    assert len(check_key_restrictions(bad)) == 2
    assert check_key_restrictions(bad, enabled_instruments={"margin": True, "futures": True}) == []


def test_missing_trading_permission_is_reported():
    assert check_key_restrictions({**GOOD, "enableSpotAndMarginTrading": False})
