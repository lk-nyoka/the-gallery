"""Writer: drafts a story as claim-carrying sentences, then revises from the verifier's feedback.

It only ever sees the Analyst's evidence brief. Its drafts must validate against the StoryDraft
contract (every sentence carries claims); a model draft that doesn't is retried once, then the
deterministic template writer takes over so the desk never stalls.
"""

from __future__ import annotations

import itertools
import json
import random
from typing import TYPE_CHECKING, Optional

from pydantic import ValidationError

from .. import writer as W
from .contracts import EvidenceBrief, Segment, StoryCommission, StoryDraft, VerificationResult
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)

REVISE = """Your previous draft was checked by the verifier, which re-computes every claim from the match data.
{guidance}
Rewrite the story so every sentence is supported by the supplied facts, copied exactly into its claims.
Return only the corrected JSON in the same format."""

LOCALISE = """Rewrite this verified football story for the market described in market_notes, in {language}.
Change the angle and emphasis for that audience, not just the words. Keep every claim object exactly as
given (same key, metric, params, op, value) and keep numbers as digits. Do not add new facts.
Return only JSON: {{"headline": "...", "segments": [{{"text": "...", "claims": [...]}}]}}"""


MAX_MODEL_VERSION = 2   # v1 and the first revision may come from the model; the last revision is deterministic


class WriterAgent:
    name = "Writer"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt
        self.rng = random.Random(7)

    def _new_id(self) -> str:
        return f"{self.rt.match_id}-d{next(_ids):04d}"

    def _template(self, c: StoryCommission, version: int) -> StoryDraft:
        ins = self.rt.insight(c.insight_id)
        d = W.TemplateWriter().write(ins, self.rt.names, self.rt.markets.get(c.market))
        return StoryDraft(draft_id=self._new_id(), commission_id=c.commission_id, version=version,
                          headline=d["headline"], segments=[Segment(**s) for s in d["segments"]],
                          graphic=d["graphic"], clip_cue=d["clip_cue"], writer="template")

    def _red_team(self, c: StoryCommission, draft: StoryDraft) -> StoryDraft:
        """Labelled test: plant one unsupported sentence so the verify-and-revise loop is visible."""
        ins = self.rt.insight(c.insight_id)
        options = W.false_claims(ins, self.rt.names, {"from_t": ins["from_t"], "to_t": ins["to_t"], "actual": None,
                                                     "official": None}, self.rng)
        pick = next((o for o in options if o["kind"] in ("unsupported_state", "wrong_number")), options[0])
        draft.segments.insert(1, Segment(text=pick["text"], claims=pick["claims"], injected=True,
                                         injected_kind=pick["kind"]))
        return draft

    async def _model_draft(self, c: StoryCommission, b: EvidenceBrief, prev: Optional[StoryDraft],
                           feedback: Optional[VerificationResult], version: int) -> StoryDraft:
        rt = self.rt
        ins = rt.insight(c.insight_id)
        events = rt.store.events_by_ids(b.evidence_event_ids)
        bundle = W.evidence_bundle(ins, rt.names, events, rt.score(), rt.markets.get(c.market))
        bundle["facts"] = [f.model_dump() for f in b.facts]          # only the Analyst's facts
        bundle["editor_brief"] = c.brief
        if b.notes:
            bundle["analyst_note"] = b.notes
        msgs = [{"role": "system", "content": W.SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(bundle, default=str)}]
        if prev is not None and feedback is not None:
            msgs += [{"role": "assistant", "content": json.dumps({"headline": prev.headline,
                                                                  "segments": [s.model_dump() for s in prev.segments]})},
                     {"role": "user", "content": REVISE.format(guidance=feedback.revision_guidance or "")}]
        last_err = ""
        for attempt in range(2):
            out = await rt.provider.chat_json(msgs, max_tokens=450)
            try:
                segs = [Segment(text=str(s["text"]), claims=s.get("claims") or []) for s in out.get("segments", [])]
                if any(not s.claims for s in segs) and attempt == 0:
                    raise ValueError("a sentence has no claims")
                return StoryDraft(draft_id=self._new_id(), commission_id=c.commission_id, version=version,
                                  headline=str(out.get("headline", "")).strip() or "Untitled", segments=segs,
                                  graphic=W.graphic_spec(ins, rt.names), clip_cue=W.clip_cue(ins),
                                  writer=rt.provider.label())
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                last_err = str(exc)[:160]
                msgs += [{"role": "assistant", "content": json.dumps(out)[:2000]},
                         {"role": "user", "content": f"Invalid draft: {last_err}. Every sentence needs claims copied "
                                                     f"from the facts. Return valid JSON."}]
        raise ValueError(f"model draft invalid twice: {last_err}")

    async def draft(self, c: StoryCommission, b: EvidenceBrief, version: int = 1, prev: Optional[StoryDraft] = None,
                    feedback: Optional[VerificationResult] = None) -> StoryDraft:
        rt, timer = self.rt, Timer()
        if c.phase == "localisation":
            return await self.localise(c, b)
        action = "drafted" if prev is None else f"revised (v{version})"
        draft: Optional[StoryDraft] = None
        last_chance = prev is not None and version > MAX_MODEL_VERSION   # final revision: no more model guesses
        if rt.provider.uses("writer") and not last_chance:
            try:
                draft = await self._model_draft(c, b, prev, feedback, version)
            except Exception as exc:
                rt.trace.emit(self.name, "model draft failed, using template", "retry", commission_id=c.commission_id,
                              output_summary=str(exc)[:200])
        if draft is None:
            if prev is not None and feedback is not None:
                # template revision: drop the sentences the verifier rejected, keep everything it accepted
                bad = {r.segment_index for r in feedback.rejected_claims if r.verdict == "blocked"}
                kept = [s for i, s in enumerate(prev.segments) if i not in bad]
                draft = (StoryDraft(draft_id=self._new_id(), commission_id=c.commission_id, version=version,
                                    headline=prev.headline, segments=kept, graphic=prev.graphic, clip_cue=prev.clip_cue,
                                    writer="template (revision: removed rejected sentences)")
                         if kept else self._template(c, version))
            else:
                draft = self._template(c, version)
        if version == 1 and c.red_team and prev is None:
            draft = self._red_team(c, draft)
        rt.trace.emit(self.name, f"{action}: “{draft.headline}”", "succeeded", commission_id=c.commission_id,
                      input_summary=f"{len(b.facts)} facts" + (" + verifier feedback" if feedback else ""),
                      output_summary=" ".join(s.text for s in draft.segments)[:500] + f" · writer: {draft.writer}"
                      + (" · includes a labelled red-team sentence" if any(s.injected for s in draft.segments) else ""),
                      ms=timer.ms)
        return draft

    async def localise(self, c: StoryCommission, b: EvidenceBrief) -> StoryDraft:
        rt = self.rt
        base = rt.storylines[c.base_storyline_id]
        mk = rt.markets[c.market]
        segs = [Segment(text=s["text"], claims=s["claims"]) for s in base["segments"] if not s.get("struck")]
        if rt.provider.uses("localiser"):
            try:
                out = await rt.provider.chat_json([
                    {"role": "system", "content": LOCALISE.format(language=mk["language_name"])},
                    {"role": "user", "content": json.dumps({"market_notes": mk["notes"], "angle": mk.get("angle"),
                                                            "headline": base["headline"],
                                                            "segments": [s.model_dump() for s in segs]})}],
                    max_tokens=700)
                new = [Segment(text=str(s["text"]), claims=s.get("claims") or []) for s in out["segments"]]
                d = StoryDraft(draft_id=self._new_id(), commission_id=c.commission_id, headline=str(out["headline"]),
                               segments=new, graphic={**base["graphic"], "emphasis": mk.get("graphic_emphasis")},
                               clip_cue=base["clip_cue"], writer=f"{rt.provider.label()} · {mk['language']}")
                rt.trace.emit("Localiser", f"adapted for {mk['label']} ({mk['language_name']})", "succeeded",
                              commission_id=c.commission_id, input_summary=f"angle: {mk.get('angle')}",
                              output_summary=d.headline)
                return d
            except Exception as exc:
                rt.trace.emit("Localiser", "model rewrite failed", "retry", commission_id=c.commission_id,
                              output_summary=str(exc)[:200])
        rt.trace.emit("Localiser", f"kept the verified English text for {mk['label']}", "waiting",
                      commission_id=c.commission_id,
                      output_summary=f"{mk['language_name']} needs a model (set MODEL_PROVIDER); the market's lead "
                                     f"story still changes by ranking (angle: {mk.get('angle')})")
        return StoryDraft(draft_id=self._new_id(), commission_id=c.commission_id, headline=base["headline"],
                          segments=segs, graphic={**base["graphic"], "emphasis": mk.get("graphic_emphasis")},
                          clip_cue=base["clip_cue"], writer=f"not localised ({mk['language_name']} needs a model)")
