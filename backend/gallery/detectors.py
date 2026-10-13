"""Deterministic detectors. Code finds the story; the model only phrases it.

Every insight carries:
  - evidence: the event IDs behind it
  - facts: true, machine-checkable claims computed here (the Writer may only use these)
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from . import events as ev

_ids = itertools.count(1)


@dataclass
class Insight:
    insight_id: str
    match_id: str
    detector: str
    t: float
    from_t: float
    to_t: float
    team_id: str
    players: list[str]
    metrics: dict[str, Any]
    evidence: list[str]
    confidence: float
    facts: list[dict[str, Any]] = field(default_factory=list)
    clock_offset: float = 0.0      # t - match clock at the trigger event (2nd-half stoppage-time shift)

    @property
    def minute(self) -> int:
        return int((self.t - self.clock_offset) // 60) + 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["minute"] = self.minute
        return d


def _fact(key: str, metric: str, params: dict, value: Any, op: str = "==") -> dict:
    return {"key": key, "metric": metric, "params": params, "op": op, "value": value}


def _r(t: float) -> float:
    return round(t, 3)


def _w(t0: float, t1: float) -> dict:
    # the SAME rounded bounds are used for counting (_window) and for the stated facts, so the
    # verifier's SQL re-computes exactly what the detector counted
    return {"from_t": _r(t0), "to_t": _r(t1)}


class DetectorSuite:
    """Runs all detectors incrementally as events arrive."""

    MOMENTUM_WINDOW = 480.0        # 8 minutes
    MOMENTUM_SHARE = 0.70
    MOMENTUM_PREV_MAX = 0.50
    MOMENTUM_MIN_THREAT = 9.0
    MOMENTUM_COOLDOWN = 900.0

    COUNTER_MAX_SECONDS = 15.0
    COUNTER_MAX_PASSES = 4

    STANDOUT_WINDOW = 900.0        # 15 minutes
    STANDOUT_MIN_ACTIONS = 5
    STANDOUT_TEAM_SHARE = 0.35
    STANDOUT_COOLDOWN = 900.0

    THREAT_WINDOW = 600.0          # 10 minutes
    THREAT_MIN_SHOTS = 4
    THREAT_MIN_ON_TARGET = 2
    THREAT_COOLDOWN = 720.0

    KEY_ACTIONS = ["shot", "box_entry", "regain", "key_pass"]

    def __init__(self, match_id: str, home_id: str, away_id: str, passes_available: bool = True,
                 caps: Optional[dict] = None):
        self.match_id = match_id
        self.teams = (home_id, away_id)
        self.events: list[dict[str, Any]] = []
        self.last_fired: dict[tuple, float] = {}
        self.counter_regains: set[str] = set()
        self.last_momentum_check = -1e9
        # what this feed can show; detectors never state facts the feed can't support
        self.caps = {"passes": passes_available, "regains": True, "box_entries": True, **(caps or {})}
        self.passes_available = self.caps["passes"]
        self.reds: list[dict] = []          # red cards so far
        self.turning_fired: set[tuple] = set()
        if not self.passes_available:
            # feeds without passes: threat comes from box entries and shots only
            self.MOMENTUM_MIN_THREAT = 6.0

    def opp(self, team: str) -> str:
        return self.teams[1] if team == self.teams[0] else self.teams[0]

    def _cool(self, key: tuple, t: float, cooldown: float) -> bool:
        last = self.last_fired.get(key)
        return last is None or t - last >= cooldown

    def _window(self, t0: float, t1: float) -> list[dict]:
        t0, t1 = _r(t0), _r(t1)
        return [e for e in self.events if t0 <= e["t"] <= t1]

    def _new(self, **kw) -> Insight:
        last = self.events[-1] if self.events else {}
        offset = (last["t"] - last["clock"]) if last.get("clock") is not None else 0.0
        return Insight(insight_id=f"{self.match_id}-i{next(_ids):04d}", match_id=self.match_id,
                       clock_offset=offset, **kw)

    # ------------------------------------------------------------------
    def on_event(self, e: dict[str, Any]) -> list[Insight]:
        self.events.append(e)
        out: list[Insight] = []
        if e["type"] == ev.CARD and e.get("outcome") == "red":
            r = self.red_card(e)
            if r:
                out.append(r)
        if self.reds and ((e["type"] == ev.SHOT and e.get("outcome") == "goal") or e["type"] == ev.OWN_GOAL
                          or (e["type"] == ev.PERIOD_END and e["period"] == 2)):
            out.extend(self.turning_points(e))
        if e["type"] == ev.OWN_GOAL:
            g = self.goal(e)
            if g:
                out.append(g)
        if e["type"] == ev.SHOT:
            g = self.goal(e)
            if g:
                out.append(g)
            c = self.counterattack(e)
            if c:
                out.append(c)
            th = self.goal_threat(e)
            if th:
                out.append(th)
            ps = self.power_shot(e)
            if ps:
                out.append(ps)
        if e["type"] in (ev.SHOT, ev.BOX_ENTRY, ev.REGAIN, ev.KEY_PASS) and e.get("player"):
            s = self.standout(e)
            if s:
                out.append(s)
        if e["t"] - self.last_momentum_check >= 30:
            self.last_momentum_check = e["t"]
            out.extend(self.momentum(e["t"]))
        return out

    # ------------------------------------------------------------------
    def score_at(self, t: float) -> dict[str, int]:
        s = {self.teams[0]: 0, self.teams[1]: 0}
        for x in self.events:
            if x["t"] <= t and ((x["type"] == ev.SHOT and x["outcome"] == "goal") or x["type"] == ev.OWN_GOAL):
                s[x["team_id"]] += 1
        return s

    def goal(self, e: dict) -> Optional[Insight]:
        own = e["type"] == ev.OWN_GOAL
        if not own and e["outcome"] != "goal":
            return None
        team = e["team_id"]
        score = self.score_at(e["t"])
        build = [x for x in self._window(e["t"] - 20, e["t"]) if x["team_id"] == team and x["type"] != ev.SHOT]
        evidence = [x["event_id"] for x in build[-3:]] + [e["event_id"]]
        facts = [
            _fact("own_goal_by" if own else "scorer", "attribution", {"event_id": e["event_id"]},
                  e.get("player") or "unknown"),
            _fact("team_goals", "score", {"team_id": team, "at_t": e["t"]}, score[team]),
            _fact("opp_goals", "score", {"team_id": self.opp(team), "at_t": e["t"]}, score[self.opp(team)]),
        ]
        if e.get("xg") is not None:
            facts.append(_fact("shot_xg", "shot_xg", {"event_id": e["event_id"]}, round(e["xg"], 2)))
        return self._new(detector="goal", t=e["t"], from_t=e["t"] - 20, to_t=e["t"], team_id=team,
                         players=[p for p in [e.get("player")] if p],
                         metrics={"score_team": score[team], "score_opp": score[self.opp(team)],
                                  "xg": e.get("xg"), "own_goal": own},
                         evidence=evidence, confidence=1.0, facts=facts)

    def counterattack(self, e: dict) -> Optional[Insight]:
        team = e["team_id"]
        recent = self._window(e["t"] - self.COUNTER_MAX_SECONDS, e["t"])
        regain = None
        for x in reversed(recent):
            if x["event_id"] == e["event_id"]:
                continue
            if x["team_id"] != team and x["type"] in (ev.REGAIN, ev.PASS, ev.KICK_OFF):
                break  # opponent had the ball after the regain we might find
            if x["team_id"] == team and x["type"] == ev.REGAIN:
                regain = x
                break
        if not regain or regain["x"] is None or regain["x"] >= ev.OWN_HALF_X:
            return None
        if regain["event_id"] in self.counter_regains:
            return None
        seq = [x for x in recent if regain["t"] <= x["t"] <= e["t"]]
        passes = [x for x in seq if x["type"] == ev.PASS and x["team_id"] == team]
        if len(passes) > self.COUNTER_MAX_PASSES:
            return None
        self.counter_regains.add(regain["event_id"])
        secs = int(round(e["t"] - regain["t"]))
        conf = 0.6 + (0.2 if secs <= 10 else 0) + (0.2 if e["outcome"] in ("goal", "on_target") else 0)
        evidence = [x["event_id"] for x in seq if x["team_id"] == team]
        facts = [
            _fact("seconds", "seconds_between", {"from_event": regain["event_id"], "to_event": e["event_id"]}, secs),
            _fact("regain_player", "attribution", {"event_id": regain["event_id"]}, regain.get("player") or "unknown"),
            _fact("shooter", "attribution", {"event_id": e["event_id"]}, e.get("player") or "unknown"),
            _fact("shot_outcome", "shot_outcome", {"event_id": e["event_id"]}, e["outcome"]),
        ]
        if self.passes_available:
            facts.append(_fact("passes", "passes_between",
                               {"team_id": team, "from_event": regain["event_id"], "to_event": e["event_id"]},
                               len(passes)))
        return self._new(detector="counterattack", t=e["t"], from_t=regain["t"], to_t=e["t"], team_id=team,
                         players=[p for p in [regain.get("player"), e.get("player")] if p],
                         metrics={"seconds": secs, "passes": len(passes) if self.passes_available else None,
                                  "regain_x": regain["x"],
                                  "outcome": e["outcome"]},
                         evidence=evidence, confidence=round(conf, 2), facts=facts)

    def _threat(self, evs: list[dict], team: str) -> dict:
        box = [x for x in evs if x["team_id"] == team and x["type"] == ev.BOX_ENTRY]
        shots = [x for x in evs if x["team_id"] == team and x["type"] == ev.SHOT]
        f3 = [x for x in evs if x["team_id"] == team and x["type"] == ev.PASS
              and (x.get("end_x") or 0) >= ev.FINAL_THIRD_X]
        score = len(box) + 2 * len(shots) + 0.25 * len(f3)
        return {"box": box, "shots": shots, "f3": f3, "score": score}

    def momentum(self, t: float) -> list[Insight]:
        W = self.MOMENTUM_WINDOW
        if t < 2 * W:
            return []
        now = self._window(t - W, t)
        prev = self._window(t - 2 * W, t - W)
        out = []
        for team in self.teams:
            a, b = self._threat(now, team), self._threat(now, self.opp(team))
            pa, pb = self._threat(prev, team), self._threat(prev, self.opp(team))
            tot, ptot = a["score"] + b["score"], pa["score"] + pb["score"]
            if tot == 0 or a["score"] < self.MOMENTUM_MIN_THREAT:
                continue
            share = a["score"] / tot
            pshare = pa["score"] / ptot if ptot else 0.5
            if share < self.MOMENTUM_SHARE or pshare > self.MOMENTUM_PREV_MAX:
                continue
            if not self._cool(("momentum", team), t, self.MOMENTUM_COOLDOWN):
                continue
            self.last_fired[("momentum", team)] = t
            win = _w(t - W, t)
            facts = ([_fact("box_entries", "count", {"team_id": team, "type": "box_entry", **win}, len(a["box"]))]
                     if self.caps["box_entries"] else []) + [
                _fact("shots", "count", {"team_id": team, "type": "shot", **win}, len(a["shots"])),
                _fact("opp_shots", "count", {"team_id": self.opp(team), "type": "shot", **win}, len(b["shots"])),
                _fact("threat_share", "threat_share_pct", {"team_id": team, **win}, round(100 * share)),
            ] + ([_fact("prev_threat_share", "threat_share_pct", {"team_id": team, **_w(t - 2 * W, t - W)},
                        round(100 * pshare))] if ptot else [])
            if self.passes_available:
                facts.append(_fact("possession", "possession_pct", {"team_id": team, **win},
                                   self._possession(now, team)))
            conf = min(1.0, 0.5 + (share - self.MOMENTUM_SHARE) + a["score"] / 40)
            out.append(self._new(
                detector="momentum_shift", t=t, from_t=t - W, to_t=t, team_id=team, players=[],
                metrics={"share_pct": round(100 * share), "prev_share_pct": round(100 * pshare),
                         "box_entries": len(a["box"]), "shots": len(a["shots"]), "opp_shots": len(b["shots"])},
                evidence=[x["event_id"] for x in a["box"] + a["shots"]],
                confidence=round(conf, 2), facts=facts))
        return out

    def _possession(self, evs: list[dict], team: str) -> Optional[int]:
        p = [x for x in evs if x["type"] == ev.PASS]
        if not p:
            return None
        return round(100 * sum(1 for x in p if x["team_id"] == team) / len(p))

    def standout(self, e: dict) -> Optional[Insight]:
        team, player, t = e["team_id"], e["player"], e["t"]
        if not self._cool(("standout", player), t, self.STANDOUT_COOLDOWN):
            return None
        W = self.STANDOUT_WINDOW
        win_evs = [x for x in self._window(t - W, t)
                   if x["team_id"] == team and x["type"] in self.KEY_ACTIONS]
        by_player: dict[str, list] = {}
        for x in win_evs:
            if x.get("player"):
                by_player.setdefault(x["player"], []).append(x)
        mine = by_player.get(player, [])
        others = sorted((len(v) for k, v in by_player.items() if k != player), reverse=True)
        nxt = others[0] if others else 0
        if len(mine) < self.STANDOUT_MIN_ACTIONS or len(mine) < self.STANDOUT_TEAM_SHARE * len(win_evs):
            return None
        if len(mine) < 2 * max(nxt, 1):
            return None
        self.last_fired[("standout", player)] = t
        win = _w(t - W, t)
        n = lambda typ: sum(1 for x in mine if x["type"] == typ)
        facts = [
            _fact("key_actions", "count", {"team_id": team, "player": player,
                                           "type": list(self.KEY_ACTIONS), **win}, len(mine)),
            _fact("team_key_actions", "count", {"team_id": team, "type": list(self.KEY_ACTIONS), **win},
                  len(win_evs)),
            _fact("shots", "count", {"team_id": team, "player": player, "type": "shot", **win}, n(ev.SHOT)),
            _fact("box_entries", "count", {"team_id": team, "player": player, "type": "box_entry", **win},
                  n(ev.BOX_ENTRY)),
            _fact("regains", "count", {"team_id": team, "player": player, "type": "regain", **win}, n(ev.REGAIN)),
            _fact("key_passes", "count", {"team_id": team, "player": player, "type": "key_pass", **win},
                  n(ev.KEY_PASS)),
        ]
        conf = min(1.0, 0.5 + 0.08 * (len(mine) - self.STANDOUT_MIN_ACTIONS) + 0.3 * len(mine) / max(len(win_evs), 1))
        return self._new(detector="standout", t=t, from_t=t - W, to_t=t, team_id=team, players=[player],
                         metrics={"actions": len(mine), "team_actions": len(win_evs), "next_best": nxt,
                                  "shots": n(ev.SHOT), "box_entries": n(ev.BOX_ENTRY), "regains": n(ev.REGAIN),
                                  "key_passes": n(ev.KEY_PASS)},
                         evidence=[x["event_id"] for x in mine], confidence=round(conf, 2), facts=facts)

    def goal_threat(self, e: dict) -> Optional[Insight]:
        team, t = e["team_id"], e["t"]
        if e["outcome"] == "goal" or not self._cool(("threat", team), t, self.THREAT_COOLDOWN):
            return None
        W = self.THREAT_WINDOW
        shots = [x for x in self._window(t - W, t) if x["team_id"] == team and x["type"] == ev.SHOT]
        if any(x["outcome"] == "goal" for x in shots):
            return None
        on = [x for x in shots if x["outcome"] == "on_target"]
        if len(shots) < self.THREAT_MIN_SHOTS or len(on) < self.THREAT_MIN_ON_TARGET:
            return None
        self.last_fired[("threat", team)] = t
        win = _w(t - W, t)
        facts = [
            _fact("shots", "count", {"team_id": team, "type": "shot", **win}, len(shots)),
            _fact("on_target", "count", {"team_id": team, "type": "shot", "outcome": "on_target", **win}, len(on)),
            _fact("goals", "count", {"team_id": team, "type": "shot", "outcome": "goal", **win}, 0),
        ]
        xg = [x["xg"] for x in shots if x.get("xg") is not None]
        if len(xg) == len(shots):
            facts.append(_fact("xg", "xg_sum", {"team_id": team, **win}, round(sum(xg), 2)))
        conf = min(1.0, 0.5 + 0.08 * len(shots) + 0.05 * len(on))
        return self._new(detector="goal_threat", t=t, from_t=t - W, to_t=t, team_id=team,
                         players=sorted({x["player"] for x in shots if x.get("player")}),
                         metrics={"shots": len(shots), "on_target": len(on),
                                  "xg": round(sum(xg), 2) if xg else None},
                         evidence=[x["event_id"] for x in shots], confidence=round(conf, 2), facts=facts)

    # ------------------------------------------------------------------ red cards and turning points
    POWER_SHOT_KMH = 110.0

    def power_shot(self, e: dict) -> Optional[Insight]:
        """Speed tag: a shot struck at or above the threshold (feeds with ball speed only)."""
        sp = e.get("speed")
        if sp is None or sp < self.POWER_SHOT_KMH or not e.get("player"):
            return None
        if not self._cool(("power_shot", e["team_id"]), e["t"], 300):
            return None
        self.last_fired[("power_shot", e["team_id"])] = e["t"]
        facts = [
            _fact("shooter", "attribution", {"event_id": e["event_id"]}, e["player"]),
            _fact("shot_speed", "shot_speed", {"event_id": e["event_id"]}, round(sp, 1)),
            _fact("shot_outcome", "shot_outcome", {"event_id": e["event_id"]}, e["outcome"]),
        ]
        return self._new(detector="power_shot", t=e["t"], from_t=e["t"], to_t=e["t"], team_id=e["team_id"],
                         players=[e["player"]], metrics={"speed": round(sp, 1), "outcome": e["outcome"],
                                                         "threshold": self.POWER_SHOT_KMH},
                         evidence=[e["event_id"]], confidence=1.0, facts=facts)

    def red_card(self, e: dict) -> Insight:
        team = e["team_id"]
        self.reds.append(e)
        score = self.score_at(e["t"])
        facts = [
            _fact("sent_off", "attribution", {"event_id": e["event_id"]}, e.get("player") or "unknown"),
            _fact("team_goals", "score", {"team_id": team, "at_t": e["t"]}, score[team]),
            _fact("opp_goals", "score", {"team_id": self.opp(team), "at_t": e["t"]}, score[self.opp(team)]),
        ]
        return self._new(detector="red_card", t=e["t"], from_t=e["t"], to_t=e["t"], team_id=team,
                         players=[p for p in [e.get("player")] if p],
                         metrics={"score_team": score[team], "score_opp": score[self.opp(team)]},
                         evidence=[e["event_id"]], confidence=1.0, facts=facts)

    def turning_points(self, e: dict) -> list[Insight]:
        """Did a red card change the match? Compare shots and goals before vs after it.

        Fires when the side with the extra player first scores after the card, and again at full time.
        """
        out = []
        start = self.events[0]["t"] if self.events else 0.0
        for red in self.reds:
            down = red["team_id"]            # side that lost a player
            up = self.opp(down)
            if e["t"] <= red["t"]:
                continue
            at_ft = e["type"] == ev.PERIOD_END
            if not at_ft:
                scorer = e["team_id"]
                if scorer != up or ("first_goal", red["event_id"]) in self.turning_fired:
                    continue
                key = ("first_goal", red["event_id"])
            else:
                key = ("full_time", red["event_id"])
                if key in self.turning_fired:
                    continue
            self.turning_fired.add(key)
            before = _w(start, red["t"] - 0.01)
            after = _w(red["t"], e["t"])
            evs_b = self._window(start, red["t"] - 0.01)
            evs_a = self._window(red["t"], e["t"])
            shots = lambda evs, tm: [x for x in evs if x["team_id"] == tm and x["type"] == ev.SHOT]
            goals = lambda evs, tm: [x for x in shots(evs, tm) if x["outcome"] == "goal"]
            facts = [
                _fact("sent_off", "attribution", {"event_id": red["event_id"]}, red.get("player") or "unknown"),
                _fact("down_shots_before", "count", {"team_id": down, "type": "shot", **before}, len(shots(evs_b, down))),
                _fact("up_shots_before", "count", {"team_id": up, "type": "shot", **before}, len(shots(evs_b, up))),
                _fact("up_shots_after", "count", {"team_id": up, "type": "shot", **after}, len(shots(evs_a, up))),
                _fact("up_goals_after", "count", {"team_id": up, "type": "shot", "outcome": "goal", **after},
                      len(goals(evs_a, up))),
                _fact("down_shots_after", "count", {"team_id": down, "type": "shot", **after}, len(shots(evs_a, down))),
                _fact("down_goals_after", "count", {"team_id": down, "type": "shot", "outcome": "goal", **after},
                      len(goals(evs_a, down))),
            ]
            evidence = [red["event_id"]] + [x["event_id"] for x in goals(evs_a, up)] + \
                       [x["event_id"] for x in shots(evs_a, up)][:8]
            out.append(self._new(
                detector="turning_point", t=e["t"], from_t=start, to_t=e["t"], team_id=up,
                players=[p for p in [red.get("player")] if p],
                metrics={"at_full_time": at_ft, "red_minute": red.get("minute"), "down_team": down,
                         "down_shots_before": len(shots(evs_b, down)), "up_shots_before": len(shots(evs_b, up)),
                         "up_shots_after": len(shots(evs_a, up)), "up_goals_after": len(goals(evs_a, up)),
                         "down_shots_after": len(shots(evs_a, down))},
                evidence=list(dict.fromkeys(evidence)), confidence=1.0 if at_ft else 0.9, facts=facts))
        return out
