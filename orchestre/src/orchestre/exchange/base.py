"""Interface d'exchange abstraite (A-3). Seul l'ExecutionAgent appelle les méthodes d'écriture (A-4)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from orchestre.exchange.filters import Side, SymbolFilters


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

    # --- écriture (ExecutionAgent uniquement) ---
    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult: ...
