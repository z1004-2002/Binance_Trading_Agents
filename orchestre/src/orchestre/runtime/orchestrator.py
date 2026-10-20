"""Orchestrateur : boucle de l'équipe (EX-ARCH-01, A-1, A-7).

Un tick : kill switch -> prix -> équité et paliers de drawdown -> stratèges -> réconciliation
-> santé des agents -> sauvegarde. Un agent défaillant n'arrête pas les autres (EX-ARCH-03).
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

from orchestre.analysis.regimes import Regime
from orchestre.analysis.sentiment import SentimentState
from orchestre.analysis.service import AnalysisService
from orchestre.config import Env, Settings
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
from orchestre.strategies.legacy import LegacyRunner
from orchestre.strategies.marketmaker import MarketMakerRunner
from orchestre.strategies.triangular import Triangle, TriangleObserver
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
    liquidate_on_drawdown_stop: bool = True   # au palier de 15 % les agents à exposition cible liquident (sinon : sous stop)


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
    flows: dict = field(default_factory=dict)        # agent -> {"start", "cash", "qty": {symbole: quantité}} : valeur propre à chaque agent
    disabled: list = field(default_factory=list)     # agents désactivés par un humain (survit au redémarrage)
    dynamic: dict = field(default_factory=dict)      # agents activés depuis l'interface : nom -> paramètres
    budgets: dict = field(default_factory=dict)      # budgets modifiés depuis l'interface

    def to_dict(self) -> dict:
        def s(v):
            return None if v is None else str(v)

        return {
            "baseline_value": s(self.baseline_value), "peak_equity": s(self.peak_equity),
            "tier1_applied": self.tier1_applied, "day": self.day,
            "day_start_equity": s(self.day_start_equity), "orders_today": self.orders_today,
            "ref_price0": s(self.ref_price0), "ref_equity0": s(self.ref_equity0),
            "flows": {a: {"start": str(f["start"]), "cash": str(f["cash"]), "qty": {k: str(v) for k, v in f["qty"].items()}}
                      for a, f in self.flows.items()},
            "disabled": list(self.disabled), "dynamic": dict(self.dynamic), "budgets": {a: str(v) for a, v in self.budgets.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeState":
        def dec(v):
            return None if v is None else Decimal(v)

        flows = {a: {"start": Decimal(f["start"]), "cash": Decimal(f["cash"]), "qty": {k: Decimal(v) for k, v in f["qty"].items()}}
                 for a, f in d.get("flows", {}).items()}
        return cls(dec(d["baseline_value"]), dec(d["peak_equity"]), d["tier1_applied"], d["day"],
                   dec(d["day_start_equity"]), d["orders_today"], dec(d.get("ref_price0")), dec(d.get("ref_equity0")),
                   flows, list(d.get("disabled", [])), dict(d.get("dynamic", {})), {a: Decimal(v) for a, v in d.get("budgets", {}).items()})


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
        self.disabling = False        # désactivation demandée : sort de ses positions puis disparaît

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
        elif self.kind in ("portfolio", "legacy"):
            self.runner.step(now, {sym: self._prices_fn()[sym] for sym in self.symbols})
        elif self.kind == "observer":
            self.runner.step(now)
        elif self.kind == "marketmaker":
            self.runner.step(now, price)
        else:
            self.runner.step(now, price, self.fear_greed)

    @property
    def positioned(self) -> bool:
        """Vrai tant que l'agent détient une position (exposition cible)."""
        if self.kind == "exposure":
            return self.runner.leg.qty > 0
        if self.kind == "portfolio":
            return any(leg.qty > 0 for leg in self.runner.legs.values())
        if self.kind in ("marketmaker", "legacy"):
            return self.runner.positioned
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
        self.self_check_problems: list[str] = []
        self._last_self_check_day: str | None = None
        self._last_snapshot: datetime | None = None
        self._report_due: tuple[str, int] | None = None
        self._logged_rejections: dict[str, int] = {}
        self._last_tick: datetime | None = None
        self.specs: dict[str, dict] = {}              # configuration de chaque stratégie connue (pour la réactiver sans paramètres)
        self._counted: dict[str, Decimal] = {}        # quantité déjà comptée par ordre, pour ne jamais compter deux fois
        self.svc.listener = self._on_order

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
        self.state.flows.setdefault(unit.agent_id, {"start": unit.budget, "cash": Decimal("0"), "qty": {}})

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

    def add_observer(self, agent_id: str, tri: Triangle, notional: Decimal, threshold: Decimal) -> None:
        """S-9a : mesure des écarts d'arbitrage triangulaire. N'envoie aucun ordre, n'a aucun budget."""
        runner = TriangleObserver(tri, self.exchange, Decimal("0.001"), notional, threshold)
        self._register(_Unit(agent_id, tri.symbols[0], "observer", runner, None, Decimal("0"), code="S-9a", symbols=list(tri.symbols)))

    def add_marketmaker(self, agent_id: str, symbol: str, budget: Decimal, **params) -> None:
        """S-9b : tenue de marché, SIMULATION UNIQUEMENT (EX-ST-10) : refusée si l'exchange n'est pas l'exchange paper."""
        if not isinstance(self.exchange, PaperExchange):
            raise ValueError("la tenue de marché est réservée à la simulation (mode paper) : jamais d'ordres réels (EX-ST-10)")
        self._reserve_budget(agent_id, budget)
        runner = MarketMakerRunner(
            symbol, symbol.removesuffix(self.settings.quote_currency), self.svc, self._context_factory(agent_id), self.exchange,
            agent_id, lambda: self.ledger.account(agent_id).allocated, env=self.settings.env, **params,
        )
        minimum = self._symbol_filters(symbol).min_notional * Decimal("2.4")
        self._register(_Unit(agent_id, symbol, "marketmaker", runner, None, budget, code="S-9b", min_viable=minimum))

    def add_legacy(self, agent_id: str, agent, symbols: list[str], budget: Decimal) -> None:
        """S-8 : agent du système existant, via l'adaptateur (ses intentions passent par le risque et l'exécuteur)."""
        self._reserve_budget(agent_id, budget)
        quote = self.settings.quote_currency
        runner = LegacyRunner(
            agent, symbols, self.svc, self._context_factory(agent_id), self.exchange, agent_id,
            {s: s.removesuffix(quote) for s in symbols}, lambda: self.ledger.account(agent_id).allocated, env=self.settings.env,
        )
        minimum = max(self._symbol_filters(s).min_notional for s in symbols) * Decimal("1.2")
        self._register(_Unit(agent_id, symbols[0], "legacy", runner, None, budget, code="S-8", symbols=list(symbols),
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

    def _on_order(self, agent_id: str, result, note: str) -> None:
        self._log_order(agent_id, result, note)
        self._count_fill(agent_id, result)

    def _count_fill(self, agent_id: str, result) -> None:
        """Flux de trésorerie et quantités de chaque agent, tirés de ses exécutions : base de sa courbe de valeur."""
        delta = result.filled_qty - self._counted.get(result.client_order_id, Decimal("0"))
        if delta <= 0 or agent_id not in self.state.flows:
            return
        self._counted[result.client_order_id] = result.filled_qty
        flow, sign = self.state.flows[agent_id], 1 if result.side.value == "BUY" else -1
        fee = result.fee_quote * delta / result.filled_qty          # frais au prorata de la part nouvellement exécutée
        flow["cash"] += -sign * delta * result.avg_price - fee
        flow["qty"][result.symbol] = flow["qty"].get(result.symbol, Decimal("0")) + sign * delta

    def agent_value(self, agent_id: str) -> Decimal | None:
        flow = self.state.flows.get(agent_id)
        if flow is None:
            return None
        held = sum((q * self._prices.get(sym, Decimal("0")) for sym, q in flow["qty"].items()), Decimal("0"))
        return flow["start"] + flow["cash"] + held

    def _log_order(self, agent_id: str, result, note: str) -> None:
        self.store.log_order(
            self.clock.now(), agent_id, result.symbol, result.side.value, result.filled_qty or result.orig_qty,
            result.avg_price, result.fee_quote, result.status, result.client_order_id, note,
        )

    # --- reprise ---
    def restore(self) -> None:
        self.svc.import_tracked(self.store.load_tracked())
        saved = self.store.load_state("orchestrator")
        if saved is not None:
            self.state = RuntimeState.from_dict(saved)
        for name, params in dict(self.state.dynamic).items():       # agents activés depuis l'interface lors d'une exécution précédente
            if name in self.units:
                continue
            try:
                self._enable(name, params)
            except Exception as exc:
                self.alerts.send("ERROR", f"agent {name} activé depuis l'interface non rétabli : {exc}")
        for agent_id in list(self.state.disabled):                  # les agents désactivés le restent, même si le fichier les liste
            self._remove_unit(agent_id)
        for agent_id, amount in self.state.budgets.items():
            if agent_id in self.units:
                self.units[agent_id].budget = amount
        for agent_id, unit in self.units.items():
            saved = self.store.load_state(agent_id)
            if saved is not None:
                unit.import_state(saved)
            self.state.flows.setdefault(agent_id, {"start": unit.budget, "cash": Decimal("0"), "qty": {}})
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
                self._pause_all(self._gap_reason("réconciliation au démarrage", gaps))
                return "PAUSED"

        self._update_equity(now, equity_value)
        if self._last_self_check_day != now.date().isoformat():
            self._last_self_check_day = now.date().isoformat()
            self._filters.clear()              # les filtres de symbole peuvent changer : on les relit chaque jour
            self.self_check_problems = self.self_check()
        self._snapshot(now, symbols)
        if self._report_due is not None:
            self.alerts.send("REPORT", self._daily_report())
            self._report_due = None
        if self._drawdown >= DRAWDOWN_STOP:
            self.alerts.send("CRITICAL", f"drawdown {self._drawdown:.2%} >= 15 % : arrêt global")
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.touch()  # l'arrêt survit à un redémarrage : reprise humaine uniquement
            return self._handle_kill("palier de drawdown 15 %", liquidate=self.limits.liquidate_on_drawdown_stop)
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

        self._finish_disabling()
        self.supervisor.heartbeat("execution")
        if self._reconcile_due(now):
            gaps = [g for s in symbols for g in self.svc.reconcile(s)]
            if gaps:
                self._pause_all(self._gap_reason("écart de réconciliation", gaps))
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
            if unit.kind == "observer":
                continue
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
            if unit.kind in ("exposure", "portfolio", "marketmaker", "legacy"):
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
            if text.startswith("info : "):
                self.alerts.send("INFO", f"{agent_id} : {text[len('info : '):]}")
                continue
            critical = "urgence" in text or "non protégée" in text
            self.alerts.send("CRITICAL" if critical else "WARNING", f"{agent_id} : {text}")
        if unit.kind in ("exposure", "portfolio", "marketmaker", "legacy") and unit.runner.suspended and not unit.positioned:
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

    def _gap_reason(self, prefix: str, gaps: list[str]) -> str:
        reason = f"{prefix} : " + "; ".join(gaps)
        if self.settings.env is Env.TESTNET and any("INCONNU" in g for g in gaps):
            reason += (" — la plateforme ne connaît plus des ordres que nous suivons : possible remise à zéro du testnet "
                       "(EX-ENV-02). Vérifier, puis repartir d'une base neuve.")
        return reason

    # --- autocontrôle quotidien (EX-SU-03) ---
    def self_check(self) -> list[str]:
        """Cohérence interne : registre, ordres suivis, stops de protection, espace disque. Journalisé et alerté."""
        problems: list[str] = []
        accounts = self.ledger.accounts()
        for a in accounts:
            if a.committed > a.allocated + Decimal("0.0001") or a.allocated < 0 or a.committed < 0:
                problems.append(f"registre incohérent pour {a.agent_id} : engagé {a.committed}, alloué {a.allocated}")
        if sum((a.allocated for a in accounts), Decimal("0")) > self.ledger.total + Decimal("0.0001"):
            problems.append("registre incohérent : la somme des budgets dépasse le capital plafonné")
        tracked = sum((Decimal(r["committed"]) for r in self.svc.export_tracked()), Decimal("0"))
        if tracked > sum((a.committed for a in accounts), Decimal("0")) + Decimal("0.0001"):
            problems.append("ordres suivis réservant plus que le montant engagé au registre")
        for agent_id, unit in self.units.items():
            legs = [unit.runner.leg] if unit.kind == "exposure" else list(unit.runner.legs.values()) if unit.kind == "portfolio" else []
            for leg in legs:
                if leg.qty > 0 and not leg.stop_order_id:
                    problems.append(f"position sans stop de protection : {agent_id} sur {leg.symbol} (EX-SEC-14)")
        try:
            free = shutil.disk_usage(Path(self.store.db.path).parent if self.store.db.path != ":memory:" else Path(".")).free
            if free < 200 * 10**6:
                problems.append(f"espace disque faible : {free // 10**6} Mo libres")
        except OSError:
            pass
        now = self.clock.now()
        if problems:
            for text in problems:
                self.alerts.send("CRITICAL" if "sans stop" in text else "WARNING", f"autocontrôle : {text}")
        else:
            self.store.log_event(now, "INFO", "self_check", "autocontrôle quotidien : OK")
        if problems:
            self.store.log_event(now, "WARNING", "self_check", "; ".join(problems)[:900])
        return problems

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
            elif unit.kind in ("grid", "dca"):
                self.store.log_equity(now, f"agent:{agent_id}", float(self.agent_value(agent_id)))
            elif unit.kind == "marketmaker":
                self.store.log_equity(now, f"agent:{agent_id}", float(unit.runner.equity(self._prices[unit.symbol])))
            elif unit.kind == "legacy":
                self.store.log_equity(now, f"agent:{agent_id}", float(unit.runner.equity({s: self._prices[s] for s in unit.symbols})))

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
            if command == "enable":
                return self._command_enable(target)
            if command == "disable":
                return self._command_disable(target)
            if command == "set_budget":
                return self._command_set_budget(target)
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

    # --- pilotage des agents depuis l'interface (activer, désactiver, budget) ---
    def _enable(self, name: str, params: dict) -> None:
        from orchestre.runtime import builder
        if name == "legacy":
            raise ValueError("l'agent existant (S-8) n'est pas activable depuis l'interface : il charge du code, réservé à la configuration")
        if params:
            section = {k: v for k, v in params.items() if k != "promoted"}   # la promotion en réel reste une décision humaine hors de l'interface (§14)
            section["enabled"] = True
        else:
            section = {**self.specs[name], "enabled": True}
        for symbol in builder.strategy_symbols({"strategies": {name: section}}):
            ensure = getattr(self.exchange, "ensure_symbol", None)
            if ensure is not None:
                ensure(symbol)
            self.exchange.get_filters(symbol)
            self.exchange.get_price(symbol)          # une paire que l'exchange ne connaît pas bloquerait tout le tick
        builder.add_strategy(name, section, self, self.exchange, self.clock.now())

    def _command_enable(self, target: str | None) -> tuple[str, str]:
        from orchestre.config import ConfigError
        from orchestre.runtime import builder
        import json
        name, _, raw = (target or "").partition("|")
        if name not in builder.KNOWN:
            return "refused", f"stratégie inconnue : {name or '?'}"
        agent_id = name
        if agent_id in self.units:
            return "refused", f"{agent_id} est déjà actif"
        params: dict = {}
        if raw:
            try:
                params = json.loads(raw)
            except ValueError:
                return "refused", "paramètres invalides (JSON attendu)"
            if not isinstance(params, dict):
                return "refused", "paramètres invalides (objet attendu)"
        elif name not in self.specs:
            return "refused", f"paramètres requis pour activer {name} (aucune configuration connue)"
        try:
            self._enable(name, params)
        except KeyError as exc:
            return "refused", f"paire inconnue de l'exchange : {exc.args[0]}"
        except (ConfigError, ValueError) as exc:
            return "refused", str(exc)
        except Exception as exc:
            return "refused", f"activation impossible : {exc!r}"
        if params:
            self.state.dynamic[name] = {k: v for k, v in params.items() if k != "promoted"}
            self.specs[name] = {**params, "enabled": True}
        if name in self.state.disabled:
            self.state.disabled.remove(name)
        return "done", f"{name} activé"

    def _command_disable(self, target: str | None) -> tuple[str, str]:
        unit = self.units.get(target or "")
        if unit is None:
            return "refused", f"agent inconnu : {target or '?'}"
        unit.disabling = True
        if hasattr(unit.runner, "suspended"):
            unit.runner.suspended = True                # sort de ses positions (stops conservés jusqu'à la sortie)
        return "done", f"désactivation de {target} en cours : positions et ordres d'abord soldés"

    def _command_set_budget(self, target: str | None) -> tuple[str, str]:
        agent_id, _, raw = (target or "").partition("|")
        try:
            amount = Decimal(raw)
            if not amount.is_finite() or amount <= 0:
                raise ValueError
        except (ArithmeticError, ValueError):
            return "refused", f"montant invalide : {raw!r}"
        unit = self.units.get(agent_id)
        if unit is None:
            return "refused", f"agent inconnu : {agent_id or '?'}"
        if unit.kind not in ("exposure", "portfolio", "marketmaker", "legacy"):
            return "refused", f"le budget de {agent_id} ({unit.kind}) est fixé par sa configuration et ne se change pas à chaud"
        acc = self.ledger.account(agent_id)
        target_alloc = amount / 2 if self.state.tier1_applied else amount
        others = sum((a.allocated for a in self.ledger.accounts() if a.agent_id != agent_id), Decimal("0"))
        investable = self.ledger.total * (1 - self.settings.treasury_reserve_pct)
        if others + target_alloc > investable:
            return "refused", f"budgets {others + target_alloc} > capital investissable {investable}"
        if target_alloc < acc.committed:
            return "refused", f"{acc.committed} est déjà engagé par {agent_id} : sortir de la position d'abord"
        if target_alloc > acc.allocated:
            self.ledger.allocate(agent_id, target_alloc - acc.allocated)
        elif target_alloc < acc.allocated:
            self.ledger.deallocate(agent_id, acc.allocated - target_alloc)
        flow = self.state.flows.get(agent_id)
        if flow is not None:
            flow["start"] += amount - unit.budget       # le capital ajouté ou retiré n'est ni un gain ni une perte
        unit.budget = amount
        self.state.budgets[agent_id] = amount
        return "done", f"budget de {agent_id} fixé à {amount}"

    def _remove_unit(self, agent_id: str) -> None:
        unit = self.units.pop(agent_id, None)
        self.unit_agents.pop(agent_id, None)
        self.supervisor.unregister(agent_id)
        if unit is not None:
            free = self.ledger.account(agent_id).available
            if free > 0:
                self.ledger.deallocate(agent_id, free)

    def _finish_disabling(self) -> None:
        for agent_id, unit in list(self.units.items()):
            if not unit.disabling:
                continue
            if unit.kind in ("grid", "dca"):
                self.svc.cancel_all_open(unit.symbol, agent_id, keep_protective=False)
            if unit.positioned or self.svc.open_order_count(agent_id) > 0:
                continue
            self._remove_unit(agent_id)
            self.state.dynamic.pop(agent_id, None)
            self.state.budgets.pop(agent_id, None)
            if agent_id not in self.state.disabled:
                self.state.disabled.append(agent_id)
            self.alerts.send("INFO", f"agent {agent_id} désactivé")

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
            "kinds": {aid: u.kind for aid, u in self.units.items()},
            "budgets": {aid: str(u.budget) for aid, u in self.units.items()},
            "symbols": {aid: list(u.symbols) for aid, u in self.units.items()},
            "positioned": {aid: u.positioned for aid, u in self.units.items()},
            "disabling": [aid for aid, u in self.units.items() if u.disabling],
            "disabled": list(self.state.disabled),
            "agent_params": {k: v for k, v in self.state.dynamic.items()},
        }

    # --- arrêts ---
    def _pause_all(self, reason: str) -> None:
        running = [a for a in self.unit_agents.values() if a.state is AgentState.RUNNING]
        for agent in running:
            agent.pause()
        self.alerts.send("CRITICAL", f"équipe en pause : {reason}")
        self._persist(self.clock.now())

    def _emergency_positions(self, liquidate: bool) -> None:
        """Avant d'arrêter les agents : régler les stops exécutés, sortir des positions dont le stop n'a pas pu
        s'exécuter (trou sous la limite), et liquider si la politique l'exige. Réduction de risque uniquement."""
        symbols = sorted({sym for u in self.units.values() for sym in u.symbols})
        for sym in symbols:
            try:
                self._prices[sym] = self.exchange.get_price(sym)
            except Exception:
                pass
        for agent_id, unit in self.units.items():
            if unit.kind not in ("exposure", "portfolio") or not all(s in self._prices for s in unit.symbols):
                continue
            try:
                if unit.kind == "exposure":
                    unit.runner.emergency_stop(self._prices[unit.symbol], liquidate)
                else:
                    unit.runner.emergency_stop({s: self._prices[s] for s in unit.symbols}, liquidate)
            except IpBannedError:
                self.alerts.send("CRITICAL", "IP bannie pendant l'arrêt : positions laissées sous leurs stops")
            except Exception as exc:
                self.alerts.send("ERROR", f"arrêt de {agent_id} : {exc!r}")
            self._forward_runner_alerts(agent_id, unit)

    def _handle_kill(self, reason: str, liquidate: bool = False) -> str:
        if not self._kill_handled:
            self._kill_handled = True
            self._emergency_positions(liquidate)
            self.supervisor.apply_command("kill")
            for symbol in sorted({sym for u in self.units.values() for sym in u.symbols}):
                self.svc.cancel_all_open(symbol)
            self.alerts.send("CRITICAL", f"kill switch : {reason}; ordres ouverts annulés (stops de protection conservés), agents arrêtés"
                             + ("; positions à exposition cible liquidées" if liquidate else ""))
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
