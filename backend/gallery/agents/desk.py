"""Desk: makes sure no key moment is missing from what goes on air.

At half-time and full-time it runs the deterministic coverage check (every goal, own goal and red card
must be covered by an approved story) and acts on each gap: commission a story, or tell the producer
which drafted story is waiting for approval.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from .contracts import StoryCommission
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)


class Desk:
    name = "Desk"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt

    async def review(self, phase: str) -> list[StoryCommission]:
        rt, timer = self.rt, Timer()
        cov = rt.coverage(2760 if phase == "half_time" else 1e9)
        commissions = []
        waiting = []
        for m in cov["missing"]:
            ins = next((i for i in rt.insights if m["event_id"] in i["evidence"]
                        and i["detector"] in ("goal", "red_card")), None)
            if ins is None:
                rt.trace.emit(self.name, f"gap: {m['label']} {m['minute']}'", "failed",
                              output_summary="no detected story covers this moment")
                continue
            live = [s for s in rt.storylines.values() if s["insight_id"] == ins["insight_id"] and s["market"] == "world"
                    and s["status"] in ("drafted", "edited", "needs_correction")]
            if live:
                waiting.append(live[0]["storyline_id"])
                rt.trace.emit(self.name, f"gap: {m['label']} {m['minute']}' ({m['player'] or m['team']})", "waiting",
                              storyline_id=live[0]["storyline_id"],
                              output_summary=f"story “{live[0]['headline']}” is drafted and waiting for producer "
                                             f"approval")
                continue
            c = StoryCommission(commission_id=f"{rt.match_id}-k{next(_ids):04d}", match_id=rt.match_id,
                                phase="coverage", insight_id=ins["insight_id"],
                                brief=f"Coverage gap: the {m['label'].lower()} at {m['minute']}' "
                                      f"({m['player'] or m['team']}) has no approved story. Explain it and its context.",
                                commissioned_by="desk", reason=f"{phase.replace('_', '-')} coverage check")
            rt.editor.commissioned.add(ins["insight_id"])
            commissions.append(c)
            rt.trace.emit(self.name, f"commissioned cover for {m['label']} {m['minute']}'", "succeeded",
                          commission_id=c.commission_id, input_summary=cov["summary"], output_summary=c.brief)
        rt.trace.emit(self.name, f"{phase.replace('_', '-')} coverage check", "succeeded",
                      tool="coverage_check", input_summary=f"{len(cov['moments'])} key moments",
                      output_summary=f"{cov['summary']}; {len(commissions)} commissioned, {len(waiting)} waiting for "
                                     f"the producer", ms=timer.ms)
        return commissions
