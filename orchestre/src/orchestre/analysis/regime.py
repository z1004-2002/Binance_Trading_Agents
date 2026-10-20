"""RegimeAgent (cœur) : classification du régime de marché sur bougies journalières (EX-AN-05 à EX-AN-08).

Règles v1 (seuils configurables, valeurs de départ à valider en backtest) :
- HAUTE_VOLATILITE (prioritaire) : volatilité réalisée 30 j > 90e centile de sa propre histoire sur 1 an ;
- TENDANCE_HAUSSE : ADX > 25, moyenne 50 j croissante, prix au-dessus de la moyenne 200 j ;
- TENDANCE_BAISSE : ADX > 25, moyenne 50 j décroissante, prix sous la moyenne 200 j ;
- RANGE : sinon.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestre.analysis.indicators import adx, percentile, rolling_realized_vol, sma, sma_series
from orchestre.analysis.regimes import Regime
from orchestre.exchange.base import Candle


@dataclass(frozen=True)
class RegimeConfig:
    adx_period: int = 14
    adx_trend: float = 25.0
    sma_fast: int = 50
    sma_slow: int = 200
    slope_days: int = 10
    vol_window: int = 30
    vol_percentile: float = 0.90
    vol_lookback: int = 365
    vol_tolerance: float = 1.001
    min_vol_samples: int = 60
    hysteresis_k: int = 3
    min_history: int = 230


@dataclass(frozen=True)
class RegimeReading:
    regime: Regime
    confidence: float  # [0 ; 1], proposition de l'auteur : voir classify
    indicators: dict[str, float]


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def classify(candles: list[Candle], cfg: RegimeConfig = RegimeConfig()) -> RegimeReading | None:
    if len(candles) < cfg.min_history:
        return None
    closes = [float(c.close) for c in candles]
    highs, lows = [float(c.high) for c in candles], [float(c.low) for c in candles]

    adx_value = adx(highs, lows, closes, cfg.adx_period)
    fast = sma_series(closes, cfg.sma_fast)
    fast_now, fast_before = fast[-1], fast[-1 - cfg.slope_days]
    slow, price = sma(closes, cfg.sma_slow), closes[-1]

    vols = rolling_realized_vol(closes, cfg.vol_window)[-(cfg.vol_lookback + 1) :]
    vol_now, history = vols[-1], vols[:-1]
    p_high = percentile(history, cfg.vol_percentile) if len(history) >= cfg.min_vol_samples else None

    indicators = {
        "adx": adx_value, "sma_fast": fast_now, "sma_fast_before": fast_before,
        "sma_slow": slow, "close": price, "vol": vol_now, "vol_p90": p_high if p_high is not None else float("nan"),
    }
    if p_high is not None and vol_now > p_high * cfg.vol_tolerance:
        return RegimeReading(Regime.HAUTE_VOLATILITE, _clip(0.5 + 0.5 * (vol_now / p_high - 1), 0.5, 1.0), indicators)
    if adx_value > cfg.adx_trend and fast_now > fast_before and price > slow:
        return RegimeReading(Regime.TENDANCE_HAUSSE, _clip(adx_value / 50), indicators)
    if adx_value > cfg.adx_trend and fast_now < fast_before and price < slow:
        return RegimeReading(Regime.TENDANCE_BAISSE, _clip(adx_value / 50), indicators)
    return RegimeReading(Regime.RANGE, _clip((cfg.adx_trend - adx_value) / cfg.adx_trend + 0.25), indicators)


class RegimeTracker:
    """Hystérésis (EX-AN-07) : un changement n'est publié que s'il persiste sur K lectures consécutives.

    `update` doit être appelé une fois par bougie clôturée, jamais à chaque tick.
    """

    def __init__(self, k: int = 3) -> None:
        self._k = k
        self.published: Regime | None = None
        self._candidate: Regime | None = None
        self._streak = 0

    def update(self, reading: RegimeReading) -> RegimeReading | None:
        regime = reading.regime
        if self.published is None:
            self.published = regime
            return reading
        if regime is self.published:
            self._candidate, self._streak = None, 0
            return None
        self._streak = self._streak + 1 if regime is self._candidate else 1
        self._candidate = regime
        if self._streak >= self._k:
            self.published, self._candidate, self._streak = regime, None, 0
            return reading
        return None


def replay(candles: list[Candle], cfg: RegimeConfig, tracker: RegimeTracker, steps: int = 10) -> RegimeReading | None:
    """Rejoue les `steps` dernières bougies pour initialiser le suivi avec la continuité de l'historique."""
    last = None
    for end in range(max(len(candles) - steps + 1, 1), len(candles) + 1):
        reading = classify(candles[:end], cfg)
        if reading is not None:
            tracker.update(reading)
            last = reading
    return last
