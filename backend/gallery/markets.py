"""Market profiles are data, not prompts. They change the lead story, depth and language."""

from __future__ import annotations

import copy
from typing import Any

DEFAULT_MARKETS: dict[str, dict[str, Any]] = {
    "world": {
        "id": "world", "label": "World feed", "language": "en", "language_name": "English",
        "focus_players": [], "focus_teams": [], "base_relevance": 0.7,
        "depth": "analyst", "graphic_emphasis": "momentum_chart",
        "angle": "tactical turning points: what changed in the match and why",
        "detector_boost": {"turning_point": 0.3, "momentum_shift": 0.2, "red_card": 0.2},
        "notes": "World feed: neutral, analytical, both teams treated equally.",
    },
    "br": {
        "id": "br", "label": "Brazil", "language": "pt-BR", "language_name": "Brazilian Portuguese",
        "focus_players": ["Santos", "Duarte", "Carvalho", "Ferreira"], "focus_teams": [], "base_relevance": 0.4,
        "depth": "broadcast", "graphic_emphasis": "player_card",
        "angle": "attacking momentum and chance creation",
        "detector_boost": {"goal_threat": 0.3, "counterattack": 0.3, "momentum_shift": 0.2, "power_shot": 0.3},
        "notes": "Brazilian broadcast: lead with players Brazilian viewers follow; Brazilian football "
                 "terminology; energetic but factual.",
    },
    "za": {
        "id": "za", "label": "South Africa (isiZulu)", "language": "zu", "language_name": "isiZulu",
        "focus_players": ["Ndlovu", "Dube"], "focus_teams": [], "base_relevance": 0.4,
        "depth": "community", "graphic_emphasis": "sequence_timeline",
        "angle": "player-focused and easy to follow",
        "detector_boost": {"standout": 0.4, "goal": 0.2},
        "notes": "South African community stream in isiZulu: lead with South African players; plain, "
                 "warm, easy to follow; use common isiZulu football terms.",
    },
}


def markets_for(overrides: dict[str, dict] | None = None) -> dict[str, dict]:
    m = copy.deepcopy(DEFAULT_MARKETS)
    for k, v in (overrides or {}).items():
        if k in m:
            m[k].update({kk: vv for kk, vv in v.items() if vv is not None})
    return m
