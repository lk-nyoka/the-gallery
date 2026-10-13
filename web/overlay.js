/* The Gallery: on-air graphics page (browser source for OBS, vMix, Wirecast, CasparCG). */
(() => {
  "use strict";
  const q = new URLSearchParams(location.search);
  const match = q.get("match"), session = q.get("s") || "", market = q.get("market") || "world";
  const $ = (id) => document.getElementById(id);
  const bg = q.get("bg");
  if (bg === "green" || bg === "preview") document.body.classList.add("bg-" + bg);

  if (!match) { $("setup").classList.remove("hidden"); return; }
  $("stage").classList.remove("hidden");
  if (q.get("bug") === "0") $("bug").classList.add("hidden");

  const KICKER = { goal_card: "Goal", card_alert: "Red card", before_after_split: "Turning point", momentum_chart: "Momentum",
    sequence_timeline: "Counterattack", player_card: "Standout", chance_timeline: "Pressure", speed_tag: "Power shot", lower_third: "Match story" };
  const code3 = (n) => (n || "").replace(/[^A-Za-z ]/g, "").split(" ")[0].slice(0, 3).toUpperCase();
  const fmt = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const firstSentence = (s) => (s || "").split(/(?<=\.)\s/)[0] || "";

  const st = { seen: new Set(), queue: [], showing: null, until: 0, feed: null, fails: 0, primed: false };

  async function poll() {
    try {
      const r = await fetch(`/api/matches/${encodeURIComponent(match)}/overlays?market=${encodeURIComponent(market)}&s=${encodeURIComponent(session)}`,
        { headers: session ? { "X-Gallery-Session": session } : {} });
      if (r.status === 404) throw new Error("That match isn't running. Copy a fresh link from the console.");
      if (!r.ok) throw new Error(r.status === 503 || r.status === 502 ? "Waking up the server…" : `Server error ${r.status}`);
      const fd = await r.json(); st.feed = fd; st.fails = 0; status("");
      const now = fd.now_t ?? 0;
      // on first load don't replay everything that already happened: only cues from the last few seconds
      const fresh = fd.cues.filter((c) => c.start_t <= now + 0.5 && !st.seen.has(c.cue_id));
      fresh.forEach((c) => st.seen.add(c.cue_id));
      const recent = st.primed ? fresh : fresh.filter((c) => now - c.start_t < 20);
      st.primed = true;
      st.queue.push(...recent.filter((c) => c.kind === "story"), ...recent.filter((c) => c.kind === "player_tag").slice(-2));
    } catch (e) {
      st.fails++;
      if (st.fails > 2 || /match isn't/.test(e.message)) status(e.message);
    }
    setTimeout(poll, 1500);
  }
  function status(text) { $("status").textContent = text; $("status").classList.toggle("hidden", !text || q.get("status") === "0"); }

  function tick() {
    const fd = st.feed;
    if (fd) {
      $("bHome").textContent = code3(fd.names.HOME); $("bAway").textContent = code3(fd.names.AWAY);
      $("bScore").textContent = `${fd.score.HOME}–${fd.score.AWAY}`; $("bClock").textContent = fmt(fd.now_clock ?? fd.now_t ?? 0);
    }
    const t = Date.now();
    if (t >= st.until) {
      const c = st.queue.shift() || null;
      st.showing = c; st.until = c ? t + (c.kind === "story" ? Math.min(9, c.duration_s) : 4) * 1000 : 0;
      render(c);
    }
  }
  function render(c) {
    const story = c && c.kind === "story" ? c : null, tag = c && c.kind === "player_tag" ? c : null;
    $("tag").classList.toggle("hidden", !tag);
    if (tag) { $("tagLabel").textContent = tag.fields.label; $("tagName").textContent = tag.player; $("tagVal").textContent = tag.fields.value; }
    $("l3").classList.toggle("hidden", !story);
    if (story) {
      $("l3Team").style.background = story.team_id === "HOME" ? "var(--home)" : "var(--away)";
      $("l3Kicker").textContent = `${KICKER[story.template] || "Match story"} · ${story.start_clock}`;
      $("l3Head").textContent = story.headline;
      $("l3Sub").textContent = firstSentence(story.text);
    }
  }
  poll();
  setInterval(tick, 250);
})();
