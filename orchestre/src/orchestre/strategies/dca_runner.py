"""Exécution du DCA : relie la logique pure (dca.py), le RiskAgent et l'ExecutionService (§11.2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable

from orchestre.config import Env
from orchestre.exchange.filters import Side
from orchestre.execution.service import ExecutionReport, ExecutionService
from orchestre.risk.engine import RiskDecision, review
from orchestre.risk.models import OrderProposal, RiskContext
from orchestre.strategies.dca import DcaConfig, DcaState, decide, record


class DcaRunner:
    def __init__(
        self,
        cfg: DcaConfig,
        service: ExecutionService,
        context_for: Callable[[OrderProposal], RiskContext],
        base_asset: str = "BTC",
        env: Env = Env.TESTNET,
        fee_rate: Decimal = Decimal("0.001"),
    ) -> None:
        self._cfg = cfg
        self._svc = service
        self._context_for = context_for
        self._base = base_asset
        self._env = env
        self._fee_rate = fee_rate
        self.state = DcaState()
        self.rejections: list[RiskDecision] = []

    def export_state(self) -> dict:
        return {
            "last_slot": self.state.last_slot.isoformat() if self.state.last_slot else None,
            "total_spent": str(self.state.total_spent),
        }

    def import_state(self, state: dict) -> None:
        last = state["last_slot"]
        self.state = DcaState(datetime.fromisoformat(last) if last else None, Decimal(state["total_spent"]))

    def step(
        self, now: datetime, price: Decimal, fear_greed: int | None = None
    ) -> ExecutionReport | None:
        order = decide(self._cfg, self.state, now, price, fear_greed)
        if order is None:
            return None
        proposal = OrderProposal(
            agent_id=self._cfg.agent_id,
            symbol=self._cfg.symbol,
            base_asset=self._base,
            side=Side.BUY,
            quantity=order.quantity,
            price=price,
            limit_price=None,
            env=self._env,
            est_fee=order.amount_quote * self._fee_rate,
        )
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return None
        report = self._svc.execute(decision.order, order.client_order_id)
        if report.status in ("FILLED", "DRY_RUN"):
            self.state = record(self.state, order)
        return report
