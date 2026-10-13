"""Deterministic verifier. No model involved.

Each segment of a storyline carries one or more structured claims. A claim names a metric from
the catalogue below, its parameters, an operator and a value. The verifier compiles the claim to a
fixed, parameterised SQL template, runs it against the event store and compares.

Verdicts
  verified     the assertion is true and the wording contains no unsupported intensifiers
  needs_human  numbers check out, but the wording is qualitative or comparative
  blocked      no claim, failed check, metric unavailable, number mismatch, or a state the data
               cannot contain (fatigue, morale, intent, emotion)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .store import Store

VERIFIED, NEEDS_HUMAN, BLOCKED = "verified", "needs_human", "blocked"

# Words describing states the event data cannot contain -> blocked.
UNSUPPORTED_STATES = [
    "tired", "exhausted", "fatigue", "fatigued", "leggy", "morale", "confident", "confidence",
    "nervous", "nerves", "frustrated", "frustration", "angry", "anger", "upset", "panicking", "panic",
    "motivated", "hungry for", "desperate", "desperation", "wants to", "intends", "deliberately",
    "lost belief", "believe", "happy", "unhappy", "complacent", "lazy", "injured", "injury",
]

# Qualitative / intensifying words -> needs_human when the numbers are true.
QUALITATIVE = [
    "dominat", "control", "relentless", "superb", "brilliant", "dreadful", "terrible", "awful",
    "world-class", "world class", "masterclass", "best", "worst", "incredible", "unstoppable",
    "total", "completely", "always", "never", "everywhere", "massive", "huge", "outclass",
    "overwhelm", "dismantl", "destroy", "magnificent", "woeful", "clinical", "sensational",
    # causal stories the event data can't prove
    "led to", "leading to", "capitaliz", "capitalis", "because", "thanks to", "as a result", "deserv",
    "impressive", "stunning", "amazing", "excellent", "fantastic", "great ", "poor ", "dazzling", "heroic",
]

# Words that declare a result. Mid-match they are false even when every number is true, so a human checks them.
# House style for a league-operated surface: no abuse, blame, speculation about officials, or betting talk.
POLICY_TERMS = ["robbed", "cheat", "disgrace", "shambles", "pathetic", "embarrassing", "scandal", "bottled", "choked",
                "clueless", "useless", "sacked", "dive", "odds", "betting", "punters", "accumulator", "wager"]
POLICY_WORDS = re.compile(r"\b(robbed|cheat(?:s|ed|ing)?|disgrace(?:ful)?|shambles|pathetic|embarrass(?:ing|ment)|"
                          r"scandal(?:ous)?|bottled|chok(?:e|ed|ing)|clueless|useless|sack(?:ed|ing)?|div(?:e|ed|ing)|"
                          r"odds|bet(?:s|ting)?|punters?|accumulator|wager)\b", re.I)
RESULT_WORDS = re.compile(r"\b((?:wins?|won)(?!\s+(?:it|the ball|possession|back|a corner|a free|a penalty|the penalty|"
                          r"the duel|the header))|victory|victorious|beats?|beaten|defeat(?:s|ed)?|triumph(?:s|ed)?|clinch(?:es|ed)?|"
                          r"seal(?:s|ed)? the|three points)\b", re.I)

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

TOLERANCE = {"team_stat": 0.5, "possession_pct": 1.0, "threat_share_pct": 1.0, "seconds_between": 1.0, "xg_sum": 0.02,
             "shot_xg": 0.01, "shot_speed": 0.5, "max_shot_speed": 0.5, "pass_accuracy_pct": 1.0,
             "avg_pass_length_m": 0.5}


@dataclass
class ClaimResult:
    metric: Optional[str]
    params: dict
    op: Optional[str]
    value: Any
    actual: Any = None
    ok: bool = False
    reason: str = ""
    sql: str = ""
    sql_params: list = field(default_factory=list)
    rows: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Metric catalogue: metric -> builder returning (sql, params, extractor)
# ---------------------------------------------------------------------------

def _types(p: dict) -> list[str]:
    t = p.get("type")
    return list(t) if isinstance(t, list) else [t]   # always a copy: never mutate a claim


def _count(match_id: str, p: dict):
    types = _types(p)
    sql = (f"SELECT COUNT(*) AS value FROM events WHERE match_id = ? AND team_id = ? "
           f"AND type IN ({','.join('?' * len(types))}) AND t BETWEEN ? AND ?")
    params = [match_id, p["team_id"], *types, p["from_t"], p["to_t"]]
    if p.get("outcome"):
        outs = p["outcome"] if isinstance(p["outcome"], list) else [p["outcome"]]
        sql += f" AND outcome IN ({','.join('?' * len(outs))})"
        params += outs
    if p.get("player"):
        sql += " AND player = ?"
        params.append(p["player"])
    return sql, params, lambda rows: rows[0]["value"]


def _seconds_between(match_id: str, p: dict):
    sql = ("SELECT ROUND(b.t - a.t) AS value FROM events a, events b "
           "WHERE a.match_id = ? AND a.event_id = ? AND b.event_id = ?")
    return sql, [match_id, p["from_event"], p["to_event"]], lambda rows: rows[0]["value"] if rows else None


def _passes_between(match_id: str, p: dict):
    sql = ("SELECT COUNT(*) AS value FROM events e, events a, events b "
           "WHERE e.match_id = ? AND a.event_id = ? AND b.event_id = ? AND e.team_id = ? "
           "AND e.type = 'pass' AND e.t BETWEEN a.t AND b.t")
    return sql, [match_id, p["from_event"], p["to_event"], p["team_id"]], lambda rows: rows[0]["value"]


def _possession(match_id: str, p: dict):
    sql = ("SELECT SUM(CASE WHEN team_id = ? THEN 1 ELSE 0 END) AS team_passes, COUNT(*) AS all_passes "
           "FROM events WHERE match_id = ? AND type = 'pass' AND t BETWEEN ? AND ?")

    def ex(rows):
        r = rows[0]
        if not r["all_passes"]:
            return None
        return round(100.0 * r["team_passes"] / r["all_passes"])
    return sql, [p["team_id"], match_id, p["from_t"], p["to_t"]], ex


def _threat_share(match_id: str, p: dict):
    # threat = box entries + 2 x shots + 0.25 x passes ending in the final third
    sql = ("SELECT SUM(CASE WHEN team_id = ? THEN w ELSE 0 END) AS team_threat, SUM(w) AS all_threat FROM ("
           " SELECT team_id, CASE WHEN type = 'box_entry' THEN 1.0 WHEN type = 'shot' THEN 2.0"
           " WHEN type = 'pass' AND end_x >= 66.7 THEN 0.25 ELSE 0 END AS w"
           " FROM events WHERE match_id = ? AND t BETWEEN ? AND ?)")

    def ex(rows):
        r = rows[0]
        if not r["all_threat"]:
            return None
        return round(100.0 * r["team_threat"] / r["all_threat"])
    return sql, [p["team_id"], match_id, p["from_t"], p["to_t"]], ex


def _score(match_id: str, p: dict):
    sql = ("SELECT COUNT(*) AS value FROM events WHERE match_id = ? AND team_id = ? "
           "AND ((type = 'shot' AND outcome = 'goal') OR type = 'own_goal') AND t <= ?")
    return sql, [match_id, p["team_id"], p["at_t"]], lambda rows: rows[0]["value"]


def _attribution(match_id: str, p: dict):
    sql = "SELECT player AS value FROM events WHERE match_id = ? AND event_id = ?"
    return sql, [match_id, p["event_id"]], lambda rows: rows[0]["value"] if rows else None


def _shot_outcome(match_id: str, p: dict):
    sql = "SELECT outcome AS value FROM events WHERE match_id = ? AND event_id = ? AND type = 'shot'"
    return sql, [match_id, p["event_id"]], lambda rows: rows[0]["value"] if rows else None


def _shot_xg(match_id: str, p: dict):
    sql = "SELECT xg AS value FROM events WHERE match_id = ? AND event_id = ?"
    return sql, [match_id, p["event_id"]], lambda rows: rows[0]["value"] if rows else None


def _xg_sum(match_id: str, p: dict):
    sql = ("SELECT ROUND(SUM(xg), 2) AS value, COUNT(*) AS n, COUNT(xg) AS with_xg FROM events "
           "WHERE match_id = ? AND team_id = ? AND type = 'shot' AND t BETWEEN ? AND ?")

    def ex(rows):
        r = rows[0]
        if r["n"] == 0 or r["with_xg"] < r["n"]:
            return None
        return r["value"]
    return sql, [match_id, p["team_id"], p["from_t"], p["to_t"]], ex


def _shot_speed(match_id: str, p: dict):
    sql = "SELECT speed AS value FROM events WHERE match_id = ? AND event_id = ?"
    return sql, [match_id, p["event_id"]], lambda rows: rows[0]["value"] if rows else None


def _who(p: dict) -> tuple[str, list]:
    if p.get("player"):
        return " AND player = ?", [p["player"]]
    return "", []


def _max_shot_speed(match_id: str, p: dict):
    extra, ex_params = _who(p)
    sql = (f"SELECT MAX(speed) AS value, COUNT(*) AS n, COUNT(speed) AS with_speed FROM events WHERE match_id = ? "
           f"AND team_id = ? AND type = 'shot' AND t BETWEEN ? AND ?{extra}")

    def ex(rows):
        r = rows[0]
        return None if r["n"] == 0 or r["with_speed"] == 0 else r["value"]
    return sql, [match_id, p["team_id"], p["from_t"], p["to_t"], *ex_params], ex


def _pass_accuracy(match_id: str, p: dict):
    """Completed passes as a percentage of all passes (team or one player) in a window."""
    extra, ex_params = _who(p)
    sql = (f"SELECT ROUND(100.0 * SUM(outcome = 'complete') / COUNT(*)) AS value, COUNT(*) AS n FROM events "
           f"WHERE match_id = ? AND team_id = ? AND type = 'pass' AND t BETWEEN ? AND ?{extra}")
    return sql, [match_id, p["team_id"], p["from_t"], p["to_t"], *ex_params], \
        lambda rows: None if not rows or rows[0]["n"] == 0 else int(rows[0]["value"])


def _avg_pass_length(match_id: str, p: dict):
    extra, ex_params = _who(p)
    sql = (f"SELECT ROUND(AVG(pass_length_m(x, y, end_x, end_y)), 1) AS value, COUNT(end_x) AS n FROM events "
           f"WHERE match_id = ? AND team_id = ? AND type = 'pass' AND t BETWEEN ? AND ?{extra}")
    return sql, [match_id, p["team_id"], p["from_t"], p["to_t"], *ex_params], \
        lambda rows: None if not rows or rows[0]["n"] == 0 else rows[0]["value"]


def _hard_passes(match_id: str, p: dict):
    """Completed passes rated 7 or more out of 10 for difficulty (length, progress, into the box, switches)."""
    extra, ex_params = _who(p)
    sql = (f"SELECT COUNT(*) AS value, COUNT(end_x) AS n FROM events WHERE match_id = ? AND team_id = ? "
           f"AND type = 'pass' AND outcome = 'complete' AND pass_difficulty(x, y, end_x, end_y) >= 7 "
           f"AND t BETWEEN ? AND ?{extra}")
    return sql, [match_id, p["team_id"], p["from_t"], p["to_t"], *ex_params], lambda rows: rows[0]["value"]


def _team_stat(match_id: str, p: dict):
    """Official team stats published by the feed (e.g. full-match possession)."""
    sql = "SELECT value FROM match_stats WHERE match_id = ? AND team_id = ? AND stat = ?"
    return sql, [match_id, p["team_id"], p["stat"]], lambda rows: rows[0]["value"] if rows else None


CATALOGUE = {
    "team_stat": (_team_stat, ["team_id", "stat"]),
    "count": (_count, ["team_id", "type", "from_t", "to_t"]),
    "seconds_between": (_seconds_between, ["from_event", "to_event"]),
    "passes_between": (_passes_between, ["team_id", "from_event", "to_event"]),
    "possession_pct": (_possession, ["team_id", "from_t", "to_t"]),
    "threat_share_pct": (_threat_share, ["team_id", "from_t", "to_t"]),
    "score": (_score, ["team_id", "at_t"]),
    "attribution": (_attribution, ["event_id"]),
    "shot_outcome": (_shot_outcome, ["event_id"]),
    "shot_xg": (_shot_xg, ["event_id"]),
    "xg_sum": (_xg_sum, ["team_id", "from_t", "to_t"]),
    "shot_speed": (_shot_speed, ["event_id"]),
    "max_shot_speed": (_max_shot_speed, ["team_id", "from_t", "to_t"]),
    "pass_accuracy_pct": (_pass_accuracy, ["team_id", "from_t", "to_t"]),
    "avg_pass_length_m": (_avg_pass_length, ["team_id", "from_t", "to_t"]),
    "hard_passes_completed": (_hard_passes, ["team_id", "from_t", "to_t"]),
}

ROW_PREVIEW_SQL = ("SELECT event_id, minute, team_id, type, outcome, player, x, y FROM events "
                   "WHERE match_id = ? AND event_id IN ({}) ORDER BY t LIMIT 12")
WINDOW_PREVIEW_SQL = ("SELECT event_id, minute, team_id, type, outcome, player, x, y FROM events "
                      "WHERE match_id = ? AND team_id = ? AND t BETWEEN ? AND ? {} ORDER BY t LIMIT 12")


def _compare(op: str, actual: Any, value: Any, tol: float) -> bool:
    if isinstance(value, str) or isinstance(actual, str):
        return op == "==" and str(actual).strip().lower() == str(value).strip().lower()
    a, v = float(actual), float(value)
    if op == "==":
        return abs(a - v) <= tol
    if op == ">=":
        return a >= v - tol
    if op == "<=":
        return a <= v + tol
    if op == ">":
        return a > v
    if op == "<":
        return a < v
    raise ValueError(op)


def check_claim(store: Store, match_id: str, claim: dict) -> ClaimResult:
    metric = claim.get("metric")
    params = claim.get("params") or {}
    op = claim.get("op", "==")
    value = claim.get("value")
    res = ClaimResult(metric=metric, params=params, op=op, value=value)
    if metric not in CATALOGUE:
        res.reason = f"Unknown metric '{metric}': only catalogue metrics can be verified."
        return res
    builder, required = CATALOGUE[metric]
    missing = [k for k in required if k not in params]
    if missing:
        res.reason = f"Claim is missing parameters: {', '.join(missing)}."
        return res
    if op not in ("==", ">=", "<=", ">", "<"):
        res.reason = f"Unsupported operator '{op}'."
        return res
    sql, sql_params, extract = builder(match_id, params)
    rows = store.query(sql, sql_params)
    res.sql, res.sql_params = sql, sql_params
    actual = extract(rows)
    res.actual = actual
    res.rows = _preview_rows(store, match_id, metric, params)
    if actual is None:
        res.reason = (f"Not available in this feed: '{metric}' can't be computed from the events received "
                      f"(for example, no passes are logged).")
        return res
    if value is None:
        res.reason = "Claim has no value."
        return res
    if _compare(op, actual, value, TOLERANCE.get(metric, 0.0)):
        res.ok = True
    else:
        res.reason = f"Claim says {op} {value}; the data says {actual}."
    return res


def _preview_rows(store: Store, match_id: str, metric: str, p: dict) -> list[dict]:
    ids = [p[k] for k in ("event_id", "from_event", "to_event") if p.get(k)]
    if ids:
        return store.query(ROW_PREVIEW_SQL.format(",".join("?" * len(ids))), [match_id, *ids])
    if {"team_id", "from_t", "to_t"} <= p.keys():
        extra, extra_params = "", []
        if metric == "count":
            types = _types(p)
            extra = f"AND type IN ({','.join('?' * len(types))})"
            extra_params = types
            if p.get("player"):
                extra += " AND player = ?"
                extra_params.append(p["player"])
        elif metric == "possession_pct":
            return []
        return store.query(WINDOW_PREVIEW_SQL.format(extra), [match_id, p["team_id"], p["from_t"], p["to_t"],
                                                              *extra_params])
    return []


# ---------------------------------------------------------------------------
# Text checks
# ---------------------------------------------------------------------------

_num_re = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")


def numbers_in(text: str, english_words: bool = True) -> list[float]:
    nums = [float(n) for n in _num_re.findall(text)]
    if english_words:
        for w in re.findall(r"[a-z]+", text.lower()):
            if w in NUMBER_WORDS:
                nums.append(float(NUMBER_WORDS[w]))
    return nums


def _allowed_numbers(claims: list[dict]) -> set[float]:
    allowed: set[float] = set()
    for c in claims:
        v = c.get("value")
        if isinstance(v, (int, float)):
            allowed.add(round(float(v), 2))
        p = c.get("params") or {}
        if "from_t" in p and "to_t" in p:
            allowed.add(round((p["to_t"] - p["from_t"]) / 60.0))
            for t in (p["from_t"], p["to_t"]):
                allowed.update({float(int(t // 60)), float(int(t // 60) + 1)})
        if "at_t" in p:
            allowed.update({float(int(p["at_t"] // 60)), float(int(p["at_t"] // 60) + 1)})
    return allowed


def _find(words: list[str], text: str) -> Optional[str]:
    low = text.lower()
    for w in words:
        if re.search(r"(?<![a-z])" + re.escape(w), low):
            return w
    return None


_placeholder_re = re.compile(r"\[\[|\]\]|[{}<>]|\b(None|undefined|null|NaN|nan|unknown|TBD|PLACEHOLDER)\b")


def check_text(text: str, claims: list[dict], english: bool = True) -> tuple[Optional[str], Optional[str]]:
    """Return (blocking_reason, needs_human_reason) from the wording alone."""
    ph = _placeholder_re.search(text)
    if ph:
        return (f"Template leftover or missing value ('{ph.group(0)}'). Machinery must never reach air."), None
    state = _find(UNSUPPORTED_STATES, text)
    if state:
        return (f"Refers to a state the data can't contain ('{state}'). Event data records actions, "
                f"not feelings or fitness."), None
    allowed = _allowed_numbers(claims)
    for n in numbers_in(text, english_words=english):
        if round(n, 2) not in allowed and not any(abs(n - a) <= 0.01 for a in allowed):
            return f"The sentence says {n:g}, but none of its claims supports that number.", None
    pol = POLICY_WORDS.search(text)
    if pol:
        return None, (f"Editorial policy: '{pol.group(0)}' is inflammatory, speculative or betting language. "
                      f"A producer must approve or rewrite it.")
    res = RESULT_WORDS.search(text)
    if res:
        return None, (f"Talks about the result ('{res.group(0)}'). Numbers check out, but a producer should confirm "
                      f"the match is decided.")
    q = _find(QUALITATIVE, text)
    if q:
        return None, f"Numbers check out, but '{q}…' is a judgement. A producer should confirm the wording."
    return None, None


OUTCOME_WORDS = {"on target": ("save", "saved", "keeper", "goalkeeper", "on target"),
                 "off target": ("wide", "over the bar", "missed", "off target", "off-target"),
                 "goal": ("goal", "scored", "scores", "net"), "blocked": ("blocked",), "post": ("post", "woodwork"),
                 "red": ("red card", "sent off"), "yellow": ("yellow",)}
ZERO_WORDS = ("no ", "none", "zero", "without", "yet to", "still waiting", "nothing", "failed to", "not a single",
              "nenhum", "sem ", "akukho", "ngaphandle")


def grounded(text: str, claims: list[dict], english: bool = True) -> bool:
    """Does the sentence actually state at least one of the facts it claims? (stops true claims being stapled
    to unrelated or invented wording)"""
    low = text.lower()
    nums = set(numbers_in(text, english_words=english))
    for c in claims:
        v = c.get("value")
        if isinstance(v, bool):
            if v is False and any(w in low for w in ZERO_WORDS):
                return True
            continue
        if isinstance(v, (int, float)):
            if any(abs(n - float(v)) < 1e-6 or abs(n - round(float(v))) < 1e-6 for n in nums):
                return True
            if float(v) == 0 and any(w in low for w in ZERO_WORDS):
                return True
        elif isinstance(v, str) and len(v.strip()) >= 2:
            val = v.strip().lower().replace("_", " ")
            if val in low or any(w in low for w in OUTCOME_WORDS.get(val, ())):
                return True
            if c.get("metric") == "attribution" or c.get("key") in ("team",):
                continue
    return False


def verify_segment(store: Store, match_id: str, segment: dict, english: bool = True) -> dict:
    text = segment.get("text", "")
    claims = segment.get("claims") or ([segment["claim"]] if segment.get("claim") else [])
    results = []
    if not claims:
        block, _ = check_text(text, [], english)
        reason = block or "No claim attached: every factual sentence must carry a checkable claim."
        return {"verdict": BLOCKED, "reason": reason, "claims": []}
    block, human = check_text(text, claims, english)
    for c in claims:
        r = check_claim(store, match_id, c)
        results.append({
            "metric": r.metric, "params": r.params, "op": r.op, "value": r.value, "actual": r.actual,
            "ok": r.ok, "reason": r.reason, "sql": r.sql, "sql_params": r.sql_params, "rows": r.rows,
            "key": c.get("key"),
        })
    failed = [r for r in results if not r["ok"]]
    if failed:
        return {"verdict": BLOCKED, "reason": failed[0]["reason"], "claims": results}
    if block:
        return {"verdict": BLOCKED, "reason": block, "claims": results}
    if human:
        return {"verdict": NEEDS_HUMAN, "reason": human, "claims": results}
    if not grounded(text, claims, english):
        return {"verdict": NEEDS_HUMAN, "claims": results,
                "reason": "Its claims are true, but the sentence doesn't state any of them. Check the wording says "
                          "what the data shows."}
    return {"verdict": VERIFIED, "reason": "Every number re-computed from the event data.", "claims": results}


def verify_headline(text: str, segments: list[dict], english: bool = True) -> dict:
    all_claims = [c for s in segments for c in (s.get("claims") or [])]
    block, human = check_text(text, all_claims, english)
    if block:
        return {"verdict": BLOCKED, "reason": block}
    if human:
        return {"verdict": NEEDS_HUMAN, "reason": human}
    return {"verdict": VERIFIED, "reason": "Headline adds no numbers beyond the verified claims."}


def overall(verdicts: list[str]) -> str:
    if BLOCKED in verdicts:
        return BLOCKED
    if NEEDS_HUMAN in verdicts:
        return NEEDS_HUMAN
    return VERIFIED


def to_json(x: Any) -> str:
    return json.dumps(x, default=str)
