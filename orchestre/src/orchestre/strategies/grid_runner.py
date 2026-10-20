"""Exécution d'un Grid : relie niveaux (logique pure), RiskAgent et ExecutionService (§11.3).

Toute intention d'ordre passe par le RiskAgent avant l'exécuteur (EX-SEC-12).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Callable

from orchestre.config import Env
from orchestre.exchange.base import OrderResult
from orchestre.exchange.filters import Side
from orchestre.execution.service import ExecutionReport, ExecutionService
from orchestre.risk.engine import RiskDecision, review
from orchestre.risk.models import OrderProposal, RiskContext
from orchestre.strategies.grid import (
    GridConfig,
    GridFill,
    GridOrder,
    cycle_profit,
    initial_orders,
    on_fill,
    range_status,
)

ContextFactory = Callable[[OrderProposal], RiskContext]


class GridRunner:
    def __init__(
        self,
        cfg: GridConfig,
        service: ExecutionService,
        context_for: ContextFactory,
        base_asset: str = "BTC",
        env: Env = Env.TESTNET,
    ) -> None:
        self._cfg = cfg
        self._svc = service
        self._context_for = context_for
        self._base = base_asset
        self._env = env
        self._live: dict[str, GridOrder] = {}
        self._open_buys: dict[int, tuple[Decimal, Decimal]] = {}
        self.cycles_completed = 0
        self.realized_profit = Decimal("0")
        self.rejections: list[RiskDecision] = []
        self.halted = False

    def start(self, price: Decimal) -> None:
        for order in initial_orders(self._cfg, price):
            self._submit(order)

    def step(self, price: Decimal) -> None:
        """À appeler à chaque tick : sortie de fourchette puis règlement des exécutions."""
        if self.halted:
            return
        if range_status(self._cfg, price) == "BELOW_STOP":
            self.halted = True
            self._svc.cancel_all_open(self._cfg.symbol)
            self._live.clear()
            return
        for report in self._svc.sync(self._cfg.symbol):
            self._on_filled(report.result)

    def _submit(self, order: GridOrder) -> ExecutionReport | None:
        proposal = OrderProposal(
            agent_id=self._cfg.agent_id,
            symbol=self._cfg.symbol,
            base_asset=self._base,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            limit_price=order.price,
            env=self._env,
            est_fee=order.quantity * order.price * self._cfg.fee_rate,
            reduces_risk=order.side is Side.SELL,
        )
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return None
        report = self._svc.execute(decision.order, order.client_order_id)
        if report.status in ("OPEN", "FILLED"):
            self._live[order.client_order_id] = order
        return report

    def _on_filled(self, result: OrderResult) -> None:
        order = self._live.pop(result.client_order_id, None)
        if order is None:
            return
        cycle = int(result.client_order_id.rsplit("-", 1)[1])
        if order.side is Side.BUY:
            self._open_buys[order.level + 1] = (result.avg_price, result.filled_qty)
        else:
            bought = self._open_buys.pop(order.level, None)
            if bought is not None:
                buy_price, _ = bought
                self.realized_profit += cycle_profit(
                    buy_price, result.avg_price, result.filled_qty, self._cfg.fee_rate
                )
                self.cycles_completed += 1
        nxt = on_fill(
            self._cfg, GridFill(order.level, order.side, result.filled_qty, result.avg_price, cycle)
        )
        if nxt is not None:
            self._submit(nxt)
