"""Follow a live ESPN match: poll, ingest new events, apply corrections, stop at full time."""

from __future__ import annotations

import asyncio
import time

from . import espn
from .engine import MatchRuntime

POLL_SECONDS = 20.0
WATCHED = ("type", "outcome", "player", "team_id")


def _sig(e) -> tuple:
    d = e if isinstance(e, dict) else e.__dict__
    return tuple(d.get(k) for k in WATCHED)


async def sync_once(rt: MatchRuntime, espn_id: str, league: str) -> dict:
    """Fetch the feed once and reconcile it with what The Gallery already holds."""
    data = await asyncio.to_thread(espn.summary, espn_id, league, True)
    parsed = espn.parse(data, rt.match_id)
    meta = parsed["meta"]
    known = {r["event_id"]: r for r in rt.store.events(rt.match_id) if r["source"] == "espn"}
    fresh = {e.event_id: e for e in parsed["events"]}
    added = corrected = removed = 0
    for e in parsed["events"]:              # in match order
        if e.event_id not in known:
            await rt.ingest(e)
            added += 1
        elif _sig(known[e.event_id]) != _sig(e):
            changes = {k: getattr(e, k) for k in WATCHED if known[e.event_id][k] != getattr(e, k)}
            rt.correct_event(e.event_id, changes,
                             f"Feed correction: {e.minute}' "
                             + ", ".join(f"{k} {known[e.event_id][k]} → {v}" for k, v in changes.items()))
            corrected += 1
    for eid in set(known) - set(fresh):
        rt.retract_event(eid, "Feed removed this event")
        removed += 1
    rt.set_team_stats(meta.get("team_stats") or {}, "espn")
    rt.meta.update({k: v for k, v in meta.items() if k != "team_stats"})
    rt.live_status = {"state": meta["state"], "status": meta["status"], "clock": meta.get("clock"),
                      "last_poll": time.time(), "added": added, "corrected": corrected, "removed": removed,
                      "every_s": POLL_SECONDS}
    rt.publish({"type": "live", "live": rt.live_status, "result_check": rt.result_check(),
                "score": rt.score()})
    return rt.live_status


async def follow(rt: MatchRuntime, espn_id: str, league: str, interval: float = POLL_SECONDS,
                 max_errors: int = 10) -> None:
    errors = 0
    while True:
        try:
            st = await sync_once(rt, espn_id, league)
            errors = 0
            if st["state"] == "post":
                rt.publish({"type": "replay_done", "result_check": rt.result_check()})
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network blips: keep trying, but say so
            errors += 1
            rt.live_status = {**rt.live_status, "error": str(exc)[:160], "errors": errors}
            rt.publish({"type": "live", "live": rt.live_status})
            if errors >= max_errors:
                return
        await asyncio.sleep(interval)
