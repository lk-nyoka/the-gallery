"""Agent workflow tests: contracts, the verification tool, each agent, and the workflow end to end.

All tests run with the template provider (no model), so they are deterministic and run in CI.
"""

import asyncio

import pytest
from pydantic import ValidationError

from gallery import fixtures, verifier
from gallery.agents import workflow
from gallery.agents.contracts import Claim, Segment, StoryCommission, StoryDraft
from gallery.engine import MatchRuntime
from gallery.store import Store


def play(fid: str, approve: bool = False, stop_minute: float | None = None, red_team: bool = True):
    """Replay a fixture through the runtime; optionally approve every verified story as it lands."""
    mid = f"t{fid}"
    f = fixtures.build(fid, mid)
    rt = MatchRuntime(Store(), mid, f["home"], f["away"], "fixture", use_model=False)
    rt.red_team_budget = f["red_team"] if red_team else 0
    pending = list(f["corrections"])

    def approve_all():
        for sl in list(rt.storylines.values()):
            if sl["status"] == "drafted" and sl["overall"] == verifier.VERIFIED:
                rt.decide(sl["storyline_id"], "approve")

    async def go():
        for e in f["events"]:
            if stop_minute is not None and e.t > stop_minute * 60:
                break
            while pending and pending[0]["at_t"] <= e.t:
                c = pending.pop(0)
                await rt.drain()
                if approve:
                    approve_all()
                rt.correct_event(c["event_id"], c["changes"], c["reason"])
                await rt.drain()
            await rt.ingest(e)
            await rt.drain()
            if approve:
                approve_all()
        await rt.drain()
    asyncio.run(go())
    return rt, f


@pytest.fixture(scope="module")
def fx_a():
    return play("A")


@pytest.fixture(scope="module")
def fx_c():
    return play("C", approve=True)


def commission(rt, insight_id, **kw):
    return StoryCommission(commission_id=kw.pop("commission_id", "test-c1"), match_id=rt.match_id,
                           phase=kw.pop("phase", "live"), insight_id=insight_id, brief="test brief",
                           commissioned_by=kw.pop("commissioned_by", "producer"), **kw)


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- contracts
def test_draft_without_sentences_is_rejected_by_the_schema():
    with pytest.raises(ValidationError):
        StoryDraft(draft_id="d", commission_id="c", headline="h", segments=[])


def test_commission_rejects_unknown_phase_and_agent():
    with pytest.raises(ValidationError):
        StoryCommission(commission_id="c", match_id="m", phase="whenever", insight_id="i", brief="b",
                        commissioned_by="editor_in_chief")
    with pytest.raises(ValidationError):
        StoryCommission(commission_id="c", match_id="m", phase="live", insight_id="i", brief="b",
                        commissioned_by="the_model")


def test_claim_operator_is_constrained():
    with pytest.raises(ValidationError):
        Claim(metric="count", params={}, op="about", value=3)


# ---------------------------------------------------------------- verification tool
def _template_draft(rt, insight_id):
    c = commission(rt, insight_id)
    b = run_async(rt.analyst.brief(c))
    return run_async(rt.writer_agent.draft(c, b, version=1))


def _some_insight(rt, detector="counterattack"):
    return next(i for i in rt.insights if i["detector"] == detector)


def test_verification_tool_verifies_a_true_draft(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    assert rt.toolbox.submit_story_for_verification(d).verdict == verifier.VERIFIED


def test_verification_tool_blocks_a_wrong_number(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    seg = next(s for s in d.segments if any(isinstance(c.value, int) and not isinstance(c.value, bool)
                                            for c in s.claims))
    cl = next(c for c in seg.claims if isinstance(c.value, int) and not isinstance(c.value, bool))
    cl.value += 7
    res = rt.toolbox.submit_story_for_verification(d)
    assert res.verdict == verifier.BLOCKED and res.rejected_claims and res.revision_guidance


def test_verification_tool_blocks_a_sentence_with_no_claim(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    d.segments.append(Segment(text="They completed 31 passes in that spell.", claims=[]))
    assert rt.toolbox.submit_story_for_verification(d).verdict == verifier.BLOCKED


def test_verification_tool_blocks_unsupported_physical_state(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    d.segments[0] = Segment(text=d.segments[0].text.rstrip(".") + ", and the defence looks exhausted.",
                            claims=d.segments[0].claims)
    assert rt.toolbox.submit_story_for_verification(d).verdict == verifier.BLOCKED


def test_verification_tool_blocks_template_leftovers(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    d.segments[0] = Segment(text="{team} scored {n} goals.", claims=d.segments[0].claims)
    assert rt.toolbox.submit_story_for_verification(d).verdict == verifier.BLOCKED


# ---------------------------------------------------------------- Editor-in-Chief
def test_editor_only_commissions_detected_insights_and_always_covers_obligations(fx_a):
    rt, _ = fx_a
    ids = {i["insight_id"] for i in rt.insights}
    stories = list(rt.storylines.values())
    assert stories and all(s["insight_id"] in ids for s in stories)
    by_editor = {s["insight_id"] for s in stories if s["commissioned_by"] == "editor_in_chief"}
    for i in rt.insights:
        if i["detector"] in ("goal", "red_card", "turning_point"):
            assert i["insight_id"] in by_editor, i["detector"]
    held = [t for t in rt.trace.recent(1000) if t["agent"] == "Editor-in-Chief" and t["status"] == "held"]
    for h in held:
        assert h["output_summary"]           # every held story says why


# ---------------------------------------------------------------- Analyst
def test_analyst_brief_cites_events_that_exist_and_uses_read_only_tools(fx_a):
    rt, _ = fx_a
    ins = _some_insight(rt)
    b = run_async(rt.analyst.brief(commission(rt, ins["insight_id"])))
    known = {e["event_id"] for e in rt.store.events(rt.match_id)}
    assert b.evidence_event_ids and set(b.evidence_event_ids) <= known
    assert b.facts and any(t.startswith("get_insight_evidence") for t in b.tool_calls)
    assert not any(t.startswith("submit") for t in b.tool_calls)


# ---------------------------------------------------------------- Writer + revision loop
def test_red_team_sentence_is_caught_then_revised(fx_c):
    rt, f = fx_c
    red = [s for s in rt.storylines.values() if s.get("red_team")]
    assert len(red) == f["red_team"]
    for s in red:
        assert s["history"][0]["verdict"] == verifier.BLOCKED     # first draft carried the false sentence
        assert s["overall"] == verifier.VERIFIED                   # the revision passes
        assert s["version"] >= 2 and not any(seg.get("injected") for seg in s["segments"])


def test_writer_gives_up_after_max_revisions():
    t = workflow.Task(commission=StoryCommission(commission_id="c", match_id="m", phase="live", insight_id="i",
                                                 brief="b", commissioned_by="producer"),
                      brief=None, started=0.0, version=workflow.MAX_REVISIONS + 1)  # type: ignore[arg-type]
    t.result = type("R", (), {"verdict": "blocked"})()
    assert not t.needs_revision


# ---------------------------------------------------------------- Desk (coverage)
def test_desk_commissions_uncovered_red_card():
    rt, _ = play("A", stop_minute=66, red_team=False)
    red = next(i for i in rt.insights if i["detector"] == "red_card")
    # simulate the Editor having missed it: drop the story and its commission memory
    for sid, s in list(rt.storylines.items()):
        if s["insight_id"] == red["insight_id"]:
            del rt.storylines[sid]
            rt.by_insight.pop((red["insight_id"], "world"), None)
    commissions = run_async(rt.desk.review("full_time"))
    assert any(c.insight_id == red["insight_id"] and c.commissioned_by == "desk" for c in commissions)


def test_full_match_is_fully_covered(fx_c):
    rt, f = fx_c
    cov = rt.coverage()
    assert cov["complete"] and len(cov["moments"]) == len(f["obligations"])


# ---------------------------------------------------------------- Corrections (fixture B)
def test_correction_supersedes_the_old_story_after_reapproval():
    rt, f = play("B", approve=False)
    c = f["corrections"][0]
    log = next(x for x in rt.correction_log if x["event_id"] == c["event_id"])
    flagged = log["flagged"] + log.get("stale_drafts", [])
    assert flagged
    new = [s for s in rt.storylines.values() if s.get("supersedes") in flagged]
    assert new and all(s["commissioned_by"] == "corrections" for s in new)
    assert all(s["overall"] == verifier.VERIFIED for s in new)
    assert any("Moreau" in seg["text"] for s in new for seg in s["segments"])


def test_correction_flow_with_an_approved_story():
    rt, f = play("B", approve=True)
    c = f["corrections"][0]
    log = next(x for x in rt.correction_log if x["event_id"] == c["event_id"])
    assert log["flagged"], "the approved 28' goal story should be flagged"
    old = rt.storylines[log["flagged"][0]]
    new = next(s for s in rt.storylines.values() if s.get("supersedes") == old["storyline_id"])
    assert new["status"] == "approved"                  # approved by the simulated producer
    assert old["status"] == "superseded" and old["superseded_by"] == new["storyline_id"]
    with pytest.raises(ValueError):
        rt.decide(old["storyline_id"], "approve")       # the wrong version can't go back on air
    actions = [a["action"] for a in rt.audit(old["storyline_id"])]
    assert "approve" in actions and "superseded" in actions
    assert any(a["action"] == "approve" for a in rt.audit(new["storyline_id"]))


# ---------------------------------------------------------------- Localiser
def test_localiser_produces_three_market_variants(fx_a):
    rt, _ = fx_a
    for market in ("br", "za"):
        out = run_async(rt.localise(market, top_n=3, phase="full"))
        assert len(out) == 3
        assert all(s["market"] == market and s["base_storyline_id"] for s in out)
        # without a model the text stays English and says so; it is still verified
        assert all(s["overall"] != verifier.BLOCKED for s in out)


# ---------------------------------------------------------------- workflow + trace
def test_workflow_trace_shows_every_agent(fx_c):
    rt, _ = fx_c
    agents = {t["agent"] for t in rt.trace.recent(5000)}
    assert {"Editor-in-Chief", "Analyst", "Writer", "Verifier", "Producer"} <= agents
    sl = next(s for s in rt.storylines.values() if s.get("red_team"))
    steps = [(t["agent"], t["status"]) for t in rt.trace.for_storyline(sl["storyline_id"])]
    assert ("Verifier", "rejected") in steps and ("Verifier", "succeeded") in steps
    assert ("Producer", "waiting") in steps and ("Producer", "succeeded") in steps


def test_fixture_results_match_their_scripts():
    for fid in fixtures.FIXTURES:
        rt, f = play(fid, red_team=False)
        assert rt.score() == f["final_score"], fid


# ---------------------------------------------------------------- model paths (fake providers)
class _FakeProvider:
    available = True
    kind = "fake"

    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, 0

    def label(self):
        return "fake-model"

    def uses(self, agent):
        return True

    def writer_scope(self):
        return "full"

    def describe(self):
        return {"provider": "fake", "status": "ready"}

    async def chat_json(self, messages, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model offline")
        return self.reply(messages) if callable(self.reply) else self.reply


def _story_with(provider, fid="A"):
    mid = f"m{fid}{id(provider)}"
    f = fixtures.build(fid, mid)
    rt = MatchRuntime(Store(), mid, f["home"], f["away"], "fixture", use_model=False)
    rt.red_team_budget = 0
    rt.provider = provider

    async def go():
        for e in f["events"]:
            await rt.ingest(e)
            await rt.drain()
            if any(s["detector"] == "counterattack" for s in rt.storylines.values()):
                break
    run_async(go())
    return rt


def test_model_failure_falls_back_to_templates_and_still_verifies():
    rt = _story_with(_FakeProvider(fail=True))
    assert rt.storylines
    assert all(s["overall"] == verifier.VERIFIED for s in rt.storylines.values())
    assert any(t["status"] == "retry" and "template" in t["action"] for t in rt.trace.recent(1000))


def test_hallucinating_model_can_never_get_a_story_verified():
    lie = {"headline": "Total control", "segments": [
        {"text": "They look exhausted after 9 brilliant passes.",
         "claims": [{"key": "passes", "metric": "count", "params": {"team_id": "HOME", "type": "pass"},
                     "op": "==", "value": 9}]}]}
    p = _FakeProvider(reply=lie)
    rt = _story_with(p)
    assert p.calls > 0
    for s in rt.storylines.values():
        assert not any("exhausted" in g["text"] for g in s["segments"] if g["verdict"] != verifier.BLOCKED)
        if s["writer"] == "fake-model":
            assert s["overall"] == verifier.BLOCKED
            with pytest.raises(ValueError):
                rt.decide(s["storyline_id"], "approve")
    # after two model attempts the last revision is deterministic, so the story still reaches the producer
    revised = [s for s in rt.storylines.values() if s["version"] == 3]
    assert revised and all(s["writer"] != "fake-model" for s in revised)


def test_true_claims_stapled_to_unrelated_wording_are_not_verified(fx_a):
    rt, _ = fx_a
    d = _template_draft(rt, _some_insight(rt)["insight_id"])
    claims = d.segments[0].claims
    d.segments = [Segment(text="They regained possession after losing the ball.", claims=claims),
                  Segment(text="That quick move led to the chance.", claims=claims)]
    res = rt.toolbox.submit_story_for_verification(d)
    assert [s["verdict"] for s in res.segment_results] == [verifier.NEEDS_HUMAN, verifier.NEEDS_HUMAN]


def test_reusing_a_match_id_after_restart_does_not_collide_in_the_trace():
    import itertools

    from gallery.agents import trace as trace_mod
    store = Store()
    rt = MatchRuntime(store, "same", "A FC", "B FC", "fixture", use_model=False)
    rt.trace.emit("Producer", "first run", "started")
    trace_mod._ids = itertools.count(1)          # a server restart resets the counter
    store.delete_match_data("same")
    rt2 = MatchRuntime(store, "same", "A FC", "B FC", "fixture", use_model=False)
    rt2.trace.emit("Producer", "second run", "started")
    assert [t["action"] for t in rt2.trace.recent()] == ["second run"]


# ---------------------------------------------------------------- personalisation
def test_viewer_modes_rank_differently_from_the_same_verified_stories(fx_a):
    from gallery import personalise
    rt, _ = fx_a
    base = rt.markets["world"]
    rt.markets["viewer"] = personalise.viewer_market(base, "casual")
    casual = [r["detector"] for r in rt.ranking("viewer", "full")]
    rt.markets["viewer"] = personalise.viewer_market(base, "analyst")
    analyst = [r["detector"] for r in rt.ranking("viewer", "full")]
    assert sorted(casual) == sorted(analyst)          # same candidates, nothing invented
    assert casual != analyst                          # different order
    assert casual.index("goal") < analyst.index("goal")


def test_favourite_player_ranks_their_stories_first(fx_a):
    from gallery import personalise
    rt, _ = fx_a
    star = next(i["players"][0] for i in rt.insights if i["detector"] == "standout")
    rt.markets["viewer"] = personalise.viewer_market(rt.markets["world"], "casual", player=star)
    top = rt.ranking("viewer", "full")[0]
    assert star in rt.insight(top["insight_id"])["players"]


def test_player_card_numbers_come_from_the_verifier_sql(fx_a):
    from gallery import personalise
    rt, _ = fx_a
    p = personalise.players(rt)[0]["player"]
    card = personalise.player_card(rt, p)
    assert card["found"] and card["stats"]
    for st in card["stats"]:
        assert st["sql"].startswith("SELECT")
    n = sum(1 for e in rt.store.events(rt.match_id) if e["player"] == p and e["type"] == "shot")
    assert next(st["value"] for st in card["stats"] if st["key"] == "shots") == n


# ---------------------------------------------------------------- governance
def test_readiness_goes_green_only_when_stories_coverage_and_corrections_are_clear():
    from gallery import governance
    rt, f = play("C", approve=False, red_team=False)
    before = governance.readiness(rt, "full_time")
    assert not before["go"] and before["score"] < 100
    for s in list(rt.storylines.values()):
        if s["status"] == "drafted" and s["overall"] == verifier.VERIFIED and s["market"] == "world":
            rt.decide(s["storyline_id"], "approve")
    after = governance.readiness(rt, "full_time")
    assert after["go"] and after["score"] > before["score"]


def test_packages_carry_a_rights_envelope(fx_a):
    rt, _ = fx_a
    pkg = rt.build_package("full_time", "world")
    env = pkg["rights"]
    assert env["package_id"] == pkg["package_id"] and env["expires_at"] > env["built_at"]
    assert set(env["storylines"]) == {i["storyline_id"] for i in pkg["items"]}


def test_correction_reports_its_downstream_impact():
    rt, f = play("B", approve=True)
    log = next(x for x in rt.correction_log if x["event_id"] == f["corrections"][0]["event_id"])
    imp = log["impact"]
    assert imp["approved_pulled"] == len(log["flagged"]) >= 1 and imp["graphics_invalidated"] >= 1


# ---------------------------------------------------------------- MCP server
def test_mcp_tools_expose_verified_read_only_intelligence(fx_a, monkeypatch):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from gallery import app as app_mod
    from gallery import mcp_server
    rt, _ = fx_a
    monkeypatch.setitem(app_mod.runtimes, rt.match_id, rt)
    client = TestClient(app_mod.app)

    def get(path, **params):
        r = client.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()
    monkeypatch.setattr(mcp_server, "_get", get)
    stories = mcp_server.get_verified_storylines(rt.match_id)
    assert stories and all(s["status"] not in ("superseded", "rejected") for s in stories)
    prov = mcp_server.get_storyline_provenance(stories[0]["storyline_id"])
    assert prov["sentences"][0]["claims"][0]["sql"].startswith("SELECT")
    assert "missing" in mcp_server.get_coverage_gaps(rt.match_id)
    tools = {t.name for t in __import__("asyncio").run(mcp_server.mcp.list_tools())}
    assert not any(w in name for name in tools for w in ("approve", "decide", "edit", "publish", "inject"))


def test_small_model_writes_headlines_only_and_the_verifier_checks_them():
    class Small(_FakeProvider):
        kind = "foundry_local"

        def writer_scope(self):
            return "headline"

        async def chat(self, messages, **kw):
            self.calls += 1
            return '**Headline:** "Riverside scored 47 goals!"' if self.calls % 2 else "Kovac red card changes everything"
    rt = _story_with(Small())
    model = [s for s in rt.storylines.values() if "headline" in s["writer"]]
    assert model
    for s in rt.storylines.values():
        if "47 goals" in s["headline"]:
            assert s["headline_verdict"] == verifier.BLOCKED     # invented number never verified


# ---------------------------------------------------------------- pass quality and speed
def test_ball_speed_and_pass_quality_are_verifiable(fx_a):
    rt, _ = fx_a
    shot = next(e for e in rt.store.events(rt.match_id) if e["type"] == "shot" and e["speed"])
    ok = verifier.check_claim(rt.store, rt.match_id, {"metric": "shot_speed", "params": {"event_id": shot["event_id"]},
                                                       "op": "==", "value": shot["speed"]})
    bad = verifier.check_claim(rt.store, rt.match_id, {"metric": "shot_speed", "params": {"event_id": shot["event_id"]},
                                                        "op": "==", "value": shot["speed"] + 20})
    assert ok.ok and not bad.ok
    acc = verifier.check_claim(rt.store, rt.match_id, {"metric": "pass_accuracy_pct", "op": ">=", "value": 0,
                                                        "params": {"team_id": "HOME", "from_t": 0, "to_t": 1e9}})
    passes = [e for e in rt.store.events(rt.match_id) if e["type"] == "pass" and e["team_id"] == "HOME"]
    expect = round(100 * sum(e["outcome"] == "complete" for e in passes) / len(passes))
    assert abs(acc.actual - expect) <= 1


def test_power_shot_story_verifies():
    from gallery.agents.contracts import StoryCommission
    rt, _ = play("A", red_team=False)
    ins = next(i for i in rt.insights if i["detector"] == "power_shot")
    c = StoryCommission(commission_id="ps1", match_id=rt.match_id, phase="live", insight_id=ins["insight_id"],
                        brief="power shot", commissioned_by="producer")
    b = run_async(rt.analyst.brief(c))
    d = run_async(rt.writer_agent.draft(c, b, version=1))
    assert "km/h" in d.headline
    assert rt.toolbox.submit_story_for_verification(d).verdict == verifier.VERIFIED


def test_editorial_policy_words_need_a_producer(fx_a):
    _, human = verifier.check_text("Northbridge were robbed by the referee.", [])
    assert human and "Editorial policy" in human
    _, human = verifier.check_text("The odds on a comeback are shortening.", [])
    assert human and "Editorial policy" in human


def test_overlay_feed_has_only_approved_stories_and_threshold_tags():
    from gallery import overlays
    rt, _ = play("A", approve=True, red_team=False)
    fd = overlays.feed(rt)
    stories = [c for c in fd["cues"] if c["kind"] == "story"]
    tags = [c for c in fd["cues"] if c["kind"] == "player_tag"]
    assert stories and all(rt.storylines[c["storyline_id"]]["status"] == "approved" for c in stories)
    assert tags and all(c["duration_s"] > 0 and c["player"] for c in tags)
    assert [c["start_t"] for c in fd["cues"]] == sorted(c["start_t"] for c in fd["cues"])


def test_public_demo_gives_each_visitor_their_own_matches(monkeypatch):
    from fastapi.testclient import TestClient

    from gallery import app as app_mod
    monkeypatch.setattr(app_mod, "PUBLIC_DEMO", True)
    monkeypatch.setattr(app_mod, "runtimes", {})
    c = TestClient(app_mod.app)
    a = c.post("/api/matches", json={"mode": "fixture", "fixture": "A"}, headers={"X-Gallery-Session": "alice"}).json()
    c.post("/api/matches", json={"mode": "fixture", "fixture": "B"}, headers={"X-Gallery-Session": "bob"})
    mine = [m["match_id"] for m in c.get("/api/matches", headers={"X-Gallery-Session": "alice"}).json()]
    assert mine == [a["match_id"]]
    assert c.get(f"/api/matches/{a['match_id']}", headers={"X-Gallery-Session": "bob"}).status_code == 404
    assert c.post("/api/matches", json={"mode": "espn", "espn_id": "1"},
                  headers={"X-Gallery-Session": "bob"}).status_code == 403
    for _ in range(4):
        c.post("/api/matches", json={"mode": "fixture", "fixture": "C"}, headers={"X-Gallery-Session": "bob"})
    assert len(c.get("/api/matches", headers={"X-Gallery-Session": "bob"}).json()) == app_mod.MAX_PER_VISITOR


def test_overlay_endpoint_is_served(fx_a, monkeypatch):
    from fastapi.testclient import TestClient

    from gallery import app as app_mod
    rt, _ = fx_a
    monkeypatch.setitem(app_mod.runtimes, rt.match_id, rt)
    r = TestClient(app_mod.app).get(f"/api/matches/{rt.match_id}/overlays")
    assert r.status_code == 200 and "cues" in r.json()
