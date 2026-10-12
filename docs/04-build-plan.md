# 04 · Build Plan

> **Superseded (12 Oct):** the build now follows the 17-step agent-workflow plan (decisions D33–D39 in the decision log). This day-by-day plan is kept for history.

**Today:** Monday 12 October 2026
**Official deadline:** 27 October 2026, 11:59 PM Pacific = **Wednesday 28 October, 08:59 SAST**
**Our deadline:** **Monday 26 October, 20:00 SAST**
**Feature freeze:** Saturday 24 October, 12:00 SAST
**Top six announced:** by 10 November 2026

**Strategy (v2):** build one thin vertical slice end to end first (one match, one detector, one claim, one verdict, one approval), then widen it. No new Azure service until the slice works locally.

---

## Phases

| Phase | Dates | Goal | Exit criterion |
|---|---|---|---|
| 0 · Lock | Mon 12 Oct | Docs, repo, rules, dataset | Docs pushed; rules + dataset read |
| 1 · Vertical slice | Tue 13 – Thu 15 Oct | Replay → counterattack → Writer → verifier → approve | One verified storyline approved in the console, locally |
| 2 · Trust features | Fri 16 – Sun 18 Oct | All 3 detectors, ranking, "Why not?", hover-card, false-claim demo | Planted false claim blocked live with actual value shown |
| 3 · Packages + markets | Mon 19 – Wed 21 Oct | Packages, graphics payload, PT-BR, isiZulu | Lead story changes by market |
| 4 · Proof + cloud | Thu 22 – Fri 23 Oct | Benchmark suite, Trust tab, Azure deploy | Real numbers on Trust tab; deployed URL works |
| 5 · Ship | Sat 24 – Mon 26 Oct | Freeze, video, write-up, submit | Submitted by Mon 26 Oct 20:00 |

## Day by day

### Mon 12 Oct · Lock
- [x] Concept and docs v2
- [ ] Create repo `the-gallery`; push docs
- [ ] Read official rules → answer Q01–Q05 in the decision log
- [ ] Join Innovation Studio Platform; download synthetic dataset
- [ ] Create Foundry project + model deployment (only Azure item needed this week)

### Tue 13 Oct · Data
- [ ] Inspect dataset; write the adapter to the minimal `Event` interface
- [ ] Postgres schema; Docker Compose (db + api)
- [ ] Replay simulator with speed control
- [ ] Hand-built fixture match with one known counterattack (for tests)

### Wed 14 Oct · First detector + Writer
- [ ] Counterattack detector + unit tests on the fixture
- [ ] Evidence bundle builder
- [ ] Writer call with strict JSON schema (segments + claims), retry-once logic

### Thu 15 Oct · Verifier + console slice
- [ ] Claim kinds → SQL templates; verdict logic; policy block list; qualitative lexicon
- [ ] Console: timeline, one storyline card with inline verdicts, approve/reject, audit
- [ ] **Checkpoint: vertical slice works locally.** Record a 30-second screen capture as backup.

### Fri 16 Oct · Detectors + ranking
- [ ] Momentum shift and standout performer detectors + tests
- [ ] Editorial Director scoring (weights in config)

### Sat 17 Oct · Explainability UI
- [ ] "Why not?" panel from stored factor scores
- [ ] Evidence hover-card (SQL + rows)
- [ ] Edit → re-verify

### Sun 18 Oct · The demo moment
- [ ] Possession (or equivalent) metric so "dominated possession" can be checked and blocked
- [ ] Seed the demo match: blocked claim → retry → verified
- [ ] **Checkpoint:** false claim blocked live with the real value shown

### Mon 19 Oct · Packages
- [ ] Half-time / full-time package builder; 90-second countdown in the top bar
- [ ] Graphics payload JSON + HTML lower-third preview; copy button

### Tue 20 Oct · Markets
- [ ] Market profiles as data (EN, PT-BR, ZU)
- [ ] Market-aware re-ranking; Localisation output re-verified
- [ ] Market switch in the console

### Wed 21 Oct · Language QA
- [ ] Native-speaker review for Portuguese and isiZulu; glossary fixes
- [ ] **Checkpoint:** same match, different lead story per market

### Thu 22 Oct · Benchmark
- [ ] Pick 5 non-tuning matches; inject 10 patterns; save ground truth
- [ ] Write 20 false claims
- [ ] Evaluator + results table; commit method and results

### Fri 23 Oct · Trust tab + Azure
- [ ] Trust tab: truth/detected/missed bars, blocked claims with reasons, funnel, latency
- [ ] Deploy to Container Apps + Static Web Apps + Azure Database for PostgreSQL via `azd`
- [ ] Add one visible-value Azure extra if time allows (Fabric report or AI Search for glossary)

### Sat 24 Oct · Freeze at 12:00
- [ ] GitHub Actions green; deployed URL stable
- [ ] After noon: bugs and polish only

### Sun 25 Oct · Video
- [ ] Rehearse and record the demo (`05-demo-and-pitch.md`); architecture diagram image

### Mon 26 Oct · Submit by 20:00 SAST
- [ ] README final: setup, run, test, screenshots, benchmark numbers
- [ ] Submission form, video, repo link, deployed URL

### Tue 27 Oct · Buffer only

## Cut lines

| If by… | …this isn't done | Cut |
|---|---|---|
| Thu 15 Oct | Vertical slice | Drop to SQLite + no WebSocket (polling); keep everything else |
| Sun 18 Oct | All 3 detectors + "Why not?" | Ship 2 detectors; keep "Why not?" (it's cheap and memorable) |
| Wed 21 Oct | isiZulu quality acceptable | Ship EN + PT-BR live; show isiZulu as reviewed sample output, clearly labelled |
| Fri 23 Oct | Benchmark complete | 3 matches, 6 patterns, 20 false claims (false claims are the priority) |
| Sat 24 Oct | Azure deploy stable | Record from local; deploy best-effort; say so honestly in the write-up |

## Never cut
1. The verifier and the live blocked-claim moment
2. Evidence hover-cards
3. Real benchmark numbers (however small)
