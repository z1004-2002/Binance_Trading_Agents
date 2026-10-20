from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from orchestre.core.messages import Message, Performative
from orchestre.persistence.db import SCHEMA_VERSION, Database
from orchestre.persistence.store import Store
from orchestre.portfolio.ledger import Ledger

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_migrations_create_schema_and_are_idempotent(tmp_path):
    path = tmp_path / "o.db"
    Database(path).close()
    db = Database(path)  # deuxième ouverture : ne rejoue rien
    assert db.schema_version() == SCHEMA_VERSION
    tables = {r[0] for r in db.conn.execute("select name from sqlite_master where type='table'")}
    assert {"agent_state", "ledger", "tracked_orders", "events", "messages"} <= tables


def test_ledger_roundtrip_is_exact():
    lg = Ledger(D("500.10"))
    lg.allocate("dca", D("100.1"))
    lg.commit("dca", D("33.333333"))
    store = Store(Database(":memory:"))
    store.save_all(lg, [], {}, T0)
    back = store.load_ledger()
    assert back.total == D("500.10")
    assert back.account("dca").allocated == D("100.1")
    assert back.account("dca").committed == D("33.333333")
    assert back.unallocated == lg.unallocated


def test_load_ledger_is_none_on_empty_database():
    assert Store(Database(":memory:")).load_ledger() is None


def test_save_all_is_atomic(tmp_path):
    db = Database(tmp_path / "o.db")
    store = Store(db)
    lg = Ledger(D("500"))
    lg.allocate("a", D("10"))
    store.save_all(lg, [], {"a": {"x": 1}}, T0)

    lg2 = Ledger(D("999"))
    with pytest.raises(TypeError):
        store.save_all(lg2, [], {"a": {"bad": object()}}, T0)  # non sérialisable
    assert store.load_ledger().total == D("500")
    assert store.load_state("a") == {"x": 1}


def test_tracked_orders_are_replaced_not_accumulated():
    store = Store(Database(":memory:"))
    row = {"symbol": "BTCUSDT", "client_order_id": "L1", "agent_id": "g", "side": "BUY", "committed": "59.56"}
    store.save_all(Ledger(D("1")), [row], {}, T0)
    store.save_all(Ledger(D("1")), [], {}, T0)
    assert store.load_tracked() == []


def test_agent_state_roundtrip_and_overwrite():
    store = Store(Database(":memory:"))
    store.save_all(Ledger(D("1")), [], {"grid": {"cycles": 1}}, T0)
    store.save_all(Ledger(D("1")), [], {"grid": {"cycles": 2}}, T0)
    assert store.load_state("grid") == {"cycles": 2}
    assert store.load_state("absent") is None


def test_messages_and_events_are_journaled():
    store = Store(Database(":memory:"))
    m = Message("risk", "risk", Performative.REJECT_PROPOSAL, {"rule": "R-03"}, "budget", T0, timedelta(seconds=5))
    store.log_message(m)
    store.log_message(m)  # idempotent sur message_id
    store.log_event(T0, "WARNING", "kill_switch", "STOP détecté")
    assert store.count("messages") == 1 and store.count("events") == 1


def test_secrets_are_masked_in_events():
    store = Store(Database(":memory:"))
    store.log_event(T0, "ERROR", "auth", "échec avec apiKey=ABCD1234SECRET et secret=zzz")
    detail = store.db.conn.execute("select detail from events").fetchone()[0]
    assert "ABCD1234SECRET" not in detail and "zzz" not in detail
