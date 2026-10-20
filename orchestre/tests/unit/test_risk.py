from dataclasses import replace
from decimal import Decimal as D

import pytest

from orchestre.config import Env, parse_settings
from orchestre.exchange.filters import Side
from orchestre.risk.engine import ApprovedOrder, review
from orchestre.risk.models import OrderProposal, RiskContext

SETTINGS = parse_settings({"max_total_capital_quote": "500"})


def proposal(**kw):
    base = dict(
        agent_id="dca", symbol="BTCUSDT", base_asset="BTC", side=Side.BUY,
        quantity=D("0.001"), price=D("60000"), limit_price=None, env=Env.TESTNET,
        est_fee=D("0.06"),
    )
    base.update(kw)
    return OrderProposal(**base)


def context(**kw):
    base = dict(
        settings=SETTINGS, env=Env.TESTNET, kill_switch=False, agent_running=True,
        agent_budget_available=D("100"), total_committed_quote=D("0"),
        balance_quote=D("500"), capital_quote=D("500"), last_price=D("60000"),
        min_notional=D("5"), max_order_notional=D("100"), max_price_deviation_pct=D("0.02"),
        pair_liquid=True, pair_allowed=True, asset_exposure_quote=D("0"),
        total_crypto_exposure_quote=D("0"), open_orders_agent=0, max_open_orders_agent=10,
        open_orders_symbol=0, max_open_orders_symbol=50, drawdown_pct=D("0"),
        daily_loss_pct=D("0"), consecutive_errors=0, max_consecutive_errors=3,
        orders_today=0, max_orders_per_day=50,
    )
    base.update(kw)
    return RiskContext(**base)


def rule_of(p=None, **ctx):
    d = review(p or proposal(), context(**ctx))
    return d.rule_id


def test_accepts_nominal_order_and_signs_it():
    d = review(proposal(), context())
    assert d.approved and isinstance(d.order, ApprovedOrder)


def test_approved_order_cannot_be_forged():
    with pytest.raises(PermissionError):
        ApprovedOrder(proposal=proposal(), _seal=object())


def test_rejection_carries_no_order():
    d = review(proposal(), context(kill_switch=True))
    assert not d.approved and d.order is None and d.reason


def test_r01_kill_switch_and_agent_state():
    assert rule_of(kill_switch=True) == "R-01"
    assert rule_of(agent_running=False) == "R-01"


def test_r02_wrong_environment():
    assert rule_of(proposal(env=Env.LIVE)) == "R-02"


def test_r03_agent_budget_includes_fees():
    assert rule_of(agent_budget_available=D("60.05")) == "R-03"
    assert rule_of(agent_budget_available=D("60.06")) is None


def test_r04_global_cap_and_treasury_reserve():
    # réserve 20 % de 500 => 400 investissables
    assert rule_of(total_committed_quote=D("340.01")) == "R-04"
    assert rule_of(total_committed_quote=D("340")) is None


def test_r05_real_balance_must_cover_cost_and_fee():
    assert rule_of(balance_quote=D("60.05")) == "R-05"


def test_r06_min_and_max_order_value():
    assert rule_of(proposal(quantity=D("0.00001"))) == "R-06"
    assert rule_of(max_order_notional=D("59")) == "R-06"


def test_r07_limit_price_deviation():
    assert rule_of(proposal(limit_price=D("70000"))) == "R-07"
    assert rule_of(proposal(limit_price=D("59500"))) is None


def test_r08_pair_must_be_liquid_and_allowed():
    assert rule_of(pair_liquid=False) == "R-08"
    assert rule_of(pair_allowed=False) == "R-08"


def test_r09_exposure_per_asset():
    # 40 % de 500 = 200
    assert rule_of(asset_exposure_quote=D("140.01")) == "R-09"


def test_r10_total_crypto_exposure():
    # 80 % de 500 = 400
    assert rule_of(total_crypto_exposure_quote=D("340.01")) == "R-10"


def test_r11_target_exposure_needs_stop():
    assert rule_of(proposal(is_target_exposure=True, has_stop=False)) == "R-11"
    assert rule_of(proposal(is_target_exposure=True, has_stop=True)) is None


def test_r12_open_order_limits():
    assert rule_of(open_orders_agent=10) == "R-12"
    assert rule_of(open_orders_symbol=50) == "R-12"


def test_r13_drawdown_freezes_new_positions_but_not_reductions():
    assert rule_of(drawdown_pct=D("0.10")) == "R-13"
    assert rule_of(daily_loss_pct=D("0.03")) == "R-13"
    sell = proposal(side=Side.SELL, reduces_risk=True)
    assert review(sell, context(drawdown_pct=D("0.20"))).approved


def test_r14_circuit_breaker():
    assert rule_of(consecutive_errors=3) == "R-14"


def test_r15_order_frequency_cap():
    assert rule_of(orders_today=50) == "R-15"


def test_r16_leverage_and_instrument_enabled():
    assert rule_of(proposal(leverage=D("2"))) == "R-16"
    assert rule_of(proposal(instrument="futures")) == "R-16"


def test_first_failing_rule_wins():
    d = review(proposal(), context(kill_switch=True, balance_quote=D("0")))
    assert d.rule_id == "R-01"


def test_reduction_still_blocked_by_kill_switch_and_wrong_env():
    sell = proposal(side=Side.SELL, reduces_risk=True)
    assert review(sell, context(kill_switch=True)).rule_id == "R-01"
    assert review(replace(sell, env=Env.LIVE), context()).rule_id == "R-02"


# --- sortie d'urgence sous kill switch (réduction de risque uniquement, C-08) ---

def test_an_emergency_exit_passes_the_kill_switch_but_nothing_else_does():
    sell = proposal(side=Side.SELL, reduces_risk=True, emergency=True)
    assert review(sell, context(kill_switch=True)).approved
    assert review(sell, context(kill_switch=True, agent_running=False)).approved
    assert review(proposal(side=Side.SELL, reduces_risk=True), context(kill_switch=True)).rule_id == "R-01"       # vente ordinaire : bloquée
    assert review(proposal(side=Side.BUY, emergency=True), context(kill_switch=True)).rule_id == "R-01"           # un achat n'est jamais « d'urgence »
    assert review(replace(sell, env=Env.LIVE), context(kill_switch=True)).rule_id == "R-02"                         # l'environnement reste vérifié


def test_an_emergency_flag_without_risk_reduction_is_ignored():
    assert review(proposal(side=Side.SELL, emergency=True), context(kill_switch=True)).rule_id == "R-01"
