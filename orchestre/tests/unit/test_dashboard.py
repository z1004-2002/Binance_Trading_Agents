import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.dashboard.data import DashboardData
from orchestre.dashboard.server import DashboardServer
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger

PASSWORD = "mot-de-passe-solide"
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(seconds=30)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self, port):
        self.base, self.cookie, self.csrf = f"http://127.0.0.1:{port}", None, None
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, data=None, headers=None):
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers=h, method=method)
        try:
            r = self.opener.open(req, timeout=5)
            return r.status, dict(r.headers), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    def login(self, password=PASSWORD):
        status, headers, _ = self.request("POST", "/login", {"password": password})
        if status == 303:
            self.cookie = headers["Set-Cookie"].split(";")[0]
            _, _, page = self.request("GET", "/")
            marker = 'name="csrf" value="'
            self.csrf = page.split(marker)[1].split('"')[0] if marker in page else None
        return status

    def post(self, path, data):
        return self.request("POST", path, {**data, "csrf": self.csrf})


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "o.db"
    store = Store(Database(db))
    ledger = Ledger(D("500"))
    ledger.allocate("dca", D("100"))
    ledger.allocate("trend", D("200"))
    ledger.commit("dca", D("20"))
    store.save_all(ledger, [], {
        "runtime_info": {"env": "testnet", "mode": "paper", "dry_run": False, "last_tick": (T0).isoformat(), "drawdown": 0.06,
                         "daily_loss": 0.01, "tier": 1, "equity": 495.0, "capital": 500.0, "regime": "RANGE",
                         "fear_greed": 30, "kill": False, "agents": {"dca": "RUNNING", "trend": "PAUSED"},
                         "codes": {"dca": "S-1", "trend": "S-3"}},
        "trend": {"leg": {"qty": "0.001", "net_spent": "60", "wanted_stop": "57000", "stop_price": "57000",
                          "stop_order_id": "x", "stop_qty": "0.001", "seq": 3}, "strategy": {}, "last": None},
        "dca": {"last_slot": T0.isoformat(), "total_spent": "20"},
    }, T0)
    store.log_event(T0, "CRITICAL", "alert", "agent grid <script>alert(1)</script> silencieux")
    store.log_event(T0, "INFO", "risk", "trend refusé par R-03 : budget insuffisant")
    store.log_order(T0, "dca", "BTCUSDT", "BUY", "0.0003", "60000", "0.02", "FILLED", "c1", "ordre envoyé")
    store.log_regime(T0, "BTCUSDT", "RANGE", 0.7, {"adx": 15.0})
    store.log_volatility(T0, "BTCUSDT", 1200.0, 0.55, 0.60)
    store.log_sentiment(T0, 30, "Fear")
    for day in range(8):
        t = T0 + timedelta(days=day)
        store.log_equity(t, "TEAM", 1000 + 5 * day)
        store.log_equity(t, "REF_BTCUSDT", 1000 + (-1) ** day * 20 * day)
    server = DashboardServer(db, tmp_path / "STOP", PASSWORD, port=0, clock=lambda: NOW, kdf_iterations=1000)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store, tmp_path
    server.shutdown()


def logged(server):
    c = Client(server.port)
    assert c.login() == 303 and c.csrf
    return c


# --- accès et sécurité ---

def test_everything_requires_a_login(env):
    server, *_ = env
    c = Client(server.port)
    for path in ("/", "/agents", "/orders", "/journal", "/api/status"):
        status, headers, _ = c.request("GET", path)
        assert status == 303 and headers["Location"] == "/login"


def test_wrong_password_is_refused_and_attempts_are_rate_limited(env):
    server, *_ = env
    c = Client(server.port)
    statuses = [c.login("mauvais") for _ in range(6)]
    assert statuses[:5] == [401] * 5 and statuses[5] == 429
    assert c.login(PASSWORD) == 429                       # même le bon mot de passe est bloqué pendant la pause


def test_session_cookie_is_httponly_and_samesite_strict(env):
    server, *_ = env
    status, headers, _ = Client(server.port).request("POST", "/login", {"password": PASSWORD})
    assert status == 303 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]


def test_security_headers_and_no_external_resources(env):
    server, *_ = env
    c = logged(server)
    status, headers, body = c.request("GET", "/")
    assert status == 200
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY" and headers["Cache-Control"] == "no-store"
    assert "http://" not in body.replace("http://127.0.0.1", "") and "https://" not in body and "<script>" not in body
    assert 'style="' not in body and "onclick" not in body          # la CSP interdit les styles et scripts en ligne


def test_foreign_host_headers_are_rejected(env):
    server, *_ = env
    status, _, _ = logged(server).request("GET", "/", headers={"Host": "evil.example.com"})
    assert status == 400


def test_the_dashboard_refuses_to_listen_on_a_public_interface(tmp_path):
    Store(Database(tmp_path / "o.db"))
    with pytest.raises(ValueError, match="127.0.0.1"):
        DashboardServer(tmp_path / "o.db", tmp_path / "STOP", PASSWORD, host="0.0.0.0", port=0)


def test_a_weak_or_empty_password_is_refused(tmp_path):
    Store(Database(tmp_path / "o.db"))
    for bad in ("", "court"):
        with pytest.raises(ValueError, match="mot de passe"):
            DashboardServer(tmp_path / "o.db", tmp_path / "STOP", bad, port=0)


def test_static_assets_are_served_but_nothing_outside_them(env):
    server, *_ = env
    c = Client(server.port)
    assert c.request("GET", "/static/app.css")[0] == 200 and c.request("GET", "/static/app.js")[0] == 200
    assert c.request("GET", "/static/../../etc/passwd")[0] == 404 and c.request("GET", "/static/inconnu.js")[0] == 404


def test_the_database_connection_used_for_display_is_read_only(env):
    server, store, tmp_path = env
    conn = DashboardData(tmp_path / "o.db", tmp_path / "STOP")._connect()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("insert into events (ts, level, kind, detail) values ('x','x','x','x')")
    conn.close()


# --- pages ---

def test_overview_shows_the_environment_banner_capital_and_alerts(env):
    server, *_ = env
    status, _, page = logged(server).request("GET", "/")
    assert status == 200
    assert "PAPER" in page and "500" in page and "495" in page and "RANGE" in page
    assert "tier-1" in page or "palier 1" in page.lower()


def test_untrusted_text_from_the_database_is_escaped(env):
    server, *_ = env
    page = logged(server).request("GET", "/journal")[2]
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_agents_page_lists_states_positions_and_last_orders(env):
    server, *_ = env
    page = logged(server).request("GET", "/agents")[2]
    assert "dca" in page and "trend" in page and "RUNNING" in page and "PAUSED" in page
    assert "0.001" in page and "BUY" in page


def test_allocation_page_shows_budgets_and_regime_multipliers(env):
    server, *_ = env
    page = logged(server).request("GET", "/allocation")[2]
    assert "200" in page and "RANGE" in page and "0.5" in page          # S-3 en RANGE : x0,5


def test_market_page_shows_regime_volatility_and_sentiment_with_attribution(env):
    server, *_ = env
    page = logged(server).request("GET", "/market")[2]
    assert "BTCUSDT" in page and "RANGE" in page and "55.0%" in page and "Alternative.me" in page and "Fear" in page


def test_orders_and_risk_pages(env):
    server, *_ = env
    c = logged(server)
    assert "c1" in c.request("GET", "/orders")[2] and "R-03" in c.request("GET", "/risk")[2]


def test_experiment_page_compares_the_team_with_the_reference_and_computes_metrics(env):
    server, *_ = env
    page = logged(server).request("GET", "/experiment")[2]
    assert "<svg" in page and "REF_BTCUSDT" in page and "Sortino" in page and "Drawdown" in page


def test_help_page_explains_the_vocabulary_in_french(env):
    server, *_ = env
    page = logged(server).request("GET", "/help")[2]
    for term in ("Drawdown", "Régime", "Sortino", "Kill switch", "Paper"):
        assert term in page


def test_status_api_is_json_for_the_header_refresh(env):
    server, *_ = env
    status, headers, body = logged(server).request("GET", "/api/status")
    data = json.loads(body)
    assert status == 200 and data["mode"] == "paper" and data["tier"] == 1 and data["kill"] is False
    assert data["tick_age"] == 30


# --- commandes ---

def test_post_without_the_csrf_token_is_forbidden(env):
    server, store, _ = env
    c = logged(server)
    status, _, _ = c.request("POST", "/command", {"command": "pause", "target": "dca"})
    assert status == 403 and store.pending_commands() == []


def test_pause_resume_are_queued_for_the_supervisor(env):
    server, store, _ = env
    c = logged(server)
    assert c.post("/command", {"command": "pause", "target": "trend"})[0] == 303
    assert c.post("/command", {"command": "resume", "target": "all"})[0] == 303
    assert [(x["command"], x["target"]) for x in store.pending_commands()] == [("pause", "trend"), ("resume", "all")]


def test_kill_needs_the_word_stop_typed_in_full(env):
    server, store, _ = env
    c = logged(server)
    c.post("/command", {"command": "kill", "target": "", "confirm": "stop"})
    c.post("/command", {"command": "kill", "target": "", "confirm": ""})
    assert store.pending_commands() == []
    c.post("/command", {"command": "kill", "target": "", "confirm": "STOP"})
    assert [x["command"] for x in store.pending_commands()] == ["kill"]


def test_only_pause_resume_and_kill_exist_and_only_for_known_agents(env):
    server, store, _ = env
    c = logged(server)
    for bad in ({"command": "withdraw", "target": "dca"}, {"command": "buy", "target": "dca"},
                {"command": "pause", "target": "fantome"}, {"command": "pause; drop table", "target": "dca"}):
        c.post("/command", bad)
    assert store.pending_commands() == []


def test_proposals_are_decided_by_a_human_once(env):
    server, store, _ = env
    pid = store.add_proposal(T0, "paramètre", "Réduire top_k à 2", {"top_k": 2})
    page = logged(server).request("GET", "/proposals")[2]
    assert "Réduire top_k à 2" in page
    c = logged(server)
    assert c.post("/proposal", {"id": str(pid), "decision": "accept"})[0] == 303
    assert store.list_proposals()[0]["status"] == "accepted"
    c.post("/proposal", {"id": str(pid), "decision": "reject"})
    assert store.list_proposals()[0]["status"] == "accepted"            # une décision est définitive


def test_questions_are_queued_for_the_assistant_worker_and_answers_are_shown(env):
    server, store, _ = env
    c = logged(server)
    assert c.post("/ask", {"question": "Pourquoi trend est en pause ?"})[0] == 303
    assert store.next_question()["question"] == "Pourquoi trend est en pause ?"
    c.post("/ask", {"question": "x" * 2000})
    assert len(store.recent_questions(5)) == 1                            # question trop longue refusée
    qid = store.next_question()["id"]
    store.answer_question(qid, "Parce qu'un humain l'a mise en pause.", None, T0)
    assert "Parce qu&#x27;un humain" in c.request("GET", "/assistant")[2] or "Parce qu'un humain" in c.request("GET", "/assistant")[2]


def test_logout_invalidates_the_session(env):
    server, *_ = env
    c = logged(server)
    assert c.post("/logout", {})[0] == 303
    assert c.request("GET", "/")[0] == 303


def test_the_dashboard_never_sees_exchange_or_llm_secrets(env):
    server, *_ = env
    import orchestre.dashboard.server as module
    import inspect
    source = inspect.getsource(module) + inspect.getsource(__import__("orchestre.dashboard.data", fromlist=["x"]))
    for forbidden in ("BINANCE_", "GROQ_", "API_KEY", "ccxt", "create_client"):
        assert forbidden not in source


def test_a_human_can_resume_a_paused_pair_from_the_market_page(env):
    server, store, _ = env
    info = store.load_state("runtime_info")
    info["paused_pairs"] = {"BTCUSDT": "piratage (gravité 5)"}
    store.save_state("runtime_info", info, T0)
    c = logged(server)
    page = c.request("GET", "/market")[2]
    assert "Paires en pause" in page and "piratage" in page and "Reprendre" in page
    c.post("/command", {"command": "resume_pair", "target": "ETHUSDT"})            # paire non en pause : refusée
    assert store.pending_commands() == []
    c.post("/command", {"command": "resume_pair", "target": "BTCUSDT"})
    assert [(x["command"], x["target"]) for x in store.pending_commands()] == [("resume_pair", "BTCUSDT")]


def test_the_dashboard_cannot_pause_a_pair_only_resume_it(env):
    server, store, _ = env
    logged(server).post("/command", {"command": "pause_pair", "target": "BTCUSDT"})
    assert store.pending_commands() == []


def test_every_page_complies_with_the_csp_without_inline_styles_or_scripts(env):
    server, *_ = env
    c = logged(server)
    for path in ("/", "/agents", "/allocation", "/market", "/orders", "/risk", "/experiment", "/proposals", "/assistant", "/journal", "/help"):
        body = c.request("GET", path)[2]
        assert 'style="' not in body and "onclick=" not in body and "<script>" not in body, path
