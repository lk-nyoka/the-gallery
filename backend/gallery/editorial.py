"""Editorial Director: deterministic scoring and ranking, plus "Why not?".

score = Σ weight × factor. Weights are config, shown in the UI. No model decides what matters.
"""

from __future__ import annotations

from typing import Any, Optional

WEIGHTS = {
    "importance": 0.30,
    "novelty": 0.20,
    "confidence": 0.20,
    "visual_clarity": 0.15,
    "market_relevance": 0.15,
}

BASE_IMPORTANCE = {
    "turning_point": 1.0,
    "red_card": 0.9,
    "goal": 1.0,
    "momentum_shift": 0.75,
    "counterattack": 0.70,
    "goal_threat": 0.65,
    "standout": 0.60,
    "power_shot": 0.55,
}

VISUAL = {  # does a ready graphic template exist for this story?
    "turning_point": 1.0, "red_card": 1.0,
    "goal": 1.0, "counterattack": 1.0, "momentum_shift": 1.0, "standout": 0.8, "goal_threat": 0.8,
    "power_shot": 0.9,
}

FACTOR_LABEL = {
    "importance": "importance",
    "novelty": "novelty",
    "confidence": "detector confidence",
    "visual_clarity": "graphic readiness",
    "market_relevance": "relevance to this market",
}

DETECTOR_LABEL = {
    "turning_point": "Turning point",
    "red_card": "Red card",
    "goal": "Goal",
    "momentum_shift": "Momentum shift",
    "counterattack": "Counterattack",
    "goal_threat": "Pressure without a goal",
    "standout": "Standout performer",
    "power_shot": "Power shot",
}


def _importance(ins: dict, score: dict[str, int], teams: tuple[str, str]) -> float:
    imp = BASE_IMPORTANCE.get(ins["detector"], 0.5)
    if ins["detector"] == "counterattack" and ins["metrics"].get("outcome") in ("goal", "on_target"):
        imp += 0.1
    margin = abs(score.get(teams[0], 0) - score.get(teams[1], 0))
    if ins["t"] >= 75 * 60 and margin <= 1:
        imp += 0.1
    return min(1.0, imp)


def _novelty(ins: dict, earlier: list[dict]) -> tuple[float, Optional[dict]]:
    similar = [
        e for e in earlier
        if e["detector"] == ins["detector"] and e["team_id"] == ins["team_id"]
        and 0 < ins["t"] - e["t"] <= 20 * 60
        and (ins["detector"] != "standout" or set(e["players"]) & set(ins["players"]))
    ]
    return max(0.2, 1.0 - 0.35 * len(similar)), (similar[-1] if similar else None)


def _relevance(ins: dict, market: dict) -> float:
    """Who the market follows (players, teams) plus the kind of story its angle favours."""
    players = set(market.get("focus_players", []))
    teams = set(market.get("focus_teams", []))
    boost = market.get("detector_boost", {}).get(ins["detector"], 0.0)
    if players & set(ins["players"]):
        return 1.0
    if ins["team_id"] in teams:
        return min(1.0, 0.8 + boost)
    return min(1.0, market.get("base_relevance", 0.6) + boost)


def score_insight(ins: dict, earlier: list[dict], score: dict[str, int], teams: tuple[str, str],
                  market: dict) -> dict[str, Any]:
    nov, similar = _novelty(ins, earlier)
    factors = {
        "importance": _importance(ins, score, teams),
        "novelty": nov,
        "confidence": float(ins["confidence"]),
        "visual_clarity": VISUAL.get(ins["detector"], 0.6),
        "market_relevance": _relevance(ins, market),
    }
    breakdown = {
        k: {"value": round(v, 2), "weight": WEIGHTS[k], "contribution": round(v * WEIGHTS[k], 3)}
        for k, v in factors.items()
    }
    total = round(sum(b["contribution"] for b in breakdown.values()), 3)
    return {"insight_id": ins["insight_id"], "score": total, "factors": breakdown,
            "similar_earlier": similar["insight_id"] if similar else None,
            "similar_minute": similar.get("minute", int(similar["t"] // 60) + 1) if similar else None}


def rank(insights: list[dict], now_t: float, score_fn_ctx: dict, market: dict,
         window_s: Optional[float] = 15 * 60, since_t: Optional[float] = None) -> list[dict[str, Any]]:
    """Rank insights in the current phase (default: last 15 match minutes)."""
    teams, score = score_fn_ctx["teams"], score_fn_ctx["score"]
    ordered = sorted(insights, key=lambda i: i["t"])
    if since_t is not None:
        pool = [i for i in ordered if i["t"] >= since_t and i["t"] <= now_t]
    elif window_s is not None:
        pool = [i for i in ordered if now_t - window_s <= i["t"] <= now_t]
    else:
        pool = [i for i in ordered if i["t"] <= now_t]
    scored = []
    for ins in pool:
        earlier = [e for e in ordered if e["t"] < ins["t"]]
        s = score_insight(ins, earlier, score, teams, market)
        s["detector"] = ins["detector"]
        s["minute"] = ins.get("minute", int(ins["t"] // 60) + 1)
        s["team_id"] = ins["team_id"]
        scored.append(s)
    scored.sort(key=lambda s: (-s["score"], -s["minute"]))
    for i, s in enumerate(scored, 1):
        s["rank"] = i
    return scored


def metric_summary(ins: dict, names: dict[str, str]) -> str:
    m, team = ins["metrics"], names.get(ins["team_id"], ins["team_id"])
    d = ins["detector"]
    if d == "goal":
        og = " (own goal)" if m.get("own_goal") else ""
        return f"{(ins['players'] or ['?'])[0]}{og} goal for {team} ({m['score_team']}-{m['score_opp']})"
    if d == "counterattack":
        passes = f", {m['passes']} passes" if m.get("passes") is not None else ""
        return f"{team}: regain to shot in {m['seconds']}s{passes}, shot {m['outcome'].replace('_', ' ')}"
    if d == "momentum_shift":
        return f"{team}: {m['share_pct']}% of attacking threat, up from {m['prev_share_pct']}%"
    if d == "standout":
        return f"{ins['players'][0]} ({team}): {m['actions']} of {m['team_actions']} key actions"
    if d == "red_card":
        return f"{(ins['players'] or ['?'])[0]} sent off ({team})"
    if d == "turning_point":
        return (f"after the red card: {team} {m['up_shots_after']} shots, {m['up_goals_after']} goals "
                f"(before: {m['up_shots_before']} v {m['down_shots_before']})")
    if d == "goal_threat":
        return f"{team}: {m['shots']} shots, {m['on_target']} on target, no goal"
    if d == "power_shot":
        return f"{(ins['players'] or ['?'])[0]} ({team}): shot at {m['speed']} km/h, {m['outcome'].replace('_', ' ')}"
    return d


def why_not(chosen: dict, others: list[dict], insights_by_id: dict[str, dict], names: dict[str, str],
            limit: int = 3) -> list[dict[str, Any]]:
    """Explain, from stored numbers only, why each runner-up ranked below the chosen story."""
    out = []
    for o in others[:limit]:
        reasons = []
        for k, label in FACTOR_LABEL.items():
            a, b = chosen["factors"][k], o["factors"][k]
            gap = a["contribution"] - b["contribution"]
            if gap >= 0.02:
                if k == "novelty" and o.get("similar_minute"):
                    reasons.append(f"Less new: a similar story was already detected at {o['similar_minute']}'"
                                   f" (novelty {b['value']:.2f} vs {a['value']:.2f})")
                else:
                    reasons.append(f"Lower {label}: {b['value']:.2f} vs {a['value']:.2f}")
        if not reasons:
            reasons.append(f"Close call: total {o['score']:.3f} vs {chosen['score']:.3f}")
        ins = insights_by_id.get(o["insight_id"], {})
        out.append({
            "insight_id": o["insight_id"],
            "title": DETECTOR_LABEL.get(o["detector"], o["detector"]),
            "minute": o["minute"],
            "summary": metric_summary(ins, names) if ins else "",
            "score": o["score"],
            "chosen_score": chosen["score"],
            "reasons": reasons,
        })
    return out
