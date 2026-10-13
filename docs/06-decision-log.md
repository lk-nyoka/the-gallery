# 06 · Decision Log

Record every decision that changes scope, design or pitch. Newest at the bottom.

## Locked decisions

| ID | Date | Decision | Why | Alternatives rejected |
|---|---|---|---|---|
| D01 | 12 Oct | Build **The Gallery**: an editorial control layer for live match coverage | Targets the real buyer (production team) and the brief's "explainable" requirement | MatchPulse OS / digital twin (unmeasurable states, platform with no user); Agent Referee and CoachGPT (off-brief audience); fan companion (competes with the Premier League's own Companion) |
| D02 | 12 Oct | Primary user is the **match producer** | One user done well beats a platform for everyone | Fans, coaches, referees |
| D03 | 12 Oct | **Numbers from code, words from the model** | Trust is the product; LLM arithmetic is unreliable | LLM-computed stats |
| D04 | 12 Oct | Fact-check gate: SQL verification for quantitative claims, LLM judge only for wording | Deterministic checks are provable | LLM-only verification |
| D05 | 12 Oct | Human approval required for anything in a package | Matches real broadcast practice; strong responsible-AI story | Fully automated output |
| D06 | 12 Oct | Markets: EN (world feed), PT-BR, isiZulu | Shows angle change + an under-served language | More markets (scope) |
| D07 | 12 Oct | Planted-pattern benchmark is in MVP, not stretch | Covers the "expand datasets" goal and gives a number no other team will have | Benchmark as stretch |
| D08 | 12 Oct | No invented qualitative states (fatigue, morale, chemistry) | Unmeasurable on the data; damages credibility | Digital-twin style states |
| D09 | 12 Oct | Internal deadline: Mon 26 Oct, 20:00 SAST | Official deadline is 28 Oct 08:59 SAST; keep buffer | Submitting last minute |
| D10 | 12 Oct | Event Hubs, voice, producer chat are stretch only | Protect core scope | — |
| D11 | 12 Oct | Positioning: **trust layer between AI match intelligence and live air**; tagline **"No evidence, no airtime."** | Owns the moat (preventing on-air errors) in one memorable line | "AI producer's assistant" (too small); "Editorial AI infrastructure" (too abstract to demo) |
| D12 | 12 Oct | Ranking is **deterministic scoring** (Editorial Director in code); the LLM only phrases the reasons | A trust product shouldn't let a model decide what matters; cheaper, faster, testable | LLM Storyline Agent; separate LLM "Editorial Director" agent |
| D13 | 12 Oct | Writer sees **only an evidence bundle** and emits **one claim per sentence** (strict JSON); retry once, then block the sentence | Makes safety structural instead of extracting claims afterwards | Free prose + claim extraction |
| D14 | 12 Oct | Verifier is **pure code** (SQL templates + policy list + qualitative lexicon); any future LLM judge can only downgrade | Milliseconds, deterministic, no new hallucination path | LLM fact-check agent |
| D15 | 12 Oct | Verdicts: ✅ true and proportionate · ⚠️ true but strong wording · ⛔ false, unclaimed or unsupportable state | Avoids implying an LLM can certify broadcast-safe language | Binary pass/fail |
| D16 | 12 Oct | **"Why not?"** panel, **evidence hover-cards** (SQL + rows), **graphics payload** export are MVP | Visible, cheap explainability; shows understanding of how graphics reach air | — |
| D17 | 12 Oct | **Local-first build**; MVP Azure = Foundry + PostgreSQL + Container Apps + Static Web Apps. Cosmos DB, AI Search, Fabric, Event Hubs only after the core loop works | Fragile breadth loses to reliable depth | Provisioning everything on day 1 |
| D18 | 12 Oct | Build order: **vertical slice first** (one detector end to end), 3 detectors before 5 | Proves the pipeline before widening it | Building layers horizontally |
| D19 | 12 Oct | Benchmark is a **fixed suite**: 5 matches, 10 injected patterns, 20 hand-written false claims; method and raw results published | Credible and finishable; supersedes the dynamic generator idea in D07 | Dynamic anomaly generator |
| D20 | 12 Oct | Localisation driven by **market profiles as data** (lead preference, depth, glossary, graphic emphasis, forbidden framings), output re-verified | Makes "angle, not translation" mechanical and visible | Prompt-only localisation; hardcoded demo outputs |
| D21 | 12 Oct | Demo **opens with a blocked claim**, captions readable on mute | First 10 seconds must land without explanation | Opening with problem statement |
| D22 | 12 Oct | Trust tab adds **editorial funnel** (insights → storylines → approved) | Shows AI reducing workload, not replacing editors | — |

| D23 | 12 Oct | **Free-tier only.** No paid services; the template writer is the default; any model is optional (Azure for Students credit or any OpenAI-compatible free tier) | Budget is zero | Paid Azure services by default |
| D24 | 12 Oct | **StatsBomb Open Data adapter** for private validation; engine score checked against the published result at full time | Proves the pipeline end to end on real event detail, free, with attribution | Paid live feeds; scraping |
| D25 | 12 Oct | **Live logger** for current matches (hand-logged events); pass-based metrics marked unavailable rather than guessed | No free event-level feed exists for current matches | Pretending the free score APIs are enough |
| D26 | 12 Oct | SQLite for the MVP store (schema kept Postgres-portable); single FastAPI app serves API + console; no build step | Runs on any laptop with Python | Docker/Postgres for day 1 |
| D27 | 12 Oct | If the model leaves a sentence unclaimed on its retry, keep it and block it visibly | Shows the gate working; never hides the model's mistake | Silently dropping it |

| D28 | 12 Oct | **Public match-feed adapter** (live or finished, polled every 20 s) | Exercises live-follow on real feeds | Hand logging as the main path; paid feeds |
| D29 | 12 Oct | **Three new differentiators** from competitor research: coverage check (ESPN/Alex Morgan failure), living verification (goal re-credits), placeholder guard (Lede AI failure) | Each answers a real, public failure of existing AI sports content | More detectors; more Azure services |
| D30 | 12 Oct | **Turning-point detector** (red card → before/after shots and goals) and official team stats as verifiable claims | Found a real match's turning point automatically in validation; lets the verifier check full-match possession | — |

| D31 | 12 Oct | **Organiser data is the submission path.** Generic file adapter (CSV/JSON, auto-detected columns, mapping file); ESPN and StatsBomb reframed as external validation; ESPN data git-ignored | Brief is about the organisers' synthetic data; avoids eligibility and IP risk | ESPN as the main source |
| D32 | 12 Oct | Live-follow is described as engineering-tested (staged replay + simulated correction), not production-proven, until a real live run succeeds | Don't claim what hasn't been shown | — |

| D33 | 12 Oct | **Synthetic data only in the submission** (official rule). Real-match adapters are off by default (`ENABLE_VALIDATION_SOURCES`), match ids live in a local `.env`, nothing fetched is committed; CI fails if it is | Rule compliance; no redistribution of league data | Real matches in the demo |
| D34 | 12 Oct | **Genuine agents on Microsoft Agent Framework**: Editor-in-Chief, Analyst, Writer, Desk, Corrections, Localiser, with a Producer human gate. Supersedes the "no Editorial Director agent" call in D12 / feedback triage | The rubric weights agentic design; each agent now has a job, tools and a typed contract, and the trust thesis is kept because only code returns verdicts | Labelling pipeline stages as agents |
| D35 | 12 Oct | **Verifier is a tool, not an agent**; Writer gets at most 2 revisions; blocked drafts are kept for the audit, never approvable | Agents can't grade their own work; bounded loops | Model-based self-critique |
| D36 | 12 Oct | Editor ranks each candidate **at its own detection time** (15-minute window, top 3 per window) and always commissions goals, red cards and turning points | Replay-speed independent; mandatory coverage | Ranking only against "now" |
| D37 | 12 Oct | **Fixtures A–C with truth files** (turning point, correction, coverage stress + red-team) replace a real match as the default demo | Known answers make every claim in the pitch measurable | Random synthetic seeds |
| D38 | 12 Oct | **MODEL_PROVIDER** switch: template (default) · Foundry Local (free, on-device) · Azure OpenAI · OpenAI-compatible; every failure falls back to templates | Free path that still uses Microsoft tech; safety doesn't depend on the model | A single hard-wired provider |
| D39 | 12 Oct | Corrections create a **new version that supersedes** the old story only on producer re-approval; both stay in the audit | Nothing wrong airs, nothing is silently rewritten | Editing the approved story in place |
| D40 | 12 Oct | **Foundry Local tested on the target laptop** (qwen2.5-0.5b, CPU, ~15 s per call). Only the Writer and Localiser call it by default; the last revision is deterministic; a grounding check flags true claims stapled to vague wording | The 0.5b model often returns invalid drafts or vague text; the run showed where the verifier needed tightening | Bigger local models (too slow / too much RAM on 7.7 GB) |
| D41 | 12 Oct | **Viewer personalisation as one more market** (`viewer`): casual fan / analyst, favourite club, player focus; text is a subset of verified sentences, never new text | Challenge stage (e) requires two genuinely different experiences; reusing the market machinery keeps every fact verified | A second model pass that rewrites per viewer |
| D42 | 12 Oct | **Readiness score, rights envelope, correction impact** (deterministic summaries) | Makes the producer's go/no-go and downstream risk visible; cheap, honest, testable | A full rights-management service |
| D43 | 12 Oct | **MCP server, read-only** | Lets Copilot and other agents reuse verified intelligence without any write path | Write tools (approve/publish) over MCP |
| D44 | 12 Oct | **Foundry Local in a child process, headline-only for small models** | In-process inference stalled the server; 0.5b can't follow the claim schema but can write a short headline the verifier can check | Dropping the on-device model; a bigger model the laptop can't run |
| D45 | 13 Oct | **Overlay feed + on-screen preview**: approved stories become timed cues; player tags fire on thresholds (shot ≥ 100 km/h, pass ≥ 40 m) | Challenge stage "render"; tags are computed from events so they need no approval | Free-text graphics written by the model |
| D46 | 13 Oct | **Pass quality and ball speed** as verifier metrics; synthetic speeds seeded per event id so existing fixtures don't change | Challenge feature list (pass quality, speed metrics); reproducible benchmark | Re-generating fixtures |
| D47 | 13 Oct | **Editorial policy words** (inflammatory, speculative, betting) need a producer | League-operated surfaces need house style, not just facts | Hard blocking (a producer may quote them in context) |
| D48 | 13 Oct | **Public demo on a free host** (Docker + Render blueprint), one sandbox per visitor, real-match sources forced off | Judges need a link that always works; visitors must not see each other's decisions | Shared single state; paid hosting |
| D49 | 13 Oct | **Production hardening** in one module (`production.py`): security headers and CSP, typed validation with ranges, body-size and per-visitor rate limits, generic 500s with request ids | A public link will be poked at; judges may open dev tools | A gateway or WAF (paid) |
| D50 | 13 Oct | **Observability without a paid service**: JSON logs, request ids, `/api/metrics`, liveness/readiness probes; Azure Application Insights optional | Free tier; one switch to Azure Monitor later | Mandatory telemetry dependency |
| D51 | 13 Oct | **Pinned lock file + pip-audit + Docker smoke test in CI**, non-root image | Reproducible builds; a broken image never reaches Render | Floating versions |
| D52 | 13 Oct | **Console retries and reconnects**, with a plain banner while a sleeping free instance wakes | Free hosting sleeps; first visitors shouldn't see an error | Paid always-on instance |
| D53 | 13 Oct | **The console sets itself up per user** (production, league/club, fan) with a first-visit welcome and a guided tour per role | Three very different users; one cluttered screen served none of them well | Separate apps |
| D54 | 13 Oct | **Fans see only proven sentences**, without producer approval, labelled as such | A fan companion has no producer; safety comes from the verifier, not the button | Auto-approving stories |
| D55 | 13 Oct | **Transparent browser-source graphics page + `?s=` session in links** | OBS, vMix, Wirecast and CasparCG all take a browser source, and none can set headers; writes still need the header | Native plugins per vendor |

## Open questions

| ID | Question | Owner | Due | Answer |
|---|---|---|---|---|
| Q01 | What are the prize categories and judging criteria? Can one entry target several? | Lindokuhle | Tue 13 Oct | |
| Q02 | Which Microsoft services are required or weighted? | Lindokuhle | Tue 13 Oct | |
| Q03 | Dataset schema: events only, or positions? xG included? | Lindokuhle | Tue 13 Oct | Adapter ready; needs the file |
| Q04 | Can organiser data be published in a public repo? | Lindokuhle | Tue 13 Oct | |
| Q05 | Submission format: video length, deployed URL, repo visibility? | Lindokuhle | Tue 13 Oct | Video under 2 min on a public platform, no trademarks or copyrighted music; public GitHub repo |
| Q06 | Solo or team? Who reviews isiZulu and Portuguese output? | Lindokuhle | Wed 14 Oct | |
| Q07 | Final tagline | Lindokuhle | Sat 24 Oct | |

## Feedback considered and not adopted (12 Oct)

| Suggestion | Source | Decision | Why |
|---|---|---|---|
| Knowledge graph of every event, claim and approval | Critique 2 | **Reduced** to evidence chains (walk linked events backwards), stretch only | A graph database adds a service and days of work; the visible benefit (expand a goal backwards) comes from linked events alone |
| Separate Editorial Director **agent** | Critique 2 | **Merged** into deterministic ranking (D12) | Another LLM step adds latency and contradicts the trust thesis |
| Distribution layer to fantasy, betting integrity, coaching | Critique 2 | **Roadmap slide only**; betting excluded entirely | Off-brief audiences; betting is a poor fit with a league co-host; scope |
| Expand to pre-match, breaking news, social, highlights now | Critique 2 | **Roadmap only** | One workflow done well beats many shown thinly |
| Contradiction detection between storylines | Critique 2 | **Stretch** | Good idea; needs the verifier finished first |
| Hardcode two polished localised outputs for the demo | Critique 3 | **Rejected** | Showing pre-written output as live would undermine a product about trust; we use reviewed glossaries and label any sample output clearly |
| Single flattened LLM call that returns insight + query | Critique 3 | **Partly adopted** | One Writer call per storyline (yes), but the model never writes the SQL; claims compile to fixed templates |
| "Push to Vizrt / Chyron" button | Critique 3 | **Adapted** as a vendor-neutral graphics payload export | We can't claim an integration we haven't built |
| Pre-set target like "98% precision" | Critique 3 | **Rejected** | Numbers in the pitch come only from the benchmark run |
| Expand scope to six consumer outputs | Summary note | **Rejected for build** | Scope; see above |

## Change log

| Date | Change | Reason |
|---|---|---|
| 12 Oct | Docs v1 created | Concept locked |
| 12 Oct | Docs v2: D11–D22, feedback triage; README, brief, architecture, build plan and demo script updated | Three external critiques reviewed |
| 12 Oct | Docs v3: D33–D39; README rewritten around the agent newsroom; demo script v3 on synthetic fixtures; real-match references removed from public docs | Official rules (synthetic data) and the 17-step agent plan |
| 13 Oct | D49–D52: production hardening; docs/09-operations.md and SECURITY.md added | "Make it production ready" |
| 13 Oct | D53–D55: role-based console, fan mode, graphics outputs and downloads; docs/10-user-guide.md | "Make it easy for the Premier League, fans and production companies" |
