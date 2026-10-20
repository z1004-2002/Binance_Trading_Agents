from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.backtest.engine import BacktestConfig, BacktestError
from orchestre.backtest.portfolio_engine import PortfolioTarget, run_portfolio_backtest
from orchestre.strategies.rotation import ConstantMix, EqualWeightHold, Rotation
from tests.unit.test_backtest import T0, bar

NO_FRICTION = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0"), slippage=D("0"))


def series(prices, start=0):
    return [bar(start + i, p, p * 1.01, p * 0.99, p) for i, p in enumerate(prices)]


class Scripted:
    def __init__(self, targets):
        self.name, self.params_tried, self._t, self.calls = "scripted", 1, list(targets), []

    def target(self, histories, positions, equity):
        self.calls.append(({s: len(h) for s, h in histories.items()}, dict(positions), equity))
        return self._t.pop(0) if self._t else PortfolioTarget(None)


def test_two_assets_split_equally_and_marked_to_market():
    data = {"AAA": series([100, 100, 110, 121]), "BBB": series([50, 50, 50, 100])}
    r = run_portfolio_backtest(data, Scripted([PortfolioTarget({"AAA": D("0.5"), "BBB": D("0.5")})]), NO_FRICTION)
    # 500 en AAA acheté à 100 (valeur finale 605), 500 en BBB acheté à 50 (valeur finale 1000)
    assert r.equity[-1] == pytest.approx(500 * 121 / 100 + 500 * 100 / 50)
    assert {t.symbol for t in r.trades} == {"AAA", "BBB"}


def test_symbols_missing_from_the_new_weights_are_sold():
    data = {"AAA": series([100] * 5), "BBB": series([100] * 5)}
    s = Scripted([PortfolioTarget({"AAA": D("0.5"), "BBB": D("0.5")}), PortfolioTarget({"AAA": D("1")})])
    r = run_portfolio_backtest(data, s, NO_FRICTION)
    assert [t.side for t in r.trades if t.symbol == "BBB"] == ["BUY", "SELL"]
    assert r.final_cash == 0 or r.final_cash < D("1")


def test_orders_use_the_next_open_and_the_strategy_only_sees_the_past():
    data = {"AAA": series([100, 120, 120, 120]), "BBB": series([100, 100, 100, 100])}
    s = Scripted([PortfolioTarget({"AAA": D("1")})])
    r = run_portfolio_backtest(data, s, BacktestConfig(initial_cash=D("1000"), fee_rate=D("0"), slippage=D("0.01")))
    assert r.trades[0].price == D("120") * D("1.01")          # ouverture de la bougie suivante
    assert [c[0]["AAA"] for c in s.calls] == [1, 2, 3, 4]      # jamais plus que le passé


def test_candles_are_aligned_on_common_timestamps():
    a, b = series([100] * 6), series([100] * 6)
    del b[2]
    r = run_portfolio_backtest({"AAA": a, "BBB": b}, Scripted([]), NO_FRICTION)
    assert len(r.equity) == 5


def test_a_stop_applies_per_symbol():
    aaa = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100), bar(2, 100, 101, 80, 85), bar(3, 85, 86, 84, 85)]
    bbb = series([100] * 4)
    t = PortfolioTarget({"AAA": D("0.5"), "BBB": D("0.5")}, stops={"AAA": D("90")})
    r = run_portfolio_backtest({"AAA": aaa, "BBB": bbb}, Scripted([t]), NO_FRICTION)
    stops = [x for x in r.trades if x.reason == "stop"]
    assert len(stops) == 1 and stops[0].symbol == "AAA" and stops[0].price == D("90")


def test_weights_cannot_exceed_one_or_be_negative():
    data = {"AAA": series([100] * 4), "BBB": series([100] * 4)}
    with pytest.raises(BacktestError):
        run_portfolio_backtest(data, Scripted([PortfolioTarget({"AAA": D("0.7"), "BBB": D("0.5")})]), NO_FRICTION)
    with pytest.raises(BacktestError):
        run_portfolio_backtest(data, Scripted([PortfolioTarget({"AAA": D("-0.1")})]), NO_FRICTION)


def test_cash_never_goes_negative_with_costs():
    data = {"AAA": series([100] * 6), "BBB": series([100] * 6)}
    cfg = BacktestConfig(initial_cash=D("1000"), fee_rate=D("0.01"), slippage=D("0.01"))
    t = PortfolioTarget({"AAA": D("0.5"), "BBB": D("0.5")})
    assert run_portfolio_backtest(data, Scripted([t, t]), cfg).final_cash >= 0


def test_unclosed_or_misaligned_inputs_are_refused():
    with pytest.raises(BacktestError):
        run_portfolio_backtest({}, Scripted([]), NO_FRICTION)
    with pytest.raises(BacktestError):
        run_portfolio_backtest({"AAA": series([100] * 3)}, Scripted([]), NO_FRICTION, warmup=5)


# --- stratèges de portefeuille ---

def hist(prices, start=0):
    return series(prices, start)


def test_equal_weight_hold_buys_once():
    s = EqualWeightHold()
    h = {"AAA": hist([100]), "BBB": hist([100])}
    first = s.target(h, {}, D("1000"))
    assert first.weights == {"AAA": D("0.5"), "BBB": D("0.5")}
    assert s.target(h, {}, D("1000")).weights is None


def test_rotation_holds_the_top_k_performers_equally_weighted():
    up = lambda r: hist([100 * (1 + r * i / 30) for i in range(30)])  # noqa: E731
    h = {"BTCUSDT": up(0.1), "ETHUSDT": up(0.5), "SOLUSDT": up(0.3), "BNBUSDT": up(-0.2), "XRPUSDT": up(0.0)}
    t = Rotation(top_k=3, lookback=21).target(h, {}, D("1000"))
    assert set(t.weights) == {"ETHUSDT", "SOLUSDT", "BTCUSDT"} and sum(t.weights.values()) <= 1
    assert all(w == D(1) / 3 for w in t.weights.values())


def test_rotation_only_rebalances_weekly():
    h = {"BTCUSDT": hist([100 + i for i in range(40)]), "ETHUSDT": hist([100 + 2 * i for i in range(40)])}
    s = Rotation(top_k=1, lookback=21, rebalance_days=7)
    assert s.target({k: v[:30] for k, v in h.items()}, {}, D("1000")).weights is not None
    assert s.target({k: v[:33] for k, v in h.items()}, {}, D("1000")).weights is None
    assert s.target({k: v[:37] for k, v in h.items()}, {}, D("1000")).weights is not None


def test_rotation_goes_to_cash_when_the_btc_regime_is_bearish(monkeypatch):
    from orchestre.analysis.regime import RegimeReading
    from orchestre.analysis.regimes import Regime
    import orchestre.strategies.rotation as module

    monkeypatch.setattr(module, "classify", lambda candles: RegimeReading(Regime.TENDANCE_BAISSE, 0.9, {}))
    h = {"BTCUSDT": hist([100 + i for i in range(30)]), "ETHUSDT": hist([100 + i for i in range(30)])}
    assert Rotation().target(h, {}, D("1000")).weights == {}


def test_rotation_skips_assets_without_enough_history():
    h = {"BTCUSDT": hist([100 + i for i in range(30)]), "NEWUSDT": hist([100, 101, 102])}
    t = Rotation(top_k=3, lookback=21).target(h, {}, D("1000"))
    assert set(t.weights) == {"BTCUSDT"}


def test_constant_mix_rebalances_on_band_breach_and_each_month():
    s = ConstantMix({"BTCUSDT": D("0.5")}, band=D("0.05"))
    h = lambda n, price: {"BTCUSDT": hist([price] * n)}  # noqa: E731
    assert s.target(h(5, 100), {}, D("1000")).weights == {"BTCUSDT": D("0.5")}          # première mise en place
    assert s.target(h(6, 100), {"BTCUSDT": D("5")}, D("1000")).weights is None          # 50 % : dans la bande
    assert s.target(h(7, 120), {"BTCUSDT": D("5")}, D("1100")).weights is None          # 54,5 % : 4,5 points, dans la bande
    assert s.target(h(8, 130), {"BTCUSDT": D("5")}, D("1150")).weights is not None      # 56,5 % : 6,5 points d'écart
    s2 = ConstantMix({"BTCUSDT": D("0.5")}, band=D("0.05"))
    s2.target(h(5, 100), {}, D("1000"))
    month_later = {"BTCUSDT": hist([100] * 40)}
    assert s2.target(month_later, {"BTCUSDT": D("5")}, D("1000")).weights is not None   # changement de mois


def test_random_rotation_is_reproducible_and_keeps_the_cash_rule(monkeypatch):
    from orchestre.strategies.rotation import RandomRotation
    h = {sym: hist([100 + i for i in range(30)]) for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")}
    a = RandomRotation(seed=3, top_k=2).target(h, {}, D("1000"))
    b = RandomRotation(seed=3, top_k=2).target(h, {}, D("1000"))
    assert a.weights == b.weights and len(a.weights) == 2
    picks = {frozenset(RandomRotation(seed=k, top_k=2).target(h, {}, D("1000")).weights) for k in range(12)}
    assert len(picks) > 1  # les tirages varient selon la graine

    from orchestre.analysis.regime import RegimeReading
    from orchestre.analysis.regimes import Regime
    import orchestre.strategies.rotation as module
    monkeypatch.setattr(module, "classify", lambda candles: RegimeReading(Regime.TENDANCE_BAISSE, 0.9, {}))
    assert RandomRotation(seed=1).target(h, {}, D("1000")).weights == {}
