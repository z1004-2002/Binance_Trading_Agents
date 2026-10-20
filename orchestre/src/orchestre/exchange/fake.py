"""FakeExchange : exchange scripté pour les tests, sans réseau (EX-SEC-18, EX-QA-04)."""

from __future__ import annotations

from decimal import Decimal

from orchestre.exchange.base import Exchange, OrderRequest, OrderResult
from orchestre.exchange.filters import FilterError, Side, SymbolFilters, validate_order


class FakeExchange(Exchange):
    def __init__(
        self,
        filters: dict[str, SymbolFilters],
        prices: dict[str, Decimal],
        balances: dict[str, Decimal],
        fee_rate: Decimal = Decimal("0.001"),
        quote: str = "USDT",
    ) -> None:
        self._filters = dict(filters)
        self._prices = dict(prices)
        self._balances = dict(balances)
        self._fee_rate = fee_rate
        self._quote = quote
        self._orders: dict[tuple[str, str], OrderResult] = {}

    def set_price(self, symbol: str, price: Decimal) -> None:
        self._prices[symbol] = price

    def get_filters(self, symbol: str) -> SymbolFilters:
        return self._filters[symbol]

    def get_price(self, symbol: str) -> Decimal:
        return self._prices[symbol]

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def get_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        return self._orders.get((symbol, client_order_id))

    def place_order(self, request: OrderRequest) -> OrderResult:
        key = (request.symbol, request.client_order_id)
        if key in self._orders:
            return self._orders[key]  # idempotence (EX-ST-02)

        filters = self._filters[request.symbol]
        price = request.price or self._prices[request.symbol]
        try:
            validate_order(request.quantity, price, filters)
        except FilterError:
            return self._store(request, Decimal("0"), price, Decimal("0"), "REJECTED")

        base = request.symbol.removesuffix(self._quote)
        cost = request.quantity * price
        fee = cost * self._fee_rate
        if request.side is Side.BUY:
            if self._balances.get(self._quote, Decimal("0")) < cost + fee:
                return self._store(request, Decimal("0"), price, Decimal("0"), "REJECTED")
            self._balances[self._quote] -= cost + fee
            self._balances[base] = self._balances.get(base, Decimal("0")) + request.quantity
        else:
            if self._balances.get(base, Decimal("0")) < request.quantity:
                return self._store(request, Decimal("0"), price, Decimal("0"), "REJECTED")
            self._balances[base] -= request.quantity
            self._balances[self._quote] = (
                self._balances.get(self._quote, Decimal("0")) + cost - fee
            )
        return self._store(request, request.quantity, price, fee, "FILLED")

    def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult:
        existing = self._orders.get((symbol, client_order_id))
        if existing is None:
            raise KeyError(f"ordre inconnu: {client_order_id}")
        return existing

    def _store(
        self, req: OrderRequest, qty: Decimal, price: Decimal, fee: Decimal, status: str
    ) -> OrderResult:
        result = OrderResult(
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            filled_qty=qty,
            avg_price=price,
            fee_quote=fee,
            status=status,
        )
        self._orders[(req.symbol, req.client_order_id)] = result
        return result
