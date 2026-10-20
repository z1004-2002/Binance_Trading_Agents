"""RiskAgent : décide sans agir ni effet de bord (EX-RK-01). Règles R-01 à R-16 dans l'ordre (§12.2).

Seule `review` peut produire un `ApprovedOrder` (EX-SEC-12) : l'exécuteur n'accepte que ce type.
Le sceau est structurel (anti-erreur), pas cryptographique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from orchestre.exchange.filters import Side
from orchestre.risk.models import OrderProposal, RiskContext

_SEAL = object()


@dataclass(frozen=True)
class ApprovedOrder:
    proposal: OrderProposal
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise PermissionError("seul le RiskAgent peut créer un ordre approuvé (EX-SEC-12)")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rule_id: str | None
    reason: str
    order: ApprovedOrder | None = None


Check = Callable[[OrderProposal, RiskContext], str | None]


def _r01(p: OrderProposal, c: RiskContext) -> str | None:
    if c.kill_switch:
        return "kill switch actif"
    if not c.agent_running:
        return f"agent {p.agent_id} non RUNNING"
    return None


def _r02(p: OrderProposal, c: RiskContext) -> str | None:
    if p.env is not c.env:
        return f"environnement de l'ordre ({p.env.value}) != configuré ({c.env.value})"
    return None


def _r03(p: OrderProposal, c: RiskContext) -> str | None:
    need = p.notional + p.est_fee
    if need > c.agent_budget_available:
        return f"budget disponible {c.agent_budget_available} < {need} (frais inclus)"
    return None


def _r04(p: OrderProposal, c: RiskContext) -> str | None:
    s = c.settings
    investable = s.max_total_capital_quote * (1 - s.treasury_reserve_pct)
    if c.total_committed_quote + p.notional > investable:
        return f"dépasse le capital investissable {investable} (plafond et réserve de trésorerie)"
    return None


def _r05(p: OrderProposal, c: RiskContext) -> str | None:
    need = p.notional + p.est_fee
    if need > c.balance_quote:
        return f"solde réel {c.balance_quote} < {need}"
    return None


def _r06(p: OrderProposal, c: RiskContext) -> str | None:
    if p.notional < c.min_notional:
        return f"valeur {p.notional} sous le minimum {c.min_notional}"
    if p.notional > c.max_order_notional:
        return f"valeur {p.notional} au-dessus du maximum par ordre {c.max_order_notional}"
    return None


def _r07(p: OrderProposal, c: RiskContext) -> str | None:
    if p.limit_price is None:
        return None
    deviation = abs(p.limit_price - c.last_price) / c.last_price
    if deviation > c.max_price_deviation_pct:
        return f"prix limite à {deviation:.2%} du dernier prix (max {c.max_price_deviation_pct:.2%})"
    return None


def _r08(p: OrderProposal, c: RiskContext) -> str | None:
    if not c.pair_allowed:
        return f"paire {p.symbol} non autorisée par l'UniverseAgent"
    if not c.pair_liquid:
        return f"paire {p.symbol} marquée illiquide"
    return None


def _r09(p: OrderProposal, c: RiskContext) -> str | None:
    cap = c.capital_quote * c.settings.risk.max_exposure_per_asset_pct
    if c.asset_exposure_quote + p.notional > cap:
        return f"exposition {p.base_asset} dépasserait {cap}"
    return None


def _r10(p: OrderProposal, c: RiskContext) -> str | None:
    cap = c.capital_quote * c.settings.risk.max_crypto_exposure_pct
    if c.total_crypto_exposure_quote + p.notional > cap:
        return f"exposition totale aux cryptos dépasserait {cap}"
    return None


def _r11(p: OrderProposal, c: RiskContext) -> str | None:
    if p.is_target_exposure and not p.has_stop:
        return "position à exposition cible sans stop de protection (EX-SEC-14)"
    return None


def _r12(p: OrderProposal, c: RiskContext) -> str | None:
    if c.open_orders_agent >= c.max_open_orders_agent:
        return "limite d'ordres ouverts de l'agent atteinte"
    if c.open_orders_symbol >= c.max_open_orders_symbol:
        return f"limite d'ordres ouverts sur {p.symbol} atteinte"
    return None


def _r13(p: OrderProposal, c: RiskContext) -> str | None:
    if c.drawdown_pct >= c.drawdown_freeze_pct:
        return f"drawdown {c.drawdown_pct:.2%} : aucune nouvelle position (§12.3)"
    if c.daily_loss_pct >= c.daily_loss_pause_pct:
        return f"perte journalière {c.daily_loss_pct:.2%} : pause des stratèges (§12.3)"
    return None


def _r14(p: OrderProposal, c: RiskContext) -> str | None:
    if c.consecutive_errors >= c.max_consecutive_errors:
        return f"disjoncteur: {c.consecutive_errors} erreurs d'exécution consécutives"
    return None


def _r15(p: OrderProposal, c: RiskContext) -> str | None:
    if c.orders_today >= c.max_orders_per_day:
        return "plafond quotidien d'ordres atteint"
    return None


def _r16(p: OrderProposal, c: RiskContext) -> str | None:
    if p.instrument != "spot" and not c.settings.instruments.get(p.instrument, False):
        return f"instrument {p.instrument} désactivé (EX-PER-01)"
    if p.leverage > c.settings.risk.max_leverage:
        return f"levier {p.leverage} > max_leverage {c.settings.risk.max_leverage} (EX-SEC-20)"
    return None


_RULES: tuple[tuple[str, Check], ...] = (
    ("R-01", _r01), ("R-02", _r02), ("R-03", _r03), ("R-04", _r04), ("R-05", _r05),
    ("R-06", _r06), ("R-07", _r07), ("R-08", _r08), ("R-09", _r09), ("R-10", _r10),
    ("R-11", _r11), ("R-12", _r12), ("R-13", _r13), ("R-14", _r14), ("R-15", _r15),
    ("R-16", _r16),
)

# Une réduction de risque peut être automatique (C-08) : seules ces règles la concernent.
_REDUCTION_RULES = frozenset({"R-01", "R-02", "R-07"})


def review(proposal: OrderProposal, ctx: RiskContext) -> RiskDecision:
    for rule_id, check in _RULES:
        if proposal.reduces_risk and rule_id not in _REDUCTION_RULES:
            continue
        reason = check(proposal, ctx)
        if reason is not None:
            return RiskDecision(False, rule_id, reason)
    return RiskDecision(
        True, None, "toutes les règles passées", ApprovedOrder(proposal, _SEAL)
    )
