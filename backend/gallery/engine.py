"""Match runtime: Detect -> Rank -> Claim -> Verify -> Approve -> Localise -> Package."""

from __future__ import annotations

import asyncio
import itertools
import json
import random
import time
from typing import Any, Optional

from . import editorial, governance, llm, verifier
from .agents.analyst import Analyst
from .agents.corrections import Corrections
from .agents.desk import Desk
from .agents.editor import EditorInChief
from .agents.localiser import Localiser
from .agents.newsroom import Newsroom
from .agents.tools import Toolbox
from .agents.trace import Trace
from .agents.writer import WriterAgent
from .detectors import DetectorSuite
from .events import Event, validate
from .markets import markets_for
from .store import Store
from .writer import false_claims

_sid = itertools.count(1)
_cid = itertools.count(1)
_pid = itertools.count(1)


class MatchRuntime:
    def __init__(self, store: Store, match_id: str, home_name: str, away_name: str, mode: str,
                 market_overrides: Optional[dict] = None, use_model: bool = True, caps: Optional[dict] = None):
        self.store = store
        self.match_id = match_id
        self.mode = mode  # "replay" | "live"
        self.names = {"HOME": home_name, "AWAY": away_name}
        self.markets = markets_for(market_overrides)
        self.caps = caps or ({"passes": False} if mode == "live" else {})
        self.detectors = DetectorSuite(match_id, "HOME", "AWAY", passes_available=self.caps.get("passes", True),
                                       caps=self.caps)
        self.correction_log: list[dict] = []
        self.live_task: Optional[asyncio.Task] = None
        self.live_status: dict[str, Any] = {}
        self.insights: list[dict] = []
        self.storylines: dict[str, dict] = {}
        self.by_insight: dict[tuple[str, str], str] = {}   # (insight_id, market) -> storyline_id
        self.subscribers: set[asyncio.Queue] = set()
        self.now_t = 0.0
        self.now_clock = 0.0
        self.period = 1
        self.meta: dict[str, Any] = {}
        self.replay_task: Optional[asyncio.Task] = None
        self.replay_speed = 1.0
        self.pending: set[asyncio.Task] = set()
        self.packages: list[dict] = []
        self.rng = random.Random(1)
        self.provider = llm.provider() if use_model else llm.Provider()
        self.red_team_budget = 0
        # the newsroom
        self.toolbox = Toolbox(self)
        self.trace = Trace(self)
        self.editor = EditorInChief(self)
        self.analyst = Analyst(self)
        self.writer_agent = WriterAgent(self)
        self.desk = Desk(self)
        self.corrections = Corrections(self)
        self.localiser = Localiser(self)
        self.newsroom = Newsroom(self)
        store.create_match(match_id, "HOME", "AWAY", home_name, away_name, mode,
                           {"markets": {k: {"focus_players": v["focus_players"]} for k, v in self.markets.items()}})

    # ------------------------------------------------------------------ pub/sub
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, msg: dict) -> None:
        data = json.dumps(msg, default=str)
        for q in list(self.subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------ state
    def score(self) -> dict[str, int]:
        return self.detectors.score_at(self.now_t)

    def lineups(self) -> dict[str, list[dict[str, Any]]]:
        """Team sheets with shirt numbers. Synthetic matches use the generator's squad list (numbered in the order
        listed); other feeds use the numbers they carry, or number players in the order they first appear."""
        from .synth import AWAY_ROSTER, HOME_ROSTER
        sheet = (self.meta or {}).get("lineups")
        if sheet:
            return sheet
        rows = self.store.query("SELECT player, team_id, MIN(t) AS first FROM events WHERE match_id = ? AND player IS NOT NULL "
                                "AND player != '' AND type != 'own_goal' GROUP BY player, team_id ORDER BY first", (self.match_id,))
        out: dict[str, list[dict[str, Any]]] = {"HOME": [], "AWAY": []}
        for team, roster in (("HOME", HOME_ROSTER), ("AWAY", AWAY_ROSTER)):
            if self.mode in ("fixture", "replay") and all(r["player"] in roster for r in rows if r["team_id"] == team):
                out[team] = [{"player": p, "number": i + 1, "listed": True} for i, p in enumerate(roster)]
            else:
                names = [r["player"] for r in rows if r["team_id"] == team]
                out[team] = [{"player": p, "number": i + 1, "listed": False} for i, p in enumerate(names)]
        return out

    def model_status(self) -> str:
        return self.provider.label()

    def insight(self, insight_id: str) -> dict:
        return next(i for i in self.insights if i["insight_id"] == insight_id)

    def red_team_next(self) -> bool:
        if self.red_team_budget > 0:
            self.red_team_budget -= 1
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id, "mode": self.mode, "names": self.names, "now_t": self.now_t,
            "now_clock": self.now_clock, "meta": self.meta, "result_check": self.result_check(),
            "caps": self.detectors.caps, "corrections": self.correction_log, "live": self.live_status,
            "provider": self.provider.describe(), "trace": self.trace.recent(250), "red_team": self.red_team_budget,
            "period": self.period, "score": self.score(), "markets": self.markets,
            "writer": self.model_status(),
            "insights": self.insights,
            "storylines": list(self.storylines.values()),
            "rankings": {m: self.ranking(m) for m in self.markets},
            "events": self.store.events(self.match_id)[-4000:],
            "lineups": self.lineups(),
            "packages": self.packages,
            "funnel": self.funnel(),
            "replay": {"running": bool(self.replay_task and not self.replay_task.done()),
                       "speed": self.replay_speed},
        }

    # ------------------------------------------------------------------ ingest
    async def ingest(self, e: Event) -> list[dict]:
        validate(e)
        self.store.add_event(e)
        d = e.to_dict()
        self.now_t = max(self.now_t, e.t)
        self.now_clock = e.clock if e.clock is not None else self.now_t
        self.period = max(self.period, e.period)
        new = [i.to_dict() for i in self.detectors.on_event(d)]      # first, so the score below includes this event
        self.publish({"type": "event", "event": d, "score": self.score(), "now_t": self.now_t,
                      "now_clock": self.now_clock})
        for ins in new:
            self._save_insight(ins)
            self.insights.append(ins)
            self.publish({"type": "insight", "insight": ins})
        if new:
            self.newsroom.on_insights()
        if e.type == "period_end":
            self.newsroom.on_period_end(e.period)
        return new

    def undo(self, event_id: str) -> bool:
        before = len(self.detectors.events)
        self.detectors.events = [x for x in self.detectors.events if x["event_id"] != event_id]
        self.store.delete_event(event_id)
        self.publish({"type": "undo", "event_id": event_id, "score": self.score()})
        return len(self.detectors.events) < before

    def _save_insight(self, ins: dict) -> None:
        self.store.execute(
            "INSERT INTO insights VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ins["insight_id"], self.match_id, ins["detector"], ins["t"], ins["from_t"], ins["to_t"],
             ins["team_id"], json.dumps(ins["players"]), json.dumps(ins["metrics"]),
             json.dumps(ins["evidence"]), ins["confidence"]))

    # ------------------------------------------------------------------ rank
    def ranking(self, market: str, phase: str = "live") -> list[dict]:
        ctx = {"teams": ("HOME", "AWAY"), "score": self.score()}
        mk = self.markets[market]
        if phase == "first_half":
            r = editorial.rank([i for i in self.insights if i["t"] <= 2700 or self._period_of(i) == 1],
                               self.now_t, ctx, mk, window_s=None)
        elif phase == "full":
            r = editorial.rank(self.insights, self.now_t, ctx, mk, window_s=None)
        else:
            r = editorial.rank(self.insights, self.now_t, ctx, mk, window_s=15 * 60)
        focus = (mk.get("viewer") or {}).get("player")
        if focus:   # player-focus mode: that player's stories lead, in score order; everything else follows
            by_id = {i["insight_id"]: i for i in self.insights}
            r.sort(key=lambda x: focus not in (by_id.get(x["insight_id"], {}).get("players") or []))
            for n, x in enumerate(r, 1):
                x["rank"] = n
                x["player_focus"] = focus in (by_id.get(x["insight_id"], {}).get("players") or [])
        for s in r:
            sid = self.by_insight.get((s["insight_id"], market)) or self.by_insight.get((s["insight_id"], "world"))
            s["storyline_id"] = sid
        return r

    def _period_of(self, ins: dict) -> int:
        rows = self.store.events_by_ids(ins["evidence"][-1:])
        return rows[0]["period"] if rows else 1

    def why_not(self, storyline_id: str, market: str, phase: str = "live") -> list[dict]:
        sl = self.storylines[storyline_id]
        r = self.ranking(market, phase)
        idx = next((i for i, s in enumerate(r) if s["insight_id"] == sl["insight_id"]), None)
        if idx is None:
            r = self.ranking(market, "full")
            idx = next((i for i, s in enumerate(r) if s["insight_id"] == sl["insight_id"]), None)
            if idx is None:
                return []
        chosen = r[idx]
        others = r[idx + 1:]
        by_id = {i["insight_id"]: i for i in self.insights}
        return editorial.why_not(chosen, others, by_id, self.names)

    # ------------------------------------------------------------------ newsroom output
    def register_storyline(self, c, draft, result, history: list[dict], latency_ms: float) -> dict:
        """Called by the workflow's producer gate: a verified (or flagged) draft waits for a human."""
        ins = self.insight(c.insight_id)
        mk = self.markets[c.market]
        sid = f"{self.match_id}-s{next(_sid):04d}"
        segments = []
        for seg, res in zip(draft.segments, result.segment_results):
            segments.append({"text": seg.text, "claims": [cl.model_dump() for cl in seg.claims],
                             "injected": seg.injected, "injected_kind": seg.injected_kind,
                             "verdict": res["verdict"], "reason": res["reason"], "checks": res["claims"]})
        not_localised = draft.writer.startswith("not localised")
        sl = {
            "storyline_id": sid, "insight_id": ins["insight_id"], "market": c.market,
            "language": "en" if not_localised else mk["language"],
            "detector": ins["detector"], "minute": ins["minute"], "team_id": ins["team_id"],
            "headline": draft.headline, "segments": segments, "graphic": draft.graphic, "clip_cue": draft.clip_cue,
            "writer": draft.writer, "status": "drafted", "created_at": time.time(),
            "base_storyline_id": c.base_storyline_id, "commission_id": c.commission_id,
            "commissioned_by": c.commissioned_by, "brief": c.brief, "commission_reason": c.reason,
            "version": draft.version, "revisions": max(0, len(history) - 1), "history": history,
            "supersedes": c.supersedes, "red_team": c.red_team,
            "headline_verdict": result.headline_verdict, "headline_reason": result.headline_reason,
            "overall": result.verdict, "latency_ms": round(latency_ms, 1),
        }
        self.storylines[sid] = sl
        self.by_insight[(ins["insight_id"], c.market)] = sid
        self._log_claims(sl)
        self.store.execute(
            "INSERT INTO storylines VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, ins["insight_id"], self.match_id, c.market, sl["headline"], json.dumps(sl["segments"], default=str),
             json.dumps(sl["graphic"]), json.dumps(sl["clip_cue"]), sl["writer"], sl["status"], sl["created_at"],
             sl["latency_ms"]))
        self.trace.link(c.commission_id, sid)
        verdict_txt = {"verified": "verified", "needs_human": "verified numbers, wording needs a human check",
                       "blocked": "still blocked after revisions: kept for the audit, not approvable"}[result.verdict]
        self.trace.emit("Producer", "awaiting decision" if result.verdict != "blocked" else "blocked draft held",
                        "waiting" if result.verdict != "blocked" else "rejected", storyline_id=sid,
                        commission_id=c.commission_id, output_summary=f"“{draft.headline}” · {verdict_txt}"
                        + (f" · replaces {c.supersedes}" if c.supersedes else ""))
        self.publish({"type": "storyline", "storyline": sl})
        self.publish({"type": "rankings", "rankings": {m: self.ranking(m) for m in self.markets},
                      "funnel": self.funnel()})
        return sl

    def _log_claims(self, sl: dict) -> None:
        for i, seg in enumerate(sl["segments"]):
            self.store.execute(
                "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{sl['storyline_id']}-c{i}-{next(_cid)}", sl["storyline_id"], i, seg["text"],
                 json.dumps([c.get("metric") for c in seg.get("checks", [])]),
                 json.dumps([c.get("params") for c in seg.get("checks", [])], default=str),
                 json.dumps([c.get("op") for c in seg.get("checks", [])]), None, None, seg["verdict"], seg["reason"],
                 json.dumps([c.get("sql") for c in seg.get("checks", [])]), None, int(bool(seg.get("injected"))),
                 time.time()))

    def verify(self, sl: dict) -> None:
        english = sl.get("language", "en") == "en"
        for i, seg in enumerate(sl["segments"]):
            res = verifier.verify_segment(self.store, self.match_id, seg, english=english)
            seg["verdict"], seg["reason"], seg["checks"] = res["verdict"], res["reason"], res["claims"]
        self._log_claims(sl)
        hv = verifier.verify_headline(sl["headline"], sl["segments"], english=english)
        sl["headline_verdict"], sl["headline_reason"] = hv["verdict"], hv["reason"]
        sl["overall"] = verifier.overall([hv["verdict"]] + [s["verdict"] for s in sl["segments"]
                                                             if not s.get("struck")])

    # ------------------------------------------------------------------ producer actions
    def decide(self, storyline_id: str, action: str, actor: str = "producer",
               edits: Optional[dict] = None, acknowledged: Optional[list[int]] = None) -> dict:
        sl = self.storylines[storyline_id]
        before = {"headline": sl["headline"], "segments": [s["text"] for s in sl["segments"]], "status": sl["status"]}
        if action == "edit":
            edits = edits or {}
            if "headline" in edits:
                sl["headline"] = edits["headline"]
            for k, txt in (edits.get("segments") or {}).items():
                sl["segments"][int(k)]["text"] = txt
            for k in edits.get("strike", []):
                sl["segments"][int(k)]["struck"] = True
            self.verify(sl)
            sl["status"] = "edited"
        elif action == "approve":
            live = [s for s in sl["segments"] if not s.get("struck")]
            if any(s["verdict"] == verifier.BLOCKED for s in live) or sl["headline_verdict"] == verifier.BLOCKED:
                raise ValueError("Blocked content can't be approved. Strike or edit the blocked sentence first.")
            need = [i for i, s in enumerate(sl["segments"]) if not s.get("struck")
                    and s["verdict"] == verifier.NEEDS_HUMAN]
            if sl["headline_verdict"] == verifier.NEEDS_HUMAN:
                need.append(-1)
            missing = [i for i in need if i not in (acknowledged or [])]
            if missing:
                raise ValueError("Confirm the flagged wording first (needs human).")
            if sl["status"] == "superseded":
                raise ValueError("This version was replaced by a corrected one. Approve the new version instead.")
            sl["status"] = "approved"
            sl.pop("correction", None)
            sl["approved_at_t"] = self.now_t
            old = self.storylines.get(sl.get("supersedes") or "")
            if old and old["status"] != "superseded":
                old["status"] = "superseded"
                old["superseded_by"] = storyline_id
                self.store.execute("INSERT INTO decisions (storyline_id, actor, action, before, after, at) "
                                   "VALUES (?,?,?,?,?,?)", (old["storyline_id"], "system", "superseded",
                                                            json.dumps({"status": "needs_correction"}),
                                                            json.dumps({"superseded_by": storyline_id}), time.time()))
                self.publish({"type": "storyline", "storyline": old})
        elif action == "reject":
            sl["status"] = "rejected"
        else:
            raise ValueError(f"unknown action {action}")
        after = {"headline": sl["headline"], "segments": [s["text"] for s in sl["segments"]], "status": sl["status"]}
        self.trace.emit("Producer", {"approve": "approved for air", "reject": "rejected",
                                     "edit": "edited (re-verified)"}[action],
                        {"approve": "succeeded", "reject": "rejected", "edit": "retry"}[action],
                        storyline_id=storyline_id, commission_id=sl.get("commission_id"),
                        output_summary=f"“{sl['headline']}” · {sl['overall'].replace('_', ' ')}"
                        + (f" · replaces {sl['supersedes']}" if action == "approve" and sl.get("supersedes") else ""))
        self.store.execute("INSERT INTO decisions (storyline_id, actor, action, before, after, at) VALUES (?,?,?,?,?,?)",
                           (storyline_id, actor, action, json.dumps(before), json.dumps(after), time.time()))
        self.publish({"type": "storyline", "storyline": sl})
        self.publish({"type": "rankings", "rankings": {m: self.ranking(m) for m in self.markets},
                      "funnel": self.funnel()})
        return sl

    def audit(self, storyline_id: Optional[str] = None) -> list[dict]:
        if storyline_id:
            return self.store.query("SELECT * FROM decisions WHERE storyline_id = ? ORDER BY at", (storyline_id,))
        ids = list(self.storylines)
        if not ids:
            return []
        return self.store.query(f"SELECT * FROM decisions WHERE storyline_id IN ({','.join('?' * len(ids))}) "
                                f"ORDER BY at DESC", ids)

    def inject_false(self, storyline_id: str, kind: Optional[str] = None) -> dict:
        sl = self.storylines[storyline_id]
        ins = next(i for i in self.insights if i["insight_id"] == sl["insight_id"])
        w = {"team_id": ins["team_id"], "from_t": max(0.0, ins["to_t"] - 480), "to_t": ins["to_t"]}
        w["actual"] = verifier.check_claim(self.store, self.match_id, {
            "metric": "possession_pct", "params": w, "op": "==", "value": 0}).actual
        w["official"] = self.official_stat("possessionPct")
        options = false_claims(ins, self.names, w, self.rng)
        pick = next((o for o in options if o["kind"] == kind), None) if kind else None
        if pick is None:
            used = {s.get("injected_kind") for s in sl["segments"]}
            pick = next((o for o in options if o["kind"] not in used), options[0])
        sl["segments"].insert(0, {"text": pick["text"], "claims": pick["claims"], "injected": True,
                                  "injected_kind": pick["kind"]})
        self.verify(sl)
        self.publish({"type": "storyline", "storyline": sl})
        self.publish({"type": "rankings", "rankings": {m: self.ranking(m) for m in self.markets},
                      "funnel": self.funnel()})
        return sl

    # ------------------------------------------------------------------ localisation
    async def localise(self, market: str, top_n: int = 3, phase: str = "live") -> list[dict]:
        """Localiser agent: the market's top stories, adapted and re-verified."""
        if market == "world" or self.markets[market].get("language", "en") == "en":
            return []          # English markets reuse the world stories; only the ranking changes
        await self.newsroom.localise(market, phase, top_n)
        return [self.storylines[self.by_insight[(r["insight_id"], market)]]
                for r in self.ranking(market, phase) if (r["insight_id"], market) in self.by_insight][:top_n]

    def set_team_stats(self, stats: dict[str, dict], source: str) -> None:
        for team, kv in stats.items():
            for k, val in kv.items():
                try:
                    num = float(str(val).replace("%", ""))
                except (TypeError, ValueError):
                    continue
                self.store.execute("INSERT OR REPLACE INTO match_stats VALUES (?,?,?,?,?)",
                                   (self.match_id, team, k, num, source))

    def official_stat(self, stat: str) -> Optional[dict]:
        rows = self.store.query("SELECT team_id, value FROM match_stats WHERE match_id = ? AND stat = ?",
                                (self.match_id, stat))
        return {r["team_id"]: r["value"] for r in rows} if len(rows) == 2 else None

    # ------------------------------------------------------------------ living verification
    def reverify_all(self, reason: str) -> list[dict]:
        """Re-check every storyline after the data changed. Approved items that are now wrong are flagged."""
        flagged = []
        self.stale_drafts: list[dict] = []
        for sl in self.storylines.values():
            if sl["status"] in ("superseded", "rejected"):
                continue
            before = sl["overall"]
            self.verify(sl)
            if sl["status"] in ("drafted", "edited") and before != verifier.BLOCKED and sl["overall"] == verifier.BLOCKED:
                self.stale_drafts.append(sl)
            if sl["status"] == "approved" and sl["overall"] == verifier.BLOCKED:
                sl["status"] = "needs_correction"
                bad = next((g for g in sl["segments"] if g["verdict"] == verifier.BLOCKED and not g.get("struck")), None)
                sl["correction"] = {"reason": reason, "detail": bad["reason"] if bad else sl["headline_reason"],
                                    "sentence": bad["text"] if bad else sl["headline"]}
                flagged.append(sl)
            if before != sl["overall"] or sl in flagged:
                self.publish({"type": "storyline", "storyline": sl})
        self.publish({"type": "rankings", "rankings": {m: self.ranking(m) for m in self.markets},
                      "funnel": self.funnel()})
        return flagged

    def correct_event(self, event_id: str, changes: dict, reason: str) -> dict:
        """Apply a data correction from the feed (e.g. a goal re-credited) and re-verify everything."""
        allowed = {k: v for k, v in changes.items() if k in ("player", "outcome", "team_id", "type")}
        if not allowed:
            raise ValueError("nothing to correct")
        rows = self.store.query("SELECT * FROM events WHERE event_id = ?", (event_id,))
        if not rows:
            raise ValueError(f"event {event_id} not found")
        before = {k: rows[0][k] for k in allowed}
        sets = ", ".join(f"{k} = ?" for k in allowed)
        self.store.execute(f"UPDATE events SET {sets} WHERE event_id = ?", [*allowed.values(), event_id])
        for e in self.detectors.events:
            if e["event_id"] == event_id:
                e.update(allowed)
        flagged = self.reverify_all(reason)
        stale = [s["storyline_id"] for s in self.stale_drafts]
        rec = {"event_id": event_id, "minute": rows[0]["minute"], "before": before, "after": allowed,
               "reason": reason, "at": time.time(), "flagged": [s["storyline_id"] for s in flagged],
               "stale_drafts": stale}
        rec["impact"] = governance.correction_impact(self, rec["flagged"], stale)
        self.correction_log.append(rec)
        self.trace.emit("Feed", f"data correction at {rows[0]['minute']}'", "rejected", tool="living_verification",
                        input_summary=reason, output_summary=f"{len(flagged)} approved and {len(stale)} drafted "
                                                             f"stories are now contradicted by the data")
        self.publish({"type": "correction", "correction": rec, "score": self.score()})
        self.newsroom.on_correction(rec["flagged"] + stale, reason)
        return rec

    def retract_event(self, event_id: str, reason: str) -> dict:
        rows = self.store.query("SELECT * FROM events WHERE event_id = ?", (event_id,))
        self.store.delete_event(event_id)
        self.detectors.events = [x for x in self.detectors.events if x["event_id"] != event_id]
        flagged = self.reverify_all(reason)
        rec = {"event_id": event_id, "minute": rows[0]["minute"] if rows else None, "before": rows[0] if rows else None,
               "after": None, "reason": reason, "at": time.time(), "flagged": [s["storyline_id"] for s in flagged],
               "stale_drafts": [s["storyline_id"] for s in self.stale_drafts]}
        rec["impact"] = governance.correction_impact(self, rec["flagged"], rec["stale_drafts"])
        self.correction_log.append(rec)
        self.publish({"type": "correction", "correction": rec, "score": self.score()})
        self.newsroom.on_correction(rec["flagged"] + rec["stale_drafts"], reason)
        return rec

    def simulate_correction(self, storyline_id: str) -> dict:
        """Demo/test: re-credit the goal in this story, the way leagues re-credit goals after review."""
        sl = self.storylines[storyline_id]
        ins = next(i for i in self.insights if i["insight_id"] == sl["insight_id"])
        target = None
        for eid in reversed(ins["evidence"]):
            row = self.store.query("SELECT * FROM events WHERE event_id = ?", (eid,))
            if row and row[0]["type"] in ("shot", "own_goal") and row[0]["player"]:
                target = row[0]
                break
        if not target:
            raise ValueError("No scorer or shooter in this story's evidence to re-credit.")
        mates = [r["player"] for r in self.store.query(
            "SELECT DISTINCT player FROM events WHERE match_id = ? AND team_id = ? AND player IS NOT NULL "
            "AND player != ? ORDER BY t", (self.match_id, target["team_id"], target["player"]))]
        new_player = mates[0] if mates else "Another player"
        return self.correct_event(target["event_id"], {"player": new_player},
                                  f"SIMULATED TEST: feed re-credited {target['minute']}' from {target['player']} "
                                  f"to {new_player}")

    # ------------------------------------------------------------------ coverage check
    def key_moments(self, until_clock: float = 1e9) -> list[dict]:
        rows = self.store.query(
            "SELECT * FROM events WHERE match_id = ? AND ((type = 'shot' AND outcome = 'goal') OR type = 'own_goal' "
            "OR (type = 'card' AND outcome = 'red')) ORDER BY t", (self.match_id,))
        out = []
        for r in rows:
            clock = r["clock"] if r["clock"] is not None else r["t"]
            if clock > until_clock:
                continue
            label = {"shot": "Goal", "own_goal": "Own goal", "card": "Red card"}[r["type"]]
            out.append({"event_id": r["event_id"], "minute": r["minute"], "label": label, "player": r["player"],
                        "team": self.names.get(r["team_id"], r["team_id"])})
        return out

    def coverage(self, until_clock: float = 1e9, market: str = "world") -> dict:
        approved = [s for s in self.storylines.values() if s["status"] == "approved"
                    and s["market"] in (market, "world")]
        ins_by_id = {i["insight_id"]: i for i in self.insights}
        covered_ids = set()
        for s in approved:
            covered_ids.update(ins_by_id.get(s["insight_id"], {}).get("evidence", []))
        moments = self.key_moments(until_clock)
        for m in moments:
            m["covered"] = m["event_id"] in covered_ids
        missing = [m for m in moments if not m["covered"]]
        return {"moments": moments, "missing": missing, "complete": not missing,
                "summary": (f"All {len(moments)} key moments covered" if not missing
                            else f"{len(moments) - len(missing)} of {len(moments)} key moments covered")}

    # ------------------------------------------------------------------ packages
    def build_package(self, kind: str, market: str) -> dict:
        t0 = time.perf_counter()
        cutoff = 2700 if kind == "half_time" else 1e9
        chosen = []
        for s in self.storylines.values():
            if s["status"] != "approved" or s["minute"] * 60 > cutoff + 900:
                continue
            if s["market"] == market:
                chosen.append(s)
            elif s["market"] == "world" and (s["insight_id"], market) not in self.by_insight:
                chosen.append({**s, "not_localised": market != "world"})
        r = {x["insight_id"]: x["score"] for x in self.ranking(market, "full")}
        chosen.sort(key=lambda s: -r.get(s["insight_id"], 0))
        sc = self.score()
        h, a = self.names["HOME"], self.names["AWAY"]
        label = "Half-time" if kind == "half_time" else "Full-time"
        scoreline = f"{h} {sc['HOME']}–{sc['AWAY']} {a}"
        items = []
        for s in chosen:
            text = " ".join(seg["text"] for seg in s["segments"] if not seg.get("struck"))
            items.append({
                "storyline_id": s["storyline_id"], "headline": s["headline"], "script": text,
                "graphic_payload": {**s["graphic"], "headline": s["headline"], "verified": True,
                                    "storyline_id": s["storyline_id"]},
                "clip_cue": s["clip_cue"], "market": s["market"], "not_localised": s.get("not_localised", False),
            })
        recap = f"{label}: {scoreline}." + ("" if not items else " " + " ".join(f"{i['headline']}." for i in items[:3]))
        social = (f"{label} | {scoreline}" + (f" | {items[0]['headline']}" if items else ""))
        cov = self.coverage(2760 if kind == "half_time" else 1e9, market)
        stale = [s["headline"] for s in self.storylines.values() if s["status"] == "needs_correction"]
        body = {"kind": kind, "market": market, "label": label, "scoreline": scoreline,
                "minute": int(self.now_clock // 60) + 1, "items": items, "recap": recap, "social": social,
                "coverage": cov, "needs_correction": stale,
                "note": None if items else "No approved storylines yet. Approve stories to fill the package."}
        built_ms = round((time.perf_counter() - t0) * 1000, 1)
        body["built_ms"] = built_ms
        pid = f"{self.match_id}-p{next(_pid):03d}"
        body["package_id"] = pid
        body["rights"] = governance.rights_envelope(self, body)
        self.store.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,?)",
                           (pid, self.match_id, kind, market, json.dumps(body, default=str), built_ms, time.time()))
        self.packages.append(body)
        self.publish({"type": "package", "package": body})
        return body

    def result_check(self) -> Optional[dict]:
        """For real matches: does the score built from events equal the official result?"""
        off = self.meta.get("official_result")
        if not off:
            return None
        finished = any(e["type"] == "period_end" and e["period"] == 2 for e in self.detectors.events)
        engine = self.score()
        return {"official": off, "engine": engine, "finished": finished,
                "match": finished and engine == off}

    # ------------------------------------------------------------------ metrics
    def funnel(self) -> dict[str, int]:
        world = [s for s in self.storylines.values() if s["market"] == "world"]
        return {
            "insights": len(self.insights),
            "storylines": len(world),
            "verified": sum(1 for s in world if s["overall"] == verifier.VERIFIED),
            "flagged": sum(1 for s in world if s["overall"] == verifier.NEEDS_HUMAN),
            "blocked": sum(1 for s in world if s["overall"] == verifier.BLOCKED),
            "approved": sum(1 for s in self.storylines.values() if s["status"] == "approved"),
            "needs_correction": sum(1 for s in self.storylines.values() if s["status"] == "needs_correction"),
            "rejected": sum(1 for s in self.storylines.values() if s["status"] == "rejected"),
        }

    # ------------------------------------------------------------------ replay
    async def replay(self, events: list[Event], speed: float = 10.0, start_t: float = 0.0,
                     corrections: Optional[list[dict]] = None) -> None:
        """Stream events at `speed`; scheduled feed corrections are applied when the replay reaches them."""
        self.replay_speed = speed
        pending = sorted([c for c in (corrections or []) if not c.get("applied")], key=lambda c: c["at_t"])
        prev = None
        for e in events:
            while pending and pending[0]["at_t"] <= e.t:
                c = pending.pop(0)
                c["applied"] = True
                await self.drain()   # let the newsroom finish what it was doing before the data changes
                self.correct_event(c["event_id"], c["changes"], c["reason"])
            if e.t >= start_t and prev is not None:
                gap = (e.t - prev) / max(self.replay_speed, 0.1)
                await asyncio.sleep(min(gap, 3.0))
            prev = e.t
            await self.ingest(e)
            await asyncio.sleep(0)   # give the newsroom a turn
        for c in pending:
            c["applied"] = True
            self.correct_event(c["event_id"], c["changes"], c["reason"])
        await self.drain()
        self.publish({"type": "replay_done", "result_check": self.result_check()})

    async def drain(self) -> None:
        while self.pending:
            await asyncio.gather(*list(self.pending), return_exceptions=True)
