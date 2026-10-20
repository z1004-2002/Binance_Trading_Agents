"""VolatilityAgent (cœur) : ATR, volatilité réalisée, prévision et ciblage de volatilité (EX-AN-10, EX-AN-11) [30]."""

from __future__ import annotations

from dataclasses import dataclass

from orchestre.analysis.indicators import atr, ewma_vol, realized_vol
from orchestre.exchange.base import Candle


@dataclass(frozen=True)
class VolatilityReading:
    atr: float
    realized: float  # annualisée
    forecast: float  # annualisée, moyenne exponentielle des carrés des rendements


def compute_volatility(candles: list[Candle], atr_period: int = 14, window: int = 30) -> VolatilityReading | None:
    if len(candles) < max(atr_period, window) + 1:
        return None
    closes = [float(c.close) for c in candles]
    highs, lows = [float(c.high) for c in candles], [float(c.low) for c in candles]
    return VolatilityReading(atr(highs, lows, closes, atr_period), realized_vol(closes, window), ewma_vol(closes))


def vol_target_factor(target: float, forecast: float, max_leverage: float = 1.0) -> float:
    """Facteur = volatilité cible / prévue, borné à [0 ; max_leverage] (1 = pas de levier)."""
    if forecast <= 0:
        return max_leverage
    return max(0.0, min(target / forecast, max_leverage))
