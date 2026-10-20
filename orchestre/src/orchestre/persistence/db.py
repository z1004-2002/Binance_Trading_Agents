"""Base SQLite locale avec migrations versionnées (EX-DATA-01). Montants stockés en TEXT (A-5)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MIGRATIONS: list[str] = [
    """
    create table agent_state (
        agent_id text primary key,
        state_json text not null,
        updated_at text not null
    );
    create table ledger (
        agent_id text primary key,
        allocated text not null,
        committed text not null
    );
    create table ledger_meta (
        id integer primary key check (id = 1),
        total text not null
    );
    create table tracked_orders (
        symbol text not null,
        client_order_id text not null,
        agent_id text not null,
        side text not null,
        committed text not null,
        primary key (symbol, client_order_id)
    );
    create table events (
        id integer primary key autoincrement,
        ts text not null,
        level text not null,
        kind text not null,
        detail text not null
    );
    create table messages (
        message_id text primary key,
        ts text not null,
        sender text not null,
        topic text not null,
        performative text not null,
        reason text not null,
        content_json text not null
    );
    """,
    """
    create table regimes (
        id integer primary key autoincrement,
        ts text not null,
        symbol text not null,
        regime text not null,
        confidence real not null,
        indicators_json text not null
    );
    create table volatility (
        id integer primary key autoincrement,
        ts text not null,
        symbol text not null,
        atr real not null,
        realized real not null,
        forecast real not null
    );
    create table sentiment (
        id integer primary key autoincrement,
        ts text not null,
        value integer not null,
        classification text not null
    );
    """,
]
SCHEMA_VERSION = len(MIGRATIONS)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("pragma journal_mode = wal")
        self.conn.execute("pragma foreign_keys = on")
        self._migrate()

    def schema_version(self) -> int:
        return self.conn.execute("pragma user_version").fetchone()[0]

    def _migrate(self) -> None:
        for version in range(self.schema_version(), SCHEMA_VERSION):
            with self.transaction():
                for statement in filter(str.strip, MIGRATIONS[version].split(";")):
                    self.conn.execute(statement)
                self.conn.execute(f"pragma user_version = {version + 1}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("begin")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("rollback")
            raise
        else:
            self.conn.execute("commit")

    def close(self) -> None:
        self.conn.close()
