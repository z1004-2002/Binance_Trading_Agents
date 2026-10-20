"""Validation glissante (*walk-forward*, EX-BT-05) : choisir les paramètres sur une période, juger sur la suivante.

Les paramètres d'une fenêtre ne sont choisis QUE sur sa période d'entraînement ; la période de test n'est jamais
vue pendant le choix. Le nombre total de combinaisons essayées est journalisé : plus on en essaie, plus un bon
résultat peut n'être dû qu'au hasard [25].
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from orchestre.backtest.engine import BacktestConfig, BacktestError, BacktestResult, ExposureStrategy, run_backtest
from orchestre.backtest.references import BuyAndHold
from orchestre.exchange.base import Candle
from orchestre.metrics.performance import sortino, total_return

Window = tuple[int, int, int, int]  # (début entraînement, fin entraînement, début test, fin test), fin exclue


def make_windows(n: int, warmup: int, train: int, test: int) -> list[Window]:
    windows: list[Window] = []
    start = warmup
    while start + train + test <= n:
        windows.append((start, start + train, start + train, start + train + test))
        start += test
    if not windows:
        raise BacktestError(f"historique insuffisant ({n} bougies) pour {warmup} + {train} + {test} jours")
    return windows


def default_objective(result: BacktestResult) -> float:
    return min(sortino(result.equity), 1e6)


@dataclass(frozen=True)
class WindowResult:
    params: dict
    train_score: float
    test_return: float
    benchmark_return: float


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    oos: BacktestResult
    benchmark: BacktestResult
    params_tried: int


def _stitch(
    name: str, results: list[BacktestResult], initial: float, params_tried: int, periods: int = 365
) -> BacktestResult:
    equity, capital = [initial], initial
    for r in results:
        base = r.equity[0]
        for value in r.equity[1:]:
            equity.append(capital * value / base)
        capital = equity[-1]
    return BacktestResult(
        name, [], equity, [t for r in results for t in r.trades],
        sum((r.fees_paid for r in results), Decimal("0")), sum((r.traded_value for r in results), Decimal("0")),
        params_tried, periods_per_year=periods,
    )


def walk_forward(
    candles: list[Candle],
    factory: Callable[[dict], ExposureStrategy],
    grid: list[dict],
    cfg: BacktestConfig,
    train_days: int = 365,
    test_days: int = 90,
    warmup: int = 120,
    objective: Callable[[BacktestResult], float] = default_objective,
    runner: Callable = run_backtest,
    benchmark_factory: Callable[[], ExposureStrategy] = BuyAndHold,
) -> WalkForwardResult:
    if not grid:
        raise BacktestError("grille de paramètres vide")
    windows = make_windows(len(candles), warmup, train_days, test_days)
    chosen: list[WindowResult] = []
    tests: list[BacktestResult] = []
    benches: list[BacktestResult] = []
    name = factory(grid[0]).name

    for ts, te, vs, ve in windows:
        best_params, best_score = grid[0], float("-inf")
        for params in grid:
            score = objective(runner(candles[ts - warmup : te], factory(params), cfg, warmup))
            if score > best_score:
                best_params, best_score = params, score
        # le test démarre une bougie plus tôt : sa 1re clôture est celle qui précède la fenêtre (chaînage exact)
        test = runner(candles[vs - 1 - warmup : ve], factory(best_params), cfg, warmup)
        bench = runner(candles[vs - 1 - warmup : ve], benchmark_factory(), cfg, warmup)
        tests.append(test)
        benches.append(bench)
        chosen.append(WindowResult(best_params, best_score, total_return(test.equity), total_return(bench.equity)))

    tried = len(grid) * len(windows)
    initial = float(cfg.initial_cash)
    return WalkForwardResult(
        chosen,
        _stitch(f"{name} (hors échantillon)", tests, initial, tried, cfg.periods_per_year),
        _stitch("B-1 détention (mêmes fenêtres)", benches, initial, 1, cfg.periods_per_year),
        tried,
    )
