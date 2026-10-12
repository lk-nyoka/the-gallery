"""Adapter: ESPN match feed -> The Gallery's event interface.

Gives every match ESPN covers (any date, many leagues), finished or live: goals, every shot with
player, assister, outcome and pitch position, cards, corners, fouls and substitutions, plus official
team stats. It's an unofficial, undocumented public feed: used here for testing and demos only.

What it does NOT contain: passes, carries, ball regains or box entries. The Gallery declares that
(feed capabilities) so detectors and the verifier never claim what the feed can't show.

Endpoints
  scoreboard: {BASE}/{league}/scoreboard?dates=YYYYMMDD
  summary:    {BASE}/{league}/summary?event={id}
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from .events import Event

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
CACHE = Path(__file__).resolve().parents[1] / "data" / "espn"

LEAGUES = {
    "eng.1": "Premier League", "esp.1": "LaLiga", "ger.1": "Bundesliga", "ita.1": "Serie A",
    "fra.1": "Ligue 1", "uefa.champions": "Champions League", "rsa.1": "South African Premiership",
    "usa.1": "MLS", "eng.2": "Championship",
}

CAPS = {"passes": False, "regains": False, "box_entries": False, "xg": False, "shot_position": True}

SHOT_TYPES = {
    "shot-on-target": "on_target", "shot-off-target": "off_target", "shot-blocked": "blocked",
    "shot-hit-woodwork": "off_target", "penalty---saved": "on_target", "penalty---missed": "off_target",
}

_score_re = re.compile(r"Goal!\s+(.+?)\s+(\d+),\s+(.+?)\s+(\d+)\.")


def _get(url: str, cache_path: Optional[Path] = None, cache_if=None) -> dict:
    if cache_path and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "TheGallery/0.2"})
    r.raise_for_status()
    data = r.json()
    if cache_path and (cache_if is None or cache_if(data)):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _state(status: dict) -> str:
    return (status.get("type") or {}).get("state", "pre")   # pre | in | post


def scoreboard(date: str, league: str = "eng.1") -> list[dict]:
    """Matches on a date (YYYYMMDD). Past dates are cached once every match has finished."""
    cache = CACHE / "scoreboard" / f"{league}-{date}.json"
    legacy = CACHE / "scoreboard" / f"{date}.json"   # bundled sample (Premier League)
    if league == "eng.1" and legacy.exists() and not cache.exists():
        cache = legacy
    data = _get(f"{BASE}/{league}/scoreboard?dates={date}", cache,
                cache_if=lambda d: d.get("events") and all(_state(e["status"]) == "post" for e in d["events"]))
    out = []
    for e in data.get("events", []):
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        out.append({
            "espn_id": e["id"], "league": league, "league_name": LEAGUES.get(league, league),
            "date": e.get("date", "")[:10], "kickoff": e.get("date"),
            "home": teams["home"]["team"]["displayName"], "away": teams["away"]["team"]["displayName"],
            "home_score": teams["home"].get("score"), "away_score": teams["away"].get("score"),
            "state": _state(e["status"]), "status": (e["status"].get("type") or {}).get("description"),
            "clock": e["status"].get("displayClock"),
        })
    return out


def summary(espn_id: str, league: str = "eng.1", fresh: bool = False) -> dict:
    cache = CACHE / "summary" / f"{espn_id}.json"
    if fresh:
        cache_path = None
    else:
        cache_path = cache
    data = _get(f"{BASE}/{league}/summary?event={espn_id}", cache_path,
                cache_if=lambda d: _state(d["header"]["competitions"][0]["status"]) == "post")
    if fresh and _state(data["header"]["competitions"][0]["status"]) == "post":
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def _clock_seconds(clock: dict) -> float:
    """Match clock in seconds. Stoppage time ("45'+2'") becomes minute 47, like event data providers."""
    disp = (clock or {}).get("displayValue") or ""
    value = float((clock or {}).get("value") or 0.0)
    m = re.match(r"(\d+)'\+(\d+)'", disp)
    if m:
        return (int(m.group(1)) + int(m.group(2)) - 1) * 60 + 30.0
    return value


def _wall(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location(play: dict, text: str) -> tuple[Optional[float], Optional[float]]:
    x, y = play.get("fieldPositionX"), play.get("fieldPositionY")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return round(float(x), 1), round(float(y), 1)
    t = text.lower()
    if "very close range" in t or "six yard box" in t or "centre of the box" in t:
        return 92.0, 50.0
    if "side of the box" in t:
        return 88.0, 30.0 if "left side" in t else 70.0
    if "outside the box" in t:
        return 76.0, 50.0
    if "more than 35 yards" in t or "long range" in t:
        return 62.0, 50.0
    return None, None


def parse(data: dict, match_id: str) -> dict[str, Any]:
    """Turn an ESPN summary into events (deduplicated by play id, in match order)."""
    comp = data["header"]["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    home_name, away_name = teams["home"]["team"]["displayName"], teams["away"]["team"]["displayName"]
    side_by_id = {teams["home"]["team"]["id"]: "HOME", teams["away"]["team"]["id"]: "AWAY"}
    side_by_name = {home_name: "HOME", away_name: "AWAY"}

    def side(team: Optional[dict]) -> Optional[str]:
        if not team:
            return None
        return side_by_id.get(str(team.get("id"))) or side_by_name.get(team.get("displayName"))

    # merge commentary plays and key events, keep first occurrence of each play id
    plays: dict[str, tuple[int, dict]] = {}
    for c in data.get("commentary", []) or []:
        p = c.get("play")
        if p and p.get("id") and p["id"] not in plays:
            plays[p["id"]] = (int(c.get("sequence", 0)), p)
    for i, p in enumerate(data.get("keyEvents", []) or []):
        if p.get("id") and p["id"] not in plays:
            plays[p["id"]] = (10_000 + i, p)

    def order(item):
        seq, p = item
        return ((p.get("period") or {}).get("number", 1), _clock_seconds(p.get("clock") or {}), seq)

    ordered = sorted(plays.values(), key=order)
    walls = [_wall(p.get("wallclock")) for _, p in ordered]
    start_wall = next((w for w in walls if w), None)

    events: list[Event] = []
    last_t = -1.0
    score = {"HOME": 0, "AWAY": 0}
    p1_last_clock = 0.0

    def add(pid: str, suffix: str, t: float, period: int, clock: float, team: str, typ: str, **kw) -> None:
        events.append(Event(event_id=f"{match_id}-x{pid}{suffix}", match_id=match_id, t=round(t, 2),
                            period=period, team_id=team, type=typ, clock=round(clock, 1), source="espn", **kw))

    for (seq, p), wall in zip(ordered, walls):
        typ = (p.get("type") or {}).get("type") or ""
        period = min((p.get("period") or {}).get("number", 1), 2)
        clock = _clock_seconds(p.get("clock") or {})
        if period == 1:
            p1_last_clock = max(p1_last_clock, clock)
        # elapsed time: from wall clock when available (handles stoppage and the break), else from the clock
        if wall and start_wall:
            t = (wall - start_wall).total_seconds()
        else:
            t = clock if period == 1 else p1_last_clock + 900 + (clock - 2700)
        t = max(t, last_t + 0.01)
        last_t = t
        pid = str(p["id"])
        text = p.get("text") or ""
        parts = [(a.get("athlete") or {}).get("displayName") for a in p.get("participants", []) or []]
        team = side(p.get("team"))

        if typ in ("kickoff", "start-2nd-half"):
            add(pid, "", t, period, clock, "HOME", "kick_off", x=50.0, y=50.0)
        elif typ == "halftime":
            add(pid, "", t, 1, clock, "HOME", "period_end")
        elif typ in ("end-regular-time", "end-game", "full-time"):
            add(pid, "", t, 2, clock, "HOME", "period_end")
        elif typ.startswith("goal") or typ in ("penalty---scored", "own-goal"):
            m = _score_re.search(text)
            scorer_side = team
            if m:  # trust the running score in the text: it tells us which side's tally went up
                new = {side_by_name.get(m.group(1), "HOME"): int(m.group(2)),
                       side_by_name.get(m.group(3), "AWAY"): int(m.group(4))}
                up = [s for s in ("HOME", "AWAY") if new.get(s, score[s]) > score[s]]
                if up:
                    scorer_side = up[0]
                score.update({k: v for k, v in new.items() if k in score})
            else:
                score[scorer_side or "HOME"] += 1
            x, y = _location(p, text)
            own = "own goal" in text.lower() or typ == "own-goal"
            if own:
                add(pid, "", t, period, clock, scorer_side or "HOME", "own_goal", player=parts[0] if parts else None,
                    x=x, y=y)
            else:
                add(pid, "", t, period, clock, scorer_side or "HOME", "shot", outcome="goal",
                    player=parts[0] if parts else None, x=x, y=y)
                if len(parts) > 1 and parts[1]:
                    add(pid, "k", t - 0.005, period, clock, scorer_side or "HOME", "key_pass", player=parts[1])
        elif typ in SHOT_TYPES and team:
            x, y = _location(p, text)
            add(pid, "", t, period, clock, team, "shot", outcome=SHOT_TYPES[typ], player=parts[0] if parts else None,
                x=x, y=y)
            if len(parts) > 1 and parts[1] and "Assisted by" in text:
                add(pid, "k", t - 0.005, period, clock, team, "key_pass", player=parts[1])
        elif typ == "corner-awarded" and team:
            add(pid, "", t, period, clock, team, "corner", x=99.0, y=1.0)
        elif typ in ("foul", "handball") and team:
            add(pid, "", t, period, clock, team, "foul", player=parts[0] if parts else None)
        elif typ in ("yellow-card", "red-card", "yellow-red-card") and team:
            outcome = "yellow" if typ == "yellow-card" else "red"
            add(pid, "", t, period, clock, team, "card", outcome=outcome, player=parts[0] if parts else None)
        elif typ == "substitution" and team:
            add(pid, "", t, period, clock, team, "sub", player=parts[0] if parts else None)

    events.sort(key=lambda e: (e.t, e.event_id))
    status = comp["status"]
    stats = {}
    for tm in (data.get("boxscore") or {}).get("teams", []):
        s = side(tm.get("team"))
        if s:
            stats[s] = {x["name"]: x.get("displayValue") for x in tm.get("statistics", [])}
    return {
        "meta": {
            "source": "ESPN match feed (unofficial)", "espn_id": data["header"]["id"],
            "date": (comp.get("date") or "")[:10], "home": home_name, "away": away_name,
            "home_score": int(teams["home"].get("score") or 0), "away_score": int(teams["away"].get("score") or 0),
            "state": _state(status), "status": (status.get("type") or {}).get("description"),
            "clock": status.get("displayClock"),
            "venue": ((data.get("gameInfo") or {}).get("venue") or {}).get("fullName"),
            "team_stats": stats,
            "official_result": ({"HOME": int(teams["home"].get("score") or 0),
                                 "AWAY": int(teams["away"].get("score") or 0)}
                                if _state(status) == "post" else None),
        },
        "events": events,
        "caps": CAPS,
    }
