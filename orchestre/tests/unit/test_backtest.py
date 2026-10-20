from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.backtest.engine import BacktestConfig, BacktestError, Target, run_backtest
from orchestre.backtest.references import BuyAndHold, CashOnly, RebalancedMix, WeeklyDca
from orchestre.exchange.base import Candle

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NO_FRICTION = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0"), slippage=D("0"))


def bar(i, o, h, l, c, closed=True):
    t = T0 + timedelta(days=i)
    return Candle(t, t + timedelta(days=1) - timedelta(seconds=1), D(str(o)), D(str(h)), D(str(l)), D(str(c)), D("1"), closed)


def flat_bars(n, price=100):
    return [bar(i, price, price + 1, price - 1, price) for i in range(n)]


class Scripted:
    """Stratège de test : renvoie les cibles prévues, dans l'ordre des appels."""

    def __init__(self, targets, name="scripted"):
        self.name, self.params_tried, self._targets, self.calls = name, 1, list(targets), []

    def target(self, history, position):
        self.calls.append((len(history), history[-1], position))
        return self._targets.pop(0) if self._targets else Target(None)


def test_cash_strategy_keeps_equity_constant_and_never_trades():
    r = run_backtest(flat_bars(10), CashOnly(), NO_FRICTION)
    assert set(r.equity) == {1000.0} and r.trades == []


def test_order_is_executed_at_the_next_open_with_slippage_and_fee():
    candles = [bar(0, 100, 101, 99, 100), bar(1, 110, 111, 109, 112), bar(2, 112, 113, 111, 113)]
    cfg = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0.001"), slippage=D("0.01"))
    r = run_backtest(candles, Scripted([Target(D("1"))]), cfg)
    trade = r.trades[0]
    assert trade.time == candles[1].open_time and trade.reason == "signal"
    assert trade.price == D("110") * D("1.01")  # ouverture de la bougie SUIVANTE, pas la clôture 100
    assert trade.fee == trade.qty * trade.price * D("0.001")
    assert r.fees_paid == trade.fee


def test_strategy_only_ever_sees_the_past():
    candles = flat_bars(8)
    s = Scripted([])
    run_backtest(candles, s, NO_FRICTION)
    assert [(n, c.open_time) for n, c, _ in s.calls] == [(i + 1, candles[i].open_time) for i in range(8)]


def test_frictionless_buy_and_hold_equals_price_ratio():
    candles = [bar(0, 100, 101, 99, 100), bar(1, 100, 121, 99, 120), bar(2, 120, 131, 119, 130)]
    r = run_backtest(candles, BuyAndHold(), NO_FRICTION)
    assert r.equity[-1] == pytest.approx(1000 * 130 / 100, rel=1e-6)


def test_frictions_reduce_the_result():
    candles = flat_bars(5)
    free = run_backtest(candles, BuyAndHold(), NO_FRICTION).equity[-1]
    costly = run_backtest(candles, BuyAndHold(), BacktestConfig(fee_rate=D("0.001"), slippage=D("0.001"))).equity[-1]
    assert costly < free


def test_stop_fills_at_the_stop_price_when_the_low_crosses_it():
    candles = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 100, 101, 85, 88), bar(3, 88, 89, 87, 88)]
    cfg = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0"), slippage=D("0.01"))
    s = Scripted([Target(D("1"), stop=D("90"))])
    r = run_backtest(candles, s, cfg)
    stop_trade = [t for t in r.trades if t.reason == "stop"][0]
    assert stop_trade.price == D("90") * D("0.99") and stop_trade.time == candles[2].open_time
    assert s.calls[-1][2] == 0  # la stratégie voit qu'elle est sortie


def test_stop_fills_at_the_open_on_a_gap_below_the_stop():
    candles = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 80, 82, 78, 81)]
    r = run_backtest(candles, Scripted([Target(D("1"), stop=D("90"))]), NO_FRICTION)
    assert [t for t in r.trades if t.reason == "stop"][0].price == D("80")


def test_small_rebalancing_deviations_do_not_trade():
    candles = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 101, 102, 100, 101), bar(3, 101, 102, 100, 101)]
    r = run_backtest(candles, Scripted([Target(D("0.5")), Target(D("0.5")), Target(D("0.5"))]), NO_FRICTION)
    assert len(r.trades) == 1


def test_half_exposure_invests_half_of_the_equity():
    candles = flat_bars(4)
    r = run_backtest(candles, Scripted([Target(D("0.5"))]), NO_FRICTION)
    assert r.trades[0].qty * r.trades[0].price == pytest.approx(500, rel=1e-6)


def test_exposure_zero_exits_completely():
    candles = flat_bars(5)
    r = run_backtest(candles, Scripted([Target(D("1")), Target(D("0"))]), NO_FRICTION)
    assert [t.side for t in r.trades] == ["BUY", "SELL"]


def test_cash_never_goes_negative_with_fees():
    candles = flat_bars(6)
    cfg = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0.01"), slippage=D("0.01"))
    r = run_backtest(candles, Scripted([Target(D("1")), Target(D("1"))]), cfg)
    assert r.final_cash >= 0


def test_warmup_skips_the_beginning_but_the_strategy_keeps_the_full_history():
    candles = flat_bars(10)
    s = Scripted([])
    r = run_backtest(candles, s, NO_FRICTION, warmup=4)
    assert len(r.equity) == 6 and s.calls[0][0] == 5


def test_params_tried_is_reported():
    s = Scripted([])
    s.params_tried = 12
    assert run_backtest(flat_bars(5), s, NO_FRICTION).params_tried == 12


def test_unclosed_or_unordered_data_is_refused():
    with pytest.raises(BacktestError):
        run_backtest([*flat_bars(3), bar(3, 1, 1, 1, 1, closed=False)], CashOnly(), NO_FRICTION)
    c = flat_bars(3)
    with pytest.raises(BacktestError):
        run_backtest([c[0], c[2], c[1]], CashOnly(), NO_FRICTION)
    with pytest.raises(BacktestError):
        run_backtest(flat_bars(2), CashOnly(), NO_FRICTION, warmup=5)


# --- références (§16.3) ---

def test_buy_and_hold_orders_once():
    s = BuyAndHold()
    assert s.target(flat_bars(1), D("0")).exposure == D("1")
    assert s.target(flat_bars(2), D("1")).exposure is None


def test_cash_only_never_invests():
    assert CashOnly().target(flat_bars(1), D("0")).exposure == D("0")


def test_rebalanced_mix_acts_on_first_call_and_each_new_month():
    s = RebalancedMix(D("0.5"))
    days = [bar(i, 100, 101, 99, 100) for i in range(70)]  # 1er janvier ... mars
    exposures = [s.target(days[: i + 1], D("0")).exposure for i in range(70)]
    acted = [i for i, e in enumerate(exposures) if e is not None]
    assert acted == [0, 31, 59]  # 1er janvier, 1er février, 1er mars
    assert all(exposures[i] == D("0.5") for i in acted)


def test_weekly_dca_ramps_up_one_slice_per_week():
    s = WeeklyDca(weeks=4)
    days = [bar(i, 100, 101, 99, 100) for i in range(40)]
    exposures = {i: s.target(days[: i + 1], D("0")).exposure for i in range(40)}
    acted = {i: e for i, e in exposures.items() if e is not None}
    assert acted == {0: D("0.25"), 7: D("0.5"), 14: D("0.75"), 21: D("1")}
