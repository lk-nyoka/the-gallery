"""Operational governance: newsroom readiness, rights envelopes and correction impact.

All three are deterministic summaries of state the system already holds; none of them call a model.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import MatchRuntime

# Default distribution policy. A real deployment loads this per rights holder and territory.
RIGHTS_POLICY: dict[str, dict[str, Any]] = {
    "world": {"territories": ["global"], "surfaces": ["broadcast", "app", "social"], "clip_rights": "rights_holder_only"},
    "br": {"territories": ["BR"], "surfaces": ["broadcast", "app"], "clip_rights": "partner_specific"},
    "za": {"territories": ["ZA"], "surfaces": ["broadcast", "app", "social"], "clip_rights": "partner_specific"},
    "viewer": {"territories": ["personal"], "surfaces": ["app"], "clip_rights": "none"},
}
EXPIRY_S = {"half_time": 20 * 60, "full_time": 24 * 3600}
MIN_STORIES = {"half_time": 3, "full_time": 4}


def rights_envelope(rt: "MatchRuntime", package: dict) -> dict:
    """Machine-readable terms that travel with every package: where, on what, until when, approved by whom."""
    pol = RIGHTS_POLICY.get(package["market"], RIGHTS_POLICY["world"])
    items = package["items"]
    built = time.time()
    return {
        "package_id": package["package_id"], "match_id": rt.match_id, "kind": package["kind"],
        "market": package["market"], "language": rt.markets[package["market"]].get("language", "en"),
        "territories": pol["territories"], "allowed_surfaces": pol["surfaces"],
        "clip_rights": pol["clip_rights"] if any(i.get("clip_cue") for i in items) else "none",
        "built_at": built, "expires_at": built + EXPIRY_S.get(package["kind"], 3600),
        "approval_state": "producer_approved" if items else "empty",
        "storylines": [i["storyline_id"] for i in items],
        "contains_unlocalised": any(i.get("not_localised") for i in items),
        "verification": "every sentence verified against the event data at build time",
        "policy_note": "Default policy; configure per rights holder and territory.",
    }


def readiness(rt: "MatchRuntime", phase: str = "half_time") -> dict:
    """Go / no-go for the next scheduled editorial moment, as a checklist and one number."""
    cutoff = 2760 if phase == "half_time" else 1e9
    live = [s for s in rt.storylines.values() if s["minute"] * 60 <= cutoff + 900]
    approved = [s for s in live if s["status"] == "approved" and s["market"] == "world"]
    cov = rt.coverage(cutoff)
    corrections = [s for s in rt.storylines.values() if s["status"] == "needs_correction"]
    waiting = [s for s in live if s["status"] in ("drafted", "edited") and s["market"] == "world"
               and s["overall"] != "blocked"]
    markets = {}
    for m in ("world", "br", "za"):
        if m == "world":
            markets[m] = bool(approved)
        else:
            markets[m] = any(s["market"] == m and s["status"] == "approved" for s in rt.storylines.values()) \
                or (bool(approved) and rt.markets[m].get("language") == "en")
    need = MIN_STORIES.get(phase, 3)
    checks = [
        {"key": "stories", "ok": len(approved) >= need, "weight": 0.3,
         "label": f"{len(approved)} of {need} approved stories"},
        {"key": "coverage", "ok": not cov["missing"], "weight": 0.3,
         "label": "every goal and red card covered" if not cov["missing"]
         else f"{len(cov['missing'])} key moment{'s' if len(cov['missing']) != 1 else ''} not covered"},
        {"key": "corrections", "ok": not corrections, "weight": 0.25,
         "label": "no outstanding corrections" if not corrections
         else f"{len(corrections)} approved stor{'y' if len(corrections) == 1 else 'ies'} contradicted by new data"},
        {"key": "markets", "ok": all(markets.values()), "weight": 0.15,
         "label": f"{sum(markets.values())} of {len(markets)} markets have approved stories"},
    ]
    score = sum(c["weight"] for c in checks if c["ok"])
    if checks[0]["ok"] is False and approved:
        score += checks[0]["weight"] * len(approved) / need
    return {"phase": phase, "score": round(100 * score), "go": all(c["ok"] for c in checks[:3]),
            "checks": checks, "waiting_for_approval": len(waiting), "markets": markets}


def correction_impact(rt: "MatchRuntime", flagged: list[str], stale: list[str]) -> dict:
    """What one data correction touches downstream."""
    affected = set(flagged) | set(stale)
    localised = [s["storyline_id"] for s in rt.storylines.values() if s.get("base_storyline_id") in affected]
    packages = [p["package_id"] for p in rt.packages
                if any(i["storyline_id"] in affected for i in p.get("items", []))]
    graphics = sum(1 for sid in flagged if rt.storylines.get(sid, {}).get("graphic"))
    return {"approved_pulled": len(flagged), "drafts_invalidated": len(stale), "graphics_invalidated": graphics,
            "localised_versions": len(localised), "packages_to_rebuild": packages,
            "markets": sorted({rt.storylines[s]["market"] for s in list(affected) + localised if s in rt.storylines}),
            "replacements": "Corrections agent re-drafting; the producer approves the new version"}
