"""Timed, machine-readable overlay feed: what a graphics engine would put on screen, and when.

Two kinds of cue:
  story       an approved, verified storyline rendered with its graphic template (lower third, goal card, ...)
  player_tag  automatic name tag with a speed or distance, fired when a threshold is crossed in the event data
              (shot at 100 km/h or faster, completed pass of 40 m or longer)

Only approved stories become cues; tags are computed directly from events, so both are checked facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .events import pass_length_m
from .writer import fmt_clock

if TYPE_CHECKING:
    from .engine import MatchRuntime

DURATION_S = {"goal_card": 10, "card_alert": 8, "before_after_split": 12, "momentum_chart": 10,
              "sequence_timeline": 10, "player_card": 8, "chance_timeline": 8, "speed_tag": 6, "lower_third": 8}
TAG_SHOT_KMH = 100.0
TAG_PASS_M = 40.0
TAG_DURATION_S = 5


def _clock(rt: "MatchRuntime", t: float) -> float:
    rows = rt.store.query("SELECT clock FROM events WHERE match_id = ? AND t <= ? AND clock IS NOT NULL "
                          "ORDER BY t DESC LIMIT 1", (rt.match_id, t))
    return rows[0]["clock"] + 0.0 if rows else t


def feed(rt: "MatchRuntime", market: str = "world") -> dict[str, Any]:
    cues: list[dict[str, Any]] = []
    for s in rt.storylines.values():
        if s["status"] != "approved" or s["market"] not in (market, "world"):
            continue
        if s["market"] == "world" and market != "world" and (s["insight_id"], market) in rt.by_insight:
            continue      # the market has its own approved version
        start = s.get("approved_at_t") or rt.insight(s["insight_id"])["t"]
        tpl = s["graphic"].get("template", "lower_third")
        cues.append({
            "cue_id": f"cue-{s['storyline_id']}", "kind": "story", "storyline_id": s["storyline_id"],
            "start_t": round(start, 1), "start_clock": fmt_clock(_clock(rt, start)),
            "duration_s": DURATION_S.get(tpl, 8), "template": tpl, "headline": s["headline"],
            "text": " ".join(g["text"] for g in s["segments"] if not g.get("struck") and g["verdict"] != "blocked"),
            "fields": s["graphic"].get("fields", {}), "team_id": s["team_id"], "market": s["market"],
            "language": s.get("language", "en"), "verified": s["overall"] == "verified", "approved": True,
        })
    rows = rt.store.query(
        "SELECT event_id, t, clock, team_id, type, outcome, player, x, y, end_x, end_y, speed FROM events "
        "WHERE match_id = ? AND player IS NOT NULL AND ((type = 'shot' AND speed >= ?) OR "
        "(type = 'pass' AND outcome = 'complete' AND pass_length_m(x, y, end_x, end_y) >= ?)) ORDER BY t",
        (rt.match_id, TAG_SHOT_KMH, TAG_PASS_M))
    for r in rows:
        if r["type"] == "shot":
            label, value = "Shot speed", f"{r['speed']:g} km/h"
        else:
            label, value = "Pass length", f"{pass_length_m(r['x'], r['y'], r['end_x'], r['end_y']):g} m"
        cues.append({
            "cue_id": f"tag-{r['event_id']}", "kind": "player_tag", "event_id": r["event_id"],
            "start_t": r["t"], "start_clock": fmt_clock(r["clock"] if r["clock"] is not None else r["t"]),
            "duration_s": TAG_DURATION_S, "template": "player_tag", "player": r["player"],
            "team_id": r["team_id"], "fields": {"label": label, "value": value}, "verified": True,
            "approved": True, "trigger": f"{label.lower()} over {TAG_SHOT_KMH:g} km/h" if r["type"] == "shot"
            else f"completed pass over {TAG_PASS_M:g} m",
        })
    cues.sort(key=lambda c: c["start_t"])
    return {"match_id": rt.match_id, "market": market, "names": rt.names, "score": rt.score(),
            "now_t": rt.now_t, "cues": cues,
            "thresholds": {"shot_speed_kmh": TAG_SHOT_KMH, "pass_length_m": TAG_PASS_M},
            "note": "Only producer-approved stories become cues; player tags are computed from the events."}
