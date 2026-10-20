import math

import pytest

from orchestre.analysis.indicators import (
    adx, atr, ewma_vol, log_returns, percentile, realized_vol, rolling_realized_vol, sma, sma_series,
)


def test_sma_and_series():
    assert sma([1, 2, 3, 4, 5], 3) == 4
    assert sma_series([1, 2, 3, 4, 5], 3) == [2, 3, 4]


def test_sma_needs_enough_values():
    with pytest.raises(ValueError):
        sma([1, 2], 3)


def test_atr_of_constant_range_is_that_range():
    closes = [100.0] * 40
    highs, lows = [101.0] * 40, [99.0] * 40
    assert atr(highs, lows, closes, 14) == pytest.approx(2.0)


def test_atr_includes_gaps_through_previous_close():
    highs, lows, closes = [10, 20], [9, 19], [9.5, 19.5]
    # TR du 2e jour = max(h-l=1, |h-prev_close|=10.5, |l-prev_close|=9.5) = 10.5
    assert atr(highs, lows, closes, 1) == pytest.approx(10.5)


def trend(n, step):
    closes = [100 + step * i for i in range(n)]
    return [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes


def test_adx_is_high_in_a_steady_trend():
    h, l, c = trend(80, 1.0)
    assert adx(h, l, c, 14) > 40


def test_adx_is_high_in_a_steady_downtrend_too():
    h, l, c = trend(80, -1.0)
    assert adx(h, l, c, 14) > 40


def test_adx_is_low_in_a_choppy_range():
    closes = [100 + (3 if i % 2 else -3) for i in range(80)]
    highs, lows = [c + 0.5 for c in closes], [c - 0.5 for c in closes]
    assert adx(highs, lows, closes, 14) < 20


def test_adx_needs_enough_history():
    h, l, c = trend(20, 1.0)
    with pytest.raises(ValueError):
        adx(h, l, c, 14)


def test_log_returns_and_realized_vol_of_alternating_moves():
    closes = [100.0]
    for i in range(60):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    assert log_returns(closes)[0] == pytest.approx(math.log(1.01))
    assert realized_vol(closes, 30) == pytest.approx(0.01 * math.sqrt(365), rel=0.05)


def test_rolling_realized_vol_length():
    closes = [100.0 + (i % 7) for i in range(100)]
    assert len(rolling_realized_vol(closes, 30)) == 100 - 1 - 30 + 1


def test_ewma_vol_converges_to_constant_move_size():
    closes = [100.0]
    for i in range(300):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    assert ewma_vol(closes) == pytest.approx(0.01 * math.sqrt(365), rel=0.02)


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile([1, 2, 3, 4, 5], 0.9) == pytest.approx(4.6)
    assert percentile([7], 0.9) == 7


from orchestre.analysis.indicators import bollinger, rsi  # noqa: E402


def test_rsi_extremes_and_flat():
    assert rsi([100 + i for i in range(30)], 14) == 100.0
    assert rsi([100 - i for i in range(30)], 14) == 0.0
    assert rsi([100.0] * 30, 14) == 50.0


def test_rsi_is_close_to_50_for_symmetric_moves_and_below_after_a_sharp_drop():
    zigzag = [100 + (1 if i % 2 else 0) for i in range(60)]
    assert 40 < rsi(zigzag, 14) < 60
    assert rsi([*zigzag, 85.0], 14) < 30


def test_rsi_needs_enough_history():
    with pytest.raises(ValueError):
        rsi([1, 2, 3], 14)


def test_bollinger_bands():
    lower, mid, upper = bollinger([1, 2, 3, 4, 5], 5, 2.0)
    assert mid == 3 and upper == pytest.approx(3 + 2 * math.sqrt(2)) and lower == pytest.approx(3 - 2 * math.sqrt(2))
    assert bollinger([7.0] * 20, 20) == (7.0, 7.0, 7.0)
