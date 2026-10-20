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
        exit_policy: str = "pause",
    ) -> None:
        if exit_policy not in ("pause", "wait", "close"):
            raise ValueError("exit_policy : pause, wait ou close")
        self._exit_policy = exit_policy
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

    def export_state(self) -> dict:
        return {
            "live": [
                {
                    "level": o.level, "side": o.side.value, "price": str(o.price),
                    "quantity": str(o.quantity), "client_order_id": o.client_order_id,
                    "reason": o.reason,
                }
                for o in self._live.values()
            ],
            "open_buys": {str(k): [str(p), str(q)] for k, (p, q) in self._open_buys.items()},
            "cycles": self.cycles_completed,
            "profit": str(self.realized_profit),
            "halted": self.halted,
        }

    def import_state(self, state: dict) -> None:
        self._live = {
            o["client_order_id"]: GridOrder(
                o["level"], Side(o["side"]), Decimal(o["price"]), Decimal(o["quantity"]),
                o["client_order_id"], o["reason"],
            )
            for o in state["live"]
        }
        self._open_buys = {
            int(k): (Decimal(p), Decimal(q)) for k, (p, q) in state["open_buys"].items()
        }
        self.cycles_completed = state["cycles"]
        self.realized_profit = Decimal(state["profit"])
        self.halted = state["halted"]

    def start(self, price: Decimal) -> None:
        for order in initial_orders(self._cfg, price):
            self._submit(order)

    def step(self, price: Decimal) -> None:
        """À appeler à chaque tick : sortie de fourchette puis règlement des exécutions."""
        if self.halted:
            return
        if range_status(self._cfg, price) == "BELOW_STOP" and self._exit_policy != "wait":
            self.halted = True
            for report in self._svc.sync(self._cfg.symbol, self._cfg.agent_id):  # enregistrer d'abord ce qui a été exécuté
                self._on_filled(report.result, reinvest=False)
            self._svc.cancel_all_open(self._cfg.symbol, self._cfg.agent_id)
            self._live.clear()
            if self._exit_policy == "close":
                self._liquidate(price)
            return
        for report in self._svc.sync(self._cfg.symbol, self._cfg.agent_id):
            self._on_filled(report.result)

    def _liquidate(self, price: Decimal) -> None:
        """Vend au marché l'inventaire acheté par la grille (réduction de risque, C-08)."""
        quantity = sum((q for _, q in self._open_buys.values()), Decimal("0"))
        self._open_buys.clear()
        if quantity <= 0:
            return
        proposal = OrderProposal(
            agent_id=self._cfg.agent_id, symbol=self._cfg.symbol, base_asset=self._base,
            side=Side.SELL, quantity=quantity, price=price, limit_price=None, env=self._env,
            est_fee=quantity * price * self._cfg.fee_rate, reduces_risk=True,
        )
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return
        self._svc.execute(decision.order, f"{self._cfg.agent_id}-CLOSE")

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

    def _on_filled(self, result: OrderResult, reinvest: bool = True) -> None:
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
        if not reinvest:
            return
        nxt = on_fill(
            self._cfg, GridFill(order.level, order.side, result.filled_qty, result.avg_price, cycle)
        )
        if nxt is not None:
            self._submit(nxt)
