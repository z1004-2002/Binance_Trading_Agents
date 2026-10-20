from decimal import Decimal as D

import pytest

from orchestre.analysis.regimes import Regime
from orchestre.backtest.engine import BacktestError, BacktestResult
from orchestre.backtest.team import combine, regime_series, team_from_results
from orchestre.portfolio.allocator_v2 import AllocatorConfig
from tests.unit.analysis_data import uptrend

FREE = AllocatorConfig(max_weight=1.0, max_step=1.0)
ALL_ONE = lambda k: {"x": 1.0, "y": 1.0}  # noqa: E731


def test_single_strategy_compounds_with_the_reserve_left_in_cash():
    path = combine({"x": [0.01] * 30}, lambda k: {"x": 1.0}, "v2", FREE, reserve=0.2)
    assert path.equity[0] == 1000.0 and len(path.equity) == 31
    assert path.equity[-1] == pytest.approx(1000 * (1 + 0.8 * 0.01) ** 30)


def test_v1_uses_fixed_base_budgets_times_the_regime_multiplier():
    returns = {"x": [0.01] * 14, "y": [0.0] * 14}
    path = combine(returns, lambda k: {"x": 1.0, "y": 0.5}, "v1", FREE, reserve=0.0)
    assert path.weights[0] == {"x": 0.5, "y": 0.25}


def test_a_zero_multiplier_removes_the_strategy_from_the_next_rebalance():
    returns = {"x": [0.0] * 21, "y": [0.01] * 7 + [0.5] * 14}
    mult = lambda k: {"x": 1.0, "y": 1.0 if k < 7 else 0.0}  # noqa: E731
    path = combine(returns, mult, "v1", FREE, reserve=0.0)
    # y est retiré dès le 2e rééquilibrage : ses énormes rendements suivants ne comptent pas
    assert path.equity[-1] < 1000 * 1.2


def test_weights_change_weekly_only():
    returns = {"x": [0.002] * 22, "y": [0.001] * 22}
    path = combine(returns, ALL_ONE, "v2", AllocatorConfig(max_weight=1.0, max_step=0.1), every=7)
    assert path.rebalance_days == [0, 7, 14, 21] and len(path.weights) == 4


def test_the_allocator_never_sees_the_future():
    # y gagne énormément APRÈS le premier rééquilibrage : au jour 0 le choix doit rester neutre
    returns = {"x": [0.001] * 20, "y": [0.0] * 7 + [0.2] * 13}
    path = combine(returns, ALL_ONE, "v2", FREE, reserve=0.0)
    assert path.weights[0] == {"x": pytest.approx(0.5), "y": pytest.approx(0.5)}


def result(name, values, fees="1", trades=3):
    return BacktestResult(name, [], values, [object()] * trades, D(fees), D("0"), 1, D("0"))


def test_team_from_results_builds_both_variants_with_scaled_fees():
    n = 30
    results = {"S-3": result("a", [1000 * 1.01 ** i for i in range(n)]), "S-4": result("b", [1000 * 1.005 ** i for i in range(n)])}
    out = team_from_results(results, [None] * (n - 1), AllocatorConfig(max_weight=1.0, max_step=1.0), reserve=0.2)
    assert set(out) == {"v1", "v2"}
    assert out["v2"].equity[0] == 1000.0 and len(out["v2"].equity) == n
    assert out["v2"].name.startswith("Équipe v2") and out["v1"].name.startswith("Équipe v1")
    assert 0 < float(out["v2"].fees_paid) < 2.0 and len(out["v2"].trades) == 6


def test_team_requires_aligned_series():
    with pytest.raises(BacktestError):
        team_from_results({"S-3": result("a", [1.0, 2.0, 3.0]), "S-4": result("b", [1.0, 2.0])}, [None, None], FREE)


def test_regime_series_is_none_before_enough_history_then_defined():
    candles = uptrend(500)
    series = regime_series(candles)
    assert len(series) == 500 and series[100] is None
    assert series[-1] is Regime.TENDANCE_HAUSSE
    assert all(r is not None for r in series[300:])
