from decimal import Decimal as D

from orchestre.backtest.engine import Target
from orchestre.strategies.breakout import DonchianBreakout
from tests.unit.test_backtest import bar


def flat(n=60):
    return [bar(i, 100, 101, 99, 100) for i in range(n)]


def with_last(close, high=None, low=None, n=60):
    candles = flat(n)
    candles.append(bar(n, 100, high or close + 1, low or min(close, 100) - 1, close))
    return candles


def test_no_decision_without_enough_history():
    assert DonchianBreakout().target(flat(10), D("0")) == Target(None)


def test_no_entry_inside_the_channel():
    t = DonchianBreakout().target(with_last(100.5), D("0"))
    assert t.exposure is None or t.exposure == D("0")


def test_entry_when_the_close_breaks_the_20_day_high_with_an_atr_stop():
    t = DonchianBreakout().target(with_last(105), D("0"))
    assert t.exposure == D("1") and D("0") < t.stop < D("105")


def test_the_initial_stop_is_kept_while_the_position_runs():
    s = DonchianBreakout()
    s.target(with_last(105), D("0"))
    t = s.target(with_last(106), D("1"))
    assert t == Target(None)  # ni nouvel ordre ni nouveau stop : le stop initial reste en place


def test_exit_when_the_close_breaks_the_10_day_low():
    s = DonchianBreakout()
    s.target(with_last(105), D("0"))
    t = s.target(with_last(90, high=101, low=89), D("1"))
    assert t.exposure == D("0") and t.stop is None


def test_stop_out_resets_the_state_so_a_new_breakout_is_required():
    s = DonchianBreakout()
    s.target(with_last(105), D("0"))
    after_stop = s.target(flat(), D("0"))      # position nulle alors qu'on était entré : stoppé
    assert after_stop.exposure in (None, D("0"))
    assert s.target(with_last(107), D("0")).exposure == D("1")  # une nouvelle cassure ré-entre


def test_parameters_are_exposed_and_validated():
    assert DonchianBreakout(entry_len=40, exit_len=20).params_tried == 1
    import pytest
    with pytest.raises(ValueError):
        DonchianBreakout(entry_len=10, exit_len=20)
