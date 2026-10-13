/* The Gallery: producer console. Vanilla JS, no build step. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ state
  const S = {
    matchId: null, snap: null, ws: null,
    events: [], insights: {}, storylines: {}, rankings: {}, funnel: {},
    market: "world", selected: null, tab: "gallery",
    team: "HOME", players: { HOME: new Set(), AWAY: new Set() }, logged: [],
    clock: { running: false, base: 0, startedAt: 0, period: 1 },
    whyNot: {}, pkg: null, pkgDeadline: 0, renderPending: false,
    trace: [], storyTrace: {}, provider: null,
    viewer: null, explainers: {}, playerCard: null, vMode: "casual",
  };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtMs = (ms) => (ms == null ? "–" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`);
  const fmt = (t) => { const m = Math.floor(t / 60), s = Math.floor(t % 60); return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; };
  const DET = { power_shot: "Power shot", turning_point: "Turning point", red_card: "Red card", goal: "Goal", momentum_shift: "Momentum shift", counterattack: "Counterattack", goal_threat: "Pressure, no goal", standout: "Standout performer" };
  const ICON = { verified: "✓", needs_human: "!", blocked: "✕" };
  const VLABEL = { verified: "Checked", needs_human: "Needs your eye", blocked: "Blocked" };
  const STATUS = { drafted: "Waiting for you", edited: "Edited, waiting", approved: "On air", rejected: "Rejected",
    needs_correction: "Facts changed", superseded: "Replaced" };

  // one sandbox per browser on the public demo (the server ignores this when run locally)
  const SESSION = (() => {
    let id = null;
    try { id = localStorage.getItem("gallery.session"); } catch (_) {}
    if (!id) { id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); try { localStorage.setItem("gallery.session", id); } catch (_) {} }
    return id;
  })();
  async function api(path, opts = {}) {
    const r = await fetch(path, { headers: { "Content-Type": "application/json", "X-Gallery-Session": SESSION }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).detail || msg; } catch (_) {} throw new Error(msg); }
    return r.json();
  }
  function toast(msg, bad = false) {
    const t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
  }
  function schedule() {
    if (S.renderPending) return; S.renderPending = true;
    const run = () => { S.renderPending = false; renderAll(); };
    if (document.hidden) setTimeout(run, 120); else requestAnimationFrame(run);   // rAF pauses in hidden tabs
  }

  // ------------------------------------------------------------------ matches + socket
  async function loadMatches(selectId) {
    const list = await api("/api/matches");
    if (!list.length) {
      let snap;
      snap = await api("/api/matches/default", { method: "POST" });
      return loadMatches(snap.match_id);
    }
    const sel = $("matchSelect");
    sel.innerHTML = list.map((m) => `<option value="${m.match_id}">${esc(m.names.HOME)} v ${esc(m.names.AWAY)} · ${({ espn: "ESPN (validation)", real: "archive (validation)", replay: "synthetic", fixture: "synthetic fixture", live: "hand-logged", file: "data file" })[m.mode] || m.mode}${m.date ? " · " + esc(m.date) : ""}${m.state === "in" ? " · LIVE" : ""}</option>`).join("");
    const id = selectId || S.matchId || list[0].match_id;
    sel.value = id; connect(id);
  }

  function connect(id) {
    if (S.ws) { S.ws.onclose = null; S.ws.close(); }
    Object.assign(S, { matchId: id, events: [], insights: {}, storylines: {}, rankings: {}, selected: null, whyNot: {}, viewer: null, playerCard: null });
    if (S.market === "viewer") { S.market = "world"; $("audienceSel").value = "world"; setAudienceHelp(); }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/${id}`);
    S.ws = ws;
    ws.onmessage = (m) => onMessage(JSON.parse(m.data));
    ws.onclose = () => setTimeout(() => S.matchId === id && connect(id), 1500);
  }

  function onMessage(msg) {
    switch (msg.type) {
      case "snapshot": {
        const s = msg.snapshot; S.snap = s;
        S.events = s.events; S.insights = Object.fromEntries(s.insights.map((i) => [i.insight_id, i]));
        S.storylines = Object.fromEntries(s.storylines.map((x) => [x.storyline_id, x]));
        S.rankings = s.rankings; S.funnel = s.funnel;
        S.events.forEach((e) => e.player && S.players[e.team_id]?.add(e.player));
        S.trace = s.trace || []; S.provider = s.provider; S.storyTrace = {};
        setupMode(); break;
      }
      case "trace": {
        const t = msg.trace; S.trace.push(t); if (S.trace.length > 600) S.trace.splice(0, S.trace.length - 600);
        const sel = S.storylines[S.selected];
        if (sel && (t.storyline_id === sel.storyline_id || (t.commission_id && t.commission_id === sel.commission_id))) refreshStoryTrace(sel.storyline_id);
        if (t.agent === "Producer" && t.action.startsWith("red-team")) toast("Red-team armed: the next drafts carry a labelled false sentence");
        break;
      }
      case "event":
        if (S.viewer?.player) refreshPlayer();
        S.events.push(msg.event); S.snap.now_t = msg.now_t; S.snap.now_clock = msg.now_clock; S.snap.score = msg.score;
        if (msg.event.player) S.players[msg.event.team_id]?.add(msg.event.player);
        break;
      case "undo":
        S.events = S.events.filter((e) => e.event_id !== msg.event_id); S.snap.score = msg.score; break;
      case "insight": S.insights[msg.insight.insight_id] = msg.insight; break;
      case "storyline": {
        const prev = S.storylines[msg.storyline.storyline_id];
        S.storylines[msg.storyline.storyline_id] = msg.storyline;
        const segs = msg.storyline.segments;
        if (prev && segs.length > prev.segments.length && segs[0].injected) flashCard(msg.storyline.storyline_id);
        break;
      }
      case "rankings": S.rankings = msg.rankings; S.funnel = msg.funnel; break;
      case "package": showPackage(msg.package); break;
      case "correction": {
        const c = msg.correction;
        const ev = S.events.find((e) => e.event_id === c.event_id);
        if (ev && c.after) Object.assign(ev, c.after);
        if (!c.after) S.events = S.events.filter((e) => e.event_id !== c.event_id);
        S.snap.score = msg.score; (S.snap.corrections ||= []).push(c);
        toast(`${c.reason.startsWith("SIMULATED") ? "Test correction" : "Data correction"}: ${c.flagged.length} approved stor${c.flagged.length === 1 ? "y" : "ies"} pulled${c.impact ? `, ${c.impact.graphics_invalidated} graphic(s) invalidated` : ""}`, c.flagged.length > 0);
        break;
      }
      case "live":
        S.snap.live = msg.live; if (msg.result_check) S.snap.result_check = msg.result_check; if (msg.score) S.snap.score = msg.score;
        break;
      case "replay_done":
        S.snap.result_check = msg.result_check; S.snap.replay = { running: false };
        toast(msg.result_check ? (msg.result_check.match ? "Full time: score matches the official result" : "Full time: score does NOT match the official result") : "Replay finished", msg.result_check && !msg.result_check.match);
        $("playBtn").textContent = "▶ Play"; break;
    }
    schedule();
  }

  function setupMode() {
    const live = S.snap.mode === "live";
    $("replayControls").classList.toggle("hidden", live);
    $("liveControls").classList.toggle("hidden", !live);
    $("loggerPanel").classList.toggle("hidden", !live);
    $("attrib").classList.toggle("hidden", !["real", "espn"].includes(S.snap.mode));
    $("attrib").textContent = S.snap.mode === "espn" ? "Data: ESPN match feed (unofficial, for testing)" : "Data: StatsBomb Open Data · github.com/statsbomb/open-data";
    const canReplay = ["replay", "real", "file", "fixture"].includes(S.snap.mode) || (S.snap.mode === "espn" && S.snap.meta?.state === "post");
    $("replayControls").classList.toggle("hidden", !canReplay);
    $("pickHome").textContent = S.snap.names.HOME; $("pickAway").textContent = S.snap.names.AWAY;
    renderProvider();
    $("playBtn").textContent = S.snap.replay?.running ? "❚❚ Pause" : "▶ Play";
    if (live && S.events.length) {
      const last = S.events[S.events.length - 1];
      S.clock = { running: false, base: last.t, startedAt: 0, period: last.period };
    }
    renderLogger();
  }

  // ------------------------------------------------------------------ clock
  function nowT() {
    if (S.snap?.mode !== "live") return S.snap?.now_clock ?? S.snap?.now_t ?? 0;
    const c = S.clock; return c.base + (c.running ? (Date.now() - c.startedAt) / 1000 : 0);
  }
  setInterval(() => {
    if (!S.snap) return;
    $("clock").textContent = S.snap.mode === "espn" && S.snap.live?.clock && S.snap.meta?.state === "in" ? S.snap.live.clock : fmt(nowT());
    renderLiveBadge();
    if (S.pkgDeadline) {
      const left = Math.max(0, Math.ceil((S.pkgDeadline - Date.now()) / 1000));
      $("htCountdown").textContent = left ? `· ${left}s` : "";
      if (!left) S.pkgDeadline = 0;
    }
  }, 250);

  // ------------------------------------------------------------------ render
  function renderAll() {
    if (!S.snap) return;
    const { names, score } = S.snap;
    $("homeName").textContent = names.HOME; $("awayName").textContent = names.AWAY;
    $("legHome").textContent = names.HOME; $("legAway").textContent = names.AWAY;
    $("score").textContent = `${score.HOME} – ${score.AWAY}`;
    const lastP = S.events.length ? S.events[S.events.length - 1].period : 1;
    $("period").textContent = S.snap.mode === "live" ? (S.clock.period === 2 ? "2nd half" : "1st half") : (lastP === 2 ? "2nd half" : "1st half");
    const rc = S.snap.result_check, rb = $("resultCheck");
    if (rc && rc.finished) {
      rb.className = "result " + (rc.match ? "ok" : "bad");
      rb.textContent = rc.match ? `✓ matches the result` : `✕ result was ${rc.official.HOME}–${rc.official.AWAY}`;
      rb.title = "At full time the score rebuilt from the events is compared with the fixture's known result.";
    } else rb.className = "result hidden";
    renderFeedPanel();
    renderFeed(); renderMomentum(); renderQueue(); renderInspector(); renderMiniTrace(); renderPipe();
    if (S.tab === "trust") renderTrust();
    if (S.tab === "newsroom") renderNewsroom();
    $("traceCount").textContent = S.trace.length ? S.trace.length : "";
  }

  const CAP_LABEL = { passes: "passes", regains: "ball regains", box_entries: "box entries", xg: "xG", shot_position: "shot positions" };
  const SOURCE_TITLE = { espn: "ESPN feed · external validation", real: "StatsBomb archive · validation", file: "Data file", replay: "Synthetic test match", fixture: "About this match data", live: "Hand-logged" };
  function renderFeedPanel() {
    const m = S.snap.meta || {}, mode = S.snap.mode, caps = S.snap.caps || {};
    $("feedPanel").classList.remove("hidden");
    $("feedTitle").textContent = SOURCE_TITLE[mode] || "Data source";
    const lv = S.snap.live || {};
    $("feedState").textContent = mode === "espn" ? (m.state === "in" ? `live · ${lv.clock || ""}` : m.status || "") : mode === "fixture" ? `${m.fixture} · ${m.title}` : (m.file || m.date || "");
    const base = ["shots & outcomes", "goals", "cards", "corners", "fouls"];
    const capRow = Object.entries(CAP_LABEL).map(([k, l]) => `<span class="cap ${caps[k] === false ? "no" : "yes"}">${l}</span>`).join("");
    const never = ["fatigue", "morale", "intent", "emotion"].map((x) => `<span class="cap no">${x}</span>`).join("");
    const rep = m.report;
    $("feedCaps").innerHTML = `<div class="caps">${base.map((c) => `<span class="cap yes">${c}</span>`).join("")}${capRow}</div>
      <div class="muted small" style="margin:6px 0 4px">Never claimed from any feed:</div><div class="caps">${never}</div>
      <div class="muted small" style="margin-top:6px">Crossed out = The Gallery blocks any sentence that needs it.</div>
      ${mode === "fixture" ? `<div class="small" style="margin-top:8px">${esc(m.description || "")}<br><span class="muted">Fictional clubs and players. Planted patterns and coverage obligations are known, so results are measurable.</span></div>` : ""}
      ${rep ? `<div class="small" style="margin-top:8px">File report: ${rep.rows} rows → ${rep.events} events · box entries ${esc(rep.box_entries)}${Object.keys(rep.skipped || {}).length ? ` · <span style="color:var(--warn)">skipped: ${esc(Object.entries(rep.skipped).map(([k, v]) => `${k} ×${v}`).join(", "))}</span>` : ""}</div>` : ""}`;
    $("teamStats").innerHTML = "";
  }
  function renderLiveBadge() {
    const b = $("liveBadge"), m = S.snap.meta || {}, lv = S.snap.live || {};
    if (S.snap.mode !== "espn" || !m.state || m.state === "post") { b.className = "livebadge hidden"; return; }
    const ago = lv.last_poll ? Math.max(0, Math.round(Date.now() / 1000 - lv.last_poll)) : null;
    b.className = "livebadge";
    b.textContent = m.state === "pre" ? "NOT STARTED · watching" : `● LIVE ${lv.clock || ""}${ago !== null ? ` · updated ${ago}s ago` : ""}${lv.error ? " · feed error, retrying" : ""}`;
  }

  function teamClass(tid) { return tid === "HOME" ? "home" : "away"; }

  function renderFeed() {
    const key = S.events.filter((e) => !["pass", "carry"].includes(e.type));
    const passes = S.events.length - key.length;
    $("eventCount").textContent = `${key.length} key${passes ? ` · ${passes} passes` : ""}`;
    const label = (e) => {
      const who = e.player ? ` · ${e.player}` : "";
      if (e.type === "shot") return (e.outcome === "goal" ? "GOAL" : `Shot (${e.outcome.replace("_", " ")})`) + who + (e.speed ? ` · ${Math.round(e.speed)} km/h` : "");
      if (e.type === "own_goal") return "OWN GOAL" + who;
      if (e.type === "card") return (e.outcome === "red" ? "RED CARD" : "Yellow card") + who;
      if (e.type === "key_pass") return "Chance created" + who;
      if (e.type === "period_end") return e.period === 2 ? "Full time" : "Half time";
      return ({ regain: "Regain", box_entry: "Box entry", corner: "Corner", foul: "Foul", kick_off: "Kick-off", period_end: "End of half", card: "Card", sub: "Sub" }[e.type] || e.type) + who;
    };
    $("feed").innerHTML = key.slice(-160).reverse().map((e) =>
      `<li class="${["shot", "box_entry", "own_goal", "card"].includes(e.type) ? "key" : ""}"><span class="m">${fmt(e.clock ?? e.t)}</span><span class="bar" style="background:var(--${teamClass(e.team_id)})"></span><span>${["period_end", "kick_off"].includes(e.type) ? "" : esc(S.snap.names[e.team_id]) + ": "}${esc(label(e))}</span></li>`).join("");
  }

  // ------------------------------------------------------------------ "who is on top" chart
  const W8 = (e) => (e.type === "shot" ? 2 : e.type === "box_entry" ? 1 : (e.type === "pass" && (e.end_x || 0) >= 66.7 ? 0.25 : 0));
  const minOf = (e) => Math.floor((e.clock ?? e.t) / 60);
  const surname = (p) => (p || "").split(" ").pop();
  function threatSeries(maxMin) {
    const per = { HOME: new Array(maxMin + 1).fill(0), AWAY: new Array(maxMin + 1).fill(0) };
    for (const e of S.events) { const w = W8(e); if (w && per[e.team_id]) per[e.team_id][Math.min(maxMin, minOf(e))] += w; }
    const roll = (a) => a.map((_, i) => a.slice(Math.max(0, i - 2), i + 1).reduce((x, y) => x + y, 0));
    return { raw: per, HOME: roll(per.HOME), AWAY: roll(per.AWAY) };
  }
  function keyMoments() {
    const out = [];
    for (const e of S.events) {
      if ((e.type === "shot" && e.outcome === "goal") || e.type === "own_goal") out.push({ kind: "goal", team: e.team_id, m: minOf(e) + 1, who: surname(e.player) + (e.type === "own_goal" ? " (o.g.)" : "") });
      if (e.type === "card" && e.outcome === "red") out.push({ kind: "red", team: e.team_id, m: minOf(e) + 1, who: surname(e.player) });
    }
    return out;
  }
  function renderMomentum() {
    const names = S.snap.names, now = nowT();
    const maxMin = Math.max(90, Math.ceil(now / 60));
    const ser = threatSeries(maxMin);
    const X0 = 10, X1 = 850, MID = 92, H = 78, W = X1 - X0, bw = W / (maxMin + 1);
    const peak = Math.max(4, ...ser.HOME, ...ser.AWAY);
    const x = (m) => X0 + (m / (maxMin + 1)) * W;
    let svg = `<rect x="${X0}" y="${MID - H - 2}" width="${W}" height="${H + 2}" fill="rgba(122,176,255,.04)"/><rect x="${X0}" y="${MID}" width="${W}" height="${H + 2}" fill="rgba(245,158,91,.04)"/>`;
    for (let i = 0; i <= maxMin; i++) {
      const hh = (ser.HOME[i] / peak) * (H - 4), ah = (ser.AWAY[i] / peak) * (H - 4), xx = x(i);
      if (hh) svg += `<rect x="${xx}" y="${MID - hh}" width="${Math.max(bw - 0.7, 0.6)}" height="${hh}" fill="var(--home)" opacity=".9"/>`;
      if (ah) svg += `<rect x="${xx}" y="${MID}" width="${Math.max(bw - 0.7, 0.6)}" height="${ah}" fill="var(--away)" opacity=".9"/>`;
    }
    svg += `<line x1="${X0}" y1="${MID}" x2="${X1}" y2="${MID}" stroke="var(--line-2)"/>`;
    // half-time and minute axis
    svg += `<line x1="${x(45)}" y1="${MID - H - 2}" x2="${x(45)}" y2="${MID + H + 2}" stroke="var(--line-2)" stroke-dasharray="2 3"/>`;
    for (const m of [0, 15, 30, 45, 60, 75, 90]) svg += `<text x="${x(m)}" y="${MID + H + 18}" text-anchor="${m === 0 ? "start" : m === 90 ? "end" : "middle"}">${m === 45 ? "HT" : m + "'"}</text>`;
    svg += `<text class="team-l" x="${X0 + 4}" y="${MID - H + 12}" fill="var(--home)">▲ ${esc(names.HOME)} attacking</text>`;
    svg += `<text class="team-l" x="${X0 + 4}" y="${MID + H - 4}" fill="var(--away)">▼ ${esc(names.AWAY)} attacking</text>`;
    // goals and red cards
    keyMoments().forEach((k, i) => {
      const xx = x(k.m - 0.5), top = k.team === "HOME", y = top ? MID - H + 26 + (i % 2) * 15 : MID + H - 24 - (i % 2) * 15;
      const icon = k.kind === "goal" ? `<circle cx="${xx}" cy="${y}" r="5" fill="var(--text)" stroke="#13171D" stroke-width="1.5"/>`
        : `<rect x="${xx - 3.5}" y="${y - 5}" width="7" height="10" rx="1.2" fill="var(--bad)"/>`;
      const anchor = xx > X1 - 90 ? "end" : "start", dx = anchor === "end" ? -8 : 8;
      svg += `<line x1="${xx}" y1="${MID}" x2="${xx}" y2="${y}" stroke="var(--line-2)" stroke-width=".6"/>${icon}<text class="mk-l" x="${xx + dx}" y="${y + 3}" text-anchor="${anchor}">${esc(k.who)} ${k.m}'</text>`;
    });
    const nx = x(now / 60);
    if (now > 0) svg += `<line x1="${nx}" y1="${MID - H - 2}" x2="${nx}" y2="${MID + H + 2}" stroke="var(--text)" stroke-width=".8" opacity=".55"/>`;
    $("momentum").innerHTML = svg;
    S._chart = { x0: X0, x1: X1, maxMin, ser };
    $("legHome").textContent = names.HOME; $("legAway").textContent = names.AWAY;
    $("momentumNow").innerHTML = threatSummary(now);
  }
  function windowStats(fromMin, toMin) {
    const st = { HOME: { threat: 0, shots: 0, box: 0, goals: 0 }, AWAY: { threat: 0, shots: 0, box: 0, goals: 0 } };
    for (const e of S.events) {
      const m = (e.clock ?? e.t) / 60; if (m < fromMin || m > toMin || !st[e.team_id]) continue;
      const t = st[e.team_id]; t.threat += W8(e);
      if (e.type === "shot") t.shots++; if (e.type === "box_entry") t.box++;
      if ((e.type === "shot" && e.outcome === "goal") || e.type === "own_goal") t.goals++;
    }
    return st;
  }
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  function threatSummary(now) {
    if (!S.events.length) return "Press Play. The chart fills in as the match happens.";
    const names = S.snap.names, m = now / 60;
    const last = windowStats(Math.max(0, m - 10), m), all = windowStats(0, m);
    const lt = last.HOME.threat + last.AWAY.threat, at = all.HOME.threat + all.AWAY.threat;
    const cls = (t) => (t === "HOME" ? "home-t" : "away-t");
    let head;
    if (lt < 1) head = `Quiet spell: no shots or box entries in the last 10 minutes.`;
    else {
      const top = last.HOME.threat >= last.AWAY.threat ? "HOME" : "AWAY", other = top === "HOME" ? "AWAY" : "HOME";
      const pct = Math.round((100 * last[top].threat) / lt);
      const verb = pct >= 75 ? "are pinning" : pct >= 60 ? "are on top of" : "are edging";
      head = `<b class="${cls(top)}">${esc(names[top])}</b> ${verb} ${esc(names[other])}: <b>${pct}%</b> of the attacking threat in the last 10 minutes (${plural(last[top].shots, "shot")}, ${plural(last[top].box, "box entry").replace("entrys", "entries")} against ${plural(last[other].shots, "shot")}).`;
    }
    const sub = at >= 1 ? `Whole match so far: <span class="home-t">${esc(names.HOME)} ${Math.round((100 * all.HOME.threat) / at)}%</span>, <span class="away-t">${esc(names.AWAY)} ${Math.round((100 * all.AWAY.threat) / at)}%</span>.` : "";
    return `${head}<span class="sub">${sub} Computed from the event feed, not written by AI.</span>`;
  }
  function chartHover(ev) {
    const c = S._chart, tip = $("momentumTip"); if (!c) return;
    const svg = $("momentum"), r = svg.getBoundingClientRect();
    const vx = ((ev.clientX - r.left) / r.width) * 860;
    if (vx < c.x0 || vx > c.x1) { tip.classList.add("hidden"); return; }
    const m = Math.min(c.maxMin, Math.max(0, Math.floor(((vx - c.x0) / (c.x1 - c.x0)) * (c.maxMin + 1))));
    const evs = S.events.filter((e) => minOf(e) === m && ["shot", "own_goal", "card", "box_entry"].includes(e.type));
    const lab = (e) => `${esc(S.snap.names[e.team_id])}: ${e.type === "shot" ? (e.outcome === "goal" ? "GOAL" : "shot " + (e.outcome || "").replace("_", " ")) : e.type === "own_goal" ? "own goal" : e.type === "card" ? e.outcome + " card" : "box entry"}${e.player ? " · " + esc(e.player) : ""}`;
    tip.innerHTML = `<b>${m + 1}'</b><span class="home-t">${esc(S.snap.names.HOME)} ${c.ser.HOME[m].toFixed(1)}</span> · <span class="away-t">${esc(S.snap.names.AWAY)} ${c.ser.AWAY[m].toFixed(1)}</span>
      <div class="muted small">threat over the last 3 minutes</div>${evs.slice(0, 5).map((e) => `<div>${lab(e)}</div>`).join("")}`;
    tip.classList.remove("hidden");
    const left = Math.min(r.width - 190, Math.max(0, ev.clientX - r.left + 12));
    tip.style.left = left + "px"; tip.style.top = "10px";
  }
  $("momentum").addEventListener("mousemove", chartHover);
  $("momentum").addEventListener("mouseleave", () => $("momentumTip").classList.add("hidden"));

  // ------------------------------------------------------------------ on-screen preview (overlay feed)
  const OV = { seen: new Set(), queue: [], showing: null, lastStory: null, until: 0, feed: null, match: null };
  const code3 = (n) => (n || "").replace(/[^A-Za-z ]/g, "").split(" ")[0].slice(0, 3).toUpperCase();
  async function pollOverlays() {
    try {
      if (S.matchId) {
        if (OV.match !== S.matchId) Object.assign(OV, { seen: new Set(), queue: [], showing: null, lastStory: null, until: 0, match: S.matchId });
        const market = S.market === "viewer" ? (S.viewer?.base_market || "world") : S.market;
        const fd = await api(`/api/matches/${S.matchId}/overlays?market=${market}`); OV.feed = fd;
        const now = S.snap?.now_t ?? 0;
        const fresh = fd.cues.filter((c) => c.start_t <= now + 0.5 && !OV.seen.has(c.cue_id));
        fresh.forEach((c) => OV.seen.add(c.cue_id));
        const stories = fresh.filter((c) => c.kind === "story"), tags = fresh.filter((c) => c.kind === "player_tag").slice(-2);
        OV.queue.push(...stories, ...tags);
      }
    } catch (_) {}
    setTimeout(pollOverlays, 1500);
  }
  function tickOverlay() {
    if (!S.snap) return;
    const n = S.snap.names || {};
    $("fbHome").textContent = code3(n.HOME); $("fbAway").textContent = code3(n.AWAY);
    $("fbScore").textContent = `${S.snap.score.HOME}–${S.snap.score.AWAY}`; $("fbClock").textContent = fmt(nowT());
    const t = Date.now();
    if (t >= OV.until && OV.queue.length) {
      const c = OV.queue.shift(); OV.showing = c; OV.until = t + (c.kind === "story" ? Math.min(7, c.duration_s) : 3) * 1000;
      if (c.kind === "story") OV.lastStory = c;
    } else if (t >= OV.until) OV.showing = null;
    const cur = OV.showing;
    const tag = cur && cur.kind === "player_tag" ? cur : null;
    $("fTag").classList.toggle("hidden", !tag);
    if (tag) { $("fTag").querySelector(".ptag-name").textContent = tag.player; $("fTag").querySelector(".ptag-val").textContent = tag.fields.value; }
    const story = cur && cur.kind === "story" ? cur : OV.lastStory;
    $("fL3").classList.toggle("hidden", !story); $("fL3").classList.toggle("stale", !!story && story !== cur);
    $("fEmpty").classList.toggle("hidden", !!story);
    if (story) {
      $("fL3").querySelector(".l3-team").style.background = story.team_id === "HOME" ? "var(--home)" : "var(--away)";
      $("fL3").querySelector(".l3-head").textContent = story.headline;
      $("fL3").querySelector(".l3-sub").textContent = story.text.split(/(?<=\.)\s/)[0] || "";
    }
  }
  setInterval(tickOverlay, 300);
  $("copyOverlay").onclick = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(OV.feed || {}, null, 2)); toast("Overlay feed copied"); } catch (_) { toast("Copy failed", true); }
  };

  // ------------------------------------------------------------------ pipeline strip
  function renderPipe() {
    const f = S.funnel || {}, st = Object.values(S.storylines).filter((x) => x.market === "world");
    const waiting = st.filter((x) => ["drafted", "edited"].includes(x.status) && x.overall !== "blocked").length;
    const onAir = st.filter((x) => x.status === "approved").length;
    const pulled = st.filter((x) => x.status === "needs_correction").length;
    const set = (step, text, cls) => { const li = document.querySelector(`.pipe li[data-step="${step}"]`); li.className = cls || ""; li.querySelector("span").textContent = text; };
    const n = S.events.length;
    set("feed", n ? `${n} events, ${fmt(nowT())} played` : "waiting for kick-off", n ? "live" : "");
    set("agents", f.storylines ? `${plural(f.storylines, "story")} from ${plural(f.insights, "moment")}`.replace("storys", "stories") : "watching for moments", f.storylines ? "live" : "");
    set("check", f.storylines ? `${f.verified || 0} checked, ${f.flagged || 0} need your eye, ${f.blocked || 0} blocked` : "nothing to check yet", f.flagged || f.blocked ? "attn" : f.storylines ? "live" : "");
    set("approve", pulled ? `${pulled} on air but facts changed` : waiting ? `${waiting} waiting for you, ${onAir} on air` : onAir ? `${onAir} on air` : "nothing waiting", pulled || waiting ? "attn" : onAir ? "live" : "");
    set("package", S.pkg ? `${S.pkg.label} package built` : "build one at half-time", S.pkg ? "live" : "");
  }

  // ------------------------------------------------------------------ help
  function openHelp() { $("help").classList.remove("hidden"); }
  $("helpBtn").onclick = openHelp;
  $("helpClose").onclick = () => { $("help").classList.add("hidden"); try { localStorage.setItem("gallery.helpSeen", "1"); } catch (_) {} };
  document.querySelectorAll(".pipe li").forEach((li) => (li.onclick = openHelp));
  try { if (!localStorage.getItem("gallery.helpSeen")) openHelp(); } catch (_) {}
  document.querySelectorAll("#toolsMenu .menu-item").forEach((b) => b.addEventListener("click", () => $("toolsMenu").removeAttribute("open")));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("help").classList.add("hidden"); $("toolsMenu").removeAttribute("open"); } });


  function storyFor(insightId) {
    const all = Object.values(S.storylines).filter((s) => s.insight_id === insightId && s.status !== "superseded")
      .sort((a, b) => b.created_at - a.created_at);
    return all.find((s) => s.market === S.market) || all.find((s) => s.market === "world");
  }

  function renderQueue() {
    const ranking = S.rankings[S.market] || [];
    const top = ranking.map((r) => ({ r, s: storyFor(r.insight_id) })).filter((x) => x.s && x.s.status !== "rejected").slice(0, 3)
      .map((x, i) => ({ ...x, r: { ...x.r, pos: i + 1 } }));
    $("queue").innerHTML = top.length ? top.map(({ r, s }) => card(s, r, false)).join("")
      : `<div class="empty">${S.events.length ? "Watching… no story in the last 15 minutes yet." : (S.snap.mode === "live" ? "Press Kick-off, then log events as you watch." : S.snap.mode === "real" ? `Real match: ${esc(S.snap.meta.date || "")} · ${esc(S.snap.meta.stadium || "")}. Press Play to replay it event by event.` : "Press Play to start the replay.")}</div>`;
    const f = S.funnel || {};
    $("queueNote").textContent = S.market === "viewer" && S.viewer
      ? `What ${fanLabel()} would see: the same checked stories, re-ordered and trimmed for them.`
      : S.market !== "world" ? `The strongest checked stories for the ${({ br: "Brazil", za: "South Africa" })[S.market]} feed. The lead story changes with the audience.`
      : "The three strongest checked stories from the last 15 minutes, ranked by code. Click one to review it.";
    $("funnel").textContent = `${f.insights || 0} insights → ${f.storylines || 0} storylines → ${f.verified || 0} verified · ${f.flagged || 0} flagged · ${f.blocked || 0} blocked → ${f.approved || 0} approved`;
    const rankById = Object.fromEntries((S.rankings[S.market] || []).map((r) => [r.insight_id, r]));
    const list = Object.values(S.storylines).filter((s) => s.market === "world").sort((a, b) => b.minute - a.minute || b.created_at - a.created_at);
    $("allStories").innerHTML = list.map((w) => card(S.market === "world" ? w : (storyFor(w.insight_id) || w), rankById[w.insight_id], true)).join("") || `<div class="empty">No storylines yet.</div>`;
  }

  function card(s, r, compact) {
    const team = S.snap.names[s.team_id];
    const v = S.market === "viewer" ? S.viewer : null;
    let shown = s.segments.map((g, i) => [g, i]);
    if (v && !compact) {
      shown = shown.filter(([g]) => g.verdict === "verified" && !g.struck);
      if (v.max_sentences) shown = shown.slice(0, v.max_sentences);
    }
    let segs = shown.map(([g, i]) => segRow(s, g, i, compact)).join("");
    if (v && !compact && v.mode === "casual" && S.explainers[s.detector]) segs += `<li class="explainer">${esc(S.explainers[s.detector])}</li>`;
    if (v && !compact && v.mode === "analyst") {
      const nums = shown.flatMap(([g]) => (g.checks || []).filter((c) => typeof c.actual === "number").map((c) => `<span class="num" title="${esc(sqlWithParams(c.sql || "", c.sql_params || []))}">${esc(numLabel(c))} <b>${esc(c.actual)}</b></span>`));
      if (nums.length) segs += `<li class="nums">${nums.join("")}</li>`;
    }
    const local = s.market !== "world" ? ` · ${esc(s.writer.startsWith("not localised") ? "EN (not localised)" : s.language)}` : "";
    const corr = s.status === "needs_correction" && s.correction ? `<div class="corr">⚠ Facts changed: ${esc(s.correction.detail)} <span class="muted">(${esc(s.correction.reason)})</span>${replacementOf(s) ? ` · <b>corrected version drafted</b>` : ""}</div>` : "";
    const ver = (s.version > 1 ? `<span class="pill warn" title="The checker rejected an earlier draft; the Writer rewrote it">rewritten ${s.version - 1}×</span>` : "")
      + (s.supersedes ? `<span class="pill bad" title="Replaces a story whose facts changed">corrected version</span>` : "")
      + (s.red_team ? `<span class="pill" title="Test: the first draft had a planted false sentence">test draft</span>` : "");
    const mine = v && ((v.club && s.team_id === v.club) || (v.player && r?.player_focus));
    return `<article class="card ${mine ? "mine" : ""} ${compact ? "compact" : ""} ${S.selected === s.storyline_id ? "sel" : ""} ${s.status === "needs_correction" ? "needs_correction" : ""}" data-sid="${s.storyline_id}" id="card-${s.storyline_id}">
      <div class="card-top">${r && !compact ? `<span class="rank" title="Position in the rundown. Ranked by code: importance, novelty, confidence, how well it shows on screen, and relevance to this audience.">${r.pos ?? r.rank}</span>` : ""}<span class="what">${DET[s.detector] || s.detector}</span><span>${s.minute}'</span>
        <span class="tag ${teamClass(s.team_id)}">${esc(team)}</span>${local}
        ${ver}<span class="status ${s.status}" title="${esc(STATUS_HELP[s.status] || "")}">${STATUS[s.status] || s.status}</span></div>
      <div class="headline">${esc(s.headline)} <span class="vchip ${s.overall}">${VLABEL[s.overall]}</span></div>${corr}
      ${compact ? "" : `<ul class="segs">${segs}</ul>${v ? "" : `<div class="card-foot"><span>Ready ${fmtMs(s.latency_ms)} after the moment</span><span>${writerName(s.writer)}</span><span>Video clip ${esc(s.clip_cue.from)}–${esc(s.clip_cue.to)}</span></div>`}`}
    </article>`;
  }

  function segRow(s, g, i, compact) {
    return `<li class="seg-row ${g.verdict} ${g.struck ? "struck" : ""}" data-sid="${s.storyline_id}" data-idx="${i}">
      <span class="v-icon" aria-label="${VLABEL[g.verdict]}">${ICON[g.verdict]}</span>
      <span class="v">${esc(g.text)}${g.injected ? `<span class="inj">planted test lie</span>` : ""}</span>
      ${g.verdict !== "verified" ? `<span class="why">${esc(g.reason)}</span>` : ""}
    </li>`;
  }

  const STATUS_HELP = { drafted: "Checked and waiting for you to approve or reject", edited: "You edited it; checked again and waiting",
    approved: "You approved it: it is on air", rejected: "You kept it off air", needs_correction: "The match data changed after you approved it",
    superseded: "Replaced by a corrected version" };
  function writerName(w) {
    if (!w || w === "template") return "templates";
    if (w.includes("headline")) return "templates, headline by the on-device model";
    if (w.startsWith("not localised")) return "templates (not translated: needs a model)";
    if (w.startsWith("template")) return "templates";
    return "AI model";
  }
  function fanLabel() {
    const v = S.viewer; if (!v) return "a fan";
    return `a ${v.mode === "analyst" ? "data-hungry analyst" : "casual fan"}${v.club ? ` who supports ${S.snap.names[v.club]}` : ""}${v.player ? `${v.club ? " and" : ""} follows ${v.player}` : ""}`;
  }
  function setAudienceHelp() {
    const m = $("audienceSel").value;
    $("audienceHelp").textContent = m === "viewer" && S.viewer ? `Showing what ${fanLabel()} would see. Pick again to change.`
      : m === "br" ? "Brazil's feed leads with attacking moments, in Portuguese."
      : m === "za" ? "South Africa's feed leads with players, in isiZulu."
      : "Same checked facts; the order and angle change per audience.";
  }
  function numLabel(c) {
    const team = S.snap.names[c.params?.team_id];
    const k = (c.key || c.metric).replace(/^(up|down|opp|team|home|away)_/, "").replace(/_/g, " ");
    return team && !/^(score|seconds|passes)/.test(k) ? `${team.split(" ")[0]} ${k}` : k;
  }
  function replacementOf(s) { return Object.values(S.storylines).find((x) => x.supersedes === s.storyline_id); }

  function flashCard(sid) {
    setTimeout(() => { const el = $("card-" + sid); if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); } }, 50);
  }

  // ------------------------------------------------------------------ inspector
  async function select(sid) {
    S.selected = sid; renderAll();
    refreshStoryTrace(sid);
    try { S.whyNot[sid + S.market] = await api(`/api/storylines/${sid}/why-not?market=${S.market}&phase=live`); } catch (_) { S.whyNot[sid + S.market] = []; }
    renderInspector();
  }

  function renderInspector() {
    const s = S.storylines[S.selected];
    if (!s) return;
    const r = (S.rankings[S.market] || []).find((x) => x.insight_id === s.insight_id);
    $("inspTitle").textContent = `${DET[s.detector]} · ${s.minute}' · ${s.market}`;
    const factors = r ? Object.entries(r.factors).map(([k, f]) =>
      `<div class="factor"><span>${k.replace("_", " ")} <span class="muted">×${f.weight}</span></span><span class="track"><span class="fill" style="width:${Math.round(f.value * 100)}%;display:block"></span></span><span class="muted">${f.value.toFixed(2)}</span></div>`).join("")
      : `<p class="muted small">Outside the live 15-minute window: not in the current ranking.</p>`;
    const wn = S.whyNot[s.storyline_id + S.market];
    const whyNot = wn === undefined ? `<p class="muted small">Loading…</p>` : !wn.length ? `<p class="muted small">No runner-up stories in this window.</p>` :
      wn.map((w) => `<div class="wn"><div class="wn-h"><span>${esc(w.title)} · ${w.minute}'</span><span class="muted">${w.score.toFixed(3)} vs ${w.chosen_score.toFixed(3)}</span></div>
        <div class="small muted">${esc(w.summary)}</div><ul>${w.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("");

    const segs = s.segments.map((g, i) => {
      let ctrl = "";
      if (g.verdict === "blocked" && !g.struck) ctrl = `<label class="ack" style="color:var(--bad)"><input type="checkbox" class="strike" data-idx="${i}" checked> strike this sentence before approval</label>`;
      if (g.verdict === "needs_human" && !g.struck) ctrl = `<label class="ack"><input type="checkbox" class="acknowledge" data-idx="${i}"> I've checked this wording</label>`;
      return `<li class="seg-row ${g.verdict} ${g.struck ? "struck" : ""}" data-sid="${s.storyline_id}" data-idx="${i}">
        <span class="v-icon">${ICON[g.verdict]}</span><span class="v">${esc(g.text)}${g.injected ? `<span class="inj">planted test lie</span>` : ""}</span>
        ${g.verdict !== "verified" ? `<span class="why">${esc(g.reason)}</span>` : ""}
        <span style="grid-column:2">${ctrl}<textarea class="edit-area hidden" data-idx="${i}" rows="2">${esc(g.text)}</textarea></span></li>`;
    }).join("");
    const hAck = s.headline_verdict === "needs_human" ? `<label class="ack"><input type="checkbox" class="acknowledge" data-idx="-1"> I've checked the headline wording</label>` : "";

    const repl = replacementOf(s);
    const banner = s.status === "superseded" ? `<div class="corr">Replaced by a corrected version. This one can no longer be approved. <button class="btn ghost small" data-goto="${s.superseded_by}">Open the correction</button></div>`
      : s.status === "needs_correction" ? `<div class="corr">⚠ The data behind this approved story changed. ${repl ? `The Corrections agent drafted v-next (${esc(repl.overall.replace("_", " "))}). Approve it to replace this one on air.` : "The Corrections agent is re-drafting it."} ${repl ? `<button class="btn ghost small" data-goto="${repl.storyline_id}">Review the correction</button>` : ""}</div>`
      : s.supersedes ? `<div class="corr fix">Correction of ${esc(s.supersedes)}. Approving it takes the old version off air. <button class="btn ghost small" data-goto="${s.supersedes}">See the old version</button></div>` : "";
    const commission = s.commissioned_by ? `<div class="small muted" style="margin-top:6px">Commissioned by <b>${esc(AGENT_LABEL[s.commissioned_by] || s.commissioned_by)}</b>: ${esc(s.brief || "")}${s.commission_reason ? ` <span class="faint">(${esc(s.commission_reason)})</span>` : ""}</div>` : "";
    $("inspBody").className = "insp";
    $("inspBody").innerHTML = `${banner}
      <div class="headline">${esc(s.headline)} ${["needs_correction", "superseded"].includes(s.status) ? `<span class="vchip blocked">contradicted by the data</span>` : `<span class="vchip ${s.headline_verdict}">${VLABEL[s.headline_verdict]}</span>`}</div>
      ${s.headline_verdict !== "verified" ? `<div class="small" style="color:var(--${s.headline_verdict === "blocked" ? "bad" : "warn"})">${esc(s.headline_reason)}</div>` : ""}
      ${hAck}
      <input class="edit-area hidden" id="editHeadline" value="${esc(s.headline)}">
      <h4>Is it true?</h4><p class="sub">Every sentence is checked against the match data. Hover one to see the evidence.</p>
      <ul class="segs">${segs}</ul>${commission}
      <div class="btnrow">
        <button class="btn primary" id="approveBtn" title="Put this story on air. Only checked sentences can go out." ${["approved", "superseded"].includes(s.status) || s.overall === "blocked" && !s.segments.some((g) => g.verdict === "blocked") ? "disabled" : ""}>${s.supersedes ? "Approve correction" : "Approve for air"}</button>
        <button class="btn ghost" id="editBtn" title="Change the wording. Your edit is checked again before it can go out.">Edit</button>
        <button class="btn bad" id="rejectBtn" title="Keep this story off air." ${s.status === "rejected" ? "disabled" : ""}>Reject</button>
      </div>
      <details class="explain"><summary>Test this story</summary>
        <div class="btnrow">
          <button class="btn warn" id="injectBtn" title="Adds a labelled false sentence to this story so you can see the checker block it">Plant a false sentence</button>
          ${["goal", "turning_point", "counterattack"].includes(s.detector) ? `<button class="btn warn" id="corrBtn" title="Pretend the league re-credits this goal to another player. If the story was approved, it is pulled and a corrected version is drafted.">Pretend the data changes</button>` : ""}
        </div></details>
      <h4>How the agents made it <span class="faint">${s.revisions ? `rewritten ${s.revisions}× before it passed` : "first draft passed the checker"}</span></h4>
      <ol class="trace story">${traceRows(S.storyTrace[s.storyline_id] || [], true) || `<li class="muted small">Loading…</li>`}</ol>
      <h4>Why it ranks here</h4><p class="sub">Each bar is one thing the ranking formula weighs. Longer is stronger.</p><div class="factors">${factors}</div>
      <h4>Why not the other stories?</h4><div class="whynot">${whyNot}</div>
      <h4>Broadcast details</h4>
      <div class="kv"><span>Status</span><span>${STATUS[s.status] || s.status}</span><span>Written by</span><span>${writerName(s.writer)}</span>
        <span>Ready after</span><span>${fmtMs(s.latency_ms)}</span><span>Graphic</span><span>${esc(s.graphic.template.replace(/_/g, " "))}</span>
        <span>Clip cue</span><span>${esc(s.clip_cue.from)}–${esc(s.clip_cue.to)} · ${esc(s.clip_cue.label)}</span>
        <span>Evidence</span><span>${s.graphic.event_ids.length} match events</span></div>`;
    $("approveBtn").onclick = () => approve(s);
    $("inspBody").querySelectorAll("[data-goto]").forEach((b) => (b.onclick = () => b.dataset.goto && select(b.dataset.goto)));
    $("rejectBtn").onclick = () => decide(s.storyline_id, { action: "reject" });
    $("editBtn").onclick = () => toggleEdit(s);
    if ($("corrBtn")) $("corrBtn").onclick = async () => { try { await api(`/api/storylines/${s.storyline_id}/simulate-correction`, { method: "POST" }); } catch (e) { toast(e.message, true); } };
    $("injectBtn").onclick = async () => { try { await api(`/api/storylines/${s.storyline_id}/inject`, { method: "POST", body: {} }); } catch (e) { toast(e.message, true); } };
  }

  async function approve(s) {
    const strike = [...document.querySelectorAll("#inspBody .strike:checked")].map((x) => +x.dataset.idx);
    const acknowledged = [...document.querySelectorAll("#inspBody .acknowledge:checked")].map((x) => +x.dataset.idx);
    try {
      if (strike.length) await api(`/api/storylines/${s.storyline_id}/decision`, { method: "POST", body: { action: "edit", edits: { strike } } });
      await api(`/api/storylines/${s.storyline_id}/decision`, { method: "POST", body: { action: "approve", acknowledged } });
      toast(s.supersedes ? "Correction approved: the old version is off air" : "Approved for air");
    } catch (e) { toast(e.message, true); }
  }
  async function decide(sid, body) {
    try { await api(`/api/storylines/${sid}/decision`, { method: "POST", body }); toast(body.action === "reject" ? "Rejected" : "Saved"); } catch (e) { toast(e.message, true); }
  }
  function toggleEdit(s) {
    const areas = document.querySelectorAll("#inspBody .edit-area");
    const editing = !areas[0].classList.contains("hidden");
    if (!editing) { areas.forEach((a) => a.classList.remove("hidden")); $("editBtn").textContent = "Save & re-verify"; return; }
    const segments = {};
    document.querySelectorAll("#inspBody textarea.edit-area").forEach((a) => { if (a.value !== s.segments[+a.dataset.idx].text) segments[a.dataset.idx] = a.value; });
    const edits = { segments };
    if ($("editHeadline").value !== s.headline) edits.headline = $("editHeadline").value;
    decide(s.storyline_id, { action: "edit", edits });
  }

  // ------------------------------------------------------------------ hover card (evidence)
  function sqlWithParams(sql, params) { let i = 0; return sql.replace(/\?/g, () => { const p = params[i++]; return typeof p === "string" ? `'${p}'` : String(p); }); }
  document.addEventListener("mouseover", (ev) => {
    const row = ev.target.closest(".seg-row");
    const hc = $("hovercard");
    if (!row) { hc.classList.add("hidden"); return; }
    const s = S.storylines[row.dataset.sid]; const g = s?.segments[+row.dataset.idx];
    if (!g) return;
    let html = `<h5 class="${g.verdict}">${VLABEL[g.verdict]}: ${esc(g.reason)}</h5>`;
    if (!g.checks || !g.checks.length) html += `<div class="muted">No structured claim attached, so nothing could be checked.</div>`;
    for (const c of g.checks || []) {
      html += `<div class="cmp"><span>The story says <b>${esc(numLabel(c))} ${c.op === "==" ? "=" : esc(c.op)} ${esc(c.value)}</b></span><span>The data says <b style="color:var(--${c.ok ? "ok" : "bad"})">${esc(c.actual ?? "not available")}</b></span></div>`;
      if (c.sql) html += `<pre>${esc(sqlWithParams(c.sql, c.sql_params || []))}</pre>`;
      if (c.rows && c.rows.length) {
        html += `<table><tr><th>min</th><th>team</th><th>type</th><th>outcome</th><th>player</th><th>id</th></tr>` +
          c.rows.slice(0, 8).map((r) => `<tr><td>${r.minute}'</td><td>${esc(S.snap.names[r.team_id] || r.team_id)}</td><td>${esc(r.type)}</td><td>${esc(r.outcome || "")}</td><td>${esc(r.player || "")}</td><td>${esc(r.event_id.split("-").pop())}</td></tr>`).join("") + `</table>`;
      }
    }
    hc.innerHTML = html; hc.classList.remove("hidden");
    const rect = row.getBoundingClientRect();
    let left = rect.left - 450; if (left < 8) left = Math.min(rect.right + 10, window.innerWidth - 450);
    hc.style.left = left + "px"; hc.style.top = Math.min(rect.top, window.innerHeight - hc.offsetHeight - 10) + "px";
  });

  // ------------------------------------------------------------------ packages
  async function buildPackage(kind) {
    try {
      if (S.market !== "world") await api(`/api/matches/${S.matchId}/localise`, { method: "POST", body: { market: S.market, phase: kind === "half_time" ? "first_half" : "full" } });
      if (kind === "half_time") S.pkgDeadline = Date.now() + 90000;
      await api(`/api/matches/${S.matchId}/packages`, { method: "POST", body: { kind, market: S.market } });
    } catch (e) { toast(e.message, true); }
  }
  function showPackage(p) {
    S.pkg = p; $("pkgDrawer").classList.remove("hidden");
    $("pkgTitle").textContent = `${p.label} package · ${p.scoreline}`;
    $("pkgMeta").textContent = `${p.market} · built in ${p.built_ms} ms · ${p.items.length} approved stories`;
    const items = p.items.map((i) => `<div class="pkg-item"><h4>${esc(i.headline)}${i.not_localised ? ` <span class="muted small">(not localised)</span>` : ""}</h4>
      <div>${esc(i.script)}</div><div class="small muted" style="margin-top:4px">Clip: ${esc(i.clip_cue.from)}–${esc(i.clip_cue.to)} · ${esc(i.clip_cue.label)}</div>
      <div class="lowerthird"><span class="lt-a">${esc(i.graphic_payload.fields.team || "")}</span><span class="lt-b">${esc(i.headline)}</span></div></div>`).join("");
    const cov = p.coverage || { moments: [], missing: [] };
    const covHtml = `<div class="coverage ${cov.missing.length ? "bad" : ""}"><b>Coverage check: ${esc(cov.summary || "")}</b>
      <ul style="margin:4px 0 0;padding-left:16px">${cov.moments.map((m) => `<li class="${m.covered ? "ok" : "miss"}">${m.covered ? "✓" : "✕ not covered:"} ${esc(m.label)} ${m.minute}' · ${esc(m.player || "")} (${esc(m.team)})</li>`).join("") || "<li class='muted'>No goals or red cards yet.</li>"}</ul>
      ${cov.missing.length ? `<div class="small muted" style="margin-top:4px">Approve a story for each missing moment before this goes to air.</div>` : ""}</div>
      ${(p.needs_correction || []).length ? `<div class="corr">⚠ ${p.needs_correction.length} approved stor${p.needs_correction.length === 1 ? "y is" : "ies are"} now contradicted by the data and left out: ${esc(p.needs_correction.join("; "))}</div>` : ""}`;
    $("pkgBody").innerHTML = `<div>${covHtml}${p.note ? `<div class="empty">${esc(p.note)}</div>` : items}</div>
      <div><h4 style="margin-top:0">Recap</h4><div>${esc(p.recap)}</div><h4>Social copy</h4><div>${esc(p.social)}</div>
      <h4>Rights envelope</h4><div class="code">${esc(JSON.stringify(p.rights || {}, null, 2))}</div><h4>Graphics payload (JSON)</h4><div class="code">${esc(JSON.stringify(p.items.map((i) => i.graphic_payload), null, 2))}</div></div>`;
  }
  $("copyPayload").onclick = async () => {
    if (!S.pkg) return;
    try { await navigator.clipboard.writeText(JSON.stringify(S.pkg.items.map((i) => i.graphic_payload), null, 2)); toast("Graphics payload copied"); } catch (_) { toast("Copy failed", true); }
  };
  $("closePkg").onclick = () => { $("pkgDrawer").classList.add("hidden"); S.pkgDeadline = 0; $("htCountdown").textContent = ""; };
  $("htBtn").onclick = () => buildPackage("half_time");
  $("ftBtn").onclick = () => buildPackage("full_time");

  // ------------------------------------------------------------------ trust tab
  let bench = null;
  async function loadBench(refresh = false) {
    $("trust").innerHTML = `<div class="panel"><p class="muted">${refresh ? "Re-running the benchmark…" : "Loading benchmark…"}</p></div>`;
    try { bench = await api(`/api/benchmark${refresh ? "?refresh=true" : ""}`); } catch (e) { bench = null; toast(e.message, true); }
    renderTrust();
  }
  function metric(label, value, note, good) {
    return `<div class="metric"><div class="mv ${good === true ? "ok" : good === false ? "bad" : ""}">${esc(value)}</div><div class="ml">${esc(label)}</div><div class="mn">${esc(note)}</div></div>`;
  }
  function blocks(found, total, flagged = 0) {
    let h = ""; for (let i = 0; i < total; i++) h += `<span class="blk ${i < found - flagged ? "" : i < found ? "flag" : "miss"}"></span>`; return `<span class="blocks">${h}</span>`;
  }
  function renderTrust() {
    if (!bench) return;
    const d = bench.detection, f = bench.false_claims, v = bench.verified_claims, l = bench.latency;
    const fl = S.funnel || {}; const maxF = Math.max(1, fl.insights || 0);
    const frow = (k, n) => `<div class="fr"><span>${k}</span><span class="fb" style="width:${Math.round(100 * (n || 0) / maxF)}%"></span><span>${n || 0}</span></div>`;
    const recent = (bench.recent_matches || []).filter((r) => !r.error);
    const recentRows = recent.map((r) => `<div class="real-row"><span><b>${esc(r.official)}</b> · ${esc(r.date)} · ESPN feed<br><span class="muted small">Goals found ${r.goals_detected}/${r.goals_official}: ${esc(r.scorers.join(", "))}<br>Storylines verified ${r.verified_storylines}/${r.storylines}${r.turning_points.length ? "<br>Found: " + esc(r.turning_points[0]) : ""}</span></span><span class="${r.result_matches ? "ok" : "bad"}">${r.result_matches ? "✓ match" : "✕"}</span></div>`).join("");
    const real = (bench.real_matches || []).filter((r) => !r.error);
    const realCard = `<div class="panel"><div class="panel-h"><span>Real matches</span><span class="muted small">ESPN feed + StatsBomb Open Data</span></div>
        <div class="stat-big">${real.filter((r) => r.result_matches).length + recent.filter((r) => r.result_matches).length}/${real.length + recent.length}</div>
        <div class="small muted">final scores rebuilt from events match the official result</div>
        ${recentRows}${real.map((r) => `<div class="real-row"><span><b>${esc(r.official)}</b> · ${esc(r.date)}<br><span class="muted small">Goals found ${r.goals_detected}/${r.goals_official}: ${esc(r.scorers.join(", "))}<br>Storylines verified ${r.verified_storylines}/${r.storylines} · ${r.claims_rechecked} claims re-checked, ${r.escapes} escapes · false claims caught ${r.false_claims_caught}/${r.false_claims}</span></span><span class="${r.result_matches ? "ok" : "bad"}">${r.result_matches ? "✓ match" : "✕"}</span></div>`).join("") || `<p class="muted small">Real data not available (offline?).</p>`}</div>`;
    const aw = bench.agent_workflow;
    const pct = (a, b) => b ? `${a}/${b}` : "–";
    const agentCard = aw ? `<div class="panel span2"><div class="panel-h"><span>Agent workflow · synthetic fixtures</span><span class="muted small">deterministic template writer, so anyone can reproduce it</span></div>
        <div class="metrics">
          ${metric("Unsupported claims that got through", aw.unsupported_claim_escape_rate.escapes, `of ${aw.unsupported_claim_escape_rate.verified_claims} verified claims, re-computed by separate code`, aw.unsupported_claim_escape_rate.escapes === 0)}
          ${metric("False claims stopped", pct(aw.false_claim_block_rate.caught, aw.false_claim_block_rate.planted), "blocked, or flagged for a human; incl. red-team drafts", aw.false_claim_block_rate.caught === aw.false_claim_block_rate.planted)}
          ${metric("Verdict accuracy", pct(aw.claim_verdict_accuracy.correct, aw.claim_verdict_accuracy.total), "verifier verdicts that were right", aw.claim_verdict_accuracy.correct === aw.claim_verdict_accuracy.total)}
          ${metric("Key moments covered", pct(aw.coverage_rate.covered, aw.coverage_rate.obligations), "every goal, own goal and red card", aw.coverage_rate.covered === aw.coverage_rate.obligations)}
          ${metric("Corrections caught", pct(aw.correction_detection_rate.detected, aw.correction_detection_rate.changed_facts), `${aw.correction_detection_rate.replacements_verified} replacement(s) verified`, aw.correction_detection_rate.detected === aw.correction_detection_rate.changed_facts)}
          ${metric("Red-team drafts revised", pct(aw.red_team_writer.revised_to_verified, aw.red_team_writer.drafts), `${aw.red_team_writer.caught_on_first_draft} caught on the first draft`, aw.red_team_writer.revised_to_verified === aw.red_team_writer.drafts)}
          ${metric("Patterns found", pct(aw.pattern_recall.found, aw.pattern_recall.planted), `${aw.pattern_precision.right_team}/${aw.pattern_precision.detections_in_planted_windows} detections in planted windows credited to the right team`)}
          ${metric("Event → verified story", `${aw.median_time_to_verified_storyline_ms} ms`, `package build ${aw.median_time_to_package_ms} ms (median)`)}
        </div>
        <div class="fxrows">${bench.fixtures.map((r) => `<div class="real-row"><span><b>Fixture ${r.fixture} · ${esc(r.title)}</b> · ${esc(r.teams)}<br><span class="muted small">Score ${r.final_score.HOME}–${r.final_score.AWAY} ${r.score_matches ? "= script" : "≠ script"} · covered ${r.covered}/${r.obligations} · red-team ${r.red_team.revised_to_verified}/${r.red_team.drafts} revised · corrections ${r.corrections.filter((c) => c.replacement_verified).length}/${r.corrections.length} · editor held ${r.held_by_editor} · revisions ${r.revisions}</span></span><span class="${r.score_matches && !r.missing.length ? "ok" : "bad"}">${r.score_matches && !r.missing.length ? "✓" : "✕"}</span></div>`).join("")}</div></div>` : "";
    const safe = `<div class="panel"><div class="panel-h"><span>Why this is safe</span></div><ul class="safe">
        <li><b>Numbers come from code.</b> Detectors and fixed SQL templates compute every figure. The model never calculates.</li>
        <li><b>Agents can't mark their own work true.</b> Only the verification tool returns a verdict, and it is plain code.</li>
        <li><b>No claim, no sentence.</b> A sentence without a checkable claim, with an unmatched number, or about fatigue, morale or intent is blocked.</li>
        <li><b>A human approves everything.</b> Blocked text can't be approved; judgement wording needs an explicit tick.</li>
        <li><b>Facts can change.</b> Every approved story is re-checked after a data correction; a wrong one is pulled and a corrected version drafted.</li>
        <li><b>Synthetic data.</b> Fictional clubs and players; no real league data is stored or published.</li></ul></div>`;
    $("trust").innerHTML = agentCard + safe + (real.length + recent.length ? realCard : "") + `
      <div class="panel"><div class="panel-h"><span>Planted patterns found</span><span class="muted small">${bench.seeds.length} synthetic matches</span></div>
        <div class="stat-big">${d.found}/${d.planted}</div>
        <div class="bars">${Object.entries(d.by_type).map(([k, x]) => `<div class="barrow"><span>${DET[k] || k}</span>${blocks(x.found, x.planted)}<span>${x.found}/${x.planted}</span></div>`).join("")}</div>
        <p class="small muted">Green: found. Red outline: missed. ${d.extra_detections} further detections were naturally occurring patterns. Thresholds were tuned on separate matches (${esc(bench.tuning_seeds)}).</p></div>
      <div class="panel"><div class="panel-h"><span>False claims stopped</span><span class="muted small">labelled tests</span></div>
        <div class="stat-big">${f.caught}/${f.injected}</div>
        <div class="bars">${Object.entries(f.by_kind).map(([k, x]) => `<div class="barrow"><span>${k.replace(/_/g, " ")}</span>${blocks(x.caught, x.injected, x.flagged)}<span>${x.caught}/${x.injected}</span></div>`).join("")}</div>
        <p class="small muted">Green: blocked. Amber: flagged for a human (true numbers, judgement wording).</p></div>
      <div class="panel"><div class="panel-h"><span>Unsupported claims that got through</span></div>
        <div class="stat-big">${v.escapes}</div>
        <p class="small muted">Every claim marked verified (${v.rechecked_independently} in total) was re-computed with separate Python code, not the verifier's SQL.</p>
        <h4 class="small muted">Examples of what was stopped</h4>
        ${f.examples.slice(0, 4).map((x) => `<div class="ex ${x.verdict === "needs_human" ? "flagged" : ""}"><div>${esc(x.text)}</div><div class="r">${esc(x.reason)}</div></div>`).join("")}</div>
      <div class="panel"><div class="panel-h"><span>Speed</span></div>
        <div class="kv"><span>Event → verified story</span><span>${l.median_storyline_ms} ms median · ${l.p95_storyline_ms} ms p95</span>
          <span>Package build</span><span>${l.median_package_ms} ms median</span><span>Writer</span><span>${esc(bench.writer)}</span></div>
        <h4 class="small muted">Editorial funnel · this match</h4>
        <div class="funnel">${frow("Insights", fl.insights)}${frow("Storylines", fl.storylines)}${frow("Verified", fl.verified)}${frow("Approved", fl.approved)}</div>
        <div class="btnrow"><button class="btn ghost" id="rerun">Re-run benchmark</button></div>
        <p class="small muted">Run ${esc(bench.run_at)} in ${bench.duration_s}s.</p></div>`;
    $("rerun").onclick = () => loadBench(true);
  }

  // ------------------------------------------------------------------ agent trace
  const AGENT_LABEL = { editor_in_chief: "Editor-in-Chief", desk: "Desk", corrections: "Corrections", localiser: "Localiser", producer: "Producer" };
  function traceTone(t) {
    if (t.status === "held") return "held";
    if (t.status === "rejected" || t.status === "failed" || t.agent === "Feed") return "bad";
    if (t.status === "retry" || t.status === "waiting") return "warn";
    if (t.status === "succeeded" && (t.agent === "Verifier" || (t.agent === "Producer" && t.action.startsWith("approved")))) return "ok";
    return "action";
  }
  function traceRows(rows, detailed) {
    return rows.map((t) => `<li class="tr ${traceTone(t)}"><span class="tt">${new Date(t.timestamp * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}</span>
      <span class="ta">${esc(t.agent)}</span><span class="tx"><b>${esc(t.action)}</b>${t.tool ? ` <code>${esc(t.tool)}</code>` : ""}${t.ms != null ? ` <span class="faint">${Math.round(t.ms)} ms</span>` : ""}
      ${detailed && t.input_summary ? `<div class="faint small">in: ${esc(t.input_summary)}</div>` : ""}${t.output_summary ? `<div class="small ${detailed ? "" : "clip"}">${esc(t.output_summary)}</div>` : ""}</span></li>`).join("");
  }
  async function refreshStoryTrace(sid) {
    clearTimeout(refreshStoryTrace["_" + sid]);
    refreshStoryTrace["_" + sid] = setTimeout(async () => {
      try { S.storyTrace[sid] = await api(`/api/storylines/${sid}/trace`); } catch (_) { S.storyTrace[sid] = []; }
      if (S.selected === sid) renderInspector();
    }, 120);
  }
  function renderMiniTrace() {
    const rows = S.trace.filter((t) => t.status !== "held").slice(-9).reverse();
    $("miniTrace").innerHTML = traceRows(rows, false) || `<li class="muted small">Agents start when the first story is detected.</li>`;
  }
  const FLOW = [
    ["Editor-in-Chief", "picks from code-ranked candidates; goals, red cards, turning points always"],
    ["Analyst", "gathers evidence with read-only tools"],
    ["Writer", "drafts from the brief's facts only"],
    ["Verifier", "code, not a model: re-computes every claim"],
    ["Producer", "a human approves, edits or rejects"],
  ];
  function renderNewsroom() {
    const counts = {}; S.trace.forEach((t) => (counts[t.agent] = (counts[t.agent] || 0) + 1));
    $("flow").innerHTML = FLOW.map(([a, d], i) => `<div class="node ${a === "Verifier" ? "det" : a === "Producer" ? "human" : ""}"><div class="nn">${a}</div><div class="nd">${d}</div><div class="nc">${counts[a] || 0} steps</div></div>${i < FLOW.length - 1 ? `<div class="arrow">${a === "Writer" ? "→" : a === "Verifier" ? "→<span class='back'>↩ rejected: revise (max 2)</span>" : "→"}</div>` : ""}`).join("")
      + `<div class="side small"><b>Also commissioning:</b> Desk (coverage gaps at half-time and full-time) · Corrections (re-drafts when the data changes) · Localiser (Brazil · PT-BR, South Africa · isiZulu)</div>`;
    const sel = $("agentFilter"), cur = sel.value;
    const agents = [...new Set(S.trace.map((t) => t.agent))].sort();
    if (sel.options.length - 1 !== agents.length) { sel.innerHTML = `<option value="">All agents</option>` + agents.map((a) => `<option>${esc(a)}</option>`).join(""); sel.value = cur; }
    const rows = S.trace.filter((t) => (!cur || t.agent === cur) && (!$("hideHeld").checked || t.status !== "held")).slice().reverse();
    $("trace").innerHTML = traceRows(rows.slice(0, 300), true) || `<li class="muted">No agent activity yet. Press Play.</li>`;
  }
  $("agentFilter").onchange = () => renderNewsroom();
  $("hideHeld").onchange = () => renderNewsroom();
  document.addEventListener("click", (e) => { const g = e.target.closest("[data-goto]"); if (g && g.closest("#trace")) select(g.dataset.goto); });

  // ------------------------------------------------------------------ model provider
  function renderProvider() {
    const p = S.provider || S.snap?.provider; if (!p) return;
    const b = $("writerBadge");
    const name = p.provider === "foundry_local" ? "Foundry Local" : p.provider === "azure_openai" ? "Azure OpenAI" : p.provider === "openai_compatible" ? "OpenAI-compatible" : "template";
    b.textContent = p.status === "ready" && p.provider !== "template" ? `Writer: ${name} (${(p.model || "").split(":")[0].replace(/-instruct.*$/, "")})` : p.selected !== "template" ? `Writer: templates (${name === "template" ? p.selected.replace("_", " ") : name} ${p.status})` : "Writer: templates";
    b.title = p.detail + (p.failures ? ` · ${p.failures} model failures fell back to templates` : "");
    b.className = "badge " + (p.status === "ready" && p.provider !== "template" ? "on" : ["starting", "downloading", "loading"].includes(p.status) ? "busy" : "");
  }
  async function pollProvider() {
    try { S.provider = await api("/api/model"); renderProvider(); } catch (_) {}
    const busy = S.provider && ["starting", "downloading", "loading"].includes(S.provider.status);
    setTimeout(pollProvider, busy ? 2000 : 15000);
  }
  $("redTeamBtn").onclick = async () => { try { await api(`/api/matches/${S.matchId}/red-team`, { method: "POST", body: { count: 2 } }); } catch (e) { toast(e.message, true); } };

  // ------------------------------------------------------------------ readiness + correction impact
  let readyBusy = false;
  async function pollReadiness() {
    if (S.matchId && !readyBusy) {
      readyBusy = true;
      const phase = nowT() < 46 * 60 ? "half_time" : "full_time";
      try { S.readiness = await api(`/api/matches/${S.matchId}/readiness?phase=${phase}`); renderReadiness(); } catch (_) {}
      readyBusy = false;
    }
    setTimeout(pollReadiness, 3000);
  }
  function renderReadiness() {
    const r = S.readiness; if (!r) return;
    const label = r.phase === "half_time" ? "Half-time" : "Full-time";
    if (!S.events.length) { $("readiness").innerHTML = `<div class="rd-score"><b>–</b><span>${label} ready</span></div><div class="rd-checks"><span class="rd">waiting for kick-off</span></div>`; $("impact").classList.add("hidden"); return; }
    $("readiness").innerHTML = `<div class="rd-score ${r.go ? "ok" : r.score >= 60 ? "warn" : "bad"}"><b>${r.score}%</b><span>${label} ready</span></div>
      <div class="rd-checks">${r.checks.map((c) => `<span class="rd ${c.ok ? "ok" : "no"}">${c.ok ? "✓" : "•"} ${esc(c.label)}</span>`).join("")}
      ${r.waiting_for_approval ? `<span class="rd wait">${r.waiting_for_approval} verified stor${r.waiting_for_approval === 1 ? "y" : "ies"} waiting for you</span>` : ""}</div>`;
    const open = (S.snap?.corrections || []).filter((c) => c.flagged.some((sid) => S.storylines[sid]?.status === "needs_correction"));
    const c = open[open.length - 1];
    $("impact").classList.toggle("hidden", !c);
    if (c && c.impact) {
      const i = c.impact;
      $("impact").innerHTML = `<b>Correction impact</b> · ${esc(c.reason)}<div class="chain">
        <span>${i.approved_pulled} approved stor${i.approved_pulled === 1 ? "y" : "ies"} pulled</span><span>${i.graphics_invalidated} graphic${i.graphics_invalidated === 1 ? "" : "s"} invalidated</span>
        <span>${i.drafts_invalidated} draft${i.drafts_invalidated === 1 ? "" : "s"} invalidated</span><span>${i.localised_versions} localised version${i.localised_versions === 1 ? "" : "s"}</span>
        <span>${i.packages_to_rebuild.length} package${i.packages_to_rebuild.length === 1 ? "" : "s"} to rebuild</span><span class="ok">replacement drafted by the Corrections agent</span></div>`;
    }
  }

  // ------------------------------------------------------------------ viewer personalisation
  async function openViewer() {
    $("vClubHome").textContent = S.snap.names.HOME; $("vClubAway").textContent = S.snap.names.AWAY;
    try {
      const ps = await api(`/api/matches/${S.matchId}/players`);
      const cur = $("vPlayer").value;
      $("vPlayer").innerHTML = `<option value="">Off</option>` + ps.map((p) => `<option value="${esc(p.player)}">${esc(p.player)} · ${esc(p.team)}</option>`).join("");
      $("vPlayer").value = cur;
    } catch (_) {}
    $("viewerDialog").showModal();
  }
  document.querySelectorAll("#vMode button").forEach((b) => (b.onclick = () => {
    S.vMode = b.dataset.v; document.querySelectorAll("#vMode button").forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("viewerDialog").addEventListener("close", async () => {
    if ($("viewerDialog").returnValue !== "ok") return;
    const body = { mode: S.vMode, club: $("vClub").value || null, player: $("vPlayer").value || null, base_market: $("vLang").value };
    try {
      const r = await api(`/api/matches/${S.matchId}/viewer`, { method: "POST", body });
      S.viewer = r.market.viewer; S.explainers = r.explainers; S.market = "viewer";
      S.rankings.viewer = r.ranking;
      $("audienceSel").value = "viewer"; setAudienceHelp();
      refreshPlayer(true); schedule();
    } catch (e) { toast(e.message, true); }
  });
  function viewerLabel() {
    const v = S.viewer; if (!v) return "For you…";
    return `For you · ${v.mode === "analyst" ? "Analyst" : "Fan"}`;
  }
  async function refreshPlayer(now = false) {
    const p = S.viewer?.player; if (!p) { S.playerCard = null; return renderPlayer(); }
    if (!now && Date.now() - (refreshPlayer._t || 0) < 2500) return;
    refreshPlayer._t = Date.now();
    try { S.playerCard = await api(`/api/matches/${S.matchId}/players/${encodeURIComponent(p)}`); } catch (_) { S.playerCard = null; }
    renderPlayer();
  }
  const TL = { shot: "Shot", own_goal: "Own goal", card: "Card", key_pass: "Chance created", box_entry: "Box entry", regain: "Regain" };
  function renderPlayer() {
    const c = S.playerCard, panel = $("playerPanel");
    panel.classList.toggle("hidden", !(S.market === "viewer" && S.viewer?.player));
    if (!c || !c.found) { $("playerBody").innerHTML = `<p class="muted small">No actions yet.</p>`; return; }
    $("playerTitle").textContent = `Player focus · ${c.player}`; $("playerTeam").textContent = c.team;
    const analyst = S.viewer?.mode === "analyst";
    const stats = analyst ? c.stats : c.stats.filter((x) => ["shots", "goals", "chances", "regains"].includes(x.key));
    $("playerBody").innerHTML = `<div class="pstats">${stats.map((x) => `<div class="pstat" title="${esc(sqlWithParams(x.sql, x.sql_params))}"><b>${esc(x.value)}</b><span>${esc(x.label)}</span></div>`).join("")}</div>
      <div class="ptl">${c.timeline.slice(-12).reverse().map((e) => `<span class="pev ${e.outcome === "goal" || e.type === "own_goal" ? "goal" : ""}">${e.minute}' ${esc(e.type === "shot" ? (e.outcome === "goal" ? "GOAL" : "Shot " + (e.outcome || "").replace("_", " ")) : e.type === "card" ? (e.outcome === "red" ? "Red card" : "Yellow") : TL[e.type] || e.type)}</span>`).join("")}</div>
      <div class="small muted" style="margin-top:6px">${c.stories.length} stor${c.stories.length === 1 ? "y features" : "ies feature"} ${esc(c.player)}. ${analyst ? "Hover a number for its query." : ""}</div>`;
  }

  // ------------------------------------------------------------------ audit
  async function renderAudit() {
    const rows = await api(`/api/matches/${S.matchId}/audit`);
    const corr = (S.snap.corrections || []).slice().reverse();
    const corrHtml = corr.length ? `<h4 class="small muted">Data corrections</h4>` + corr.map((c) => `<div class="audit-row"><span class="muted">${new Date(c.at * 1000).toLocaleTimeString()}</span><span style="color:var(--bad)">correction</span><span>${esc(c.reason)} · ${c.flagged.length} approved stor${c.flagged.length === 1 ? "y" : "ies"} pulled${c.impact ? ` · ${c.impact.graphics_invalidated} graphic(s), ${c.impact.drafts_invalidated} draft(s), ${c.impact.packages_to_rebuild.length} package(s) affected` : ""}</span></div>`).join("") + `<h4 class="small muted">Producer decisions</h4>` : "";
    $("audit").innerHTML = corrHtml + (rows.length ? rows.map((r) => {
      const a = JSON.parse(r.after || "{}");
      return `<div class="audit-row"><span class="muted">${new Date(r.at * 1000).toLocaleTimeString()}</span><span>${esc(r.action)}</span><span>${esc(a.headline || "")} <span class="muted">· ${esc(r.storyline_id)}</span></span></div>`;
    }).join("") : `<p class="muted">No decisions yet.</p>`);
  }

  // ------------------------------------------------------------------ live logger
  const ACTIONS = [
    { k: "1", label: "Regain · own half", type: "regain", zone: "own_half" },
    { k: "2", label: "Regain · opp half", type: "regain", zone: "opp_half" },
    { k: "3", label: "Box entry", type: "box_entry" },
    { k: "4", label: "Shot · off target", type: "shot", outcome: "off_target" },
    { k: "5", label: "Shot · on target", type: "shot", outcome: "on_target" },
    { k: "6", label: "Shot · blocked", type: "shot", outcome: "blocked" },
    { k: "7", label: "GOAL", type: "shot", outcome: "goal", cls: "goal" },
    { k: "8", label: "Corner", type: "corner", zone: "corner" },
    { k: "9", label: "Foul", type: "foul", zone: "midfield" },
  ];
  function renderLogger() {
    $("actionGrid").innerHTML = ACTIONS.map((a) => `<button class="act ${a.cls || ""}" data-k="${a.k}"><span class="k">${a.k}</span>${a.label}</button>`).join("");
    $("actionGrid").querySelectorAll(".act").forEach((b) => (b.onclick = () => logAction(ACTIONS.find((a) => a.k === b.dataset.k))));
    setTeam(S.team);
  }
  function setTeam(t) {
    S.team = t;
    $("pickHome").classList.toggle("on", t === "HOME"); $("pickAway").classList.toggle("on", t === "AWAY");
    $("playerList").innerHTML = [...S.players[t]].map((p) => `<option value="${esc(p)}">`).join("");
  }
  $("pickHome").onclick = () => setTeam("HOME"); $("pickAway").onclick = () => setTeam("AWAY");
  async function logAction(a, extra = {}) {
    if (S.snap?.mode !== "live") return;
    if (!S.clock.running && !extra.force) { toast("Clock is paused: press Kick-off or resume the clock", true); return; }
    const player = $("playerInput").value.trim();
    const body = { team: extra.team || S.team, type: a.type, outcome: a.outcome, zone: a.zone, player: player || null, t: Math.round(nowT() * 10) / 10, period: S.clock.period };
    try {
      const r = await api(`/api/matches/${S.matchId}/events`, { method: "POST", body });
      if (player) S.players[body.team].add(player);
      $("playerInput").value = ""; setTeam(S.team);
      S.logged.unshift(r.event);
      $("logList").innerHTML = S.logged.slice(0, 25).map((e) => `<li><span>${fmt(e.t)} ${esc(S.snap.names[e.team_id])} · ${esc(e.type === "shot" ? "shot " + e.outcome.replace("_", " ") : e.type.replace("_", " "))}${e.player ? " · " + esc(e.player) : ""}</span><button data-id="${e.event_id}" title="Undo">undo</button></li>`).join("");
      $("logList").querySelectorAll("button").forEach((b) => (b.onclick = async () => { await api(`/api/events/${b.dataset.id}/undo`, { method: "POST" }); S.logged = S.logged.filter((x) => x.event_id !== b.dataset.id); b.closest("li").remove(); }));
      if (r.insights.length) toast(`Detected: ${r.insights.map((i) => DET[i.detector]).join(", ")}`);
    } catch (e) { toast(e.message, true); }
  }
  document.addEventListener("keydown", (e) => {
    if (S.snap?.mode !== "live" || S.tab !== "gallery") return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.key === "h" || e.key === "H") return setTeam("HOME");
    if (e.key === "a" || e.key === "A") return setTeam("AWAY");
    const a = ACTIONS.find((x) => x.k === e.key); if (a) { e.preventDefault(); logAction(a); }
  });
  $("kickoffBtn").onclick = async () => {
    S.clock = { running: true, base: 0, startedAt: Date.now(), period: 1 };
    await logAction({ type: "kick_off" }, { team: "HOME", force: true }); toast("Kick-off: clock running");
  };
  $("pauseClockBtn").onclick = () => {
    const c = S.clock;
    if (c.running) { c.base = nowT(); c.running = false; $("pauseClockBtn").textContent = "Resume clock"; }
    else { c.startedAt = Date.now(); c.running = true; $("pauseClockBtn").textContent = "Pause clock"; }
  };
  $("secondHalfBtn").onclick = async () => {
    await logAction({ type: "period_end" }, { team: "HOME", force: true });
    S.clock = { running: true, base: 2700, startedAt: Date.now(), period: 2 };
    await logAction({ type: "kick_off" }, { team: "AWAY", force: true }); toast("Second half: clock at 45:00");
  };
  $("fullTimeBtn").onclick = async () => {
    S.clock.period = 2;
    await logAction({ type: "period_end" }, { team: "HOME", force: true });
    S.clock.base = nowT(); S.clock.running = false;
    const snap = await api(`/api/matches/${S.matchId}`); S.snap.result_check = snap.result_check; schedule();
    const rc = snap.result_check;
    toast(rc ? (rc.match ? "Full time: your logged score matches the official result" : `Full time: logged ${rc.engine.HOME}–${rc.engine.AWAY}, official ${rc.official.HOME}–${rc.official.AWAY}`) : "Full time", rc && !rc.match);
  };
  $("setClockBtn").onclick = () => {
    const m = $("setClock").value.match(/^(\d{1,3}):(\d{2})$/); if (!m) return toast("Use mm:ss, e.g. 23:40", true);
    S.clock.base = +m[1] * 60 + +m[2]; S.clock.startedAt = Date.now(); if (S.clock.base >= 2700) S.clock.period = 2; $("setClock").value = "";
  };

  // ------------------------------------------------------------------ controls
  $("playBtn").onclick = async () => {
    if (S.snap.replay?.running || $("playBtn").textContent.includes("Pause")) {
      await api(`/api/matches/${S.matchId}/pause`, { method: "POST" }); S.snap.replay = { running: false }; $("playBtn").textContent = "▶ Play";
    } else {
      await api(`/api/matches/${S.matchId}/replay`, { method: "POST", body: { speed: +$("speedSel").value } }); S.snap.replay = { running: true }; $("playBtn").textContent = "❚❚ Pause";
    }
  };
  $("speedSel").onchange = () => { if (S.snap.replay?.running) api(`/api/matches/${S.matchId}/replay`, { method: "POST", body: { speed: +$("speedSel").value } }); };
  $("matchSelect").onchange = (e) => connect(e.target.value);
  // ---- finding matches
  const LEAGUES = { "eng.1": "Premier League", "esp.1": "LaLiga", "ger.1": "Bundesliga", "ita.1": "Serie A", "fra.1": "Ligue 1", "uefa.champions": "Champions League", "rsa.1": "South African Premiership", "usa.1": "MLS", "eng.2": "Championship" };
  $("fLeague").innerHTML = Object.entries(LEAGUES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  const yday = new Date(Date.now() - 86400000); $("fDate").value = yday.toISOString().slice(0, 10);
  async function searchMatches() {
    $("fList").innerHTML = `<p class="muted small">Searching…</p>`;
    try {
      const r = await api(`/api/espn/scoreboard?date=${$("fDate").value}&league=${$("fLeague").value}`);
      $("fList").innerHTML = r.matches.length ? r.matches.map((m) => {
        const st = m.state === "in" ? `LIVE ${esc(m.clock || "")}` : m.state === "post" ? "FT" : new Date(m.kickoff).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const sc = m.state === "pre" ? "v" : `${esc(m.home_score)} – ${esc(m.away_score)}`;
        return `<div class="frow"><span><b>${esc(m.home)}</b> ${sc} <b>${esc(m.away)}</b></span><span class="st ${m.state}">${st}</span>
          <button type="button" class="btn ${m.state === "in" ? "accent" : ""}" data-id="${m.espn_id}" data-league="${m.league}">${m.state === "post" ? "Replay" : m.state === "in" ? "Follow live" : "Watch for kick-off"}</button></div>`;
      }).join("") : `<p class="muted small">No ${LEAGUES[r.league] || r.league} matches on this date.</p>`;
      $("fList").querySelectorAll("button[data-id]").forEach((b) => (b.onclick = async () => {
        b.disabled = true; b.textContent = "Loading…";
        try { const s = await api("/api/matches", { method: "POST", body: { mode: "espn", espn_id: b.dataset.id, league: b.dataset.league } }); $("findDialog").close(); loadMatches(s.match_id); }
        catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Retry"; }
      }));
    } catch (e) { $("fList").innerHTML = `<p class="small" style="color:var(--bad)">${esc(e.message)}</p>`; }
  }
  $("findMatch").onclick = () => { $("findDialog").showModal(); searchMatches(); };
  $("fSearch").onclick = searchMatches;
  $("moreSources").onchange = async (e) => {
    const v = e.target.value; e.target.value = "";
    if (v.startsWith("fixture:")) { try { const s = await api("/api/matches", { method: "POST", body: { mode: "fixture", fixture: v.slice(8) } }); loadMatches(s.match_id); } catch (err) { toast(err.message, true); } }
    if (v === "synthetic") { const s = await api("/api/matches", { method: "POST", body: { mode: "replay", seed: Math.floor(Math.random() * 1000) + 200 } }); loadMatches(s.match_id); }
    if (v === "logger") $("liveDialog").showModal();
    if (v === "file") {
      const fs = await api("/api/files");
      $("fileList").innerHTML = fs.length ? fs.map((f) => `<div class="frow"><span><b>${esc(f.name)}</b>${f.example ? ` <span class="muted small">(example, not organiser data)</span>` : ""}</span><span class="st">${Math.round(f.bytes / 1024)} KB</span><button type="button" class="btn" data-file="${esc(f.name)}">Load</button></div>`).join("")
        : `<p class="muted small">No files yet. Put the organisers' match files in backend/data/organiser/.</p>`;
      $("fileList").querySelectorAll("button[data-file]").forEach((b) => (b.onclick = async () => {
        try { const s = await api("/api/matches", { method: "POST", body: { mode: "file", file: b.dataset.file } }); $("fileDialog").close(); loadMatches(s.match_id); }
        catch (err) { toast(err.message, true); }
      }));
      $("fileDialog").showModal();
    }
    if (v === "archive") {
      try {
        const ms = await api("/api/real/matches");
        $("realSelect").innerHTML = ms.map((m) => `<option value="${m.match_id}">${esc(m.date)} · ${esc(m.home)} v ${esc(m.away)}</option>`).join("");
        $("realDialog").showModal();
      } catch (err) { toast(err.message, true); }
    }
  };
  $("realDialog").addEventListener("close", async () => {
    if ($("realDialog").returnValue !== "ok") return;
    try { const s = await api("/api/matches", { method: "POST", body: { mode: "real", sb_match_id: +$("realSelect").value } }); loadMatches(s.match_id); }
    catch (e) { toast(e.message, true); }
  });
  $("liveDialog").addEventListener("close", async () => {
    if ($("liveDialog").returnValue !== "ok") return;
    const split = (v) => v.split(",").map((x) => x.trim()).filter(Boolean);
    const body = { mode: "live", home: $("dHome").value.trim(), away: $("dAway").value.trim(), markets: {}, date: $("dDate").value.trim() || null };
    if ($("dOffH").value.trim() !== "" && $("dOffA").value.trim() !== "") { body.official_home = +$("dOffH").value; body.official_away = +$("dOffA").value; }
    if ($("dBr").value.trim()) body.markets.br = { focus_players: split($("dBr").value) };
    if ($("dZa").value.trim()) body.markets.za = { focus_players: split($("dZa").value) };
    try { const s = await api("/api/matches", { method: "POST", body }); loadMatches(s.match_id); } catch (e) { toast(e.message, true); }
  });
  $("audienceSel").onchange = async (e) => {
    const m = e.target.value;
    if (m === "viewer") { $("audienceSel").value = S.market; return openViewer(); }
    S.market = m; S.viewer = null; renderPlayer(); setAudienceHelp();
    if (S.market !== "world" && S.matchId) { try { await api(`/api/matches/${S.matchId}/localise`, { method: "POST", body: { market: S.market } }); } catch (err) { toast(err.message, true); } }
    if (S.selected) select(S.selected); else schedule();
  };
  document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    S.tab = b.dataset.tab;
    ["gallery", "newsroom", "trust", "audit"].forEach((t) => $("view-" + t).classList.toggle("hidden", t !== S.tab));
    if (S.tab === "trust") loadBench(); if (S.tab === "audit") renderAudit(); if (S.tab === "newsroom") renderNewsroom();
  }));
  document.addEventListener("click", (e) => {
    const c = e.target.closest(".card"); if (c && !e.target.closest("button")) select(c.dataset.sid);
  });

  api("/api/health").then((h) => {
    const v = !!h.validation_sources;
    $("findMatch").classList.toggle("hidden", !v);
    const arch = document.querySelector('#moreSources option[value="archive"]');
    if (arch && !v) arch.remove();
  }).catch(() => {});
  loadMatches();
  pollProvider();
  pollReadiness();
  pollOverlays();
})();
