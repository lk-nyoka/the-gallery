"""Writers turn an evidence bundle into a storyline: headline + segments, each segment with claims.

- TemplateWriter: deterministic, English, always available.
- ModelWriter: Azure OpenAI / Microsoft Foundry chat model, strict JSON, retry once, falls back to
  the template writer if the model is unavailable or keeps returning invalid output.

The model never sees raw data: only the evidence bundle (insight metrics, true facts computed by
code, and a compact list of the evidence events).
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Optional

import httpx

from .events import fmt_clock


def _n(v, one: str, many: str) -> str:
    return f"{v} {one if v == 1 else many}"


def _team(names: dict, tid: str) -> str:
    return names.get(tid, tid)


def _facts(ins: dict) -> dict[str, dict]:
    return {f["key"]: f for f in ins["facts"]}


def _claim(f: dict) -> dict:
    return {"key": f["key"], "metric": f["metric"], "params": f["params"], "op": f["op"], "value": f["value"]}


def _seg(text: str, *facts: dict) -> dict:
    return {"text": text, "claims": [_claim(f) for f in facts]}


OUTCOME_TEXT = {"goal": "ended in a goal", "on_target": "forced a save", "off_target": "went wide",
                "blocked": "was blocked"}

GRAPHIC = {
    "goal": "goal_card", "counterattack": "sequence_timeline", "momentum_shift": "momentum_chart",
    "standout": "player_card", "goal_threat": "chance_timeline", "red_card": "card_alert",
    "turning_point": "before_after_split", "power_shot": "speed_tag",
}


def graphic_spec(ins: dict, names: dict) -> dict:
    m = ins["metrics"]
    fields: dict[str, Any] = {"team": _team(names, ins["team_id"]), "minute": ins["minute"]}
    if ins["detector"] == "goal":
        fields.update(scorer=(ins["players"] or ["?"])[0], score=f"{m['score_team']}-{m['score_opp']}",
                      own_goal=bool(m.get("own_goal")))
    elif ins["detector"] == "counterattack":
        fields.update(seconds=m["seconds"], passes=m["passes"], outcome=m["outcome"])
    elif ins["detector"] == "momentum_shift":
        fields.update(share_pct=m["share_pct"], prev_share_pct=m["prev_share_pct"])
    elif ins["detector"] == "standout":
        fields.update(player=ins["players"][0], actions=m["actions"], shots=m["shots"],
                      box_entries=m["box_entries"], regains=m["regains"])
    elif ins["detector"] == "goal_threat":
        fields.update(shots=m["shots"], on_target=m["on_target"], xg=m.get("xg"))
    elif ins["detector"] == "red_card":
        fields.update(player=(ins["players"] or ["?"])[0], score=f"{m['score_team']}-{m['score_opp']}")
    elif ins["detector"] == "power_shot":
        fields.update(player=(ins["players"] or ["?"])[0], speed_kmh=m["speed"], outcome=m["outcome"])
    elif ins["detector"] == "turning_point":
        fields.update(red_card_minute=m.get("red_minute"), sent_off=(ins["players"] or ["?"])[0],
                      before={"down_shots": m["down_shots_before"], "up_shots": m["up_shots_before"]},
                      after={"up_shots": m["up_shots_after"], "up_goals": m["up_goals_after"],
                             "down_shots": m["down_shots_after"]})
    return {"template": GRAPHIC.get(ins["detector"], "lower_third"), "fields": fields,
            "event_ids": ins["evidence"]}


def clip_cue(ins: dict) -> dict:
    off = ins.get("clock_offset", 0.0)
    if ins["detector"] == "turning_point":
        m = (ins["metrics"].get("red_minute") or ins["minute"])
        return {"from": f"{max(m - 1, 0):02d}:00", "to": f"{m:02d}:59",
                "label": f"Red card, {m}' (then the goals that followed)"}
    if ins["detector"] in ("goal", "counterattack", "red_card", "power_shot"):
        a, b = ins["from_t"] - 5 - off, ins["to_t"] + 8 - off
    else:
        a, b = max(0, ins["to_t"] - 120 - off), ins["to_t"] - off
    return {"from": fmt_clock(a), "to": fmt_clock(b), "label": f"{ins['detector'].replace('_', ' ').title()}, "
                                                               f"{ins['minute']}'"}


class TemplateWriter:
    name = "template"

    def write(self, ins: dict, names: dict, market: Optional[dict] = None) -> dict:
        f = _facts(ins)
        team = _team(names, ins["team_id"])
        d, m = ins["detector"], ins["metrics"]
        segs: list[dict] = []
        if d == "goal":
            opp = [v for k, v in names.items() if k != ins["team_id"]][0]
            if "own_goal_by" in f:
                og = f["own_goal_by"]["value"]
                headline = f"{team} score through an own goal"
                segs.append(_seg(f"{og} turns the ball into his own net for {team}.", f["own_goal_by"]))
            else:
                scorer = f["scorer"]["value"]
                headline = f"{scorer} scores for {team}"
                segs.append(_seg(f"{scorer} scores for {team}.", f["scorer"]))
            home_f, away_f = (f["team_goals"], f["opp_goals"]) if ins["team_id"] == "HOME" else \
                (f["opp_goals"], f["team_goals"])
            segs.append(_seg(f"It's {names['HOME']} {home_f['value']}, {names['AWAY']} {away_f['value']}.",
                             home_f, away_f))
            if "shot_xg" in f:
                segs.append(_seg(f"The chance was worth {f['shot_xg']['value']} expected goals.", f["shot_xg"]))
        elif d == "counterattack":
            headline = f"{team} break at speed"
            segs.append(_seg(f"{team} went from winning the ball in their own half to a shot in "
                             f"{_n(f['seconds']['value'], 'second', 'seconds')}.", f["seconds"]))
            rp, sh = f["regain_player"]["value"], f["shooter"]["value"]
            if rp != "unknown" and sh != "unknown":
                txt = (f"{rp} won it back and took the shot himself." if rp == sh
                       else f"{rp} won it back and {sh} took the shot.")
                segs.append(_seg(txt, f["regain_player"], f["shooter"]))
            if "passes" in f:
                n = f["passes"]["value"]
                segs.append(_seg(f"The move needed {n} pass{'es' if n != 1 else ''}.", f["passes"]))
            segs.append(_seg(f"The shot {OUTCOME_TEXT[m['outcome']]}.", f["shot_outcome"]))
        elif d == "momentum_shift":
            headline = f"Momentum swings to {team}"
            if "box_entries" in f:
                segs.append(_seg(f"{team} have had {_n(f['box_entries']['value'], 'box entry', 'box entries')} and "
                                 f"{_n(f['shots']['value'], 'shot', 'shots')} in the last 8 minutes.",
                                 f["box_entries"], f["shots"]))
            else:
                segs.append(_seg(f"{team} have had {_n(f['shots']['value'], 'shot', 'shots')} in the last 8 minutes.",
                                 f["shots"]))
            if "prev_threat_share" in f:
                segs.append(_seg(f"That is {f['threat_share']['value']}% of the attacking threat in that spell, up "
                                 f"from {f['prev_threat_share']['value']}% in the 8 minutes before.",
                                 f["threat_share"], f["prev_threat_share"]))
            else:
                segs.append(_seg(f"That is {f['threat_share']['value']}% of the attacking threat in that spell, "
                                 f"after a quiet 8 minutes with no attacking threat from either side.",
                                 f["threat_share"]))
            opp = [v for k, v in names.items() if k != ins["team_id"]][0]
            segs.append(_seg(f"{opp} have had {_n(f['opp_shots']['value'], 'shot', 'shots')} in the same period.",
                             f["opp_shots"]))
        elif d == "standout":
            p = ins["players"][0]
            headline = f"{p} drives {team}'s play"
            segs.append(_seg(f"{p} has been involved in {f['key_actions']['value']} of {team}'s "
                             f"{f['team_key_actions']['value']} key actions in the last 15 minutes.",
                             f["key_actions"], f["team_key_actions"]))
            parts, used = [], []
            for key, one, many in (("shots", "shot", "shots"), ("box_entries", "box entry", "box entries"),
                                   ("regains", "ball regain", "ball regains"),
                                   ("key_passes", "chance created", "chances created")):
                if key not in f:
                    continue
                v = f[key]["value"]
                if v:
                    parts.append(f"{v} {one if v == 1 else many}")
                    used.append(f[key])
            if parts:
                txt = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
                segs.append(_seg(f"That includes {txt}.", *used))
        elif d == "goal_threat":
            headline = f"{team} keep knocking"
            segs.append(_seg(f"{team} have had {_n(f['shots']['value'], 'shot', 'shots')} in the last 10 minutes, "
                             f"{f['on_target']['value']} of them on target.", f["shots"], f["on_target"]))
            segs.append(_seg("They are still waiting for a goal in that spell.", f["goals"]))
            if "xg" in f:
                segs.append(_seg(f"Those chances add up to {f['xg']['value']} expected goals.", f["xg"]))
        elif d == "red_card":
            p = f["sent_off"]["value"]
            headline = f"{p} sent off"
            segs.append(_seg(f"{p} is sent off, and {team} must play on with a player fewer.", f["sent_off"]))
            home_f, away_f = (f["team_goals"], f["opp_goals"]) if ins["team_id"] == "HOME" else \
                (f["opp_goals"], f["team_goals"])
            segs.append(_seg(f"It's {names['HOME']} {home_f['value']}, {names['AWAY']} {away_f['value']} at that moment.",
                             home_f, away_f))
        elif d == "turning_point":
            p = f["sent_off"]["value"]
            down = _team(names, m["down_team"])
            up = team
            headline = ("The red card that turned the match" if m.get("at_full_time")
                        else f"{up} punish {p}'s red card")
            segs.append(_seg(f"Before {p}'s red card, {down} had {_n(f['down_shots_before']['value'], 'shot', 'shots')} "
                             f"to {up}'s {f['up_shots_before']['value']}.",
                             f["sent_off"], f["down_shots_before"], f["up_shots_before"]))
            g = f["up_goals_after"]["value"]
            segs.append(_seg(f"Since then, {up} have had {_n(f['up_shots_after']['value'], 'shot', 'shots')} and scored "
                             f"{_n(g, 'goal', 'goals')}.", f["up_shots_after"], f["up_goals_after"]))
            if m.get("at_full_time"):
                segs.append(_seg(f"{down} managed {_n(f['down_shots_after']['value'], 'shot', 'shots')} with a player fewer.",
                                 f["down_shots_after"]))
        elif d == "power_shot":
            p = f["shooter"]["value"]
            sp = f["shot_speed"]["value"]
            headline = f"{p} lets fly at {sp:g} km/h"
            segs.append(_seg(f"{p}'s shot left the boot at {sp:g} km/h.", f["shooter"], f["shot_speed"]))
            segs.append(_seg(f"The shot {OUTCOME_TEXT.get(f['shot_outcome']['value'], 'was taken')}.", f["shot_outcome"]))
        else:
            headline = d
        return {"headline": headline, "segments": segs, "graphic": graphic_spec(ins, names),
                "clip_cue": clip_cue(ins), "writer": self.name}


# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the Writer for The Gallery, a live football production tool.
Rule: numbers come from code, words come from you. Every word must point back to a number.

You receive an evidence bundle: one detected insight, a list of TRUE FACTS computed by code, and the
evidence events. Write a short broadcast storyline for presenters and graphics.

Hard rules:
1. Use ONLY the facts provided. Never invent numbers, players, events or causes.
2. Every segment is one sentence and MUST include a "claims" array with at least one fact copied
   EXACTLY from the facts list (same key, metric, params, op, value).
3. Any number you write must equal the value of a claim in that segment (or the length/minutes of
   its time window). Writing numbers as digits is preferred.
4. Do not describe feelings, fitness, intent or morale (tired, nervous, confident, wants to...).
   The data cannot show those.
5. Avoid judgement words (dominant, brilliant, relentless, best, total control). Describe actions.
6. Do not mention match minutes or times; the graphic carries the clock.
7. 2 to 4 segments. Headline: max 8 words, no numbers that are not in the facts.
8. Write in the requested language and adapt the angle to the market notes, but claims stay identical.

Return ONLY JSON:
{"headline": "...", "segments": [{"text": "...", "claims": [ {fact}, ... ]}, ...]}"""


def evidence_bundle(ins: dict, names: dict, events: list[dict], score: dict, market: Optional[dict]) -> dict:
    return {
        "match": {"home": names.get("HOME") or list(names.values())[0], "teams": names,
                  "score_now": {names.get(k, k): v for k, v in score.items()}},
        "insight": {"type": ins["detector"], "team": names.get(ins["team_id"], ins["team_id"]),
                    "minute": ins["minute"], "players": ins["players"], "metrics": ins["metrics"]},
        "facts": [_claim(f) for f in ins["facts"]],
        "evidence_events": [
            {k: e.get(k) for k in ("event_id", "minute", "team_id", "type", "outcome", "player")}
            for e in events[:15]
        ],
        "language": (market or {}).get("language_name", "English"),
        "market_notes": (market or {}).get("notes", "World feed: neutral, analytical."),
    }


class ModelWriter:
    """Chat-model writer. Optional: without configuration The Gallery uses the template writer.

    Option A, Azure OpenAI / Microsoft Foundry:
      AZURE_OPENAI_ENDPOINT    e.g. https://<resource>.openai.azure.com
      AZURE_OPENAI_API_KEY
      AZURE_OPENAI_DEPLOYMENT  model deployment name
      AZURE_OPENAI_API_VERSION default 2024-10-21; "v1" for the /openai/v1 route

    Option B, any OpenAI-compatible endpoint (including free tiers):
      LLM_BASE_URL  e.g. https://<provider>/v1   (the code appends /chat/completions)
      LLM_API_KEY
      LLM_MODEL
    """

    name = "model"

    def __init__(self):
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        self.key = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        self.base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        self.generic_key = os.getenv("LLM_API_KEY", "")
        self.generic_model = os.getenv("LLM_MODEL", "")
        self.provider = "azure" if (self.endpoint and self.key and self.deployment) else (
            "openai_compatible" if (self.base_url and self.generic_key and self.generic_model) else None)
        if self.provider == "openai_compatible":
            self.deployment = self.generic_model
        self.fallback = TemplateWriter()

    @property
    def available(self) -> bool:
        return self.provider is not None

    def _request(self) -> tuple[str, dict]:
        if self.provider == "openai_compatible":
            return f"{self.base_url}/chat/completions", {"Authorization": f"Bearer {self.generic_key}"}
        if self.api_version == "v1":
            return f"{self.endpoint}/openai/v1/chat/completions", {"api-key": self.key}
        return (f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
                f"?api-version={self.api_version}", {"api-key": self.key})

    async def _call(self, messages: list[dict]) -> str:
        url, headers = self._request()
        body: dict[str, Any] = {"messages": messages, "temperature": 0.3,
                                "response_format": {"type": "json_object"}}
        if self.provider == "openai_compatible" or self.api_version == "v1":
            body["model"] = self.deployment
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
            if r.status_code == 400 and "response_format" in r.text:
                body.pop("response_format")  # some providers don't support JSON mode
                r = await client.post(url, headers={**headers, "Content-Type": "application/json"}, json=body)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    @staticmethod
    def validate(out: Any, require_claims: bool = True) -> Optional[str]:
        if not isinstance(out, dict):
            return "output is not a JSON object"
        if not isinstance(out.get("headline"), str) or not out["headline"].strip():
            return "missing headline"
        segs = out.get("segments")
        if not isinstance(segs, list) or not segs:
            return "missing segments"
        for i, s in enumerate(segs):
            if not isinstance(s, dict) or not isinstance(s.get("text"), str):
                return f"segment {i} has no text"
            if require_claims and (not isinstance(s.get("claims"), list) or not s["claims"]):
                return f"segment {i} has no claims"
        return None

    async def write(self, ins: dict, names: dict, events: list[dict], score: dict,
                    market: Optional[dict] = None, base: Optional[dict] = None) -> dict:
        if not self.available:
            out = self.fallback.write(ins, names, market)
            out["writer"] = "template (no model configured)"
            return out
        bundle = evidence_bundle(ins, names, events, score, market)
        if base:  # localisation: rewrite a verified storyline for a market, claims unchanged
            bundle["verified_storyline"] = {"headline": base["headline"],
                                            "segments": [{"text": s["text"], "claims": s["claims"]}
                                                         for s in base["segments"]]}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(bundle, default=str)}]
        last_err = ""
        for attempt in range(2):
            try:
                raw = await self._call(messages)
                out = json.loads(raw)
                # first attempt must be fully claimed; on the retry, unclaimed sentences are kept so the
                # verifier blocks them visibly instead of hiding the model's mistake
                err = self.validate(out, require_claims=(attempt == 0))
                if not err:
                    # sentences without claims are kept so the verifier can block them visibly
                    return {"headline": out["headline"].strip(),
                            "segments": [{"text": s["text"].strip(), "claims": s.get("claims") or []}
                                         for s in out["segments"]],
                            "graphic": graphic_spec(ins, names), "clip_cue": clip_cue(ins),
                            "writer": f"model:{self.deployment}" + (" (retry)" if attempt else "")}
                last_err = err
                messages += [{"role": "assistant", "content": raw},
                             {"role": "user", "content": f"Invalid: {err}. Return valid JSON following every rule."}]
            except Exception as exc:  # network, auth, JSON
                last_err = str(exc)[:200]
                break
        out = self.fallback.write(ins, names, market)
        out["writer"] = f"template (model failed: {last_err})"
        return out


# ---------------------------------------------------------------------------
# Red-team: deliberately false claims, clearly labelled as injected tests.
# ---------------------------------------------------------------------------

def false_claims(ins: dict, names: dict, possession: dict, rng: random.Random) -> list[dict]:
    """Build labelled false segments of different kinds for the demo and the benchmark.

    `possession` = {"team_id": ..., "from_t": ..., "to_t": ..., "actual": int | None} for an 8-minute window.
    """
    team = _team(names, ins["team_id"])
    f = _facts(ins)
    out = []
    w = {"from_t": possession["from_t"], "to_t": possession["to_t"]}
    actual = possession.get("actual")
    p_team, p_actual = ins["team_id"], actual
    if actual is not None and actual > 80:  # claim the other side dominated instead, so the claim is false
        p_team = "AWAY" if ins["team_id"] == "HOME" else "HOME"
        p_actual = 100 - actual
    claimed = min((p_actual + 15) if p_actual is not None else 65, 95)
    official = possession.get("official")  # {"HOME": 35.4, "AWAY": 64.6} from the feed's team stats
    if actual is None and official:
        lo = min(official, key=official.get)  # claim the side with LESS of the ball dominated it
        out.append({"kind": "inflated_possession",
                    "text": f"{_team(names, lo)} dominated possession.",
                    "claims": [{"metric": "team_stat", "params": {"team_id": lo, "stat": "possessionPct"},
                                "op": ">=", "value": 60}]})
    else:
        out.append({"kind": "inflated_possession",
                    "text": f"{_team(names, p_team)} dominated possession in the last 8 minutes.",
                    "claims": [{"metric": "possession_pct", "params": {"team_id": p_team, **w},
                                "op": ">=", "value": claimed}]})
    player = (ins["players"] or ["Their captain"])[0]
    out.append({"kind": "unsupported_state", "text": f"{player} looks exhausted and has lost confidence.",
                "claims": []})
    numeric = [x for x in ins["facts"] if isinstance(x["value"], (int, float)) and not isinstance(x["value"], bool)]
    if numeric:
        x = rng.choice(numeric)
        wrong = x["value"] + rng.choice([2, 3, 4])
        out.append({"kind": "wrong_number",
                    "text": f"The data shows {wrong:g} for {x['key'].replace('_', ' ')} here.",
                    "claims": [{**_claim(x), "value": wrong}]})
        out.append({"kind": "number_mismatch",
                    "text": f"That figure is {x['value'] + 7:g}.", "claims": [_claim(x)]})
    out.append({"kind": "unclaimed", "text": f"{team} have been the better side all afternoon.", "claims": []})
    out.append({"kind": "judgement_wording",
                "text": f"{team} were relentless here.",
                "claims": [_claim(numeric[0])] if numeric else []})
    if f.get("scorer") or f.get("own_goal_by"):
        f["scorer"] = f.get("scorer") or f["own_goal_by"]
        other = "Somebody Else"
        out.append({"kind": "wrong_attribution", "text": f"{other} scored it.",
                    "claims": [{**_claim(f["scorer"]), "value": other}]})
    return out
