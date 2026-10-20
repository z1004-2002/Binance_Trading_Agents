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
                    "insert into tracked_orders (symbol, client_order_id, agent_id, side, committed, protective) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (row["symbol"], row["client_order_id"], row["agent_id"], row["side"], row["committed"],
                     int(row.get("protective", False))),
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
            "select symbol, client_order_id, agent_id, side, committed, protective from tracked_orders"
        )
        return [{**dict(r), "protective": bool(r["protective"])} for r in rows]

    def load_state(self, agent_id: str) -> dict | None:
        row = self.db.conn.execute(
            "select state_json from agent_state where agent_id = ?", (agent_id,)
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def save_state(self, agent_id: str, state: dict, now: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into agent_state (agent_id, state_json, updated_at) values (?, ?, ?) "
                "on conflict(agent_id) do update set state_json = excluded.state_json, updated_at = excluded.updated_at",
                (agent_id, _json(state), now.isoformat()),
            )

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

    # --- courbes d'équité (EX-DATA-02) ---
    def log_equity(self, ts: datetime, series: str, equity: float) -> None:
        with self.db.transaction() as c:
            c.execute("insert into equity_snapshots (ts, series, equity) values (?, ?, ?)", (ts.isoformat(), series, equity))

    def equity_series(self, series: str) -> list[tuple[str, float]]:
        rows = self.db.conn.execute(
            "select ts, equity from equity_snapshots where series = ? order by ts, id", (series,)
        )
        return [(r["ts"], r["equity"]) for r in rows]

    # --- journal des ordres ---
    def log_order(self, ts, agent_id, symbol, side, qty, price, fee, status, client_order_id, note) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into order_log (ts, agent_id, symbol, side, qty, price, fee, status, client_order_id, note) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts.isoformat(), agent_id, symbol, side, str(qty), str(price), str(fee), status, client_order_id,
                 mask_secrets(note)),
            )

    def recent_orders(self, limit: int = 100) -> list[dict]:
        rows = self.db.conn.execute("select * from order_log order by id desc limit ?", (limit,))
        return [dict(r) for r in rows]

    # --- commandes du tableau de bord vers le superviseur (EX-ARCH-02) ---
    def enqueue_command(self, ts: datetime, command: str, target: str | None = None) -> int:
        with self.db.transaction() as c:
            cur = c.execute("insert into commands (ts, command, target) values (?, ?, ?)", (ts.isoformat(), command, target))
            return cur.lastrowid

    def pending_commands(self) -> list[dict]:
        return [dict(r) for r in self.db.conn.execute("select * from commands where status = 'pending' order by id")]

    def finish_command(self, command_id: int, status: str, result: str, ts: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "update commands set status = ?, result = ?, processed_at = ? where id = ?",
                (status, mask_secrets(result), ts.isoformat(), command_id),
            )

    def recent_commands(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.conn.execute("select * from commands order by id desc limit ?", (limit,))]

    # --- propositions de l'assistant, validées par un humain (C-08) ---
    def add_proposal(self, ts: datetime, kind: str, title: str, payload: dict) -> int:
        with self.db.transaction() as c:
            cur = c.execute(
                "insert into proposals (ts, kind, title, payload_json) values (?, ?, ?, ?)",
                (ts.isoformat(), kind, mask_secrets(title), mask_secrets(json.dumps(payload, sort_keys=True, default=str))),
            )
            return cur.lastrowid

    def list_proposals(self, status: str | None = None) -> list[dict]:
        query, args = "select * from proposals", ()
        if status:
            query, args = query + " where status = ?", (status,)
        return [dict(r) for r in self.db.conn.execute(query + " order by id desc", args)]

    def decide_proposal(self, proposal_id: int, status: str, ts: datetime) -> bool:
        """Une proposition n'est décidée qu'une fois."""
        with self.db.transaction() as c:
            cur = c.execute(
                "update proposals set status = ?, decided_at = ? where id = ? and status = 'pending'",
                (status, ts.isoformat(), proposal_id),
            )
            return cur.rowcount == 1

    # --- file de questions pour l'assistant (le tableau de bord n'a pas la clé LLM, EX-UI-08) ---
    def enqueue_question(self, ts: datetime, question: str) -> int:
        with self.db.transaction() as c:
            cur = c.execute("insert into assistant_queue (ts, question) values (?, ?)", (ts.isoformat(), question))
            return cur.lastrowid

    def next_question(self) -> dict | None:
        row = self.db.conn.execute("select * from assistant_queue where status = 'pending' order by id limit 1").fetchone()
        return dict(row) if row else None

    def answer_question(self, question_id: int, answer: str | None, error: str | None, ts: datetime) -> None:
        with self.db.transaction() as c:
            c.execute(
                "update assistant_queue set status = ?, answer = ?, error = ?, answered_at = ? where id = ?",
                ("error" if error else "done", answer and mask_secrets(answer), error and mask_secrets(error),
                 ts.isoformat(), question_id),
            )

    def recent_questions(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.conn.execute("select * from assistant_queue order by id desc limit ?", (limit,))]

    # --- coût de l'assistant (EX-LLM-08, EX-LLM-10) ---
    def log_llm_call(self, ts, model, purpose, tokens_in, tokens_out, duration_ms, error) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into llm_calls (ts, model, purpose, tokens_in, tokens_out, duration_ms, error) values (?, ?, ?, ?, ?, ?, ?)",
                (ts.isoformat(), model, purpose, tokens_in, tokens_out, duration_ms, error and mask_secrets(error)),
            )

    def llm_tokens_for_month(self, now: datetime) -> int:
        month = f"{now.year:04d}-{now.month:02d}"
        row = self.db.conn.execute(
            "select coalesce(sum(tokens_in + tokens_out), 0) from llm_calls where substr(ts, 1, 7) = ?", (month,)
        ).fetchone()
        return int(row[0])

    # --- cycle de vie des stratégies (EX-LC-02) ---
    def log_lifecycle(self, ts: datetime, code: str, from_stage: str, to_stage: str, reason: str, decided_by: str) -> None:
        with self.db.transaction() as c:
            c.execute(
                "insert into lifecycle_events (ts, agent_code, from_stage, to_stage, reason, decided_by) values (?, ?, ?, ?, ?, ?)",
                (ts.isoformat(), code, from_stage, to_stage, mask_secrets(reason), decided_by),
            )

    def lifecycle_events(self, code: str | None = None) -> list[dict]:
        query, args = "select * from lifecycle_events", ()
        if code:
            query, args = query + " where agent_code = ?", (code,)
        return [dict(r) for r in self.db.conn.execute(query + " order by id desc", args)]

    def count(self, table: str) -> int:
        allowed = {
            "messages", "events", "agent_state", "ledger", "tracked_orders", "regimes", "volatility", "sentiment",
            "equity_snapshots", "order_log", "commands", "proposals", "assistant_queue", "llm_calls", "lifecycle_events",
        }
        if table not in allowed:
            raise ValueError("table inconnue")
        return self.db.conn.execute(f"select count(*) from {table}").fetchone()[0]
