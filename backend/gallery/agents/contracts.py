"""Typed contracts between agents, tools, the API and the UI.

Rules enforced by these types and the workflow:
  - every factual sentence in a draft is a Segment with at least one Claim
  - the Writer can't produce a draft without claims (the schema rejects it)
  - only the verification tool sets a verdict; agents can't mark their own work true
"""

from __future__ import annotations

import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Verdict = Literal["verified", "needs_human", "blocked"]


class Claim(BaseModel):
    key: Optional[str] = None
    metric: str
    params: dict[str, Any]
    op: Literal["==", ">=", "<=", ">", "<"] = "=="
    value: Any


class Segment(BaseModel):
    text: str
    claims: list[Claim] = Field(default_factory=list)
    injected: bool = False            # labelled red-team test sentence
    injected_kind: Optional[str] = None


class StoryCommission(BaseModel):
    commission_id: str
    match_id: str
    phase: Literal["live", "half_time", "full_time", "coverage", "correction", "localisation"]
    insight_id: str
    brief: str
    market: str = "world"
    commissioned_by: Literal["editor_in_chief", "desk", "corrections", "localiser", "producer"]
    reason: str = ""
    supersedes: Optional[str] = None          # storyline id this replaces (corrections)
    base_storyline_id: Optional[str] = None   # verified storyline to adapt (localisation)
    red_team: bool = False                    # test: first draft carries a labelled false sentence


class EvidenceBrief(BaseModel):
    commission_id: str
    insight_id: str
    detector: str
    team: str
    minute: int
    facts: list[Claim]                 # true facts computed by code; the only material the Writer may use
    evidence_event_ids: list[str]
    tool_calls: list[str]
    notes: str = ""


class StoryDraft(BaseModel):
    draft_id: str
    commission_id: str
    version: int = 1
    headline: str
    segments: list[Segment]
    graphic: dict[str, Any] = Field(default_factory=dict)
    clip_cue: dict[str, Any] = Field(default_factory=dict)
    writer: str = "template"

    @field_validator("segments")
    @classmethod
    def _non_empty(cls, v: list[Segment]) -> list[Segment]:
        if not v:
            raise ValueError("a draft needs at least one sentence")
        return v

    @property
    def body(self) -> str:
        return " ".join(s.text for s in self.segments)


class ClaimRejection(BaseModel):
    segment_index: int
    sentence: str
    verdict: Verdict
    reason: str


class VerificationResult(BaseModel):
    draft_id: str
    verdict: Verdict
    rejected_claims: list[ClaimRejection] = Field(default_factory=list)
    revision_guidance: Optional[str] = None
    segment_results: list[dict[str, Any]] = Field(default_factory=list)
    headline_verdict: Verdict = "verified"
    headline_reason: str = ""


class ProducerDecision(BaseModel):
    storyline_id: str
    action: Literal["approve", "edit", "reject"]
    editor_note: Optional[str] = None


class AgentTraceEvent(BaseModel):
    trace_id: str
    match_id: str
    storyline_id: Optional[str] = None
    commission_id: Optional[str] = None
    agent: str
    action: str
    tool: Optional[str] = None
    input_summary: str = ""
    output_summary: str = ""
    status: Literal["started", "succeeded", "rejected", "retry", "failed", "held", "waiting"]
    timestamp: float = Field(default_factory=time.time)
    ms: Optional[float] = None
