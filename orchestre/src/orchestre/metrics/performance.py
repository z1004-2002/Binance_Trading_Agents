"""Indicateurs de performance (§17, EX-MET-01) à partir d'une série de valeurs quotidiennes. Marché ouvert 7 j/7."""

from __future__ import annotations

import math
import statistics
from typing import Sequence

PERIODS = 365


def _need(equity: Sequence[float], n: int) -> None:
    if len(equity) < n:
        raise ValueError(f"au moins {n} valeurs requises")


def total_return(equity: Sequence[float]) -> float:
    _need(equity, 2)
    return equity[-1] / equity[0] - 1


def annualized_return(equity: Sequence[float], days: int) -> float:
    _need(equity, 2)
    return (equity[-1] / equity[0]) ** (PERIODS / days) - 1


def daily_returns(equity: Sequence[float]) -> list[float]:
    _need(equity, 2)
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]


def max_drawdown(equity: Sequence[float]) -> float:
    _need(equity, 2)
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak)
    return worst


def sharpe(equity: Sequence[float], rf_annual: float = 0.0) -> float:
    """Moyenne des rendements en excès / écart-type, annualisé [23]. 0 si la série est constante."""
    _need(equity, 3)
    excess = [r - rf_annual / PERIODS for r in daily_returns(equity)]
    spread = statistics.stdev(excess)
    return 0.0 if spread == 0 else statistics.mean(excess) / spread * math.sqrt(PERIODS)


def sortino(equity: Sequence[float], target_annual: float = 0.0) -> float:
    """Même numérateur ÷ écart aux rendements sous la cible [24]. Infini s'il n'y a aucune perte et un gain."""
    _need(equity, 3)
    target = target_annual / PERIODS
    excess = [r - target for r in daily_returns(equity)]
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in excess) / len(excess))
    mean = statistics.mean(excess)
    if downside == 0:
        return math.inf if mean > 0 else 0.0
    return mean / downside * math.sqrt(PERIODS)


def calmar(equity: Sequence[float], days: int) -> float:
    drawdown = max_drawdown(equity)
    annual = annualized_return(equity, days)
    if drawdown == 0:
        return math.inf if annual > 0 else 0.0
    return annual / drawdown
