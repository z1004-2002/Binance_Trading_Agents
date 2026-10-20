from decimal import Decimal as D

import pytest

from orchestre.analysis.regimes import Regime
from orchestre.portfolio.allocator import (
    AllocationError,
    StrategyBudget,
    apply_allocations,
    compute_allocations,
    min_viable_budget,
)
from orchestre.portfolio.ledger import Ledger

CONFIGS = [
    StrategyBudget("dca", "S-1", D("100"), D("10")),
    StrategyBudget("grid", "S-2", D("120"), D("60")),
    StrategyBudget("trend", "S-3", D("100"), D("10")),
    StrategyBudget("rebal", "S-7", D("80"), D("10")),
]


def test_min_viable_budget_matches_spec_example():
    # Grid de 10 niveaux, minimum 5 USDT, marge 1,2 => 60 USDT (EX-AL-03)
    assert min_viable_budget(10, D("5")) == D("60.0")


def test_range_regime_gives_full_grid_budget():
    a = compute_allocations(CONFIGS, Regime.RANGE, D("500"), D("0.20"))
    assert a["grid"].budget == D("120") and a["grid"].status == "LIVE"


def test_bear_regime_zeroes_grid_and_trend_to_paper():
    a = compute_allocations(CONFIGS, Regime.TENDANCE_BAISSE, D("500"), D("0.20"))
    assert a["grid"].budget == D("0") and a["grid"].status == "PAPER"
    assert a["trend"].budget == D("0")
    assert a["dca"].budget == D("100") and a["rebal"].budget == D("80")


def test_half_multiplier_below_min_viable_goes_paper():
    # Grid en TENDANCE_HAUSSE : 0,5 x 120 = 60 >= 60 viable ; avec 110 de base : 55 < 60
    cfg = [StrategyBudget("grid", "S-2", D("110"), D("60"))]
    a = compute_allocations(cfg, Regime.TENDANCE_HAUSSE, D("500"), D("0.20"))
    assert a["grid"].status == "PAPER" and a["grid"].budget == D("0")


def test_total_never_exceeds_investable_capital():
    with pytest.raises(AllocationError):
        compute_allocations(CONFIGS, Regime.RANGE, D("400"), D("0.20"))  # 400 > 320


def test_apply_updates_ledger_and_keeps_total():
    lg = Ledger(D("500"))
    a = compute_allocations(CONFIGS, Regime.RANGE, D("500"), D("0.20"))
    apply_allocations(lg, a)
    assert lg.account("grid").allocated == D("120")
    assert lg.unallocated + sum(x.allocated for x in lg.accounts()) == D("500")


def test_regime_change_reduces_budget_but_never_below_committed():
    lg = Ledger(D("500"))
    apply_allocations(lg, compute_allocations(CONFIGS, Regime.RANGE, D("500"), D("0.20")))
    lg.commit("grid", D("90"))
    apply_allocations(lg, compute_allocations(CONFIGS, Regime.TENDANCE_BAISSE, D("500"), D("0.20")))
    assert lg.account("grid").allocated == D("90")  # engagé : conservé
    assert lg.account("trend").allocated == D("0")
