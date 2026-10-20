"""S-9a Arbitrage triangulaire (§11.10) : OBSERVATION seulement, jamais d'ordre (EX-ST-10).

USDT -> BTC -> ETH -> USDT (et le sens inverse) : trois transactions à 0,1 % coûtent environ 0,3 % ; l'écart de prix doit dépasser ce
coût ET le glissement, ce qui reste rare et vite exploité par des acteurs plus rapides. L'intérêt est pédagogique : mesurer ces écarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from orchestre.exchange.base import IpBannedError, OrderBook


@dataclass(frozen=True)
class Triangle:
    a: str       # ex. BTC
    b: str       # ex. ETH
    quote: str   # ex. USDT

    @property
    def symbols(self) -> tuple[str, str, str]:
        return f"{self.a}{self.quote}", f"{self.b}{self.quote}", f"{self.b}{self.a}"   # BTCUSDT, ETHUSDT, ETHBTC


@dataclass(frozen=True)
class TriangleResult:
    best_edge: Decimal | None     # rendement net (après frais et glissement) du meilleur sens, None si profondeur insuffisante
    direction: str
    edges: dict[str, Decimal | None]

    def opportunity(self, threshold: Decimal) -> bool:
        return self.best_edge is not None and self.best_edge > threshold


def walk_buy(asks: list[tuple[Decimal, Decimal]], quote_amount: Decimal) -> Decimal | None:
    """Quantité de base obtenue en dépensant `quote_amount` contre les offres de vente ; None si le carnet est trop mince."""
    remaining, bought = quote_amount, Decimal("0")
    for price, qty in asks:
        take = min(remaining, price * qty)
        bought += take / price
        remaining -= take
        if remaining <= 0:
            return bought
    return None


def walk_sell(bids: list[tuple[Decimal, Decimal]], base_qty: Decimal) -> Decimal | None:
    """Montant de cotation obtenu en vendant `base_qty` contre les offres d'achat ; None si le carnet est trop mince."""
    remaining, received = base_qty, Decimal("0")
    for price, qty in bids:
        take = min(remaining, qty)
        received += take * price
        remaining -= take
        if remaining <= 0:
            return received
    return None


def evaluate_triangle(tri: Triangle, books: dict[str, OrderBook], fee_rate: Decimal, notional: Decimal) -> TriangleResult:
    s_a, s_b, s_ba = tri.symbols
    keep = 1 - fee_rate
    edges: dict[str, Decimal | None] = {}

    # USDT -> A -> B -> USDT
    a = walk_buy(books[s_a].asks, notional)
    b = walk_buy(books[s_ba].asks, a * keep) if a is not None else None
    end = walk_sell(books[s_b].bids, b * keep) if b is not None else None
    edges["USDT>BTC>ETH>USDT"] = None if end is None else end * keep / notional - 1

    # USDT -> B -> A -> USDT
    b = walk_buy(books[s_b].asks, notional)
    a = walk_sell(books[s_ba].bids, b * keep) if b is not None else None
    end = walk_sell(books[s_a].bids, a * keep) if a is not None else None
    edges["USDT>ETH>BTC>USDT"] = None if end is None else end * keep / notional - 1

    valid = {k: v for k, v in edges.items() if v is not None}
    if not valid:
        return TriangleResult(None, "", edges)
    best = max(valid, key=valid.__getitem__)
    return TriangleResult(valid[best], best, edges)


class TriangleObserver:
    """Agent d'observation : mesure périodiquement l'écart net des deux sens du triangle. N'envoie JAMAIS d'ordre."""

    def __init__(self, tri: Triangle, exchange, fee_rate: Decimal, notional: Decimal, threshold: Decimal = Decimal("0.0005"),
                 scan_every: timedelta = timedelta(minutes=1), depth: int = 20) -> None:
        self.tri, self._exchange, self._fee, self._notional = tri, exchange, fee_rate, notional
        self._threshold, self._every, self._depth = threshold, scan_every, depth
        self.stats: dict = {"scans": 0, "errors": 0, "opportunities": 0, "best_edge": None, "last_edge": None, "last_scan": None}
        self._next: datetime | None = None
        self._alerts: list[str] = []
        self.rejections: list = []
        self.positioned = False

    def step(self, now: datetime, prices=None) -> None:
        if self._next is not None and now < self._next:
            return
        self._next = now + self._every
        try:
            books = {s: self._exchange.get_order_book(s, self._depth) for s in self.tri.symbols}
        except IpBannedError:
            raise
        except Exception:
            self.stats["errors"] += 1
            return
        result = evaluate_triangle(self.tri, books, self._fee, self._notional)
        self.stats["scans"] += 1
        self.stats["last_scan"] = now.isoformat()
        if result.best_edge is not None:
            edge = float(result.best_edge)
            self.stats["last_edge"] = edge
            self.stats["best_edge"] = edge if self.stats["best_edge"] is None else max(self.stats["best_edge"], edge)
        if result.opportunity(self._threshold):
            self.stats["opportunities"] += 1
            self._alerts.append(f"info : opportunité {result.direction} : {result.best_edge:.3%} net de frais (simulation, aucun ordre)")

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts, []
        return alerts

    def export_state(self) -> dict:
        return {"stats": dict(self.stats)}

    def import_state(self, state: dict) -> None:
        self.stats = dict(state["stats"])
