"""Core tests: detectors, verifier, writer contract, ranking and the approval gate."""

import asyncio

import pytest

from gallery import synth, verifier
from gallery.detectors import DetectorSuite
from gallery.engine import MatchRuntime
from gallery.events import Event
from gallery.store import Store
from gallery.writer import ModelWriter


def ev(n, t, team, typ, **kw):
    return Event(event_id=f"m-e{n:03d}", match_id="m", t=t, period=1, team_id=team, type=typ, **kw)


def fixture_counter():
    """Away has the ball high up, Home regain in own half, two passes, shot on target 9 s later."""
    return [
        ev(1, 0, "HOME", "kick_off", x=50, y=50),
        ev(2, 100, "AWAY", "pass", outcome="complete", player="A9", x=60, y=40, end_x=70, end_y=45),
        ev(3, 102, "HOME", "regain", player="H4", x=30, y=50),
        ev(4, 105, "HOME", "pass", outcome="complete", player="H4", x=30, y=50, end_x=55, end_y=40),
        ev(5, 108, "HOME", "pass", outcome="complete", player="H8", x=55, y=40, end_x=86, end_y=45),
        ev(6, 110, "HOME", "box_entry", player="H9", x=86, y=45),
        ev(7, 111, "HOME", "shot", outcome="on_target", player="H9", x=88, y=46, xg=0.3),
    ]


def run(events, mode="replay"):
    store = Store()
    rt = MatchRuntime(store, "m", "Home FC", "Away FC", mode, use_model=False)

    async def go():
        for e in events:
            await rt.ingest(e)
        await rt.drain()
    asyncio.run(go())
    return rt


# ---------------------------------------------------------------- detectors
def test_counterattack_detected_with_true_facts():
    rt = run(fixture_counter())
    c = [i for i in rt.insights if i["detector"] == "counterattack"]
    assert len(c) == 1
    facts = {f["key"]: f["value"] for f in c[0]["facts"]}
    assert facts["seconds"] == 9 and facts["passes"] == 2 and facts["shooter"] == "H9"


def test_no_counterattack_when_opponent_had_the_ball_in_between():
    evs = fixture_counter()
    evs.insert(4, ev(99, 104, "AWAY", "regain", player="A5", x=40, y=50))
    rt = run(sorted(evs, key=lambda e: e.t))
    assert not [i for i in rt.insights if i["detector"] == "counterattack"]


def test_planted_patterns_recall_on_tuning_seed():
    m = synth.generate(seed=100)
    d = DetectorSuite(m.match_id, "HOME", "AWAY")
    ins = [i for e in m.events for i in d.on_event(e.to_dict())]
    for tr in [t for t in m.truth if t["pattern"] == "counterattack"]:
        assert any(i.detector == "counterattack" and i.team_id == tr["team_id"]
                   and tr["t_from"] - 1 <= i.t <= tr["t_to"] + 300 for i in ins)


# ---------------------------------------------------------------- verifier
@pytest.fixture
def rt_counter():
    return run(fixture_counter())


def test_every_template_sentence_verifies(rt_counter):
    for s in rt_counter.storylines.values():
        assert s["overall"] == "verified", s


def test_wrong_number_blocked(rt_counter):
    claim = {"metric": "seconds_between", "params": {"from_event": "m-e003", "to_event": "m-e007"},
             "op": "==", "value": 5}
    r = verifier.verify_segment(rt_counter.store, "m", {"text": "A shot in 5 seconds.", "claims": [claim]})
    assert r["verdict"] == "blocked" and "data says 9" in r["reason"]


def test_sentence_number_must_match_claim(rt_counter):
    claim = {"metric": "seconds_between", "params": {"from_event": "m-e003", "to_event": "m-e007"},
             "op": "==", "value": 9}
    r = verifier.verify_segment(rt_counter.store, "m", {"text": "A shot in 12 seconds.", "claims": [claim]})
    assert r["verdict"] == "blocked"


def test_unclaimed_and_state_sentences_blocked(rt_counter):
    assert verifier.verify_segment(rt_counter.store, "m", {"text": "Home are the better side.", "claims": []})[
        "verdict"] == "blocked"
    claim = {"metric": "attribution", "params": {"event_id": "m-e007"}, "op": "==", "value": "H9"}
    r = verifier.verify_segment(rt_counter.store, "m", {"text": "H9 looks exhausted.", "claims": [claim]})
    assert r["verdict"] == "blocked" and "state" in r["reason"]


def test_judgement_wording_needs_human(rt_counter):
    claim = {"metric": "attribution", "params": {"event_id": "m-e007"}, "op": "==", "value": "H9"}
    r = verifier.verify_segment(rt_counter.store, "m", {"text": "H9 was relentless.", "claims": [claim]})
    assert r["verdict"] == "needs_human"


def test_unknown_metric_blocked(rt_counter):
    r = verifier.verify_segment(rt_counter.store, "m", {"text": "x", "claims": [{"metric": "vibes", "params": {}}]})
    assert r["verdict"] == "blocked"


def test_possession_unavailable_in_live_feed():
    evs = [e for e in fixture_counter() if e.type != "pass"]
    rt = run(evs, mode="live")
    claim = {"metric": "possession_pct", "params": {"team_id": "HOME", "from_t": 0, "to_t": 200}, "op": ">=", "value": 60}
    r = verifier.verify_segment(rt.store, "m", {"text": "Home had most of the ball.", "claims": [claim]})
    assert r["verdict"] == "blocked" and "Not available" in r["reason"]


# ---------------------------------------------------------------- approval gate
def test_blocked_content_cannot_be_approved(rt_counter):
    sid = next(iter(rt_counter.storylines))
    rt_counter.inject_false(sid, "wrong_number")
    with pytest.raises(ValueError):
        rt_counter.decide(sid, "approve")
    rt_counter.decide(sid, "edit", edits={"strike": [0]})
    assert rt_counter.decide(sid, "approve")["status"] == "approved"
    assert len(rt_counter.audit(sid)) == 2


def test_edit_is_reverified(rt_counter):
    sid = next(s for s, v in rt_counter.storylines.items() if v["detector"] == "counterattack")
    sl = rt_counter.decide(sid, "edit", edits={"segments": {"0": "Home Fc scored in 3 seconds."}})
    assert sl["segments"][0]["verdict"] == "blocked"


# ---------------------------------------------------------------- writer contract
def test_model_writer_validation():
    assert ModelWriter.validate({"headline": "x", "segments": [{"text": "y", "claims": [{}]}]}) is None
    assert ModelWriter.validate({"headline": "x", "segments": [{"text": "y"}]}) is not None
    assert ModelWriter.validate({"segments": []}) is not None


def test_model_writer_without_config_falls_back(monkeypatch):
    for k in ("AZURE_OPENAI_ENDPOINT", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert not ModelWriter().available


def test_ranking_is_deterministic(rt_counter):
    a = rt_counter.ranking("world", "full")
    b = rt_counter.ranking("world", "full")
    assert [x["insight_id"] for x in a] == [x["insight_id"] for x in b]
    assert all(abs(x["score"] - sum(f["contribution"] for f in x["factors"].values())) < 0.01 for x in a)


def test_package_only_contains_approved(rt_counter):
    p = rt_counter.build_package("full_time", "world")
    assert p["items"] == []
    sid = next(iter(rt_counter.storylines))
    rt_counter.decide(sid, "approve")
    assert len(rt_counter.build_package("full_time", "world")["items"]) == 1


def test_window_bounds_match_between_detector_and_verifier():
    """Regression: real StatsBomb times have milliseconds; facts must use the detector's exact window."""
    base = 1000.004
    evs = [ev(1, 0, "HOME", "kick_off", x=50, y=50)]
    n = 2
    for i in range(6):  # 6 regains by one player inside 15 minutes, the first right on the window edge
        evs.append(ev(n, base + i * 60.0, "HOME", "regain", player="P1", x=60, y=50)); n += 1
    evs.append(ev(n, base + 900.0, "HOME", "regain", player="P1", x=60, y=50))
    rt = run(evs)
    for s in rt.storylines.values():
        rt.verify(s)
        assert s["overall"] == "verified", [g["reason"] for g in s["segments"]]


def test_own_goal_counts_for_the_benefiting_team():
    evs = [ev(1, 0, "HOME", "kick_off", x=50, y=50),
           Event(event_id="m-e002", match_id="m", t=300, period=1, team_id="AWAY", type="own_goal",
                 player="H5", x=5, y=50)]
    rt = run(evs)
    assert rt.score() == {"HOME": 0, "AWAY": 1}
    s = next(iter(rt.storylines.values()))
    assert s["overall"] == "verified" and "own net" in s["segments"][0]["text"]


def test_placeholders_are_blocked(rt_counter):
    claim = {"metric": "attribution", "params": {"event_id": "m-e007"}, "op": "==", "value": "H9"}
    for bad in ("[[WINNING_TEAM_MASCOT]] beat H9.", "H9 scored for {team}.", "None scored."):
        r = verifier.verify_segment(rt_counter.store, "m", {"text": bad, "claims": [claim]})
        assert r["verdict"] == "blocked", bad


# ---------------------------------------------------------------- organiser-data file adapter
import json as _json

from gallery import filefeed


def test_file_adapter_reads_unfamiliar_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(filefeed, "FOLDER", tmp_path)
    rows = [
        {"id": 1, "half": 1, "time": "00:00", "team_name": "Reds", "event": "Kick Off", "result": ""},
        {"id": 2, "half": 1, "time": "03:10", "team_name": "Blues", "event": "Pass", "result": "complete",
         "start_x": 70, "start_y": 40, "end_x": 75, "end_y": 45},
        {"id": 3, "half": 1, "time": "03:12", "team_name": "Reds", "event": "Interception", "result": "won",
         "start_x": 30, "start_y": 40},
        {"id": 4, "half": 1, "time": "03:21", "team_name": "Reds", "event": "Shot", "result": "Saved",
         "player": "R9", "start_x": 108, "start_y": 40},
        {"id": 5, "half": 2, "time": "05:00", "team_name": "Blues", "event": "Shot", "result": "Goal",
         "player": "B10", "start_x": 110, "start_y": 38},
        {"id": 6, "half": 2, "time": "06:00", "team_name": "Blues", "event": "Mystery Thing", "result": ""},
    ]
    (tmp_path / "m.json").write_text(_json.dumps({"events": rows}))
    d = filefeed.load("m.json", "fm")
    assert d["meta"]["home"] == "Reds" and d["meta"]["away"] == "Blues"
    assert d["report"]["skipped"] == {"Mystery Thing": 1}
    goal = next(e for e in d["events"] if e.outcome == "goal")
    assert goal.team_id == "AWAY" and goal.minute == 51        # second-half clock restarts -> 45:00 + 5:00
    shot = next(e for e in d["events"] if e.outcome == "on_target")
    assert 89 < shot.x < 91                                    # 120-wide pitch scaled to 0-100


def test_file_adapter_example_finds_planted_patterns():
    if not (filefeed.FOLDER / "example_synthetic_match.csv").exists():
        pytest.skip("example file not present")
    d = filefeed.load("example_synthetic_match.csv", "fx")
    rt = MatchRuntime(Store(), "fx", d["meta"]["home"], d["meta"]["away"], "file", use_model=False, caps=d["caps"])

    async def go():
        for e in d["events"]:
            await rt.ingest(e)
        await rt.drain()
    asyncio.run(go())
    truth = synth.generate(match_id="ex", seed=21).truth
    found = sum(any(i["detector"] == t["pattern"] and i["team_id"] == t["team_id"]
                    and t["t_from"] - 61 <= i["t"] <= t["t_to"] + 361
                    and (not t["player"] or t["player"] in i["players"]) for i in rt.insights) for t in truth)
    assert found == len(truth)
    assert {s["overall"] for s in rt.storylines.values()} == {"verified"}


def test_file_adapter_explains_missing_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(filefeed, "FOLDER", tmp_path)
    (tmp_path / "bad.csv").write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="mapping.json"):
        filefeed.load("bad.csv", "b")
