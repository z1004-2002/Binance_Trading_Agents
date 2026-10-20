import io
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from orchestre.cli.main import load_env_file, main
from orchestre.persistence.db import Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger
from tests.unit.test_binance_adapter import MARKET, StubClient

EXAMPLE = str(Path(__file__).parents[2] / "config" / "settings.example.toml")
KEYS = {"BINANCE_TESTNET_API_KEY": "kk-test", "BINANCE_TESTNET_API_SECRET": "ss-test"}


FNG = {"data": [{"value": "30", "value_classification": "Fear", "timestamp": "1700000000"}]}


def run(argv, environ=None, factory=None):
    if argv[0] in ("check-config", "check-keys", "run", "cancel-all", "analyze") and "--env-file" not in argv:
        argv = [*argv, "--env-file", "/nonexistent/.env"]  # ne jamais lire le vrai .env
    out, err = io.StringIO(), io.StringIO()
    # jamais de réseau dans les tests : la source du sentiment est toujours simulée
    code = main(argv, environ=environ or {}, out=out, err=err, client_factory=factory, fetch_fng=lambda: FNG)
    return code, out.getvalue(), err.getvalue()


def write_cfg(tmp_path, text):
    p = tmp_path / "s.toml"
    p.write_text(text)
    return str(p)


def test_check_config_prints_environment_banner():
    code, out, _ = run(["check-config", "--config", EXAMPLE])
    assert code == 0 and "TESTNET" in out and "dry_run" in out


def test_check_config_refuses_live_without_flag(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    code, _, err = run(["check-config", "--config", cfg])
    assert code == 2 and "--live" in err


def test_check_config_live_with_both_confirmations_warns(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    code, out, _ = run(["check-config", "--config", cfg, "--live"])
    assert code == 0 and "LIVE" in out and "ARGENT RÉEL" in out


def test_check_config_reports_invalid_file(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "moon"\n')
    code, _, err = run(["check-config", "--config", cfg])
    assert code == 2 and "env invalide" in err


def test_check_keys_testnet_connectivity_without_leaking_secrets():
    stub = StubClient()
    seen = {}

    def factory(env, key, secret, live_confirmed=False):
        seen.update(env=env, live=live_confirmed)
        return stub

    code, out, err = run(["check-keys", "--config", EXAMPLE], KEYS, factory)
    assert code == 0 and "BTCUSDT" in out
    assert "kk-test" not in out + err and "ss-test" not in out + err
    assert seen["live"] is False


def test_check_keys_missing_credentials():
    code, _, err = run(["check-keys", "--config", EXAMPLE], {}, lambda *a, **k: StubClient())
    assert code == 2 and "BINANCE_TESTNET_API_KEY" in err


def test_check_keys_network_error_is_masked():
    class Boom(StubClient):
        def load_markets(self):
            raise RuntimeError("401 apiKey=kk-test secret=ss-test")

    code, out, err = run(["check-keys", "--config", EXAMPLE], KEYS, lambda *a, **k: Boom())
    assert code == 4 and "kk-test" not in out + err and "ss-test" not in out + err


class LiveStub(StubClient):
    def __init__(self, restrictions):
        super().__init__()
        self.restrictions = restrictions

    def sapiGetAccountApiRestrictions(self):
        return self.restrictions


def live_args(tmp_path):
    cfg = write_cfg(tmp_path, 'env = "live"\nmax_total_capital_quote = "100"\n')
    env = {"BINANCE_LIVE_API_KEY": "lk", "BINANCE_LIVE_API_SECRET": "ls"}
    return ["check-keys", "--config", cfg, "--live"], env


GOOD = {
    "ipRestrict": True, "enableWithdrawals": False, "enableInternalTransfer": False,
    "permitsUniversalTransfer": False, "enableMargin": False, "enableFutures": False,
    "enableSpotAndMarginTrading": True,
}


def test_check_keys_live_accepts_safe_key(tmp_path):
    argv, env = live_args(tmp_path)
    code, out, _ = run(argv, env, lambda *a, **k: LiveStub(GOOD))
    assert code == 0 and "restrictions conformes" in out


def test_check_keys_live_refuses_dangerous_key(tmp_path):
    argv, env = live_args(tmp_path)
    bad = {**GOOD, "enableWithdrawals": True, "ipRestrict": False}
    code, _, err = run(argv, env, lambda *a, **k: LiveStub(bad))
    assert code == 3 and "retraits" in err and "restriction IP" in err


def test_live_client_receives_confirmation_only_with_flag(tmp_path):
    argv, env = live_args(tmp_path)
    flags = []

    def factory(e, k, s, live_confirmed=False):
        flags.append(live_confirmed)
        return LiveStub(GOOD)

    run(argv, env, factory)
    assert flags == [True]


def test_kill_creates_stop_file(tmp_path):
    stop = tmp_path / "var" / "STOP"
    code, out, _ = run(["kill", "--stop-file", str(stop)])
    assert code == 0 and stop.exists() and "STOP" in out


def test_status_without_database(tmp_path):
    code, out, _ = run(["status", "--db", str(tmp_path / "none.db")])
    assert code == 0 and "aucune base" in out


def test_status_shows_ledger(tmp_path):
    db = tmp_path / "o.db"
    lg = Ledger(D("500"))
    lg.allocate("grid", D("200"))
    lg.commit("grid", D("59.56"))
    Store(Database(db)).save_all(lg, [], {}, datetime(2026, 10, 20, tzinfo=timezone.utc))
    code, out, _ = run(["status", "--db", str(db)])
    assert code == 0 and "grid" in out and "59.56" in out and "300" in out


def test_env_file_does_not_override_real_environment(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nA=1\nB='deux'\n\nC = trois\nBAD LINE\n")
    env = {"A": "reel"}
    load_env_file(f, env)
    assert env == {"A": "reel", "B": "deux", "C": "trois"}


def test_missing_env_file_is_ignored(tmp_path):
    env = {}
    load_env_file(tmp_path / "absent", env)
    assert env == {}


# --- run / cancel-all ---

from tests.unit.test_binance_adapter import order  # noqa: E402


class RunStub(StubClient):
    restrictions = None
    open_orders_script = None

    def load_time_difference(self):
        return 0

    def sapiGetAccountApiRestrictions(self):
        return self.restrictions


def team_cfg(tmp_path, env="testnet", cap="500", extra=""):
    text = (
        f'env = "{env}"\nmax_total_capital_quote = "{cap}"\ndry_run = true\n{extra}\n'
        '[strategies.dca]\nenabled = true\nsymbol = "BTCUSDT"\namount_quote = "20"\n'
        'interval_days = 7\nstart = "2026-01-05T12:00:00+00:00"\ncumulative_cap = "100"\n'
    )
    return write_cfg(tmp_path, text)


def run_args(tmp_path, cfg, *extra):
    return ["run", "--config", cfg, "--db", str(tmp_path / "v" / "o.db"),
            "--stop-file", str(tmp_path / "v" / "STOP"), "--ticks", "1", "--interval", "0", *extra]


def test_run_one_dry_run_tick_creates_state(tmp_path):
    code, out, err = run(run_args(tmp_path, team_cfg(tmp_path)), KEYS, lambda *a, **k: RunStub())
    assert code == 0 and "Équipe : dca" in out and "arrêtée après 1 tick" in out
    from orchestre.persistence.db import Database as D_
    from orchestre.persistence.store import Store as S_
    store = S_(D_(tmp_path / "v" / "o.db"))
    assert store.load_ledger().account("dca").allocated == D("100")
    assert store.load_state("dca")["last_slot"] is not None  # le DCA a avancé son calendrier (dry-run)


def test_run_requires_a_positive_capital_cap(tmp_path):
    code, _, err = run(run_args(tmp_path, team_cfg(tmp_path, cap="0")), KEYS, lambda *a, **k: RunStub())
    assert code == 2 and "max_total_capital_quote" in err


def test_run_without_any_enabled_strategy_is_refused(tmp_path):
    cfg = write_cfg(tmp_path, 'max_total_capital_quote = "500"\n')
    code, _, err = run(run_args(tmp_path, cfg), KEYS, lambda *a, **k: RunStub())
    assert code == 2 and "aucune stratégie" in err


def test_run_refuses_a_changed_capital_cap_against_stored_ledger(tmp_path):
    run(run_args(tmp_path, team_cfg(tmp_path)), KEYS, lambda *a, **k: RunStub())
    code, _, err = run(run_args(tmp_path, team_cfg(tmp_path, cap="800")), KEYS, lambda *a, **k: RunStub())
    assert code == 2 and "plafond" in err


def test_run_stops_immediately_when_stop_file_exists(tmp_path):
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "STOP").write_text("")
    code, out, _ = run(run_args(tmp_path, team_cfg(tmp_path)), KEYS, lambda *a, **k: RunStub())
    assert code == 0 and "arrêtée après 1 tick" in out


LIVE_KEYS = {"BINANCE_LIVE_API_KEY": "lk", "BINANCE_LIVE_API_SECRET": "ls"}


def live_stub():
    stub = RunStub()
    stub.restrictions = GOOD
    return stub


def test_live_run_refused_without_written_decision(tmp_path):
    cfg = team_cfg(tmp_path, env="live")
    args = run_args(tmp_path, cfg, "--live", "--decision-file", str(tmp_path / "absent.md"))
    code, _, err = run(args, LIVE_KEYS, lambda *a, **k: live_stub())
    assert code == 5 and "décision écrite absente" in err


def test_live_run_refused_with_dangerous_key_even_with_decision(tmp_path):
    decision = tmp_path / "decision-live.md"
    decision.write_text("décision du 2026-10-20")
    stub = RunStub()
    stub.restrictions = {**GOOD, "enableWithdrawals": True}
    args = run_args(tmp_path, team_cfg(tmp_path, env="live"), "--live", "--decision-file", str(decision))
    code, _, err = run(args, LIVE_KEYS, lambda *a, **k: stub)
    assert code == 3 and "retraits" in err


def test_live_run_proceeds_only_with_decision_and_safe_key(tmp_path):
    decision = tmp_path / "decision-live.md"
    decision.write_text("décision du 2026-10-20")
    args = run_args(tmp_path, team_cfg(tmp_path, env="live"), "--live", "--decision-file", str(decision))
    code, out, _ = run(args, LIVE_KEYS, lambda *a, **k: live_stub())
    assert code == 0 and "ARGENT RÉEL" in out


def test_cancel_all_cancels_open_orders(tmp_path):
    stub = RunStub()
    stub.open_orders = [order(status="NEW", cid="a", price="59000")]
    stub.next_order = order(status="CANCELED", cid="x", extra={"origClientOrderId": "a"})
    code, out, _ = run(["cancel-all", "--config", EXAMPLE, "--symbol", "BTCUSDT"], KEYS, lambda *a, **k: stub)
    assert code == 0 and "1 ordre" in out


# --- analyze / analyse dans run ---

def test_analyze_prints_market_view_with_attribution(tmp_path):
    code, out, _ = run(["analyze", "--config", EXAMPLE, "--symbol", "BTCUSDT"], KEYS, lambda *a, **k: RunStub())
    assert code == 0
    assert "Régime" in out and "historique insuffisant" in out
    assert "Liquidité" in out and "liquide" in out
    assert "Sentiment" in out and "30" in out and "Alternative.me" in out


def test_analyze_works_without_keys_on_public_data():
    code, out, _ = run(["analyze", "--config", EXAMPLE], {}, lambda *a, **k: RunStub())
    assert code == 0 and "Liquidité" in out


def test_analyze_reports_unavailable_sentiment():
    out, err = io.StringIO(), io.StringIO()
    code = main(["analyze", "--config", EXAMPLE, "--env-file", "/nonexistent/.env"], environ={}, out=out, err=err,
                client_factory=lambda *a, **k: RunStub(), fetch_fng=lambda: (_ for _ in ()).throw(OSError("réseau")))
    assert code == 0 and "indisponible" in out.getvalue()


def test_run_with_analysis_logs_market_data(tmp_path):
    code, out, _ = run(run_args(tmp_path, team_cfg(tmp_path)), KEYS, lambda *a, **k: RunStub())
    assert code == 0
    from orchestre.persistence.db import Database as D_
    from orchestre.persistence.store import Store as S_
    assert S_(D_(tmp_path / "v" / "o.db")).count("sentiment") == 1


def test_run_without_analysis_flag_skips_market_analysis(tmp_path):
    code, _, _ = run(run_args(tmp_path, team_cfg(tmp_path), "--no-analysis"), KEYS, lambda *a, **k: RunStub())
    from orchestre.persistence.db import Database as D_
    from orchestre.persistence.store import Store as S_
    assert code == 0 and S_(D_(tmp_path / "v" / "o.db")).count("sentiment") == 0


# --- run --paper ---

def paper_args(tmp_path, cfg, *extra):
    return ["run", "--paper", "--config", cfg, "--db", str(tmp_path / "v" / "paper.db"),
            "--stop-file", str(tmp_path / "v" / "STOP"), "--ticks", "1", "--interval", "0", *extra]


def run_paper(argv, factory=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ={}, out=out, err=err, public_client_factory=factory or (lambda: RunStub()),
                client_factory=lambda *a, **k: (_ for _ in ()).throw(AssertionError("paper : aucune clé ni client privé")),
                fetch_fng=lambda: FNG)
    return code, out.getvalue(), err.getvalue()


def test_paper_run_needs_no_credentials_and_simulates_the_orders(tmp_path):
    code, out, err = run_paper(paper_args(tmp_path, team_cfg(tmp_path)))
    assert code == 0, err
    assert "MODE PAPER" in out and "Équipe : dca" in out
    from orchestre.persistence.db import Database as D_
    from orchestre.persistence.store import Store as S_
    store = S_(D_(tmp_path / "v" / "paper.db"))
    paper = store.load_state("paper_exchange")
    assert D(paper["balances"]["BTC"]) > 0                 # le DCA a « acheté » dans la simulation
    assert store.load_state("run_mode") == {"mode": "paper"}


def test_paper_run_resumes_without_buying_the_same_slot_twice(tmp_path):
    cfg = team_cfg(tmp_path)
    run_paper(paper_args(tmp_path, cfg))
    from orchestre.persistence.db import Database as D_
    from orchestre.persistence.store import Store as S_
    first = S_(D_(tmp_path / "v" / "paper.db")).load_state("paper_exchange")["balances"]["BTC"]
    code, _, err = run_paper(paper_args(tmp_path, cfg))
    assert code == 0, err
    assert S_(D_(tmp_path / "v" / "paper.db")).load_state("paper_exchange")["balances"]["BTC"] == first


def test_paper_and_live_flags_are_incompatible(tmp_path):
    cfg = team_cfg(tmp_path, env="live")
    code, _, err = run_paper(paper_args(tmp_path, cfg, "--live"))
    assert code == 2 and "incompatibles" in err


def test_a_database_is_never_shared_between_paper_and_exchange_modes(tmp_path):
    cfg = team_cfg(tmp_path)
    run_paper(paper_args(tmp_path, cfg))
    args = ["run", "--config", cfg, "--db", str(tmp_path / "v" / "paper.db"), "--stop-file", str(tmp_path / "v" / "STOP"),
            "--ticks", "1", "--interval", "0"]
    code, _, err = run(args, KEYS, lambda *a, **k: RunStub())
    assert code == 2 and "mode 'paper'" in err


def test_paper_run_without_any_strategy_is_refused(tmp_path):
    cfg = write_cfg(tmp_path, 'max_total_capital_quote = "500"\n')
    code, _, err = run_paper(paper_args(tmp_path, cfg))
    assert code == 2 and "aucune stratégie" in err


def test_critical_alerts_go_to_telegram_when_configured_and_never_when_not(tmp_path):
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "STOP").write_text("")                     # le kill switch déclenche une alerte CRITICAL
    sent = []
    env = {**KEYS, "TELEGRAM_BOT_TOKEN": "123456:ABCdefTOKENxyz_1234567890", "TELEGRAM_CHAT_ID": "42"}
    out, err = io.StringIO(), io.StringIO()
    code = main(run_args(tmp_path, team_cfg(tmp_path)) + ["--env-file", "/nonexistent/.env"], environ=env, out=out, err=err,
                client_factory=lambda *a, **k: RunStub(), fetch_fng=lambda: FNG,
                telegram_post=lambda url, payload: sent.append((url, payload)))
    assert code == 0 and sent and "kill switch" in sent[0][1]["text"] and sent[0][1]["chat_id"] == "42"
    assert "ABCdefTOKENxyz_1234567890" not in sent[0][1]["text"] + err.getvalue()

    sent.clear()
    main(run_args(tmp_path, team_cfg(tmp_path)) + ["--env-file", "/nonexistent/.env"], environ=KEYS, out=io.StringIO(),
         err=io.StringIO(), client_factory=lambda *a, **k: RunStub(), fetch_fng=lambda: FNG,
         telegram_post=lambda url, payload: sent.append(1))
    assert sent == []


# --- dashboard ---

def test_dashboard_check_needs_a_password_and_a_database(tmp_path):
    db = tmp_path / "o.db"
    Store_ = __import__("orchestre.persistence.store", fromlist=["Store"]).Store
    Database_ = __import__("orchestre.persistence.db", fromlist=["Database"]).Database
    Store_(Database_(db))
    base = ["dashboard", "--db", str(db), "--stop-file", str(tmp_path / "STOP"), "--port", "0", "--check", "--env-file", "/nonexistent/.env"]
    code, _, err = run(base, {})
    assert code == 2 and "DASHBOARD_PASSWORD" in err
    code, _, err = run(base, {"DASHBOARD_PASSWORD": "court"})
    assert code == 2 and "trop court" in err
    code, out, _ = run(base, {"DASHBOARD_PASSWORD": "mot-de-passe-solide"})
    assert code == 0 and "http://127.0.0.1:" in out
    missing = [*base]
    missing[2] = str(tmp_path / "absente.db")
    assert run(missing, {"DASHBOARD_PASSWORD": "mot-de-passe-solide"})[0] == 2


def test_the_dashboard_only_reads_its_own_password_from_the_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("BINANCE_LIVE_API_KEY=secretlive\nGROQ_API_KEY=gsk_secret\nDASHBOARD_PASSWORD=mot-de-passe-solide\n")
    env = {}
    load_env_file(f, env, only={"DASHBOARD_PASSWORD"})
    assert env == {"DASHBOARD_PASSWORD": "mot-de-passe-solide"}


# --- assistant Groq et actualités ---

import json as _json  # noqa: E402

GROQ_ENV = {"GROQ_API_KEY": "gsk_TESTKEYTESTKEYTESTKEYTESTKEY123", "GROQ_MODEL": "modele-choisi", "GROQ_MONTHLY_TOKEN_BUDGET": "100000"}


def groq_reply(content, tin=20, tout=10):
    return {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


def assistant_db(tmp_path):
    db = tmp_path / "a.db"
    Store_ = __import__("orchestre.persistence.store", fromlist=["Store"]).Store
    Database_ = __import__("orchestre.persistence.db", fromlist=["Database"]).Database
    store = Store_(Database_(db))
    store.save_all(__import__("orchestre.portfolio.ledger", fromlist=["Ledger"]).Ledger(D("500")), [], {"runtime_info": {
        "env": "testnet", "mode": "paper", "dry_run": False, "last_tick": "2026-10-20T12:00:00+00:00", "drawdown": 0.0,
        "daily_loss": 0.0, "tier": 0, "equity": 500.0, "capital": 500.0, "regime": "RANGE", "kill": False,
        "agents": {}, "codes": {}}}, datetime(2026, 10, 20, tzinfo=timezone.utc))
    return db, store


def run_assistant(argv, env, responses=None, news=None):
    posts = list(responses or [])
    calls = []

    def post(url, headers, body):
        calls.append(body)
        return posts.pop(0)

    out, err = io.StringIO(), io.StringIO()
    code = main(argv + ["--env-file", "/nonexistent/.env"], environ=env, out=out, err=err, groq_post=post,
                news_fetch=news, telegram_post=lambda *a: None)
    return code, out.getvalue(), err.getvalue(), calls


def test_assistant_ask_prints_the_answer_and_never_leaks_the_key(tmp_path):
    db, store = assistant_db(tmp_path)
    code, out, err, calls = run_assistant(["assistant", "ask", "Comment va l'équipe ?", "--db", str(db)], GROQ_ENV, [groq_reply("Tout va bien.")])
    assert code == 0 and "Tout va bien." in out and GROQ_ENV["GROQ_API_KEY"] not in out + err
    assert calls[0]["model"] == "modele-choisi"
    assert store.db.conn.execute("select sum(tokens_in + tokens_out), min(purpose) from llm_calls").fetchone()[:] == (30, "ask")


def test_assistant_needs_a_key_and_an_explicit_model(tmp_path):
    db, _ = assistant_db(tmp_path)
    code, _, err, _ = run_assistant(["assistant", "ask", "q", "--db", str(db)], {"GROQ_MODEL": "m"})
    assert code == 2 and "GROQ_API_KEY" in err
    code, _, err, _ = run_assistant(["assistant", "ask", "q", "--db", str(db)], {"GROQ_API_KEY": "gsk_x" * 8})
    assert code == 2 and "GROQ_MODEL" in err


def test_assistant_report_falls_back_without_llm_when_groq_fails(tmp_path):
    db, _ = assistant_db(tmp_path)
    def fail(*a):
        raise OSError("réseau")
    out, err = io.StringIO(), io.StringIO()
    code = main(["assistant", "report", "--db", str(db), "--env-file", "/nonexistent/.env"], environ=GROQ_ENV, out=out, err=err,
                groq_post=fail, telegram_post=lambda *a: None)
    assert code == 0 and "sans LLM" in out.getvalue() and "500" in out.getvalue()


def test_assistant_worker_answers_queued_questions(tmp_path):
    db, store = assistant_db(tmp_path)
    store.enqueue_question(datetime(2026, 10, 20, tzinfo=timezone.utc), "Où en est le drawdown ?")
    code, out, _, _ = run_assistant(["assistant", "worker", "--db", str(db), "--interval", "0", "--max-loops", "1"], GROQ_ENV,
                                    [groq_reply("Le drawdown est nul.")] * 3)
    assert code == 0
    assert store.recent_questions(1)[0]["answer"] == "Le drawdown est nul."


RSS_FEED = "<rss><channel><item><title>Grande plateforme piratée</title></item><item><title>BTC en hausse</title></item></channel></rss>"


def test_news_command_queues_a_pause_for_severe_events_only(tmp_path):
    db, store = assistant_db(tmp_path)
    classification = _json.dumps({"items": [
        {"index": 0, "asset": "BTC", "tone": -0.9, "category": "piratage", "severity": 5},
        {"index": 1, "asset": "BTC", "tone": 0.4, "category": "cotation", "severity": 1},
    ]})
    code, out, err, calls = run_assistant(["news", "--db", str(db), "--rss", "https://flux.example/rss", "--assets", "BTC,ETH"],
                                          GROQ_ENV, [groq_reply(classification)], news=lambda url: RSS_FEED)
    assert code == 0, err
    assert calls[0].get("tools") is None and calls[0]["response_format"] == {"type": "json_object"}
    pending = store.pending_commands()
    assert [(c["command"], c["target"].split("|")[0]) for c in pending] == [("pause_pair", "BTCUSDT")]
    assert "BTCUSDT" in out


def test_news_with_no_title_does_not_call_the_llm(tmp_path):
    db, _ = assistant_db(tmp_path)
    code, out, _, calls = run_assistant(["news", "--db", str(db), "--rss", "https://x/rss"], GROQ_ENV, [], news=lambda url: "pas du xml")
    assert code == 0 and calls == [] and "aucun titre" in out.lower()
