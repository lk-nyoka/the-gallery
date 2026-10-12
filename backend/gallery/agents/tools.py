"""Deterministic tools the agents act through.

The Analyst only reads match data through these functions, and the Writer can only get a verdict
by submitting a structured draft to `submit_story_for_verification`. Every response carries the
underlying event IDs so anything an agent says can be traced back to the data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .. import verifier
from .contracts import Claim, ClaimRejection, StoryDraft, VerificationResult

if TYPE_CHECKING:
    from ..engine import MatchRuntime


class Toolbox:
    """Tools bound to one match. Read-only except the verifier, which only reads too."""

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt

    # ------------------------------------------------------------------ analyst tools (read-only)
    def get_match_summary(self) -> dict[str, Any]:
        rt = self.rt
        score = rt.score()
        reds = [e for e in rt.detectors.events if e["type"] == "card" and e.get("outcome") == "red"]
        return {"match_id": rt.match_id, "home": rt.names["HOME"], "away": rt.names["AWAY"],
                "score": {rt.names[k]: v for k, v in score.items()}, "minute": int(rt.now_clock // 60) + 1,
                "period": rt.period, "red_cards": [{"team": rt.names[e["team_id"]], "player": e.get("player"),
                                                    "minute": e["minute"], "event_id": e["event_id"]} for e in reds],
                "feed_cannot_show": [k for k, v in rt.detectors.caps.items() if v is False]}

    def get_events_in_window(self, from_minute: int, to_minute: int, types: Optional[list[str]] = None,
                             limit: int = 40) -> list[dict[str, Any]]:
        rows = [e for e in self.rt.detectors.events
                if from_minute <= e["minute"] <= to_minute and e["type"] not in ("pass", "carry")
                and (not types or e["type"] in types)]
        return [{k: e.get(k) for k in ("event_id", "minute", "team_id", "type", "outcome", "player")}
                for e in rows[:limit]]

    def get_insight_evidence(self, insight_id: str) -> dict[str, Any]:
        ins = self.rt.insight(insight_id)
        rows = self.rt.store.events_by_ids(ins["evidence"])
        return {"insight_id": insight_id, "detector": ins["detector"], "team": self.rt.names[ins["team_id"]],
                "minute": ins["minute"], "metrics": ins["metrics"], "facts": ins["facts"],
                "evidence_events": [{k: r.get(k) for k in ("event_id", "minute", "team_id", "type", "outcome",
                                                            "player")} for r in rows]}

    def get_team_match_stats(self, team_id: str) -> dict[str, Any]:
        rows = self.rt.store.query("SELECT stat, value FROM match_stats WHERE match_id = ? AND team_id = ?",
                                   (self.rt.match_id, team_id))
        return {"team": self.rt.names.get(team_id, team_id), "stats": {r["stat"]: r["value"] for r in rows},
                "source": "feed team stats" if rows else "none in this feed"}

    def get_coverage(self) -> dict[str, Any]:
        return self.rt.coverage()

    # ------------------------------------------------------------------ verification tool
    def submit_story_for_verification(self, draft: StoryDraft, language: str = "en") -> VerificationResult:
        """Validate the draft and re-compute every claim from the event store. The only source of verdicts."""
        english = language == "en"
        segs = []
        rejections: list[ClaimRejection] = []
        for i, s in enumerate(draft.segments):
            res = verifier.verify_segment(self.rt.store, self.rt.match_id,
                                          {"text": s.text, "claims": [c.model_dump() for c in s.claims]},
                                          english=english)
            for c in res["claims"]:
                if not c.get("evidence_event_ids"):
                    c["evidence_event_ids"] = [r["event_id"] for r in c.get("rows", [])][:12]
            segs.append(res)
            if res["verdict"] != verifier.VERIFIED:
                rejections.append(ClaimRejection(segment_index=i, sentence=s.text, verdict=res["verdict"],
                                                 reason=res["reason"]))
        hv = verifier.verify_headline(draft.headline,
                                      [{"claims": [c.model_dump() for c in s.claims]} for s in draft.segments],
                                      english=english)
        verdicts = [hv["verdict"]] + [r["verdict"] for r in segs]
        overall = verifier.overall(verdicts)
        guidance = None
        if overall != verifier.VERIFIED:
            lines = [f"- Sentence {r.segment_index + 1} ({r.verdict}): \"{r.sentence}\" -> {r.reason}"
                     for r in rejections]
            if hv["verdict"] != verifier.VERIFIED:
                lines.append(f"- Headline ({hv['verdict']}): {hv['reason']}")
            guidance = ("Revise using only the supplied facts. Remove or rewrite these sentences:\n"
                        + "\n".join(lines))
        return VerificationResult(draft_id=draft.draft_id, verdict=overall, rejected_claims=rejections,
                                  revision_guidance=guidance, segment_results=segs,
                                  headline_verdict=hv["verdict"], headline_reason=hv["reason"])

    # ------------------------------------------------------------------ facts
    def refresh_facts(self, insight_id: str) -> list[Claim]:
        """Re-compute an insight's facts from current data (used after a correction)."""
        ins = self.rt.insight(insight_id)
        fresh = []
        for f in ins["facts"]:
            r = verifier.check_claim(self.rt.store, self.rt.match_id, f)
            value = r.actual if r.actual is not None else f["value"]
            if isinstance(value, float) and value.is_integer() and isinstance(f["value"], int):
                value = int(value)
            fresh.append(Claim(**{**f, "value": value}))
        return fresh
