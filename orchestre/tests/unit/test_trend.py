from decimal import Decimal as D

from orchestre.backtest.engine import BacktestConfig, Target, run_backtest
from orchestre.backtest.references import BuyAndHold
from orchestre.metrics.performance import max_drawdown
from orchestre.strategies.trend import TrendFollowing
from tests.unit.analysis_data import _alternating, downtrend, make_candles, uptrend


def test_no_decision_without_enough_history():
    assert TrendFollowing().target(uptrend(50), D("0")) == Target(None)


def test_uptrend_gives_full_exposure_and_a_stop_below_the_price():
    candles = uptrend(300)
    t = TrendFollowing().target(candles, D("0"))
    assert t.exposure == D("1")
    assert D("0") < t.stop < candles[-1].close


def test_downtrend_gives_zero_exposure_and_no_stop():
    t = TrendFollowing().target(downtrend(300), D("0"))
    assert t.exposure == D("0") and t.stop is None


def test_trailing_stop_only_moves_up_in_a_rising_market():
    candles, s = uptrend(300), TrendFollowing()
    stops = []
    for end in range(150, 300):
        t = s.target(candles[:end], D("1") if stops else D("0"))
        stops.append(t.stop)
    assert all(b >= a - D("0.0000001") for a, b in zip(stops, stops[1:]))


def test_volatility_targeting_reduces_exposure_in_a_wild_market():
    closes = _alternating(100.0, 300, 1.05, 1 / 1.04)  # montée nette mais très volatile
    t = TrendFollowing().target(make_candles(closes), D("0"))
    assert D("0") < t.exposure < D("1")


def test_vol_targeting_can_be_disabled():
    closes = _alternating(100.0, 300, 1.05, 1 / 1.04)
    t = TrendFollowing(use_vol_target=False).target(make_candles(closes), D("0"))
    assert t.exposure == D("1")


def test_no_immediate_reentry_after_a_stop_out_until_the_signal_resets():
    s = TrendFollowing()
    up, down = uptrend(300), downtrend(300)
    assert s.target(up, D("0")).exposure == D("1")          # entrée
    assert s.target(up, D("1")).exposure == D("1")          # en position
    assert s.target(up, D("0")).exposure == D("0")          # position nulle alors que le signal tient : stoppé, bloqué
    assert s.target(up, D("0")).exposure == D("0")          # toujours bloqué
    assert s.target(down, D("0")).exposure == D("0")        # le signal retombe : déblocage
    assert s.target(up, D("0")).exposure == D("1")          # nouvelle entrée autorisée


def test_params_tried_is_exposed_for_overfitting_control():
    assert TrendFollowing().params_tried == 1


def crash_series():
    closes = _alternating(100.0, 250, 1.004, 1.002)
    price = closes[-1]
    for _ in range(150):
        price *= 0.995
        closes.append(price)
    return make_candles(closes)


def test_backtest_trend_limits_the_drawdown_of_a_crash_versus_buy_and_hold():
    candles = crash_series()
    cfg = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0.001"), slippage=D("0.0005"))
    trend = run_backtest(candles, TrendFollowing(), cfg, warmup=120)
    hold = run_backtest(candles, BuyAndHold(), cfg, warmup=120)
    assert max_drawdown(trend.equity) < 0.5 * max_drawdown(hold.equity)
    assert any(t.reason == "stop" for t in trend.trades)
