"""The newsroom workflow, built on Microsoft Agent Framework.

        ┌──────────────── commissions (Editor-in-Chief · Desk · Corrections · Localiser)
        ▼
    Analyst ──brief──▶ Writer ──draft──▶ Verifier (deterministic tool)
                         ▲                   │
                         └── rejected, ≤2 ───┤
                             revisions       ▼ verified / needs-human / out of revisions
                                          Producer gate (awaits a human decision)

Each run takes a batch of commissions and fans them out; every step writes to the agent trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from .contracts import EvidenceBrief, StoryCommission, StoryDraft, VerificationResult
from .trace import Timer

if TYPE_CHECKING:
    from ..engine import MatchRuntime

MAX_REVISIONS = 2


@dataclass
class Batch:
    commissions: list[StoryCommission]


@dataclass
class Task:
    commission: StoryCommission
    brief: EvidenceBrief
    started: float
    version: int = 1
    draft: Optional[StoryDraft] = None
    result: Optional[VerificationResult] = None
    history: list[dict] = field(default_factory=list)

    @property
    def needs_revision(self) -> bool:
        return (self.result is not None and self.result.verdict == "blocked" and self.version <= MAX_REVISIONS
                and self.commission.phase != "localisation")


class Dispatch(Executor):
    def __init__(self, rt: "MatchRuntime"):
        super().__init__(id="dispatch")
        self.rt = rt

    @handler
    async def run(self, batch: Batch, ctx: WorkflowContext[StoryCommission]) -> None:
        for c in batch.commissions:
            await ctx.send_message(c)


class AnalystStep(Executor):
    def __init__(self, rt: "MatchRuntime"):
        super().__init__(id="analyst")
        self.rt = rt

    @handler
    async def run(self, c: StoryCommission, ctx: WorkflowContext[Task]) -> None:
        brief = await self.rt.analyst.brief(c)
        await ctx.send_message(Task(commission=c, brief=brief, started=time.perf_counter()))


class WriterStep(Executor):
    def __init__(self, rt: "MatchRuntime"):
        super().__init__(id="writer")
        self.rt = rt

    @handler
    async def run(self, t: Task, ctx: WorkflowContext[Task]) -> None:
        if t.draft is not None:          # coming back from the verifier: revise
            t.version += 1
        t.draft = await self.rt.writer_agent.draft(t.commission, t.brief, version=t.version,
                                                   prev=t.draft if t.result else None, feedback=t.result)
        await ctx.send_message(t)


class VerifierStep(Executor):
    """Not an agent: deterministic code. The only place a verdict can come from."""

    def __init__(self, rt: "MatchRuntime"):
        super().__init__(id="verifier")
        self.rt = rt

    @handler
    async def run(self, t: Task, ctx: WorkflowContext[Task]) -> None:
        timer = Timer()
        lang = self.rt.markets[t.commission.market]["language"]
        if t.draft and t.draft.writer.startswith("not localised"):
            lang = "en"
        t.result = self.rt.toolbox.submit_story_for_verification(t.draft, language=lang)
        t.history.append({"version": t.version, "verdict": t.result.verdict,
                          "rejected": [r.model_dump() for r in t.result.rejected_claims]})
        status = {"verified": "succeeded", "needs_human": "retry", "blocked": "rejected"}[t.result.verdict]
        detail = ("every number re-computed from the event data" if t.result.verdict == "verified" else
                  "; ".join(f"“{r.sentence[:80]}” → {r.reason}" for r in t.result.rejected_claims)
                  or t.result.headline_reason)
        self.rt.trace.emit("Verifier", f"{t.result.verdict.replace('_', ' ')} (v{t.version})", status,
                           commission_id=t.commission.commission_id, tool="submit_story_for_verification",
                           input_summary=f"{len(t.draft.segments)} sentences, "
                                         f"{sum(len(s.claims) for s in t.draft.segments)} claims",
                           output_summary=detail, ms=timer.ms)
        if t.needs_revision:
            self.rt.trace.emit("Writer", f"sent back for revision ({t.version}/{MAX_REVISIONS})", "retry",
                               commission_id=t.commission.commission_id,
                               output_summary=t.result.revision_guidance or "")
        await ctx.send_message(t)


class ProducerGate(Executor):
    def __init__(self, rt: "MatchRuntime"):
        super().__init__(id="producer_gate")
        self.rt = rt

    @handler
    async def run(self, t: Task, ctx: WorkflowContext[None, str]) -> None:
        latency = (time.perf_counter() - t.started) * 1000
        sl = self.rt.register_storyline(t.commission, t.draft, t.result, t.history, latency)
        await ctx.yield_output(sl["storyline_id"])


def build(rt: "MatchRuntime"):
    dispatch, analyst, writer = Dispatch(rt), AnalystStep(rt), WriterStep(rt)
    verify, gate = VerifierStep(rt), ProducerGate(rt)
    return (WorkflowBuilder(start_executor=dispatch, name="the-gallery-newsroom")
            .add_edge(dispatch, analyst)
            .add_edge(analyst, writer)
            .add_edge(writer, verify)
            .add_edge(verify, writer, condition=lambda t: t.needs_revision)
            .add_edge(verify, gate, condition=lambda t: not t.needs_revision)
            .build())


async def run(rt: "MatchRuntime", commissions: list[StoryCommission]) -> list[str]:
    if not commissions:
        return []
    wf = build(rt)
    result = await wf.run(Batch(commissions))
    return list(result.get_outputs())
