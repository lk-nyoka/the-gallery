"""Synthetic, football-realistic fixtures with known answers (the hackathon's required data type).

Fictional clubs and players only. Each fixture records its planted patterns, its coverage obligations
(every goal, own goal and red card) and any data corrections that arrive mid-match, so detection,
coverage and correction handling can all be measured.

    python -m gallery.fixtures      writes data/fixtures/<id>.json (events + truth)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import Event
from .synth import Plant, generate

M = 60

FIXTURES: dict[str, dict[str, Any]] = {
    "A": {
        "title": "Turning point",
        "description": "Northbridge dominate early and lead; a red card at 63' flips the match and Riverside "
                       "score twice.",
        "home": "Northbridge FC", "away": "Riverside Athletic", "seed": 11,
        "plants": [Plant("momentum_shift", "H", 8 * M, 24 * M, label="early home dominance"),
                   Plant("goal", "H", 19 * M, 19 * M, player="Reyes", label="home goal"),
                   Plant("standout", "H", 5 * M, 20 * M, player="Santos", label="home playmaker"),
                   Plant("red_card", "H", 63 * M, 63 * M, player="Kovac", label="red card"),
                   Plant("goal", "A", 70 * M, 70 * M, player="Ndlovu", label="away goal after the red"),
                   Plant("counterattack", "A", 76 * M, 76 * M + 20, label="away counter"),
                   Plant("goal", "A", 84 * M, 84 * M, player="Dube", label="away winner")],
        "corrections": [], "red_team": 0,
    },
    "B": {
        "title": "Correction",
        "description": "A goal is first credited to Adeyemi; two minutes later the feed re-credits it to Moreau. "
                       "Approved coverage must not go out wrong.",
        "home": "Harbour City", "away": "Eastvale Rovers", "seed": 23,
        "plants": [Plant("goal", "H", 28 * M, 28 * M, player="Adeyemi", label="goal later re-credited"),
                   Plant("counterattack", "A", 41 * M, 41 * M + 20, label="away counter"),
                   Plant("momentum_shift", "A", 50 * M, 60 * M, label="away pressure"),
                   Plant("goal", "A", 57 * M, 57 * M, player="Walsh", label="equaliser"),
                   Plant("goal", "H", 80 * M, 80 * M, player="Byrne", label="home winner")],
        "corrections": [{"plant_index": 0, "after_s": 120, "changes": {"player": "Moreau"},
                         "reason": "Feed correction: 29' goal re-credited from Adeyemi to Moreau"}],
        "red_team": 0,
    },
    "C": {
        "title": "Coverage stress test",
        "description": "Three goals, a red card, an own goal and a missed penalty; the Writer is red-teamed with "
                       "unsupported sentences that must be caught and revised.",
        "home": "Kingsmead United", "away": "Port Albion", "seed": 31,
        "plants": [Plant("goal", "H", 12 * M, 12 * M, player="Lindqvist", label="opener"),
                   Plant("goal", "A", 40 * M, 40 * M, player="Ferreira", label="equaliser"),
                   Plant("red_card", "A", 55 * M, 55 * M, player="Bakari", label="away red card"),
                   Plant("own_goal", "H", 66 * M, 66 * M, player="Ivanov", label="own goal (Kingsmead benefit)"),
                   Plant("goal", "H", 77 * M, 77 * M, player="Adeyemi", label="third goal"),
                   Plant("missed_penalty", "H", 84 * M, 84 * M, player="Santos", label="missed penalty")],
        "corrections": [], "red_team": 2,
    },
}


def build(fid: str, match_id: str) -> dict[str, Any]:
    spec = FIXTURES[fid]
    m = generate(match_id=match_id, seed=spec["seed"], plants=spec["plants"], home_name=spec["home"],
                 away_name=spec["away"], natural_goals=False)
    events: list[Event] = m.events
    by_id = {e.event_id: e for e in events}
    corrections = []
    for c in spec["corrections"]:
        plant = m.truth[c["plant_index"]]
        target = by_id[plant["event_id"]]
        corrections.append({"event_id": plant["event_id"], "at_t": target.t + c["after_s"], "changes": c["changes"],
                            "reason": c["reason"], "before": {k: getattr(target, k) for k in c["changes"]}})
    obligations = [{"event_id": e.event_id, "minute": e.minute,
                    "label": "Own goal" if e.type == "own_goal" else ("Red card" if e.type == "card" else "Goal"),
                    "team_id": e.team_id, "player": e.player}
                   for e in events if (e.type == "shot" and e.outcome == "goal") or e.type == "own_goal"
                   or (e.type == "card" and e.outcome == "red")]
    final = {"HOME": 0, "AWAY": 0}
    for e in events:
        if (e.type == "shot" and e.outcome == "goal") or e.type == "own_goal":
            final[e.team_id] += 1
    truth = [t for t in m.truth if t["pattern"] in ("momentum_shift", "counterattack", "standout")]
    expected_turning_points = [t for t in m.truth if t["pattern"] == "red_card"]
    return {"id": fid, "title": spec["title"], "description": spec["description"], "home": spec["home"],
            "away": spec["away"], "events": events, "truth": truth, "obligations": obligations,
            "corrections": corrections, "red_team": spec["red_team"], "final_score": final,
            "expected_turning_points": expected_turning_points, "planted": m.truth}


def export(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for fid in FIXTURES:
        f = build(fid, f"fixture{fid}")
        p = folder / f"fixture_{fid}.json"
        p.write_text(json.dumps({**{k: v for k, v in f.items() if k != "events"},
                                 "events": [e.to_dict() for e in f["events"]]}, indent=1, default=str))
        out.append(p)
    return out


if __name__ == "__main__":
    for p in export(Path(__file__).resolve().parents[1] / "data" / "fixtures"):
        print("wrote", p)
