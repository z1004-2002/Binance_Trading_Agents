import json
from decimal import Decimal as D

from orchestre.strategies.breakout import DonchianBreakout
from orchestre.strategies.meanreversion import MeanReversion
from orchestre.strategies.rotation import ConstantMix, EqualWeightHold, Rotation
from orchestre.strategies.trend import TrendFollowing
from tests.unit.analysis_data import uptrend
from tests.unit.test_meanreversion import with_closes
from tests.unit.test_portfolio_engine import hist


def roundtrip(strategy, fresh):
    state = json.loads(json.dumps(strategy.state()))       # doit survivre à la base de données (JSON)
    fresh.load_state(state)
    return fresh


def test_trend_following_state_survives_a_restart_without_dropping_the_position():
    s, history = TrendFollowing(), uptrend(300)
    assert s.target(history, D("0")).exposure == D("1")          # entrée
    restarted = roundtrip(s, TrendFollowing())
    t = restarted.target(history, D("1"))                          # en position, signal toujours là
    assert t.exposure == D("1") and t.stop is not None
    # sans restauration, une instance neuve se comporterait comme une nouvelle entrée (sans plus haut mémorisé)
    assert restarted.state()["highest"] == s.state()["highest"] or restarted.state()["highest"] >= s.state()["highest"]


def test_breakout_in_trade_flag_survives_so_the_position_is_not_sold_on_restart():
    s = DonchianBreakout()
    from tests.unit.test_breakout import with_last
    s.target(with_last(105), D("0"))
    restarted = roundtrip(s, DonchianBreakout())
    assert restarted.target(with_last(100.5), D("1")).exposure is None        # prix dans le canal : on garde la position
    fresh = DonchianBreakout()
    assert fresh.target(with_last(100.5), D("1")).exposure == D("0")          # sans état restauré : vente erronée


def test_mean_reversion_state():
    s = MeanReversion()
    s.target(with_closes([85.0]), D("0"))
    restarted = roundtrip(s, MeanReversion())
    assert restarted.state() == {"in_trade": True, "bars": 0}


def test_rotation_and_mix_and_equal_weight_state():
    h = {"BTCUSDT": hist([100 + i for i in range(40)]), "ETHUSDT": hist([100 + 2 * i for i in range(40)])}
    rot = Rotation(top_k=1)
    rot.target({k: v[:30] for k, v in h.items()}, {}, D("1000"))
    again = roundtrip(rot, Rotation(top_k=1))
    assert again.target({k: v[:33] for k, v in h.items()}, {}, D("1000")).weights is None     # toujours pas une semaine

    mix = ConstantMix({"BTCUSDT": D("0.5")})
    mix.target({"BTCUSDT": hist([100] * 5)}, {}, D("1000"))
    assert roundtrip(mix, ConstantMix({"BTCUSDT": D("0.5")})).state() == mix.state()

    eq = EqualWeightHold()
    eq.target({"BTCUSDT": hist([100])}, {}, D("1000"))
    assert roundtrip(eq, EqualWeightHold()).target({"BTCUSDT": hist([100])}, {}, D("1000")).weights is None
