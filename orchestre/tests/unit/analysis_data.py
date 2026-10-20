"""Séries synthétiques déterministes. Les motifs ont une période qui divise 30 (fenêtre de volatilité)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.exchange.base import Candle

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def make_candles(closes, spread=0.005, start=T0, step=DAY, closed=True):
    candles = []
    for i, c in enumerate(closes):
        t = start + step * i
        high, low = c * (1 + spread), c * (1 - spread)  # bougie de type doji : ouverture = clôture
        candles.append(Candle(t, t + step - timedelta(seconds=1), D(str(c)), D(str(high)), D(str(low)),
                              D(str(c)), D("100"), closed))
    return candles


def _alternating(base, n, up, down):
    out, price = [], base
    for i in range(n):
        price *= up if i % 2 == 0 else down
        out.append(price)
    return out


def uptrend(n=400):
    return make_candles(_alternating(100.0, n, 1.004, 1.002))


def downtrend(n=400):
    return make_candles(_alternating(100.0, n, 0.996, 0.998))


def zigzag_range(n=400):
    return make_candles([100.0 + (3.0 if i % 2 else -3.0) for i in range(n)])


def calm_then_spike(calm=365, wild=35):
    closes = _alternating(100.0, calm, 1.005, 1 / 1.005)
    price = closes[-1]
    for i in range(wild):
        price *= 1.06 if i % 2 == 0 else 1 / 1.06
        closes.append(price)
    return make_candles(closes)
