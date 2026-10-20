"""S-2 Grid : ordres d'achat sous le prix, vente un niveau plus haut après chaque achat (§11.3).

Logique pure (A-2). Formules : P_i = L x (U/L)^(i/N) ; g = (U/L)^(1/N) - 1 ; profit net ~ g - 2f.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from orchestre.exchange.filters import Side
from orchestre.portfolio.allocator import min_viable_budget

STOP_BELOW_LOWER_PCT = Decimal("0.02")


@dataclass(frozen=True)
class GridConfig:
    agent_id: str
    symbol: str
    lower: Decimal
    upper: Decimal
    intervals: int
    budget_quote: Decimal
    fee_rate: Decimal
    min_notional: Decimal
    tick_size: Decimal
    step_size: Decimal
    min_net_profit: Decimal = Decimal("0.001")
    max_num_orders: int = 200

    @property
    def spacing(self) -> Decimal:
        return ((self.upper / self.lower).ln() / self.intervals).exp() - 1

    @property
    def net_profit_per_cycle(self) -> Decimal:
        return self.spacing - 2 * self.fee_rate

    @property
    def quote_per_level(self) -> Decimal:
        return self.budget_quote / self.intervals

    def __post_init__(self) -> None:
        if not 0 < self.lower < self.upper:
            raise ValueError("bornes invalides : 0 < L < U")
        if self.intervals < 2:
            raise ValueError("au moins 2 intervalles")
        if self.net_profit_per_cycle < self.min_net_profit:
            raise ValueError(
                f"rentabilité insuffisante : profit net {self.net_profit_per_cycle:.4%} "
                f"< seuil {self.min_net_profit:.4%} (EX-ST-03)"
            )
        if self.intervals + 1 > self.max_num_orders:
            raise ValueError("nombre d'ordres supérieur à MAX_NUM_ORDERS (EX-ST-03)")
        if self.budget_quote < min_viable_budget(self.intervals, self.min_notional):
            raise ValueError("budget sous le minimum viable (EX-AL-03)")
        if self.quote_per_level < self.min_notional:
            raise ValueError("valeur d'un niveau sous le minimum d'ordre (EX-ST-03)")


@dataclass(frozen=True)
class GridOrder:
    level: int
    side: Side
    price: Decimal
    quantity: Decimal
    client_order_id: str
    reason: str


@dataclass(frozen=True)
class GridFill:
    level: int
    side: Side
    filled_qty: Decimal  # quantité réellement reçue (commission déduite si prélevée en base)
    price: Decimal
    cycle: int = 0


def _snap(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value() * step


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def build_levels(cfg: GridConfig) -> list[Decimal]:
    ratio_ln = (cfg.upper / cfg.lower).ln()
    return [
        _snap(cfg.lower * (ratio_ln * i / cfg.intervals).exp(), cfg.tick_size)
        for i in range(cfg.intervals + 1)
    ]


def _order_id(cfg: GridConfig, level: int, side: Side, cycle: int) -> str:
    return f"{cfg.agent_id}-L{level}-{side.value[0]}-{cycle}"


def _buy_at(cfg: GridConfig, level: int, price: Decimal, cycle: int, why: str) -> GridOrder:
    qty = _floor(cfg.quote_per_level / price, cfg.step_size)
    return GridOrder(level, Side.BUY, price, qty, _order_id(cfg, level, Side.BUY, cycle), why)


def initial_orders(cfg: GridConfig, price: Decimal) -> list[GridOrder]:
    """Achats `LIMIT_MAKER` sur chaque niveau strictement sous le prix (EX-ST-04)."""
    if range_status(cfg, price) != "IN_RANGE":
        return []
    levels = build_levels(cfg)
    return [
        _buy_at(cfg, i, p, 0, f"niveau {i} sous le prix {price}")
        for i, p in enumerate(levels[:-1])
        if p < price
    ]


def range_status(cfg: GridConfig, price: Decimal) -> str:
    if price < cfg.lower * (1 - STOP_BELOW_LOWER_PCT):
        return "BELOW_STOP"
    if price > cfg.upper:
        return "ABOVE"
    return "IN_RANGE"


def on_fill(cfg: GridConfig, fill: GridFill) -> GridOrder | None:
    """Achat au niveau i -> vente au niveau i+1 ; vente au niveau j -> achat au niveau j-1 (EX-ST-05)."""
    levels = build_levels(cfg)
    cycle = fill.cycle + 1
    if fill.side is Side.BUY:
        if fill.level >= cfg.intervals:
            return None
        nxt = fill.level + 1
        qty = _floor(fill.filled_qty, cfg.step_size)
        return GridOrder(
            nxt, Side.SELL, levels[nxt], qty, _order_id(cfg, nxt, Side.SELL, cycle),
            f"achat exécuté au niveau {fill.level} : vente de la quantité reçue au niveau {nxt}",
        )
    if fill.level <= 0:
        return None
    nxt = fill.level - 1
    return _buy_at(cfg, nxt, levels[nxt], cycle, f"vente exécutée au niveau {fill.level} : rachat au niveau {nxt}")


def cycle_profit(buy_price: Decimal, sell_price: Decimal, qty: Decimal, fee_rate: Decimal) -> Decimal:
    return (sell_price - buy_price) * qty - fee_rate * (buy_price + sell_price) * qty
