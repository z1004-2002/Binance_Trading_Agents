"""Sauvegarde et reprise de l'état de l'équipe (A-7). Une sauvegarde = une transaction atomique."""

from __future__ import annotations

import json
from datetime import datetime

from orchestre.core.messages import Message
from orchestre.core.secrets import mask_secrets
from orchestre.persistence.db import Database
from orchestre.portfolio.ledger import Ledger


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True)  # TypeError si non sérialisable : refus explicite


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save_all(
        self,
        ledger: Ledger,
        tracked_orders: list[dict],
        agent_states: dict[str, dict],
        now: datetime,
    ) -> None:
        payloads = {aid: _json(state) for aid, state in agent_states.items()}
        with self.db.transaction() as c:
            snap = ledger.snapshot()
            c.execute("delete from ledger")
            c.execute("delete from ledger_meta")
            c.execute("insert into ledger_meta (id, total) values (1, ?)", (snap["total"],))
            for aid, acc in snap["accounts"].items():
                c.execute(
                    "insert into ledger (agent_id, allocated, committed) values (?, ?, ?)",
                    (aid, acc["allocated"], acc["committed"]),
                )
            c.execute("delete from tracked_orders")
            for row in tracked_orders:
                c.execute(
                    "insert into tracked_orders (symbol, client_order_id, agent_id, side, committed) "
                    "values (?, ?, ?, ?, ?)",
                    (row["symbol"], row["client_order_id"], row["agent_id"], row["side"], row["committed"]),
                )
            for aid, payload in payloads.items():
                c.execute(
                    "insert into agent_state (agent_id, state_json, updated_at) values (?, ?, ?) "
                    "on conflict(agent_id) do update set "
                    "state_json = excluded.state_json, updated_at = excluded.updated_at",
                    (aid, payload, now.isoformat()),
                )

    def load_ledger(self) -> Ledger | None:
        meta = self.db.conn.execute("select total from ledger_meta where id = 1").fetchone()
        if meta is None:
            return None
        accounts = {
            r["agent_id"]: {"allocated": r["allocated"], "committed": r["committed"]}
            for r in self.db.conn.execute("select * from ledger")
        }
        return Ledger.from_snapshot({"total": meta["total"], "accounts": accounts})

    def load_tracked(self) -> list[dict]:
        rows = self.db.conn.execute(
            "select symbol, client_order_id, agent_id, side, committed from tracked_orders"
        )
        return [dict(r) for r in rows]

    def load_state(self, agent_id: str) -> dict | None:
        row = self.db.conn.execute(
            "select state_json from agent_state where agent_id = ?", (agent_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def log_event(self, ts: datetime, level: str, kind: str, detail: str) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into events (ts, level, kind, detail) values (?, ?, ?, ?)",
                (ts.isoformat(), level, kind, mask_secrets(detail)),
            )

    def log_message(self, m: Message) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert or ignore into messages "
                "(message_id, ts, sender, topic, performative, reason, content_json) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                (
                    m.message_id, m.timestamp.isoformat(), m.sender, m.topic,
                    m.performative.value, mask_secrets(m.reason),
                    mask_secrets(json.dumps(m.content, default=str, sort_keys=True)),
                ),
            )

    def log_regime(self, ts: datetime, symbol: str, regime: str, confidence: float, indicators: dict) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into regimes (ts, symbol, regime, confidence, indicators_json) values (?, ?, ?, ?, ?)",
                (ts.isoformat(), symbol, regime, confidence, json.dumps(indicators, sort_keys=True)),
            )

    def log_volatility(self, ts: datetime, symbol: str, atr: float, realized: float, forecast: float) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into volatility (ts, symbol, atr, realized, forecast) values (?, ?, ?, ?, ?)",
                (ts.isoformat(), symbol, atr, realized, forecast),
            )

    def log_sentiment(self, ts: datetime, value: int, classification: str) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into sentiment (ts, value, classification) values (?, ?, ?)",
                (ts.isoformat(), value, classification),
            )

    def count(self, table: str) -> int:
        allowed = {"messages", "events", "agent_state", "ledger", "tracked_orders", "regimes", "volatility", "sentiment"}
        if table not in allowed:
            raise ValueError("table inconnue")
        return self.db.conn.execute(f"select count(*) from {table}").fetchone()[0]
