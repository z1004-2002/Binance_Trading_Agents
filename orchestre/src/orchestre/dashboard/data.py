"""Lecture seule de la base pour le tableau de bord (EX-UI-08). Aucune clé, aucun accès au marché."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from orchestre.analysis.regimes import Regime
from orchestre.metrics.performance import max_drawdown, sharpe, sortino, total_return
from orchestre.portfolio.allocator import MULTIPLIERS


class DashboardData:
    def __init__(self, db_path, stop_file, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._path, self._stop_file, self._clock = Path(db_path), Path(stop_file), clock

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=5)
            conn.execute("select 1 from sqlite_master limit 1")
        except sqlite3.OperationalError:
            conn = sqlite3.connect(f"file:{self._path}?immutable=1", uri=True, timeout=5)   # base fermée en mode WAL
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self, query: str, args: tuple = ()) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(query, args)]
        except sqlite3.OperationalError:
            return []      # table absente : l'équipe n'a pas encore tourné
        finally:
            conn.close()

    def state(self, agent_id: str) -> dict | None:
        rows = self._rows("select state_json from agent_state where agent_id = ?", (agent_id,))
        return json.loads(rows[0]["state_json"]) if rows else None

    # --- vues ---
    def info(self) -> dict:
        info = self.state("runtime_info") or {}
        if info.get("last_tick"):
            age = (self._clock() - datetime.fromisoformat(info["last_tick"])).total_seconds()
            info["tick_age"] = max(0, int(age))
        info["kill"] = bool(info.get("kill")) or self._stop_file.exists()
        return info

    def ledger(self) -> dict:
        meta = self._rows("select total from ledger_meta where id = 1")
        accounts = [
            {"agent_id": r["agent_id"], "allocated": float(r["allocated"]), "committed": float(r["committed"]),
             "available": float(r["allocated"]) - float(r["committed"])}
            for r in self._rows("select * from ledger order by agent_id")
        ]
        total = float(meta[0]["total"]) if meta else 0.0
        return {"total": total, "unallocated": total - sum(a["allocated"] for a in accounts), "accounts": accounts}

    def agent_ids(self) -> list[str]:
        ids = {a["agent_id"] for a in self.ledger()["accounts"]} | set((self.info().get("agents") or {}))
        return sorted(ids)

    def events(self, level: str | None = None, kind: str | None = None, limit: int = 200) -> list[dict]:
        query, args = "select * from events", []
        clauses = []
        if level:
            clauses.append("level = ?")
            args.append(level)
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if clauses:
            query += " where " + " and ".join(clauses)
        return self._rows(query + " order by id desc limit ?", (*args, limit))

    def alerts(self, limit: int = 10) -> list[dict]:
        return self._rows(
            "select * from events where level in ('WARNING','ERROR','CRITICAL') order by id desc limit ?", (limit,)
        )

    def series(self, name: str) -> list[tuple[str, float]]:
        return [(r["ts"], r["equity"]) for r in self._rows("select ts, equity from equity_snapshots where series = ? order by ts, id", (name,))]

    def reference_series_names(self) -> list[str]:
        return [r["series"] for r in self._rows("select distinct series from equity_snapshots where series like 'REF_%' order by series")]

    def agents(self) -> list[dict]:
        info = self.info()
        codes, states = info.get("codes") or {}, info.get("agents") or {}
        last = {r["agent_id"]: r for r in self._rows("select * from order_log where id in (select max(id) from order_log group by agent_id)")}
        rows = []
        for acc in self.ledger()["accounts"]:
            a = acc["agent_id"]
            rows.append({**acc, "code": codes.get(a, ""), "state": states.get(a, "INCONNU"),
                         "summary": self._summary(a), "last_order": last.get(a)})
        return rows

    def _summary(self, agent_id: str) -> str:
        s = self.state(agent_id)
        if not s:
            return "—"
        if "leg" in s:
            leg = s["leg"]
            return f"position {leg['qty']}" + (f", stop {leg['stop_price']}" if leg.get("stop_price") else ", sans stop")
        if "legs" in s:
            held = {k: v["qty"] for k, v in s["legs"].items() if float(v["qty"]) > 0}
            return "positions " + ", ".join(f"{k} {q}" for k, q in held.items()) if held else "aucune position"
        if "total_spent" in s:
            return f"dépensé {s['total_spent']}"
        if "cycles" in s:
            return f"{s['cycles']} cycles, profit {s['profit']}, {len(s.get('live', []))} ordres de grille"
        return "—"

    def allocation(self) -> dict:
        info = self.info()
        regime = info.get("regime")
        codes = info.get("codes") or {}
        multipliers = {}
        if regime:
            for agent, code in codes.items():
                if code in MULTIPLIERS:
                    multipliers[agent] = float(MULTIPLIERS[code][Regime(regime)])
        return {"ledger": self.ledger(), "regime": regime, "multipliers": multipliers, "codes": codes}

    def market(self) -> dict:
        regimes = self._rows("select * from regimes where id in (select max(id) from regimes group by symbol) order by symbol")
        vols = {r["symbol"]: r for r in self._rows("select * from volatility where id in (select max(id) from volatility group by symbol)")}
        senti = self._rows("select * from sentiment order by id desc limit 1")
        return {"regimes": regimes, "volatility": vols, "sentiment": senti[0] if senti else None}

    def open_orders(self) -> list[dict]:
        return self._rows("select * from tracked_orders order by symbol, client_order_id")

    def orders(self, limit: int = 100) -> list[dict]:
        return self._rows("select * from order_log order by id desc limit ?", (limit,))

    def proposals(self, status: str | None = None) -> list[dict]:
        if status:
            return self._rows("select * from proposals where status = ? order by id desc", (status,))
        return self._rows("select * from proposals order by id desc limit 50")

    def questions(self, limit: int = 20) -> list[dict]:
        return self._rows("select * from assistant_queue order by id desc limit ?", (limit,))

    def commands(self, limit: int = 10) -> list[dict]:
        return self._rows("select * from commands order by id desc limit ?", (limit,))

    def experiment(self) -> dict:
        team = self.series("TEAM")
        out = {"team": team, "refs": {n: self.series(n) for n in self.reference_series_names()}, "metrics": {}}
        for name, points in [("TEAM", team), *out["refs"].items()]:
            daily = list({ts[:10]: value for ts, value in points}.values())      # dernière valeur de chaque jour (UTC)
            if len(daily) >= 3 and daily[0] > 0:
                out["metrics"][name] = {
                    "days": len(daily) - 1, "return": total_return(daily), "drawdown": max_drawdown(daily),
                    "sharpe": sharpe(daily), "sortino": sortino(daily),
                }
        return out
