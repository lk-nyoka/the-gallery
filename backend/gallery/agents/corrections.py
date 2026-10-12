"""Corrections: when the data changes, approved coverage is never silently overwritten.

Affected approved stories are already marked `needs_correction` by living verification. This agent
re-computes the story's facts from the corrected data and commissions a replacement draft. The new
draft goes through verification and needs the producer's approval; only then is the old version
marked `superseded`. Both versions stay in the audit trail.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from .contracts import StoryCommission
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)
ATTRIBUTION_KEYS = {"scorer", "own_goal_by", "sent_off", "shooter", "regain_player"}


class Corrections:
    name = "Corrections"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt

    async def handle(self, flagged_ids: list[str], reason: str) -> list[StoryCommission]:
        rt, timer = self.rt, Timer()
        out = []
        for sid in flagged_ids:
            sl = rt.storylines.get(sid)
            if not sl:
                continue
            ins = rt.insight(sl["insight_id"])
            old = {f["key"]: f["value"] for f in ins["facts"]}
            fresh = rt.toolbox.refresh_facts(ins["insight_id"])
            changed = {f.key: (old.get(f.key), f.value) for f in fresh if old.get(f.key) != f.value}
            ins["facts"] = [f.model_dump() for f in fresh]       # the insight now reflects the corrected data
            for k, (_, new) in changed.items():
                if k in ATTRIBUTION_KEYS and isinstance(new, str):
                    if ins["detector"] in ("goal", "red_card", "turning_point"):
                        ins["players"] = [new]
            rt.trace.emit(self.name, f"re-computed facts for “{sl['headline']}”", "succeeded", storyline_id=sid,
                          tool="refresh_facts", input_summary=reason,
                          output_summary=(", ".join(f"{k}: {a} → {b}" for k, (a, b) in changed.items())
                                          or "no fact changed; re-drafting to re-verify the wording"))
            c = StoryCommission(commission_id=f"{rt.match_id}-r{next(_ids):04d}", match_id=rt.match_id,
                                phase="correction", insight_id=ins["insight_id"], market=sl["market"],
                                brief=f"Correction: {reason}. Rewrite “{sl['headline']}” from the corrected facts.",
                                commissioned_by="corrections", reason=reason, supersedes=sid)
            out.append(c)
            rt.trace.emit(self.name, "commissioned a corrected version", "succeeded", commission_id=c.commission_id,
                          storyline_id=sid, output_summary="the replacement needs verification and producer "
                                                           "re-approval; the original stays in the audit trail",
                          ms=timer.ms)
        return out
