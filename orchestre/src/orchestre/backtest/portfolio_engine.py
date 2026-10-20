"""Moteur de backtest multi-actifs (§11.7, §11.8) : mêmes règles d'exécution que le moteur mono-actif.

Cibles = poids du capital par actif (le reste est en devise de cotation). Exécution à l'OUVERTURE de la bougie
suivante, frais et glissement, stops par actif, seuil anti va-et-vient. Les bougies sont alignées sur les
horodatages communs à tous les actifs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from orchestre.backtest.engine import QTY_STEP, BacktestConfig, BacktestError, BacktestResult, Trade
from orchestre.exchange.base import Candle


@dataclass(frozen=True)
class PortfolioTarget:
    weights: dict[str, Decimal] | None  # None = ne rien changer ; un actif absent = poids 0
    stops: dict[str, Decimal] = field(default_factory=dict)


class PortfolioStrategy(Protocol):
    name: str
    params_tried: int

    def target(
        self, histories: dict[str, list[Candle]], positions: dict[str, Decimal], equity: Decimal
    ) -> PortfolioTarget: ...


@dataclass
class _MultiBook:
    cfg: BacktestConfig
    cash: Decimal
    qty: dict[str, Decimal] = field(default_factory=dict)
    fees: Decimal = Decimal("0")
    traded: Decimal = Decimal("0")
    trades: list[Trade] = field(default_factory=list)

    def buy(self, time: datetime, symbol: str, qty: Decimal, price: Decimal, reason: str) -> None:
        px = price * (1 + self.cfg.slippage)
        qty = min(qty, self.cash / (px * (1 + self.cfg.fee_rate))).quantize(QTY_STEP, rounding=ROUND_DOWN)
        if qty <= 0:
            return
        cost = qty * px
        fee = cost * self.cfg.fee_rate
        self.cash -= cost + fee
        self.qty[symbol] = self.qty.get(symbol, Decimal("0")) + qty
        self._record(time, symbol, "BUY", qty, px, fee, reason)

    def sell(self, time: datetime, symbol: str, qty: Decimal, price: Decimal, reason: str) -> None:
        held = self.qty.get(symbol, Decimal("0"))
        px = price * (1 - self.cfg.slippage)
        qty = held if qty >= held else qty.quantize(QTY_STEP, rounding=ROUND_DOWN)
        if qty <= 0:
            return
        proceeds = qty * px
        fee = proceeds * self.cfg.fee_rate
        self.cash += proceeds - fee
        self.qty[symbol] = held - qty
        self._record(time, symbol, "SELL", qty, px, fee, reason)

    def _record(self, time, symbol, side, qty, px, fee, reason) -> None:
        self.fees += fee
        self.traded += qty * px
        self.trades.append(Trade(time, side, qty, px, fee, reason, symbol))


def align_candles(candle_map: dict[str, list[Candle]]) -> dict[str, list[Candle]]:
    if not candle_map:
        raise BacktestError("aucun actif fourni")
    common = set.intersection(*({c.open_time for c in candles} for candles in candle_map.values()))
    aligned = {s: [c for c in candles if c.open_time in common] for s, candles in candle_map.items()}
    for symbol, candles in aligned.items():
        if any(not c.closed for c in candles):
            raise BacktestError(f"{symbol} : bougie non clôturée")
        if any(candles[i].open_time <= candles[i - 1].open_time for i in range(1, len(candles))):
            raise BacktestError(f"{symbol} : bougies non ordonnées")
    return aligned


def _check_weights(weights: dict[str, Decimal]) -> None:
    if any(w < 0 for w in weights.values()) or sum(weights.values(), Decimal("0")) > Decimal("1.000000001"):
        raise BacktestError("poids invalides : chacun >= 0 et somme <= 1 (pas de levier)")


def run_portfolio_backtest(
    candle_map: dict[str, list[Candle]],
    strategy: PortfolioStrategy,
    cfg: BacktestConfig = BacktestConfig(),
    warmup: int = 0,
) -> BacktestResult:
    data = align_candles(candle_map)
    n = len(next(iter(data.values())))
    if warmup < 0 or warmup >= n - 1:
        raise BacktestError("préchauffage incompatible avec la longueur de l'historique commun")

    book = _MultiBook(cfg, cfg.initial_cash)
    pending: PortfolioTarget | None = None
    stops: dict[str, Decimal] = {}
    last_equity = cfg.initial_cash
    dates: list[datetime] = []
    equity: list[float] = []

    for i in range(warmup, n):
        bars = {s: data[s][i] for s in data}
        if pending is not None and pending.weights is not None:
            _rebalance(book, bars, pending.weights, last_equity)
        for symbol, bar in bars.items():
            stop = stops.get(symbol)
            if book.qty.get(symbol, Decimal("0")) > 0 and stop is not None and bar.low <= stop:
                book.sell(bar.open_time, symbol, book.qty[symbol], min(bar.open, stop), "stop")
                stops.pop(symbol, None)

        last_equity = book.cash + sum((q * bars[s].close for s, q in book.qty.items()), Decimal("0"))
        dates.append(next(iter(bars.values())).close_time)
        equity.append(float(last_equity))

        histories = {s: data[s][: i + 1] for s in data}
        positions = {s: q for s, q in book.qty.items() if q > 0}
        pending = strategy.target(histories, positions, last_equity)
        if pending.weights is not None:
            _check_weights(pending.weights)
            stops = {sym: _ratchet(stops.get(sym), stop, book.qty.get(sym, Decimal("0"))) for sym, stop in pending.stops.items()}
        elif pending.stops:
            stops.update({sym: _ratchet(stops.get(sym), stop, book.qty.get(sym, Decimal("0"))) for sym, stop in pending.stops.items()})

    return BacktestResult(
        strategy.name, dates, equity, book.trades, book.fees, book.traded,
        getattr(strategy, "params_tried", 1), book.cash, cfg.periods_per_year,
    )


def _ratchet(old: Decimal | None, new: Decimal, held: Decimal) -> Decimal:
    """Un stop ne descend pas tant que la position est ouverte (C-08)."""
    return old if held > 0 and old is not None and new < old else new


def _rebalance(book: _MultiBook, bars: dict[str, Candle], weights: dict[str, Decimal], equity: Decimal) -> None:
    threshold = book.cfg.min_trade_pct * equity
    symbols = sorted(set(bars) & (set(weights) | {s for s, q in book.qty.items() if q > 0}))
    plan = []
    for symbol in symbols:
        weight, bar = weights.get(symbol, Decimal("0")), bars[symbol]
        held = book.qty.get(symbol, Decimal("0"))
        delta = weight * equity / bar.open - held
        if weight == 0:
            if held > 0:
                plan.append((symbol, -held))
        elif abs(delta) * bar.open >= threshold:
            plan.append((symbol, delta))
    for symbol, delta in sorted(plan, key=lambda p: p[1]):  # d'abord les ventes : elles libèrent des liquidités
        bar = bars[symbol]
        (book.buy if delta > 0 else book.sell)(bar.open_time, symbol, abs(delta), bar.open, "signal")
