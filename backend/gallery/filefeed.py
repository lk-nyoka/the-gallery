"""Adapter: any event file (CSV, JSON, JSON lines) -> The Gallery's event interface.

Built for the organisers' synthetic dataset, whose exact schema we don't know yet. It recognises
common column and event names automatically; anything else is set in a mapping file
(data/organiser/mapping.json, see mapping.example.json). Every load returns a report of what was
recognised, what was skipped and what the feed can't support, so nothing is silently guessed.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Optional

from .events import Event

FOLDER = Path(__file__).resolve().parents[1] / "data" / "organiser"

COLUMN_SYNONYMS = {
    "event_id": ["event_id", "eventid", "id", "uuid", "event_uuid"],
    "match_id": ["match_id", "matchid", "game_id", "gameid", "fixture_id", "fixtureid", "match"],
    "t": ["t", "seconds", "elapsed", "elapsed_seconds", "time_seconds", "timestamp_s", "secs"],
    "timestamp": ["timestamp", "time", "match_time", "matchclock", "clock", "game_clock"],
    "minute": ["minute", "min", "match_minute"],
    "second": ["second", "sec"],
    "period": ["period", "half", "period_id", "phase"],
    "team": ["team", "team_name", "teamname", "team_id", "teamid", "side", "possession_team"],
    "type": ["type", "event_type", "eventtype", "event", "action", "action_type", "event_name", "eventname"],
    "outcome": ["outcome", "result", "event_outcome", "shot_outcome", "outcome_type"],
    "player": ["player", "player_name", "playername", "player_id", "playerid"],
    "x": ["x", "start_x", "location_x", "pos_x", "startx"],
    "y": ["y", "start_y", "location_y", "pos_y", "starty"],
    "end_x": ["end_x", "endx", "pass_end_x", "end_location_x"],
    "end_y": ["end_y", "endy", "pass_end_y", "end_location_y"],
    "xg": ["xg", "expected_goals", "shot_xg", "statsbomb_xg", "x_g"],
    "card": ["card", "card_type", "cardtype"],
    "home_away": ["home_away", "homeaway", "venue_side"],
}

TYPE_SYNONYMS = {
    "pass": ["pass", "passes", "pass_completed", "completed_pass"],
    "carry": ["carry", "dribble", "carries"],
    "regain": ["regain", "recovery", "ball_recovery", "interception", "tackle", "tackle_won", "ball_won",
               "turnover_won", "steal"],
    "box_entry": ["box_entry", "penalty_area_entry", "entry_box", "box entry"],
    "shot": ["shot", "shots", "attempt", "shot_on_target", "shot_off_target", "shot_blocked", "shot_saved"],
    "goal": ["goal", "goals"],
    "own_goal": ["own_goal", "owngoal", "own goal"],
    "corner": ["corner", "corner_kick", "corner_awarded"],
    "foul": ["foul", "foul_committed", "foul committed"],
    "card": ["card", "yellow_card", "red_card", "booking", "second_yellow"],
    "sub": ["sub", "substitution", "player_on", "player_off"],
    "kick_off": ["kick_off", "kickoff", "half_start", "period_start"],
    "period_end": ["period_end", "half_end", "end_of_half", "full_time", "half_time"],
}

OUTCOME_SYNONYMS = {
    "goal": ["goal", "scored"],
    "on_target": ["on_target", "saved", "save", "on target", "ontarget", "saved_to_post"],
    "off_target": ["off_target", "off target", "wide", "miss", "missed", "post", "bar", "woodwork", "wayward", "off t"],
    "blocked": ["blocked", "block"],
}
FAILED = {"incomplete", "failed", "unsuccessful", "out", "lost", "fail", "false", "0"}

NEVER_SUPPORTED = ["fatigue", "morale", "confidence", "intent", "emotion"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")


def _lookup(table: dict[str, list[str]], value: str) -> Optional[str]:
    v = _norm(value)
    for key, syns in table.items():
        if v == key or v in (_norm(s) for s in syns):
            return key
    for key, syns in table.items():  # loose contains-match, e.g. "Shot - Saved"
        if any(len(_norm(s)) >= 4 and _norm(s) in v for s in [key, *syns]):
            return key
    return None


def _read(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict):
        for k in ("events", "data", "items", "rows"):
            if isinstance(data.get(k), list):
                return data[k]
        raise ValueError("JSON file has no list of events (expected a list, or an 'events' field)")
    return data


def _flatten(row: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in row.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "_"))
        elif isinstance(v, list) and len(v) in (2, 3) and all(isinstance(x, (int, float)) for x in v):
            out[key + "_x"], out[key + "_y"] = v[0], v[1]
        else:
            out[key] = v
    return out


def load_mapping() -> dict:
    p = FOLDER / "mapping.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def detect_columns(rows: list[dict], mapping: dict) -> dict[str, Optional[str]]:
    cols: list[str] = []
    for r in rows[:500]:          # JSON rows can have different fields: use the union
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    normed = {_norm(c): c for c in cols}
    found: dict[str, Optional[str]] = {}
    for field, syns in COLUMN_SYNONYMS.items():
        if mapping.get("columns", {}).get(field):
            found[field] = mapping["columns"][field]
            continue
        found[field] = next((normed[_norm(s)] for s in syns if _norm(s) in normed), None)
    # flattened locations like location_x
    for f, suffix in (("x", "location_x"), ("y", "location_y")):
        if not found[f] and suffix in normed:
            found[f] = normed[suffix]
    return found


def list_files() -> list[dict]:
    FOLDER.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(FOLDER.iterdir()):
        if p.suffix.lower() in (".csv", ".json", ".jsonl", ".ndjson") and not p.name.startswith("mapping"):
            out.append({"name": p.name, "bytes": p.stat().st_size, "example": p.name.startswith("example")})
    return out


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _seconds(row: dict, col: dict, period: int) -> Optional[float]:
    if col["t"] and _num(row.get(col["t"])) is not None:
        return _num(row[col["t"]])
    if col["minute"] and _num(row.get(col["minute"])) is not None:
        return _num(row[col["minute"]]) * 60 + (_num(row.get(col["second"])) or 0 if col["second"] else 0)
    if col["timestamp"] and row.get(col["timestamp"]):
        parts = str(row[col["timestamp"]]).replace("+", ":").split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:      # hh:mm:ss or mm:ss:ms ambiguity: assume hh:mm:ss
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        return nums[0]
    return None


def load(name: str, match_id: str, match_key: Optional[str] = None) -> dict[str, Any]:
    path = (FOLDER / name).resolve()
    if FOLDER.resolve() not in path.parents or not path.exists():
        raise ValueError(f"file {name} not found in data/organiser/")
    mapping = load_mapping()
    rows = [_flatten(r) for r in _read(path)]
    if not rows:
        raise ValueError("file has no rows")
    col = detect_columns(rows, mapping)
    missing = [f for f in ("type", "team") if not col[f]]
    if missing or not (col["t"] or col["minute"] or col["timestamp"]):
        raise ValueError(f"Couldn't find columns for: {', '.join(missing + ([] if (col['t'] or col['minute'] or col['timestamp']) else ['time']))}. "
                         f"Columns in file: {', '.join(dict.fromkeys(k for r in rows[:500] for k in r))}. Set them in data/organiser/mapping.json.")
    # one match per load
    matches = sorted({str(r.get(col["match_id"])) for r in rows}) if col["match_id"] else []
    if len(matches) > 1:
        key = match_key or matches[0]
        rows = [r for r in rows if str(r.get(col["match_id"])) == key]
    else:
        key = matches[0] if matches else None
    # teams: explicit mapping, home/away column, or first two names in order of appearance
    teams_seen: list[str] = []
    for r in rows:
        tv = str(r.get(col["team"]) or "").strip()
        if tv and tv not in teams_seen:
            teams_seen.append(tv)
    home = mapping.get("home_team")
    if not home and col["home_away"]:
        home = next((str(r[col["team"]]) for r in rows if _norm(r.get(col["home_away"], "")) == "home"), None)
    if not home and any(_norm(t) in ("home", "away") for t in teams_seen):
        home = next(t for t in teams_seen if _norm(t) == "home")
    home = home or (teams_seen[0] if teams_seen else "Home")
    away = next((t for t in teams_seen if t != home), "Away")
    side = {home: "HOME", away: "AWAY"}
    names = {"HOME": mapping.get("home_name", home if _norm(home) != "home" else "Home"),
             "AWAY": mapping.get("away_name", away if _norm(away) != "away" else "Away")}
    # coordinate scale
    xs = [_num(r.get(col["x"])) for r in rows if col["x"]]
    xs = [x for x in xs if x is not None]
    scale = mapping.get("coordinate_max_x") or (120.0 if xs and max(xs) > 105 else 105.0 if xs and max(xs) > 100 else 100.0)
    yscale = mapping.get("coordinate_max_y") or (80.0 if scale == 120.0 else 68.0 if scale == 105.0 else 100.0)
    type_map = {_norm(k): v for k, v in (mapping.get("types") or {}).items()}

    explicit_box = any((type_map.get(_norm(str(r.get(col["type"]) or ""))) or
                        _lookup(TYPE_SYNONYMS, str(r.get(col["type"]) or ""))) == "box_entry" for r in rows)
    events: list[Event] = []
    skipped: dict[str, int] = {}
    mapped_types: dict[str, str] = {}
    period_offset = {1: 0.0}
    last_t = {}
    for i, r in enumerate(rows):
        raw_type = str(r.get(col["type"]) or "")
        kind = type_map.get(_norm(raw_type)) or _lookup(TYPE_SYNONYMS, raw_type)
        if not kind:
            skipped[raw_type] = skipped.get(raw_type, 0) + 1
            continue
        mapped_types[raw_type] = kind
        period = int(_num(r.get(col["period"])) or 1) if col["period"] else 1
        sec = _seconds(r, col, period)
        if sec is None:
            skipped[f"{raw_type} (no time)"] = skipped.get(f"{raw_type} (no time)", 0) + 1
            continue
        # clock: if second-half times restart at 0, add 45:00
        clock = sec + (2700 if period == 2 and sec < 2700 else 0)
        t = clock + (60 if period == 2 else 0)
        last_t[period] = t
        team_raw = str(r.get(col["team"]) or "").strip()
        team = side.get(team_raw, "HOME")
        outcome_raw = str(r.get(col["outcome"]) or "") if col["outcome"] else ""
        x, y = _num(r.get(col["x"])) if col["x"] else None, _num(r.get(col["y"])) if col["y"] else None
        ex, ey = (_num(r.get(col["end_x"])) if col["end_x"] else None,
                  _num(r.get(col["end_y"])) if col["end_y"] else None)
        sx = lambda v, s: None if v is None else round(100.0 * v / s, 1)
        x, y, ex, ey = sx(x, scale), sx(y, yscale), sx(ex, scale), sx(ey, yscale)
        player = (str(r.get(col["player"])) if col["player"] and r.get(col["player"]) not in (None, "") else None)
        eid = f"{match_id}-f{(str(r.get(col['event_id'])) if col['event_id'] else str(i))}"
        kw: dict[str, Any] = {"x": x, "y": y, "end_x": ex, "end_y": ey, "player": player}
        outcome = None
        if kind == "goal":
            kind, outcome = "shot", "goal"
        elif kind == "shot":
            outcome = _lookup(OUTCOME_SYNONYMS, outcome_raw or raw_type) or "off_target"
            if "goal" in _norm(outcome_raw):
                outcome = "goal"
        elif kind == "pass" and _norm(outcome_raw) in FAILED:
            skipped["pass (incomplete)"] = skipped.get("pass (incomplete)", 0) + 1
            continue
        elif kind == "card":
            card_val = _norm(str(r.get(col["card"]) or "") if col["card"] else "") or _norm(outcome_raw) or _norm(raw_type)
            outcome = "red" if ("red" in card_val or "second" in card_val) else "yellow"
        elif kind == "kick_off":
            kw.update(x=50.0, y=50.0)
        xg = _num(r.get(col["xg"])) if col["xg"] else None
        events.append(Event(event_id=eid, match_id=match_id, t=round(t, 2), period=min(period, 2), team_id=team,
                            type=kind, outcome=outcome, xg=xg, clock=round(clock, 2), source="file", **kw))
        # derived box entries, the same rule as the StatsBomb adapter
        if not explicit_box and kind in ("pass", "carry") and ex is not None and ey is not None and ex >= 83 and 21 <= ey <= 79 \
                and not (x is not None and x >= 83 and y is not None and 21 <= y <= 79):
            events.append(Event(event_id=eid + "b", match_id=match_id, t=round(t + 0.01, 2), period=min(period, 2),
                                team_id=team, type="box_entry", x=ex, y=ey, clock=round(clock, 2), source="file"))
    events.sort(key=lambda e: (e.t, e.event_id))
    if not any(e.type == "kick_off" for e in events) and events:
        events.insert(0, Event(event_id=f"{match_id}-fKO", match_id=match_id, t=max(0.0, events[0].t - 1), period=1,
                               team_id="HOME", type="kick_off", x=50.0, y=50.0, clock=0.0, source="file"))
    present = {e.type for e in events}
    caps = {"passes": "pass" in present, "regains": "regain" in present,
            "box_entries": "box_entry" in present, "xg": any(e.xg is not None for e in events),
            "shot_position": any(e.type == "shot" and e.x is not None for e in events)}
    goals = {"HOME": 0, "AWAY": 0}
    for e in events:
        if (e.type == "shot" and e.outcome == "goal") or e.type == "own_goal":
            goals[e.team_id] += 1
    return {
        "meta": {"source": f"Data file: {name}", "file": name, "match_key": key, "matches_in_file": matches,
                 "home": names["HOME"], "away": names["AWAY"], "date": mapping.get("date"),
                 "official_result": mapping.get("official_result")},
        "events": events,
        "caps": caps,
        "report": {"rows": len(rows), "events": len(events), "columns": {k: v for k, v in col.items() if v},
                   "types_recognised": mapped_types, "skipped": skipped, "coordinate_scale": [scale, yscale],
                   "final_score_from_events": goals, "never_supported": NEVER_SUPPORTED,
                   "box_entries": "from file" if explicit_box else ("derived from pass/carry end points"
                                                                    if caps["box_entries"] else "not available")},
    }
