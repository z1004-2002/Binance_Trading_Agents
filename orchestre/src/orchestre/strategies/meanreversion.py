"""S-5 Retour à la moyenne (bougies de 4 h), acheteur ou neutre (§11.6) [36].

Entrée : clôture sous la bande de Bollinger basse (20 ; 2 écarts-types) ET RSI(14) < 30.
Sortie : retour à la moyenne 20 périodes, ou RSI > 50, ou sortie forcée après `max_bars` bougies.
Stop : 1,5 x ATR(14) sous la clôture d'entrée (EX-SEC-14). Actif en régime RANGE seulement : la gouvernance
par régime (multiplicateur 0 hors RANGE, §12.1) est appliquée par l'allocateur, pas par ce module.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.analysis.indicators import atr, bollinger, rsi
from orchestre.backtest.engine import Target
from orchestre.exchange.base import Candle


class MeanReversion:
    name = "S-5 retour moyenne"
    params_tried = 1

    def __init__(
        self,
        bb_len: int = 20,
        bb_k: float = 2.0,
        rsi_len: int = 14,
        rsi_entry: float = 30.0,
        rsi_exit: float = 50.0,
        atr_len: int = 14,
        atr_mult: float = 1.5,
        max_bars: int = 30,
    ) -> None:
        self._bb_len, self._bb_k, self._rsi_len = bb_len, bb_k, rsi_len
        self._rsi_entry, self._rsi_exit = rsi_entry, rsi_exit
        self._atr_len, self._atr_mult, self._max_bars = atr_len, atr_mult, max_bars
        self._in_trade = False
        self._bars = 0
        # Le RSI et l'ATR de Wilder oublient leur passé de façon exponentielle : 10 périodes suffisent (écart < 0,01)
        self._window = max(bb_len, 10 * rsi_len, 10 * atr_len) + 1

    def target(self, history: list[Candle], position: Decimal) -> Target:
        if len(history) < max(self._bb_len, self._rsi_len + 1, self._atr_len + 1) + 1:
            return Target(None)
        tail = history[-self._window :]
        closes = [float(c.close) for c in tail]
        highs, lows = [float(c.high) for c in tail], [float(c.low) for c in tail]
        lower, mid, _ = bollinger(closes, self._bb_len, self._bb_k)
        strength = rsi(closes, self._rsi_len)

        if position == 0 and self._in_trade:  # stoppé
            self._in_trade, self._bars = False, 0
        if self._in_trade:
            self._bars += 1
            if closes[-1] >= mid or strength > self._rsi_exit or self._bars >= self._max_bars:
                self._in_trade, self._bars = False, 0
                return Target(Decimal("0"))
            return Target(None)  # on garde la position et son stop
        if closes[-1] < lower and strength < self._rsi_entry:
            stop = closes[-1] - self._atr_mult * atr(highs, lows, closes, self._atr_len)
            self._in_trade, self._bars = True, 0
            return Target(Decimal("1"), Decimal(str(stop)) if stop > 0 else None)
        return Target(Decimal("0")) if position > 0 else Target(None)

    def state(self) -> dict:
        return {"in_trade": self._in_trade, "bars": self._bars}

    def load_state(self, state: dict) -> None:
        self._in_trade, self._bars = state["in_trade"], state["bars"]
