"""Agent trace: every commission, tool call, draft, verdict, revision and decision, in order."""

from __future__ import annotations

import itertools
import json
import time
from typing import TYPE_CHECKING, Optional

from .contracts import AgentTraceEvent

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_trace (
    trace_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, storyline_id TEXT, commission_id TEXT,
    agent TEXT NOT NULL, action TEXT NOT NULL, tool TEXT, input_summary TEXT, output_summary TEXT,
    status TEXT NOT NULL, timestamp REAL NOT NULL, ms REAL
);
CREATE INDEX IF NOT EXISTS ix_trace_match ON agent_trace(match_id, timestamp);
"""


class Trace:
    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt
        with rt.store.lock:
            rt.store.conn.executescript(SCHEMA)
            rt.store.conn.commit()

    def emit(self, agent: str, action: str, status: str, *, commission_id: Optional[str] = None,
             storyline_id: Optional[str] = None, tool: Optional[str] = None, input_summary: str = "",
             output_summary: str = "", ms: Optional[float] = None) -> AgentTraceEvent:
        ev = AgentTraceEvent(trace_id=f"{self.rt.match_id}-t{next(_ids):05d}", match_id=self.rt.match_id,
                             storyline_id=storyline_id, commission_id=commission_id, agent=agent, action=action,
                             tool=tool, input_summary=input_summary[:400], output_summary=output_summary[:600],
                             status=status, ms=None if ms is None else round(ms, 1))
        self.rt.store.execute("INSERT INTO agent_trace VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (ev.trace_id, ev.match_id, ev.storyline_id, ev.commission_id, ev.agent, ev.action,
                               ev.tool, ev.input_summary, ev.output_summary, ev.status, ev.timestamp, ev.ms))
        self.rt.publish({"type": "trace", "trace": json.loads(ev.model_dump_json())})
        return ev

    def link(self, commission_id: str, storyline_id: str) -> None:
        """Attach a commission's earlier trace rows to the storyline it produced."""
        self.rt.store.execute("UPDATE agent_trace SET storyline_id = ? WHERE commission_id = ? AND storyline_id IS NULL",
                              (storyline_id, commission_id))

    def for_storyline(self, storyline_id: str) -> list[dict]:
        sl = self.rt.storylines.get(storyline_id) or {}
        ids = [storyline_id] + ([sl["supersedes"]] if sl.get("supersedes") else [])
        q = ",".join("?" * len(ids))
        return self.rt.store.query(f"SELECT * FROM agent_trace WHERE storyline_id IN ({q}) ORDER BY timestamp, trace_id",
                                   ids)

    def recent(self, limit: int = 200) -> list[dict]:
        rows = self.rt.store.query("SELECT * FROM agent_trace WHERE match_id = ? ORDER BY timestamp DESC, trace_id DESC "
                                   "LIMIT ?", (self.rt.match_id, limit))
        return list(reversed(rows))


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000
