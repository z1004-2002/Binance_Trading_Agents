"""LiquidityAgent (cœur) : écart, glissement estimé, paire « illiquide » au-delà du seuil (EX-AN-12)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orchestre.exchange.base import OrderBook


@dataclass(frozen=True)
class LiquidityReading:
    spread_pct: Decimal | None
    slippage_pct: Decimal | None
    liquid: bool
    reason: str


def assess_liquidity(
    book: OrderBook,
    order_notional: Decimal,
    max_spread: Decimal = Decimal("0.003"),
    max_slippage: Decimal = Decimal("0.003"),
) -> LiquidityReading:
    if not book.bids or not book.asks:
        return LiquidityReading(None, None, False, "carnet vide")
    best_bid, best_ask = book.bids[0][0], book.asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread = (best_ask - best_bid) / mid
    if spread > max_spread:
        return LiquidityReading(spread, None, False, f"écart achat/vente {spread:.3%} > {max_spread:.3%}")

    remaining, spent, quantity = order_notional, Decimal("0"), Decimal("0")
    for price, qty in book.asks:
        take = min(remaining, price * qty)
        spent += take
        quantity += take / price
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        return LiquidityReading(spread, None, False, "profondeur insuffisante pour la taille d'ordre")
    slippage = (spent / quantity - mid) / mid
    if slippage > max_slippage:
        return LiquidityReading(spread, slippage, False, f"glissement estimé {slippage:.3%} > {max_slippage:.3%}")
    return LiquidityReading(spread, slippage, True, "liquide")
