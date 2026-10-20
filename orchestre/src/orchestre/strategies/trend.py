"""S-3 Suivi de tendance (momentum temporel), acheteur ou neutre (§11.4) [28].

Règle v1 (bougies journalières) : exposition = facteur de volatilité si le rendement sur 90 jours est positif
ET si le prix est au-dessus de sa moyenne 100 jours ; sinon 0. Stop suiveur à 3 x ATR(14) sous le plus haut
depuis l'entrée (EX-SEC-14). Après un stop, pas de ré-entrée tant que le signal n'est pas retombé (anti-whipsaw).
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.analysis.indicators import atr, ewma_vol, sma
from orchestre.analysis.volatility import vol_target_factor
from orchestre.backtest.engine import Target
from orchestre.exchange.base import Candle


class TrendFollowing:
    name = "S-3 tendance"
    params_tried = 1

    def __init__(
        self,
        lookback: int = 90,
        sma_len: int = 100,
        atr_len: int = 14,
        atr_mult: float = 3.0,
        vol_target: float = 0.40,
        use_vol_target: bool = True,
    ) -> None:
        self._lookback, self._sma_len, self._atr_len = lookback, sma_len, atr_len
        self._atr_mult, self._vol_target, self._use_vol = atr_mult, vol_target, use_vol_target
        self._in_trade = False
        self._blocked = False
        self._highest: float | None = None

    def target(self, history: list[Candle], position: Decimal) -> Target:
        if len(history) < max(self._lookback + 1, self._sma_len, self._atr_len + 1, 31):
            return Target(None)
        closes = [float(c.close) for c in history]
        highs, lows = [float(c.high) for c in history], [float(c.low) for c in history]
        signal = closes[-1] / closes[-1 - self._lookback] > 1 and closes[-1] > sma(closes, self._sma_len)

        if position == 0 and self._in_trade:  # stoppé : on attend que le signal retombe
            self._in_trade, self._blocked, self._highest = False, True, None
        if self._blocked and not signal:
            self._blocked = False

        if not signal or self._blocked:
            self._in_trade, self._highest = False, None
            return Target(Decimal("0"))

        factor = vol_target_factor(self._vol_target, ewma_vol(closes)) if self._use_vol else 1.0
        entering = not self._in_trade and position == 0
        self._highest = highs[-1] if entering or self._highest is None else max(self._highest, highs[-1])
        self._in_trade = True
        stop = self._highest - self._atr_mult * atr(highs, lows, closes, self._atr_len)
        return Target(Decimal(str(round(factor, 4))), Decimal(str(stop)) if stop > 0 else None)

    def state(self) -> dict:
        return {"in_trade": self._in_trade, "blocked": self._blocked, "highest": self._highest}

    def load_state(self, state: dict) -> None:
        self._in_trade, self._blocked, self._highest = state["in_trade"], state["blocked"], state["highest"]
