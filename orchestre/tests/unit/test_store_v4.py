from datetime import datetime, timedelta, timezone

from orchestre.persistence.db import SCHEMA_VERSION, Database
from orchestre.persistence.store import Store

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def store():
    return Store(Database(":memory:"))


def test_schema_v4_tables_exist():
    s = store()
    assert SCHEMA_VERSION >= 4
    tables = {r[0] for r in s.db.conn.execute("select name from sqlite_master where type='table'")}
    assert {"equity_snapshots", "order_log", "commands", "proposals", "assistant_queue", "llm_calls"} <= tables


def test_equity_series_roundtrip_in_time_order():
    s = store()
    s.log_equity(T0 + timedelta(hours=1), "TEAM", 1010.5)
    s.log_equity(T0, "TEAM", 1000.0)
    s.log_equity(T0, "REF_BTCUSDT", 1000.0)
    assert [v for _, v in s.equity_series("TEAM")] == [1000.0, 1010.5]
    assert s.equity_series("REF_BTCUSDT")[0][1] == 1000.0 and s.equity_series("absent") == []


def test_order_log_keeps_the_latest_first_and_masks_nothing_sensitive():
    s = store()
    s.log_order(T0, "dca", "BTCUSDT", "BUY", "0.001", "60000", "0.06", "FILLED", "c1", "ordre envoyé")
    s.log_order(T0 + timedelta(minutes=1), "grid", "BTCUSDT", "SELL", "0.002", "61000", "0.12", "NEW", "c2", "")
    rows = s.recent_orders(10)
    assert [r["client_order_id"] for r in rows] == ["c2", "c1"] and rows[1]["status"] == "FILLED"


def test_commands_are_queued_then_finished_once():
    s = store()
    cid = s.enqueue_command(T0, "pause", "grid")
    assert [c["id"] for c in s.pending_commands()] == [cid] and s.pending_commands()[0]["target"] == "grid"
    s.finish_command(cid, "done", "agent grid en pause", T0)
    assert s.pending_commands() == []
    assert s.recent_commands(5)[0]["status"] == "done"


def test_proposals_are_pending_until_a_human_decides():
    s = store()
    pid = s.add_proposal(T0, "paramètre", "Réduire max_leverage", {"max_leverage": "1"})
    assert s.list_proposals("pending")[0]["title"] == "Réduire max_leverage"
    assert s.decide_proposal(pid, "accepted", T0) is True
    assert s.decide_proposal(pid, "rejected", T0) is False       # déjà décidée : pas de second changement
    assert s.list_proposals("pending") == [] and s.list_proposals()[0]["status"] == "accepted"


def test_assistant_queue_flow():
    s = store()
    qid = s.enqueue_question(T0, "Pourquoi le Grid s'est arrêté ?")
    nxt = s.next_question()
    assert nxt["id"] == qid and nxt["question"].startswith("Pourquoi")
    s.answer_question(qid, "Parce que le prix est sorti de la fourchette.", None, T0)
    assert s.next_question() is None and s.recent_questions(5)[0]["answer"].startswith("Parce que")


def test_llm_usage_is_summed_per_month():
    s = store()
    s.log_llm_call(T0, "m", "report", 100, 50, 800, None)
    s.log_llm_call(T0 + timedelta(days=1), "m", "ask", 200, 20, 900, None)
    s.log_llm_call(T0 - timedelta(days=40), "m", "ask", 5000, 5000, 900, None)    # autre mois : ignoré
    assert s.llm_tokens_for_month(T0) == 370
