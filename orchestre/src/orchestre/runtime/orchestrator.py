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

from orchestre.analysis.regimes import Regime
from orchestre.analysis.sentiment import SentimentState
from orchestre.analysis.service import AnalysisService
from orchestre.config import Settings
from orchestre.core.agent import Agent, AgentState
from orchestre.core.clock import Clock
from orchestre.exchange.base import Exchange, IpBannedError
from orchestre.exchange.paper import PaperExchange
from orchestre.execution.service import ExecutionService
from orchestre.persistence.store import Store
from orchestre.portfolio.allocator import StrategyBudget, apply_allocations, compute_allocations, min_viable_budget
from orchestre.portfolio.ledger import Ledger
from orchestre.risk.models import OrderProposal, RiskContext
from orchestre.runtime.alerts import Alerts
from orchestre.strategies.dca import DcaConfig
from orchestre.strategies.dca_runner import DcaRunner
from orchestre.strategies.exposure_runner import ExposureRunner, PortfolioRunner
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
    ref_price0: Decimal | None = None
    ref_equity0: Decimal | None = None

    def to_dict(self) -> dict:
        def s(v):
            return None if v is None else str(v)

        return {
            "baseline_value": s(self.baseline_value), "peak_equity": s(self.peak_equity),
            "tier1_applied": self.tier1_applied, "day": self.day,
            "day_start_equity": s(self.day_start_equity), "orders_today": self.orders_today,
            "ref_price0": s(self.ref_price0), "ref_equity0": s(self.ref_equity0),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeState":
        def dec(v):
            return None if v is None else Decimal(v)

        return cls(dec(d["baseline_value"]), dec(d["peak_equity"]), d["tier1_applied"], d["day"],
                   dec(d["day_start_equity"]), d["orders_today"], dec(d.get("ref_price0")), dec(d.get("ref_equity0")))


class _SimpleAgent(Agent):
    def __init__(self, agent_id: str, role: str) -> None:
        super().__init__()
        self.agent_id, self.role, self.agent_type = agent_id, role, "réflexe fondé sur un modèle"

    def decide(self, message, snapshot):
        return []


class _Unit:
    def __init__(
        self, agent_id: str, symbol: str, kind: str, runner, grid_cfg: GridConfig | None, budget: Decimal,
        code: str = "", symbols: list[str] | None = None, min_viable: Decimal | None = None, prices_fn=None,
    ):
        self.agent_id, self.symbol, self.kind = agent_id, symbol, kind
        self.runner, self._grid_cfg, self.budget = runner, grid_cfg, budget
        self.code = code or {"grid": "S-2", "dca": "S-1"}.get(kind, "")
        self.symbols = symbols or [symbol]
        self.min_viable, self._prices_fn = min_viable, prices_fn
        self.started = False
        self.errors = 0
        self.fear_greed: int | None = None

    @property
    def grid_cfg(self) -> GridConfig | None:
        return self._grid_cfg

    def tick(self, now: datetime, price: Decimal) -> None:
        if self.kind == "grid":
            if not self.started and range_status(self._grid_cfg, price) == "IN_RANGE":
                self.runner.start(price)
                self.started = True
            self.runner.step(price)
        elif self.kind == "exposure":
            self.runner.step(now, price)
        elif self.kind == "portfolio":
            self.runner.step(now, {sym: self._prices_fn()[sym] for sym in self.symbols})
        else:
            self.runner.step(now, price, self.fear_greed)

    @property
    def positioned(self) -> bool:
        """Vrai tant que l'agent détient une position (exposition cible)."""
        if self.kind == "exposure":
            return self.runner.leg.qty > 0
        if self.kind == "portfolio":
            return any(leg.qty > 0 for leg in self.runner.legs.values())
        return False

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
        analysis: AnalysisService | None = None,
        reference_symbol: str | None = None,
    ) -> None:
        if settings.max_total_capital_quote <= 0:
            raise ValueError("max_total_capital_quote doit être > 0 pour lancer l'équipe (EX-SEC-11)")
        self.settings, self.exchange, self.ledger = settings, exchange, ledger
        self.store, self.clock, self.stop_file = store, clock, stop_file
        self.limits = limits or RuntimeLimits()
        self.alerts = alerts or Alerts(clock, store)
        self.analysis, self.reference_symbol = analysis, reference_symbol
        self._regime: Regime | None = None
        self._liquid: dict[str, bool] = {}
        self._fear_greed: int | None = None
        self._reported_problems: dict[str, tuple] = {}
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
        self.run_mode = "exchange"
        self.sentiment_state = SentimentState()   # paires en pause sur actualité grave : réduction de risque seulement
        self._equity = Decimal("0")
        self._last_snapshot: datetime | None = None
        self._report_due: tuple[str, int] | None = None
        self._logged_rejections: dict[str, int] = {}
        self._last_tick: datetime | None = None
        self.svc.listener = self._log_order

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

    def add_exposure(
        self, agent_id: str, code: str, strategy, symbol: str, budget: Decimal, interval: str = "1d",
    ) -> None:
        """Stratège à exposition cible sur une paire (S-3, S-4, S-5) : exposition -> ordres + stop chez Binance."""
        self._reserve_budget(agent_id, budget)
        runner = ExposureRunner(
            strategy, symbol, interval, self.svc, self._context_factory(agent_id), self.exchange, agent_id,
            symbol.removesuffix(self.settings.quote_currency), lambda: self.ledger.account(agent_id).allocated,
            env=self.settings.env,
        )
        minimum = self._symbol_filters(symbol).min_notional * Decimal("1.2")
        self._register(_Unit(agent_id, symbol, "exposure", runner, None, budget, code=code, min_viable=minimum))

    def add_portfolio(
        self, agent_id: str, code: str, strategy, symbols: list[str], budget: Decimal, interval: str = "1d",
        default_stop_pct: Decimal = Decimal("0.25"), positions: int = 1,
    ) -> None:
        """Stratège de portefeuille (S-6 rotation, S-7 rééquilibrage) : poids par actif -> ordres + stops."""
        self._reserve_budget(agent_id, budget)
        quote = self.settings.quote_currency
        runner = PortfolioRunner(
            strategy, symbols, interval, self.svc, self._context_factory(agent_id), self.exchange, agent_id,
            {s: s.removesuffix(quote) for s in symbols}, lambda: self.ledger.account(agent_id).allocated,
            env=self.settings.env, default_stop_pct=default_stop_pct,
        )
        minimum = max(self._symbol_filters(s).min_notional for s in symbols) * Decimal("1.2") * positions
        self._register(_Unit(agent_id, symbols[0], "portfolio", runner, None, budget, code=code, symbols=list(symbols),
                             min_viable=minimum, prices_fn=lambda: self._prices))

    # --- contexte de risque (instantané du tick) ---
    def _context_factory(
        self, agent_id: str, symbol: str | None = None, max_deviation: Decimal = Decimal("0.02")
    ) -> Callable[[OrderProposal], RiskContext]:
        def build(proposal: OrderProposal) -> RiskContext:
            symbol = proposal.symbol
            committed = sum((a.committed for a in self.ledger.accounts()), Decimal("0"))
            symbol_committed = sum(
                (self.ledger.account(u.agent_id).committed for u in self.units.values() if symbol in u.symbols),
                Decimal("0"),
            )
            return RiskContext(
                settings=self.settings, env=self.settings.env,
                kill_switch=self.supervisor.kill_switch_active(),
                agent_running=self.unit_agents[agent_id].state is AgentState.RUNNING,
                agent_budget_available=self.ledger.account(agent_id).available,
                total_committed_quote=committed, balance_quote=self._quote_balance,
                capital_quote=self.settings.max_total_capital_quote,
                last_price=self._prices[symbol], min_notional=self._symbol_filters(symbol).min_notional,
                max_order_notional=self.limits.max_order_notional, max_price_deviation_pct=max_deviation,
                pair_liquid=self._liquid.get(symbol, True), pair_allowed=symbol in {s for u in self.units.values() for s in u.symbols} and not self.sentiment_state.is_paused(symbol),
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

    def _symbol_filters(self, symbol: str):
        if symbol not in self._filters:
            self._filters[symbol] = self.exchange.get_filters(symbol)
        return self._filters[symbol]

    def _log_order(self, agent_id: str, result, note: str) -> None:
        self.store.log_order(
            self.clock.now(), agent_id, result.symbol, result.side.value, result.filled_qty or result.orig_qty,
            result.avg_price, result.fee_quote, result.status, result.client_order_id, note,
        )

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
        sentiment = self.store.load_state("sentiment_state")
        if sentiment is not None:
            self.sentiment_state.reasons = dict(sentiment["reasons"])
        paper = self.store.load_state("paper_exchange")
        if paper is not None and isinstance(self.exchange, PaperExchange):
            self.exchange.import_state(paper)

    # --- un tick ---
    def run_once(self) -> str:
        now = self.clock.now()
        self._last_tick = now
        self._process_commands(now)
        if self.supervisor.kill_switch_active():
            return self._handle_kill("kill switch actif")

        symbols = sorted({sym for u in self.units.values() for sym in u.symbols})
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
        self._snapshot(now, symbols)
        if self._report_due is not None:
            self.alerts.send("REPORT", self._daily_report())
            self._report_due = None
        if self._drawdown >= DRAWDOWN_STOP:
            self.alerts.send("CRITICAL", f"drawdown {self._drawdown:.2%} >= 15 % : arrêt global")
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.touch()  # l'arrêt survit à un redémarrage : reprise humaine uniquement
            return self._handle_kill("palier de drawdown 15 %")
        if self._drawdown >= DRAWDOWN_REDUCE and not self.state.tier1_applied:
            self._reduce_budgets()

        try:
            self._analyze(symbols)
        except IpBannedError as exc:
            self._pause_all(f"adresse IP bannie (418) : {exc}")
            return "PAUSED"

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

    # --- analyse de marché (§10) ---
    def _analyze(self, symbols: list[str]) -> None:
        if self.analysis is None:
            return
        try:
            snapshots = {s: self.analysis.refresh(s) for s in symbols}
            sentiment = self.analysis.refresh_sentiment()
        except IpBannedError:
            raise
        except Exception as exc:  # l'analyse est un capteur : sa panne ne doit pas arrêter l'équipe
            self.alerts.send("WARNING", f"analyse indisponible : {exc!r}")
            return
        self._fear_greed = sentiment.value if sentiment is not None else None
        for unit in self.units.values():
            unit.fear_greed = self._fear_greed
        for symbol, snap in snapshots.items():
            self._liquid[symbol] = snap.liquidity.liquid if snap.liquidity is not None else True
            problems = tuple(snap.problems)
            if problems != self._reported_problems.get(symbol, ()):
                self._reported_problems[symbol] = problems
                if problems:
                    self.alerts.send("WARNING", f"données d'analyse suspectes sur {symbol} : {'; '.join(problems)}")
        reference = snapshots.get(self.reference_symbol or symbols[0]) if symbols else None
        if reference and reference.healthy and reference.regime is not None and reference.regime is not self._regime:
            self._regime = reference.regime
            self._apply_regime(reference.regime)

    def _apply_regime(self, regime: Regime) -> None:
        """Budgets fixes x multiplicateur de régime (§12.1) : peut seulement réduire, jamais dépasser le budget configuré."""
        configs = []
        for unit in self.units.values():
            if unit.kind == "grid":
                minimum = min_viable_budget(unit.grid_cfg.intervals, unit.grid_cfg.min_notional)
            elif unit.min_viable is not None:
                minimum = unit.min_viable
            else:
                minimum = self._symbol_filters(unit.symbol).min_notional
            base = unit.budget / 2 if self.state.tier1_applied else unit.budget
            configs.append(StrategyBudget(unit.agent_id, unit.code, base, minimum))
        allocations = compute_allocations(configs, regime, self.ledger.total, self.settings.treasury_reserve_pct)
        apply_allocations(self.ledger, allocations)
        for unit in self.units.values():       # budget nul : un agent à exposition cible sort de ses positions
            if unit.kind in ("exposure", "portfolio"):
                unit.runner.suspended = allocations[unit.agent_id].budget == 0
        detail = ", ".join(f"{aid}={a.budget} ({a.status})" for aid, a in allocations.items())
        self.alerts.send("INFO", f"régime {regime.value} : budgets {detail}")

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
        self._forward_runner_alerts(agent_id, unit)
        self._log_new_rejections(agent_id, unit)

    def _forward_runner_alerts(self, agent_id: str, unit: _Unit) -> None:
        drain = getattr(unit.runner, "drain_alerts", None)
        for text in drain() if drain else []:
            critical = "urgence" in text or "non protégée" in text
            self.alerts.send("CRITICAL" if critical else "WARNING", f"{agent_id} : {text}")
        if unit.kind in ("exposure", "portfolio") and unit.runner.suspended and not unit.positioned:
            free = self.ledger.account(agent_id).available
            if free > 0:
                self.ledger.deallocate(agent_id, free)      # budget libéré tant que le régime est défavorable

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
        self._equity = equity
        s.peak_equity = max(s.peak_equity or equity, equity)
        self._drawdown = max(Decimal("0"), (s.peak_equity - equity) / s.peak_equity)
        today = now.date().isoformat()
        sent = self.svc.orders_sent
        if s.day != today:
            if s.day is not None:
                self._report_due = (s.day, s.orders_today)      # rapport du jour qui se termine (EX-NOT-03)
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

    # --- courbes, rapport, commandes, refus du risque ---
    def _snapshot(self, now: datetime, symbols: list[str]) -> None:
        if self._last_snapshot is not None and now - self._last_snapshot < timedelta(hours=1):
            return
        self._last_snapshot = now
        self.store.log_equity(now, "TEAM", float(self._equity))
        reference = self.reference_symbol or (symbols[0] if symbols else None)
        if reference and reference in self._prices:
            if self.state.ref_price0 is None:
                self.state.ref_price0, self.state.ref_equity0 = self._prices[reference], self._equity
            ref = self.state.ref_equity0 * self._prices[reference] / self.state.ref_price0
            self.store.log_equity(now, f"REF_{reference}", float(ref))
        for agent_id, unit in self.units.items():
            if unit.kind == "exposure":
                self.store.log_equity(now, f"agent:{agent_id}", float(unit.runner.equity(self._prices[unit.symbol])))
            elif unit.kind == "portfolio":
                self.store.log_equity(now, f"agent:{agent_id}", float(unit.runner.equity(self._prices)))

    def _daily_report(self) -> str:
        day, orders = self._report_due
        s = self.state
        pnl = self._equity - (s.day_start_equity or self._equity)
        positions = [
            u.agent_id for u in self.units.values() if u.positioned
        ] or ["aucune"]
        critical = sum(1 for level, _ in self.alerts.history if level == "CRITICAL")
        return (
            f"Rapport du {day} : capital de l'équipe {self._equity:.2f} {self.settings.quote_currency}, "
            f"drawdown {self._drawdown:.1%}, ordres envoyés ce jour-là {orders}, positions ouvertes : "
            f"{', '.join(positions)}, alertes critiques cumulées {critical}, mode {self.run_mode}."
        )

    def _process_commands(self, now: datetime) -> None:
        for cmd in self.store.pending_commands():
            status, result = self._apply_command(cmd["command"], cmd["target"])
            self.store.finish_command(cmd["id"], status, result, now)
            self.alerts.send("INFO" if status == "done" else "WARNING",
                             f"commande {cmd['command']} {cmd['target'] or ''} {status} : {result}".replace("  ", " "))

    def _apply_command(self, command: str, target: str | None) -> tuple[str, str]:
        try:
            if command == "kill":
                self.stop_file.parent.mkdir(parents=True, exist_ok=True)
                self.stop_file.touch()
                return "done", "kill switch activé (fichier STOP créé)"
            if command in ("pause_pair", "resume_pair"):
                pair, _, reason = (target or "").partition("|")
                known = {sym for u in self.units.values() for sym in u.symbols}
                if pair not in known:
                    return "refused", f"paire inconnue de l'équipe : {pair or '?'}"
                if command == "pause_pair":
                    self.sentiment_state.pause_pair(pair, reason or "actualité grave")
                    return "done", f"{pair} en pause (aucun nouvel achat) : {reason or 'actualité grave'}"
                self.sentiment_state.resume_pair(pair)
                return "done", f"{pair} reprise"
            if command not in ("pause", "resume"):
                return "refused", f"commande inconnue : {command}"
            targets = list(self.unit_agents) if target in (None, "", "all") else [target]
            unknown = [t for t in targets if t not in self.unit_agents]
            if unknown:
                return "refused", f"agent inconnu : {', '.join(unknown)}"
            for t in targets:
                self.supervisor.apply_command(command, t)
            return "done", f"{command} : {', '.join(targets)}"
        except PermissionError as exc:
            return "refused", str(exc)

    def _log_new_rejections(self, agent_id: str, unit: _Unit) -> None:
        rejections = getattr(unit.runner, "rejections", [])
        for decision in rejections[self._logged_rejections.get(agent_id, 0):]:
            self.store.log_event(self.clock.now(), "INFO", "risk", f"{agent_id} refusé par {decision.rule_id} : {decision.reason}")
        self._logged_rejections[agent_id] = len(rejections)

    def _runtime_info(self) -> dict:
        tier = 3 if self._drawdown >= DRAWDOWN_STOP else 2 if self._drawdown >= DRAWDOWN_FREEZE else 1 if self._drawdown >= DRAWDOWN_REDUCE else 0
        return {
            "env": self.settings.env.value, "mode": self.run_mode, "dry_run": self.settings.dry_run,
            "last_tick": self._last_tick.isoformat() if self._last_tick else None,
            "drawdown": float(self._drawdown), "daily_loss": float(self._daily_loss), "tier": tier,
            "equity": float(self._equity), "capital": float(self.ledger.total),
            "regime": self._regime.value if self._regime else None, "fear_greed": self._fear_greed,
            "kill": self.supervisor.kill_switch_active(),
            "agents": {aid: a.state.value for aid, a in self.unit_agents.items()},
            "codes": {aid: u.code for aid, u in self.units.items()},
            "paused_pairs": dict(self.sentiment_state.reasons),
        }

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
            for symbol in sorted({sym for u in self.units.values() for sym in u.symbols}):
                self.svc.cancel_all_open(symbol)
            self.alerts.send("CRITICAL", f"kill switch : {reason}; ordres ouverts annulés, agents arrêtés")
            self._persist(self.clock.now())
        return "KILLED"

    def _persist(self, now: datetime) -> None:
        states = {aid: u.export_state() for aid, u in self.units.items()}
        states["orchestrator"] = self.state.to_dict()
        states["run_mode"] = {"mode": self.run_mode}
        states["runtime_info"] = self._runtime_info()
        states["sentiment_state"] = {"reasons": dict(self.sentiment_state.reasons)}
        if isinstance(self.exchange, PaperExchange):
            states["paper_exchange"] = self.exchange.export_state()
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
