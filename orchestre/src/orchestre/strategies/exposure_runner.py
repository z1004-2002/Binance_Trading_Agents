"""Exécuteurs des stratèges à exposition cible (§11.4 à §11.8, EX-AG-04) : S-3/S-4/S-5 (un actif) et S-6/S-7 (portefeuille).

À chaque bougie clôturée (une seule fois), la stratégie propose une exposition ; l'exécuteur la traduit en ordres selon
EX-EX-03 et pose le stop de protection (EX-SEC-14). L'équité de l'agent = budget alloué - dépenses nettes + valeur détenue.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from orchestre.analysis.marketdata import validate_series
from orchestre.config import Env
from orchestre.exchange.base import Exchange, IpBannedError
from orchestre.execution.service import ExecutionService
from orchestre.strategies.legs import Leg


def _state_of(strategy) -> dict:
    return strategy.state() if hasattr(strategy, "state") else {}


def _load_state(strategy, state: dict) -> None:
    if hasattr(strategy, "load_state"):
        strategy.load_state(state)


class ExposureRunner:
    def __init__(
        self,
        strategy,
        symbol: str,
        interval: str,
        svc: ExecutionService,
        context_for,
        exchange: Exchange,
        agent_id: str,
        base_asset: str,
        budget_fn: Callable[[], Decimal],
        env: Env = Env.TESTNET,
        fee_rate: Decimal = Decimal("0.001"),
        check_every: timedelta = timedelta(minutes=15),
        candle_limit: int = 500,
    ) -> None:
        self.strategy, self.symbol, self.interval = strategy, symbol, interval
        self._exchange, self._budget_fn, self._every, self._limit = exchange, budget_fn, check_every, candle_limit
        self._context_for = context_for
        self.leg = Leg(agent_id, symbol, base_asset, svc, context_for, env, fee_rate)
        self._last: datetime | None = None
        self._next_check: datetime | None = None
        self._alerts: list[str] = []
        self.suspended = False  # gouvernance par régime : budget nul -> sortie des positions, aucune entrée

    @property
    def rejections(self):
        return self.leg.rejections

    def drain_alerts(self) -> list[str]:
        alerts, self._alerts = self._alerts + self.leg.drain_alerts(), []
        return alerts

    def equity(self, price: Decimal) -> Decimal:
        return self._budget_fn() - self.leg.net_spent + self.leg.qty * price

    def export_state(self) -> dict:
        return {
            "leg": self.leg.export_state(), "strategy": _state_of(self.strategy),
            "last": self._last.isoformat() if self._last else None,
        }

    def import_state(self, state: dict) -> None:
        self.leg.import_state(state["leg"])
        _load_state(self.strategy, state["strategy"])
        self._last = datetime.fromisoformat(state["last"]) if state["last"] else None

    def emergency_stop(self, price: Decimal, liquidate: bool) -> None:
        self.leg.shutdown(price, liquidate)

    def step(self, now: datetime, price: Decimal) -> None:
        self.leg.sync()
        self.leg.guard(price)
        if self.suspended:
            if self.leg.qty > 0:
                self.leg.set_target(Decimal("0"), price, self.equity(price))
            return
        if self._next_check is not None and now < self._next_check:
            return
        self._next_check = now + self._every
        try:
            candles = self._exchange.get_candles(self.symbol, self.interval, self._limit)
        except IpBannedError:
            raise
        except Exception as exc:
            self._alerts.append(f"bougies {self.symbol} indisponibles : {exc}")
            return
        report = validate_series(candles, self.interval)
        if not report.healthy or not report.accepted:
            if not report.healthy:
                self._alerts.append(f"bougies incohérentes sur {self.symbol} : {'; '.join(report.problems[:3])}")
            return
        newest = report.accepted[-1].open_time
        if self._last is not None and newest <= self._last:
            self.leg.ensure_protection(price)
            return
        self._last = newest

        target = self.strategy.target(report.accepted, self.leg.qty)
        if target.exposure is None:
            self.leg.propose_stop(target.stop)
        else:
            self.leg.propose_stop(target.stop)
            equity = self.equity(price)
            self.leg.set_target(target.exposure * equity / price, price, equity)
        self.leg.ensure_protection(price)


class PortfolioRunner:
    """S-6 / S-7 : poids par actif. Stop de protection par défaut `default_stop_pct` sous le prix d'entrée
    (NON couvert par les backtests : à valider séparément)."""

    def __init__(
        self,
        strategy,
        symbols: list[str],
        interval: str,
        svc: ExecutionService,
        context_for,
        exchange: Exchange,
        agent_id: str,
        base_assets: dict[str, str],
        budget_fn: Callable[[], Decimal],
        env: Env = Env.TESTNET,
        fee_rate: Decimal = Decimal("0.001"),
        check_every: timedelta = timedelta(minutes=15),
        candle_limit: int = 500,
        default_stop_pct: Decimal = Decimal("0.25"),
    ) -> None:
        self.strategy, self.symbols, self.interval = strategy, list(symbols), interval
        self._exchange, self._budget_fn, self._every, self._limit = exchange, budget_fn, check_every, candle_limit
        self._default_stop = default_stop_pct
        self._context_for = context_for
        self.legs = {s: Leg(agent_id, s, base_assets[s], svc, context_for, env, fee_rate) for s in symbols}
        self._last: datetime | None = None
        self._next_check: datetime | None = None
        self._alerts: list[str] = []
        self.suspended = False

    @property
    def rejections(self):
        return [d for leg in self.legs.values() for d in leg.rejections]

    def drain_alerts(self) -> list[str]:
        alerts = self._alerts
        for leg in self.legs.values():
            alerts += leg.drain_alerts()
        self._alerts = []
        return alerts

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        spent = sum((leg.net_spent for leg in self.legs.values()), Decimal("0"))
        held = sum((leg.qty * prices[s] for s, leg in self.legs.items()), Decimal("0"))
        return self._budget_fn() - spent + held

    def export_state(self) -> dict:
        return {
            "legs": {s: leg.export_state() for s, leg in self.legs.items()}, "strategy": _state_of(self.strategy),
            "last": self._last.isoformat() if self._last else None,
        }

    def import_state(self, state: dict) -> None:
        for s, leg_state in state["legs"].items():
            if s in self.legs:
                self.legs[s].import_state(leg_state)
        _load_state(self.strategy, state["strategy"])
        self._last = datetime.fromisoformat(state["last"]) if state["last"] else None

    def emergency_stop(self, prices: dict[str, Decimal], liquidate: bool) -> None:
        for s, leg in self.legs.items():
            if s in prices:
                leg.shutdown(prices[s], liquidate)

    def step(self, now: datetime, prices: dict[str, Decimal]) -> None:
        for s, leg in self.legs.items():
            leg.sync()
            leg.guard(prices[s])
        if self.suspended:
            equity = self.equity(prices)
            for s, leg in self.legs.items():
                if leg.qty > 0:
                    leg.set_target(Decimal("0"), prices[s], equity)
            return
        if self._next_check is not None and now < self._next_check:
            return
        self._next_check = now + self._every

        histories = {}
        for s in self.symbols:
            try:
                candles = self._exchange.get_candles(s, self.interval, self._limit)
            except IpBannedError:
                raise
            except Exception as exc:
                self._alerts.append(f"bougies {s} indisponibles : {exc}")
                continue
            report = validate_series(candles, self.interval)
            if not report.healthy:
                self._alerts.append(f"bougies incohérentes sur {s} : {'; '.join(report.problems[:3])}")
            elif report.accepted:
                histories[s] = report.accepted
        if not histories:
            return
        newest = min(h[-1].open_time for h in histories.values())
        histories = {s: [c for c in h if c.open_time <= newest] for s, h in histories.items()}
        if self._last is not None and newest <= self._last:
            for s, leg in self.legs.items():
                leg.ensure_protection(prices[s])
            return
        self._last = newest

        equity = self.equity(prices)
        positions = {s: leg.qty for s, leg in self.legs.items() if leg.qty > 0}
        target = self.strategy.target(histories, positions, equity)
        if target.weights is not None:
            plan = []
            for s, leg in self.legs.items():
                weight = target.weights.get(s, Decimal("0"))
                plan.append((s, weight * equity / prices[s] - leg.qty, weight))
            for s, delta, weight in sorted(plan, key=lambda p: p[1]):      # d'abord les ventes
                leg, price = self.legs[s], prices[s]
                if weight > 0:
                    explicit = target.stops.get(s)
                    if explicit is not None:
                        leg.propose_stop(explicit)
                    elif leg.wanted_stop is None or leg.qty == 0:
                        leg.wanted_stop = price * (1 - self._default_stop)
                leg.set_target(weight * equity / price, price, equity)
        for s, leg in self.legs.items():
            leg.ensure_protection(prices[s])
