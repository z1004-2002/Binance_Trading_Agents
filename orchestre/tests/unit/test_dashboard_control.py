"""Tableau de bord : catalogue, pilotage des agents depuis l'interface, jumeau numérique."""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from orchestre.dashboard.catalog import CATALOG, BY_ID, FormError, parse_form
from orchestre.dashboard.server import DashboardServer
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from tests.unit.test_dashboard import NOW, PASSWORD, T0, env, logged  # noqa: F401


def commands(store):
    return [(c["command"], c["target"]) for c in store.pending_commands()]


# ---------- catalogue ----------

def test_the_catalog_lists_every_strategy_including_the_ones_not_built(env):
    server, *_ = env
    page = logged(server).request("GET", "/agents")[2]
    for code in ("S-1", "S-2", "S-3", "S-4", "S-5", "S-6", "S-7", "S-8", "S-9a", "S-9b", "S-10", "S-11", "S-12", "S-13", "S-14"):
        assert code in page, code
    assert page.count("Non implémenté") == 6                                       # A-6 (UniverseAgent) et S-10 à S-14


def test_a_not_built_agent_explains_why(env):
    server, *_ = env
    page = logged(server).request("GET", "/agent/futures_trend")[2]
    assert "Non implémenté" in page and "futures" in page and "name=\"target\"" not in page


def test_the_legacy_agent_cannot_be_enabled_from_the_interface(env):
    server, store, _ = env
    c = logged(server)
    assert "Configuration uniquement" in c.request("GET", "/agent/legacy")[2]
    c.post("/command", {"command": "enable", "target": "legacy"})
    assert commands(store) == []


def test_unknown_agent_page_is_404(env):
    server, *_ = env
    assert logged(server).request("GET", "/agent/fantome")[0] == 404


def test_an_active_agent_page_has_controls_and_an_inactive_one_has_the_enable_form(env):
    server, *_ = env
    c = logged(server)
    active = c.request("GET", "/agent/dca")[2]
    assert 'value="disable"' in active and 'value="pause"' in active or 'value="resume"' in active
    inactive = c.request("GET", "/agent/breakout")[2]
    assert 'value="enable"' in inactive and 'name="budget_quote"' in inactive and 'name="symbol"' in inactive


# ---------- commandes de pilotage ----------

def test_enable_builds_a_validated_target(env):
    server, store, _ = env
    c = logged(server)
    status, headers, _ = c.post("/command", {"command": "enable", "target": "trend", "symbol": "ethusdt", "budget_quote": "80", "back": "/agent/trend"})
    assert status == 303 and headers["Location"].startswith("/agent/trend")
    (command, target), = commands(store)
    name, _, raw = target.partition("|")
    assert command == "enable" and name == "trend" and json.loads(raw) == {"symbol": "ETHUSDT", "budget_quote": "80"}


def test_enable_refuses_bad_fields_without_queueing_anything(env):
    server, store, _ = env
    c = logged(server)
    for bad in ({"symbol": "BTC/USDT; drop", "budget_quote": "80"}, {"symbol": "BTCUSDT", "budget_quote": "-5"},
                {"symbol": "BTCUSDT", "budget_quote": "abc"}, {"symbol": "BTCUSDT", "budget_quote": "NaN"},
                {"symbol": "BTCUSDT", "budget_quote": "1e30"}, {"symbol": "BTCUSDT"}, {"symbol": "", "budget_quote": "5"}):
        c.post("/command", {"command": "enable", "target": "trend", **bad})
    assert commands(store) == []


def test_enable_cannot_smuggle_extra_parameters(env):
    server, store, _ = env
    logged(server).post("/command", {"command": "enable", "target": "trend", "symbol": "BTCUSDT", "budget_quote": "50",
                                     "promoted": "true", "path": "/etc/passwd"})
    (_, target), = commands(store)
    assert json.loads(target.partition("|")[2]) == {"symbol": "BTCUSDT", "budget_quote": "50"}


def test_disable_and_set_budget_are_queued_for_known_agents_only(env):
    server, store, _ = env
    c = logged(server)
    c.post("/command", {"command": "disable", "target": "dca"})
    c.post("/command", {"command": "set_budget", "target": "trend", "amount": "150"})
    c.post("/command", {"command": "disable", "target": "fantome"})
    c.post("/command", {"command": "set_budget", "target": "trend", "amount": "-1"})
    c.post("/command", {"command": "set_budget", "target": "trend", "amount": "x"})
    c.post("/command", {"command": "set_budget", "target": "risk", "amount": "10"})          # un agent du cœur n'a pas de budget
    assert commands(store) == [("disable", "dca"), ("set_budget", "trend|150")]


def test_the_redirect_target_is_restricted_to_known_pages(env):
    server, *_ = env
    status, headers, _ = logged(server).post("/command", {"command": "pause", "target": "dca", "back": "https://evil.example/x"})
    assert status == 303 and headers["Location"].startswith("/agents")


def test_forms_reject_a_missing_csrf_token(env):
    server, store, _ = env
    c = logged(server)
    status, _, _ = c.request("POST", "/command", {"command": "disable", "target": "dca"})
    assert status == 403 and commands(store) == []


def test_parse_form_covers_every_field_kind():
    assert parse_form(BY_ID["rotation"], {"symbols": ["btcusdt, ethusdt"], "budget_quote": ["90"], "top_k": ["2"]}) == {
        "symbols": ["BTCUSDT", "ETHUSDT"], "budget_quote": "90", "top_k": 2}
    assert parse_form(BY_ID["rebalance"], {"weights": ["BTCUSDT:0.5,ETHUSDT:0.3"], "budget_quote": ["90"]})["weights"] == {"BTCUSDT": "0.5", "ETHUSDT": "0.3"}
    assert parse_form(BY_ID["triangular"], {"a": ["btc"], "b": ["eth"]}) == {"a": "BTC", "b": "ETH"}
    for entry, bad in ((BY_ID["rebalance"], {"weights": ["BTCUSDT:0.8,ETHUSDT:0.5"], "budget_quote": ["9"]}),
                       (BY_ID["rotation"], {"symbols": ["BTCUSDT,BTCUSDT"], "budget_quote": ["9"]}),
                       (BY_ID["dca"], {"symbol": ["BTCUSDT"], "amount_quote": ["5"], "interval_days": ["0"], "cumulative_cap": ["9"]}),
                       (BY_ID["triangular"], {"a": ["B T C"], "b": ["ETH"]})):
        try:
            parse_form(entry, bad)
        except FormError:
            continue
        raise AssertionError(f"{entry.id} aurait dû refuser {bad}")


def test_every_controllable_strategy_is_a_known_engine_strategy():
    from orchestre.runtime.builder import KNOWN
    assert {e.id for e in CATALOG if e.controllable} <= set(KNOWN)


# ---------- style et sécurité ----------

def test_pages_stay_free_of_inline_styles_and_external_resources(env):
    server, *_ = env
    c = logged(server)
    for path in ("/", "/agents", "/agent/trend", "/agent/breakout", "/twins", "/allocation", "/market", "/orders", "/help"):
        status, _, page = c.request("GET", path)
        assert status == 200, path
        assert " style=" not in page and "<style" not in page and "onclick" not in page, path
        assert not re.search(r'(src|href)="https?://', page), path


def test_the_theme_is_light_only():
    from orchestre.dashboard.assets import CSS
    assert "prefers-color-scheme:dark" not in CSS and "color-scheme:light" in CSS


# ---------- jumeau numérique ----------

def make_twin(tmp_path, live_equity=495.0, twin_equity=480.0):
    db = tmp_path / "twin.db"
    store = Store(Database(db))
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D("100"))
    store.save_all(ledger, [], {"runtime_info": {"env": "testnet", "mode": "paper", "last_tick": T0.isoformat(), "equity": twin_equity,
                                                 "agents": {"dca": "RUNNING"}, "codes": {"dca": "S-1"}}}, T0)
    for day in range(4):
        store.log_equity(T0 + timedelta(days=day), "TEAM", 500 - 5 * day)
        store.log_equity(T0 + timedelta(days=day), "agent:dca", 100 - day)
    store.log_order(T0, "dca", "BTCUSDT", "BUY", "0.0003", "60000", "0.05", "FILLED", "t1", "ordre envoyé")
    return db


def test_the_twins_page_explains_how_to_start_one_when_absent(env):
    server, *_ = env
    page = logged(server).request("GET", "/twins")[2]
    assert "Aucun jumeau" in page and "--twin" in page


def test_the_twins_page_compares_live_and_twin(env, tmp_path):
    server, store, base = env
    twin = DashboardServer(base / "o.db", base / "STOP", PASSWORD, port=0, clock=lambda: NOW, kdf_iterations=1000, twin_db=make_twin(base))
    import threading
    threading.Thread(target=twin.serve_forever, daemon=True).start()
    try:
        page = logged(twin).request("GET", "/twins")[2]
        assert "Équipe jumelle" in page and "480.00" in page and "495.00" in page
        assert "-3.03%" in page or "+3.03%" in page                                # (495 - 480) / 495
        assert 'href="/agent/dca"' in page and "<svg" in page
        assert "jumeau" in logged(twin).request("GET", "/agent/dca")[2]            # la courbe de l'agent montre aussi le jumeau
    finally:
        twin.shutdown()


def test_piloting_commands_are_mirrored_to_the_twin_but_not_proposals(env):
    server, store, base = env
    twin_db = make_twin(base)
    twin = DashboardServer(base / "o.db", base / "STOP", PASSWORD, port=0, clock=lambda: NOW, kdf_iterations=1000, twin_db=twin_db)
    import threading
    threading.Thread(target=twin.serve_forever, daemon=True).start()
    try:
        c = logged(twin)
        c.post("/command", {"command": "pause", "target": "dca"})
        c.post("/command", {"command": "set_budget", "target": "trend", "amount": "90"})
        assert commands(store) == [("pause", "dca"), ("set_budget", "trend|90")]
        assert commands(Store(Database(twin_db))) == commands(store)
    finally:
        twin.shutdown()
