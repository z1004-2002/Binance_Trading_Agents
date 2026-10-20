"""ExecutionAgent (cœur) : seul écrivain sur l'exchange (A-4, EX-EX-01).

N'accepte que des `ApprovedOrder` (EX-SEC-12). Jamais de renvoi à l'aveugle (EX-EX-06).
Suit les ordres limites en attente, réserve le budget à l'envoi et le règle à l'exécution.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orchestre.exchange.base import Exchange, IpBannedError, OrderRequest, OrderResult
from orchestre.exchange.filters import FilterError, Side, round_price, round_qty, validate_order
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.engine import ApprovedOrder


@dataclass(frozen=True)
class ExecutionReport:
    status: str  # "FILLED" | "OPEN" | "CANCELED" | "DRY_RUN" | "FAILURE"
    reason: str
    result: OrderResult | None = None


@dataclass
class _Tracked:
    agent_id: str
    side: Side
    committed: Decimal
    protective: bool = False


class ExecutionService:
    def __init__(self, exchange: Exchange, ledger: Ledger, dry_run: bool = True) -> None:
        self._exchange = exchange
        self._ledger = ledger
        self._dry_run = dry_run
        self._tracked: dict[tuple[str, str], _Tracked] = {}
        self.consecutive_errors = 0
        self.orders_sent = 0

    # --- envoi ---
    def execute(self, approved: ApprovedOrder, client_order_id: str) -> ExecutionReport:
        if not isinstance(approved, ApprovedOrder):
            raise TypeError("l'exécuteur n'accepte que des ordres approuvés par le RiskAgent")
        p = approved.proposal

        existing = self._exchange.get_order(p.symbol, client_order_id)
        if existing is not None:
            return self._settle(approved, existing, "ordre déjà connu de la plateforme")

        filters = self._exchange.get_filters(p.symbol)
        qty = round_qty(p.quantity, filters)
        # un stop de vente : limite arrondie vers le bas (plus facile à exécuter), déclenchement au tick inférieur
        side_for_price = Side.BUY if p.stop_price is not None else p.side
        price = round_price(p.limit_price or p.price, side_for_price, filters)
        stop_price = round_price(p.stop_price, Side.BUY, filters) if p.stop_price is not None else None
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
            price=price if p.limit_price is not None else None,
            client_order_id=client_order_id,
            stop_price=stop_price,
        )
        self.orders_sent += 1
        try:
            result = self._exchange.place_order(request)
        except IpBannedError:
            raise
        except Exception as exc:
            known = self._exchange.get_order(p.symbol, client_order_id)
            if known is None:
                self.consecutive_errors += 1
                return ExecutionReport("FAILURE", f"échec d'envoi: {exc!r}")
            return self._settle(approved, known, f"statut récupéré après {exc!r}")
        return self._settle(approved, result, "ordre envoyé")

    def filters(self, symbol: str):
        return self._exchange.get_filters(symbol)

    # --- persistance (A-7) ---
    def export_tracked(self) -> list[dict]:
        return [
            {
                "symbol": s, "client_order_id": cid, "agent_id": t.agent_id,
                "side": t.side.value, "committed": str(t.committed), "protective": t.protective,
            }
            for (s, cid), t in sorted(self._tracked.items())
        ]

    def import_tracked(self, rows: list[dict]) -> None:
        self._tracked = {
            (r["symbol"], r["client_order_id"]): _Tracked(
                r["agent_id"], Side(r["side"]), Decimal(r["committed"]), bool(r.get("protective", False))
            )
            for r in rows
        }

    # --- suivi des ordres en attente ---
    def open_order_count(self, agent_id: str | None = None, symbol: str | None = None) -> int:
        return sum(
            1 for (s, _), t in self._tracked.items()
            if (agent_id is None or t.agent_id == agent_id) and (symbol is None or s == symbol)
        )

    def open_order_ids(self, symbol: str) -> list[str]:
        return sorted(cid for (s, cid) in self._tracked if s == symbol)

    def sync(self, symbol: str, agent_id: str | None = None) -> list[ExecutionReport]:
        """Règle les ordres suivis exécutés ou annulés depuis le dernier appel (d'un agent, ou de tous)."""
        reports = []
        for cid in self.open_order_ids(symbol):
            if agent_id is not None and self._tracked[(symbol, cid)].agent_id != agent_id:
                continue
            result = self._exchange.get_order(symbol, cid)
            if result is None or result.status == "NEW":
                continue
            reports.append(self._close_tracked(symbol, cid, result))
        return [r for r in reports if r.status == "FILLED"]

    def cancel(self, symbol: str, client_order_id: str) -> ExecutionReport:
        result = self._exchange.cancel_order(symbol, client_order_id)
        if (symbol, client_order_id) in self._tracked:
            return self._close_tracked(symbol, client_order_id, result)
        return ExecutionReport(result.status, "annulation d'un ordre non suivi", result)

    def cancel_all_open(
        self, symbol: str, agent_id: str | None = None, keep_protective: bool = True
    ) -> list[ExecutionReport]:
        """Zéro ordre orphelin à l'arrêt (EX-ST-06). Les stops de protection restent en place par défaut :
        ils doivent protéger les positions même si le programme s'arrête (EX-SEC-14). Avec `agent_id`, seuls les
        ordres suivis de cet agent sont annulés."""
        ids = set(self.open_order_ids(symbol))
        if agent_id is not None:
            ids = {c for c in ids if self._tracked[(symbol, c)].agent_id == agent_id}
        else:
            ids |= {o.client_order_id for o in self._exchange.get_open_orders(symbol)}
        if keep_protective:
            ids = {c for c in ids if not self._tracked.get((symbol, c), _Tracked("", Side.SELL, Decimal(0))).protective}
        return [self.cancel(symbol, cid) for cid in sorted(ids)]

    def reconcile(self, symbol: str) -> list[str]:
        """Écarts entre l'état local et la plateforme (EX-EX-10)."""
        on_exchange = {o.client_order_id for o in self._exchange.get_open_orders(symbol)}
        local = set(self.open_order_ids(symbol))
        gaps = [f"ordre orphelin sur la plateforme: {cid}" for cid in sorted(on_exchange - local)]
        for cid in sorted(local - on_exchange):
            known = self._exchange.get_order(symbol, cid)
            status = known.status if known else "INCONNU"
            gaps.append(f"ordre suivi absent de la plateforme: {cid} (statut {status})")
        return gaps

    # --- interne ---
    def _settle(self, approved: ApprovedOrder, result: OrderResult, note: str) -> ExecutionReport:
        p = approved.proposal
        key = (result.symbol, result.client_order_id)
        if result.status == "NEW":
            if key not in self._tracked:
                committed = Decimal("0")
                if result.side is Side.BUY:
                    committed = result.orig_qty * result.avg_price + p.est_fee
                    self._ledger.commit(p.agent_id, committed)
                self._tracked[key] = _Tracked(p.agent_id, result.side, committed, p.stop_price is not None)
            return ExecutionReport("OPEN", f"{note}: ordre en attente", result)
        if result.status != "FILLED":
            self.consecutive_errors += 1
            return ExecutionReport("FAILURE", f"{note}: statut {result.status}", result)
        self.consecutive_errors = 0
        if key in self._tracked:
            return self._close_tracked(result.symbol, result.client_order_id, result)
        self._update_ledger(p.agent_id, result)
        return ExecutionReport("FILLED", note, result)

    def _close_tracked(self, symbol: str, cid: str, result: OrderResult) -> ExecutionReport:
        tracked = self._tracked.pop((symbol, cid))
        agent = tracked.agent_id
        if result.status == "FILLED":
            self.consecutive_errors = 0
            value = result.filled_qty * result.avg_price
            if tracked.side is Side.BUY:
                diff = tracked.committed - (value + result.fee_quote)
                if diff > 0:
                    self._ledger.release(agent, diff)
                elif diff < 0:
                    self._ledger.commit(agent, -diff)
            else:
                self._release_up_to(agent, value)
            return ExecutionReport("FILLED", "ordre en attente exécuté", result)
        if tracked.committed > 0:
            self._ledger.release(agent, tracked.committed)
        return ExecutionReport(result.status, "ordre en attente clos", result)

    def _update_ledger(self, agent: str, r: OrderResult) -> None:
        value = r.filled_qty * r.avg_price
        if r.side is Side.BUY:
            self._ledger.commit(agent, value + r.fee_quote)
        else:
            self._release_up_to(agent, value)

    def _release_up_to(self, agent: str, value: Decimal) -> None:
        released = min(value, self._ledger.account(agent).committed)
        if released > 0:
            self._ledger.release(agent, released)
