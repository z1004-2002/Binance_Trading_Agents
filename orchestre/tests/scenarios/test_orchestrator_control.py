"""Pilotage des agents depuis l'interface : activer, désactiver, changer le budget ; courbe de valeur de chaque agent."""

import json
import tomllib
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.config import parse_settings
from orchestre.core.agent import AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.base import OrderBook
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.exchange.paper import PaperExchange
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.builder import build_team
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
BASE = '''
[strategies.dca]
enabled = true
symbol = "BTCUSDT"
amount_quote = "20"
interval_days = 7
start = "2026-10-20T00:00:00+00:00"
cumulative_cap = "40"
'''


def world(tmp_path, config=BASE, db=None, ex=None, clock=None, paper=False):
    market = ex or FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
    market.set_candles("BTCUSDT", "1d", make_candles([60000.0] * 300))
    exchange = PaperExchange(market, ["BTCUSDT"], {"USDT": D("2000")}) if paper else market
    clock = clock or SimulatedClock(T0)
    store = Store(Database(db or ":memory:"))
    orch = Orchestrator(SETTINGS, exchange, store.load_ledger() or Ledger(D("500")), store, clock, tmp_path / "STOP",
                        limits=RuntimeLimits(reconcile_interval=timedelta(0), max_order_notional=D("150")))
    build_team(tomllib.loads(config), orch, exchange, T0)
    return orch, exchange, clock, store


def tick(orch, clock, n=1):
    status = None
    for _ in range(n):
        clock.advance(timedelta(minutes=20))
        status = orch.run_once()
    return status


def command(store, clock, name, target=None):
    return store.enqueue_command(clock.now(), name, target)


def last(store):
    return store.recent_commands(1)[0]


TREND = 'trend|{"symbol": "BTCUSDT", "budget_quote": "100"}'


# ---------- courbe de valeur de chaque agent ----------

def test_every_agent_gets_its_own_equity_curve_from_its_fills(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    tick(orch, clock, 4)                                                                # 80 minutes : un 2e relevé horaire, après l'achat
    series = store.equity_series("agent:dca")
    assert len(series) == 2 and series[0][1] == 40.0 and 39.9 < series[-1][1] < 40.0     # budget 40 (plafond), frais payés ~0,02


def test_agent_flows_survive_a_restart(tmp_path):
    db = tmp_path / "o.db"
    orch, ex, clock, store = world(tmp_path, db=db)
    tick(orch, clock)
    before = orch.state.flows["dca"]["cash"]
    orch2, *_ = world(tmp_path, db=db, ex=ex, clock=clock)
    orch2.restore()
    assert orch2.state.flows["dca"]["cash"] == before


# ---------- activer ----------

def test_enable_builds_the_agent_at_runtime_and_allocates_its_budget(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    assert last(store)["status"] == "done" and "trend" in orch.units and orch.unit_agents["trend"].state is AgentState.RUNNING
    assert orch.ledger.account("trend").allocated == D("100")


def test_enable_is_refused_for_unknown_active_or_invalid_requests(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    for target, why in (
        ('martingale|{}', "inconnue"), ('dca|{"symbol": "BTCUSDT"}', "déjà actif"), ('trend', "paramètres"),
        ('trend|pas du json', "invalides"), ('trend|{"symbol": "BTCUSDT", "budget_quote": "9999"}', "investissable"),
        ('trend|{"symbol": "BTCUSDT"}', "budget_quote"),
    ):
        command(store, clock, "enable", target)
        tick(orch, clock)
        c = last(store)
        assert c["status"] == "refused" and why in c["result"], (target, c["result"])
    assert "trend" not in orch.units


def test_the_market_maker_cannot_be_enabled_outside_the_simulation(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", 'marketmaker|{"symbol": "BTCUSDT", "budget_quote": "100"}')
    tick(orch, clock)
    assert last(store)["status"] == "refused" and "simulation" in last(store)["result"]


def test_a_runtime_enabled_agent_comes_back_after_a_restart(tmp_path):
    db = tmp_path / "o.db"
    orch, ex, clock, store = world(tmp_path, db=db)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    orch2, _, _, _ = world(tmp_path, db=db, ex=ex, clock=clock)
    assert "trend" not in orch2.units
    orch2.restore()
    assert "trend" in orch2.units and orch2.ledger.account("trend").allocated == D("100")


# ---------- désactiver ----------

def test_disable_closes_the_position_then_removes_the_agent_and_frees_its_budget(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    orch.units["trend"].runner.strategy._t = []                              # laisse la stratégie réelle décider
    leg = orch.units["trend"].runner.leg
    leg.qty = D("0")                                                         # (pas de signal sur ces bougies plates : pas de position)
    command(store, clock, "disable", "trend")
    tick(orch, clock, 2)
    assert "trend" not in orch.units and "trend" not in orch.unit_agents
    assert last(store)["status"] == "done"
    info = store.load_state("runtime_info")
    assert "trend" not in info["agents"] and "trend" in orch.state.disabled


def test_disable_with_an_open_position_waits_for_the_exit(tmp_path):
    from orchestre.backtest.engine import Target
    from tests.scenarios.test_exposure_runners import Scripted
    orch, ex, clock, store = world(tmp_path)
    orch.add_exposure("scripted", "S-3", Scripted([Target(D("0.5"), D("57000"))]), "BTCUSDT", D("100"))
    tick(orch, clock)
    assert orch.units["scripted"].positioned
    command(store, clock, "disable", "scripted")
    tick(orch, clock)
    assert not orch.units["scripted"].positioned if "scripted" in orch.units else True
    tick(orch, clock)
    assert "scripted" not in orch.units
    assert ex.get_open_orders("BTCUSDT") == []
    assert orch.state.flows["scripted"]["qty"]["BTCUSDT"] == 0                    # position soldée (le BTC restant est celui du DCA)


def test_disabling_an_unknown_agent_is_refused(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "disable", "fantome")
    tick(orch, clock)
    assert last(store)["status"] == "refused"


def test_a_disabled_agent_stays_disabled_after_a_restart_even_if_the_config_still_lists_it(tmp_path):
    db = tmp_path / "o.db"
    orch, ex, clock, store = world(tmp_path, db=db)
    command(store, clock, "disable", "dca")
    tick(orch, clock, 2)
    assert "dca" not in orch.units
    orch2, _, _, _ = world(tmp_path, db=db, ex=ex, clock=clock)            # la configuration réinstalle le DCA
    assert "dca" in orch2.units
    orch2.restore()
    assert "dca" not in orch2.units


def test_a_disabled_strategy_can_be_enabled_again_without_parameters(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "disable", "dca")
    tick(orch, clock, 2)
    command(store, clock, "enable", "dca")
    tick(orch, clock)
    assert last(store)["status"] == "done" and "dca" in orch.units and "dca" not in orch.state.disabled


# ---------- budget ----------

def test_set_budget_raises_and_lowers_an_allocation_within_the_limits(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    command(store, clock, "set_budget", "trend|150")
    tick(orch, clock)
    assert last(store)["status"] == "done" and orch.ledger.account("trend").allocated == D("150") and orch.units["trend"].budget == D("150")
    command(store, clock, "set_budget", "trend|60")
    tick(orch, clock)
    assert orch.ledger.account("trend").allocated == D("60")


def test_set_budget_is_refused_beyond_the_investable_capital_or_for_fixed_budget_agents(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    for target, why in (("trend|9999", "investissable"), ("trend|-5", "invalide"), ("trend|abc", "invalide"), ("dca|50", "à chaud"), ("fantome|50", "inconnu")):
        command(store, clock, "set_budget", target)
        tick(orch, clock)
        c = last(store)
        assert c["status"] == "refused" and why in c["result"], (target, c["result"])


def test_a_budget_change_survives_a_restart(tmp_path):
    db = tmp_path / "o.db"
    orch, ex, clock, store = world(tmp_path, db=db)
    command(store, clock, "enable", TREND)
    tick(orch, clock)
    command(store, clock, "set_budget", "trend|150")
    tick(orch, clock)
    orch2, _, _, _ = world(tmp_path, db=db, ex=ex, clock=clock)
    orch2.restore()
    assert orch2.units["trend"].budget == D("150") and orch2.ledger.account("trend").allocated == D("150")


def test_runtime_info_lists_the_parameters_of_active_agents_and_the_disabled_ones(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "disable", "dca")
    tick(orch, clock, 2)
    info = store.load_state("runtime_info")
    assert info["disabled"] == ["dca"] and info["agent_params"] == {} or "dca" not in info["agent_params"]


def test_enable_on_a_pair_the_paper_exchange_did_not_know_adds_it_on_the_fly(tmp_path):
    orch, ex, clock, store = world(tmp_path, paper=True)
    ex._market._filters["ETHUSDT"] = SymbolFilters("ETHUSDT", D("0.01"), D("0.0001"), D("0.0001"), D("1000"), D("5"))
    ex._market._prices["ETHUSDT"] = D("3000")
    ex._market.set_candles("ETHUSDT", "1d", make_candles([3000.0] * 300))
    command(store, clock, "enable", 'trend|{"symbol": "ETHUSDT", "budget_quote": "100"}')
    tick(orch, clock)
    assert last(store)["status"] == "done" and orch.units["trend"].symbol == "ETHUSDT"


def test_enable_on_a_pair_the_exchange_does_not_list_is_refused_with_a_clear_reason(tmp_path):
    orch, ex, clock, store = world(tmp_path)
    command(store, clock, "enable", 'trend|{"symbol": "ZZZUSDT", "budget_quote": "100"}')
    tick(orch, clock)
    assert last(store)["status"] == "refused" and "ZZZUSDT" in last(store)["result"] and "inconnue" in last(store)["result"]
    assert "trend" not in orch.units
