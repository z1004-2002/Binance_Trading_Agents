"""FakeExchange : exchange scripté pour les tests, sans réseau (EX-SEC-18, EX-QA-04).

Un ordre avec prix est un `LIMIT_MAKER` : rejeté s'il s'exécuterait immédiatement (EX-ST-04),
sinon il reste en attente et se remplit quand `set_price` traverse son niveau.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.exchange.base import Candle, Exchange, OrderBook, OrderRequest, OrderResult
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
        self._resting: dict[tuple[str, str], OrderRequest] = {}
        self._stops: dict[tuple[str, str], OrderRequest] = {}
        self._candles: dict[tuple[str, str], list[Candle]] = {}
        self._books: dict[str, OrderBook] = {}

    # --- persistance de l'état simulé (mode paper) ---
    def export_state(self) -> dict:
        def order(r: OrderResult) -> dict:
            return {"cid": r.client_order_id, "symbol": r.symbol, "side": r.side.value, "filled": str(r.filled_qty),
                    "price": str(r.avg_price), "fee": str(r.fee_quote), "status": r.status, "orig": str(r.orig_qty)}

        def request(q: OrderRequest) -> dict:
            return {"symbol": q.symbol, "side": q.side.value, "qty": str(q.quantity), "cid": q.client_order_id,
                    "price": None if q.price is None else str(q.price),
                    "stop": None if q.stop_price is None else str(q.stop_price)}

        return {
            "balances": {k: str(v) for k, v in self._balances.items()},
            "orders": [order(r) for r in self._orders.values()],
            "resting": [request(q) for q in self._resting.values()],
            "stops": [request(q) for q in self._stops.values()],
        }

    def import_state(self, state: dict) -> None:
        def dec(v):
            return None if v is None else Decimal(v)

        def request(d: dict) -> OrderRequest:
            return OrderRequest(d["symbol"], Side(d["side"]), Decimal(d["qty"]), dec(d["price"]), d["cid"], dec(d["stop"]))

        self._balances = {k: Decimal(v) for k, v in state["balances"].items()}
        self._orders = {
            (d["symbol"], d["cid"]): OrderResult(
                d["cid"], d["symbol"], Side(d["side"]), Decimal(d["filled"]), Decimal(d["price"]),
                Decimal(d["fee"]), d["status"], Decimal(d["orig"]),
            )
            for d in state["orders"]
        }
        self._resting = {(d["symbol"], d["cid"]): request(d) for d in state["resting"]}
        self._stops = {(d["symbol"], d["cid"]): request(d) for d in state["stops"]}

    # --- scénario ---
    def set_price(self, symbol: str, price: Decimal) -> list[OrderResult]:
        """Met à jour le prix et retourne les ordres en attente nouvellement exécutés."""
        self._prices[symbol] = price
        filled = []
        for key, req in list(self._stops.items()):
            if key[0] == symbol and price <= req.stop_price:
                del self._stops[key]
                if price >= req.price:      # déclenché et le marché est au-dessus de la limite : exécuté au marché
                    filled.append(self._fill_stop(req, price))
                else:                       # trou sous la limite : devient un ordre limite en attente (non protégé !)
                    self._resting[key] = OrderRequest(req.symbol, req.side, req.quantity, req.price, req.client_order_id)
        for key, req in list(self._resting.items()):
            if key[0] != symbol:
                continue
            crossed = price <= req.price if req.side is Side.BUY else price >= req.price
            if crossed:
                filled.append(self._fill_resting(key, req))
        return filled

    def set_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        self._candles[(symbol, interval)] = list(candles)

    def set_order_book(self, symbol: str, book: OrderBook) -> None:
        self._books[symbol] = book

    # --- lecture ---
    def get_candles(self, symbol: str, interval: str, limit: int, end_time=None) -> list[Candle]:
        candles = self._candles.get((symbol, interval), [])
        if end_time is not None:
            candles = [c for c in candles if c.open_time < end_time]
        return candles[-limit:]

    def get_order_book(self, symbol: str, depth: int = 100) -> OrderBook:
        if symbol in self._books:
            return self._books[symbol]
        mid = self._prices[symbol]  # carnet profond et serré par défaut
        step = mid * Decimal("0.0001")
        return OrderBook(
            bids=[(mid - step * (i + 1), Decimal("10")) for i in range(depth)],
            asks=[(mid + step * (i + 1), Decimal("10")) for i in range(depth)],
        )

    def get_filters(self, symbol: str) -> SymbolFilters:
        return self._filters[symbol]

    def get_price(self, symbol: str) -> Decimal:
        return self._prices[symbol]

    def get_balances(self) -> dict[str, Decimal]:
        return dict(self._balances)

    def get_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        return self._orders.get((symbol, client_order_id))

    def get_open_orders(self, symbol: str) -> list[OrderResult]:
        return [r for (s, _), r in self._orders.items() if s == symbol and r.status == "NEW"]

    # --- écriture ---
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

        if request.stop_price is not None:
            return self._place_stop(request)
        if request.price is not None:
            return self._place_resting(request)
        return self._place_market(request, price)

    def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult:
        key = (symbol, client_order_id)
        existing = self._orders.get(key)
        if existing is None:
            raise KeyError(f"ordre inconnu: {client_order_id}")
        stop = self._stops.pop(key, None)
        if stop is not None:
            self._balances[symbol.removesuffix(self._quote)] += stop.quantity
            return self._store(stop, Decimal("0"), stop.price, Decimal("0"), "CANCELED")
        req = self._resting.pop(key, None)
        if req is None:
            return existing
        base = symbol.removesuffix(self._quote)
        if req.side is Side.BUY:
            cost = req.quantity * req.price
            self._balances[self._quote] += cost + cost * self._fee_rate
        else:
            self._balances[base] += req.quantity
        return self._store(req, Decimal("0"), req.price, Decimal("0"), "CANCELED")

    # --- interne ---
    def _place_market(self, request: OrderRequest, price: Decimal) -> OrderResult:
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

    def _place_resting(self, request: OrderRequest) -> OrderResult:
        last = self._prices[request.symbol]
        base = request.symbol.removesuffix(self._quote)
        if request.side is Side.BUY:
            if request.price >= last:
                return self._store(request, Decimal("0"), request.price, Decimal("0"), "REJECTED")
            cost = request.quantity * request.price
            need = cost + cost * self._fee_rate
            if self._balances.get(self._quote, Decimal("0")) < need:
                return self._store(request, Decimal("0"), request.price, Decimal("0"), "REJECTED")
            self._balances[self._quote] -= need
        else:
            if request.price <= last:
                return self._store(request, Decimal("0"), request.price, Decimal("0"), "REJECTED")
            if self._balances.get(base, Decimal("0")) < request.quantity:
                return self._store(request, Decimal("0"), request.price, Decimal("0"), "REJECTED")
            self._balances[base] -= request.quantity
        self._resting[(request.symbol, request.client_order_id)] = request
        return self._store(request, Decimal("0"), request.price, Decimal("0"), "NEW")

    def _place_stop(self, request: OrderRequest) -> OrderResult:
        base = request.symbol.removesuffix(self._quote)
        rejected = (
            request.side is not Side.SELL
            or request.stop_price >= self._prices[request.symbol]      # se déclencherait immédiatement
            or self._balances.get(base, Decimal("0")) < request.quantity
        )
        if rejected:
            return self._store(request, Decimal("0"), request.price, Decimal("0"), "REJECTED")
        self._balances[base] -= request.quantity
        self._stops[(request.symbol, request.client_order_id)] = request
        return self._store(request, Decimal("0"), request.price, Decimal("0"), "NEW")

    def _fill_stop(self, req: OrderRequest, price: Decimal) -> OrderResult:
        cost = req.quantity * price
        fee = cost * self._fee_rate
        self._balances[self._quote] = self._balances.get(self._quote, Decimal("0")) + cost - fee
        return self._store(req, req.quantity, price, fee, "FILLED")

    def _fill_resting(self, key: tuple[str, str], req: OrderRequest) -> OrderResult:
        del self._resting[key]
        base = req.symbol.removesuffix(self._quote)
        cost = req.quantity * req.price
        fee = cost * self._fee_rate
        if req.side is Side.BUY:
            self._balances[base] = self._balances.get(base, Decimal("0")) + req.quantity
        else:
            self._balances[self._quote] = self._balances.get(self._quote, Decimal("0")) + cost - fee
        return self._store(req, req.quantity, req.price, fee, "FILLED")

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
            orig_qty=req.quantity,
        )
        self._orders[(req.symbol, req.client_order_id)] = result
        return result
