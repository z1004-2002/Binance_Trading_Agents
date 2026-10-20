"""Moteur de backtest prudent (§16.2, EX-BT-03 à EX-BT-05).

- aucun regard vers l'avenir : la stratégie ne voit que les bougies clôturées jusqu'à t ;
- un ordre décidé à la clôture de t s'exécute à l'OUVERTURE de t+1, avec glissement et frais ;
- un stop est exécuté au prix du stop (ou à l'ouverture en cas de trou), moins le glissement ;
- les expositions cibles sont traduites comme l'exécuteur réel (EX-EX-03), avec seuil anti va-et-vient.

Limite connue : ce moteur rejoue UNE stratégie à exposition cible. Le rejeu de l'équipe entière
(allocateur, risque, exécuteur) viendra avec la phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from orchestre.exchange.base import Candle

QTY_STEP = Decimal("1E-8")


class BacktestError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    exposure: Decimal | None  # part du capital en actif, [0 ; 1] ; None = ne rien changer
    stop: Decimal | None = None


class ExposureStrategy(Protocol):
    name: str
    params_tried: int

    def target(self, history: list[Candle], position: Decimal) -> Target: ...


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: Decimal = Decimal("1000")
    fee_rate: Decimal = Decimal("0.001")
    slippage: Decimal = Decimal("0.0005")
    min_trade_pct: Decimal = Decimal("0.02")
    periods_per_year: int = 365  # 365 en journalier, 2190 en 4 h


@dataclass(frozen=True)
class Trade:
    time: datetime
    side: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    reason: str  # "signal" | "stop"
    symbol: str = ""


@dataclass
class BacktestResult:
    name: str
    dates: list[datetime]
    equity: list[float]
    trades: list[Trade]
    fees_paid: Decimal
    traded_value: Decimal
    params_tried: int
    final_cash: Decimal = Decimal("0")
    periods_per_year: int = 365

    @property
    def days(self) -> float:
        return max((len(self.equity) - 1) * 365 / self.periods_per_year, 1.0)


@dataclass
class _Book:
    cfg: BacktestConfig
    cash: Decimal
    qty: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    traded: Decimal = Decimal("0")
    trades: list[Trade] = field(default_factory=list)

    def buy(self, time: datetime, qty: Decimal, price: Decimal, reason: str) -> None:
        px = price * (1 + self.cfg.slippage)
        qty = min(qty, self.cash / (px * (1 + self.cfg.fee_rate))).quantize(QTY_STEP, rounding=ROUND_DOWN)
        if qty <= 0:
            return
        cost = qty * px
        fee = cost * self.cfg.fee_rate
        self.cash -= cost + fee
        self.qty += qty
        self._record(time, "BUY", qty, px, fee, reason)

    def sell(self, time: datetime, qty: Decimal, price: Decimal, reason: str) -> None:
        px = price * (1 - self.cfg.slippage)
        qty = self.qty if qty >= self.qty else qty.quantize(QTY_STEP, rounding=ROUND_DOWN)
        if qty <= 0:
            return
        proceeds = qty * px
        fee = proceeds * self.cfg.fee_rate
        self.cash += proceeds - fee
        self.qty -= qty
        self._record(time, "SELL", qty, px, fee, reason)

    def _record(self, time, side, qty, px, fee, reason) -> None:
        self.fees += fee
        self.traded += qty * px
        self.trades.append(Trade(time, side, qty, px, fee, reason))


def _validate(candles: list[Candle], warmup: int) -> None:
    if any(not c.closed for c in candles):
        raise BacktestError("bougie non clôturée : seules les bougies clôturées sont admises (EX-AN-02)")
    if any(candles[i].open_time <= candles[i - 1].open_time for i in range(1, len(candles))):
        raise BacktestError("bougies non strictement ordonnées dans le temps")
    if warmup < 0 or warmup >= len(candles) - 1:
        raise BacktestError("préchauffage incompatible avec la longueur de l'historique")


def run_backtest(
    candles: list[Candle], strategy: ExposureStrategy, cfg: BacktestConfig = BacktestConfig(), warmup: int = 0
) -> BacktestResult:
    _validate(candles, warmup)
    book = _Book(cfg, cfg.initial_cash)
    pending: Target | None = None
    stop: Decimal | None = None
    last_equity = cfg.initial_cash
    dates: list[datetime] = []
    equity: list[float] = []

    for i in range(warmup, len(candles)):
        c = candles[i]
        if pending is not None and pending.exposure is not None:
            _rebalance(book, c, pending.exposure, last_equity)
        if book.qty > 0 and stop is not None and c.low <= stop:
            book.sell(c.open_time, book.qty, min(c.open, stop), "stop")
        if book.qty == 0:
            stop = None

        last_equity = book.cash + book.qty * c.close
        dates.append(c.close_time)
        equity.append(float(last_equity))

        pending = strategy.target(candles[: i + 1], book.qty)
        if pending.stop is not None or pending.exposure is not None:
            new_stop = pending.stop
            if book.qty > 0 and stop is not None and new_stop is not None and new_stop < stop:
                new_stop = stop  # cliquet : un stop ne descend pas tant que la position est ouverte (C-08)
            stop = new_stop

    return BacktestResult(
        strategy.name, dates, equity, book.trades, book.fees, book.traded,
        getattr(strategy, "params_tried", 1), book.cash, cfg.periods_per_year,
    )


def _rebalance(book: _Book, c: Candle, exposure: Decimal, reference_equity: Decimal) -> None:
    if exposure <= 0:
        if book.qty > 0:
            book.sell(c.open_time, book.qty, c.open, "signal")
        return
    desired = exposure * reference_equity / c.open
    delta = desired - book.qty
    if abs(delta) * c.open < book.cfg.min_trade_pct * reference_equity:
        return  # seuil anti va-et-vient (EX-EX-03)
    if delta > 0:
        book.buy(c.open_time, delta, c.open, "signal")
    else:
        book.sell(c.open_time, -delta, c.open, "signal")
