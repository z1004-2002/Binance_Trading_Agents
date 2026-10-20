"""Connecteur Binance Spot via ccxt, derrière l'interface Exchange (A-3, ENF-01).

Le client ccxt est injecté : les tests n'ouvrent jamais de réseau (EX-SEC-18).
Montants lus depuis les chaînes brutes de l'API (champ `info`), jamais depuis des flottants (A-5).
Limite connue : les quantités et prix passent à ccxt sous forme de flottants ; ccxt les formate à la précision du marché.
"""

from __future__ import annotations

from decimal import Decimal

import ccxt

from orchestre.config import ConfigError, Env
from orchestre.exchange.base import Exchange, OrderRequest, OrderResult
from orchestre.exchange.filters import Side, SymbolFilters
from orchestre.exchange.ratelimit import RateLimiter

_STATUS = {
    "NEW": "NEW",
    "PARTIALLY_FILLED": "NEW",
    "FILLED": "FILLED",
    "CANCELED": "CANCELED",
    "EXPIRED": "CANCELED",
    "EXPIRED_IN_MATCH": "CANCELED",
    "REJECTED": "REJECTED",
}
STP_MODE = "EXPIRE_TAKER"


def create_client(env: Env, api_key: str, api_secret: str, live_confirmed: bool = False) -> ccxt.binance:
    """Seul point de construction du client ccxt ; le réel exige une confirmation explicite (EX-SEC-01)."""
    if env is Env.LIVE and not live_confirmed:
        raise ConfigError("client live refusé sans confirmation explicite")
    client = ccxt.binance(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "recvWindow": 5000, "adjustForTimeDifference": True},
        }
    )
    if env is Env.TESTNET:
        client.set_sandbox_mode(True)
    elif env is Env.DEMO:
        client.enable_demo_trading(True)
    return client


class BinanceExchange(Exchange):
    def __init__(
        self,
        client,
        quote: str = "USDT",
        fee_rate: Decimal = Decimal("0.001"),
        limiter: RateLimiter | None = None,
    ) -> None:
        self._client = client
        self._quote = quote
        self._fee_rate = fee_rate
        self._markets_loaded = False
        self._call = (limiter or RateLimiter(headers=self._response_headers)).call

    def _response_headers(self) -> dict:
        return getattr(self._client, "last_response_headers", None) or {}

    def sync_time(self):
        """Synchronise l'horloge avec le serveur (EX-EX-08)."""
        return self._call(self._client.load_time_difference)

    # --- lecture ---
    def get_filters(self, symbol: str) -> SymbolFilters:
        self._ensure_markets()
        by_type = {f["filterType"]: f for f in self._client.market(self._pair(symbol))["info"]["filters"]}
        lot, price = by_type["LOT_SIZE"], by_type["PRICE_FILTER"]
        notional = by_type.get("NOTIONAL") or by_type["MIN_NOTIONAL"]
        return SymbolFilters(
            symbol=symbol,
            tick_size=Decimal(price["tickSize"]).normalize(),
            step_size=Decimal(lot["stepSize"]).normalize(),
            min_qty=Decimal(lot["minQty"]).normalize(),
            max_qty=Decimal(lot["maxQty"]).normalize(),
            min_notional=Decimal(notional["minNotional"]).normalize(),
        )

    def get_price(self, symbol: str) -> Decimal:
        return Decimal(self._call(self._client.fetch_ticker, self._pair(symbol))["info"]["lastPrice"])

    def get_balances(self) -> dict[str, Decimal]:
        balances = self._call(self._client.fetch_balance)["info"]["balances"]
        free = {b["asset"]: Decimal(b["free"]) for b in balances}
        return {asset: amount for asset, amount in free.items() if amount > 0}

    def fetch_key_restrictions(self) -> dict:
        """Permissions de la clé (`/sapi`, réel uniquement : absent du testnet, EX-SEC-05)."""
        return dict(self._call(self._client.sapiGetAccountApiRestrictions))

    def get_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        try:
            raw = self._call(self._client.fetch_order, None, self._pair(symbol), {"origClientOrderId": client_order_id})
        except ccxt.OrderNotFound:
            return None
        return self._to_result(raw, symbol)

    def get_open_orders(self, symbol: str) -> list[OrderResult]:
        return [self._to_result(o, symbol) for o in self._call(self._client.fetch_open_orders, self._pair(symbol))]

    # --- écriture (ExecutionAgent uniquement) ---
    def place_order(self, request: OrderRequest) -> OrderResult:
        params = {"newClientOrderId": request.client_order_id, "selfTradePreventionMode": STP_MODE}
        if request.price is None:
            type_, price = "market", None
        else:
            type_, price = "limit", float(request.price)
            params["postOnly"] = True
        raw = self._call(
            self._client.create_order,
            self._pair(request.symbol), type_, request.side.value.lower(), float(request.quantity), price, params,
        )
        return self._to_result(raw, request.symbol)

    def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult:
        try:
            raw = self._call(self._client.cancel_order, None, self._pair(symbol), {"origClientOrderId": client_order_id})
        except ccxt.OrderNotFound:
            known = self.get_order(symbol, client_order_id)
            if known is None:
                raise
            return known
        return self._to_result(raw, symbol, client_order_id)

    # --- interne ---
    def _pair(self, symbol: str) -> str:
        return f"{symbol.removesuffix(self._quote)}/{self._quote}"

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._call(self._client.load_markets)
            self._markets_loaded = True

    def _to_result(self, raw: dict, symbol: str, client_order_id: str | None = None) -> OrderResult:
        info = raw["info"]
        executed = Decimal(info["executedQty"])
        cumulative = Decimal(info["cummulativeQuoteQty"])
        avg_price = cumulative / executed if executed > 0 else Decimal(info["price"])
        return OrderResult(
            client_order_id=client_order_id or info.get("origClientOrderId") or info["clientOrderId"],
            symbol=symbol,
            side=Side(info["side"]),
            filled_qty=executed,
            avg_price=avg_price,
            fee_quote=self._fee_in_quote(info, symbol, cumulative, avg_price),
            status=_STATUS.get(info["status"], "REJECTED"),
            orig_qty=Decimal(info["origQty"]),
        )

    def _fee_in_quote(self, info: dict, symbol: str, cumulative: Decimal, avg_price: Decimal) -> Decimal:
        fills = info.get("fills")
        if not fills:
            return cumulative * self._fee_rate  # estimation : frais réels non fournis
        base, total = symbol.removesuffix(self._quote), Decimal("0")
        for fill in fills:
            commission, asset = Decimal(fill["commission"]), fill["commissionAsset"]
            if asset == self._quote:
                total += commission
            elif asset == base:
                total += commission * avg_price
            # Autre actif (ex. BNB) : non converti, voir question ouverte Q-7.
        return total
