"""PortfolioAgent v1 : budgets fixes x multiplicateur de régime (§12.1, EX-AL-02, EX-AL-03, EX-AL-05).

Les budgets ne dépassent jamais leur plafond de configuration : toute augmentation du plafond
est une décision humaine (C-08) ; le régime ne fait que réduire.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orchestre.analysis.regimes import Regime
from orchestre.portfolio.ledger import Ledger

_H, _R, _B, _V = (Regime.TENDANCE_HAUSSE, Regime.RANGE, Regime.TENDANCE_BAISSE, Regime.HAUTE_VOLATILITE)

MULTIPLIERS: dict[str, dict[Regime, Decimal]] = {
    "S-1": {_H: Decimal("1"), _R: Decimal("1"), _B: Decimal("1"), _V: Decimal("1")},
    "S-2": {_H: Decimal("0.5"), _R: Decimal("1"), _B: Decimal("0"), _V: Decimal("0")},
    "S-3": {_H: Decimal("1"), _R: Decimal("0.5"), _B: Decimal("0"), _V: Decimal("0.5")},
    "S-4": {_H: Decimal("1"), _R: Decimal("1"), _B: Decimal("0"), _V: Decimal("0.5")},
    "S-5": {_H: Decimal("0"), _R: Decimal("1"), _B: Decimal("0"), _V: Decimal("0")},
    "S-6": {_H: Decimal("1"), _R: Decimal("0.5"), _B: Decimal("0"), _V: Decimal("0.5")},
    "S-7": {_H: Decimal("1"), _R: Decimal("1"), _B: Decimal("1"), _V: Decimal("1")},
}

SAFETY_MARGIN = Decimal("1.2")


class AllocationError(ValueError):
    pass


@dataclass(frozen=True)
class StrategyBudget:
    agent_id: str
    strategy: str  # "S-1" ... "S-7"
    base_budget: Decimal
    min_viable: Decimal


@dataclass(frozen=True)
class Allocation:
    budget: Decimal
    status: str  # "LIVE" | "PAPER"
    reason: str


def min_viable_budget(simultaneous_orders: int, min_notional: Decimal) -> Decimal:
    return simultaneous_orders * min_notional * SAFETY_MARGIN


def compute_allocations(
    configs: list[StrategyBudget],
    regime: Regime,
    total_capital: Decimal,
    treasury_reserve_pct: Decimal,
) -> dict[str, Allocation]:
    investable = total_capital * (1 - treasury_reserve_pct)
    ceiling = sum((c.base_budget for c in configs), Decimal("0"))
    if ceiling > investable:
        raise AllocationError(f"budgets configurés {ceiling} > capital investissable {investable}")

    result: dict[str, Allocation] = {}
    for c in configs:
        mult = MULTIPLIERS[c.strategy][regime]
        budget = c.base_budget * mult
        if budget < c.min_viable:
            result[c.agent_id] = Allocation(
                Decimal("0"), "PAPER", f"budget {budget} sous le minimum viable {c.min_viable} (x{mult})"
            )
        else:
            result[c.agent_id] = Allocation(budget, "LIVE", f"régime {regime.value} : multiplicateur {mult}")
    return result


def apply_allocations(ledger: Ledger, allocations: dict[str, Allocation]) -> None:
    """Baisses d'abord (libèrent du capital), hausses ensuite ; jamais sous le montant engagé."""
    targets = {
        aid: max(a.budget, ledger.account(aid).committed) for aid, a in allocations.items()
    }
    for aid, target in targets.items():
        current = ledger.account(aid).allocated
        if target < current:
            ledger.deallocate(aid, current - target)
    for aid, target in targets.items():
        current = ledger.account(aid).allocated
        if target > current:
            ledger.allocate(aid, target - current)
