"""Analyst: gathers the evidence for a commission through read-only tools. Never reads raw data.

The brief it hands the Writer contains only facts computed by code, each with the event IDs behind it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .contracts import Claim, EvidenceBrief, StoryCommission
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

PROMPT = """You are the Analyst on a football production desk. You get a commission and evidence that
code has already gathered with tools. Pick which facts the Writer should lead with (by key) and add one
short note on context. Use only fact keys from the list. Do not calculate or invent numbers.
Return only JSON: {"lead_fact_keys": ["..."], "notes": "..."}"""


class Analyst:
    name = "Analyst"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt

    async def brief(self, c: StoryCommission) -> EvidenceBrief:
        rt, tb, timer = self.rt, self.rt.toolbox, Timer()
        calls = []
        ev = tb.get_insight_evidence(c.insight_id)
        calls.append(f"get_insight_evidence({c.insight_id})")
        rt.trace.emit(self.name, "queried evidence", "succeeded", commission_id=c.commission_id,
                      tool="get_insight_evidence", input_summary=c.insight_id,
                      output_summary=f"{len(ev['evidence_events'])} events, {len(ev['facts'])} facts: "
                                     + ", ".join(e["event_id"].split("-")[-1] for e in ev["evidence_events"][:6]))
        summary = tb.get_match_summary()
        calls.append("get_match_summary()")
        rt.trace.emit(self.name, "read match state", "succeeded", commission_id=c.commission_id,
                      tool="get_match_summary", output_summary=f"score {summary['score']}, minute {summary['minute']}"
                      + (f"; feed can't show {', '.join(summary['feed_cannot_show'])}"
                         if summary["feed_cannot_show"] else ""))
        window = tb.get_events_in_window(max(1, ev["minute"] - 5), ev["minute"])
        calls.append(f"get_events_in_window({max(1, ev['minute'] - 5)}, {ev['minute']})")
        rt.trace.emit(self.name, f"queried {max(1, ev['minute'] - 5)}'–{ev['minute']}' events", "succeeded",
                      commission_id=c.commission_id, tool="get_events_in_window",
                      output_summary=f"{len(window)} key events in the window")
        facts = [Claim(**f) for f in ev["facts"]]
        notes = ""
        if rt.provider.uses("analyst"):
            try:
                out = await rt.provider.chat_json([
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": json.dumps({"brief": c.brief, "facts": [f.model_dump() for f in facts],
                                                            "match": summary}, default=str)}], max_tokens=250)
                keys = [k for k in out.get("lead_fact_keys", []) if any(f.key == k for f in facts)]
                if keys:  # reorder: lead facts first; the set of facts never changes
                    facts = sorted(facts, key=lambda f: keys.index(f.key) if f.key in keys else len(keys))
                notes = str(out.get("notes", ""))[:300]
            except Exception as exc:
                rt.trace.emit(self.name, "note skipped", "retry", commission_id=c.commission_id,
                              output_summary=f"model error: {str(exc)[:120]}")
        b = EvidenceBrief(commission_id=c.commission_id, insight_id=c.insight_id, detector=ev["detector"],
                          team=ev["team"], minute=ev["minute"], facts=facts,
                          evidence_event_ids=[e["event_id"] for e in ev["evidence_events"]], tool_calls=calls,
                          notes=notes)
        rt.trace.emit(self.name, "evidence brief ready", "succeeded", commission_id=c.commission_id,
                      output_summary=f"{len(facts)} facts for the Writer" + (f" · note: {notes}" if notes else ""),
                      ms=timer.ms)
        return b
