from decimal import Decimal as D

from orchestre.backtest.engine import Target
from orchestre.strategies.meanreversion import MeanReversion
from tests.unit.test_backtest import bar


def calm(n=40):
    return [bar(i, 100, 101.5, 99.5, 100 + (1 if i % 2 else 0)) for i in range(n)]


def with_closes(closes, base=None):
    candles = base if base is not None else calm()
    for k, c in enumerate(closes):
        i = len(candles)
        candles = [*candles, bar(i, c, c + 0.5, c - 0.5, c)]
    return candles


def test_no_decision_without_enough_history():
    assert MeanReversion().target(calm(10), D("0")) == Target(None)


def test_no_entry_in_a_quiet_market():
    t = MeanReversion().target(calm(), D("0"))
    assert t.exposure is None


def test_entry_after_a_sharp_drop_below_the_lower_band_with_low_rsi_and_an_atr_stop():
    t = MeanReversion().target(with_closes([85.0]), D("0"))
    assert t.exposure == D("1") and D("0") < t.stop < D("85")


def test_exit_when_the_price_returns_to_the_mean():
    s = MeanReversion()
    s.target(with_closes([85.0]), D("0"))
    t = s.target(with_closes([85.0, 101.0]), D("1"))
    assert t.exposure == D("0")


def test_position_is_held_while_neither_exit_condition_holds():
    s = MeanReversion(rsi_exit=101.0, max_bars=50)
    s.target(with_closes([85.0]), D("0"))
    assert s.target(with_closes([85.0, 85.2]), D("1")) == Target(None)


def test_forced_exit_after_max_bars():
    s = MeanReversion(rsi_exit=101.0, max_bars=3)
    s.target(with_closes([85.0]), D("0"))
    closes = [85.0]
    results = []
    for k in range(3):
        closes.append(85.0 + 0.1 * (k + 1))
        results.append(s.target(with_closes(closes), D("1")))
    assert results[0] == results[1] == Target(None) and results[2].exposure == D("0")


def test_stop_out_resets_the_state():
    s = MeanReversion()
    s.target(with_closes([85.0]), D("0"))
    # position nulle alors qu'on était entré : le stop a servi ; le prix n'est plus survendu -> rien à faire
    assert s.target(with_closes([85.0, 99.0]), D("0")).exposure is None
    assert s.target(with_closes([85.0]), D("0")).exposure == D("1")  # un nouveau creux ré-entre normalement


def test_params_tried_is_exposed():
    assert MeanReversion().params_tried == 1


def test_windowed_indicators_match_full_history_ones():
    import random
    from orchestre.analysis.indicators import atr, rsi
    rng = random.Random(7)
    price, closes = 100.0, []
    for _ in range(2000):
        price *= 1 + rng.uniform(-0.02, 0.02)
        closes.append(price)
    highs, lows = [c * 1.005 for c in closes], [c * 0.995 for c in closes]
    window = MeanReversion()._window
    assert abs(rsi(closes) - rsi(closes[-window:])) < 0.05
    assert abs(atr(highs, lows, closes) - atr(highs[-window:], lows[-window:], closes[-window:])) < 0.05
