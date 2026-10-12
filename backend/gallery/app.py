"""FastAPI server: REST + WebSocket + the producer console.

Run:  uvicorn gallery.app:app --reload   (from the backend folder)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import itertools
import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import benchmark, espn, filefeed, fixtures, governance, live, llm, personalise, statsbomb, synth
from .engine import MatchRuntime
from .events import Event
from .store import Store

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
DATA = ROOT / "backend" / "data"
DATA.mkdir(parents=True, exist_ok=True)


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    llm.provider()        # start the model provider early so Foundry Local can download/load while the console opens
    yield


app = FastAPI(title="The Gallery", version="0.2.0", lifespan=_lifespan)
store = Store(str(DATA / "gallery.db"))
runtimes: dict[str, MatchRuntime] = {}
replay_events: dict[str, list[Event]] = {}
replay_corrections: dict[str, list[dict]] = {}
_live_ids = itertools.count(1)



_match_ids = itertools.count(1)
_bench_cache: dict[str, Any] = {}

ZONES = {  # live logger: approximate coordinates in the acting team's attacking direction
    "own_half": (30.0, 50.0), "opp_half": (65.0, 50.0), "box": (88.0, 50.0), "midfield": (50.0, 50.0),
    "corner": (99.0, 1.0),
}


# --------------------------------------------------------------------------- models
class MarketOverride(BaseModel):
    focus_players: Optional[list[str]] = None
    focus_teams: Optional[list[str]] = None


class NewMatch(BaseModel):
    mode: str = "fixture"                # fixture | file | replay | live | (validation: espn | real)
    fixture: str = "A"                   # fixture mode: A turning point · B correction · C coverage stress
    file: Optional[str] = None           # file mode: name in backend/data/organiser/
    match_key: Optional[str] = None      # file mode: which match, if the file has several
    espn_id: str = ""                    # validation only: ESPN event id
    league: str = "eng.1"
    sb_match_id: int = 0                 # validation only: StatsBomb Open Data match id
    home: Optional[str] = None
    away: Optional[str] = None
    seed: int = 7
    markets: Optional[dict[str, MarketOverride]] = None
    official_home: Optional[int] = None   # live mode: the real final score, checked at full time
    official_away: Optional[int] = None
    date: Optional[str] = None


class ReplayReq(BaseModel):
    speed: float = 20.0
    start_minute: float = 0.0


class LiveEvent(BaseModel):
    team: str                            # HOME | AWAY
    type: str
    t: float
    period: int = 1
    outcome: Optional[str] = None
    player: Optional[str] = None
    zone: Optional[str] = None


class Decision(BaseModel):
    action: str
    edits: Optional[dict] = None
    acknowledged: Optional[list[int]] = None


class Inject(BaseModel):
    kind: Optional[str] = None


class MarketReq(BaseModel):
    market: str
    phase: str = "live"


class PackageReq(BaseModel):
    kind: str = "half_time"
    market: str = "world"


# --------------------------------------------------------------------------- helpers
def rt_or_404(match_id: str) -> MatchRuntime:
    rt = runtimes.get(match_id)
    if not rt:
        raise HTTPException(404, f"match {match_id} not found")
    return rt


def rt_for_storyline(sid: str) -> MatchRuntime:
    for rt in runtimes.values():
        if sid in rt.storylines:
            return rt
    raise HTTPException(404, f"storyline {sid} not found")


# --------------------------------------------------------------------------- routes
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


def validation_enabled() -> bool:
    """Real-match sources (ESPN, StatsBomb) are for private validation only: off unless switched on."""
    return os.getenv("ENABLE_VALIDATION_SOURCES", "").strip().lower() in ("1", "true", "yes", "on")


@app.get("/api/health")
def health() -> dict:
    p = llm.provider()
    return {"ok": True, "writer": p.label(), "model": p.describe(),
            "matches": list(runtimes), "validation_sources": validation_enabled()}


@app.get("/api/matches")
def list_matches() -> list[dict]:
    return [{"match_id": k, "names": v.names, "mode": v.mode, "now_t": v.now_t, "score": v.score(),
             "date": v.meta.get("date"), "state": v.meta.get("state")}
            for k, v in runtimes.items()]


@app.post("/api/matches")
async def create_match(req: NewMatch) -> dict:
    n = next(_match_ids)
    overrides = {k: v.model_dump() for k, v in (req.markets or {}).items()}
    if req.mode in ("espn", "real") and not validation_enabled():
        raise HTTPException(403, "Real-match validation sources are switched off. The hackathon requires synthetic "
                                 "data; set ENABLE_VALIDATION_SOURCES=1 in .env for private validation runs.")
    if req.mode == "fixture":
        if req.fixture not in fixtures.FIXTURES:
            raise HTTPException(400, f"unknown fixture {req.fixture}")
        mid = f"fixture{req.fixture}-{n}"
        f = fixtures.build(req.fixture, mid)
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, f["home"], f["away"], "fixture", overrides)
        rt.meta = {"source": f"Synthetic fixture {req.fixture}: {f['title']}", "fixture": req.fixture,
                   "title": f["title"], "description": f["description"], "official_result": f["final_score"],
                   "obligations": f["obligations"], "corrections_scheduled": len(f["corrections"])}
        rt.red_team_budget = f["red_team"]
        replay_events[mid] = f["events"]
        replay_corrections[mid] = f["corrections"]
        runtimes[mid] = rt
        return rt.snapshot()
    if req.mode == "file":
        mid = f"file{n}"
        try:
            d = filefeed.load(req.file or "", mid, req.match_key)
        except Exception as exc:
            raise HTTPException(400, str(exc))
        m = d["meta"]
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, m["home"], m["away"], "file", overrides, caps=d["caps"])
        rt.meta = {**m, "report": d["report"]}
        replay_events[mid] = d["events"]
        runtimes[mid] = rt
        return rt.snapshot()
    if req.mode == "espn":
        if not req.espn_id:
            raise HTTPException(400, "espn_id is required")
        mid = f"espn{req.espn_id}-{n}"
        try:
            data = await asyncio.to_thread(espn.summary, req.espn_id, req.league)
        except Exception as exc:
            raise HTTPException(502, f"Couldn't reach the ESPN match feed ({exc}). Check your internet connection.")
        d = espn.parse(data, mid)
        m = d["meta"]
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, m["home"], m["away"], "espn", overrides, caps=d["caps"])
        rt.meta = {k: v for k, v in m.items() if k != "team_stats"}
        rt.meta["league"] = req.league
        runtimes[mid] = rt
        if m["state"] == "post":
            rt.set_team_stats(m.get("team_stats") or {}, "espn")
            replay_events[mid] = d["events"]
        else:
            # live or not started yet: follow the feed
            rt.live_task = asyncio.create_task(live.follow(rt, req.espn_id, req.league))
        return rt.snapshot()
    if req.mode == "real":
        if not req.sb_match_id:
            raise HTTPException(400, "sb_match_id is required")
        mid = f"real{n}"
        try:
            d = statsbomb.load(req.sb_match_id, match_id=mid)
        except Exception as exc:
            raise HTTPException(502, f"Couldn't load StatsBomb open data ({exc}). Check your internet connection.")
        m = d["meta"]
        for k, v in overrides.items():   # user-supplied focus players win over nationality defaults
            d["market_overrides"].setdefault(k, {}).update({kk: vv for kk, vv in v.items() if vv})
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, m["home"], m["away"], "real", d["market_overrides"])
        rt.meta = m
        replay_events[mid] = d["events"]
    elif req.mode == "replay":
        mid = f"replay{n}"
        m = synth.generate(match_id=mid, seed=req.seed, home_name=req.home or "Northbridge FC",
                           away_name=req.away or "Riverside Athletic")
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, m.home_name, m.away_name, "replay", overrides)
        replay_events[mid] = m.events
    elif req.mode == "live":
        mid = f"live{n}"
        if not req.home or not req.away:
            raise HTTPException(400, "live mode needs home and away team names")
        store.delete_match_data(mid)
        rt = MatchRuntime(store, mid, req.home, req.away, "live", overrides)
        rt.meta = {"date": req.date, "source": "Live logger"}
        if req.official_home is not None and req.official_away is not None:
            rt.meta["official_result"] = {"HOME": req.official_home, "AWAY": req.official_away}
    else:
        raise HTTPException(400, "mode must be espn, real, replay or live")
    runtimes[mid] = rt
    return rt.snapshot()


@app.get("/api/fixtures")
def list_fixtures() -> list[dict]:
    return [{"id": k, "title": v["title"], "description": v["description"], "home": v["home"], "away": v["away"]}
            for k, v in fixtures.FIXTURES.items()]


@app.get("/api/model")
def model_status() -> dict:
    return llm.provider().describe()


@app.get("/api/matches/{match_id}/trace")
def match_trace(match_id: str, limit: int = 300) -> list[dict]:
    return rt_or_404(match_id).trace.recent(limit)


@app.get("/api/storylines/{sid}/provenance")
def storyline_provenance(sid: str) -> dict:
    """Everything behind one story: each sentence's claims with the SQL and values, the agent steps, the decisions."""
    rt = rt_for_storyline(sid)
    sl = rt.storylines[sid]
    return {
        "storyline_id": sid, "match_id": rt.match_id, "headline": sl["headline"], "status": sl["status"],
        "overall": sl["overall"], "market": sl["market"], "version": sl.get("version", 1),
        "supersedes": sl.get("supersedes"), "superseded_by": sl.get("superseded_by"),
        "sentences": [{"text": g["text"], "verdict": g["verdict"], "reason": g["reason"], "struck": g.get("struck", False),
                       "claims": [{k: c.get(k) for k in ("metric", "params", "op", "value", "actual", "ok", "sql",
                                                         "sql_params")}
                                  | {"event_ids": [r["event_id"] for r in (c.get("rows") or [])][:20]}
                                  for c in g.get("checks", [])]}
                      for g in sl["segments"]],
        "agent_trace": rt.trace.for_storyline(sid),
        "decisions": rt.audit(sid),
    }


@app.get("/api/storylines/{sid}/trace")
def storyline_trace(sid: str) -> list[dict]:
    return rt_for_storyline(sid).trace.for_storyline(sid)


class RedTeam(BaseModel):
    count: int = 1


@app.post("/api/matches/{match_id}/red-team")
def red_team(match_id: str, req: RedTeam) -> dict:
    """Test switch: the next N commissioned drafts carry one labelled false sentence."""
    rt = rt_or_404(match_id)
    rt.red_team_budget = max(0, min(10, req.count))
    rt.trace.emit("Producer", f"red-team test armed for the next {rt.red_team_budget} drafts", "started",
                  output_summary="each will carry one labelled unsupported sentence; the verifier must catch it")
    return {"red_team": rt.red_team_budget}


class DeskReq(BaseModel):
    phase: str = "full_time"


@app.post("/api/matches/{match_id}/desk")
async def desk_review(match_id: str, req: DeskReq) -> dict:
    rt = rt_or_404(match_id)
    sids = await rt.newsroom.desk_now(req.phase)
    return {"commissioned": sids, "coverage": rt.coverage(2760 if req.phase == "half_time" else 1e9)}


@app.get("/api/files")
def files() -> list[dict]:
    """Organiser data files in backend/data/organiser/."""
    return filefeed.list_files()


@app.post("/api/matches/default")
async def default_match() -> dict:
    """First match to open. Set DEFAULT_SOURCE in .env: file:<name> | espn:<id> | archive:<id> | synthetic.

    Without a setting: the first organiser file (non-example) if one exists, else the bundled ESPN match,
    else the StatsBomb archive, else a synthetic match.
    """
    pref = os.getenv("DEFAULT_SOURCE", "").strip()
    organiser = [f["name"] for f in filefeed.list_files() if not f["example"]]
    attempts = []
    if pref.startswith("file:"):
        attempts.append(NewMatch(mode="file", file=pref[5:]))
    elif pref.startswith("espn:") and validation_enabled():
        attempts.append(NewMatch(mode="espn", espn_id=pref[5:]))
    elif pref.startswith("archive:") and validation_enabled():
        attempts.append(NewMatch(mode="real", sb_match_id=int(pref[8:])))
    if organiser:
        attempts.append(NewMatch(mode="file", file=organiser[0]))
    if pref.startswith("fixture:"):
        attempts.insert(0, NewMatch(mode="fixture", fixture=pref[8:] or "A"))
    attempts.append(NewMatch(mode="fixture", fixture="A"))   # synthetic, football-realistic: the compliant default
    attempts.append(NewMatch(mode="replay", seed=7))
    last = None
    for req in attempts:
        try:
            return await create_match(req)
        except HTTPException as exc:
            last = exc
    raise last


@app.get("/api/espn/scoreboard")
async def espn_scoreboard(date: str, league: str = "eng.1") -> dict:
    """Matches on a date (YYYYMMDD or YYYY-MM-DD) from the ESPN feed: finished, live and upcoming."""
    if not validation_enabled():
        raise HTTPException(403, "Real-match validation sources are switched off (ENABLE_VALIDATION_SOURCES).")
    d = date.replace("-", "")
    try:
        ms = await asyncio.to_thread(espn.scoreboard, d, league)
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach the ESPN feed: {exc}")
    return {"date": d, "league": league, "leagues": espn.LEAGUES, "matches": ms}


@app.post("/api/storylines/{sid}/simulate-correction")
def simulate_correction(sid: str) -> dict:
    try:
        return rt_for_storyline(sid).simulate_correction(sid)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/matches/{match_id}/coverage")
def coverage(match_id: str, market: str = "world") -> dict:
    return rt_or_404(match_id).coverage(1e9, market)


@app.get("/api/real/matches")
def real_matches(competition_id: int = 2, season_id: int = 27) -> list[dict]:
    """Matches available from StatsBomb Open Data (validation only)."""
    if not validation_enabled():
        raise HTTPException(403, "Real-match validation sources are switched off (ENABLE_VALIDATION_SOURCES).")
    try:
        ms = statsbomb.matches(competition_id, season_id)
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach StatsBomb open data: {exc}")
    return sorted(ms, key=lambda m: m["date"])


@app.get("/api/matches/{match_id}")
def get_match(match_id: str) -> dict:
    return rt_or_404(match_id).snapshot()


@app.post("/api/matches/{match_id}/replay")
async def start_replay(match_id: str, req: ReplayReq) -> dict:
    rt = rt_or_404(match_id)
    if rt.mode not in ("replay", "real", "espn", "file", "fixture") or match_id not in replay_events:
        raise HTTPException(400, "not a replayable match")
    if rt.replay_task and not rt.replay_task.done():
        rt.replay_speed = req.speed
        return {"running": True, "speed": req.speed}
    done = {e["event_id"] for e in store.events(match_id)}
    remaining = [e for e in replay_events[match_id] if e.event_id not in done]
    rt.replay_task = asyncio.create_task(rt.replay(remaining, req.speed, req.start_minute * 60,
                                                   corrections=replay_corrections.get(match_id)))
    return {"running": True, "speed": req.speed}


@app.post("/api/matches/{match_id}/pause")
async def pause_replay(match_id: str) -> dict:
    rt = rt_or_404(match_id)
    if rt.replay_task and not rt.replay_task.done():
        rt.replay_task.cancel()
    return {"running": False}


@app.post("/api/matches/{match_id}/events")
async def log_event(match_id: str, req: LiveEvent) -> dict:
    rt = rt_or_404(match_id)
    if req.team not in ("HOME", "AWAY"):
        raise HTTPException(400, "team must be HOME or AWAY")
    x, y = ZONES.get(req.zone or "", (None, None))
    if req.type in ("box_entry", "shot") and x is None:
        x, y = ZONES["box"]
    if req.type == "regain" and x is None:
        x, y = ZONES["midfield"]
    e = Event(event_id=f"{match_id}-L{next(_live_ids):05d}", match_id=match_id, t=req.t, period=req.period,
              team_id=req.team, type=req.type, outcome=req.outcome, player=(req.player or None),
              x=x, y=y, source="live_logger")
    try:
        new = await rt.ingest(e)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"event": e.to_dict(), "insights": new}


@app.post("/api/events/{event_id}/undo")
def undo_event(event_id: str) -> dict:
    for rt in runtimes.values():
        if event_id.startswith(rt.match_id + "-"):
            return {"undone": rt.undo(event_id), "score": rt.score()}
    raise HTTPException(404, "event not found")


@app.get("/api/storylines/{sid}/why-not")
def why_not(sid: str, market: str = "world", phase: str = "live") -> list[dict]:
    return rt_for_storyline(sid).why_not(sid, market, phase)


@app.post("/api/storylines/{sid}/decision")
def decide(sid: str, req: Decision) -> dict:
    rt = rt_for_storyline(sid)
    try:
        return rt.decide(sid, req.action, edits=req.edits, acknowledged=req.acknowledged)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/storylines/{sid}/inject")
def inject(sid: str, req: Inject) -> dict:
    return rt_for_storyline(sid).inject_false(sid, req.kind)


class ViewerReq(BaseModel):
    base_market: str = "world"         # language / market the viewer starts from
    mode: str = "casual"               # casual | analyst
    club: Optional[str] = None         # HOME | AWAY
    player: Optional[str] = None       # player-focus mode


@app.post("/api/matches/{match_id}/viewer")
async def set_viewer(match_id: str, req: ViewerReq) -> dict:
    """Personalise: the same verified stories, ranked and shown for one viewer."""
    rt = rt_or_404(match_id)
    if req.base_market not in rt.markets or req.base_market == "viewer":
        raise HTTPException(400, f"unknown market {req.base_market}")
    if req.mode not in personalise.MODES:
        raise HTTPException(400, f"unknown mode {req.mode}")
    rt.markets["viewer"] = personalise.viewer_market(rt.markets[req.base_market], req.mode, req.club, req.player)
    rt.trace.emit("Producer", f"viewer profile: {personalise.MODES[req.mode]['label']}", "started",
                  output_summary=", ".join(x for x in [
                      rt.names.get(req.club or "", ""), req.player and f"player focus {req.player}",
                      req.base_market != "world" and rt.markets[req.base_market]["language_name"]] if x)
                  or "no club or player chosen")
    if req.base_market != "world":
        await rt.localise("viewer")
    rt.publish({"type": "rankings", "rankings": {m: rt.ranking(m) for m in rt.markets}, "funnel": rt.funnel()})
    return {"market": rt.markets["viewer"], "ranking": rt.ranking("viewer"),
            "explainers": personalise.EXPLAINERS}


@app.get("/api/matches/{match_id}/readiness")
def readiness(match_id: str, phase: str = "half_time") -> dict:
    """Go / no-go for the next editorial moment (half_time | full_time)."""
    return governance.readiness(rt_or_404(match_id), phase)


@app.get("/api/matches/{match_id}/players")
def match_players(match_id: str) -> list[dict]:
    return personalise.players(rt_or_404(match_id))


@app.get("/api/matches/{match_id}/players/{player}")
def player_card(match_id: str, player: str) -> dict:
    return personalise.player_card(rt_or_404(match_id), player)


@app.post("/api/matches/{match_id}/localise")
async def localise(match_id: str, req: MarketReq) -> list[dict]:
    return await rt_or_404(match_id).localise(req.market, phase=req.phase)


@app.get("/api/matches/{match_id}/ranking")
def ranking(match_id: str, market: str = "world", phase: str = "live") -> list[dict]:
    return rt_or_404(match_id).ranking(market, phase)


@app.post("/api/matches/{match_id}/packages")
def package(match_id: str, req: PackageReq) -> dict:
    return rt_or_404(match_id).build_package(req.kind, req.market)


@app.get("/api/matches/{match_id}/audit")
def audit(match_id: str) -> list[dict]:
    return rt_or_404(match_id).audit()


@app.get("/api/benchmark")
async def get_benchmark(refresh: bool = False) -> JSONResponse:
    path = ROOT / "backend" / "benchmark_results.json"
    if not refresh and path.exists():
        return JSONResponse(json.loads(path.read_text()))
    res = await benchmark.run()
    path.write_text(json.dumps(res, indent=2, default=str))
    return JSONResponse(res)


@app.websocket("/ws/{match_id}")
async def ws(websocket: WebSocket, match_id: str) -> None:
    rt = runtimes.get(match_id)
    await websocket.accept()
    if not rt:
        await websocket.send_text(json.dumps({"type": "error", "message": "match not found"}))
        await websocket.close()
        return
    q = rt.subscribe()
    try:
        await websocket.send_text(json.dumps({"type": "snapshot", "snapshot": rt.snapshot()}, default=str))
        while True:
            msg = await q.get()
            await websocket.send_text(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.unsubscribe(q)
