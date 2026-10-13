# 05 · Demo and Pitch

**Status:** Script v3 (agent workflow), 12 Oct 2026. Video rules: **under 2:00**, public URL (YouTube / Vimeo / Facebook Video / Youku), **no third-party trademarks or copyrighted music**. Use synthetic fixtures only.

---

## 1. Before recording

- In `.env`: remove `ENABLE_VALIDATION_SOURCES` (or set it to 0). The *Find a match* button and archive option must not be visible.
- Fictional clubs only (fixtures A, B, C). No real club names, logos, kits, broadcast footage or music. Silence or your own voice is safest.
- Browser zoom 110–125%, 1080p, close other tabs. Hide the bookmarks bar.
- If Foundry Local is on, start the app 5 minutes early so the model is loaded (badge shows *Foundry Local*). If it isn't ready, record on the template writer; the story is the same.
- Do a dry run of each fixture once; replay speeds below are what worked in testing.

## 2. Demo video (≈1:55)

Large on-screen captions throughout: judges may watch muted.

| Time | Screen | Voice-over / caption |
|---|---|---|
| 0:00–0:12 | Fixture C, Live desk. Press **Test tools → Plant 2 false sentences**, then ▶ Play at 60×. A story card arrives; Story details → How the agents made it shows **Verifier: blocked (v1)** · *"…looks exhausted…" → unsupported state* | "This AI newsroom just wrote a sentence the data can't support. It never reached the producer." |
| 0:12–0:30 | Same trace: **Writer: sent back for revision (1/2)** → **Verifier: verified (v2)** → **Producer: awaiting decision**. Hover a sentence: SQL + event rows | "Agents write. Code checks. Every sentence carries a claim that's re-computed from the match data. No evidence, no airtime." |
| 0:30–0:50 | **Newsroom** tab: the workflow diagram and the live trace (Editor-in-Chief commissions and holds, Analyst tool calls) | "Built on Microsoft Agent Framework: an Editor-in-Chief commissions, an Analyst gathers evidence with read-only tools, a Writer drafts, and a deterministic Verifier is the only one allowed to say what's true." |
| 0:50–1:10 | Fixture A at 60×: turning-point story *"The red card that turned the match"*; **Why not?** panel | "It finds the moment that mattered, ranks it with a visible formula, and explains why other stories lost." |
| 1:10–1:35 | Fixture B at 20×: approve the 29' goal story → feed re-credits the goal → card turns red → **Review the correction** → approve | "Facts change. When the feed re-credits a goal, the approved story is pulled, the Corrections agent drafts a fixed version, and the producer re-approves. Both versions stay in the audit." |
| 1:35–1:45 | Readiness bar at 100%; **Show stories for → A single fan…** → Casual fan following one player, then Analyst | "The same verified stories, shaped for a casual fan or an analyst, a favourite club or one player." |
| 1:45–1:55 | **Trust** tab: 0 escapes of 361, 37/37 stopped, 12/12 covered | "Measured, not claimed. The Gallery: no evidence, no airtime." |

Only show numbers from your own benchmark run on the recording day.

## 3. Submission write-up

**Name:** The Gallery

**One line:** A verification-first AI newsroom for live football: agents commission, research, write and correct stories; deterministic code proves every claim; a human approves what airs.

**What it does:**
The Gallery turns a match event feed into producer-ready coverage. Deterministic detectors find momentum shifts, counterattacks, standout performers, goals, red cards and turning points. A newsroom of agents built on Microsoft Agent Framework (Editor-in-Chief, Analyst, Writer, Desk, Corrections, Localiser) turns them into stories. Every factual sentence must carry a structured claim, and a deterministic Verifier re-computes it from the event data; blocked drafts go back to the Writer (max two revisions). A coverage check makes sure every goal and red card is covered, and when the feed corrects an event, approved stories are re-checked, pulled if wrong and replaced after re-approval.

**Microsoft technologies:** Microsoft Agent Framework (workflow, executors, conditional revision loop); Microsoft Foundry Local (on-device model via `foundry-local-sdk`, OpenAI-compatible endpoint); Azure OpenAI / Microsoft Foundry supported as a hosted provider through the same interface; GitHub Actions CI. *(Add GitHub Copilot only if you used it.)*

**What makes it different:**
1. Agents can't grade their own work: the only verdict comes from code.
2. True-but-incomplete is a failure: the coverage check catches missing moments.
3. Living verification: corrections re-check approved stories, with full version history.
4. Measured: planted-pattern fixtures and red-team drafts with known answers.

**Results:** 0 unsupported claims through (of 361 verified, independently re-computed) · 37/37 planted false claims stopped · 12/12 key moments covered · 1/1 correction caught and replaced · 29/32 planted patterns found.

**Data:** synthetic only; fictional clubs and players.

**What's next:** run the organisers' dataset through the file adapter; shadow-mode pilot on a studio half-time segment.

## 4. Judge Q&A prep

| Likely question | Answer |
|---|---|
| "Are these real agents or a pipeline with labels?" | Each agent has its own job, tools and contract, and they interact: the Editor holds stories, the Verifier sends drafts back, the Desk commissions gaps, Corrections supersedes approved stories. All of it is in the trace. |
| "Isn't it ironic that a trust product uses LLMs?" | The model does the least trusted job only: choosing and phrasing. Detection, scoring and verification are code; the model never sees raw data and can't approve anything. |
| "What if the model is down or tiny?" | Every agent falls back to a deterministic template. Verification is identical either way, so the safety properties don't depend on the model. |
| "How is this different from AI recap tools?" | They generate text. We prove each line, catch the missing story and re-check after corrections. |
| "Your benchmark uses your own synthetic data. Isn't that circular?" | Thresholds were tuned on separate seeds; patterns and false claims are planted afterwards with known answers, and verified claims are re-computed by separate code. It's a floor, not a guarantee. |
| "Why not just translate?" | Each market leads with a different story; the localised text is re-verified. |
| "Who pays?" | Production teams and rights holders, per match and per market. |
| "Real data?" | Any feed plugs in through an adapter into the same event model. The submission uses synthetic data only, as the rules require. |
