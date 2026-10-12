"""Minimal event interface every data source is adapted to.

Coordinates: x and y run 0-100. x is always in the attacking direction of the team
performing the action (0 = own goal line, 100 = opponent goal line). y is 0-100 across.
Time: `t` is seconds since kick-off of the match clock (second half starts at 2700).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# Event types the engine understands.
PASS = "pass"
CARRY = "carry"
REGAIN = "regain"          # tackle / interception / recovery: the team gains the ball
BOX_ENTRY = "box_entry"    # ball carried or passed into the opponent box
SHOT = "shot"              # outcome: goal | on_target | off_target | blocked
CORNER = "corner"
FOUL = "foul"
CARD = "card"
SUB = "sub"
KICK_OFF = "kick_off"
PERIOD_END = "period_end"
OWN_GOAL = "own_goal"        # team_id = team that BENEFITS; player = player who put it in his own net
KEY_PASS = "key_pass"        # the pass that set up a shot (assist on a shot)

EVENT_TYPES = {PASS, CARRY, REGAIN, BOX_ENTRY, SHOT, CORNER, FOUL, CARD, SUB, KICK_OFF, PERIOD_END, OWN_GOAL, KEY_PASS}

SHOT_OUTCOMES = {"goal", "on_target", "off_target", "blocked"}

OWN_HALF_X = 50.0
FINAL_THIRD_X = 66.7
BOX_X = 83.0
BOX_Y = (21.0, 79.0)


@dataclass
class Event:
    event_id: str
    match_id: str
    t: float
    period: int
    team_id: str
    type: str
    outcome: Optional[str] = None
    player: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    end_x: Optional[float] = None
    end_y: Optional[float] = None
    xg: Optional[float] = None
    source: str = "replay"
    clock: Optional[float] = None   # official match clock in seconds (2nd half starts at 45:00); t is elapsed
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def minute(self) -> int:
        return int((self.clock if self.clock is not None else self.t) // 60) + 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["minute"] = self.minute
        d.pop("raw", None)
        return d


def validate(e: Event) -> None:
    if e.type not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {e.type}")
    if e.type == SHOT and e.outcome not in SHOT_OUTCOMES:
        raise ValueError(f"shot needs outcome in {sorted(SHOT_OUTCOMES)}")
    if e.t < 0:
        raise ValueError("t must be >= 0")


def in_box(x: Optional[float], y: Optional[float]) -> bool:
    if x is None or y is None:
        return False
    return x >= BOX_X and BOX_Y[0] <= y <= BOX_Y[1]


def fmt_clock(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"
