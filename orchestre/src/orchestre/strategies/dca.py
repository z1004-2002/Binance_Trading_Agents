"""S-1 DCA : achat d'un montant fixe à intervalle régulier (§11.2). Logique pure (A-2).

Un achat par échéance (EX-ST-02) : l'identifiant d'ordre est déterministe par échéance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class DcaConfig:
    agent_id: str
    symbol: str
    amount_quote: Decimal
    interval: timedelta
    start: datetime
    cumulative_cap: Decimal
    min_notional: Decimal
    missed_policy: str = "skip"  # "skip" | "catchup_once"
    modulated: bool = False

    def __post_init__(self) -> None:
        if self.amount_quote < self.min_notional:
            raise ValueError("montant sous la valeur minimale d'ordre (EX-ST-01)")
        if self.missed_policy not in ("skip", "catchup_once"):
            raise ValueError("missed_policy invalide")
        if self.start.tzinfo is None:
            raise ValueError("start doit être en UTC")


@dataclass(frozen=True)
class DcaState:
    last_slot: datetime | None = None
    total_spent: Decimal = Decimal("0")


@dataclass(frozen=True)
class DcaOrder:
    slot: datetime
    amount_quote: Decimal
    quantity: Decimal
    client_order_id: str
    reason: str


def _latest_slot(cfg: DcaConfig, now: datetime) -> tuple[datetime, int] | None:
    if now < cfg.start:
        return None
    k = (now - cfg.start) // cfg.interval
    return cfg.start + k * cfg.interval, k


def _sentiment_factor(fear_greed: int | None) -> Decimal:
    if fear_greed is None:
        return Decimal("1")
    if fear_greed < 25:
        return Decimal("1.5")
    if fear_greed > 75:
        return Decimal("0.5")
    return Decimal("1")


def decide(
    cfg: DcaConfig,
    state: DcaState,
    now: datetime,
    price: Decimal,
    fear_greed: int | None = None,
    budget_available: Decimal | None = None,
) -> DcaOrder | None:
    due = _latest_slot(cfg, now)
    if due is None:
        return None
    slot, k = due
    if state.last_slot is not None and state.last_slot >= slot:
        return None

    prev_slot = state.last_slot
    missed = (
        k - ((prev_slot - cfg.start) // cfg.interval) - 1
        if prev_slot is not None
        else k
    )
    amount = cfg.amount_quote
    notes = [f"échéance {slot.isoformat()}"]
    if cfg.modulated:
        factor = _sentiment_factor(fear_greed)
        amount = cfg.amount_quote * factor
        notes.append(f"sentiment x{factor}")
    if missed > 0 and cfg.missed_policy == "catchup_once":
        amount += cfg.amount_quote
        notes.append("un rattrapage")
    if budget_available is not None:
        amount = min(amount, budget_available)
    amount = min(amount, cfg.cumulative_cap - state.total_spent)
    if amount < cfg.min_notional:
        return None

    return DcaOrder(
        slot=slot,
        amount_quote=amount,
        quantity=amount / price,
        client_order_id=f"{cfg.agent_id}-{slot:%Y%m%dT%H%M%S}",
        reason="DCA : " + ", ".join(notes),
    )


def record(state: DcaState, order: DcaOrder) -> DcaState:
    return replace(state, last_slot=order.slot, total_spent=state.total_spent + order.amount_quote)
