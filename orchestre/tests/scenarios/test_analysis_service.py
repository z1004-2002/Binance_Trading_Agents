from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.analysis.regimes import Regime
from orchestre.analysis.service import AnalysisService
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.base import IpBannedError, OrderBook
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from tests.unit.analysis_data import make_candles, uptrend, zigzag_range

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class Counting(FakeExchange):
    candle_calls = 0
    book_calls = 0

    def get_candles(self, *a, **k):
        self.candle_calls += 1
        return super().get_candles(*a, **k)

    def get_order_book(self, *a, **k):
        self.book_calls += 1
        return super().get_order_book(*a, **k)


def build(candles=None, **kw):
    ex = Counting({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {})
    ex.set_candles("BTCUSDT", "1d", candles if candles is not None else uptrend())
    clock = SimulatedClock(NOW)
    store = Store(Database(":memory:"))
    return ex, clock, store, AnalysisService(ex, clock, store, **kw)


def test_full_snapshot_for_a_healthy_trending_market():
    _, _, _, svc = build()
    s = svc.refresh("BTCUSDT")
    assert s.healthy and s.regime is Regime.TENDANCE_HAUSSE
    assert s.volatility.atr > 0 and s.liquidity.liquid


def test_refreshes_are_throttled_by_the_clock():
    ex, clock, _, svc = build()
    svc.refresh("BTCUSDT")
    clock.advance(timedelta(seconds=30))
    svc.refresh("BTCUSDT")
    assert (ex.candle_calls, ex.book_calls) == (1, 1)
    clock.advance(timedelta(minutes=2))
    svc.refresh("BTCUSDT")
    assert (ex.candle_calls, ex.book_calls) == (1, 2)  # carnet : chaque minute ; bougies : chaque heure
    clock.advance(timedelta(hours=1))
    svc.refresh("BTCUSDT")
    assert ex.candle_calls == 2


def test_same_closed_candle_is_never_counted_twice():
    ex, clock, store, svc = build()
    for _ in range(3):
        svc.refresh("BTCUSDT")
        clock.advance(timedelta(hours=2))
    assert store.count("regimes") == 1 and store.count("volatility") == 1


def test_new_closed_candle_updates_volatility_log():
    ex, clock, store, svc = build()
    svc.refresh("BTCUSDT")
    longer = uptrend(401)
    ex.set_candles("BTCUSDT", "1d", longer)
    clock.advance(timedelta(hours=2))
    svc.refresh("BTCUSDT")
    assert store.count("volatility") == 2


def test_data_gap_degrades_the_snapshot_and_skips_regime_update():
    candles = uptrend()
    del candles[300]
    _, _, _, svc = build(candles)
    s = svc.refresh("BTCUSDT")
    assert not s.healthy and any("trou" in p for p in s.problems) and s.regime is None


def test_candle_fetch_failure_keeps_previous_values_and_flags_unhealthy():
    ex, clock, _, svc = build()
    first = svc.refresh("BTCUSDT")
    ex.get_candles = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("hors ligne"))
    clock.advance(timedelta(hours=2))
    s = svc.refresh("BTCUSDT")
    assert not s.healthy and s.regime is first.regime and "indisponibles" in s.problems[0]


def test_ip_ban_propagates():
    ex, _, _, svc = build()
    ex.get_candles = lambda *a, **k: (_ for _ in ()).throw(IpBannedError("418"))
    with pytest.raises(IpBannedError):
        svc.refresh("BTCUSDT")


def test_illiquid_book_is_reported():
    ex, _, _, svc = build()
    ex.set_order_book("BTCUSDT", OrderBook([(D("59000"), D("1"))], [(D("61000"), D("1"))]))
    assert not svc.refresh("BTCUSDT").liquidity.liquid


def test_sentiment_is_fetched_throttled_and_logged():
    calls = []

    def fetch():
        calls.append(1)
        return {"data": [{"value": "22", "value_classification": "Extreme Fear", "timestamp": "1700000000"}]}

    _, clock, store, svc = build(fetch_fng=fetch)
    assert svc.refresh_sentiment().value == 22
    svc.refresh_sentiment()
    assert len(calls) == 1 and store.count("sentiment") == 1
    clock.advance(timedelta(hours=7))
    svc.refresh_sentiment()
    assert len(calls) == 2


def test_sentiment_disabled_without_fetcher():
    _, _, _, svc = build()
    assert svc.refresh_sentiment() is None
