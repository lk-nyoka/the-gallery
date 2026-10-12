"""Localiser: adapts approved (or verified) stories to each market's angle and language.

The market's lead story comes from the code ranking with that market's profile; the Writer then
rewrites the chosen stories for the market and every claim is verified again.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from .. import editorial
from .contracts import StoryCommission

if TYPE_CHECKING:
    from ..engine import MatchRuntime

_ids = itertools.count(1)


class Localiser:
    name = "Localiser"

    def __init__(self, rt: "MatchRuntime"):
        self.rt = rt

    def plan(self, market: str, phase: str = "live", top_n: int = 3) -> list[StoryCommission]:
        rt = self.rt
        mk = rt.markets[market]
        out = []
        for r in rt.ranking(market, phase):
            if len(out) >= top_n:
                break
            if (r["insight_id"], market) in rt.by_insight:
                continue
            base_id = rt.by_insight.get((r["insight_id"], "world"))
            base = rt.storylines.get(base_id) if base_id else None
            if not base or base["overall"] == "blocked":
                continue
            c = StoryCommission(commission_id=f"{rt.match_id}-l{next(_ids):04d}", match_id=rt.match_id,
                                phase="localisation", insight_id=r["insight_id"], market=market,
                                brief=f"Adapt for {mk['label']}: {mk.get('angle')}",
                                commissioned_by="localiser", base_storyline_id=base_id,
                                reason=f"#{r['rank']} for {mk['label']} (relevance "
                                       f"{r['factors']['market_relevance']['value']:.2f})")
            out.append(c)
            rt.trace.emit(self.name, f"chose {editorial.DETECTOR_LABEL.get(r['detector'])} ({r['minute']}') "
                          f"for {mk['label']}", "succeeded", commission_id=c.commission_id, output_summary=c.reason)
        return out
