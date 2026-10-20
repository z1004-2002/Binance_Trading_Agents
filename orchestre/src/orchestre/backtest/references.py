"""Références de comparaison B-1 à B-4 (§16.3). Mêmes règles d'exécution que n'importe quel stratège."""

from __future__ import annotations

from decimal import Decimal

from orchestre.backtest.engine import Target
from orchestre.exchange.base import Candle


class BuyAndHold:
    """B-1 : achat unique au début."""

    name, params_tried = "B-1 détention", 1

    def __init__(self) -> None:
        self._done = False

    def target(self, history: list[Candle], position: Decimal) -> Target:
        if self._done:
            return Target(None)
        self._done = True
        return Target(Decimal("1"))


class WeeklyDca:
    """B-2 : le capital initial est investi par tranches égales, une par semaine, sur `weeks` semaines."""

    name, params_tried = "B-2 DCA hebdomadaire", 1

    def __init__(self, weeks: int = 52) -> None:
        self._weeks, self._start, self._k = weeks, None, 0

    def target(self, history: list[Candle], position: Decimal) -> Target:
        now = history[-1].open_time
        if self._start is None:
            self._start = now
        due = (now - self._start).days // 7 + 1
        if due > self._k and self._k < self._weeks:
            self._k = min(due, self._weeks)
            return Target(Decimal(self._k) / Decimal(self._weeks))
        return Target(None)


class RebalancedMix:
    """B-3 : poids constant (50 % actif / 50 % devise de cotation), rééquilibré chaque mois."""

    name, params_tried = "B-3 50/50 mensuel", 1

    def __init__(self, weight: Decimal = Decimal("0.5")) -> None:
        self._weight, self._month = weight, None

    def target(self, history: list[Candle], position: Decimal) -> Target:
        key = (history[-1].open_time.year, history[-1].open_time.month)
        if key == self._month:
            return Target(None)
        self._month = key
        return Target(self._weight)


class CashOnly:
    """B-4 : ne rien faire, tout en devise de cotation."""

    name, params_tried = "B-4 cash", 1

    def target(self, history: list[Candle], position: Decimal) -> Target:
        return Target(Decimal("0"))
