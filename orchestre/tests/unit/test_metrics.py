import math
import statistics

import pytest

from orchestre.metrics.performance import (
    annualized_return, calmar, daily_returns, max_drawdown, sharpe, sortino, total_return,
)


def test_total_and_annualized_return():
    assert total_return([100, 110, 121]) == pytest.approx(0.21)
    assert annualized_return([100, 121], days=365) == pytest.approx(0.21)
    assert annualized_return([100, 110], days=730) == pytest.approx(math.sqrt(1.10) - 1)


def test_max_drawdown_peak_to_trough():
    assert max_drawdown([100, 110, 99, 120, 108]) == pytest.approx((110 - 99) / 110)
    assert max_drawdown([100, 101, 102]) == 0.0


def test_max_drawdown_uses_the_running_peak():
    assert max_drawdown([100, 200, 150, 100]) == pytest.approx(0.5)


EQUITY = [100.0, 102.0, 100.98, 104.0094]  # rendements +2 %, -1 %, +3 %


def test_daily_returns():
    assert daily_returns(EQUITY) == pytest.approx([0.02, -0.01, 0.03])


def test_sharpe_matches_definition():
    r = daily_returns(EQUITY)
    expected = statistics.mean(r) / statistics.stdev(r) * math.sqrt(365)
    assert sharpe(EQUITY) == pytest.approx(expected, rel=1e-6)


def test_sharpe_subtracts_the_risk_free_rate():
    assert sharpe(EQUITY, rf_annual=0.5) < sharpe(EQUITY)


def test_sortino_only_penalises_downside():
    r = daily_returns(EQUITY)
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in r) / len(r))
    expected = statistics.mean(r) / downside * math.sqrt(365)
    assert sortino(EQUITY) == pytest.approx(expected, rel=1e-6)


def test_sortino_is_infinite_without_any_loss_and_zero_if_flat():
    assert sortino([100, 101, 102, 103]) == math.inf
    assert sortino([100, 100, 100]) == 0.0


def test_sharpe_of_constant_equity_is_zero():
    assert sharpe([100, 100, 100, 100]) == 0.0


def test_calmar_is_annualized_return_over_drawdown():
    eq = [100, 120, 90, 130]
    assert calmar(eq, days=365) == pytest.approx(annualized_return(eq, 365) / max_drawdown(eq))


def test_calmar_without_drawdown():
    assert calmar([100, 110], days=365) == math.inf


def test_too_short_series_are_refused():
    with pytest.raises(ValueError):
        total_return([100])
    with pytest.raises(ValueError):
        sharpe([100, 101])


def test_sharpe_and_sortino_scale_with_the_number_of_periods_per_year():
    assert sharpe(EQUITY, periods=2190) == pytest.approx(sharpe(EQUITY) * math.sqrt(2190 / 365), rel=1e-9)
    assert sortino(EQUITY, periods=2190) == pytest.approx(sortino(EQUITY) * math.sqrt(2190 / 365), rel=1e-9)
