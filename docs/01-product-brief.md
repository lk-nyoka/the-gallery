# 01 · Product Brief

**Status:** Locked · 12 October 2026
**Owner:** Lindokuhle Nyoka

---

## 1. One-line thesis

**The Gallery is the trust layer between AI match intelligence and live air: it turns a live event feed into evidence-linked, fact-checked, market-ready coverage that a producer can trust on air.**

**Tagline:** *No evidence, no airtime.*
**Rule:** *Numbers come from code. Words come from the model. Every word must point back to a number.*

The positioning is "trust layer"; the proof is one concrete workflow, the match producer's. We sell the principle, we demo the workflow.

## 2. Pitch versions

**10 seconds**
> AI can already spot what happened in a match. The Gallery decides what's worth saying, proves it's true, and adapts it for every market, before a producer puts it on air.

**30 seconds**
> At half-time a studio has minutes to choose its stories, and a wrong stat on air is a public mistake. Insight engines generate plenty of output, but nobody can broadcast output they can't verify. The Gallery ranks the storylines that matter, links every claim to the exact match events behind it, blocks anything unsupported, and produces versions tailored for different countries and languages. And we prove it works with a benchmark of synthetic matches with planted patterns.

## 3. Who it's for

| | Primary user | Secondary users |
|---|---|---|
| **Role** | Match producer / editor in a studio or production gallery | Presenters & pundits, graphics operators, social/digital editors, international rights-holder broadcasters |
| **Moment** | Live match, especially half-time and full-time | Pre-match prep, post-match recap, social clips |
| **Job to be done** | "Tell me the 3 stories that matter right now, prove each one, and give me what I need to put it on air." | "Give me the version of this match my audience cares about, in their language." |
| **Current workaround** | Researchers, data providers' stat feeds, notes passed to presenters, manual graphics requests | Generic world feed; translated commentary |
| **What they fear** | Wrong stat on air; missing the real story; being too slow | Generic coverage that ignores their market's players |

## 4. The problem, precisely

1. **Speed.** Storyline decisions happen in minutes, under pressure.
2. **Trust.** Generative AI output can't go on air unless every claim is traceable. "Explainable" is the requirement, not a feature.
3. **Scale across markets.** One match, many audiences. Translation alone misses the point: each market has different players and narratives that matter to it.

## 5. Why now / why this hackathon

- The brief asks for **explainable** match intelligence, real-time narratives, recaps, multilingual delivery, personalisation, and **dataset expansion**. The Gallery covers all six, with one coherent product rather than six features.
- Microsoft's own reference ("Synthetic Match Insights Engine for Premier League Studio") already covers insight detection, narratives and recaps. Most entries will rebuild it. We **build on top of that layer**: editorial workflow, evidence discipline, market localisation and measured accuracy.

## 6. Differentiation

| Likely common entry | The Gallery |
|---|---|
| Detects momentum, counterattacks, player patterns | Same detection, but every insight carries event IDs, a pitch graphic, confidence and a "why it matters now" note |
| LLM writes narratives and recaps | A fact-check gate verifies every sentence before a human sees it |
| Translates output into other languages | Changes the **editorial angle** per market, not just the language |
| "It works, trust us" | Planted-pattern benchmark: precision, recall, unsupported-claim rate |
| AI dashboard | A producer workflow: approve, edit, reject, schedule, with an audit trail |

## 7. Core features (MVP: must ship)

| # | Feature | Acceptance test |
|---|---|---|
| F1 | Replay of a synthetic match feed through a schema adapter | Match replays at 1×–20×; events stream into the console |
| F2 | 3 deterministic detectors first (momentum shift, counterattack, standout performer); pressing spell and goal-threat build-up after the core loop works | Each fires on benchmark matches with stored event IDs |
| F3 | Editorial Director: deterministic scoring and ranking | Top 3 storylines per phase; weights visible |
| F4 | **"Why not?"** panel | For any storyline, shows runner-up candidates and the factors that held them back |
| F5 | Writer that only sees an evidence bundle and returns one claim per sentence | Schema-valid output; sentences without claims are blocked |
| F6 | Deterministic verifier: ✅ verified · ⚠️ needs human · ⛔ blocked | Planted false claim blocked live in the demo, with the actual value shown |
| F7 | Evidence hover-card | Hovering a claim shows its SQL and the event rows returned |
| F8 | Producer approve / edit / reject with audit log; edits re-verified | Every decision stored with before/after |
| F9 | Half-time / full-time package with graphics payload (JSON + lower-third preview), recap, clip cues, social copy | Built inside the 90-second clock |
| F10 | Market localisation via data profiles: EN, PT-BR, then isiZulu | Lead story differs between markets on the same match |
| F11 | Fixed benchmark suite + Trust tab | Truth vs detected vs missed bars, false-claim block rate, editorial funnel, latency |

## 8. Stretch (only if MVP is done by 24 Oct)

- Evidence chains expanded backwards from a goal (if the data links events)
- Contradiction check between storylines
- Voice read-out of the localised package (Azure AI Speech)
- Cosmos DB live state, Azure AI Search for glossary and profiles, Fabric benchmark report
- Producer chat: "What are the three strongest stories right now?"

## 9. Explicit non-goals

- No real video processing or clip extraction (we output clip **cues**: timestamps and descriptions)
- No consumer fan app, accounts or social features
- No betting, predictions-for-wagering or odds
- No invented qualitative states (fatigue, morale, chemistry) unless the data actually contains them
- No more than 3 markets; no more than ~15 planted benchmark patterns

## 10. Success metrics

**For the hackathon**

| Metric | Target |
|---|---|
| Benchmark recall (planted patterns found) | ≥ 80% |
| Benchmark precision (detections that are real) | ≥ 80% |
| Planted false claims blocked or flagged | 20 / 20 |
| Unsupported claims marked ✅ verified | 0 in benchmark runs |
| Time from triggering event to ranked, verified storyline | < 30 s |
| Half-time package generation | < 90 s |
| Markets per package | 3 (EN, PT-BR, ZU) |

**For a real deployment (narrative for judges)**
- Researcher/producer minutes saved per match
- Share of AI-suggested storylines approved for air
- Corrections issued after broadcast (target: zero attributable to the system)
- Number of international feeds served per match

## 11. Guiding principles

1. **Numbers from code, words from the model.** No LLM-computed stats.
2. **No evidence, no claim.** Every sentence links to events or is blocked.
3. **Human on the button.** Nothing goes to air without producer approval.
4. **One user, done well.** We serve the producer; everything else is a demonstration of the same engine.
5. **Prove it.** If we can't measure it, we don't claim it in the pitch.
