"""S-4 Cassure de canal (Donchian), acheteur ou neutre (§11.5) [37].

Entrée : la clôture dépasse le plus haut des `entry_len` derniers jours (jour courant exclu).
Sortie : la clôture passe sous le plus bas des `exit_len` derniers jours. Stop initial à `atr_mult` x ATR(14)
sous la clôture d'entrée, posé côté plateforme (EX-SEC-14) et conservé tant que la position tient.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.analysis.indicators import atr
from orchestre.backtest.engine import Target
from orchestre.exchange.base import Candle


class DonchianBreakout:
    name = "S-4 cassure"
    params_tried = 1

    def __init__(self, entry_len: int = 20, exit_len: int = 10, atr_len: int = 14, atr_mult: float = 2.0) -> None:
        if exit_len > entry_len:
            raise ValueError("exit_len doit être <= entry_len (canal de sortie plus court)")
        self._entry, self._exit, self._atr_len, self._atr_mult = entry_len, exit_len, atr_len, atr_mult
        self._in_trade = False

    def target(self, history: list[Candle], position: Decimal) -> Target:
        if len(history) < max(self._entry, self._atr_len + 1) + 1:
            return Target(None)
        closes = [float(c.close) for c in history]
        highs, lows = [float(c.high) for c in history], [float(c.low) for c in history]

        if position == 0 and self._in_trade:  # stoppé : il faudra une nouvelle cassure
            self._in_trade = False
        if self._in_trade:
            if closes[-1] < min(lows[-1 - self._exit : -1]):
                self._in_trade = False
                return Target(Decimal("0"))
            return Target(None)  # on garde la position et le stop initial
        if closes[-1] > max(highs[-1 - self._entry : -1]):
            stop = closes[-1] - self._atr_mult * atr(highs, lows, closes, self._atr_len)
            self._in_trade = True
            return Target(Decimal("1"), Decimal(str(stop)) if stop > 0 else None)
        return Target(Decimal("0")) if position > 0 else Target(None)

    def state(self) -> dict:
        return {"in_trade": self._in_trade}

    def load_state(self, state: dict) -> None:
        self._in_trade = state["in_trade"]
