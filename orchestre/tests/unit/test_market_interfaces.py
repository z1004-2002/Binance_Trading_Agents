from datetime import datetime, timezone
from decimal import Decimal as D

from orchestre.exchange.base import Candle, OrderBook
from orchestre.exchange.binance import BinanceExchange
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from tests.unit.test_binance_adapter import StubClient

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candle(i, close="100", closed=True):
    return Candle(T0, T0, D(close), D(close), D(close), D(close), D("1"), closed)


def test_fake_returns_scripted_candles_limited_to_the_tail():
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("100")}, {})
    ex.set_candles("BTCUSDT", "1d", [candle(i, str(100 + i)) for i in range(10)])
    got = ex.get_candles("BTCUSDT", "1d", 3)
    assert [c.close for c in got] == [D("107"), D("108"), D("109")]
    assert ex.get_candles("BTCUSDT", "4h", 3) == []


def test_fake_default_order_book_is_tight_and_deep():
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {})
    book = ex.get_order_book("BTCUSDT", 5)
    assert len(book.bids) == len(book.asks) == 5 and book.bids[0][0] < D("60000") < book.asks[0][0]


def test_fake_order_book_can_be_scripted():
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {})
    book = OrderBook([(D("1"), D("1"))], [(D("2"), D("1"))])
    ex.set_order_book("BTCUSDT", book)
    assert ex.get_order_book("BTCUSDT") is book


def test_binance_klines_are_exact_decimals_with_closed_flag():
    stub = StubClient()
    ex = BinanceExchange(stub, now_ms=lambda: 86_400_000 + 5)  # la 2e bougie est encore en cours
    candles = ex.get_candles("BTCUSDT", "1d", 2)
    assert candles[0].open == D("100.0") and candles[0].volume == D("12.5")
    assert [c.closed for c in candles] == [True, False]
    assert candles[0].open_time == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert stub.calls[-1] == ("klines", {"symbol": "BTCUSDT", "interval": "1d", "limit": 2})


def test_binance_depth_parsed_as_decimals():
    book = BinanceExchange(StubClient()).get_order_book("BTCUSDT", 5)
    assert book.bids[0] == (D("59990.00"), D("1.5")) and book.asks[1] == (D("60020.00"), D("3"))


def test_end_time_pages_backwards_in_fake_and_binance():
    ex = FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("100")}, {})
    candles = [Candle(T0.replace(day=1 + i), T0, D("1"), D("1"), D("1"), D("1"), D("1"), True) for i in range(10)]
    ex.set_candles("BTCUSDT", "1d", candles)
    got = ex.get_candles("BTCUSDT", "1d", 3, end_time=candles[5].open_time)
    assert [c.open_time for c in got] == [c.open_time for c in candles[2:5]]  # fin exclue
    stub = StubClient()
    BinanceExchange(stub).get_candles("BTCUSDT", "1d", 2, end_time=T0)
    assert stub.calls[-1][1]["endTime"] == int(T0.timestamp() * 1000) - 1
