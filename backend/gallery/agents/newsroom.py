"""Newsroom: decides when each agent acts, and runs the workflow one batch at a time."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from . import workflow

if TYPE_CHECKING:
    from ..engine import MatchRuntime


class Newsroom:
    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt
        self.lock: Optional[asyncio.Lock] = None   # one batch at a time: small local models can't run in parallel
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def _spawn(self, coro) -> None:
        """Run agent work from async code (task), a worker thread (hand to the app's loop) or plain sync code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self.loop = self.loop or loop
            task = loop.create_task(coro)
            self.rt.pending.add(task)
            task.add_done_callback(self.rt.pending.discard)
        elif self.loop is not None and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        else:
            asyncio.run(coro)

    async def _run(self, make_commissions) -> list[str]:
        current = asyncio.get_running_loop()
        if self.lock is None or getattr(self, "_lock_loop", None) is not current:
            self.lock, self._lock_loop = asyncio.Lock(), current
        async with self.lock:
            commissions = await make_commissions()
            return await workflow.run(self.rt, commissions)

    # triggers ----------------------------------------------------------------
    def on_insights(self) -> None:
        self._spawn(self._run(lambda: self.rt.editor.plan("live")))

    def on_period_end(self, period: int) -> None:
        phase = "half_time" if period == 1 else "full_time"
        self._spawn(self._run(lambda: self.rt.desk.review(phase)))

    def on_correction(self, flagged: list[str], reason: str) -> None:
        if flagged:
            self._spawn(self._run(lambda: self.rt.corrections.handle(flagged, reason)))

    async def localise(self, market: str, phase: str = "live", top_n: int = 3) -> list[str]:
        async def plan():
            return self.rt.localiser.plan(market, phase, top_n)
        return await self._run(plan)

    async def desk_now(self, phase: str) -> list[str]:
        return await self._run(lambda: self.rt.desk.review(phase))
