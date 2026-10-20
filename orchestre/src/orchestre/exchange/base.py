"""Interface d'exchange abstraite (A-3). Seul l'ExecutionAgent appelle les méthodes d'écriture (A-4)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from orchestre.exchange.filters import Side, SymbolFilters


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool


@dataclass(frozen=True)
class OrderBook:
    bids: list[tuple[Decimal, Decimal]]  # (prix, quantité), du meilleur au pire
    asks: list[tuple[Decimal, Decimal]]


class IpBannedError(RuntimeError):
    """Adresse IP bannie par la plateforme (HTTP 418) : pause de toute l'équipe (EX-EX-07)."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal | None  # None = ordre au marché
    client_order_id: str


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    symbol: str
    side: Side
    filled_qty: Decimal
    avg_price: Decimal
    fee_quote: Decimal
    status: str  # "FILLED" | "NEW" | "CANCELED" | "REJECTED"
    orig_qty: Decimal = Decimal("0")


class Exchange(ABC):
    # --- lecture ---
    @abstractmethod
    def get_filters(self, symbol: str) -> SymbolFilters: ...

    @abstractmethod
    def get_price(self, symbol: str) -> Decimal: ...

    @abstractmethod
    def get_balances(self) -> dict[str, Decimal]: ...

    @abstractmethod
    def get_order(self, symbol: str, client_order_id: str) -> OrderResult | None: ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[OrderResult]: ...

    @abstractmethod
    def get_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]: ...

    @abstractmethod
    def get_order_book(self, symbol: str, depth: int = 100) -> OrderBook: ...

    # --- écriture (ExecutionAgent uniquement) ---
    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult: ...
