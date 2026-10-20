"""Filtres de symbole et arrondis (EX-EX-02, A-5). Décimal exact, jamais de flottants."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class FilterError(ValueError):
    """Ordre rejeté localement, avant tout envoi."""


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal


def _snap(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step <= 0:
        raise ValueError("le pas doit être > 0")
    return (value / step).to_integral_value(rounding=rounding) * step


def round_qty(qty: Decimal, filters: SymbolFilters) -> Decimal:
    """Quantité arrondie vers le bas au stepSize."""
    return _snap(qty, filters.step_size, ROUND_FLOOR)


def round_price(price: Decimal, side: Side, filters: SymbolFilters) -> Decimal:
    """Prix arrondi au tickSize dans le sens favorable (achat: bas, vente: haut)."""
    mode = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
    return _snap(price, filters.tick_size, mode)


def validate_order(qty: Decimal, price: Decimal, filters: SymbolFilters) -> None:
    """Lève FilterError si l'ordre viole un filtre. Quantité et prix déjà arrondis."""
    if qty != round_qty(qty, filters):
        raise FilterError(f"quantité {qty} non multiple de stepSize {filters.step_size}")
    if price != _snap(price, filters.tick_size, ROUND_FLOOR):
        raise FilterError(f"prix {price} non multiple de tickSize {filters.tick_size}")
    if not filters.min_qty <= qty <= filters.max_qty:
        raise FilterError(f"quantité {qty} hors [{filters.min_qty} ; {filters.max_qty}]")
    if qty * price < filters.min_notional:
        raise FilterError(f"valeur {qty * price} sous le minimum {filters.min_notional}")
