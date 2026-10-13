"""Fixed benchmark suite.

- 5 synthetic matches (seeds 1-5; detector thresholds were tuned on seeds 100-111 only)
- planted patterns per match (counterattacks, a momentum shift, a standout player) with ground truth
- labelled false claims injected into real storylines (wrong numbers, unsupported states, unclaimed
  sentences, number mismatches, judgement wording, inflated possession, wrong attribution)
- an independent re-check of every verified claim in plain Python (not the verifier's SQL)

Run:  python -m gallery.benchmark
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Optional

from . import espn, fixtures, statsbomb, synth, verifier
from .engine import MatchRuntime
from .store import Store

SEEDS = [1, 2, 3, 4, 5]
# Private validation runs only (ENABLE_VALIDATION_SOURCES=1): match ids come from .env, never from the repo.
REAL_MATCHES = [int(x) for x in os.getenv("VALIDATION_STATSBOMB_IDS", "").split(",") if x.strip()]
ESPN_MATCHES = [x.strip() for x in os.getenv("VALIDATION_ESPN_IDS", "").split(",") if x.strip()]
DETECTION_GRACE_S = 300  # a detection may come up to 5 minutes after a planted spell ends
KINDS = ["inflated_possession", "unsupported_state", "wrong_number", "number_mismatch", "unclaimed",
         "judgement_wording", "wrong_attribution"]


def _independent(claim: dict, evs: list[dict]) -> Any:
    """Re-compute a claim without SQL. Returns None if this re-check doesn't cover the metric."""
    m, p = claim["metric"], claim["params"]
    by_id = {e["event_id"]: e for e in evs}
    if m == "count":
        types = p["type"] if isinstance(p["type"], list) else [p["type"]]
        outs = p.get("outcome")
        outs = outs if isinstance(outs, list) or outs is None else [outs]
        return sum(1 for e in evs if e["team_id"] == p["team_id"] and e["type"] in types
                   and p["from_t"] <= e["t"] <= p["to_t"] and (outs is None or e["outcome"] in outs)
                   and (not p.get("player") or e["player"] == p["player"]))
    if m == "score":
        return sum(1 for e in evs if e["team_id"] == p["team_id"] and e["t"] <= p["at_t"]
                   and ((e["type"] == "shot" and e["outcome"] == "goal") or e["type"] == "own_goal"))
    if m == "seconds_between":
        return round(by_id[p["to_event"]]["t"] - by_id[p["from_event"]]["t"])
    if m == "attribution":
        return by_id[p["event_id"]]["player"]
    if m == "shot_outcome":
        return by_id[p["event_id"]]["outcome"]
    if m == "shot_speed":
        return by_id[p["event_id"]]["speed"]
    if m == "possession_pct":
        ps = [e for e in evs if e["type"] == "pass" and p["from_t"] <= e["t"] <= p["to_t"]]
        return round(100 * sum(1 for e in ps if e["team_id"] == p["team_id"]) / len(ps)) if ps else None
    return None


async def run_one(seed: int) -> dict:
    m = synth.generate(match_id=f"bench{seed}", seed=seed)
    store = Store()
    rt = MatchRuntime(store, m.match_id, m.home_name, m.away_name, "replay", use_model=False)
    for e in m.events:
        await rt.ingest(e)
    await rt.drain()

    # detection vs planted truth
    found = []
    for tr in m.truth:
        hit = next((i for i in rt.insights if i["detector"] == tr["pattern"] and i["team_id"] == tr["team_id"]
                    and tr["t_from"] - 1 <= i["t"] <= tr["t_to"] + DETECTION_GRACE_S
                    and (not tr["player"] or tr["player"] in i["players"])), None)
        found.append({**tr, "detected": bool(hit), "insight_id": hit["insight_id"] if hit else None,
                      "detected_minute": hit["minute"] if hit else None})
    planted_ids = {f["insight_id"] for f in found if f["insight_id"]}
    extra = [i for i in rt.insights if i["insight_id"] not in planted_ids
             and i["detector"] in ("counterattack", "momentum_shift", "standout")]

    # independent re-check of every verified claim
    evs = store.events(m.match_id)
    checked = escapes = 0
    for sl in rt.storylines.values():
        for seg in sl["segments"]:
            if seg["verdict"] != verifier.VERIFIED:
                continue
            for c in seg["checks"]:
                truth = _independent(c, evs)
                if truth is None:
                    continue
                checked += 1
                ok = (str(truth).lower() == str(c["value"]).lower()) if isinstance(c["value"], str) else \
                    abs(float(truth) - float(c["value"])) <= (1.0 if c["metric"] in ("possession_pct", "seconds_between") else 0)
                escapes += 0 if ok else 1

    # labelled false claims
    false_results = []
    world = [s for s in rt.storylines.values() if s["market"] == "world"]
    goals = [s for s in world if s["detector"] == "goal"]
    others = [s for s in world if s["detector"] != "goal"]
    for kind in KINDS:
        pool = goals if kind == "wrong_attribution" else others
        if not pool:
            continue
        sl = pool[len(false_results) % len(pool)]
        sl = rt.inject_false(sl["storyline_id"], kind)
        seg = sl["segments"][0]
        false_results.append({"kind": kind, "text": seg["text"], "verdict": seg["verdict"], "reason": seg["reason"],
                              "caught": seg["verdict"] != verifier.VERIFIED})

    # packages
    for s in world[:3]:
        if s["overall"] == verifier.VERIFIED:
            rt.decide(s["storyline_id"], "approve")
    t0 = time.perf_counter()
    rt.build_package("full_time", "world")
    pkg_ms = (time.perf_counter() - t0) * 1000

    return {
        "seed": seed, "patterns": found, "extra_detections": len(extra),
        "verified_claims_rechecked": checked, "escapes": escapes, "false_claims": false_results,
        "latency_ms": [s["latency_ms"] for s in world], "package_ms": round(pkg_ms, 2),
        "funnel": rt.funnel(),
    }


async def run_espn(espn_id: str) -> dict:
    """Recent real match from the ESPN feed (bundled copy if offline)."""
    mid = f"benchespn{espn_id}"
    d = espn.parse(espn.summary(espn_id), mid)
    m = d["meta"]
    store = Store()
    rt = MatchRuntime(store, mid, m["home"], m["away"], "espn", use_model=False, caps=d["caps"])
    rt.meta = {k: v for k, v in m.items() if k != "team_stats"}
    rt.set_team_stats(m.get("team_stats") or {}, "espn")
    for e in d["events"]:
        await rt.ingest(e)
    await rt.drain()
    world = [x for x in rt.storylines.values() if x["market"] == "world"]
    rc = rt.result_check()
    goals = [i for i in rt.insights if i["detector"] == "goal"]
    return {"source": "ESPN match feed", "espn_id": espn_id, "date": m["date"],
            "official": f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}",
            "engine_score": rc["engine"], "result_matches": rc["match"],
            "goals_official": m["home_score"] + m["away_score"], "goals_detected": len(goals),
            "scorers": [f"{(i['players'] or ['?'])[0]} {i['minute']}'" for i in goals],
            "turning_points": [" ".join(g["text"] for g in x["segments"]) for x in world
                               if x["detector"] == "turning_point" and x["graphic"]["fields"].get("minute", 0) >= 90][:1],
            "storylines": len(world), "verified_storylines": sum(1 for x in world if x["overall"] == verifier.VERIFIED)}


async def run_real(sb_id: int) -> dict:
    """Replay a real match and check The Gallery against the official result."""
    mid = f"benchreal{sb_id}"
    d = statsbomb.load(sb_id, match_id=mid)
    m = d["meta"]
    store = Store()
    rt = MatchRuntime(store, mid, m["home"], m["away"], "real", d["market_overrides"], use_model=False)
    rt.meta = m
    for e in d["events"]:
        await rt.ingest(e)
    await rt.drain()
    evs = store.events(mid)
    official_goals = m["home_score"] + m["away_score"]
    goal_insights = [i for i in rt.insights if i["detector"] == "goal"]
    checked = escapes = 0
    for sl in rt.storylines.values():
        for seg in sl["segments"]:
            if seg["verdict"] != verifier.VERIFIED:
                continue
            for c in seg["checks"]:
                truth = _independent(c, evs)
                if truth is None:
                    continue
                checked += 1
                ok = (str(truth).lower() == str(c["value"]).lower()) if isinstance(c["value"], str) else \
                    abs(float(truth) - float(c["value"])) <= (1.0 if c["metric"] in ("possession_pct", "seconds_between") else 0)
                escapes += 0 if ok else 1
    false_results = []
    world = [x for x in rt.storylines.values() if x["market"] == "world"]
    verified_before = sum(1 for x in world if x["overall"] == verifier.VERIFIED)  # before injecting tests
    goals = [x for x in world if x["detector"] == "goal"]
    others = [x for x in world if x["detector"] != "goal"] or goals
    for kind in KINDS:
        pool = goals if kind == "wrong_attribution" else others
        sl = rt.inject_false(pool[len(false_results) % len(pool)]["storyline_id"], kind)
        seg = sl["segments"][0]
        false_results.append({"kind": kind, "text": seg["text"], "verdict": seg["verdict"], "reason": seg["reason"],
                              "caught": seg["verdict"] != verifier.VERIFIED})
    rc = rt.result_check()
    return {
        "sb_match_id": sb_id, "date": m["date"], "fixture": f"{m['home']} v {m['away']}",
        "official": f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}",
        "engine_score": rc["engine"], "result_matches": rc["match"],
        "goals_official": official_goals, "goals_detected": len(goal_insights),
        "scorers": [f"{(i['players'] or ['?'])[0]}{' (og)' if i['metrics'].get('own_goal') else ''} {i['minute']}'"
                    for i in goal_insights],
        "storylines": len(world),
        "verified_storylines": verified_before,
        "claims_rechecked": checked, "escapes": escapes,
        "false_claims_caught": sum(f["caught"] for f in false_results), "false_claims": len(false_results),
        "source": "StatsBomb Open Data",
    }


def _approve_verified(rt: MatchRuntime, approved_at: dict) -> None:
    """Simulated producer: approves every verified draft (and nothing else) as soon as it appears."""
    for sid, sl in list(rt.storylines.items()):
        if sl["status"] == "drafted" and sl["overall"] == verifier.VERIFIED and sl["market"] == "world":
            rt.decide(sid, "approve", actor="benchmark-producer")
            approved_at[sid] = time.perf_counter()


async def run_fixture(fid: str) -> dict:
    """Full agent workflow on a synthetic fixture: detection, verification, coverage, corrections, packaging."""
    mid = f"benchfx{fid}"
    f = fixtures.build(fid, mid)
    store = Store()
    rt = MatchRuntime(store, mid, f["home"], f["away"], "fixture", use_model=False)
    rt.red_team_budget = f["red_team"]
    approved_at: dict = {}
    pending = list(f["corrections"])
    for e in f["events"]:
        while pending and pending[0]["at_t"] <= e.t:
            c = pending.pop(0)
            await rt.drain()
            _approve_verified(rt, approved_at)
            rt.correct_event(c["event_id"], c["changes"], c["reason"])
            await rt.drain()
        await rt.ingest(e)
        await rt.drain()
        _approve_verified(rt, approved_at)
    await rt.drain()
    _approve_verified(rt, approved_at)
    # detection vs planted truth
    found, in_window, correct_in_window = [], 0, 0
    for tr in f["truth"]:
        lo, hi = tr["t_from"] - 1, tr["t_to"] + DETECTION_GRACE_S
        dets = [i for i in rt.insights if i["detector"] == tr["pattern"] and lo <= i["t"] <= hi]
        hit = any(i["team_id"] == tr["team_id"] and (not tr["player"] or tr["player"] in i["players"]) for i in dets)
        found.append({"pattern": tr["pattern"], "label": tr.get("label"), "minute": int(tr["t_from"] // 60) + 1,
                      "detected": hit})
        in_window += len(dets)
        correct_in_window += sum(1 for i in dets if i["team_id"] == tr["team_id"])
    # verdict accuracy: verified claims re-checked independently; red-team sentences must not be verified
    evs = store.events(mid)
    checked = escapes = 0
    for sl in rt.storylines.values():
        for seg in sl["segments"]:
            if seg["verdict"] != verifier.VERIFIED:
                continue
            for c in seg.get("checks", []):
                truth = _independent(c, evs)
                if truth is None:
                    continue
                checked += 1
                ok = (str(truth).lower() == str(c["value"]).lower()) if isinstance(c["value"], str) else \
                    abs(float(truth) - float(c["value"])) <= (1.0 if c["metric"] in ("possession_pct", "seconds_between") else 0)
                escapes += 0 if ok else 1
    red_team = [h for sl in rt.storylines.values() if sl.get("red_team") for h in sl.get("history", [])]
    red_caught = sum(1 for sl in rt.storylines.values() if sl.get("red_team")
                     and sl["history"] and sl["history"][0]["verdict"] != verifier.VERIFIED)
    red_total = sum(1 for sl in rt.storylines.values() if sl.get("red_team"))
    revised_ok = sum(1 for sl in rt.storylines.values() if sl.get("red_team") and sl["overall"] == verifier.VERIFIED)
    # coverage after the Desk
    cov = rt.coverage()
    # corrections
    corr = []
    for c in f["corrections"]:
        log = next((x for x in rt.correction_log if x["event_id"] == c["event_id"]), None)
        flagged = log["flagged"] + log.get("stale_drafts", []) if log else []
        replacements = [s for s in rt.storylines.values() if s.get("supersedes") in flagged]
        corr.append({"reason": c["reason"], "affected_flagged": len(flagged),
                     "replacement_drafted": bool(replacements),
                     "replacement_verified": all(s["overall"] == verifier.VERIFIED for s in replacements) and bool(replacements),
                     "replacement_text": " ".join(g["text"] for g in replacements[0]["segments"]) if replacements else None})
    t0 = time.perf_counter()
    pkg = rt.build_package("full_time", "world")
    pkg_ms = (time.perf_counter() - t0) * 1000
    lat = [s["latency_ms"] for s in rt.storylines.values() if s["market"] == "world"]
    trace = rt.trace.recent(10000)
    return {
        "fixture": fid, "title": f["title"], "teams": f"{f['home']} v {f['away']}",
        "final_score": rt.score(), "expected_score": f["final_score"], "score_matches": rt.score() == f["final_score"],
        "patterns": found, "pattern_precision": {"in_window": in_window, "correct_team": correct_in_window},
        "obligations": len(f["obligations"]), "covered": len(cov["moments"]) - len(cov["missing"]),
        "missing": [f"{m['label']} {m['minute']}'" for m in cov["missing"]],
        "turning_points": sum(1 for s in rt.storylines.values() if s["detector"] == "turning_point"),
        "expected_turning_points": len(f["expected_turning_points"]),
        "claims_rechecked": checked, "escapes": escapes,
        "red_team": {"drafts": red_total, "caught_on_first_draft": red_caught, "revised_to_verified": revised_ok,
                     "verdicts": len(red_team)},
        "corrections": corr,
        "latency_ms": lat, "package_ms": round(pkg_ms, 2), "package_items": len(pkg["items"]),
        "funnel": rt.funnel(),
        "agents": {a: sum(1 for t in trace if t["agent"] == a) for a in sorted({t["agent"] for t in trace})},
        "held_by_editor": sum(1 for t in trace if t["status"] == "held"),
        "revisions": sum(s.get("revisions", 0) for s in rt.storylines.values()),
    }


async def run(seeds: list[int] = SEEDS, include_validation: Optional[bool] = None) -> dict:
    t0 = time.time()
    if include_validation is None:
        include_validation = os.getenv("ENABLE_VALIDATION_SOURCES", "").strip().lower() in ("1", "true", "yes", "on")
    real = []
    recent = []
    if not include_validation:
        REAL, RECENT = [], []
    else:
        REAL, RECENT = REAL_MATCHES, ESPN_MATCHES
    for sb in REAL:
        try:
            real.append(await run_real(sb))
        except Exception as exc:  # offline: report it rather than hide it
            real.append({"sb_match_id": sb, "error": str(exc)[:200]})
    for eid in RECENT:
        try:
            recent.append(await run_espn(eid))
        except Exception as exc:
            recent.append({"espn_id": eid, "error": str(exc)[:200]})
    per = [await run_one(s) for s in seeds]
    fx = [await run_fixture(fid) for fid in fixtures.FIXTURES]
    patterns = [p for r in per for p in r["patterns"]]
    by_type: dict[str, dict] = {}
    for p in patterns:
        d = by_type.setdefault(p["pattern"], {"planted": 0, "found": 0})
        d["planted"] += 1
        d["found"] += int(p["detected"])
    false = [f for r in per for f in r["false_claims"]]
    fk: dict[str, dict] = {}
    for f in false:
        d = fk.setdefault(f["kind"], {"injected": 0, "caught": 0, "blocked": 0, "flagged": 0})
        d["injected"] += 1
        d["caught"] += int(f["caught"])
        d["blocked"] += int(f["verdict"] == verifier.BLOCKED)
        d["flagged"] += int(f["verdict"] == verifier.NEEDS_HUMAN)
    for r in fx:
        for p in r["patterns"]:
            d = by_type.setdefault(p["pattern"], {"planted": 0, "found": 0})
            d["planted"] += 1
            d["found"] += int(p["detected"])
    patterns_all = patterns + [p for r in fx for p in r["patterns"]]
    lat = [x for r in per for x in r["latency_ms"]] + [x for r in fx for x in r["latency_ms"]]
    checked = sum(r["verified_claims_rechecked"] for r in per) + sum(r["claims_rechecked"] for r in fx)
    escapes = sum(r["escapes"] for r in per) + sum(r["escapes"] for r in fx)
    red = {k: sum(r["red_team"][k] for r in fx) for k in ("drafts", "caught_on_first_draft", "revised_to_verified")}
    obligations = sum(r["obligations"] for r in fx)
    covered = sum(r["covered"] for r in fx)
    corr = [c for r in fx for c in r["corrections"]]
    in_window = sum(r["pattern_precision"]["in_window"] for r in fx)
    correct_window = sum(r["pattern_precision"]["correct_team"] for r in fx)
    false_total = len(false) + red["drafts"]
    false_caught = sum(f["caught"] for f in false) + red["caught_on_first_draft"]
    verdicts_total = checked + false_total
    verdicts_correct = (checked - escapes) + false_caught
    return {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "real_matches": real,
        "recent_matches": recent,
        "seeds": seeds, "tuning_seeds": "100-111 (excluded from the benchmark)",
        "writer": "template (deterministic)",
        "fixtures": fx,
        "agent_workflow": {
            "pattern_recall": {"planted": len(patterns_all), "found": sum(p["detected"] for p in patterns_all)},
            "pattern_precision": {"detections_in_planted_windows": in_window, "right_team": correct_window},
            "claim_verdict_accuracy": {"correct": verdicts_correct, "total": verdicts_total},
            "false_claim_block_rate": {"caught": false_caught, "planted": false_total},
            "unsupported_claim_escape_rate": {"escapes": escapes, "verified_claims": checked},
            "coverage_rate": {"covered": covered, "obligations": obligations},
            "correction_detection_rate": {"detected": sum(1 for c in corr if c["affected_flagged"] > 0),
                                          "changed_facts": len(corr),
                                          "replacements_verified": sum(1 for c in corr if c["replacement_verified"])},
            "red_team_writer": red,
            "median_time_to_verified_storyline_ms": round(statistics.median(lat), 1) if lat else None,
            "median_time_to_package_ms": round(statistics.median(r["package_ms"] for r in fx), 2),
            "turning_points": {"found": sum(min(r["turning_points"], 1) for r in fx if r["expected_turning_points"]),
                               "expected": sum(1 for r in fx if r["expected_turning_points"])},
            "results_match_fixture": sum(1 for r in fx if r["score_matches"]),
        },
        "detection": {"planted": len(patterns), "found": sum(p["detected"] for p in patterns),
                      "by_type": by_type,
                      "extra_detections": sum(r["extra_detections"] for r in per),
                      "note": "Extra detections are patterns that also occur naturally in the synthetic matches; "
                              "each meets the detector definition, verified by the claim re-check below."},
        "false_claims": {"injected": len(false), "caught": sum(f["caught"] for f in false),
                         "blocked": sum(f["verdict"] == verifier.BLOCKED for f in false),
                         "flagged": sum(f["verdict"] == verifier.NEEDS_HUMAN for f in false),
                         "by_kind": fk, "examples": false[:7]},
        "verified_claims": {"rechecked_independently": checked, "escapes": escapes,
                            "escape_rate": round(escapes / checked, 4) if checked else None},
        "latency": {"median_storyline_ms": round(statistics.median(lat), 1) if lat else None,
                    "p95_storyline_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1) if lat else None,
                    "median_package_ms": round(statistics.median(r["package_ms"] for r in per), 2)},
        "funnel": {k: sum(r["funnel"][k] for r in per) for k in per[0]["funnel"]},
        "matches": per,
        "duration_s": round(time.time() - t0, 2),
    }


def main() -> None:
    import sys
    quick = "--quick" in sys.argv
    res = asyncio.run(run(seeds=SEEDS[:2] if quick else SEEDS))
    if quick:
        a = res["agent_workflow"]
        print("QUICK", json.dumps(a["coverage_rate"]), json.dumps(a["unsupported_claim_escape_rate"]),
              json.dumps(a["correction_detection_rate"]))
        assert a["unsupported_claim_escape_rate"]["escapes"] == 0, "an unsupported claim was marked verified"
        assert a["coverage_rate"]["covered"] == a["coverage_rate"]["obligations"], "a key moment was not covered"
        assert a["correction_detection_rate"]["detected"] == a["correction_detection_rate"]["changed_facts"]
        return
    out = Path(__file__).resolve().parents[1] / "benchmark_results.json"
    out.write_text(json.dumps(res, indent=2, default=str))
    d, f, v = res["detection"], res["false_claims"], res["verified_claims"]
    for r in res.get("recent_matches", []):
        if "error" in r:
            print(f"Recent match {r['espn_id']}: not available ({r['error']})")
            continue
        print(f"RECENT {r['date']} {r['official']}: engine {r['engine_score']} -> "
              f"{'MATCHES official result' if r['result_matches'] else 'MISMATCH'}; goals {r['goals_detected']}/"
              f"{r['goals_official']}; storylines verified {r['verified_storylines']}/{r['storylines']}")
    for r in res["real_matches"]:
        if "error" in r:
            print(f"Real match {r['sb_match_id']}: not available ({r['error']})")
            continue
        print(f"REAL {r['date']} {r['official']}: engine {r['engine_score']} -> "
              f"{'MATCHES official result' if r['result_matches'] else 'MISMATCH'}; goals {r['goals_detected']}/"
              f"{r['goals_official']} {r['scorers']}; storylines verified {r['verified_storylines']}/{r['storylines']}; "
              f"re-check escapes {r['escapes']}/{r['claims_rechecked']}; false claims caught "
              f"{r['false_claims_caught']}/{r['false_claims']}")
    a = res["agent_workflow"]
    for r in res["fixtures"]:
        print(f"FIXTURE {r['fixture']} {r['title']} ({r['teams']}): score {r['final_score']} "
              f"{'= fixture' if r['score_matches'] else '!= fixture'}; covered {r['covered']}/{r['obligations']}; "
              f"turning points {r['turning_points']}; red-team {r['red_team']}; corrections {r['corrections']}; "
              f"held {r['held_by_editor']}; revisions {r['revisions']}")
    for k, val in a.items():
        print(f"  {k}: {val}")
    print(f"Planted patterns found: {d['found']}/{d['planted']}  {d['by_type']}")
    print(f"False claims caught:    {f['caught']}/{f['injected']} (blocked {f['blocked']}, flagged {f['flagged']})")
    print(f"Verified claims re-checked independently: {v['rechecked_independently']}, escapes: {v['escapes']}")
    print(f"Median storyline latency: {res['latency']['median_storyline_ms']} ms; "
          f"package build: {res['latency']['median_package_ms']} ms")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
