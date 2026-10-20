"""Orchestrateur : journal d'ordres, courbes, rapport quotidien, refus du risque, commandes, état exposé."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.backtest.engine import Target
from orchestre.config import parse_settings
from orchestre.core.agent import AgentState
from orchestre.core.clock import SimulatedClock
from orchestre.exchange.fake import FakeExchange
from orchestre.exchange.filters import SymbolFilters
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from orchestre.runtime.orchestrator import Orchestrator, RuntimeLimits
from orchestre.strategies.dca import DcaConfig
from tests.scenarios.test_exposure_runners import Scripted
from tests.unit.analysis_data import make_candles

F = SymbolFilters("BTCUSDT", D("0.01"), D("0.00001"), D("0.00001"), D("100"), D("5"))
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
SETTINGS = parse_settings({"max_total_capital_quote": "500", "dry_run": False})
DCA = DcaConfig("dca", "BTCUSDT", D("20"), timedelta(days=7), T0, D("100"), D("5"))


def build(tmp_path, strategies=True, db=None, ex=None, clock=None):
    ex = ex or FakeExchange({"BTCUSDT": F}, {"BTCUSDT": D("60000")}, {"USDT": D("2000")})
    if not ex.get_candles("BTCUSDT", "1d", 1):
        ex.set_candles("BTCUSDT", "1d", make_candles([60000.0] * 300))
    clock = clock or SimulatedClock(T0)
    store = Store(Database(db or ":memory:"))
    orch = Orchestrator(SETTINGS, ex, store.load_ledger() or Ledger(D("500")), store, clock, tmp_path / "STOP",
                        limits=RuntimeLimits(reconcile_interval=timedelta(0), max_order_notional=D("150")))
    if strategies:
        orch.add_dca(DCA)
        orch.add_exposure("trend", "S-3", Scripted([Target(D("0.5"), D("57000"))]), "BTCUSDT", D("200"))
    return orch, ex, clock, store


def tick(orch, clock, minutes=20):
    clock.advance(timedelta(minutes=minutes))
    return orch.run_once()


def test_every_order_event_is_journaled_with_its_agent(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    rows = store.recent_orders(50)
    by_agent = {(r["agent_id"], r["side"], r["status"]) for r in rows}
    assert ("dca", "BUY", "FILLED") in by_agent and ("trend", "BUY", "FILLED") in by_agent
    assert ("trend", "SELL", "NEW") in by_agent                  # le stop de protection posé


def test_a_stop_fill_is_journaled_too(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    ex.set_price("BTCUSDT", D("56900"))
    tick(orch, clock)
    assert any(r["agent_id"] == "trend" and r["side"] == "SELL" and r["status"] == "FILLED" for r in store.recent_orders(50))


def test_equity_curves_are_sampled_hourly_with_a_btc_reference(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    tick(orch, clock)                          # 20 min plus tard : pas de nouvel instantané
    assert len(store.equity_series("TEAM")) == 1 and len(store.equity_series("REF_BTCUSDT")) == 1
    ex.set_price("BTCUSDT", D("66000"))        # +10 %
    tick(orch, clock, minutes=65)
    ref = store.equity_series("REF_BTCUSDT")
    assert len(ref) == 2 and ref[1][1] == 1.1 * ref[0][1] or abs(ref[1][1] / ref[0][1] - 1.1) < 1e-9
    assert len(store.equity_series("agent:trend")) == 2


def test_a_daily_report_is_sent_once_per_day_change(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    assert not [a for a in orch.alerts.history if a[0] == "REPORT"]           # premier jour : rien à rapporter
    clock.advance(timedelta(hours=13))                                          # lendemain 01:00
    orch.run_once()
    reports = [text for level, text in orch.alerts.history if level == "REPORT"]
    assert len(reports) == 1 and "Rapport du 2026-10-20" in reports[0] and "capital" in reports[0].lower()
    orch.run_once()
    assert len([a for a in orch.alerts.history if a[0] == "REPORT"]) == 1


def test_risk_rejections_are_logged_once_each(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    orch.ledger.commit("trend", D("195"))                                       # budget épuisé -> R-03
    tick(orch, clock)
    tick(orch, clock)
    details = [r["detail"] for r in store.db.conn.execute("select detail from events where kind = 'risk'")]
    trend = [d for d in details if d.startswith("trend ")]
    assert trend and all("R-03" in d for d in trend)
    assert len(trend) == len(orch.units["trend"].runner.rejections)          # une ligne par refus, jamais en double
    assert all(" refusé par R-" in d for d in details)


def test_runtime_info_is_exposed_for_the_dashboard(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    orch.run_mode = "paper"
    tick(orch, clock)
    info = store.load_state("runtime_info")
    assert info["env"] == "testnet" and info["mode"] == "paper" and info["dry_run"] is False
    assert info["agents"] == {"dca": "RUNNING", "trend": "RUNNING"}
    assert info["tier"] == 0 and info["last_tick"] == clock.now().isoformat() and info["kill"] is False


def test_pause_and_resume_commands_from_the_dashboard(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    cid = store.enqueue_command(clock.now(), "pause", "trend")
    tick(orch, clock)
    assert orch.unit_agents["trend"].state is AgentState.PAUSED and orch.unit_agents["dca"].state is AgentState.RUNNING
    assert store.recent_commands(1)[0]["id"] == cid and store.recent_commands(1)[0]["status"] == "done"
    store.enqueue_command(clock.now(), "resume", "trend")
    tick(orch, clock)
    assert orch.unit_agents["trend"].state is AgentState.RUNNING


def test_pause_all_and_unknown_targets(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    store.enqueue_command(clock.now(), "pause", "all")
    store.enqueue_command(clock.now(), "pause", "fantome")
    store.enqueue_command(clock.now(), "withdraw", None)
    tick(orch, clock)
    assert all(a.state is AgentState.PAUSED for a in orch.unit_agents.values())
    statuses = [c["status"] for c in store.recent_commands(3)]
    assert statuses.count("refused") == 2 and "done" in statuses


def test_kill_command_stops_the_team_and_creates_the_stop_file(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    store.enqueue_command(clock.now(), "kill", None)
    assert tick(orch, clock) == "KILLED"
    assert (tmp_path / "STOP").exists() and store.recent_commands(1)[0]["status"] == "done"


def test_resume_is_refused_while_the_kill_switch_is_active(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    store.enqueue_command(clock.now(), "kill", None)
    tick(orch, clock)
    store.enqueue_command(clock.now(), "resume", "all")
    tick(orch, clock)
    last = store.recent_commands(1)[0]
    assert last["status"] == "refused" and "kill" in last["result"].lower()


# --- pause d'une paire sur actualité grave (réduction de risque seulement) ---

def test_a_paused_pair_refuses_new_purchases_but_not_protective_exits(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)                                                         # position ouverte + stop
    store.enqueue_command(clock.now(), "pause_pair", "BTCUSDT|piratage (gravité 5) : plateforme piratée")
    tick(orch, clock)
    assert orch.sentiment_state.is_paused("BTCUSDT")
    assert store.load_state("runtime_info")["paused_pairs"] == {"BTCUSDT": "piratage (gravité 5) : plateforme piratée"}

    leg = orch.units["trend"].runner.leg
    qty_before = leg.qty
    orch.units["trend"].runner.strategy._t = [Target(D("0.8"), D("57000"))]     # la stratégie voudrait acheter davantage
    new_candle(ex)
    tick(orch, clock)
    assert leg.qty == qty_before and any(d.rule_id == "R-08" for d in orch.units["trend"].runner.rejections)
    ex.set_price("BTCUSDT", D("56900"))                                           # le stop de protection reste actif
    tick(orch, clock)
    assert leg.qty == 0


def new_candle(ex, symbol="BTCUSDT"):
    c = ex.get_candles(symbol, "1d", 1000)
    ex.set_candles(symbol, "1d", make_candles([float(x.close) for x in c] + [float(c[-1].close)]))


def test_resume_pair_and_unknown_pairs(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    store.enqueue_command(clock.now(), "pause_pair", "XYZUSDT|test")          # paire inconnue de l'équipe
    store.enqueue_command(clock.now(), "pause_pair", "BTCUSDT|test")
    store.enqueue_command(clock.now(), "resume_pair", "BTCUSDT")
    tick(orch, clock)
    statuses = [c["status"] for c in reversed(store.recent_commands(3))]
    assert statuses == ["refused", "done", "done"] and not orch.sentiment_state.is_paused("BTCUSDT")


def test_paused_pairs_survive_a_restart(tmp_path):
    db = tmp_path / "o.db"
    orch, ex, clock, store = build(tmp_path, db=db)
    store.enqueue_command(clock.now(), "pause_pair", "BTCUSDT|piratage")
    tick(orch, clock)
    orch2, _, _, _ = build(tmp_path, db=db, ex=ex, clock=clock)
    orch2.restore()
    assert orch2.sentiment_state.is_paused("BTCUSDT")


# --- autocontrôle quotidien (EX-SU-03) et remise à zéro du testnet (EX-ENV-02) ---

def alerts_text(orch):
    return " | ".join(f"{lvl}:{txt}" for lvl, txt in orch.alerts.history)


def test_the_self_check_runs_once_per_day_and_reports_ok_when_everything_is_consistent(tmp_path):
    orch, ex, clock, store = build(tmp_path)
    tick(orch, clock)
    assert orch.self_check_problems == [] and any(
        r["kind"] == "self_check" and "OK" in r["detail"] for r in store.db.conn.execute("select * from events"))
    n = store.db.conn.execute("select count(*) from events where kind = 'self_check'").fetchone()[0]
    tick(orch, clock)
    assert store.db.conn.execute("select count(*) from events where kind = 'self_check'").fetchone()[0] == n   # pas deux fois le même jour
    clock.advance(timedelta(days=1))
    orch.run_once()
    assert store.db.conn.execute("select count(*) from events where kind = 'self_check'").fetchone()[0] == n + 1


def test_a_corrupted_ledger_is_detected(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    tick(orch, clock)
    orch.ledger.account("trend").committed = orch.ledger.account("trend").allocated + D("5")      # corruption volontaire
    problems = orch.self_check()
    assert any("registre" in p for p in problems)


def test_a_position_without_a_protective_stop_is_critical(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    tick(orch, clock)
    leg = orch.units["trend"].runner.leg
    assert leg.qty > 0
    leg.stop_order_id = None                                                                    # le stop a disparu
    problems = orch.self_check()
    assert any("sans stop" in p and "trend" in p for p in problems)
    assert any(level == "CRITICAL" and "sans stop" in text for level, text in orch.alerts.history)


def test_low_disk_space_is_reported(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(10**12, 10**12 - 50 * 10**6, 50 * 10**6))
    orch, ex, clock, _ = build(tmp_path)
    assert any("disque" in p for p in orch.self_check())


def test_tracked_orders_missing_from_the_exchange_look_like_a_testnet_reset(tmp_path):
    orch, ex, clock, _ = build(tmp_path)
    tick(orch, clock)
    for req in [*ex._stops.values(), *ex._resting.values()]:                                    # remise à zéro : fonds rendus,
        base = req.symbol.removesuffix("USDT")                                                   # mais plus aucun ordre connu
        if req.side.value == "SELL":
            ex._balances[base] = ex._balances.get(base, D("0")) + req.quantity
    ex._orders.clear()
    ex._stops.clear()
    ex._resting.clear()
    assert tick(orch, clock) == "PAUSED"
    text = alerts_text(orch)
    assert "remise à zéro du testnet" in text
    assert all(a.state is AgentState.PAUSED for a in orch.unit_agents.values())
