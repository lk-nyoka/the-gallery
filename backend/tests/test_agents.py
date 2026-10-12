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
