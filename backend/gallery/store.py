"""SQLite store. Events live here so that every claim can be re-computed with plain SQL.

The schema is deliberately portable to PostgreSQL (no SQLite-only types).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

from .events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    home_id TEXT NOT NULL, away_id TEXT NOT NULL,
    home_name TEXT NOT NULL, away_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    t REAL NOT NULL, period INTEGER NOT NULL, minute INTEGER NOT NULL,
    team_id TEXT NOT NULL, type TEXT NOT NULL, outcome TEXT,
    player TEXT, x REAL, y REAL, end_x REAL, end_y REAL, xg REAL,
    source TEXT NOT NULL, clock REAL
);
CREATE INDEX IF NOT EXISTS ix_events_match_t ON events(match_id, t);
CREATE TABLE IF NOT EXISTS insights (
    insight_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL, detector TEXT NOT NULL,
    t REAL NOT NULL, from_t REAL NOT NULL, to_t REAL NOT NULL,
    team_id TEXT NOT NULL, players TEXT NOT NULL, metrics TEXT NOT NULL,
    evidence TEXT NOT NULL, confidence REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS storylines (
    storyline_id TEXT PRIMARY KEY,
    insight_id TEXT NOT NULL, match_id TEXT NOT NULL, market TEXT NOT NULL,
    headline TEXT NOT NULL, segments TEXT NOT NULL, graphic TEXT NOT NULL,
    clip_cue TEXT NOT NULL, writer TEXT NOT NULL, status TEXT NOT NULL,
    created_at REAL NOT NULL, latency_ms REAL
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    storyline_id TEXT NOT NULL, seg_idx INTEGER NOT NULL,
    text TEXT NOT NULL, metric TEXT, params TEXT, op TEXT, value REAL,
    actual REAL, verdict TEXT NOT NULL, reason TEXT, sql TEXT, rows TEXT,
    injected INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    storyline_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
    before TEXT, after TEXT, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS match_stats (
    match_id TEXT NOT NULL, team_id TEXT NOT NULL, stat TEXT NOT NULL, value REAL,
    source TEXT NOT NULL, PRIMARY KEY (match_id, team_id, stat)
);
CREATE TABLE IF NOT EXISTS packages (
    package_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL, kind TEXT NOT NULL, market TEXT NOT NULL,
    body TEXT NOT NULL, built_ms REAL NOT NULL, created_at REAL NOT NULL
);
"""

EVENT_COLS = ["event_id", "match_id", "t", "period", "minute", "team_id", "type", "outcome",
              "player", "x", "y", "end_x", "end_y", "xg", "source", "clock"]


class Store:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---- generic helpers -------------------------------------------------
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.lock:
            self.conn.execute(sql, tuple(params))
            self.conn.commit()

    # ---- matches ---------------------------------------------------------
    def create_match(self, match_id: str, home_id: str, away_id: str, home_name: str,
                     away_name: str, mode: str, meta: Optional[dict] = None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?)",
            (match_id, home_id, away_id, home_name, away_name, mode, json.dumps(meta or {}), time.time()),
        )

    def get_match(self, match_id: str) -> Optional[dict[str, Any]]:
        rows = self.query("SELECT * FROM matches WHERE match_id = ?", (match_id,))
        if not rows:
            return None
        m = rows[0]
        m["meta"] = json.loads(m["meta"])
        return m

    def list_matches(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM matches ORDER BY created_at DESC")
        for r in rows:
            r["meta"] = json.loads(r["meta"])
        return rows

    def delete_match_data(self, match_id: str) -> None:
        with self.lock:
            sl = [r["storyline_id"] for r in self.query(
                "SELECT storyline_id FROM storylines WHERE match_id = ?", (match_id,))]
            for sid in sl:
                self.conn.execute("DELETE FROM claims WHERE storyline_id = ?", (sid,))
                self.conn.execute("DELETE FROM decisions WHERE storyline_id = ?", (sid,))
            for table in ("events", "insights", "storylines", "packages", "match_stats"):
                self.conn.execute(f"DELETE FROM {table} WHERE match_id = ?", (match_id,))
            try:   # created by the agent trace; ids restart with the process, so old rows must go
                self.conn.execute("DELETE FROM agent_trace WHERE match_id = ?", (match_id,))
            except sqlite3.OperationalError:
                pass
            self.conn.commit()

    # ---- events ----------------------------------------------------------
    def add_event(self, e: Event) -> None:
        d = e.to_dict()
        self.execute(
            f"INSERT INTO events ({','.join(EVENT_COLS)}) VALUES ({','.join('?' * len(EVENT_COLS))})",
            [d[c] for c in EVENT_COLS],
        )

    def events(self, match_id: str, since_t: float = 0.0, until_t: float = 1e9) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM events WHERE match_id = ? AND t >= ? AND t <= ? ORDER BY t, event_id",
            (match_id, since_t, until_t),
        )

    def events_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return self.query(f"SELECT * FROM events WHERE event_id IN ({q}) ORDER BY t", ids)

    def delete_event(self, event_id: str) -> None:
        self.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
