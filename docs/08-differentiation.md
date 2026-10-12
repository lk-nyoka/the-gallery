# 08 · What others built, where it breaks, how The Gallery is different

**Status:** v1 · 12 October 2026

## 1. What already exists

| Product | What it does | Who it's for | Where it breaks |
|---|---|---|---|
| **Premier League Companion** (Microsoft Copilot, Azure Foundry) | Fans ask questions; answers from 30+ seasons of stats, articles and video | Fans in the PL app | Consumer Q&A. Not built for live production decisions, no producer approval step |
| **Bundesliga Match Facts** (DFL + AWS) | 12+ live stats on TV graphics and in the app, e.g. Win Probability, Pressure Handling, xGoals | Broadcasters, fans | Needs full tracking data (millions of data points per match). It produces numbers, not stories, and doesn't say which number matters right now |
| **Opta AI Studio** (Stats Perform) | Opta Search (stat research), Opta Live (live dashboard), Opta Graphics (branded graphics) | Broadcasters, media, sportsbooks | Mentions "verification" and "validation of narratives" but doesn't describe how. Expensive, enterprise-only, closed |
| **WSC Sports** | Automated highlight clips; gen-AI division for commentary translation, graphics, personalised clips | 450+ rights holders incl. ESPN | Video-first. No public mention of how generated text is checked |
| **IBM watsonx at Wimbledon** | Generative AI audio commentary and captions over highlight clips | Tournament digital platforms | Commentary over clips after the fact; no live editorial control |
| **ESPN AI recaps** (2024) | Generated match recaps, "each reviewed by a human editor" | ESPN readers | The recap of Alex Morgan's final professional match never mentioned her. Every sentence was true; the story was missing. Fixed the next morning after public criticism |
| **Lede AI for Gannett** (2023) | Automated high-school sports recaps | Local newspapers | Published template placeholders (`[[WINNING_TEAM_MASCOT]]`), repetitive phrasing, missing details. Paused across all markets |
| **Microsoft's reference "Synthetic Match Insights Engine"** | Hackathon starter: insights, narratives, recaps on Azure | Hackathon entrants | Most entries will rebuild this. It's our baseline, not our competition |

## 2. The patterns behind the failures

1. **True but incomplete.** ESPN's Morgan recap had no false sentence. It left out the only story that mattered. Fact-checking alone doesn't catch this.
2. **Machinery leaking onto the page.** Lede AI printed unfilled template slots. Nothing checked the output before it went out.
3. **Trust me, I'm AI.** Vendors say "verified" or "reviewed" without showing how. A producer can't see why a claim is true.
4. **Numbers without editorial judgement.** Match Facts and Opta Live give dozens of numbers; the producer still has to decide which one tells the story.
5. **Data changes after it's published.** Live stats are corrected: goals get re-credited to another player or ruled own goals after the match (the Premier League's own review panel does this). Approved graphics and scripts quietly go stale.
6. **Locked behind tracking data and enterprise prices.** The best tools need tracking cameras and Opta contracts. Smaller leagues, community broadcasters and new markets are shut out.

## 3. How The Gallery answers each one

| Failure | The Gallery's answer | Status |
|---|---|---|
| True but incomplete | **Coverage check.** Every key moment (goal, own goal, red card) must be covered by an approved story before a package is marked complete. Missing ones are listed: "Not covered: red card 63' (Kovac)" | Built |
| Machinery leaking | **Placeholder guard.** The verifier blocks unfilled slots (`[[…]]`, `{…}`, "None", "undefined") and unknown names before a human sees them | Building now |
| Trust me, I'm AI | **Evidence on hover.** Every sentence shows the query that re-computed it and the match events behind it. Ranking shows its weights; "Why not?" explains losing stories | Built |
| Numbers without judgement | **Code-scored ranking + turning points.** The engine ranks stories, and detects moments that changed the match (e.g. a red card, with shots and goals before vs after) | Ranking built · turning points building now |
| Data changes after publishing | **Living verification.** When the feed corrects an event, every story is re-checked; approved items that are now wrong turn red: "Correction needed" | Building now |
| Locked behind tracking data | **Runs on the data people actually have.** Any event feed through an adapter (CSV/JSON files, shot-level or full event data), hand logging, or the organisers' synthetic data. It states what each feed can't support instead of guessing | Built |

## 4. What a lot of people will build (and why we won't)

| Common idea | Why it's weak | Our position |
|---|---|---|
| "AI commentator" that talks over the match | Already shown (IBM, hackathon winners). Fun demo, no buyer, and it can't prove what it says | We write less, and prove all of it |
| Fan chatbot about the match | The Premier League already has one, built by Microsoft | We serve the people making the broadcast |
| Dashboard of advanced stats | Opta and Match Facts already do this, with better data | We pick the 3 stories that matter and say why |
| Automatic recap generator | ESPN and Lede AI show what goes wrong | Recaps only from approved, verified, complete stories |
| "Digital twin" with morale and fatigue scores | Invented numbers the data can't support | We block those words on purpose |

## 5. Holes in our own idea (and the fix)

| Hole | Fix |
|---|---|
| "Verified" can still be boring or unimportant | Ranking scores importance and novelty; the coverage check forces the big moments in |
| A producer is a narrow market | The verified story stream is the product; producer is the first user. Same stream feeds apps, social, partners later |
| Free public feeds are unofficial | Used only for private validation, never in the submission; the adapter interface takes licensed feeds or the organisers' data unchanged |
| Template text sounds robotic | Optional model writer, but its sentences still pass the same checks |
| Can't detect what isn't in the feed (e.g. a player's last match) | Producers can pin a story manually; the coverage check lists key moments the data does show |

## 6. One line for judges

> **Other tools generate stories. The Gallery proves them, notices the one you missed, and tells you when the facts change.**

## Sources

- Premier League × Microsoft partnership: https://www.premierleague.com/en/about/partners/microsoft
- Bundesliga Match Facts: https://amp.bundesliga.com/en/bundesliga/news/pressure-handling-win-probability-join-bundesliga-match-facts-powered-by-aws-21390
- Opta AI Studio: https://www.svgeurope.org/blog/news-roundup/stats-perform-launches-ai-fuelled-optaai-studio/
- WSC Sports gen-AI: https://www.sportico.com/business/tech/2024/ai-sports-highlights-creator-wsc-sports-1234782055/
- IBM at Wimbledon: https://aibusiness.com/data/wimbledon-brings-generative-ai-to-the-court-in-ibm-partnership
- ESPN AI recap and Alex Morgan: https://awfulannouncing.com/espn/alex-morgan-ai-generated-recap-mention.html
- Gannett / Lede AI: https://edition.cnn.com/2023/08/30/tech/gannett-ai-experiment-paused
- Goal re-credited after review (example): https://arseblog.news/2024/01/gabi-goals-premier-league-reverses-own-goal-ruling
