# 10 · User guide

The Gallery sets itself up for whoever is using it. The first time you open it, it asks who you are; you can switch at any time with **Set up for** in the top bar, and **Tour** walks you through the screen in under a minute.

Live demo: https://the-gallery-dm6q.onrender.com · All clubs, players and matches are fictional.

---

## Production company or broadcaster

**What you get:** the full live desk.

1. **Play** a match (or connect your data feed). Events stream in and the chart shows who is on top.
2. **Stories arrive on their own.** The top three for the last 15 minutes are ranked by code. Click one.
3. **Check and approve.** Green = every number re-computed from the data. Amber = the numbers are right but a word is a judgement. Red = false or unsupported; it can never air. Hover a sentence for the exact query and events behind it. Press **Approve for air**.
4. **Put it on screen.** Press **Use in your graphics system** (under *On screen*):
   - **Browser source:** a transparent 1920×1080 page with score bug, lower thirds and player tags. In OBS: *Sources → + → Browser*, paste the link, width 1920, height 1080. In vMix: *Add Input → Web Browser*. Works the same in Wirecast and as a CasparCG HTML template. Add `&bug=0` to hide the score bug, `&bg=green` for a chroma key.
   - **Data feed:** the same cues as JSON (start time, duration, template, headline, text, team, fields) for Vizrt, Chyron, Ross or your own system.
5. **Half-time / Full-time** builds the package: presenter script, graphics data, clip in and out times, recap, social copy, rights terms, and a coverage check that every goal and red card has an approved story. Download it as **Script (.txt)**, **Graphics (.csv)** or **Everything (.json)**.

Other markets: **Show stories for** switches to the Brazil (Portuguese) or South Africa (isiZulu) feed, each with its own lead story. Translations are checked again.

## League or club media team

**What you get:** the same desk, with the checks a rights holder cares about up front.

- **Proof it works** opens with the **house rules**: the words that are always blocked (states the data can't show, like "exhausted"), the words that need a producer's tick (judgement, abuse, blame, betting, result words before full time), and where each feed may be shown.
- **Readiness** answers "can we go to the break?": enough approved stories, every goal and red card covered, nothing contradicted by a data correction.
- **Corrections:** when the data provider changes a fact, every approved story that relied on it is pulled and a corrected version waits for approval. Both versions are kept.
- **Decision log → Download (.csv):** every approval, edit, rejection and correction with time and actor, for compliance records.
- **Benchmark:** planted false claims stopped, unsupported claims that got through (zero), key moments covered.

## Fan

**What you get:** a simple match companion. No producer controls.

1. Press **Play**.
2. **Follow** a club or a player; their moments come first. **Simple** gives plain words and a one-line explainer; **All the stats** adds the numbers.
3. **The story right now** shows the biggest moment of the last 15 minutes. **Earlier in the match** lists the rest.
4. Tap any sentence to see the match data that proves it. Fans only ever see sentences the code has proven.

Works on a phone.

## Other sports

What is sport-agnostic today: the claim-and-verify model (every sentence carries a claim that SQL re-computes), the human approval step, corrections, packages, the overlay feed and the audit log.

What is football-specific: the event types (shot, box entry, card…), the detectors (momentum, counterattack, turning point…) and the writing templates. Adding a sport means an adapter for its feed, its event types and metrics in the verifier catalogue, and detectors for its key moments. The rest is reused unchanged.

## Your own data

**Load another… → Organiser data file** reads a CSV or JSON event file from `backend/data/organiser/` (columns are detected automatically; a `mapping.json` covers unusual names). **Log a match by hand** lets you log events with the keyboard while you watch.

## Troubleshooting

| You see | What it means |
|---|---|
| "Waking up the demo…" | The free server was asleep. It takes up to a minute; the page retries on its own. |
| "That match was cleared…" | The server restarted or your match sat idle for two hours. A fresh one opens automatically. |
| The graphics page says "That match isn't running" | Copy a fresh link from **Use in your graphics system**; links follow your current match. |
| A story won't approve | It has a red sentence. Strike it (the tick box is pre-set), or edit the wording and it's checked again. |
