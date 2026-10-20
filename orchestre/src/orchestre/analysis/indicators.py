"""Indicateurs de Wilder et de volatilité (EX-AN-06, EX-AN-10) [35].

Ce sont des mesures statistiques, pas des montants : calcul en flottants (A-5 ne vise que l'argent).
"""

from __future__ import annotations

from math import log, sqrt
from typing import Sequence

PERIODS_PER_YEAR = 365  # marché ouvert 7 j/7


def sma(values: Sequence[float], n: int) -> float:
    if len(values) < n:
        raise ValueError(f"au moins {n} valeurs requises")
    return sum(values[-n:]) / n


def sma_series(values: Sequence[float], n: int) -> list[float]:
    return [sum(values[i - n + 1 : i + 1]) / n for i in range(n - 1, len(values))]


def _true_ranges(highs, lows, closes) -> list[float]:
    return [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> float:
    tr = _true_ranges(highs, lows, closes)
    if len(tr) < n:
        raise ValueError(f"au moins {n + 1} bougies requises")
    value = sum(tr[:n]) / n
    for t in tr[n:]:
        value = (value * (n - 1) + t) / n
    return value


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> float:
    if len(closes) < 2 * n + 1:
        raise ValueError(f"au moins {2 * n + 1} bougies requises")
    tr = _true_ranges(highs, lows, closes)
    plus_dm, minus_dm = [], []
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    s_tr, s_plus, s_minus = sum(tr[:n]), sum(plus_dm[:n]), sum(minus_dm[:n])
    dx_values = [_dx(s_plus, s_minus, s_tr)]
    for i in range(n, len(tr)):
        s_tr += tr[i] - s_tr / n
        s_plus += plus_dm[i] - s_plus / n
        s_minus += minus_dm[i] - s_minus / n
        dx_values.append(_dx(s_plus, s_minus, s_tr))

    value = sum(dx_values[:n]) / n
    for dx in dx_values[n:]:
        value = (value * (n - 1) + dx) / n
    return value


def _dx(s_plus: float, s_minus: float, s_tr: float) -> float:
    if s_tr == 0:
        return 0.0
    plus_di, minus_di = 100 * s_plus / s_tr, 100 * s_minus / s_tr
    total = plus_di + minus_di
    return 0.0 if total == 0 else 100 * abs(plus_di - minus_di) / total


def log_returns(closes: Sequence[float]) -> list[float]:
    return [log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def _stdev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def realized_vol(closes: Sequence[float], n: int = 30) -> float:
    returns = log_returns(closes)
    if len(returns) < n:
        raise ValueError(f"au moins {n + 1} clôtures requises")
    return _stdev(returns[-n:]) * sqrt(PERIODS_PER_YEAR)


def rolling_realized_vol(closes: Sequence[float], n: int = 30) -> list[float]:
    returns = log_returns(closes)
    return [_stdev(returns[i - n : i]) * sqrt(PERIODS_PER_YEAR) for i in range(n, len(returns) + 1)]


def ewma_vol(closes: Sequence[float], lam: float = 0.94) -> float:
    """Prévision de volatilité annualisée : moyenne exponentielle des carrés des rendements (EX-AN-10)."""
    returns = log_returns(closes)
    variance = returns[0] ** 2
    for r in returns[1:]:
        variance = lam * variance + (1 - lam) * r * r
    return sqrt(variance * PERIODS_PER_YEAR)


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
