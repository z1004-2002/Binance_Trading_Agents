"""ExecutionAgent (cœur) : seul écrivain sur l'exchange (A-4, EX-EX-01).

N'accepte que des `ApprovedOrder` (EX-SEC-12). Jamais de renvoi à l'aveugle (EX-EX-06).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orchestre.exchange.base import Exchange, OrderRequest, OrderResult
from orchestre.exchange.filters import FilterError, Side, round_price, round_qty, validate_order
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import ApprovedOrder


@dataclass(frozen=True)
class ExecutionReport:
    status: str  # "FILLED" | "DRY_RUN" | "FAILURE"
    reason: str
    result: OrderResult | None = None


class ExecutionService:
    def __init__(self, exchange: Exchange, ledger: Ledger, dry_run: bool = True) -> None:
        self._exchange = exchange
        self._ledger = ledger
        self._dry_run = dry_run
        self.consecutive_errors = 0

    def execute(self, approved: ApprovedOrder, client_order_id: str) -> ExecutionReport:
        if not isinstance(approved, ApprovedOrder):
            raise TypeError("l'exécuteur n'accepte que des ordres approuvés par le RiskAgent")
        p = approved.proposal

        existing = self._exchange.get_order(p.symbol, client_order_id)
        if existing is not None:
            return self._settle(approved, existing, "ordre déjà connu de la plateforme")

        filters = self._exchange.get_filters(p.symbol)
        qty = round_qty(p.quantity, filters)
        price = round_price(p.limit_price or p.price, p.side, filters)
        try:
            validate_order(qty, price, filters)
        except FilterError as exc:
            return ExecutionReport("FAILURE", f"rejet local: {exc}")

        if self._dry_run:
            return ExecutionReport("DRY_RUN", f"dry-run: {p.side.value} {qty} {p.symbol} non envoyé")

        request = OrderRequest(
            symbol=p.symbol,
            side=p.side,
            quantity=qty,
            price=p.limit_price and price,
            client_order_id=client_order_id,
        )
        try:
            result = self._exchange.place_order(request)
        except Exception as exc:
            known = self._exchange.get_order(p.symbol, client_order_id)
            if known is None:
                self.consecutive_errors += 1
                return ExecutionReport("FAILURE", f"échec d'envoi: {exc!r}")
            return self._settle(approved, known, f"statut récupéré après {exc!r}")
        return self._settle(approved, result, "ordre envoyé")

    def _settle(self, approved: ApprovedOrder, result: OrderResult, note: str) -> ExecutionReport:
        if result.status != "FILLED":
            self.consecutive_errors += 1
            return ExecutionReport("FAILURE", f"{note}: statut {result.status}", result)
        self.consecutive_errors = 0
        self._update_ledger(approved, result)
        return ExecutionReport("FILLED", note, result)

    def _update_ledger(self, approved: ApprovedOrder, r: OrderResult) -> None:
        agent = approved.proposal.agent_id
        value = r.filled_qty * r.avg_price
        if r.side is Side.BUY:
            self._ledger.commit(agent, value + r.fee_quote)
        else:
            released = min(value, self._ledger.account(agent).committed)
            if released > 0:
                self._ledger.release(agent, released)
