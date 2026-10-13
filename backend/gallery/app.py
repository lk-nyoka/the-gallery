"""FastAPI server: REST + WebSocket + the producer console.

Run:  uvicorn gallery.app:app --reload   (from the backend folder)
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
import itertools
import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import benchmark, espn, filefeed, fixtures, governance, live, llm, overlays, personalise, statsbomb, synth
from .production import SECURITY_HEADERS, bucket, limiter, log, metrics, new_request_id, settings, setup_azure_monitor
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
    setup_azure_monitor()
    llm.provider()        # start the model provider early so Foundry Local can download/load while the console opens
    janitor = asyncio.create_task(_evict_idle_loop())
    log.info("started", extra={"path": f"public_demo={settings.public_demo} version={settings.version}"})
    try:
        yield
    finally:          # graceful shutdown: stop replays and live follows, close the database
        janitor.cancel()
        for rt in list(runtimes.values()):
            for task in (getattr(rt, "replay_task", None), getattr(rt, "live_task", None)):
                if task and not task.done():
                    task.cancel()
        try:
            store.conn.close()
        except Exception:
            pass
        log.info("stopped")


app = FastAPI(title="The Gallery", version=settings.version, lifespan=_lifespan,
              docs_url="/docs" if settings.api_docs else None, redoc_url=None,
              openapi_url="/openapi.json" if settings.api_docs else None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
if settings.public_demo and (DATA / "gallery.db").exists():
    (DATA / "gallery.db").unlink()      # the public demo keeps no state between restarts
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
Short = Field(default=None, max_length=60)


class MarketOverride(BaseModel):
    focus_players: Optional[list[str]] = Field(default=None, max_length=20)
    focus_teams: Optional[list[str]] = Field(default=None, max_length=2)


class NewMatch(BaseModel):
    mode: str = Field(default="fixture", pattern="^(fixture|file|replay|live|espn|real)$")
    fixture: str = Field(default="A", max_length=2)  # A turning point · B correction · C coverage stress
    file: Optional[str] = Field(default=None, max_length=120)      # name in backend/data/organiser/
    match_key: Optional[str] = Field(default=None, max_length=120)
    espn_id: str = Field(default="", max_length=20)                 # validation only
    league: str = Field(default="eng.1", max_length=30)
    sb_match_id: int = Field(default=0, ge=0)                       # validation only
    home: Optional[str] = Field(default=None, max_length=40)
    away: Optional[str] = Field(default=None, max_length=40)
    seed: int = Field(default=7, ge=0, le=1_000_000)
    markets: Optional[dict[str, MarketOverride]] = None
    official_home: Optional[int] = Field(default=None, ge=0, le=50)  # live mode: final score, checked at full time
    official_away: Optional[int] = Field(default=None, ge=0, le=50)
    date: Optional[str] = Field(default=None, max_length=20)


class ReplayReq(BaseModel):
    speed: float = Field(default=20.0, gt=0, le=3000)
    start_minute: float = Field(default=0.0, ge=0, le=130)


class LiveEvent(BaseModel):
    team: str = Field(pattern="^(HOME|AWAY)$")
    type: str = Field(max_length=20)
    t: float = Field(ge=0, le=4 * 3600)
    period: int = Field(default=1, ge=1, le=5)
    outcome: Optional[str] = Field(default=None, max_length=20)
    player: Optional[str] = Short
    zone: Optional[str] = Field(default=None, max_length=20)


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|reject|edit)$")
    edits: Optional[dict] = None
    acknowledged: Optional[list[int]] = Field(default=None, max_length=50)


class Inject(BaseModel):
    kind: Optional[str] = Field(default=None, max_length=40)


class MarketReq(BaseModel):
    market: str = Field(max_length=20)
    phase: str = Field(default="live", max_length=20)


class PackageReq(BaseModel):
    kind: str = Field(default="half_time", pattern="^(half_time|full_time)$")
    market: str = Field(default="world", max_length=20)


# --------------------------------------------------------------------------- helpers
def rt_or_404(match_id: str) -> MatchRuntime:
    rt = runtimes.get(match_id)
    if not rt or not _mine(rt):
        raise HTTPException(404, f"match {match_id} not found")
    _touched[match_id] = time.time()
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
    if os.getenv("PUBLIC_DEMO", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return os.getenv("ENABLE_VALIDATION_SOURCES", "").strip().lower() in ("1", "true", "yes", "on")


@app.get("/api/health")
def health() -> dict:
    p = llm.provider()
    return {"ok": True, "writer": p.label(), "model": p.describe(),
            "matches": len(runtimes), "validation_sources": validation_enabled(), "public_demo": PUBLIC_DEMO}


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Liveness: the process is up."""
    return {"ok": True}


@app.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Readiness: the database answers and the console files are present."""
    try:
        store.query("SELECT 1 AS ok")
        ok = (WEB / "index.html").exists()
    except Exception:
        ok = False
    return JSONResponse({"ready": ok}, status_code=200 if ok else 503)


@app.get("/api/metrics")
def service_metrics() -> dict:
    """Operational counters for this instance (no personal data)."""
    p = llm.provider()
    return {**metrics.snapshot(), "version": settings.version, "commit": settings.commit,
            "matches_active": len(runtimes), "public_demo": PUBLIC_DEMO,
            "model": {"provider": p.describe()["provider"], "calls": p.calls, "failures": p.failures}}


# ------------------------------------------------------------------ public demo: one sandbox per visitor
PUBLIC_DEMO = settings.public_demo
MAX_PER_VISITOR, MAX_MATCHES = settings.max_per_visitor, settings.max_matches
_owner: ContextVar[str] = ContextVar("owner", default="local")
_created: dict[str, float] = {}
_touched: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))[:64]


@app.middleware("http")
async def _edge(request: Request, call_next):
    """Request id, visitor sandbox, body-size and rate limits, security headers, access log and metrics."""
    rid = request.headers.get("x-request-id", "")[:32] or new_request_id()
    sid = request.headers.get("x-gallery-session", "")[:64]
    token = _owner.set(sid if PUBLIC_DEMO and sid else "local")
    t0 = time.perf_counter()
    status = 500
    try:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > settings.max_body_bytes:
            response = JSONResponse({"detail": "Request body too large.", "request_id": rid}, status_code=413)
        else:
            b = bucket(request.method, request.url.path)
            ok, retry = (True, 0)
            if b and PUBLIC_DEMO:
                ok, retry = limiter.allow(f"{b[0]}:{_client_ip(request)}:{sid}", b[1])
            if not ok:
                metrics.rate_limited += 1
                response = JSONResponse({"detail": "Too many requests. Try again in a moment.", "request_id": rid},
                                        status_code=429, headers={"Retry-After": str(retry)})
            else:
                response = await call_next(request)
        status = response.status_code
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        ms = (time.perf_counter() - t0) * 1000
        metrics.observe(status, ms)
        if request.url.path.startswith(("/api/", "/healthz", "/readyz")) and request.url.path not in ("/healthz",):
            quiet = request.method == "GET" and status < 400   # polling is noisy; keep errors and writes at INFO
            log.log(10 if quiet else 20, "request", extra={"request_id": rid, "method": request.method, "path": request.url.path,
                                       "status": status, "ms": round(ms, 1), "visitor": (sid or "-")[:8]})
        _owner.reset(token)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    problems = [f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in exc.errors()][:5]
    return JSONResponse({"detail": "Invalid request: " + "; ".join(problems)}, status_code=422)


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception):
    rid = new_request_id()
    log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
    return JSONResponse({"detail": "Something went wrong on our side. It has been logged.", "request_id": rid},
                        status_code=500)


async def _evict_idle_loop() -> None:
    """Free memory: drop matches nobody has touched for MATCH_IDLE_MINUTES (public demo only)."""
    while True:
        await asyncio.sleep(300)
        if not PUBLIC_DEMO:
            continue
        cutoff = time.time() - settings.idle_minutes * 60
        for mid in [m for m, t in list(_touched.items()) if t < cutoff]:
            _drop(mid)
            metrics.matches_evicted += 1


def _drop(mid: str) -> None:
    rt = runtimes.pop(mid, None)
    replay_events.pop(mid, None)
    replay_corrections.pop(mid, None)
    _created.pop(mid, None)
    _touched.pop(mid, None)
    if rt:
        for task in (getattr(rt, "replay_task", None), getattr(rt, "live_task", None)):
            if task and not task.done():
                task.cancel()
        store.delete_match_data(mid)


def _mine(rt: MatchRuntime) -> bool:
    return not PUBLIC_DEMO or getattr(rt, "owner", "local") == _owner.get()


@app.get("/api/matches")
def list_matches() -> list[dict]:
    return [{"match_id": k, "names": v.names, "mode": v.mode, "now_t": v.now_t, "score": v.score(),
             "date": v.meta.get("date"), "state": v.meta.get("state")}
            for k, v in runtimes.items() if _mine(v)]


@app.post("/api/matches")
async def create_match(req: NewMatch) -> dict:
    if PUBLIC_DEMO and req.mode not in ("fixture", "replay", "file", "live"):
        raise HTTPException(403, "This public demo runs synthetic matches only.")
    snap = await _create_match(req)
    mid = snap["match_id"]
    runtimes[mid].owner = _owner.get()
    _created[mid] = _touched[mid] = time.time()
    metrics.matches_created += 1
    if PUBLIC_DEMO:
        mine = sorted((m for m, r in runtimes.items() if getattr(r, "owner", "") == _owner.get()),
                      key=lambda m: _created.get(m, 0))
        for old in mine[:-MAX_PER_VISITOR]:
            _drop(old)
        for old in sorted(runtimes, key=lambda m: _created.get(m, 0))[:-MAX_MATCHES]:
            _drop(old)
    return snap


async def _create_match(req: NewMatch) -> dict:
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
    count: int = Field(default=1, ge=0, le=10)


@app.post("/api/matches/{match_id}/red-team")
def red_team(match_id: str, req: RedTeam) -> dict:
    """Test switch: the next N commissioned drafts carry one labelled false sentence."""
    rt = rt_or_404(match_id)
    rt.red_team_budget = max(0, min(10, req.count))
    rt.trace.emit("Producer", f"red-team test armed for the next {rt.red_team_budget} drafts", "started",
                  output_summary="each will carry one labelled unsupported sentence; the verifier must catch it")
    return {"red_team": rt.red_team_budget}


class DeskReq(BaseModel):
    phase: str = Field(default="full_time", pattern="^(half_time|full_time)$")


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
    base_market: str = Field(default="world", max_length=20)   # language / market the viewer starts from
    mode: str = Field(default="casual", pattern="^(casual|analyst)$")
    club: Optional[str] = Field(default=None, pattern="^(HOME|AWAY)$")
    player: Optional[str] = Short                               # player-focus mode


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


@app.get("/api/matches/{match_id}/overlays")
def overlay_feed(match_id: str, market: str = "world") -> dict:
    """Timed, machine-readable overlay cues for a graphics engine (approved stories + threshold player tags)."""
    return overlays.feed(rt_or_404(match_id), market)


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
    if PUBLIC_DEMO:
        refresh = False          # the public demo serves the committed benchmark run
    if not refresh and path.exists():
        return JSONResponse(json.loads(path.read_text()))
    res = await benchmark.run()
    path.write_text(json.dumps(res, indent=2, default=str))
    return JSONResponse(res)


@app.websocket("/ws/{match_id}")
async def ws(websocket: WebSocket, match_id: str) -> None:
    rt = runtimes.get(match_id)
    await websocket.accept()
    sid = websocket.query_params.get("s", "")[:64]
    if rt and PUBLIC_DEMO and getattr(rt, "owner", "local") != sid:
        rt = None                        # another visitor's sandbox
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
