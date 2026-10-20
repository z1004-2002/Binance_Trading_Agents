"""Orchestrateur : boucle de l'équipe (EX-ARCH-01, A-1, A-7).

Un tick : kill switch -> prix -> équité et paliers de drawdown -> stratèges -> réconciliation
-> santé des agents -> sauvegarde. Un agent défaillant n'arrête pas les autres (EX-ARCH-03).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

from orchestre.config import Settings
from orchestre.core.agent import Agent, AgentState
from orchestre.core.clock import Clock
from orchestre.exchange.base import Exchange, IpBannedError
from orchestre.execution.service import ExecutionService
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.models import OrderProposal, RiskContext
from orchestre.runtime.alerts import Alerts
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.dca_runner import DcaRunner
from orchestre.strategies.grid import GridConfig, range_status
from orchestre.strategies.grid_runner import GridRunner
from orchestre.supervisor.service import Supervisor

DRAWDOWN_REDUCE = Decimal("0.05")
DRAWDOWN_STOP = Decimal("0.15")
DRAWDOWN_FREEZE = Decimal("0.10")


@dataclass(frozen=True)
class RuntimeLimits:
    max_order_notional: Decimal = Decimal("100")
    max_open_orders_agent: int = 50
    max_open_orders_symbol: int = 200
    max_consecutive_errors: int = 3
    max_orders_per_day: int = 500
    heartbeat_timeout: timedelta = timedelta(minutes=5)
    reconcile_interval: timedelta = timedelta(minutes=15)
    unit_error_threshold: int = 3


@dataclass
class RuntimeState:
    baseline_value: Decimal | None = None
    peak_equity: Decimal | None = None
    tier1_applied: bool = False
    day: str | None = None
    day_start_equity: Decimal | None = None
    orders_today: int = 0

    def to_dict(self) -> dict:
        def s(v):
            return None if v is None else str(v)

        return {
            "baseline_value": s(self.baseline_value), "peak_equity": s(self.peak_equity),
            "tier1_applied": self.tier1_applied, "day": self.day,
            "day_start_equity": s(self.day_start_equity), "orders_today": self.orders_today,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeState":
        def dec(v):
            return None if v is None else Decimal(v)

        return cls(dec(d["baseline_value"]), dec(d["peak_equity"]), d["tier1_applied"], d["day"],
                   dec(d["day_start_equity"]), d["orders_today"])


class _SimpleAgent(Agent):
    def __init__(self, agent_id: str, role: str) -> None:
        super().__init__()
        self.agent_id, self.role, self.agent_type = agent_id, role, "réflexe fondé sur un modèle"

    def decide(self, message, snapshot):
        return []


class _Unit:
    def __init__(self, agent_id: str, symbol: str, kind: str, runner, grid_cfg: GridConfig | None, budget: Decimal):
        self.agent_id, self.symbol, self.kind = agent_id, symbol, kind
        self.runner, self._grid_cfg, self.budget = runner, grid_cfg, budget
        self.started = False
        self.errors = 0

    def tick(self, now: datetime, price: Decimal) -> None:
        if self.kind == "grid":
            if not self.started and range_status(self._grid_cfg, price) == "IN_RANGE":
                self.runner.start(price)
                self.started = True
            self.runner.step(price)
        else:
            self.runner.step(now, price)

    def export_state(self) -> dict:
        state = self.runner.export_state()
        return {**state, "started": self.started} if self.kind == "grid" else state

    def import_state(self, state: dict) -> None:
        state = dict(state)
        self.started = state.pop("started", self.started)
        self.runner.import_state(state)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        exchange: Exchange,
        ledger: Ledger,
        store: Store,
        clock: Clock,
        stop_file: Path,
        limits: RuntimeLimits | None = None,
        alerts: Alerts | None = None,
    ) -> None:
        if settings.max_total_capital_quote <= 0:
            raise ValueError("max_total_capital_quote doit être > 0 pour lancer l'équipe (EX-SEC-11)")
        self.settings, self.exchange, self.ledger = settings, exchange, ledger
        self.store, self.clock, self.stop_file = store, clock, stop_file
        self.limits = limits or RuntimeLimits()
        self.alerts = alerts or Alerts(clock, store)
        self.svc = ExecutionService(exchange, ledger, dry_run=settings.dry_run)
        self.supervisor = Supervisor(clock, stop_file, self.limits.heartbeat_timeout)
        self.state = RuntimeState()
        self.units: dict[str, _Unit] = {}
        self.unit_agents: dict[str, Agent] = {}
        for core in ("marketdata", "execution"):
            agent = _SimpleAgent(core, "agent critique")
            agent.start()
            self.supervisor.register(agent, critical=True)
        self._prices: dict[str, Decimal] = {}
        self._quote_balance = Decimal("0")
        self._filters: dict = {}
        self._drawdown = Decimal("0")
        self._daily_loss = Decimal("0")
        self._last_sent = 0
        self._last_reconcile: datetime | None = None
        self._startup_done = False
        self._kill_handled = False

    # --- composition de l'équipe ---
    def _reserve_budget(self, agent_id: str, budget: Decimal) -> None:
        investable = self.ledger.total * (1 - self.settings.treasury_reserve_pct)
        if self.ledger.account(agent_id).allocated > 0:
            return  # budget déjà alloué (reprise)
        allocated = sum((a.allocated for a in self.ledger.accounts()), Decimal("0"))
        if allocated + budget > investable:
            raise ValueError(f"budgets {allocated + budget} > capital investissable {investable}")
        self.ledger.allocate(agent_id, budget)

    def _register(self, unit: _Unit) -> None:
        agent = _SimpleAgent(unit.agent_id, "stratège")
        agent.start()
        self.supervisor.register(agent)
        self.units[unit.agent_id], self.unit_agents[unit.agent_id] = unit, agent

    def add_grid(self, cfg: GridConfig, exit_policy: str = "pause", max_price_deviation_pct: Decimal = Decimal("0.25")) -> None:
        if any(u.kind == "grid" and u.symbol == cfg.symbol for u in self.units.values()):
            raise ValueError(f"un seul Grid par paire ({cfg.symbol}, EX-ST-06)")
        self._reserve_budget(cfg.agent_id, cfg.budget_quote)
        runner = GridRunner(
            cfg, self.svc, self._context_factory(cfg.agent_id, cfg.symbol, max_price_deviation_pct),
            base_asset=cfg.symbol.removesuffix(self.settings.quote_currency), env=self.settings.env,
            exit_policy=exit_policy,
        )
        self._register(_Unit(cfg.agent_id, cfg.symbol, "grid", runner, cfg, cfg.budget_quote))

    def add_dca(self, cfg: DcaConfig, max_price_deviation_pct: Decimal = Decimal("0.02")) -> None:
        self._reserve_budget(cfg.agent_id, cfg.cumulative_cap)
        runner = DcaRunner(
            cfg, self.svc, self._context_factory(cfg.agent_id, cfg.symbol, max_price_deviation_pct),
            base_asset=cfg.symbol.removesuffix(self.settings.quote_currency), env=self.settings.env,
        )
        self._register(_Unit(cfg.agent_id, cfg.symbol, "dca", runner, None, cfg.cumulative_cap))

    # --- contexte de risque (instantané du tick) ---
    def _context_factory(self, agent_id: str, symbol: str, max_deviation: Decimal) -> Callable[[OrderProposal], RiskContext]:
        def build(_proposal: OrderProposal) -> RiskContext:
            if symbol not in self._filters:
                self._filters[symbol] = self.exchange.get_filters(symbol)
            committed = sum((a.committed for a in self.ledger.accounts()), Decimal("0"))
            symbol_committed = sum(
                (self.ledger.account(u.agent_id).committed for u in self.units.values() if u.symbol == symbol),
                Decimal("0"),
            )
            return RiskContext(
                settings=self.settings, env=self.settings.env,
                kill_switch=self.supervisor.kill_switch_active(),
                agent_running=self.unit_agents[agent_id].state is AgentState.RUNNING,
                agent_budget_available=self.ledger.account(agent_id).available,
                total_committed_quote=committed, balance_quote=self._quote_balance,
                capital_quote=self.settings.max_total_capital_quote,
                last_price=self._prices[symbol], min_notional=self._filters[symbol].min_notional,
                max_order_notional=self.limits.max_order_notional, max_price_deviation_pct=max_deviation,
                pair_liquid=True, pair_allowed=symbol in {u.symbol for u in self.units.values()},
                asset_exposure_quote=symbol_committed, total_crypto_exposure_quote=committed,
                open_orders_agent=self.svc.open_order_count(agent_id),
                max_open_orders_agent=self.limits.max_open_orders_agent,
                open_orders_symbol=self.svc.open_order_count(symbol=symbol),
                max_open_orders_symbol=self.limits.max_open_orders_symbol,
                drawdown_pct=self._drawdown, daily_loss_pct=self._daily_loss,
                consecutive_errors=self.svc.consecutive_errors,
                max_consecutive_errors=self.limits.max_consecutive_errors,
                orders_today=self.state.orders_today + (self.svc.orders_sent - self._last_sent), max_orders_per_day=self.limits.max_orders_per_day,
                drawdown_freeze_pct=DRAWDOWN_FREEZE,
            )

        return build

    # --- reprise ---
    def restore(self) -> None:
        self.svc.import_tracked(self.store.load_tracked())
        for agent_id, unit in self.units.items():
            saved = self.store.load_state(agent_id)
            if saved is not None:
                unit.import_state(saved)
        saved = self.store.load_state("orchestrator")
        if saved is not None:
            self.state = RuntimeState.from_dict(saved)

    # --- un tick ---
    def run_once(self) -> str:
        now = self.clock.now()
        if self.supervisor.kill_switch_active():
            return self._handle_kill("kill switch actif")

        symbols = sorted({u.symbol for u in self.units.values()})
        try:
            self._prices = {s: self.exchange.get_price(s) for s in symbols}
            balances = self.exchange.get_balances()
            equity_value = self._wallet_value(symbols, balances)
            self.supervisor.heartbeat("marketdata")
        except IpBannedError as exc:
            self._pause_all(f"adresse IP bannie (418) : {exc}")
            return "PAUSED"
        except Exception as exc:
            self.alerts.send("WARNING", f"données de marché indisponibles : {exc}")
            self._health()
            return "NO_DATA"

        if not self._startup_done:
            self._startup_done = True
            gaps = [g for s in symbols for g in self.svc.reconcile(s)]
            if gaps:
                self._pause_all("réconciliation au démarrage : " + "; ".join(gaps))
                return "PAUSED"

        self._update_equity(now, equity_value)
        if self._drawdown >= DRAWDOWN_STOP:
            self.alerts.send("CRITICAL", f"drawdown {self._drawdown:.2%} >= 15 % : arrêt global")
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.touch()  # l'arrêt survit à un redémarrage : reprise humaine uniquement
            return self._handle_kill("palier de drawdown 15 %")
        if self._drawdown >= DRAWDOWN_REDUCE and not self.state.tier1_applied:
            self._reduce_budgets()

        try:
            for agent_id, unit in self.units.items():
                if self.unit_agents[agent_id].state is not AgentState.RUNNING:
                    continue
                self._tick_unit(agent_id, unit, now)
        except IpBannedError as exc:
            self._pause_all(f"adresse IP bannie (418) : {exc}")
            return "PAUSED"

        self.supervisor.heartbeat("execution")
        if self._reconcile_due(now):
            gaps = [g for s in symbols for g in self.svc.reconcile(s)]
            if gaps:
                self._pause_all("écart de réconciliation : " + "; ".join(gaps))
                return self._finish("PAUSED", now)
        return self._finish("OK", now)

    def _tick_unit(self, agent_id: str, unit: _Unit, now: datetime) -> None:
        try:
            unit.tick(now, self._prices[unit.symbol])
        except IpBannedError:
            raise
        except Exception as exc:  # EX-ARCH-03
            unit.errors += 1
            self.alerts.send("ERROR", f"agent {agent_id} en erreur ({unit.errors}) : {exc!r}")
            if unit.errors >= self.limits.unit_error_threshold:
                self.unit_agents[agent_id].degrade()
                self.alerts.send("CRITICAL", f"agent {agent_id} dégradé après {unit.errors} erreurs")
        else:
            unit.errors = 0
            self.supervisor.heartbeat(agent_id)

    def _finish(self, status: str, now: datetime) -> str:
        self._health()
        self._persist(now)
        return status

    def _health(self) -> None:
        for alert in self.supervisor.check_health():
            self.alerts.send("CRITICAL", alert)

    def _reconcile_due(self, now: datetime) -> bool:
        if self._last_reconcile is None or now - self._last_reconcile >= self.limits.reconcile_interval:
            self._last_reconcile = now
            return True
        return False

    # --- équité et paliers (§12.3) ---
    def _wallet_value(self, symbols: list[str], balances: dict) -> Decimal:
        quote = self.settings.quote_currency
        self._quote_balance = balances.get(quote, Decimal("0"))
        total = self._quote_balance
        for symbol in symbols:
            price = self._prices[symbol]
            total += balances.get(symbol.removesuffix(quote), Decimal("0")) * price
            for order in self.exchange.get_open_orders(symbol):
                total += order.orig_qty * (order.avg_price if order.side.value == "BUY" else price)
        return total

    def _update_equity(self, now: datetime, wallet_value: Decimal) -> None:
        s = self.state
        if s.baseline_value is None:
            s.baseline_value = wallet_value
        equity = self.ledger.total + (wallet_value - s.baseline_value)
        s.peak_equity = max(s.peak_equity or equity, equity)
        self._drawdown = max(Decimal("0"), (s.peak_equity - equity) / s.peak_equity)
        today = now.date().isoformat()
        sent = self.svc.orders_sent
        if s.day != today:
            s.day, s.day_start_equity, s.orders_today = today, equity, 0
        s.orders_today += sent - self._last_sent
        self._last_sent = sent
        base = s.day_start_equity or equity
        self._daily_loss = max(Decimal("0"), (base - equity) / base)

    def _reduce_budgets(self) -> None:
        """Palier 5 % : budgets disponibles divisés par deux (réduction de risque, automatique)."""
        self.state.tier1_applied = True
        for unit in self.units.values():
            half = self.ledger.account(unit.agent_id).available / 2
            if half > 0:
                self.ledger.deallocate(unit.agent_id, half)
        self.alerts.send("WARNING", f"drawdown {self._drawdown:.2%} >= 5 % : budgets réduits de moitié")

    # --- arrêts ---
    def _pause_all(self, reason: str) -> None:
        running = [a for a in self.unit_agents.values() if a.state is AgentState.RUNNING]
        for agent in running:
            agent.pause()
        self.alerts.send("CRITICAL", f"équipe en pause : {reason}")
        self._persist(self.clock.now())

    def _handle_kill(self, reason: str) -> str:
        if not self._kill_handled:
            self._kill_handled = True
            self.supervisor.apply_command("kill")
            for symbol in sorted({u.symbol for u in self.units.values()}):
                self.svc.cancel_all_open(symbol)
            self.alerts.send("CRITICAL", f"kill switch : {reason}; ordres ouverts annulés, agents arrêtés")
            self._persist(self.clock.now())
        return "KILLED"

    def _persist(self, now: datetime) -> None:
        states = {aid: u.export_state() for aid, u in self.units.items()}
        states["orchestrator"] = self.state.to_dict()
        self.store.save_all(self.ledger, self.svc.export_tracked(), states, now)

    # --- boucle ---
    async def run(
        self,
        interval: float,
        max_ticks: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> int:
        ticks = 0
        while max_ticks is None or ticks < max_ticks:
            status = await asyncio.to_thread(self.run_once)
            ticks += 1
            if status == "KILLED" or (max_ticks is not None and ticks >= max_ticks):
                break
            await sleep(interval)
        return ticks
