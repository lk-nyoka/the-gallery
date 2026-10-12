"""Adapter: StatsBomb Open Data -> The Gallery's minimal event interface.

Real, free, event-level match data published by StatsBomb at
https://github.com/statsbomb/open-data (attribution required: "Data: StatsBomb Open Data").

Files are downloaded on first use and cached in backend/data/statsbomb/.

Mapping
  Pass (complete)                 -> pass   (+ box_entry when it ends in the box from outside)
  Pass with pass.type = Corner    -> corner (+ the pass)
  Carry                           -> carry  (+ box_entry when it ends in the box from outside)
  Shot                            -> shot   (goal | on_target | off_target | blocked), xg = statsbomb_xg
  Own Goal For                    -> own_goal (team = beneficiary, player = defender who scored it)
  Ball Recovery (not failed), Interception (won), Duel/Tackle (won) -> regain
  Foul Committed                  -> foul   (team = team that committed it)
  Substitution                    -> sub
  Half Start / Half End           -> kick_off / period_end

Coordinates: StatsBomb uses a 120 x 80 pitch in the acting team's attacking direction; we scale to 0-100.
Time: `clock` is the official match clock (2nd half restarts at 45:00); `t` is continuous elapsed time,
so first-half stoppage time never overlaps the second half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx

from .events import Event

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
CACHE = Path(__file__).resolve().parents[1] / "data" / "statsbomb"

SHOT_OUTCOME = {
    "Goal": "goal", "Saved": "on_target", "Saved To Post": "on_target",
    "Saved Off Target": "off_target", "Off T": "off_target", "Post": "off_target", "Wayward": "off_target",
    "Blocked": "blocked",
}
WON = {"Won", "Success In Play", "Success To Team", "Success"}
HALF_BREAK_S = 60.0  # gap inserted between halves on the continuous timeline


def _get(rel: str) -> Any:
    path = CACHE / rel
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get(f"{BASE}/{rel}", timeout=60, follow_redirects=True)
    r.raise_for_status()
    path.write_text(r.text, encoding="utf-8")
    return r.json()


def competitions() -> list[dict]:
    return _get("competitions.json")


def matches(competition_id: int = 2, season_id: int = 27) -> list[dict]:
    out = []
    for m in _get(f"matches/{competition_id}/{season_id}.json"):
        out.append({
            "match_id": m["match_id"], "date": m["match_date"],
            "home": m["home_team"]["home_team_name"], "away": m["away_team"]["away_team_name"],
            "home_score": m["home_score"], "away_score": m["away_score"],
            "competition": m["competition"]["competition_name"], "season": m["season"]["season_name"],
            "stadium": (m.get("stadium") or {}).get("name"),
        })
    return sorted(out, key=lambda x: x["date"])


def _in_box(loc: Optional[list]) -> bool:
    return bool(loc) and loc[0] >= 102 and 18 <= loc[1] <= 62


def _xy(loc: Optional[list]) -> tuple[Optional[float], Optional[float]]:
    if not loc:
        return None, None
    return round(loc[0] / 1.2, 1), round(loc[1] / 0.8, 1)


def load(sb_match_id: int, competition_id: int = 2, season_id: int = 27,
         match_id: Optional[str] = None) -> dict[str, Any]:
    """Return {"meta": ..., "events": [Event], "lineups": {...}} for one real match."""
    info = next((m for m in matches(competition_id, season_id) if m["match_id"] == sb_match_id), None)
    if info is None:
        raise ValueError(f"StatsBomb match {sb_match_id} not found in competition {competition_id}/{season_id}")
    raw = _get(f"events/{sb_match_id}.json")
    lineups = _get(f"lineups/{sb_match_id}.json")
    mid = match_id or f"sb{sb_match_id}"

    # names: prefer the short nickname (e.g. "Firmino" instead of the full legal name)
    nick, country = {}, {}
    for team in lineups:
        for p in team["lineup"]:
            nick[p["player_id"]] = p.get("player_nickname") or p["player_name"]
            country[p["player_id"]] = (p.get("country") or {}).get("name")
    side = {}
    for team in lineups:
        side[team["team_name"]] = "HOME" if team["team_name"] == info["home"] else "AWAY"

    # continuous time: t = clock in period 1; period 2 starts after the last period-1 event + a gap
    p1_end = max((e["minute"] * 60 + e["second"]) for e in raw if e["period"] == 1)
    p2_start_t = p1_end + HALF_BREAK_S

    def times(e: dict) -> tuple[float, float]:
        clock = e["minute"] * 60 + e["second"] + (int(e["timestamp"][-3:]) / 1000 if e.get("timestamp") else 0)
        if e["period"] == 1:
            return clock, clock
        if e["period"] == 2:
            return p2_start_t + (clock - 2700), clock
        return p2_start_t + 3000 + (clock - 5400), clock  # extra time (not in league data)

    out: list[Event] = []
    seen_half: set[tuple[int, str]] = set()

    def add(e: dict, typ: str, suffix: str = "", **kw) -> None:
        t, clock = times(e)
        team = side.get(e["team"]["name"], "HOME")
        pid = (e.get("player") or {}).get("id")
        player = kw.pop("player", nick.get(pid, (e.get("player") or {}).get("name")))
        out.append(Event(event_id=f"{mid}-{e['index']:05d}{suffix}", match_id=mid, t=round(t, 2),
                         period=min(e["period"], 2), team_id=kw.pop("team_id", team), type=typ, player=player,
                         clock=round(clock, 2), source="statsbomb", **kw))

    for e in sorted(raw, key=lambda x: x["index"]):
        name = e["type"]["name"]
        loc = e.get("location")
        x, y = _xy(loc)
        if name == "Half Start" and (e["period"], "start") not in seen_half:
            seen_half.add((e["period"], "start"))
            add(e, "kick_off", x=50.0, y=50.0)
        elif name == "Half End" and (e["period"], "end") not in seen_half:
            seen_half.add((e["period"], "end"))
            add(e, "period_end")
        elif name == "Pass":
            p = e["pass"]
            if (p.get("type") or {}).get("name") == "Corner":
                add(e, "corner", "c", x=x, y=y)
            if p.get("outcome"):  # incomplete / out / offside: possession lost, not a pass completion
                continue
            ex, ey = _xy(p.get("end_location"))
            add(e, "pass", outcome="complete", x=x, y=y, end_x=ex, end_y=ey)
            if _in_box(p.get("end_location")) and not _in_box(loc):
                rec = (p.get("recipient") or {}).get("id")
                add(e, "box_entry", "b", x=ex, y=ey, player=nick.get(rec) if rec else None)
        elif name == "Carry":
            end = e["carry"].get("end_location")
            ex, ey = _xy(end)
            add(e, "carry", x=x, y=y, end_x=ex, end_y=ey)
            if _in_box(end) and not _in_box(loc):
                add(e, "box_entry", "b", x=ex, y=ey)
        elif name == "Shot":
            s = e["shot"]
            add(e, "shot", outcome=SHOT_OUTCOME.get(s["outcome"]["name"], "off_target"), x=x, y=y,
                xg=round(s.get("statsbomb_xg", 0.0), 3))
        elif name == "Own Goal Against":
            # the defender's team conceded; record it for the benefiting team
            benefit = "AWAY" if side.get(e["team"]["name"]) == "HOME" else "HOME"
            add(e, "own_goal", x=x, y=y, team_id=benefit)
        elif name == "Ball Recovery":
            if not (e.get("ball_recovery") or {}).get("recovery_failure"):
                add(e, "regain", x=x, y=y)
        elif name == "Interception":
            if (e.get("interception", {}).get("outcome") or {}).get("name") in WON:
                add(e, "regain", x=x, y=y)
        elif name == "Duel":
            d = e.get("duel", {})
            if (d.get("type") or {}).get("name") == "Tackle" and (d.get("outcome") or {}).get("name") in WON:
                add(e, "regain", x=x, y=y)
        elif name == "Foul Committed":
            add(e, "foul", x=x, y=y)
        elif name == "Substitution":
            add(e, "sub")

    out.sort(key=lambda ev: (ev.t, ev.event_id))
    players = {"HOME": [], "AWAY": []}
    brazil = []
    for team in lineups:
        for p in team["lineup"]:
            n = nick[p["player_id"]]
            players[side[team["team_name"]]].append(n)
            if country.get(p["player_id"]) == "Brazil":
                brazil.append(n)
    za = [nick[p["player_id"]] for team in lineups for p in team["lineup"]
          if country.get(p["player_id"]) == "South Africa"]
    return {
        "meta": {**info, "source": "StatsBomb Open Data", "sb_match_id": sb_match_id,
                 "official_result": {"HOME": info["home_score"], "AWAY": info["away_score"]}},
        "events": out,
        "players": players,
        "market_overrides": {"br": {"focus_players": brazil}, "za": {"focus_players": za}},
    }
