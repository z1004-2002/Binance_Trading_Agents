"""Données d'entrée du RiskAgent : la proposition et l'instantané de contexte (A-2, A-9)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from orchestre.config import Env, Settings
from orchestre.exchange.filters import Side


@dataclass(frozen=True)
class OrderProposal:
    agent_id: str
    symbol: str
    base_asset: str
    side: Side
    quantity: Decimal
    price: Decimal
    limit_price: Decimal | None
    env: Env
    est_fee: Decimal
    reduces_risk: bool = False
    has_stop: bool = False
    is_target_exposure: bool = False
    leverage: Decimal = Decimal("1")
    instrument: str = "spot"
    stop_price: Decimal | None = None   # ordre stop de protection posé chez Binance (EX-SEC-14)
    kind: str = "limit"                 # "limit" | "stop" : un stop est loin du prix par construction

    @property
    def notional(self) -> Decimal:
        return self.quantity * (self.limit_price or self.price)


@dataclass(frozen=True)
class RiskContext:
    settings: Settings
    env: Env
    kill_switch: bool
    agent_running: bool
    agent_budget_available: Decimal
    total_committed_quote: Decimal
    balance_quote: Decimal
    capital_quote: Decimal
    last_price: Decimal
    min_notional: Decimal
    max_order_notional: Decimal
    max_price_deviation_pct: Decimal
    pair_liquid: bool
    pair_allowed: bool
    asset_exposure_quote: Decimal
    total_crypto_exposure_quote: Decimal
    open_orders_agent: int
    max_open_orders_agent: int
    open_orders_symbol: int
    max_open_orders_symbol: int
    drawdown_pct: Decimal
    daily_loss_pct: Decimal
    consecutive_errors: int
    max_consecutive_errors: int
    orders_today: int
    max_orders_per_day: int
    drawdown_freeze_pct: Decimal = Decimal("0.10")
    daily_loss_pause_pct: Decimal = Decimal("0.03")
