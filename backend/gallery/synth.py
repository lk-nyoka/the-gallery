"""Synthetic, football-realistic match generator with planted patterns.

Used for development, the demo and the benchmark until organiser data is wired in
(see adapters.py). Patterns are planted deliberately and recorded as ground truth, so
detection can be measured instead of asserted.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Optional

from . import events as ev
from .events import Event

HOME_ROSTER = ["Mensah", "Okafor", "Hale", "Duarte", "Kovac", "Reyes", "Lindqvist",
               "Adeyemi", "Moreau", "Santos", "Byrne"]
AWAY_ROSTER = ["Ndlovu", "Carvalho", "Price", "Ivanov", "Bakari", "Torres", "Holm",
               "Dube", "Ferreira", "Walsh", "Kimura"]

HALF = 2700.0


def ball_speed(e: Event) -> Optional[float]:
    """Synthetic ball speed in km/h for passes and shots. Seeded by the event id (not the match RNG), so adding
    speeds never changes the rest of a generated match."""
    if e.type not in (ev.PASS, ev.SHOT):
        return None
    r = random.Random(zlib.crc32(e.event_id.encode()))
    if e.type == ev.PASS:
        length = ev.pass_length_m(e.x, e.y, e.end_x, e.end_y) or 15.0
        return round(max(20.0, min(110.0, 28 + length * 1.15 + r.uniform(-6, 6))), 1)
    x, y = (e.x if e.x is not None else 85.0), (e.y if e.y is not None else 50.0)
    dist_m = (((100 - x) * 1.05) ** 2 + ((50 - y) * 0.68) ** 2) ** 0.5
    return round(max(45.0, min(124.0, 118 - dist_m * 1.6 + r.uniform(-14, 9))), 1)


@dataclass
class Plant:
    pattern: str                 # counterattack | momentum_shift | standout | goal | red_card | own_goal | missed_penalty
    team: str                    # "H" or "A"
    t_from: float
    t_to: float
    player: Optional[str] = None
    event_id: Optional[str] = None
    label: Optional[str] = None


@dataclass
class SynthMatch:
    match_id: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    events: list[Event] = field(default_factory=list)
    truth: list[dict] = field(default_factory=list)


DEFAULT_PLANTS = [
    Plant("momentum_shift", "H", 55 * 60, 64 * 60),
    Plant("counterattack", "A", 23 * 60, 23 * 60 + 20),
    Plant("counterattack", "H", 61 * 60, 61 * 60 + 20),
    Plant("counterattack", "A", 78 * 60, 78 * 60 + 20),
    Plant("standout", "A", 30 * 60, 44 * 60, player="Ndlovu"),
]


SCRIPTED = ("counterattack", "goal", "red_card", "own_goal", "missed_penalty")


class _Sim:
    def __init__(self, m: SynthMatch, seed: int, plants: list[Plant], natural_goals: bool = True):
        self.m = m
        self.natural_goals = natural_goals
        self.sent_off: dict[str, set[str]] = {"H": set(), "A": set()}
        self.rng = random.Random(seed)
        # copy so recorded truth reflects when a pattern actually happened
        self.plants = [Plant(p.pattern, p.team, p.t_from, p.t_to, p.player, None, p.label) for p in plants]
        self.n = 0
        self.t = 0.0
        self.period = 1
        self.team = "H"
        self.x = 50.0
        self.y = 50.0
        self.done_counters: set[int] = set()

    # -- helpers ---------------------------------------------------------
    def tid(self, side: str) -> str:
        return self.m.home_id if side == "H" else self.m.away_id

    def other(self, side: str) -> str:
        return "A" if side == "H" else "H"

    def roster(self, side: str) -> list[str]:
        return HOME_ROSTER if side == "H" else AWAY_ROSTER

    def emit(self, side: str, typ: str, player: Optional[str] = None, outcome: Optional[str] = None,
             x: Optional[float] = None, y: Optional[float] = None, end_x: Optional[float] = None,
             end_y: Optional[float] = None, xg: Optional[float] = None) -> Event:
        self.n += 1
        e = Event(
            event_id=f"{self.m.match_id}-e{self.n:05d}", match_id=self.m.match_id,
            t=round(self.t, 1), period=self.period, team_id=self.tid(side), type=typ,
            outcome=outcome, player=player,
            x=None if x is None else round(x, 1), y=None if y is None else round(y, 1),
            end_x=None if end_x is None else round(end_x, 1), end_y=None if end_y is None else round(end_y, 1),
            xg=xg, source="synthetic",
        )
        e.speed = ball_speed(e)
        self.m.events.append(e)
        return e

    def active(self, pattern: str, side: Optional[str] = None) -> Optional[Plant]:
        for p in self.plants:
            if p.pattern == pattern and p.t_from <= self.t <= p.t_to and (side is None or p.team == side):
                return p
        return None

    def pick_player(self, side: str, attacking: bool = False) -> str:
        roster = self.roster(side)
        st = self.active("standout", side)
        if st and st.player and self.rng.random() < (0.75 if attacking else 0.5):
            return st.player
        pool = [p for p in (roster[5:] if attacking else roster) if p not in self.sent_off[side]] or roster
        return self.rng.choice(pool)

    def control(self, side: str) -> float:
        """Base quality: higher = fewer turnovers, more progression."""
        c = 0.55 if side == "H" else 0.5
        mom = self.active("momentum_shift")
        if mom:
            c += 0.35 if mom.team == side else -0.3
        return c

    # -- phases ----------------------------------------------------------
    def kick_off(self, side: str) -> None:
        self.team, self.x, self.y = side, 50.0, 50.0
        self.emit(side, ev.KICK_OFF, x=50, y=50)

    def check_period(self) -> bool:
        if self.period == 1 and self.t >= HALF:
            self.emit("H", ev.PERIOD_END)
            self.period = 2
            self.t = max(self.t + 0.1, HALF + 0.1)
            self.kick_off("A")
            return True
        return False

    def shot(self, side: str, player: str) -> str:
        dist = max(4.0, 100 - self.x)
        xg = round(max(0.02, min(0.6, 0.55 - dist * 0.025 + self.rng.uniform(-0.05, 0.05))), 2)
        r = self.rng.random()
        if r < xg * 0.3 and self.natural_goals:
            outcome = "goal"
        elif r < xg * 0.3 + 0.3:
            outcome = "on_target"
        elif r < 0.85:
            outcome = "off_target"
        else:
            outcome = "blocked"
        self.emit(side, ev.SHOT, player=player, outcome=outcome, x=self.x, y=self.y, xg=xg)
        return outcome

    def planted_counter(self, p: Plant) -> None:
        """Scripted fast break: regain in own half -> 2 passes -> box entry -> shot on target."""
        side, opp = p.team, self.other(p.team)
        self.t = max(self.t, p.t_from) + 1.0
        p.t_from = self.t
        # opponent had the ball high up the pitch
        self.emit(opp, ev.PASS, player=self.pick_player(opp, True), outcome="complete", x=60, y=40, end_x=70, end_y=45)
        self.t += 2.5
        regainer = self.rng.choice(self.roster(side)[1:5])
        self.emit(side, ev.REGAIN, player=regainer, x=32, y=48)
        self.t += 3.0
        p1 = self.pick_player(side, True)
        self.emit(side, ev.PASS, player=regainer, outcome="complete", x=32, y=48, end_x=58, end_y=30)
        self.t += 3.5
        p2 = self.pick_player(side, True)
        self.emit(side, ev.PASS, player=p1, outcome="complete", x=58, y=30, end_x=86, end_y=40)
        self.t += 2.0
        self.emit(side, ev.BOX_ENTRY, player=p2, x=86, y=40)
        self.t += 1.5
        self.x, self.y = 88, 44
        outcome = "on_target" if self.rng.random() < 0.75 else "goal"
        self.emit(side, ev.SHOT, player=p2, outcome=outcome, x=88, y=44, xg=0.32)
        p.t_to = self.t
        self.t += 4
        if outcome == "goal":
            self.t += 40
            self.kick_off(opp)
        else:
            self.team, self.x, self.y = opp, 8, 50

    def _start(self, p: Plant) -> None:
        self.t = max(self.t, p.t_from) + 1.0
        p.t_from = self.t

    def planted_goal(self, p: Plant) -> None:
        """Scripted goal: final-third pass -> box entry -> goal by the named player."""
        side, opp = p.team, self.other(p.team)
        self._start(p)
        scorer = p.player or self.pick_player(side, True)
        passer = self.pick_player(side, True)
        self.emit(side, ev.PASS, player=passer, outcome="complete", x=70, y=45, end_x=86, end_y=48)
        self.t += 1.5
        self.emit(side, ev.BOX_ENTRY, player=scorer, x=86, y=48)
        self.t += 1.5
        e = self.emit(side, ev.SHOT, player=scorer, outcome="goal", x=89, y=50, xg=0.38)
        p.t_to = self.t
        p.event_id = e.event_id
        self.t += 45
        self.kick_off(opp)

    def planted_red_card(self, p: Plant) -> None:
        """Scripted red card: a foul, then the player is sent off; the other side takes control afterwards."""
        side, opp = p.team, self.other(p.team)
        self._start(p)
        player = p.player or self.pick_player(side)
        self.emit(side, ev.FOUL, player=player, x=45, y=50)
        self.t += 20
        e = self.emit(side, ev.CARD, player=player, outcome="red")
        self.sent_off[side].add(player)
        p.t_to = self.t
        p.event_id = e.event_id
        self.plants.append(Plant("momentum_shift", opp, self.t + 60, 2 * HALF))  # ten men lose control
        self.t += 30
        self.team, self.x, self.y = opp, 40, 50

    def planted_own_goal(self, p: Plant) -> None:
        """Scripted own goal: `team` benefits; `player` is the defender who turned it in."""
        side, opp = p.team, self.other(p.team)
        self._start(p)
        self.emit(side, ev.PASS, player=self.pick_player(side, True), outcome="complete", x=72, y=20, end_x=92, end_y=45)
        self.t += 1.0
        defender = p.player or self.rng.choice(self.roster(opp)[1:5])
        e = self.emit(side, ev.OWN_GOAL, player=defender, x=95, y=50)
        p.t_to = self.t
        p.event_id = e.event_id
        self.t += 45
        self.kick_off(opp)

    def planted_missed_penalty(self, p: Plant) -> None:
        """Scripted penalty: foul in the box, then the kick goes wide."""
        side, opp = p.team, self.other(p.team)
        self._start(p)
        taker = p.player or self.pick_player(side, True)
        self.emit(side, ev.BOX_ENTRY, player=taker, x=88, y=40)
        self.t += 1.0
        self.emit(opp, ev.FOUL, player=self.rng.choice(self.roster(opp)[1:5]), x=12, y=60)
        self.t += 60
        e = self.emit(side, ev.SHOT, player=taker, outcome="off_target", x=88.5, y=50, xg=0.79)
        p.t_to = self.t
        p.event_id = e.event_id
        self.t += 10
        self.team, self.x, self.y = opp, 8, 50

    # -- main loop -------------------------------------------------------
    def run(self) -> None:
        self.kick_off("H")
        while self.t < 2 * HALF:
            if self.check_period():
                continue
            # scripted moments fire at their scheduled time
            for i, p in enumerate(self.plants):
                if p.pattern in SCRIPTED and i not in self.done_counters and self.t >= p.t_from:
                    self.done_counters.add(i)
                    getattr(self, f"planted_{p.pattern}" if p.pattern != "counterattack" else "planted_counter")(p)
                    break
            else:
                self.step()
        self.t = max(self.t, 2 * HALF)
        self.emit("H", ev.PERIOD_END)

    def step(self) -> None:
        side, opp = self.team, self.other(self.team)
        c = self.control(side)
        player = self.pick_player(side, attacking=self.x > 55)
        self.t += self.rng.uniform(2.0, 5.5)

        # shot opportunity
        if self.x >= 78 and self.rng.random() < 0.09 + 0.2 * (c - 0.5):
            outcome = self.shot(side, player)
            self.t += self.rng.uniform(5, 15)
            if outcome == "goal":
                self.t += 40
                self.kick_off(opp)
            elif outcome == "blocked" and self.rng.random() < 0.5:
                self.emit(side, ev.CORNER, player=player, x=99, y=self.rng.choice([1, 99]))
                self.t += 25
                self.x, self.y = 92, 50
            else:
                self.team, self.x, self.y = opp, 8, 50
            return

        # turnover
        turnover_p = 0.16 - 0.12 * (c - 0.5) + (0.04 if self.x > 70 else 0)
        if self.rng.random() < turnover_p:
            if self.rng.random() < 0.12:
                self.emit(opp, ev.FOUL, player=self.pick_player(opp), x=100 - self.x, y=100 - self.y)
                self.t += 20
                return  # same team keeps the ball after a foul
            nx, ny = 100 - self.x, 100 - self.y
            self.emit(opp, ev.REGAIN, player=self.pick_player(opp), x=nx, y=ny)
            self.team, self.x, self.y = opp, nx, ny
            return

        # pass forward
        gain = self.rng.gauss(4 + 10 * (c - 0.5), 9)
        nx = max(3.0, min(97.0, self.x + gain))
        ny = max(2.0, min(98.0, self.y + self.rng.gauss(0, 18)))
        self.emit(side, ev.PASS, player=player, outcome="complete", x=self.x, y=self.y, end_x=nx, end_y=ny)
        was_in_box = ev.in_box(self.x, self.y)
        self.x, self.y = nx, ny
        if not was_in_box and ev.in_box(nx, ny):
            self.t += 0.5
            self.emit(side, ev.BOX_ENTRY, player=self.pick_player(side, True), x=nx, y=ny)


def generate(match_id: str = "demo", seed: int = 7, plants: Optional[list[Plant]] = None,
             home_name: str = "Northbridge FC", away_name: str = "Riverside Athletic",
             natural_goals: bool = True) -> SynthMatch:
    plants = DEFAULT_PLANTS if plants is None else plants
    m = SynthMatch(match_id=match_id, home_id="HOME", away_id="AWAY", home_name=home_name, away_name=away_name)
    sim = _Sim(m, seed, plants, natural_goals=natural_goals)
    sim.run()
    m.truth = [
        {"pattern": p.pattern, "team_id": sim.tid(p.team), "t_from": p.t_from, "t_to": p.t_to, "player": p.player,
         "event_id": p.event_id, "label": p.label}
        for p in sim.plants
    ]
    return m
