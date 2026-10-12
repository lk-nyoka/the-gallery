# 07 · Test guide

Everything here runs on synthetic data: fictional clubs and players with known answers.

## 1. Automated checks

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check backend
pytest -q                                   # 53 tests
cd backend && python -m gallery.benchmark   # full benchmark, writes benchmark_results.json
python -m gallery.benchmark --quick         # the CI smoke test
```

The agent tests cover: typed contracts (empty drafts, unknown phases and agents are rejected), the verification tool (true draft verified; wrong number, missing claim, fatigue and template leftovers blocked), the Editor (only detected insights, mandatory moments always commissioned, every hold explained), the Analyst (evidence ids exist, read-only tools), the Writer revision loop (red-team caught then revised, gives up after 2), the Desk (commissions an uncovered red card), Corrections (fixture B: old story superseded only after re-approval, audit has both), the Localiser (3 variants per market) and the workflow trace end to end.

## 2. In the console

Run `run.bat` (Windows) or `python -m uvicorn gallery.app:app --port 8000` from `backend/`. It opens on fixture A.

| Test | Steps | What you should see |
|---|---|---|
| **Turning point** (fixture A) | ▶ Play at 60× | After 63', *"The red card that turned the match"* ranks #1; hover a sentence for the SQL and events |
| **Red-team** (fixture C) | *More sources* → Fixture C; press **Red-team next 2**; ▶ Play | Inspector → Agent trace: Verifier *blocked (v1)* → Writer *sent back* → Verifier *verified (v2)*. The card shows *v2 · revised* |
| **Correction** (fixture B) | Fixture B at 10–20×; approve the 29' goal story as soon as it's verified | Two minutes of match time later the feed re-credits the goal: the card turns red, the Inspector offers **Review the correction**; approving it marks the old one *superseded* |
| **Coverage** | Any fixture; press **Full-time** before approving anything | The package lists every goal, own goal and red card that has no approved story; the Desk trace says which drafts are waiting |
| **Markets** | Switch to *Brazil · PT* or *SA · isiZulu* | The Localiser picks that market's top stories; with no model they stay in English and say so |
| **Manual red-team** | Inspector → **Red-team: inject false claim** | A labelled false sentence is blocked with the reason |
| **Viewer modes** | **For you…** → Casual fan or Analyst, pick a club, follow a player | Casual: headline, 1–2 verified sentences and an explainer. Analyst: every verified sentence plus its numbers (hover for SQL). Player focus: that player's stories first and a live player card |
| **Readiness** | Watch the bar above the queue; approve stories | The half-time / full-time score rises as stories are approved and gaps close; a correction drops it |
| **Correction impact** | Fixture B, approve the 29' goal story | A red panel lists what the correction pulled: stories, graphics, drafts, localised versions, packages |
| **Rights envelope** | Build a package | The package drawer shows markets, surfaces, expiry and approval state |
| **MCP** | `python -m gallery.mcp_server` with an MCP client | Read-only tools return verified stories, provenance, gaps and readiness; no tool can approve |
| **Newsroom** | Newsroom tab | Workflow diagram, live trace, filter by agent, hide held |
| **Audit** | Audit log tab | Every decision, the correction, and the superseded row |

## 3. With a model (optional)

Set in `.env`:

```
MODEL_PROVIDER=foundry_local
FOUNDRY_MODEL=qwen2.5-0.5b
```

`run.bat` installs the Foundry Local SDK when it sees this. The badge in the top-right shows *starting → downloading → loading → Foundry Local*. Model failures appear in the trace as *fell back* and the template writer takes over; verdicts don't change.

## 4. Organiser data

Drop the organisers' files into `backend/data/organiser/` and pick them from *More sources → Organiser data file*. See `backend/data/organiser/README.md` for the column mapping.

## 5. Hand logger

*More sources → Log a match by hand* creates an empty match you log with the keyboard (H / A for team, 1–9 for actions). Useful for a quick live check; anything that needs pass data is honestly reported as unavailable and blocked if claimed.

## 6. Private validation (not for the repo or the video)

Adapters for public real-match feeds exist to check the engine against published results. They are off unless `ENABLE_VALIDATION_SOURCES=1` is set in your local `.env`; benchmark match ids go in `VALIDATION_ESPN_IDS` / `VALIDATION_STATSBOMB_IDS`. Nothing they fetch is committed, and they must be switched off before recording.
