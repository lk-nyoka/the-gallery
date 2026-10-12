"""Editor-in-Chief: decides which detected stories get written. It commissions; it never writes.

Code supplies the candidates and their scores; the Editor chooses and briefs. Goals, red cards and
turning points are coverage obligations, so they are always commissioned whatever the model says.
"""

from __future__ import annotations

import itertools
import json
from typing import TYPE_CHECKING

from .. import editorial
from .contracts import StoryCommission
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)

MANDATORY = {"goal", "red_card", "turning_point"}
LIVE_BUDGET = 3

PROMPT = """You are the Editor-in-Chief of a live football production desk.
You receive story candidates that code has already detected, scored and ranked.
Choose the ones worth putting on air now (at most {budget}) and give each a one-sentence brief for the
Writer saying what the story is about and what to lead with.
Do not calculate statistics. Do not invent facts or numbers. Only use insight_ids from the list.
Return only JSON: {{"commissions": [{{"insight_id": "...", "brief": "..."}}], "held": [{{"insight_id": "...", "reason": "..."}}]}}"""


class EditorInChief:
    name = "Editor-in-Chief"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt
        self.commissioned: set[str] = set()
        self.considered: set[str] = set()

    def _brief(self, ins: dict) -> str:
        label = editorial.DETECTOR_LABEL.get(ins["detector"], ins["detector"])
        return f"{label}: {editorial.metric_summary(ins, self.rt.names)}. Lead with what changed and why it matters now."

    def _rank_at_detection(self, ins: dict) -> dict:
        """Score a candidate against the live window as it was when it was detected (replay-speed independent)."""
        rt = self.rt
        pool = [i for i in rt.insights if i["t"] <= ins["t"]]
        ctx = {"teams": ("HOME", "AWAY"), "score": rt.detectors.score_at(ins["t"])}
        ranked = editorial.rank(pool, ins["t"], ctx, rt.markets["world"], window_s=15 * 60)
        return next(r for r in ranked if r["insight_id"] == ins["insight_id"])

    async def plan(self, phase: str = "live") -> list[StoryCommission]:
        rt, timer = self.rt, Timer()
        by_id = {i["insight_id"]: i for i in rt.insights}
        fresh = [self._rank_at_detection(i) for i in rt.insights
                 if i["insight_id"] not in self.commissioned and i["insight_id"] not in self.considered]
        if not fresh:
            return []
        self.considered.update(r["insight_id"] for r in fresh)
        mandatory = [r for r in fresh if r["detector"] in MANDATORY]
        optional = [r for r in fresh if r["detector"] not in MANDATORY]
        # a candidate is eligible if it was in the top LIVE_BUDGET of its own 15-minute window
        eligible = [r for r in optional if r["rank"] <= LIVE_BUDGET]
        budget = len(eligible)
        chosen = {r["insight_id"]: self._brief(by_id[r["insight_id"]]) for r in mandatory}
        decided_by = "code rule (coverage obligation)" if mandatory else ""
        model_used = False
        prov = rt.provider
        if eligible and prov.uses("editor"):
            cands = [{"insight_id": r["insight_id"], "type": editorial.DETECTOR_LABEL.get(r["detector"]),
                      "minute": r["minute"], "team": rt.names.get(r["team_id"]), "score": r["score"],
                      "rank_in_window": r["rank"],
                      "summary": editorial.metric_summary(by_id[r["insight_id"]], rt.names)} for r in eligible[:8]]
            try:
                out = await prov.chat_json([
                    {"role": "system", "content": PROMPT.format(budget=budget)},
                    {"role": "user", "content": json.dumps({"phase": phase, "score": rt.score(),
                                                            "candidates": cands})}], max_tokens=400)
                valid = {c["insight_id"] for c in cands}
                picks = [c for c in out.get("commissions", []) if isinstance(c, dict) and c.get("insight_id") in valid]
                for c in picks[:budget]:
                    chosen.setdefault(c["insight_id"], str(c.get("brief") or self._brief(by_id[c["insight_id"]]))[:300])
                model_used = bool(picks)
            except Exception as exc:
                rt.trace.emit(self.name, "fell back to ranking", "retry", input_summary=f"{len(cands)} candidates",
                              output_summary=f"model error: {str(exc)[:120]}")
        if not model_used:
            for r in eligible:
                chosen.setdefault(r["insight_id"], self._brief(by_id[r["insight_id"]]))
        commissions = []
        for r in fresh:
            iid = r["insight_id"]
            if iid in chosen:
                self.commissioned.add(iid)
                c = StoryCommission(commission_id=f"{rt.match_id}-c{next(_ids):04d}", match_id=rt.match_id,
                                    phase=phase if phase in ("live", "half_time", "full_time") else "live",
                                    insight_id=iid, brief=chosen[iid], commissioned_by="editor_in_chief",
                                    reason=(decided_by if r["detector"] in MANDATORY else
                                            ("chosen by the model from code-ranked candidates" if model_used
                                             else f"ranked #{r['rank']} in its 15-minute window "
                                                  f"(score {r['score']:.2f})")),
                                    red_team=rt.red_team_next())
                commissions.append(c)
                rt.trace.emit(self.name, f"commissioned {editorial.DETECTOR_LABEL.get(r['detector'], r['detector'])} "
                              f"({r['minute']}')", "succeeded", commission_id=c.commission_id,
                              input_summary=f"{len(fresh)} new candidates, phase {phase}",
                              output_summary=f"{c.brief} · why: {c.reason}", ms=timer.ms)
            else:
                why = (f"ranked #{r['rank']} in its 15-minute window (score {r['score']:.2f}); the desk runs "
                       f"{LIVE_BUDGET} stories per window" if r["rank"] > LIVE_BUDGET
                       else "not chosen by the Editor-in-Chief this time")
                rt.trace.emit(self.name, f"held {editorial.DETECTOR_LABEL.get(r['detector'], r['detector'])} "
                              f"({r['minute']}')", "held", input_summary=editorial.metric_summary(by_id[iid], rt.names),
                              output_summary=why)
        return commissions
