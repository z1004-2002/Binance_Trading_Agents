"""Exchange « paper » (EX-BT-07, niveau L0) : données de marché RÉELLES en lecture seule, ordres et soldes SIMULÉS.

Aucun ordre n'atteint jamais le marché réel : toute l'écriture est locale (FakeExchange). Les ordres en attente et les
stops sont évalués aux prix relevés à chaque interrogation (donc à chaque tick), pas intra-bougie : un trait de mèche
entre deux ticks n'est pas vu. L'état est exportable pour survivre à un redémarrage.
"""

from __future__ import annotations

from decimal import Decimal

from orchestre.exchange.base import Candle, Exchange, OrderBook, OrderRequest, OrderResult
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import Side, SymbolFilters


class PaperExchange(FakeExchange):
    def __init__(
        self,
        market: Exchange,
        symbols: list[str],
        initial_balances: dict[str, Decimal],
        fee_rate: Decimal = Decimal("0.001"),
        slippage: Decimal = Decimal("0.0005"),
        quote: str = "USDT",
    ) -> None:
        super().__init__(
            {s: market.get_filters(s) for s in symbols}, {s: market.get_price(s) for s in symbols},
            initial_balances, fee_rate, quote,
        )
        self._market, self._slippage = market, slippage

    def get_price(self, symbol: str) -> Decimal:
        price = self._market.get_price(symbol)
        self.set_price(symbol, price)  # déclenche les ordres en attente et les stops
        return price

    def get_candles(self, symbol: str, interval: str, limit: int, end_time=None) -> list[Candle]:
        return self._market.get_candles(symbol, interval, limit, end_time)

    def get_order_book(self, symbol: str, depth: int = 100) -> OrderBook:
        return self._market.get_order_book(symbol, depth)

    def _place_market(self, request: OrderRequest, price: Decimal) -> OrderResult:
        factor = 1 + self._slippage if request.side is Side.BUY else 1 - self._slippage
        return super()._place_market(request, price * factor)
