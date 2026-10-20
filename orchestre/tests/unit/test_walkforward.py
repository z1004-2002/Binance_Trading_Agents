from decimal import Decimal as D

import pytest

from orchestre.backtest.engine import BacktestConfig, BacktestError, BacktestResult
from orchestre.backtest.walkforward import make_windows, walk_forward
from tests.unit.test_backtest import flat_bars

CFG = BacktestConfig()


def test_windows_are_contiguous_non_overlapping_and_inside_the_data():
    w = make_windows(n=1000, warmup=120, train=365, test=90)
    assert w[0] == (120, 485, 485, 575)                      # entraînement puis test, sans trou
    assert all(w[i][3] == w[i + 1][2] for i in range(len(w) - 1))   # les tests se suivent
    assert all(w[i + 1][0] == w[i][0] + 90 for i in range(len(w) - 1))  # l'entraînement glisse du pas de test
    assert all(ve <= 1000 and ts - 120 >= 0 for ts, te, vs, ve in w)
    assert len(w) == 5


def test_not_enough_history_is_refused():
    with pytest.raises(BacktestError):
        make_windows(n=500, warmup=120, train=365, test=90)


class Stub:
    def __init__(self, params):
        self.rate, self.name, self.params_tried = params["rate"], f"stub{params['rate']}", 1

    def target(self, history, position):  # jamais appelé : le lanceur est simulé
        raise AssertionError


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, candles, strategy, cfg, warmup):
        self.calls.append((candles[0].open_time, candles[-1].open_time, len(candles), strategy))
        rate = getattr(strategy, "rate", 0.0)
        equity = [1000 * (1 + rate) ** i for i in range(len(candles) - warmup)]
        return BacktestResult(strategy.name, [], equity, [], D("1"), D("0"), 1, D("0"))


class Hold:
    name, params_tried, rate = "hold", 1, 0.001


def run_wf(**kw):
    runner = FakeRunner()
    result = walk_forward(
        flat_bars(1000), lambda p: Stub(p), [{"rate": 0.0}, {"rate": 0.002}, {"rate": 0.001}], CFG,
        objective=lambda r: r.equity[-1], runner=runner, benchmark_factory=Hold, **kw,
    )
    return result, runner


def test_the_parameters_are_chosen_on_the_training_slice_only():
    result, runner = run_wf()
    candles = flat_bars(1000)
    for k, (ts, te, vs, ve) in enumerate(make_windows(1000, 120, 365, 90)):
        train_calls = runner.calls[k * 5 : k * 5 + 3]      # 3 essais d'entraînement
        test_call = runner.calls[k * 5 + 3]
        assert all(call[1] == candles[te - 1].open_time for call in train_calls)   # jamais au-delà de la fin d'entraînement
        assert test_call[0] == candles[vs - 1 - 120].open_time and test_call[1] == candles[ve - 1].open_time
        assert test_call[3].rate == 0.002                    # le meilleur de l'entraînement
        assert result.windows[k].params == {"rate": 0.002}


def test_out_of_sample_equity_is_stitched_continuously():
    result, _ = run_wf()
    assert result.oos.equity[0] == 1000.0 and len(result.oos.equity) == 1 + 5 * 90
    assert result.oos.equity[90] == pytest.approx(1000 * 1.002 ** 90, rel=1e-9)  # raccord exact entre fenêtres
    assert result.oos.equity[180] == pytest.approx(1000 * 1.002 ** 180, rel=1e-9)
    assert all(b > 0 for b in result.oos.equity)
    assert result.benchmark.equity[0] == 1000.0 and len(result.benchmark.equity) == len(result.oos.equity)


def test_the_number_of_trials_is_reported_for_overfitting_control():
    result, _ = run_wf()
    assert result.params_tried == 3 * 5      # 3 combinaisons x 5 fenêtres
    assert result.oos.params_tried == 15


def test_in_sample_and_out_of_sample_scores_are_both_exposed():
    result, _ = run_wf()
    w = result.windows[0]
    assert w.train_score > 0 and w.test_return > 0
