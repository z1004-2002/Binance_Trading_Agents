"""S-9b Tenue de marché (§11.10) : SIMULATION UNIQUEMENT (EX-ST-10) — le builder la refuse hors du mode paper.

Poste un achat sous le prix milieu et, une fois du stock acquis, une vente au-dessus, pour capter l'écart. Pas de vente à découvert
(Spot) : sans stock, seul l'achat est coté. Risque principal : accumuler un stock d'un actif qui baisse (plafonné par `max_inventory_pct`).
Fidélité limitée : le mode paper évalue les exécutions aux prix relevés à chaque tick, pas intra-bougie.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.config import Env
from orchestre.exchange.filters import Side, round_price, round_qty
from orchestre.execution.service import ExecutionService
from orchestre.risk.engine import RiskDecision, review
from orchestre.risk.models import OrderProposal


class MarketMakerRunner:
    def __init__(
        self, symbol: str, base_asset: str, svc: ExecutionService, context_for, exchange, agent_id: str, budget_fn,
        env: Env = Env.TESTNET, fee_rate: Decimal = Decimal("0.001"), half_spread: Decimal = Decimal("0.003"),
        quote_size_pct: Decimal = Decimal("0.25"), max_inventory_pct: Decimal = Decimal("0.5"), requote_pct: Decimal = Decimal("0.002"),
    ) -> None:
        self.symbol, self.base_asset, self.agent_id = symbol, base_asset, agent_id
        self._svc, self._context_for, self._budget_fn, self._env, self._fee = svc, context_for, budget_fn, env, fee_rate
        self._half, self._size_pct, self._max_inv, self._requote = half_spread, quote_size_pct, max_inventory_pct, requote_pct
        self.qty = Decimal("0")
        self.net_spent = Decimal("0")
        self.orders: dict[str, tuple[str, Decimal]] = {}      # "BUY"/"SELL" -> (identifiant, prix)
        self.seq = 0
        self.suspended = False
        self.rejections: list[RiskDecision] = []
        self._alerts: list[str] = []

    @property
    def positioned(self) -> bool:
        return self.qty > 0

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts, []
        return alerts

    def export_state(self) -> dict:
        return {"qty": str(self.qty), "net_spent": str(self.net_spent), "seq": self.seq,
                "orders": {side: [cid, str(price)] for side, (cid, price) in self.orders.items()}}

    def import_state(self, state: dict) -> None:
        self.qty, self.net_spent, self.seq = Decimal(state["qty"]), Decimal(state["net_spent"]), state["seq"]
        self.orders = {side: (cid, Decimal(price)) for side, (cid, price) in state["orders"].items()}

    def equity(self, price: Decimal) -> Decimal:
        return self._budget_fn() - self.net_spent + self.qty * price

    # --- exécutions ---
    def _sync(self) -> None:
        for report in self._svc.sync(self.symbol, self.agent_id):
            r = report.result
            for side, (cid, _) in list(self.orders.items()):
                if cid != r.client_order_id:
                    continue
                value = r.filled_qty * r.avg_price
                if side == "BUY":
                    self.qty += r.filled_qty
                    self.net_spent += value + r.fee_quote
                else:
                    self.qty -= r.filled_qty
                    self.net_spent -= value - r.fee_quote
                del self.orders[side]
        open_ids = set(self._svc.open_order_ids(self.symbol))
        for side, (cid, _) in list(self.orders.items()):
            if cid not in open_ids:
                del self.orders[side]
        if 0 < self.qty < self._svc.filters(self.symbol).min_qty:
            self.qty = Decimal("0")

    def _cancel(self, side: str) -> None:
        if side not in self.orders:
            return
        cid, _ = self.orders.pop(side)
        report = self._svc.cancel(self.symbol, cid)
        if report.status == "FILLED" and report.result is not None:    # exécuté pendant l'annulation
            r, value = report.result, report.result.filled_qty * report.result.avg_price
            if side == "BUY":
                self.qty += r.filled_qty
                self.net_spent += value + r.fee_quote
            else:
                self.qty -= r.filled_qty
                self.net_spent -= value - r.fee_quote

    def _place(self, side: str, price: Decimal, qty: Decimal) -> None:
        self.seq += 1
        cid = f"{self.agent_id}-{self.symbol}-MM{side[0]}{self.seq}"
        proposal = OrderProposal(
            agent_id=self.agent_id, symbol=self.symbol, base_asset=self.base_asset, side=Side(side), quantity=qty, price=price,
            limit_price=price, env=self._env, est_fee=qty * price * self._fee, reduces_risk=side == "SELL",
        )
        decision = review(proposal, self._context_for(proposal))
        if not decision.approved:
            self.rejections.append(decision)
            return
        report = self._svc.execute(decision.order, cid)
        if report.status == "OPEN":
            self.orders[side] = (cid, price)
        elif report.status == "FAILURE":
            self._alerts.append(f"cotation {side} refusée par la plateforme : {report.reason}")

    def _manage(self, side: str, desired: Decimal | None, qty: Decimal) -> None:
        if desired is None or qty <= 0:
            self._cancel(side)
            return
        existing = self.orders.get(side)
        if existing is not None:
            if abs(desired - existing[1]) / desired <= self._requote:
                return
            self._cancel(side)
        self._place(side, desired, qty)

    # --- tick ---
    def step(self, now, price: Decimal) -> None:
        self._sync()
        if self.suspended:
            self._cancel("BUY")
            self._cancel("SELL")
            return
        budget, filters = self._budget_fn(), self._svc.filters(self.symbol)
        bid = round_price(price * (1 - self._half), Side.BUY, filters)
        ask = round_price(price * (1 + self._half), Side.SELL, filters)
        size_quote = budget * self._size_pct
        bid_qty = round_qty(size_quote / bid, filters) if bid > 0 else Decimal("0")
        ask_qty = round_qty(min(self.qty, size_quote / ask), filters)
        want_bid = self.qty * price < self._max_inv * budget and bid_qty * bid >= filters.min_notional
        want_ask = ask_qty >= filters.min_qty and ask_qty * ask >= filters.min_notional
        self._manage("BUY", bid if want_bid else None, bid_qty)
        self._manage("SELL", ask if want_ask else None, ask_qty)
