"""The Gallery as an MCP server: read-only, verified match intelligence for other agents.

Copilot, Claude or any MCP client can ask what is verified, why, and what is still missing, without being able
to approve, edit or publish anything. It reads from a running Gallery (GALLERY_URL, default localhost:8000).

    pip install -r requirements-mcp.txt
    python -m gallery.mcp_server            # stdio transport

VS Code / Claude Desktop config:
    {"servers": {"the-gallery": {"command": "python", "args": ["-m", "gallery.mcp_server"],
                                 "cwd": "<repo>/backend", "env": {"GALLERY_URL": "http://localhost:8000"}}}}
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.getenv("GALLERY_URL", "http://localhost:8000").rstrip("/")

mcp = FastMCP("the-gallery", instructions=(
    "Verified football match intelligence from The Gallery. Every story sentence carries claims re-computed from "
    "the event data by deterministic code. Only report facts from sentences whose verdict is 'verified'; "
    "'needs_human' means the numbers are true but a producer must check the wording; never repeat 'blocked' "
    "sentences as fact. Stories are publishable only when status is 'approved'. All data is synthetic."))


def _get(path: str, **params: Any) -> Any:
    r = httpx.get(f"{BASE}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=20)
    r.raise_for_status()
    return r.json()


def _match(match_id: Optional[str]) -> str:
    if match_id:
        return match_id
    ms = _get("/api/matches")
    if not ms:
        raise ValueError("No match is loaded in The Gallery.")
    return ms[-1]["match_id"]


def _story(s: dict, full: bool = False) -> dict:
    out = {"storyline_id": s["storyline_id"], "minute": s["minute"], "type": s["detector"], "team": s["team_id"],
           "market": s["market"], "status": s["status"], "verdict": s["overall"], "headline": s["headline"],
           "version": s.get("version", 1)}
    out["sentences"] = [{"text": g["text"], "verdict": g["verdict"]} | ({"reason": g["reason"]} if full else {})
                        for g in s["segments"] if not g.get("struck")]
    return out


@mcp.tool()
def list_matches() -> list[dict]:
    """Matches loaded in The Gallery, with score and clock."""
    return [{"match_id": m["match_id"], "teams": m["names"], "score": m["score"], "clock_s": m["now_t"],
             "mode": m["mode"]} for m in _get("/api/matches")]


@mcp.tool()
def get_match_state(match_id: Optional[str] = None) -> dict:
    """Score, clock, team names, data capabilities and whether the score matches the official result."""
    s = _get(f"/api/matches/{_match(match_id)}")
    return {"match_id": s["match_id"], "teams": s["names"], "score": s["score"], "clock_s": s["now_clock"],
            "period": s["period"], "feed_can_show": s["caps"], "result_check": s["result_check"],
            "funnel": s["funnel"]}


@mcp.tool()
def get_verified_storylines(match_id: Optional[str] = None, market: str = "world",
                            approved_only: bool = False) -> list[dict]:
    """Stories for a market (world, br, za, viewer). Each sentence has its verdict; only 'approved' stories are
    cleared for air. Superseded and rejected versions are excluded."""
    s = _get(f"/api/matches/{_match(match_id)}")
    keep = [x for x in s["storylines"] if x["market"] == market and x["status"] not in ("superseded", "rejected")
            and (not approved_only or x["status"] == "approved")]
    return [_story(x) for x in sorted(keep, key=lambda x: x["minute"])]


@mcp.tool()
def get_storyline_provenance(storyline_id: str) -> dict:
    """Why a story is true: every claim with its SQL, the value claimed and the value in the data, the event ids,
    the agent steps that produced it and the producer's decisions."""
    return _get(f"/api/storylines/{storyline_id}/provenance")


@mcp.tool()
def get_coverage_gaps(match_id: Optional[str] = None) -> dict:
    """Goals, own goals and red cards that no approved story covers yet."""
    c = _get(f"/api/matches/{_match(match_id)}/coverage")
    return {"summary": c["summary"], "complete": c["complete"],
            "missing": [{k: m.get(k) for k in ("label", "minute", "player", "team", "event_id")} for m in c["missing"]]}


@mcp.tool()
def get_readiness(match_id: Optional[str] = None, phase: str = "half_time") -> dict:
    """Go / no-go for half_time or full_time: approved stories, coverage, outstanding corrections, markets."""
    return _get(f"/api/matches/{_match(match_id)}/readiness", phase=phase)


@mcp.tool()
def get_corrections(match_id: Optional[str] = None) -> list[dict]:
    """Data corrections so far and what each one invalidated downstream."""
    s = _get(f"/api/matches/{_match(match_id)}")
    return [{k: c.get(k) for k in ("minute", "reason", "before", "after", "flagged", "stale_drafts", "impact")}
            for c in s.get("corrections", [])]


@mcp.tool()
def get_player_card(player: str, match_id: Optional[str] = None) -> dict:
    """A player's numbers (each computed by the verifier's SQL), recent actions and the stories they feature in."""
    return _get(f"/api/matches/{_match(match_id)}/players/{player}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
