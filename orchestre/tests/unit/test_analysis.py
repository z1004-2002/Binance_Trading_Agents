from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from orchestre.analysis.liquidity import assess_liquidity
from orchestre.analysis.marketdata import validate_series
from orchestre.analysis.regime import RegimeConfig, RegimeTracker, classify, replay
from orchestre.analysis.regimes import Regime
from orchestre.analysis.sentiment import (
    ATTRIBUTION, SentimentState, fetch_fear_greed, parse_fear_greed, sentiment_multiplier,
)
from orchestre.analysis.volatility import compute_volatility, vol_target_factor
from orchestre.exchange.base import OrderBook
from tests.unit.analysis_data import DAY, T0, calm_then_spike, downtrend, make_candles, uptrend, zigzag_range

# ---------- cohérence des données (EX-AN-02, EX-AN-03) ----------


def clean(n=30):
    return make_candles([100.0 + (i % 3) for i in range(n)])


def test_clean_series_is_healthy_and_fully_accepted():
    r = validate_series(clean(), "1d")
    assert r.healthy and len(r.accepted) == 30 and r.problems == []


def test_open_candle_is_never_broadcast():
    candles = clean()
    candles[-1] = replace(candles[-1], closed=False)
    r = validate_series(candles, "1d")
    assert len(r.accepted) == 29 and r.healthy


def test_zero_price_is_rejected():
    candles = clean()
    candles[10] = replace(candles[10], close=D("0"), low=D("0"))
    r = validate_series(candles, "1d")
    assert not r.healthy and len(r.accepted) == 29 and any("prix" in p for p in r.problems)


def test_incoherent_ohlc_is_rejected():
    candles = clean()
    candles[5] = replace(candles[5], high=D("1"))
    r = validate_series(candles, "1d")
    assert any("OHLC" in p for p in r.problems) and len(r.accepted) == 29


def test_aberrant_jump_is_rejected():
    candles = clean()
    candles[12] = replace(candles[12], close=D("300"), high=D("301"))
    r = validate_series(candles, "1d")
    assert any("saut" in p for p in r.problems)


def test_non_increasing_timestamps_are_rejected():
    candles = clean()
    candles[8] = replace(candles[8], open_time=candles[7].open_time)
    assert any("horodatage" in p for p in validate_series(candles, "1d").problems)


def test_data_gap_makes_the_series_unhealthy():
    candles = clean()
    del candles[15]
    r = validate_series(candles, "1d")
    assert not r.healthy and any("trou" in p for p in r.problems)


# ---------- régime (EX-AN-05 à EX-AN-08) ----------


def test_uptrend_is_classified_as_bull_with_indicators():
    r = classify(uptrend())
    assert r.regime is Regime.TENDANCE_HAUSSE and r.confidence > 0.5
    assert r.indicators["adx"] > 25 and r.indicators["close"] > r.indicators["sma_slow"]


def test_downtrend_is_classified_as_bear():
    assert classify(downtrend()).regime is Regime.TENDANCE_BAISSE


def test_zigzag_is_a_range():
    r = classify(zigzag_range())
    assert r.regime is Regime.RANGE and r.indicators["adx"] < 20


def test_volatility_spike_has_priority_over_trend():
    r = classify(calm_then_spike())
    assert r.regime is Regime.HAUTE_VOLATILITE and 0.5 <= r.confidence <= 1.0
    assert r.indicators["vol"] > r.indicators["vol_p90"]


def test_not_enough_history_gives_no_reading():
    assert classify(uptrend(100)) is None


def test_thresholds_are_configurable():
    strict = RegimeConfig(adx_trend=1000.0)  # seuil volontairement inatteignable
    assert classify(uptrend(), strict).regime is Regime.RANGE


def reading(regime):
    from orchestre.analysis.regime import RegimeReading
    return RegimeReading(regime, 0.8, {})


def test_hysteresis_requires_k_consecutive_readings():
    t = RegimeTracker(k=3)
    assert t.update(reading(Regime.RANGE)).regime is Regime.RANGE  # premier régime : publié tout de suite
    assert t.update(reading(Regime.TENDANCE_HAUSSE)) is None
    assert t.update(reading(Regime.TENDANCE_HAUSSE)) is None
    assert t.update(reading(Regime.TENDANCE_HAUSSE)).regime is Regime.TENDANCE_HAUSSE
    assert t.published is Regime.TENDANCE_HAUSSE


def test_hysteresis_streak_resets_on_interruption():
    t = RegimeTracker(k=3)
    t.update(reading(Regime.RANGE))
    for r in (Regime.TENDANCE_BAISSE, Regime.TENDANCE_BAISSE, Regime.RANGE, Regime.TENDANCE_BAISSE, Regime.TENDANCE_BAISSE):
        assert t.update(reading(r)) is None
    assert t.published is Regime.RANGE


def test_replay_warms_up_the_tracker_on_history():
    t = RegimeTracker(k=3)
    last = replay(uptrend(), RegimeConfig(), t, steps=10)
    assert t.published is Regime.TENDANCE_HAUSSE and last.regime is Regime.TENDANCE_HAUSSE


# ---------- volatilité (EX-AN-10, EX-AN-11) ----------


def test_volatility_reading_has_atr_realized_and_forecast():
    v = compute_volatility(calm_then_spike())
    assert v.atr > 0 and v.realized > v.forecast * 0.1 and v.forecast > 0


def test_volatility_needs_enough_candles():
    assert compute_volatility(make_candles([100.0] * 10)) is None


def test_vol_target_factor_spec_example():
    assert vol_target_factor(0.40, 0.80) == pytest.approx(0.5)


def test_vol_target_factor_is_capped_without_leverage_and_never_negative():
    assert vol_target_factor(0.40, 0.10) == 1.0
    assert vol_target_factor(0.40, 0.10, max_leverage=2.0) == 2.0
    assert vol_target_factor(0.40, 0.0) == 1.0
    assert vol_target_factor(0.0, 0.8) == 0.0


# ---------- liquidité (EX-AN-12) ----------


def book(bid="59990", ask="60010", qty="10", levels=5, step="10"):
    b = [(D(bid) - D(step) * i, D(qty)) for i in range(levels)]
    a = [(D(ask) + D(step) * i, D(qty)) for i in range(levels)]
    return OrderBook(b, a)


def test_tight_deep_book_is_liquid():
    r = assess_liquidity(book(), D("100"))
    assert r.liquid and r.spread_pct < D("0.001") and r.slippage_pct < D("0.001")


def test_wide_spread_is_illiquid():
    r = assess_liquidity(book(bid="59800", ask="60200"), D("100"))
    assert not r.liquid and "écart" in r.reason


def test_thin_book_creates_slippage_and_is_illiquid():
    r = assess_liquidity(book(qty="0.001", step="600"), D("100"))
    assert not r.liquid


def test_insufficient_depth_is_illiquid():
    r = assess_liquidity(book(qty="0.0001", levels=1), D("100000"))
    assert not r.liquid and "profondeur" in r.reason


def test_empty_book_is_illiquid():
    assert not assess_liquidity(OrderBook([], []), D("100")).liquid


# ---------- sentiment (EX-AN-13 à EX-AN-15) ----------

PAYLOAD = {"name": "Fear and Greed Index", "data": [
    {"value": "25", "value_classification": "Extreme Fear", "timestamp": "1700000000", "time_until_update": "3000"}]}


def test_parse_fear_greed():
    r = parse_fear_greed(PAYLOAD)
    assert r.value == 25 and r.classification == "Extreme Fear" and r.timestamp is not None


def test_parse_rejects_malformed_or_out_of_range_payloads():
    for bad in ({}, {"data": []}, {"data": [{"value": "abc"}]}, {"data": [{"value": "150"}]}):
        with pytest.raises(ValueError):
            parse_fear_greed(bad)


def test_fetch_returns_none_on_failure_instead_of_crashing():
    def boom():
        raise OSError("réseau")

    assert fetch_fear_greed(boom) is None
    assert fetch_fear_greed(lambda: PAYLOAD).value == 25
    assert fetch_fear_greed(lambda: {"data": []}) is None


def test_sentiment_can_only_reduce_risk():
    for value in range(0, 101):
        m = sentiment_multiplier(value)
        assert D("0.5") <= m <= D("1")
    assert sentiment_multiplier(50) == D("1")
    assert sentiment_multiplier(95) == D("0.5") and sentiment_multiplier(5) == D("0.5")


def test_attribution_is_provided_for_display():
    assert "Alternative.me" in ATTRIBUTION


def test_severe_event_pauses_a_pair():
    s = SentimentState()
    assert not s.is_paused("BTCUSDT")
    s.pause_pair("BTCUSDT", "piratage d'une plateforme")
    assert s.is_paused("BTCUSDT") and "piratage" in s.reasons["BTCUSDT"]
