"""Viewer personalisation: the same verified facts, shaped for different people.

A viewer profile becomes one more market (`viewer`) built from a base market (language) plus:
  mode     analyst = every verified sentence, the numbers and the evidence; tactical stories rank higher
           casual  = headline and the plainest verified sentence, a short explainer; goals and cards rank higher
  club     the viewer's team: its stories rank higher and are highlighted
  player   player-focus mode: stories that involve the player rank first, plus a live player card

Nothing here writes new facts. Ranking uses the same deterministic formula; text is a subset of sentences the
verifier already passed; the player card's numbers are computed by the verifier's own SQL templates.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Optional

from . import verifier

if TYPE_CHECKING:
    from .engine import MatchRuntime

MODES: dict[str, dict[str, Any]] = {
    "analyst": {
        "label": "Analyst", "depth": "analyst", "max_sentences": None, "show_numbers": True,
        "detector_boost": {"turning_point": 0.3, "momentum_shift": 0.3, "goal_threat": 0.25, "counterattack": 0.2,
                           "power_shot": 0.25},
        "angle": "tactical detail: what changed, by how much, and the evidence",
    },
    "casual": {
        "label": "Casual fan", "depth": "casual", "max_sentences": 2, "show_numbers": False,
        "detector_boost": {"goal": 0.35, "red_card": 0.3, "turning_point": 0.25, "standout": 0.2},
        "angle": "the big moments in plain words",
    },
}

# Plain-language explainers for casual viewers. They describe the *kind* of moment and contain no match facts,
# so they need no verification (every fact shown next to them is still a verified sentence).
EXPLAINERS = {
    "goal": "Goals change everything: the scoreline decides what both teams do next.",
    "red_card": "A red card means a player is sent off and the team plays the rest of the match with one fewer.",
    "turning_point": "A turning point compares the match before and after one big moment, to show what it changed.",
    "momentum_shift": "Momentum is which team is creating more danger right now: box entries and shots.",
    "counterattack": "A counterattack wins the ball deep and reaches the other goal before the defence is set.",
    "goal_threat": "Pressure without a goal: lots of chances, but the breakthrough hasn't come yet.",
    "standout": "A standout performer is the player most involved in the dangerous moments of a spell.",
    "power_shot": "A power shot is struck at 110 km/h or faster: ball speed measured from the event data.",
}


def viewer_market(base: dict, mode: str = "casual", club: Optional[str] = None,
                  player: Optional[str] = None) -> dict:
    m = MODES.get(mode, MODES["casual"])
    mk = copy.deepcopy(base)
    boost = dict(mk.get("detector_boost", {}))
    for k, v in m["detector_boost"].items():
        boost[k] = max(boost.get(k, 0.0), v)
    mk.update({
        "id": "viewer", "label": f"You · {m['label']}", "depth": m["depth"], "detector_boost": boost,
        "angle": m["angle"] + (f"; focus on {player}" if player else ""),
        "focus_teams": [club] if club in ("HOME", "AWAY") else [],
        "focus_players": [player] if player else [],
        "base_relevance": 0.5,
        "viewer": {"mode": mode, "club": club if club in ("HOME", "AWAY") else None, "player": player,
                   "base_market": base.get("id", "world"), "max_sentences": m["max_sentences"],
                   "show_numbers": m["show_numbers"]},
    })
    return mk


def players(rt: "MatchRuntime") -> list[dict]:
    rows = rt.store.query("SELECT player, team_id, COUNT(*) AS n FROM events WHERE match_id = ? AND player IS NOT NULL "
                          "AND player != '' GROUP BY player, team_id ORDER BY n DESC", (rt.match_id,))
    return [{"player": r["player"], "team_id": r["team_id"], "team": rt.names.get(r["team_id"]), "actions": r["n"]}
            for r in rows]


PLAYER_STATS = [
    ("shots", "Shots", {"type": "shot"}),
    ("on_target", "On target", {"type": "shot", "outcome": ["on_target", "goal"]}),
    ("goals", "Goals", {"type": "shot", "outcome": "goal"}),
    ("chances", "Chances created", {"type": "key_pass"}),
    ("box_entries", "Box entries", {"type": "box_entry"}),
    ("regains", "Ball regains", {"type": "regain"}),
    ("passes", "Passes completed", {"type": "pass", "outcome": "complete"}),
    ("fouls", "Fouls", {"type": "foul"}),
]


def player_card(rt: "MatchRuntime", player: str) -> dict:
    """Live player card. Every number comes from the verifier's own count template, with its SQL."""
    row = rt.store.query("SELECT team_id FROM events WHERE match_id = ? AND player = ? LIMIT 1", (rt.match_id, player))
    if not row:
        return {"player": player, "found": False, "stats": [], "stories": [], "timeline": []}
    team = row[0]["team_id"]
    caps = rt.detectors.caps or {}
    stats = []
    for key, label, p in PLAYER_STATS:
        if key == "passes" and caps.get("passes") is False:
            continue
        claim = {"metric": "count", "op": ">=", "value": 0,
                 "params": {"team_id": team, "player": player, "from_t": 0, "to_t": 1e9, **p}}
        r = verifier.check_claim(rt.store, rt.match_id, claim)
        if r.actual is None:
            continue
        stats.append({"key": key, "label": label, "value": r.actual, "sql": r.sql, "sql_params": r.sql_params})
    for key, label, metric, unit in (("pass_acc", "Pass accuracy", "pass_accuracy_pct", "%"),
                                     ("pass_len", "Avg pass length", "avg_pass_length_m", " m"),
                                     ("hard_passes", "Difficult passes completed", "hard_passes_completed", ""),
                                     ("top_speed", "Fastest shot", "max_shot_speed", " km/h")):
        if metric != "max_shot_speed" and caps.get("passes") is False:
            continue
        claim = {"metric": metric, "op": ">=", "value": 0,
                 "params": {"team_id": team, "player": player, "from_t": 0, "to_t": 1e9}}
        r = verifier.check_claim(rt.store, rt.match_id, claim)
        if r.actual is None:
            continue
        stats.append({"key": key, "label": label, "value": f"{r.actual:g}{unit}" if isinstance(r.actual, (int, float))
                      else r.actual, "sql": r.sql, "sql_params": r.sql_params})
    xg = rt.store.query("SELECT ROUND(COALESCE(SUM(xg), 0), 2) AS v FROM events WHERE match_id = ? AND player = ? "
                        "AND type = 'shot'", (rt.match_id, player))[0]["v"]
    if caps.get("xg") is not False:
        stats.append({"key": "xg", "label": "xG", "value": xg,
                      "sql": "SELECT SUM(xg) FROM events WHERE match_id = ? AND player = ? AND type = 'shot'",
                      "sql_params": [rt.match_id, player]})
    timeline = rt.store.query(
        "SELECT event_id, minute, type, outcome FROM events WHERE match_id = ? AND player = ? AND type IN "
        "('shot','own_goal','card','key_pass','box_entry','regain') ORDER BY t", (rt.match_id, player))
    stories = [s["storyline_id"] for s in rt.storylines.values()
               if s["market"] == "world" and s["status"] != "superseded"
               and player in (rt.insight(s["insight_id"]).get("players") or [])]
    return {"player": player, "found": True, "team_id": team, "team": rt.names.get(team), "stats": stats,
            "timeline": timeline[-40:], "stories": stories}
