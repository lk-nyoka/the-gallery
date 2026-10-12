# 02 · Business Case

**Status:** Locked (hypotheses marked 🧪 need validation) · 12 October 2026

---

## 1. Customer and buyer

| Segment | Who | What they buy The Gallery for | Priority |
|---|---|---|---|
| **League production** | The league's in-house studio/production arm (world feed, studio shows, digital) | Faster, verified storylines and recaps across every match, every matchday | 🥇 Beachhead |
| **International rights holders** | Broadcasters and streamers licensing matches in their territory | Market-specific coverage packages in their language without a full local research team | 🥈 Expansion |
| **Digital & social teams** | League and club content teams | Verified recap copy, social posts and clip cues minutes after the whistle | 🥉 Upsell |
| **Smaller leagues / federations** | Leagues without large production budgets | "Production team in a box" for lower-tier coverage | Later |

**Economic buyer:** Head of Production / Director of Broadcast Operations.
**Champion:** Match producers and editors.
**Blocker to win over:** Editorial standards / compliance (they care most about accuracy, which is our core feature).

## 2. Value proposition

| For the producer | For the organisation |
|---|---|
| The 3 stories that matter, ranked, in seconds | More output per match without more researchers |
| Every claim has its evidence one click away | Lower risk of on-air errors and corrections |
| Ready-to-use package at half-time and full-time | Consistent quality across many matches at once |
| Market versions without extra work | Better value from international rights: localised coverage for every territory |

**Value statement:** *The Gallery lets one production team cover more matches, in more markets, with fewer errors, without taking editorial control away from humans.*

## 3. Business model 🧪

Software licence sold to production organisations, priced by usage.

| Tier | Includes | Pricing basis |
|---|---|---|
| **Studio** | Live insights, fact-check gate, producer console, half-time/full-time packages, 1 language | Per match covered |
| **Global** | Studio + market localisation (per additional market/language) | Per match + per market |
| **Enterprise** | API access to verified insight stream, custom detectors, house style guide, SSO, audit export | Annual licence |

Prices are deliberately not set: they need validation with production teams. The pitch states the pricing **model**, not numbers.

**Unit-cost logic:** model inference per match is small relative to the cost of a researcher's time, because detectors are deterministic code and the LLM only writes and checks short texts. Cost scales with matches × markets, which is also how value scales.

## 4. Why we win (moat)

1. **Trust layer, not insight layer.** Insight generation is becoming a commodity. Verified, auditable editorial output isn't.
2. **The benchmark.** A repeatable way to measure accuracy on synthetic matches is something a buyer can audit, and something competitors have to replicate.
3. **Workflow lock-in.** Once the approve/edit/reject flow and the audit trail sit inside a production team's routine, switching is costly.
4. **Market localisation as editorial knowledge.** Market profiles (which players, which narratives, what tone) improve with use.

## 5. Competitive landscape

| Category | Examples | What they do | Our position |
|---|---|---|---|
| Data providers | Opta / Stats Perform, StatsBomb (Hudl), Genius Sports | Event and tracking data, stat feeds | We sit **on top** of data feeds; potential partners, not rivals |
| League fan AI | Premier League Companion (Copilot, Azure Foundry) | Fan-facing Q&A and personalisation | Consumer-facing; we are production-facing (B2B workflow) |
| Generic LLM tools | Copilot, ChatGPT, Claude, Gemini | Summaries and text | No evidence links, no fact-check gate, no broadcast workflow |
| Automated video | Pixellot, Veo, WSC Sports-type highlight tools | Automated recording, clips, highlights | Video-first; we're story-first and can feed clip cues to them |

## 6. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Synthetic dataset is events-only (no positions) | Medium | Limits spatial evidence | Detectors designed for events first; pitch graphics use event x/y if present |
| LLM writes an unsupported claim that slips past the fact-checker | Medium | High (core promise) | Structured claims checked by code against the event store; LLM-judge only for qualitative wording; benchmark tracks the rate |
| isiZulu output quality | Medium | Medium | Short, template-guided text; glossary of football terms; native-speaker check before recording the demo |
| Scope creep in 15 days | High | High | Non-goals list; stretch features only after 24 Oct |
| Azure costs/quota limits | Low–Medium | Medium | Small models for ranking/localisation, caching, replay instead of continuous live streams |
| Judges see it as "another dashboard" | Medium | High | Demo built around one producer's decisions and a visible fact-check block, not charts |
| Rules restrict data, services or submission format | Unknown | Medium | Read official rules before Day 3; update the decision log |

## 7. Go-to-market (narrative for judges)

1. **Pilot:** one studio show's half-time segment, shadow mode (The Gallery runs alongside the human team; compare outputs).
2. **Prove:** report approval rate, minutes saved, zero unsupported claims.
3. **Expand:** every match on a matchday → international feeds via market localisation.
4. **Platform:** expose the verified insight stream as an API for digital, social and partner apps.

## 8. Impact beyond the league

- **Accessibility and reach:** quality coverage in languages that rarely get it (isiZulu as the proof case), including community and regional broadcasters.
- **Responsible AI showcase:** a concrete pattern for putting generative AI into live media with human control, evidence and audit, reusable beyond football.
