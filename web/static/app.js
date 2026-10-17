// paltop web - read-only view of the Palworld server.
// Everything that comes from the server is escaped before it is put in the
// page: player names and Pal nicknames are typed by players.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const elClass = (e) => "el-" + String(e || "Normal").toLowerCase();
const ivClass = (v) => v >= 100 ? "iv-perfect" : v >= 80 ? "iv-high" : "label";
const ivs = (a) => a.map((v) => `<span class="${ivClass(v)}">${v}</span>`)
  .join('<span class="dim">/</span>');
// element letters: Dr Dk Gr Gd for the pairs that share a first letter
const EL_LETTER = {Normal: "N", Fire: "F", Water: "W", Leaf: "Gr", Electricity: "E",
  Ice: "I", Earth: "Gd", Dark: "Dk", Dragon: "Dr"};
const EL_NAME = {Normal: "Neutral", Leaf: "Grass", Electricity: "Electric", Earth: "Ground"};
const elLetters = (els) => `<span class=els title="${esc((els || []).map((e) => EL_NAME[e] || e).join(" / "))}">${
  (els || []).map((e) => `<b class="${elClass(e)}">${EL_LETTER[e] || "?"}</b>`).join("")}</span>`;
const TAG_SHORT = {"flying": "fly", "mount": "ride", "water mount": "swim", "buffs team": "team+",
  "team synergy": "synergy", "team speed": "speed", "buffs player": "player+", "healer": "heal", "carry weight": "carry", "base": "base",
  "drains HP": "\u26a0 drains HP"};
const tags = (list) => (list || []).filter((t) => t !== "base").map((t) =>
  `<span class="tag${t === "drains HP" ? " bad" : ""}" title="${esc(t === "drains HP" ? "its partner skill makes the Pal fighting beside you keep losing Health" : t)}">${esc(TAG_SHORT[t] || t)}</span>`).join("");
const stars = (n) => n ? `<span class="warn">${"★".repeat(n)}</span>` : "";

function dur(sec) {
  if (sec == null) return "-";
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600),
    m = Math.floor(sec % 3600 / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${sec}s`;
}
const ago = (ts) => ts ? dur(Date.now() / 1000 - ts) + " ago" : "never";
const inT = (ts) => ts ? "in " + dur(ts - Date.now() / 1000) : "-";
function when(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false});
  if (d.toDateString() === now.toDateString()) return hm;
  if (now - d < 6 * 86400000)
    return d.toLocaleDateString([], {weekday: "short"}) + " " + hm;
  return d.toLocaleDateString([], {day: "2-digit", month: "2-digit"}) + " " + hm;
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({credentials: "same-origin"}, opts));
  if (r.status === 401) { showLogin(); throw new Error("login"); }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// -- login -----------------------------------------------------------------

let regTicket = null;
function showForm(id) {
  for (const f of ["login-form", "reg1-form", "reg2-form"]) $(f).classList.toggle("hidden", f !== id);
  for (const m of ["login-msg", "reg1-msg", "reg2-msg"]) $(m).textContent = "";
  const first = {"login-form": $("login-name").value ? "pw" : "login-name", "reg1-form": "reg-server", "reg2-form": "reg-name"}[id];
  $(first).focus();
}
function showLogin() {
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  for (const id of ["chat-btn", "chat-panel"]) $(id).classList.add("hidden");
  showForm("login-form");
}
// a message that waits for OK
function notice(title, html) {
  return new Promise((done) => {
    $("notice-title").textContent = title;
    $("notice-text").innerHTML = html;
    $("notice").classList.remove("hidden");
    $("notice-ok").focus();
    $("notice-ok").onclick = () => { $("notice").classList.add("hidden"); done(); };
  });
}
const linkedText = (p) => `We found the player <b>${esc(p.name)}</b>${p.level ? ` (level ${esc(p.level)}${p.pals ? `, ${esc(p.pals)} Pals` : ""})` : ""}
  on the server and linked it to your user. Your own Pals are shown first.`;
async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  let data = {};
  try { data = await r.json(); } catch (e) {}
  return [r.ok, data];
}
$("to-register").addEventListener("click", (ev) => { ev.preventDefault(); showForm(regTicket ? "reg2-form" : "reg1-form"); });
for (const a of document.querySelectorAll(".to-login"))
  a.addEventListener("click", (ev) => { ev.preventDefault(); showForm("login-form"); });
$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const [ok, data] = await post("/api/login", {name: $("login-name").value.trim(), password: $("pw").value});
    if (!ok) { $("login-msg").textContent = data.error || "login failed"; return; }
    $("pw").value = "";
    state.player = null;
    start();
  } catch (e) { $("login-msg").textContent = "could not reach paltop"; }
});
$("reg1-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const [ok, data] = await post("/api/register/start", {server_password: $("reg-server").value});
    $("reg-server").value = "";
    if (!ok) { $("reg1-msg").textContent = data.error || "wrong server password"; return; }
    regTicket = data.ticket;
    setTimeout(() => { regTicket = null; }, (data.minutes || 15) * 60000 - 5000);
    showForm("reg2-form");
  } catch (e) { $("reg1-msg").textContent = "could not reach paltop"; }
});
$("reg2-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if ($("reg-pw").value !== $("reg-pw2").value) { $("reg2-msg").textContent = "the two passwords are not the same"; return; }
  try {
    const [ok, data] = await post("/api/register", {ticket: regTicket || "", name: $("reg-name").value.trim(), password: $("reg-pw").value});
    if (!ok) {
      $("reg2-msg").textContent = data.error || "could not make the user";
      if (/server password/.test(data.error || "")) { regTicket = null; setTimeout(() => showForm("reg1-form"), 1500); }
      return;
    }
    regTicket = null;
    for (const id of ["reg-pw", "reg-pw2", "reg-name"]) $(id).value = "";
    await notice("user made", linkedText(data.player));
    state.player = null;
    start();
  } catch (e) { $("reg2-msg").textContent = "could not reach paltop"; }
});
$("logout").addEventListener("click", async () => {
  await fetch("/api/logout", {method: "POST"});
  showLogin();
});

// -- tabs ------------------------------------------------------------------

const state = {tab: "overview", ov: null, player: null,
  sort: "best", filter: "all", atLevel: false, pal: null, pals: [], page: 0,
  team: false, goal: null, locks: {}, choose: null, hard: false, q: "", cq: "",
  fillOnce: false, csort: "score", cmpPin: null, wishes: loadWishes(),
  plan: null, mode: "real", plans: loadPlans(), breed: false,
  boss: false, bosses: null, bossId: null, bossLevel: {}, bossHp: 1, bossHard: false,
  bossTodo: false};

// passive plans, per player and Pal, kept in this browser only
function loadPlans() {
  try { return JSON.parse(localStorage.getItem("paltop-plans") || "{}") || {}; }
  catch (e) { return {}; }
}
function savePlans() {
  try { localStorage.setItem("paltop-plans", JSON.stringify(state.plans)); } catch (e) {}
}
function playerPlans() { return state.plans[state.player] || {}; }
function plansParam() {
  return Object.entries(playerPlans()).map(([id, ids]) => `${id}:${ids.filter(Boolean).join(".")}`).join(";");
}

function setTab(name) {
  state.tab = name;
  for (const b of document.querySelectorAll(".tab"))
    b.classList.toggle("active", b.dataset.tab === name);
  for (const p of document.querySelectorAll(".tabpage"))
    p.classList.toggle("hidden", p.id !== "tab-" + name);
  if (name === "pals") openPals();
  if (name === "palpedia") loadPedia();
  if (name === "traitpedia") loadTraits();
  if (name === "quests") loadQuests();
  musicWanted();
  if (name === "achievements") loadAchievements();
  if (!$("app").classList.contains("hidden")) ping();       // on this page: which tab
  document.dispatchEvent(new Event("paltop-tab"));          // the battles window draws
  try { localStorage.setItem("paltop-tab", name); } catch (e) {}
}

// show the Pals tab with its content loaded, for whichever player is picked
// (your own player is picked before the tab is first opened)
function openPals() {
  const had = state.player;
  renderPalPlayers();              // with no player yet, this picks and loads one
  if (!had || !state.ov) return;
  if (state.plan) loadPlan();
  else if (state.breed) loadBreeding();
  else if (state.boss) loadBosses();
  else loadPals();                 // the list, or the team builder when it is open
}
for (const b of document.querySelectorAll(".tab[data-tab]"))   // not the chat button
  b.addEventListener("click", () => setTab(b.dataset.tab));
document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
  if (ev.key === "c" && !$("app").classList.contains("hidden")) { toggleChat(); return; }
  if (ev.key === "Escape" && !$("chat-panel").classList.contains("hidden")) { toggleChat(false); return; }
  const k = {o: "overview", l: "pals", p: "palpedia", t: "traitpedia", q: "quests", b: "bases", x: "crashes", a: "battles", h: "achievements"}[ev.key];
  if (k) setTab(k);
});

// -- overview --------------------------------------------------------------

function meter(el, frac) {
  el.innerHTML = `<i style="width:${Math.max(0, Math.min(1, frac)) * 100}%"></i>`;
}

function spark(canvas, data, max, color) {
  const w = canvas.clientWidth, h = canvas.clientHeight || canvas.height;
  canvas.width = w * devicePixelRatio; canvas.height = h * devicePixelRatio;
  const c = canvas.getContext("2d");
  c.scale(devicePixelRatio, devicePixelRatio);
  c.clearRect(0, 0, w, h);
  if (!data || !data.length) return;
  // null = nothing recorded then (collector or machine off): leave a gap
  const top = max || Math.max(1, ...data.filter((v) => v != null));
  const runs = [];
  let run = [];
  data.forEach((v, i) => {
    if (v == null) { if (run.length) runs.push(run); run = []; return; }
    run.push([i / (data.length - 1) * w, h - 2 - (v / top) * (h - 4)]);
  });
  if (run.length) runs.push(run);
  c.strokeStyle = color; c.lineWidth = 1.5; c.fillStyle = color + "33";
  for (const r of runs) {
    c.beginPath();
    r.forEach(([x, y], i) => i ? c.lineTo(x, y) : c.moveTo(x, y));
    c.stroke();
    c.lineTo(r[r.length - 1][0], h); c.lineTo(r[0][0], h); c.closePath(); c.fill();
  }
}

function renderOverview() {
  const ov = state.ov;
  if (!ov || ov.error) {
    $("srv-name").textContent = ov ? ov.error : "loading...";
    return;
  }
  const s = ov.server;
  $("stale").classList.toggle("hidden", !ov.stale);
  $("srv-name").textContent = s.name;
  $("srv-line2").textContent = [s.version, s.difficulty, `day ${s.day ?? "?"}`, `${s.camps ?? "?"} base camps`].filter(Boolean).join("  \u00b7  ");
  $("srv-line3").textContent = [`last backup ${ago(s.last_backup)}`, `next backup ${inT(s.next_backup)}`, `next restart ${inT(s.next_restart)}`].join("  \u00b7  ");
  $("srv-status").innerHTML = `<span class="badge ${s.ok ? "ok" : "bad"}">${esc(s.status)}</span> <span class="label">up ${dur(s.uptime)}</span>`;
  meter($("m-players"), (s.players || 0) / (s.max || 1));
  $("t-players").textContent = `${s.players ?? 0}/${s.max ?? "?"}`;
  meter($("m-fps"), (s.fps || 0) / 60);
  $("t-fps").textContent = `${s.fps ?? 0}`;
  spark($("fps-spark"), ov.fps_hist, 60, "#b5e685");
  $("clock").textContent = new Date(ov.t * 1000).toLocaleTimeString([], {hour12: false});

  const alerts = ov.alerts || [];
  $("alerts-box").classList.toggle("hidden", !alerts.length);
  $("alerts").innerHTML = alerts.map((a) =>
    `<li class="${a.level === "crit" ? "crit" : "warn"}">${esc(a.text)}</li>`).join("");

  // active first, then AFK, then offline by who was on most recently
  const rankOf = (p) => p.online ? (p.afk ? 1 : 0) : 2;
  const players = ov.players.slice().sort((a, b) =>
    (rankOf(a) - rankOf(b)) || (rankOf(a) === 2 ? (b.last_seen || 0) - (a.last_seen || 0) : b.level - a.level));
  const on = players.filter((p) => p.online), afk = on.filter((p) => p.afk);
  $("players-count").textContent = `${on.length} online` + (afk.length ? `, ${afk.length} AFK` : "");
  const status = (p) => !p.online ? `<span class=dim>${ago(p.last_seen)}</span>`
    : p.afk ? `<span class=warn>AFK ${dur(p.idle)}</span><span class=dim> &middot; on ${dur(Date.now() / 1000 - (p.online_since || p.last_seen || 0))}</span>`
    : `<span class=good>active</span><span class=dim> &middot; on ${p.online_since ? dur(Date.now() / 1000 - p.online_since) : "now"}</span>`;
  $("players-table").innerHTML = "<tr><th>player</th><th class=num>lv</th><th>status</th><th class=num>pals</th><th class=num>played</th><th>guild</th></tr>" +
    players.map((p) => `<tr>
      <td class="${p.online ? "value bold" : "label"}">${esc(p.name)}</td>
      <td class=num>${p.level}</td>
      <td>${status(p)}</td>
      <td class=num>${p.pals}</td>
      <td class=num>${dur(p.playtime)}</td>
      <td class="${p.guild && p.guild !== "Unnamed Guild" ? "value" : "dim"}">${esc(p.guild || "")}</td></tr>`).join("");
  spark($("chart-fps"), ov.fps_hist, 60, "#50f095");
  spark($("chart-players"), ov.players_hist, Math.max(2, s.max || 0), "#7fd4ff");
  renderBases();
  renderCrashes();
}

async function loadOverview() {
  try { state.ov = await api("/api/overview"); renderOverview(); renderOnline(); }
  catch (e) { if (e.message !== "login") console.warn(e); }
}

// -- online now: who is in the game, and who is on this page -----------------
// a line under the tabs, on every tab

const TAB_LABEL = {overview: "overview", pals: "Pals", palpedia: "the Palpedia",
  bases: "bases", crashes: "crashes", battles: "battles"};
let web = [];
async function ping() {
  try {
    const d = await api(`/api/presence?tab=${encodeURIComponent(state.tab)}&focus=${document.hasFocus() && !document.hidden ? 1 : 0}`);
    web = d.web || [];
    renderOnline();
  } catch (e) {}
}
function renderOnline() {
  const bar = $("online-bar");
  const players = (state.ov && state.ov.players) || [];
  const on = players.filter((p) => p.online)
    .sort((a, b) => (a.afk - b.afk) || (b.level - a.level));
  const inGame = on.map((p) => {
    const since = p.online_since ? dur(Date.now() / 1000 - p.online_since) : null;
    return `<span class=on-who><span class="${p.afk ? "warn" : "good"}">&#9679;</span> <b>${esc(p.name)}</b> <span class=dim>lv ${p.level}</span>` +
      (p.afk ? ` <span class=warn>AFK ${dur(p.idle)}</span>` : "") +
      (since ? ` <span class=dim>&middot; ${since}</span>` : "") + "</span>";
  });
  const onPage = web.map((w) => `<span class=on-who><span class="${w.away ? "dim" : "good"}">&#9679;</span> <b>${esc(w.name)}</b>${w.me ? " <span class=dim>(you)</span>" : ""}` +
    (w.tab ? ` <span class=dim>on ${esc(TAB_LABEL[w.tab] || w.tab)}</span>` : "") +
    (w.away ? " <span class=dim>&middot; away</span>" : "") + "</span>");
  bar.innerHTML = `<span class=label>in the game</span> ${inGame.join("") || '<span class=dim>nobody</span>'}
    <span class=on-gap></span><span class=label>on this page</span> ${onPage.join("") || '<span class=dim>-</span>'}`;
  bar.classList.remove("hidden");
}
setInterval(() => { if (!$("app").classList.contains("hidden")) ping(); }, 20000);
window.addEventListener("focus", () => ping());
window.addEventListener("blur", () => ping());

// -- bases -----------------------------------------------------------------

function renderBases() {
  const bases = state.ov.bases || [];
  $("bases-count").textContent = `${bases.length} base camps`;
  $("bases-table").innerHTML = "<tr><th>base</th><th>where</th><th class=num>built</th><th class=num>workers</th><th>status</th></tr>" +
    bases.map((b) => {
      const n = (sev) => b.trouble.filter((t) => t.sev === sev).length;
      const bits = [];
      if (n(2)) bits.push(`<span class=crit>${n(2)} in trouble</span>`);
      if (n(1)) bits.push(`<span class=warn>${n(1)} unhappy</span>`);
      if (n(0)) bits.push(`<span class=dim>${n(0)} slacking</span>`);
      return `<tr><td class="value bold">${esc(b.label)}</td>
        <td class=label>${b.map.join(", ")}</td>
        <td class=num>${b.structures}</td>
        <td class="num ${!b.cap || b.workers >= b.cap ? "good" : "warn"}">${b.cap ? b.workers + "/" + b.cap : b.workers}</td>
        <td>${bits.join("  ") || "<span class=good>ok</span>"}</td></tr>`;
    }).join("");
  const trouble = bases.flatMap((b) => b.trouble.map((t) => Object.assign({base: b.label}, t)))
    .sort((a, b) => b.sev - a.sev);
  $("trouble-title").classList.toggle("hidden", !trouble.length);
  $("trouble-table").innerHTML = trouble.map((t) => `<tr>
    <td class=label>${esc(t.base)}</td><td>${esc(t.pal)}</td>
    <td class="${t.sev === 2 ? "crit" : t.sev === 1 ? "warn" : "dim"}">${esc(t.what)}</td></tr>`).join("");
}

// -- crashes ---------------------------------------------------------------

function renderCrashes() {
  const incs = state.ov.incidents || [];
  const bad = incs.filter((i) => !i.planned).length;
  $("crash-count").textContent = `${bad} crashes/hangs, ${incs.length - bad} planned`;
  const open = new Set([...$("crash-list").querySelectorAll("details[open]")].map((d) => d.dataset.start));
  $("crash-list").innerHTML = incs.length ? incs.map((i) => `
    <details class="crash" data-start="${i.start}" ${open.has(String(i.start)) ? "open" : ""}>
      <summary><span class="${i.planned ? "dim" : "crit bold"}">${esc(i.kind)}</span>
        <span class=value>${when(i.start)}</span>
        <span class=label>down ${i.down != null ? dur(i.down) : "?"}</span>
        <span class=value>${esc(i.people.join(", ") || "nobody online")}</span></summary>
      <div class=kv>
        <span class=label>likely cause</span><span class="${i.planned ? "dim" : "warn"}">${esc(i.verdict)}</span>
        ${Object.entries(i.notes).map(([n, notes]) =>
          `<span class=label>${esc(n)}</span><span>${esc(notes.join("; ") || "-")}</span>`).join("")}
        ${i.patterns.map((p) => `<span class=label>pattern</span><span class=warn>${esc(p)}</span>`).join("")}
      </div>
    </details>`).join("") : '<p class=good>no crashes, hangs or restarts recorded</p>';
}

// -- pals ------------------------------------------------------------------

function renderPalPlayers() {
  if (!state.ov || !state.ov.players) return;
  const players = state.ov.players.filter((p) => p.pid && p.pals)
    .sort((a, b) => b.level - a.level);
  $("pals-count").textContent = `${players.reduce((a, p) => a + p.pals, 0)} pals`;
  $("pal-players").innerHTML = players.map((p) =>
    `<button class="btn ${p.pid === state.player ? "active" : ""}" data-pid="${esc(p.pid)}">${esc(p.name)} <span class=dim>lv ${p.level} &middot; ${p.pals}</span></button>`).join("");
  for (const b of $("pal-players").querySelectorAll("button"))
    b.onclick = () => { state.player = b.dataset.pid; state.pal = null; state.locks = {}; state.choose = null; state.pals = [];
      state.q = ""; $("pal-search").value = ""; state.plan = null;
      if (state.breed) { renderPalPlayers(); return loadBreeding(); }
      if (state.boss) { state.bosses = null; renderPalPlayers(); return loadBosses(); }
      renderPalPlayers(); loadPals(); };
  if (!state.player && players.length) { state.player = players[0].pid; renderPalPlayers(); loadPals(); }
  const other = state.breed || state.boss;
  $("pal-view").classList.toggle("hidden", state.team || !!state.plan || other);
  $("team-view").classList.toggle("hidden", !state.team || !!state.plan || other);
  $("plan-view").classList.toggle("hidden", !state.plan || other);
  $("breed-view").classList.toggle("hidden", !state.breed);
  $("boss-view").classList.toggle("hidden", !state.boss);
  const view = state.boss ? "boss" : state.breed ? "breed" : state.team ? "team" : "list";
  for (const b of $("pal-subnav").querySelectorAll("button"))
    b.classList.toggle("active", b.dataset.view === view && !state.plan);
}
for (const b of $("pal-subnav").querySelectorAll("button"))
  b.onclick = () => {
    const v = b.dataset.view;
    state.plan = null; state.breed = v === "breed"; state.team = v === "team";
    state.boss = v === "boss";
    renderPalPlayers();
    if (v === "breed") loadBreeding(); else if (v === "boss") loadBosses(); else loadPals();
  };

const SORT_LABEL = {best: "best overall", attack: "attack", defense: "defense",
  tank: "tank (HP x defense)", fighter: "all-round fighter", work: "work speed",
  level: "level", IVs: "IVs", name: "name", where: "where it is"};
const FILTER_LABEL = {all: "all Pals", team: "in the team", Palbox: "in the Palbox",
  bases: "at the bases", "lucky + alpha": "lucky and alpha"};
function options(sel, items, current, labels) {
  if (sel.dataset.items !== items.join("|")) {
    sel.innerHTML = items.map((x) => `<option value="${esc(x)}">${esc((labels || {})[x] || x)}</option>`).join("");
    sel.dataset.items = items.join("|");
  }
  sel.value = current;
}
$("pal-sort").onchange = () => { state.sort = $("pal-sort").value; state.pal = null; loadPals(); };
$("pal-filter").onchange = () => { state.filter = $("pal-filter").value; state.pal = null; loadPals(); };

function buttons(el, items, current, onpick) {
  el.innerHTML = items.map((x) =>
    `<button class="btn small ${x === current ? "active" : ""}">${esc(x)}</button>`).join("");
  [...el.children].forEach((b, i) => b.onclick = () => onpick(items[i]));
}

let palListKey = "";
async function loadPals() {
  if (!state.player) return;
  if (state.team) return loadTeam();
  const q = `sort=${encodeURIComponent(state.sort)}&filter=${encodeURIComponent(state.filter)}&level=${state.atLevel ? 1 : 0}&q=${encodeURIComponent(state.q)}&skills=${searchSkills ? 1 : 0}`;
  // another player, sort, filter or search starts on page 1
  const key = state.player + "|" + q;
  if (key !== palListKey) { state.page = 0; palListKey = key; }
  let data;
  if (!state.pals.length) $("pal-list").innerHTML = '<tr><td class=dim>loading pals...</td></tr>';
  try { data = await api(`/api/players/${encodeURIComponent(state.player)}/pals?${q}&page=${state.page || 0}`); }
  catch (e) { return; }
  state.pals = data.pals;
  state.page = data.page;
  options($("pal-sort"), data.sorts, state.sort, SORT_LABEL);
  options($("pal-filter"), data.filters, state.filter, FILTER_LABEL);
  $("pal-level").classList.toggle("active", state.atLevel);
  const role = data.pals.some((p) => p.score != null);
  const first = data.page * data.per_page;
  $("pal-list").innerHTML = `<tr><th></th><th>pal</th><th>el</th><th class=num>lv</th><th></th><th>HP/ATK/DEF</th><th>rank</th><th>partner</th><th>${role ? "score" : "where"}</th></tr>` +
    data.pals.map((p, i) => `<tr class="click ${p.id === state.pal ? "sel" : ""}" data-i="${i}">
      <td>${p.lucky ? '<span class=iv-perfect>✦</span>' : p.alpha ? '<span class=warn>α</span>' : ""}</td>
      <td class="${elClass(p.element)}">${esc(p.name)}</td>
      <td>${elLetters(p.els)}</td>
      <td class=num>${p.level}</td>
      <td class="${p.gender === "Male" ? "iv-high" : "el-fire"}">${p.gender === "Male" ? "♂" : p.gender === "Female" ? "♀" : ""}</td>
      <td>${ivs(p.iv)}</td><td>${stars(p.stars)}</td>
      <td>${tags(p.partner_tags)}</td>
      <td class="${role ? "value bold" : "dim"}">${role ? p.score ?? "-" : esc(p.where)}</td></tr>`).join("");
  // picking a Pal only marks the row; the list is not fetched again
  for (const tr of $("pal-list").querySelectorAll("tr.click"))
    tr.onclick = () => {
      state.pal = state.pals[+tr.dataset.i].id;
      for (const r of $("pal-list").querySelectorAll("tr.sel")) r.classList.remove("sel");
      tr.classList.add("sel");
      loadPal();
    };
  if (!data.pals.length)
    $("pal-list").innerHTML += `<tr><td colspan=9 class=dim>${state.q ? "no pals match \u201c" + esc(state.q) + "\u201d" : "no pals here"}</td></tr>`;
  renderPager(data, first);
  if (!state.pal && data.pals.length) state.pal = data.pals[0].id;
  loadPal();
}

function renderPager(data, first) {
  const html = data.pages <= 1 ? "" : `
    <button class="btn small" data-go="0" ${data.page === 0 ? "disabled" : ""} title="first page">&laquo;</button>
    <button class="btn small" data-go="${data.page - 1}" ${data.page === 0 ? "disabled" : ""}>&lsaquo; previous</button>
    <span class="label grow">page <b class=value>${data.page + 1}</b> of ${data.pages}
      <span class=dim>&middot; ${first + 1}-${first + data.pals.length} of ${data.total} pals</span></span>
    <button class="btn small" data-go="${data.page + 1}" ${data.page >= data.pages - 1 ? "disabled" : ""}>next &rsaquo;</button>
    <button class="btn small" data-go="${data.pages - 1}" ${data.page >= data.pages - 1 ? "disabled" : ""} title="last page">&raquo;</button>`;
  for (const id of ["pal-pager-top", "pal-pager"]) {
    $(id).innerHTML = html;
    for (const b of $(id).querySelectorAll("button[data-go]"))
      b.onclick = () => {
        state.page = +b.dataset.go; state.pal = null;
        if (id === "pal-pager") $("pal-list").closest(".list-wrap").scrollTop = 0;
        loadPals().then(() => { if (id === "pal-pager") $("pal-pager-top").scrollIntoView({block: "nearest"}); });
      };
  }
}

async function loadPal() {
  if (!state.pal) { $("pal-detail").innerHTML = ""; return; }
  let p;
  try { p = await api(`/api/players/${encodeURIComponent(state.player)}/pals/${encodeURIComponent(state.pal)}?sort=${encodeURIComponent(state.sort)}&level=${state.atLevel ? 1 : 0}`); }
  catch (e) { $("pal-detail").textContent = e.message; return; }
  const rank = (r) => r >= 4 ? "iv-perfect" : r === 3 ? "iv-high" : r < 0 ? "crit" : "value";
  const bar = (label, v) => `<div class=bar><span class=label>${label}</span><span class=meter><i style="width:${v}%"></i></span><span class="${ivClass(v)}">${v}</span></div>`;
  const kv = (k, v) => `<span class=label>${esc(k)}</span><span>${v}</span>`;
  $("pal-detail").innerHTML = `
    <div class=detail-head><h2>${elLetters(p.els)} <span class="${elClass(p.element)}">${esc(p.species)}</span>
      ${p.nickname ? `<span class=value>“${esc(p.nickname)}”</span>` : ""}
      ${p.lucky ? '<span class=iv-perfect>LUCKY</span>' : ""} ${p.alpha ? '<span class=warn>ALPHA</span>' : ""}</h2>
      <button class="btn primary" id="plan-open" title="try passives on this Pal and find its best set">plan passives&hellip;</button></div>
    <div class=label>${esc(p.elements.join(" / "))} &middot; lv ${p.level} &middot; ${esc(p.gender || "-")}${p.dex != null ? " &middot; Paldeck #" + p.dex : ""}</div>
    <div class=label>HP ${p.hp != null ? Math.round(p.hp) : "?"}${p.trust ? " &middot; trust " + p.trust : ""} &middot; ${esc(p.place)}</div>
    ${p.role ? `<div class=role-box><h3>as ${esc(p.role.goal)}: #${p.role.place} of ${p.role.of}</h3>
      <div class=kv>${kv("why", esc(p.role.why.join("; ")))}
      ${p.role.good.length ? kv("helps", `<span class=good>${esc(p.role.good.join(", "))}</span>`) : ""}
      ${p.role.bad.length ? kv("hurts", `<span class=crit>${esc(p.role.bad.join(", "))}</span>`) : ""}</div></div>` : ""}
    <h3>stats</h3>${bar("HP", p.iv[0])}${bar("ATK", p.iv[1])}${bar("DEF", p.iv[2])}
    <div class=kv>
      ${kv("condenser", `<span class=warn>${"★".repeat(p.stars)}${"☆".repeat(4 - p.stars)}</span>`)}
      ${kv("souls", `HP+${p.souls.HP} ATK+${p.souls.Attack} DEF+${p.souls.Defence} WORK+${p.souls.CraftSpeed}`)}
      ${kv("passives", Object.keys(p.bonus).length ? `<span class=good>${esc(Object.entries(p.bonus).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}%`).join("   "))}</span>` : '<span class=dim>no stat bonus</span>')}
    </div>
    <h3>passives</h3>
    <div>${p.passives.map((q) => `<div><span class="${rank(q.rank)} tip" title="${esc(q.desc || q.effects.join(", "))}">${esc(q.name)}</span> <span class=label>${esc(q.effects.join(", "))}</span></div>`).join("") || '<span class=dim>none</span>'}</div>
    <h3>partner skill</h3>
    <div>${p.partner_skill && p.partner_skill.name ? `<span class="partner-name">${esc(p.partner_skill.name)}</span> ${tags(p.partner_skill.tags)}
      <div class=partner-text>${scaleText(p.partner_skill.scaled || p.partner_skill.text)}</div>
      ${hasScale(p.partner_skill.scaled) ? `<div class=dim>${SCALE_NOTE}; this Pal's is highlighted</div>` : ""}` : '<span class=dim>none</span>'}</div>
    <h3>skills and work</h3>
    <div>${p.skills.map((k) => `<div><span class="${elClass(k.element)}">${esc(k.name)}</span> <span class=label>power ${k.power} &middot; cooldown ${k.cooldown}s</span></div>`).join("")}
    ${p.also.length ? `<div class=dim>also knows ${esc(p.also.join(", "))}</div>` : ""}</div>
    <div class=kv>
      ${kv("work", esc(p.work.map(([k, v]) => `${k} ${v}`).join(", ") || "-"))}
      ${p.status.length ? kv("status", `<span class=crit>${esc(p.status.join(", "))}</span>`) : ""}
      ${p.summon_note ? kv("note", `<span class=warn>${esc(p.summon_note)}</span>`) : ""}
      ${p.caught ? kv("caught", when(p.caught)) : ""}
    </div>
    <h3>breeding with the owner's other Pals</h3>
    <table class=grid>${(p.breeding || []).map((b) => `<tr><td class="${elClass(b.element)} bold">${esc(b.child)}</td><td class=label>with ${esc(b.mate)} lv ${b.level}</td><td>${ivs(b.iv)}</td></tr>`).join("") || '<tr><td class=dim>no partner of the opposite gender</td></tr>'}</table>`;
  const po = $("plan-open");
  if (po) po.onclick = () => openPlan(state.pal);
}

$("pal-level").onclick = () => { state.atLevel = !state.atLevel; loadPals(); };
$("team-open").onclick = () => { state.team = true; renderPalPlayers(); loadTeam(); };
$("team-back").onclick = () => { state.team = false; renderPalPlayers(); loadPals(); };
$("team-level").onclick = () => { state.atLevel = !state.atLevel; keepTeam(); };
$("team-reset").onclick = () => resetTeam();
$("team-fill").onclick = () => { state.fillOnce = true; keepTeam(); };
$("choose-sort").onchange = () => { state.csort = $("choose-sort").value; loadTeam(); };
// search boxes: wait until typing pauses before asking the server
let searchTimer = null;
const debounce = (fn) => { clearTimeout(searchTimer); searchTimer = setTimeout(fn, 250); };
$("pal-search").addEventListener("input", () => debounce(() => {
  state.q = $("pal-search").value.trim(); state.pal = null; loadPals(); }));
$("choose-search").addEventListener("input", () => debounce(() => {
  state.cq = $("choose-search").value.trim(); loadTeam(); }));
$("team-hard").onclick = () => { state.hard = !state.hard; keepTeam(); };
$("goal").onchange = () => { state.goal = $("goal").value; keepTeam(); };
// wishes for the team (flying mount, Fire, healer, ...), kept in this browser
function loadWishes() {
  try { return JSON.parse(localStorage.getItem("paltop-wishes") || "[]"); } catch (e) { return []; }
}
function saveWishes() {
  try { localStorage.setItem("paltop-wishes", JSON.stringify(state.wishes)); } catch (e) {}
}
const WISH_GROUP = {mount: "mounts", element: "elements", role: "roles"};
function renderWishes(t) {
  const met = {};
  for (const w of t.wishes || []) met[w.key] = w.by;
  const unmet = new Set((t.wish_unmet || []).map(([k]) => k));
  const groups = {};
  for (const [k, label, g] of t.wish_options || []) (groups[g] = groups[g] || []).push([k, label]);
  const chip = ([k, label]) => {
    const on = state.wishes.includes(k);
    const by = met[k] || [];
    const cls = !on ? "" : by.length ? "met" : unmet.has(k) ? "none" : "open";
    const title = !on ? "wish for this" : by.length ? `met by ${by.map((i) => t.slots[i] && t.slots[i].name).join(", ")}`
      : unmet.has(k) ? "none of this player's Pals for this goal has it" : "not in the team yet";
    return `<button class="chip-btn ${on ? "active" : ""} ${cls}" data-wish="${esc(k)}" title="${esc(title)}">${
      k.startsWith("el_") ? `<b class="${elClass(k.slice(3))}">${esc(label)}</b>` : esc(label)}${on && by.length ? " &#10003;" : ""}</button>`;
  };
  const sug = t.suggested;
  $("team-wishes").innerHTML = `<h3>wishes <span class=dim>&middot; what the team should have; paltop suggests Pals that meet them</span>
      ${state.wishes.length ? '<button class="btn small" id="wish-clear">clear</button>' : ""}</h3>
    ${Object.entries(groups).map(([g, list]) => `<div class=wish-row><span class=label>${esc(WISH_GROUP[g] || g)}</span>${list.map(chip).join("")}</div>`).join("")}
    ${(t.wish_unmet || []).length ? `<p class=warn>none of this player's Pals ${t.goal.startsWith("work") ? "that work for this goal " : ""}can meet: ${esc(t.wish_unmet.map(([, l]) => l).join(", "))}</p>` : ""}
    ${sug && sug.slots.some(Boolean) ? `<div class=suggest><span class=label>suggested for the empty slots</span>
      <span>${sug.slots.map((p, i) => p ? `<span class="${elClass(p.element)} bold">${esc(p.name)}</span> <span class=dim>lv ${p.level}${p.wishes.length ? " &middot; " + esc(p.wishes.map((w) => (t.wish_options.find((o) => o[0] === w) || [w, w])[1]).join(", ")) : ""}</span>` : "").filter(Boolean).join(" &nbsp;|&nbsp; ")}</span>
      <button class="btn small primary" id="wish-use">use these</button></div>` : ""}`;
  for (const b of $("team-wishes").querySelectorAll("button[data-wish]"))
    b.onclick = () => {
      const k = b.dataset.wish;
      state.wishes = state.wishes.includes(k) ? state.wishes.filter((x) => x !== k) : [...state.wishes, k];
      saveWishes(); loadTeam();
    };
  if ($("wish-clear")) $("wish-clear").onclick = () => { state.wishes = []; saveWishes(); loadTeam(); };
  if ($("wish-use")) $("wish-use").onclick = () => {
    sug.slots.forEach((p, i) => { if (p) state.locks[i] = p.id; });
    state.choose = null; loadTeam();
  };
}
const wishTags = (t, keys) => (keys || []).map((w) => `<span class="tag wish">${esc((t.wish_options.find((o) => o[0] === w) || [w, w])[1])}</span>`).join("");

// the team is what you picked (state.locks): it starts empty, and changing
// the goal or the settings keeps it; "empty the team" clears it
function resetTeam() { state.locks = {}; state.choose = null; state.cmpPin = null; loadTeam(); }
function keepTeam() { state.choose = null; loadTeam(); }
const stepGoal = (d) => {
  const opts = [...$("goal").options].map((o) => o.value);
  if (!opts.length) return;
  state.goal = opts[(opts.indexOf(state.goal) + d + opts.length) % opts.length];
  keepTeam();
};
$("goal-prev").onclick = () => stepGoal(-1);
$("goal-next").onclick = () => stepGoal(1);

async function loadTeam() {
  if (!state.player) return;
  const lock = Object.entries(state.locks).map(([k, v]) => `${k}:${v}`).join(",");
  const choose = state.choose != null ? `&choose=${state.choose}&q=${encodeURIComponent(state.cq)}&skills=${searchSkills ? 1 : 0}` : "";
  let t;
  try { t = await api(`/api/players/${encodeURIComponent(state.player)}/team?goal=${encodeURIComponent(state.goal || "")}&level=${state.atLevel ? 1 : 0}&lock=${encodeURIComponent(lock)}${choose}&hard=${state.hard ? 1 : 0}&mode=${state.mode}&plans=${encodeURIComponent(plansParam())}&fill=${state.fillOnce ? 1 : 0}&csort=${encodeURIComponent(state.csort)}&wish=${encodeURIComponent(state.wishes.join(","))}`); }
  catch (e) { $("team-info").textContent = e.message; return; }
  if (state.fillOnce) {            // what paltop filled in is now picked
    state.fillOnce = false;
    t.slots.forEach((p, i) => { if (p) state.locks[i] = p.id; });
  }
  state.goal = t.goal;
  $("goal").innerHTML = t.goals.map(([g, label]) => `<option value="${esc(g)}" ${g === t.goal ? "selected" : ""}>${esc(label)}</option>`).join("");
  $("team-level").textContent = `as if levelled to ${t.level}`;
  $("team-level").classList.toggle("active", state.atLevel);

  $("team-modes").innerHTML = t.modes.map(([m, label]) =>
    `<button class="${m === state.mode ? "active" : ""}" data-mode="${m}" title="${
      m === "real" ? "score the Pals with the passives they have" : m === "plans"
      ? "use the passives you planned for a Pal (plan... on a slot)" : "score every Pal as if it had its best passives for this goal - shows who is worth investing in"}">${esc(label)}${m === "plans" && Object.keys(playerPlans()).length ? ` (${Object.keys(playerPlans()).length})` : ""}</button>`).join("");
  for (const b of $("team-modes").querySelectorAll("button"))
    b.onclick = () => { state.mode = b.dataset.mode; keepTeam(); };
  $("team-hard").classList.toggle("active", state.hard);
  renderWishes(t);
  const locked = new Set(t.locked);
  $("team-table").innerHTML = "<tr><th>#</th><th>pal</th><th class=num>lv</th><th>HP/ATK/DEF</th><th class=num>score</th><th>why</th><th></th></tr>" +
    t.slots.map((p, i) => p ? `<tr class="${state.choose === i ? "choosing" : ""}">
      <td class=dim>${i + 1}</td>
      <td class="${elClass(p.element)} bold">${esc(p.name)} ${elLetters(p.els)} ${tags(p.partner_tags)} ${wishTags(t, p.wishes)}</td>
      <td class=num>${p.level}</td><td>${ivs(p.iv)}</td>
      <td class="num value bold">${p.score}</td>
      <td class="label wrap">${esc(p.why.join("; "))}
        ${p.good.length ? `<br><span class=good>+ ${esc(p.good.join(", "))}</span>` : ""}
        ${p.bad.length ? `<br><span class=crit>&minus; ${esc(p.bad.join(", "))}</span>` : ""}
        ${(p.synergy || []).map((n) => `<br><span class=syn>&#10022; ${esc(n)}</span>`).join("")}
        <br><span class=dim>passives:</span> ${p.passives.map((n) => p.changed.includes(n)
          ? `<span class="warn" title="planned - it does not have this yet">${esc(n)}*</span>` : `<span class=label>${esc(n)}</span>`).join(", ") || '<span class=dim>none</span>'}
        ${p.hint ? `<br><span class=warn>${esc(p.hint)}</span>` : ""}</td>
      <td><div class=slot-actions><button class="btn small ${state.choose === i ? "active" : ""}" data-choose="${i}" title="pick another Pal for this slot">choose&hellip;</button>
        <button class="btn small" data-lock="${i}" title="take this Pal out of the team">remove</button>
        <button class="btn small" data-plan="${i}" title="plan this Pal's passives for the goal">plan&hellip;</button></div></td></tr>`
      : `<tr class="${state.choose === i ? "choosing" : ""}"><td class=dim>${i + 1}</td><td class=dim colspan=5>empty - choose a Pal for this slot</td><td><div class=slot-actions><button class="btn small ${state.choose === i ? "active" : ""}" data-choose="${i}">choose&hellip;</button></div></td></tr>`).join("");
  for (const b of $("team-table").querySelectorAll("button[data-choose]"))
    b.onclick = () => { const i = +b.dataset.choose; state.choose = state.choose === i ? null : i;
      state.cq = ""; $("choose-search").value = ""; loadTeam(); };
  setTimeout(() => {
    for (const b of $("team-info").querySelectorAll("button[data-pair]"))
      b.onclick = () => {
        const q = syn.best_pairs[+b.dataset.pair];
        state.locks = {0: q.a.id, 1: q.b.id};     // the rest is filled around them
        loadTeam();
      };
    for (const b of $("team-info").querySelectorAll("button[data-swap]"))
      b.onclick = () => {
        // exactly the team the gain was worked out for: the others stay put
        const w = syn.swaps[+b.dataset.swap];
        t.slots.forEach((p, i) => { if (p) state.locks[i] = p.id; });
        state.locks[w.slot] = w.in.id;
        loadTeam();
      };
  });
  for (const b of $("team-table").querySelectorAll("button[data-plan]"))
    b.onclick = () => openPlan(t.slots[+b.dataset.plan].id, t.goal, true);
  for (const b of $("team-table").querySelectorAll("button[data-lock]"))
    b.onclick = () => {
      delete state.locks[+b.dataset.lock];
      loadTeam();
    };
  renderChoices(t);
  renderRecs(t);
  const syn = t.synergy || {links: [], unused: [], swaps: []};
  const synHtml = t.goal.startsWith("work") || t.goal === "breed" ? "" : `
    <h3>team synergy ${syn.gain > 0.001 ? `<span class=good>partner skills make this team ${pct(syn.gain)} stronger</span>`
      : '<span class=dim>no partner skills that help each other</span>'}</h3>
    ${syn.links.length ? `<table class=grid>${syn.links.map((l) => `<tr>
      <td class="value bold">${esc(l.from)}</td><td class=dim>&rarr;</td><td class=value>${esc(l.to)}</td>
      <td class="syn wrap">${esc(l.effect)}</td></tr>`).join("")}</table>` : ""}
    ${syn.vs_fighter != null ? `<p class=label>${syn.vs_fighter > 0.005
      ? `This team is built for buffs: the strongest all-round fighter team is ${pct(syn.vs_fighter)} stronger in total, but relies less on partner skills.`
      : "This is also as strong in total as the strongest all-round fighter team."}</p>` : ""}
    ${syn.unused.map((u) => `<div class=warn>&#9888; ${esc(u)}</div>`).join("")}
    ${(syn.pairs || []).length ? `<h3>pairs in the team that work together</h3><table class=grid>${syn.pairs.map((q) => `<tr>
      <td><span class="${elClass(q.a.element)} bold">${esc(q.a.name)}</span> + <span class="${elClass(q.b.element)} bold">${esc(q.b.name)}</span></td>
      <td class="num good">${pct(q.gain)}</td><td class="syn wrap">${esc(q.why.join("; "))}</td></tr>`).join("")}</table>` : ""}
    ${(syn.best_pairs || []).length ? `<h3>other good pairs you own</h3><table class=grid>${syn.best_pairs.map((q, k) => `<tr>
      <td><span class="${elClass(q.a.element)} bold">${esc(q.a.name)}</span> <span class=label>lv ${q.a.level}</span> + <span class="${elClass(q.b.element)} bold">${esc(q.b.name)}</span> <span class=label>lv ${q.b.level}</span></td>
      <td class="num good">${pct(q.gain)}</td><td class="syn wrap">${esc(q.why.join("; "))}</td>
      <td><button class="btn small" data-pair="${k}" title="build the team around these two">try together</button></td></tr>`).join("")}</table>` : ""}
    ${syn.swaps.length ? `<h3>swaps that add synergy</h3><table class=grid>${syn.swaps.map((w, k) => `<tr>
      <td>slot ${w.slot + 1}: <span class=label>${esc(w.out)}</span> &rarr; <span class="${elClass(w.in.element)} bold">${esc(w.in.name)}</span> <span class=label>lv ${w.in.level}</span></td>
      <td class="num good">${pct(w.gain)}</td><td class="label wrap">${esc((w.why || []).join("; "))}</td>
      <td><button class="btn small" data-swap="${k}">use</button></td></tr>`).join("")}</table>` : ""}
    <div class=section-gap></div>`;
  $("team-info").innerHTML = synHtml + `<div class=kv>
    <span class=label>elements</span><span>${esc(t.elements.join(", "))}</span>
    ${t.goal.startsWith("work") ? "" : `<span class=label>weak spot</span><span class="${t.weak.length ? "warn" : "good"}">${
      t.weak.length ? esc(t.weak.map(([e, n, of]) => `${n} of ${of} weak to ${e}`).join(", ")) : "no element hits more than one of them for double damage"}</span>`}
    ${t.status ? `<span class=label>${esc(t.status.name)}</span><span>${esc(t.status.desc)}
      <div>${t.status.appliers.length ? `<span class=good>applied by ${esc(t.status.appliers.join(", "))}</span>`
        : '<span class=warn>nobody in the team can apply it</span>'}${t.status.combo ? ` &middot; ${t.status.finishers.length
        ? `<span class=good>${esc(t.status.combo)} hits from ${esc(t.status.finishers.join(", "))}</span>`
        : `<span class=warn>no ${esc(t.status.combo)} skills to cash in on it</span>`}` : ""}${t.status.booster
        ? ` &middot; <span class=good>${esc(t.status.booster[0])}'s partner skill: +${t.status.booster[1]}% damage to them</span>` : ""}${
        t.status.immune.length ? ` &middot; <span class=dim>${esc(t.status.immune.join(" and "))} foes are immune</span>` : ""}</div></span>` : ""}
    <span class=label>ideal</span><span>${t.ideal.map(([n, owned]) => esc(n) + (owned ? ' <span class=good>(owned)</span>' : "")).join(", ")}</span>
    ${t.needs.length ? `<span class=label>to get there</span><span>${t.needs.map((n) =>
      `<div><span class=warn>${esc(n.name)}</span> <span class=dim>for ${esc(n.for.join(", "))}</span> <span class=label>&middot; ${esc(n.get.join(" \u00b7 ") || "-")}</span></div>`).join("")}</span>` : ""}
    ${t.left_out.length ? `<span class=label>left out</span><span class=warn>${esc(t.left_out.join("; "))}</span>` : ""}
  </div>`;
}

// which values get a % next to them: only what the list is sorted by
const PCT_ON = {score: ["score"], hp: ["hp"], atk: ["atk"], def: ["def"],
  tank: ["hp", "def"], craft: ["craft"], level: ["level"]};
const pctOn = (key) => (PCT_ON[state.csort] || []).includes(key);
// "+12%" against the Pal in the slot now, green when better
const vsPct = (v, base) => {
  if (!base || v == null) return "";
  const d = v / base - 1;
  if (Math.abs(d) < 0.005) return ' <span class="dim small">(=)</span>';
  return ` <span class="small ${d > 0 ? "good" : "crit"}">(${d > 0 ? "+" : ""}${Math.round(d * 100)}%)</span>`;
};

function renderChoices(t) {
  const box = $("team-choose");
  const slot = state.choose;
  $("choose-bar").classList.toggle("hidden", slot == null);
  if (slot == null || !t.choices) { box.classList.add("hidden"); return; }
  $("choose-sort").innerHTML = (t.choose_sorts || []).map(([k, label]) =>
    `<option value="${esc(k)}" ${k === t.csort ? "selected" : ""}>${esc(label)}</option>`).join("");
  const cur = t.slots[slot];
  const base = cur ? cur.stats : null;
  box.classList.remove("hidden");
  const note = (c) => {
    const bits = [];
    if (c.same_species != null) bits.push(`<span class=warn>same species as slot ${c.same_species + 1}</span>`);
    if (c.gains.length) bits.push(`<span class=good>+ ${esc(c.gains.join(", "))}</span>`);
    if (c.loses.length) bits.push(`<span class=crit>&minus; ${esc(c.loses.join(", "))}</span>`);
    if (!t.goal.startsWith("work") && c.weak.length)
      bits.push(`<span class=label>weak: ${esc(c.weak.map(([e, n]) => `${n} to ${e}`).join(", "))}</span>`);
    return bits.join(" &middot; ");
  };
  const pin = state.cmpPin && t.choices.find((c) => c.id === state.cmpPin);
  box.innerHTML = `<h3>choose slot ${slot + 1}${cur ? ` - now ${esc(cur.name)} (score ${cur.score})` : " - empty"}
      <button class="btn small" id="choose-close">close</button></h3>
    <p class=dim>${cur ? `(%) is each Pal against ${esc(cur.name)} in the slot now, for what the list is sorted by; <b>compare</b> shows the two side by side.`
      : pin ? `<b>compare</b> another Pal to see it next to ${esc(pin.name)}.`
      : "the slot is empty: <b>compare</b> one Pal, then another, to see the two side by side."}</p>
    <div class=list-wrap><table class=grid><tr><th>pal</th><th class=num>lv</th><th>IVs</th><th class=num>score</th>
      <th class=num>HP</th><th class=num>attack</th><th class=num>defense</th><th class=num>work</th><th>for the team</th><th></th></tr>
    ${t.choices.length ? "" : `<tr><td colspan=10 class=dim>${state.cq ? "no candidates match “" + esc(state.cq) + "”" : "no other candidates"}</td></tr>`}
    ${t.choices.map((c, k) => `<tr class="${c.id === state.cmpPin ? "sel" : ""}">
      <td class="${elClass(c.element)} bold" title="${esc(c.why.join("; "))}">${esc(c.name)} ${elLetters(c.els)} ${stars(c.stars)} ${tags(c.partner_tags)}</td>
      <td class=num>${c.level}${pctOn("level") ? vsPct(c.level, cur && cur.level) : ""}</td><td>${ivs(c.iv)}</td>
      <td class="num value bold">${c.score}${pctOn("score") ? vsPct(c.stats.raw, base && base.raw) : ""}</td>
      ${["hp", "atk", "def", "craft"].map((k) => `<td class=num>${c.stats[k]}${pctOn(k) ? vsPct(c.stats[k], base && base[k]) : ""}</td>`).join("")}
      <td class=wrap>${wishTags(t, c.covers)} ${note(c)}${c.good.length ? ` <span class=good>+ ${esc(c.good.join(", "))}</span>` : ""}${c.bad.length ? ` <span class=crit>&minus; ${esc(c.bad.join(", "))}</span>` : ""}</td>
      <td><div class=slot-actions><button class="btn small primary" data-pick="${k}" title="put this Pal in slot ${slot + 1}">choose</button>
        <button class="btn small ${c.id === state.cmpPin ? "active" : ""}" data-cmp="${k}" title="${cur ? `compare with ${esc(cur.name)}` : "compare with another Pal"}">compare</button></div></td></tr>`).join("")}</table></div>`;
  $("choose-close").onclick = () => { state.choose = null; state.cq = ""; state.cmpPin = null; $("choose-search").value = ""; loadTeam(); };
  for (const b of box.querySelectorAll("button[data-pick]"))
    b.onclick = () => {
      const c = t.choices[+b.dataset.pick];
      for (const [k, v] of Object.entries(state.locks)) if (v === c.id) delete state.locks[k];
      state.locks[slot] = c.id;
      state.choose = null; state.cq = ""; state.cmpPin = null; $("choose-search").value = "";
      loadTeam();
    };
  for (const b of box.querySelectorAll("button[data-cmp]"))
    b.onclick = () => {
      const c = t.choices[+b.dataset.cmp];
      if (cur) return openCompare(cur.id, c.id, slot);
      if (!state.cmpPin || state.cmpPin === c.id) {
        state.cmpPin = state.cmpPin === c.id ? null : c.id;
        return renderChoices(t);
      }
      openCompare(state.cmpPin, c.id, slot);
    };
}

// -- recommendations for the team as it is -------------------------------------

function renderRecs(t) {
  const box = $("team-recs");
  const recs = t.recommend || [];
  const free = t.slots.findIndex((p) => !p);
  if (!recs.length || free < 0) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const names = t.slots.filter(Boolean).map((p) => p.name);
  box.innerHTML = `<h3>recommended for this team <span class=dim>&middot; what each would add to ${esc(names.join(", "))}, with their partner skills and traits weighed together</span></h3>
    <table class=grid>${recs.map((r, k) => `<tr>
      <td class="${elClass(r.element)} bold">${esc(r.name)} ${elLetters(r.els)} ${stars(r.stars)} ${tags(r.partner_tags)} ${wishTags(t, r.wishes)}
        <div class=dim>lv ${r.level} &middot; ${ivs(r.iv)} &middot; score ${r.score}</div></td>
      <td class="num good bold" title="how much the team's total, with synergy, grows">+${Math.round(r.share * 100)}%</td>
      <td class="wrap label">${r.reasons.map((x) => `<div class="${x.startsWith("\u26a0") ? "warn" : /heal|life steal|meets your wish|adds/.test(x) ? "good" : "syn"}">${esc(x)}</div>`).join("") || '<span class=dim>strong on its own</span>'}</td>
      <td><div class=slot-actions><button class="btn small primary" data-rec-add="${k}" title="put it in slot ${free + 1}">add</button>
        ${k ? `<button class="btn small" data-rec-cmp="${k}" title="compare with ${esc(recs[0].name)}">compare</button>` : ""}</div></td></tr>`).join("")}</table>`;
  for (const b of box.querySelectorAll("button[data-rec-add]"))
    b.onclick = () => {
      state.locks[free] = recs[+b.dataset.recAdd].id;
      state.choose = null; loadTeam();
    };
  for (const b of box.querySelectorAll("button[data-rec-cmp]"))
    b.onclick = () => openCompare(recs[0].id, recs[+b.dataset.recCmp].id, free);
}

// -- chat: the game's chat and this page's, in one --------------------------------
// new messages every few seconds; while the chat is closed they show as a
// count on its button and a short note in the corner

const chat = {last: null, seen: 0, open: false, msgs: [], timer: null, sound: true};
try { chat.seen = +localStorage.getItem("paltop-chat-seen") || 0; } catch (e) {}
try { chat.sound = localStorage.getItem("paltop-chat-sound") !== "0"; } catch (e) {}
// the chat's own sound for new messages, and its mute button
function showChatMute() {
  $("chat-mute").innerHTML = chat.sound ? "&#128276;" : "&#128277;";   // bell, bell with a line
  $("chat-mute").title = `sound for new messages: ${chat.sound ? "on" : "off"} (click to ${chat.sound ? "mute" : "unmute"})`;
  $("chat-mute").classList.toggle("active", !chat.sound);
}
showChatMute();
$("chat-mute").onclick = () => {
  chat.sound = !chat.sound;
  try { localStorage.setItem("paltop-chat-sound", chat.sound ? "1" : "0"); } catch (e) {}
  showChatMute();
};
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false});

function chatLine(m) {
  const mine = state.me && m.source === "web" && m.name === state.me.name;
  return `<div class="chat-line ${m.source} ${mine ? "mine" : ""}"><span class=dim>${hhmm(m.ts)}</span>
    <b class="${m.source === "web" ? "syn" : "value"}">${esc(m.name)}</b> ${m.source === "web" ? '<span class="tag">web</span>' : '<span class="tag game">game</span>'}
    <span>${esc(m.text)}</span></div>`;
}

function renderChat() {
  const box = $("chat-log");
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = chat.msgs.map(chatLine).join("") || '<p class=dim>no messages yet - say hi</p>';
  if (atEnd || chat.justOpened) box.scrollTop = box.scrollHeight;
  chat.justOpened = false;
  const unread = chat.msgs.filter((m) => m.id > chat.seen).length;
  $("chat-unread").textContent = unread > 99 ? "99+" : unread;
  $("chat-unread").classList.toggle("hidden", !unread || chat.open);
  document.title = (unread && !chat.open ? `(${unread}) ` : "") + "Paltop web";
}

function markSeen() {
  if (!chat.msgs.length) return;
  chat.seen = chat.msgs[chat.msgs.length - 1].id;
  try { localStorage.setItem("paltop-chat-seen", chat.seen); } catch (e) {}
}

function toast(m) {
  const el = document.createElement("div");
  el.className = "chat-toast";
  el.innerHTML = `<b class="${m.source === "web" ? "syn" : "value"}">${esc(m.name)}</b> ${esc(m.text)}`;
  el.onclick = () => { el.remove(); toggleChat(true); };
  $("chat-toasts").appendChild(el);
  setTimeout(() => el.remove(), 7000);
}

async function pollChat() {
  if ($("app").classList.contains("hidden")) return;
  let d;
  try { d = await api(`/api/chat${chat.last != null ? `?after=${chat.last}` : ""}`); }
  catch (e) { return; }
  const fresh = d.messages || [];
  const first = chat.last == null;
  chat.last = d.last;
  if (!fresh.length) { if (first) renderChat(); return; }
  chat.msgs = chat.msgs.concat(fresh).slice(-300);
  const others = first ? [] : fresh.filter((m) => !(state.me && m.source === "web" && m.name === state.me.name));
  if (others.length && chat.sound && window.paltopMusic) window.paltopMusic.sfx.chime(others[others.length - 1].source);
  if (chat.open) markSeen();
  else if (!first)                      // not the ones there when the page opened
    for (const m of fresh.slice(-3))
      if (!(state.me && m.source === "web" && m.name === state.me.name)) toast(m);
  renderChat();
}

function toggleChat(open) {
  chat.open = open == null ? !chat.open : open;
  $("chat-panel").classList.toggle("hidden", !chat.open);
  $("chat-btn").classList.toggle("hidden", chat.open || $("app").classList.contains("hidden"));
  if (chat.open) { chat.justOpened = true; markSeen(); $("chat-toasts").innerHTML = ""; setTimeout(() => $("chat-input").focus(), 0); }
  renderChat();
}
$("chat-btn").onclick = () => toggleChat();

// the chat's size: drag the grip in its top left corner (it sits in the
// bottom right corner, so that corner is the one that moves); kept in this
// browser
const CHAT_MIN = [280, 220];
function chatSize(w, h, save) {
  const p = $("chat-panel");
  w = Math.max(CHAT_MIN[0], Math.min(window.innerWidth - 32, w));
  h = Math.max(CHAT_MIN[1], Math.min(window.innerHeight - 32, h));
  p.style.width = w + "px";
  p.style.height = h + "px";
  if (save) try { localStorage.setItem("paltop-chat-size", JSON.stringify([w, h])); } catch (e) {}
}
try {
  const saved = JSON.parse(localStorage.getItem("paltop-chat-size") || "null");
  if (saved) chatSize(saved[0], saved[1]);
} catch (e) {}
$("chat-grip").addEventListener("pointerdown", (ev) => {
  ev.preventDefault();
  const p = $("chat-panel"), grip = $("chat-grip");
  const start = [ev.clientX, ev.clientY, p.offsetWidth, p.offsetHeight];
  grip.setPointerCapture(ev.pointerId);
  const move = (e) => chatSize(start[2] + start[0] - e.clientX, start[3] + start[1] - e.clientY);
  const up = () => {
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", up);
    grip.removeEventListener("pointercancel", up);
    chatSize(p.offsetWidth, p.offsetHeight, true);
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up);
  grip.addEventListener("pointercancel", up);
});
$("chat-close").onclick = () => toggleChat(false);
$("chat-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-send").disabled = true;
  try {
    const r = await fetch("/api/chat", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text})});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "could not send");
    $("chat-input").value = "";
    $("chat-msg").innerHTML = "shows in the game as <b>[web] your name: message</b>";
    $("chat-msg").className = "dim chat-note";
    await pollChat();
  } catch (e) {
    $("chat-msg").textContent = e.message;
    $("chat-msg").className = "warn chat-note";
  } finally { $("chat-send").disabled = false; }
};
setInterval(() => { if (!document.hidden) pollChat(); }, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollChat(); });

// -- quests ------------------------------------------------------------------------

let quests = null, questPlayer = null, questHide = false;
try { questHide = localStorage.getItem("paltop-quest-hide") === "1"; } catch (e) {}

async function loadQuests() {
  try { quests = await api("/api/quests"); }
  catch (e) { $("quest-body").textContent = e.message; return; }
  if (!questPlayer || !quests.players[questPlayer])
    questPlayer = quests.players[quests.me] ? quests.me : Object.keys(quests.players)[0];
  renderQuests();
}

function renderQuests() {
  if (!quests) return;
  const st = quests.players[questPlayer] || {done: [], active: []};
  const done = new Set(st.done), active = new Set(st.active);
  const q = $("quest-search").value.trim().toLowerCase();
  $("quest-hide").classList.toggle("active", questHide);
  const ids = Object.keys(quests.players).sort((a, b) => quests.names[a].localeCompare(quests.names[b]));
  $("quest-players").innerHTML = ids.map((pid) => `<button class="btn ${pid === questPlayer ? "active" : ""}" data-pid="${esc(pid)}">${esc(quests.names[pid])}${pid === quests.me ? ' <span class=dim>(you)</span>' : ""}</button>`).join("");
  for (const b of $("quest-players").querySelectorAll("button"))
    b.onclick = () => { questPlayer = b.dataset.pid; renderQuests(); };
  const who = quests.names[questPlayer] || "";
  const line = (x, n) => {
    const isDone = done.has(x.id), isActive = !isDone && active.has(x.id);
    const others = x.done_by.filter((w) => w !== who);
    const tip = [x.desc, others.length ? `done by ${others.join(", ")}` : "nobody else on the server has done it"].filter(Boolean).join("\n\n");
    return `<li class="quest ${isDone ? "done" : ""}" title="${esc(tip)}">
      <span class="check">${isDone ? "&#10003;" : ""}</span>
      ${n != null ? `<span class="qnum">${n}.</span>` : ""}<span class=qtitle>${esc(x.title)}</span>
      ${isActive ? '<span class="tag active-q">in progress</span>' : ""}
      ${others.length ? `<span class="dim small-note">${others.length} other${others.length > 1 ? "s" : ""}</span>` : ""}</li>`;
  };
  const keep = (x, group) => (!questHide || !done.has(x.id)) &&
    (!q || `${x.title} ${x.desc} ${group || "story"}`.toLowerCase().includes(q));
  const count = (list) => `${list.filter((x) => done.has(x.id)).length} of ${list.length}`;
  const bar = (list) => { const f = list.length ? list.filter((x) => done.has(x.id)).length / list.length : 0;
    return `<span class="qbar"><i style="width:${Math.round(f * 100)}%"></i></span>`; };
  const story = quests.story.map((x, i) => [x, i + 1]).filter(([x]) => keep(x));
  const all = quests.story.concat(...quests.side.map((g) => g.quests));
  $("quest-count").textContent = `${who}: ${count(all)} done`;
  $("quest-body").innerHTML = `<div class="quest-cols">
    <div class=card><h3>story <span class=dim>&middot; ${count(quests.story)}</span> ${bar(quests.story)}</h3>
      <ul class=quests>${story.map(([x, n]) => line(x, n)).join("") || '<li class=dim>nothing left here</li>'}</ul></div>
    <div class=card><h3>side quests <span class=dim>&middot; ${count(quests.side.flatMap((g) => g.quests))}</span> ${bar(quests.side.flatMap((g) => g.quests))}</h3>
      ${quests.side.map((g) => { const rows = g.quests.filter((x) => keep(x, g.group));
        return rows.length ? `<h4>${esc(g.group)} <span class=dim>&middot; ${count(g.quests)}</span></h4><ul class=quests>${rows.map((x) => line(x)).join("")}</ul>` : ""; }).join("")
        || '<p class=dim>nothing left here</p>'}</div></div>`;
}
$("quest-search").addEventListener("input", () => renderQuests());
$("quest-hide").onclick = () => { questHide = !questHide;
  try { localStorage.setItem("paltop-quest-hide", questHide ? "1" : "0"); } catch (e) {}
  renderQuests(); };

// -- traitpedia ----------------------------------------------------------------

let traits = null, traitSel = null;
const tierClass = (t) => t >= 5 ? "tier-5" : t === 4 ? "tier-4" : t > 0 ? "tier-good" : t < 0 ? "tier-bad" : "dim";
const gold = (n) => n ? `${n.toLocaleString("en").replace(/,/g, " ")} gold` : "";
const pct1 = (x) => x >= 0.1 ? `${Math.round(x * 100)}%` : `${(x * 100).toFixed(1)}%`;

async function loadTraits() {
  if (!traits) {
    try { traits = await api("/api/traitpedia"); }
    catch (e) { $("trait-list").textContent = e.message; return; }
    const uses = [...new Set(traits.traits.flatMap((t) => t.uses))].sort();
    $("trait-use").innerHTML = '<option value="">anything</option>' + uses.map((u) => `<option>${esc(u)}</option>`).join("");
  }
  renderTraits();
}

function traitWhere(t, long) {
  const bits = [];
  if (t.random) bits.push('<span class="tag" title="can appear on any Pal you catch or breed">random</span>');
  if (t.lucky) bits.push('<span class="tag" title="only lucky Pals have it">lucky Pals</span>');
  if (t.species.length) bits.push(`<span class="tag" title="born with: ${esc(t.species.join(", "))}">born with ${long ? esc(t.species.join(", ")) : t.species.length === 1 ? esc(t.species[0]) : t.species.length + " species"}</span>`);
  if (t.surgery) bits.push(`<span class="tag ${t.surgery.consumable ? "bad" : ""}" title="${esc([gold(t.surgery.price), t.surgery.item].filter(Boolean).join(" + ") || "free")}">surgery${
    esc([t.surgery.price ? ` ${t.surgery.price / 1000}k` : "", t.surgery.item ? (t.surgery.consumable ? " + disposable" : " + implant") : ""].join(""))}</span>`);
  if (t.total && !t.random && !t.lucky && !t.surgery && !t.species.length) bits.push('<span class="tag">breed it over</span>');
  return bits.join(" ");
}

function renderTraits() {
  if (!traits) return;
  const q = $("trait-search").value.trim().toLowerCase();
  const show = $("trait-show").value, use = $("trait-use").value, sort = $("trait-sort").value;
  let rows = traits.traits.filter((t) => {
    if (show === "good" && t.tier <= 0 || show === "bad" && t.tier >= 0) return false;
    if (show === "random" && !t.random || show === "surgery" && !t.surgery) return false;
    if (show === "disposable" && !(t.surgery && t.surgery.consumable)) return false;
    if (show === "born" && !t.species.length || show === "server" && !t.total) return false;
    if (show === "mine" && !t.mine || show === "notmine" && t.mine) return false;
    if (use && !t.uses.includes(use)) return false;
    if (q && !`${t.name} ${t.effects.join(" ")} ${t.desc} ${t.species.join(" ")} ${t.surgery ? t.surgery.item || "" : ""} ${t.owners.map((o) => o[0]).join(" ")} ${t.tier_label}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const key = {tier: (t) => -t.tier, name: () => 0, price: (t) => t.surgery ? t.surgery.price || 0.5 : 1e12,
    server: (t) => -t.total, mine: (t) => -t.mine}[sort];
  rows = rows.slice().sort((a, b) => key(a) - key(b) || a.name.localeCompare(b.name));
  $("trait-count").textContent = `${rows.length} of ${traits.traits.length} traits`;
  $("trait-list").innerHTML = `<tr><th>trait</th><th>effect</th><th>where</th><th class=num title="Pals on the server with it">server</th><th class=num title="your Pals with it">mine</th></tr>` +
    rows.map((t) => `<tr class="click ${t.id === traitSel ? "sel" : ""}" data-id="${esc(t.id)}">
      <td class="bold ${tierClass(t.tier)}">${esc(t.name)}<div class="small ${tierClass(t.tier)}">${esc(t.tier_label)}</div></td>
      <td class="label wrap">${esc(t.effects.join(", ") || t.desc)}</td>
      <td>${traitWhere(t)}</td>
      <td class=num>${t.total || '<span class=dim>0</span>'}</td>
      <td class="num ${t.mine ? "good" : "dim"}">${t.mine || 0}</td></tr>`).join("")
    || '<tr><td class=dim>no trait matches</td></tr>';
  for (const tr of $("trait-list").querySelectorAll("tr[data-id]"))
    tr.onclick = () => { traitSel = tr.dataset.id; renderTraits(); };
  const t = traits.traits.find((x) => x.id === traitSel) || rows[0];
  if (!t) { $("trait-detail").innerHTML = ""; return; }
  const sg = t.surgery;
  $("trait-detail").innerHTML = `<h3 class="${tierClass(t.tier)}">${esc(t.name)} <span class=dim>&middot; tier ${esc(t.tier_label)}</span></h3>
    <p>${esc(t.desc)}</p>
    <div class=kv><span class=label>effect</span><span>${esc(t.effects.join(", ") || "-")}</span></div>
    ${t.uses.length ? `<div class=kv><span class=label>good for</span><span>${esc(t.uses.join(", "))}</span></div>` : ""}
    <h3>where to get it</h3>
    ${t.random ? '<div class=kv><span class=label>random</span><span>can appear on any Pal: catch more, or breed (parents pass their traits on)</span></div>' : ""}
    ${t.lucky ? '<div class=kv><span class=label>lucky</span><span>only lucky Pals have it</span></div>' : ""}
    ${t.species.length ? `<div class=kv><span class=label>born with</span><span>every ${esc(t.species.join(", "))} has it - catch or breed one, then breed it over</span></div>` : ""}
    ${sg ? `<div class=kv><span class=label>surgery table</span><span>${esc(gold(sg.price) || "no gold")}${sg.item ? ` + <b>${esc(sg.item)}</b> ${sg.consumable ? '<span class=warn>(used up)</span>' : '<span class=good>(kept, can be used again)</span>'}` : ""}
      ${(sg.from || []).map((f) => `<br><span class=dim>the implant: ${esc(f.where)}, ${pct1(f.chance)} of its implant rolls</span>`).join("")}
      ${sg.item && !(sg.from || []).length ? '<br><span class=dim>the game data does not say where the implant is found</span>' : ""}</span></div>` : ""}
    ${!t.random && !t.lucky && !t.species.length && !sg ? '<div class=kv><span class=label>breeding</span><span>not random and not at the surgery table: breed it over from a Pal that has it</span></div>' : ""}
    <h3>on the server <span class=dim>&middot; ${t.total} Pals${t.mine ? `, ${t.mine} of them yours` : ""}</span></h3>
    ${t.owners.length ? `<table class=grid>${t.owners.map(([n, c]) => `<tr><td>${esc(n)}</td><td class=num>${c}</td></tr>`).join("")}</table>` : '<p class=dim>nobody has it yet</p>'}`;
}
$("trait-search").addEventListener("input", () => renderTraits());
for (const id of ["trait-sort", "trait-show", "trait-use"]) $(id).onchange = () => renderTraits();

// -- compare two Pals (a modal window) ----------------------------------------

async function openCompare(a, b, slot) {
  let d;
  try {
    d = await api(`/api/players/${encodeURIComponent(state.player)}/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}` +
      `&goal=${encodeURIComponent(state.goal || "")}&level=${state.atLevel ? 1 : 0}`);
  } catch (e) { d = {error: e.message}; }
  const modal = $("cmp-modal");
  if (d.error) { $("cmp-body").textContent = d.error; modal.showModal(); return; }
  const [x, y] = d.pals;
  $("cmp-title").innerHTML = `compare <span class="${elClass(x.element)}">${esc(x.name)}</span> and <span class="${elClass(y.element)}">${esc(y.name)}</span>
    ${d.level ? `<span class=dim>&middot; as if levelled to ${d.level}</span>` : ""}`;
  // one row: a's value, b's value, and b against a in %
  const row = (label, va, vb, fmt = (v) => v, higher = true, key = null) => {
    let diff = "";
    if (key && pctOn(key) && typeof va === "number" && typeof vb === "number" && va) {
      const r = vb / va - 1;
      diff = Math.abs(r) < 0.005 ? '<span class=dim>same</span>'
        : `<span class="${(r > 0) === higher ? "good" : "crit"}">${r > 0 ? "+" : ""}${Math.round(r * 100)}%</span>`;
    }
    const better = (v, o) => typeof v === "number" && typeof o === "number" && o &&
      Math.abs(v / o - 1) >= 0.005 && (higher ? v > o : v < o) ? " bold value" : "";
    return `<tr><td class=label>${label}</td><td class="num${better(va, vb)}">${fmt(va)}</td><td class="num${better(vb, va)}">${fmt(vb)}</td><td class=num>${diff}</td></tr>`;
  };
  const r2 = (v) => v == null ? "-" : Math.round(v * 100) / 100;
  const list = (items) => items.length ? items.join("<br>") : '<span class=dim>none</span>';
  const pass = (p) => list(p.passives.map((q) => `<span class="${q.rank >= 3 ? "good" : q.rank < 0 ? "crit" : "value"}" title="${esc(q.desc || "")}">${esc(q.name)}</span> <span class=dim>${esc((q.effects || []).join(", "))}</span>`));
  const sk = (p) => list(p.skills.map((q) => `<span class="${elClass(q.element)} bold">${esc(q.name)}</span> <span class=dim>${q.power || ""}</span>`));
  const work = (p) => list(p.work.map(([n, v]) => `${esc(n)} <span class=value>${v}</span>`));
  const side = (label, fa, fb) => `<tr><td class=label>${label}</td><td class=wrap>${fa}</td><td class=wrap>${fb}</td><td></td></tr>`;
  $("cmp-body").innerHTML = `<table class="grid cmp">
    <tr><th></th><th>${esc(x.name)}${x.species !== x.name ? ` <span class=dim>${esc(x.species)}</span>` : ""}</th><th>${esc(y.name)}${y.species !== y.name ? ` <span class=dim>${esc(y.species)}</span>` : ""}</th><th class=num>${PCT_ON[state.csort] ? "2nd vs 1st" : ""}</th></tr>
    ${side("elements", elLetters(x.els) + " " + esc(x.els.map((e) => EL_NAME[e] || e).join(" / ")), elLetters(y.els) + " " + esc(y.els.map((e) => EL_NAME[e] || e).join(" / ")))}
    ${row("level", x.level, y.level, undefined, true, "level")}
    ${row("condenser stars", x.stars, y.stars)}
    ${side("IVs (HP/ATK/DEF)", ivs(x.iv), ivs(y.iv))}
    ${row("HP", x.stats.hp, y.stats.hp, undefined, true, "hp")}
    ${row("attack", x.stats.atk, y.stats.atk, undefined, true, "atk")}
    ${row("defense", x.stats.def, y.stats.def, undefined, true, "def")}
    ${row("work speed", x.stats.craft, y.stats.craft, undefined, true, "craft")}
    ${Object.entries(d.goal_labels).map(([g, label]) => row(`score: ${esc(label)}${g === d.goal ? " <span class=warn>(goal)</span>" : ""}`, x.scores[g], y.scores[g], r2, true, g === d.goal ? "score" : null)).join("")}
    ${side("weak to", esc(x.weak.join(", ") || "-"), esc(y.weak.join(", ") || "-"))}
    ${side("resists", esc(x.resists.join(", ") || "-"), esc(y.resists.join(", ") || "-"))}
    ${side("passives", pass(x), pass(y))}
    ${side("skills", sk(x), sk(y))}
    ${side("partner skill", `<b class=syn>${esc(x.partner_skill.name || "-")}</b><div class=partner-text>${scaleText(x.partner_skill.scaled || x.partner_skill.text)}</div>`,
      `<b class=syn>${esc(y.partner_skill.name || "-")}</b><div class=partner-text>${scaleText(y.partner_skill.scaled || y.partner_skill.text)}</div>`)}
    ${side("work", work(x), work(y))}
    ${side("where", esc(x.where), esc(y.where))}
    ${x.summon_note || y.summon_note ? side("note", esc(x.summon_note || ""), esc(y.summon_note || "")) : ""}
  </table>
  ${slot != null ? `<div class=controls><span class=spacer></span>
    <button class="btn" data-cmp-pick="${esc(x.id)}">put ${esc(x.name)} in slot ${slot + 1}</button>
    <button class="btn primary" data-cmp-pick="${esc(y.id)}">put ${esc(y.name)} in slot ${slot + 1}</button></div>` : ""}`;
  for (const btn of $("cmp-body").querySelectorAll("button[data-cmp-pick]"))
    btn.onclick = () => {
      const id = btn.dataset.cmpPick;
      for (const [k, v] of Object.entries(state.locks)) if (v === id) delete state.locks[k];
      state.locks[slot] = id;
      state.choose = null; state.cq = ""; state.cmpPin = null; $("choose-search").value = "";
      modal.close(); loadTeam();
    };
  if (!modal.open) modal.showModal();
}
$("cmp-close").onclick = () => $("cmp-modal").close();
$("cmp-modal").addEventListener("click", (ev) => { if (ev.target === $("cmp-modal")) $("cmp-modal").close(); });


// -- passive planner --------------------------------------------------------

const SRC_LABEL = {random: "random", surgery: "surgery", server: "on your server", "this Pal": "this Pal"};
const srcTags = (list) => (list || []).map((s) => `<span class=tag title="${esc(
  s === "random" ? "can appear on any Pal" : s === "surgery" ? "can be implanted at the Pal surgery table"
  : s === "server" ? "a Pal on this server has it - breed it over" : "only this Pal has it")}">${esc(SRC_LABEL[s] || s)}</span>`).join("");
const pct = (v) => v == null ? "" : `${v >= 0 ? "+" : ""}${Math.round(v * 100)}%`;

function openPlan(pal, goal, fromTeam) {
  const saved = playerPlans()[pal];
  state.plan = {id: pal, goal: goal || (state.sort && !["best", "level", "IVs", "name", "where"].includes(state.sort)
    ? state.sort : "fighter"), p: saved ? saved.slice() : null, slot: null, q: "", fromTeam: !!fromTeam};
  $("plan-search").value = "";
  renderPalPlayers();
  loadPlan();
}

async function loadPlan() {
  const pl = state.plan;
  if (!pl) return;
  const p = pl.p == null ? "" : pl.p.map((x) => x || "").join(",");
  const extra = pl.slot != null ? `&slot=${pl.slot}&q=${encodeURIComponent(pl.q)}` : "";
  let d;
  try { d = await api(`/api/players/${encodeURIComponent(state.player)}/pals/${encodeURIComponent(pl.id)}/plan?goal=${encodeURIComponent(pl.goal)}&level=${state.atLevel ? 1 : 0}${pl.p == null ? "&p=" + d_real() : "&p=" + encodeURIComponent(p)}${extra}`); }
  catch (e) { $("plan-head").textContent = e.message; return; }
  if (pl.p == null) { pl.p = d.real.map((x) => x.id); while (pl.p.length < 4) pl.p.push(null); return loadPlan(); }
  pl.goal = d.goal;
  // remember the plan for the team builder; its own passives are no plan
  const own = d.real.map((x) => x.id).sort().join(), now = pl.p.filter(Boolean).sort().join();
  const mine = state.plans[state.player] = state.plans[state.player] || {};
  if (now === own) delete mine[pl.id]; else mine[pl.id] = pl.p.slice();
  savePlans();
  $("plan-back").innerHTML = pl.fromTeam ? "&larr; back to the team" : "&larr; back to the list";
  $("plan-goal").innerHTML = d.goals.map(([g, label]) => `<option value="${esc(g)}" ${g === d.goal ? "selected" : ""}>${esc(label)}</option>`).join("");
  $("plan-level").textContent = `as if levelled to ${d.level}`;
  $("plan-level").classList.toggle("active", state.atLevel);
  const sc = d.score, r = (x) => Math.round(x);
  const rel = (a) => sc.real ? pct(a / sc.real - 1) : "";
  $("plan-head").innerHTML = `
    <h2>${elLetters(d.pal.els)} <span class="${elClass(d.pal.element)}">${esc(d.pal.name)}</span> <span class=label>lv ${d.pal.level}</span></h2>
    ${d.cannot ? '<p class=crit>This Pal cannot do this job at all - no passive changes that. Pick another goal or another Pal.</p>' : ""}
    <table class=grid><tr><th></th><th class=num>score</th><th class=num>HP</th><th class=num>attack</th><th class=num>defense</th><th class=num>work speed</th><th>why</th></tr>
    ${[["its own passives", "real"], ["planned", "planned"], ["best possible", "best"]].map(([label, k]) => `<tr>
      <td class="${k === "planned" ? "value bold" : "label"}">${label}</td>
      <td class="num ${k === "planned" ? "value bold" : ""}">${r(sc[k])} <span class=${sc[k] >= sc.real ? "good" : "crit"}>${k === "real" ? "" : rel(sc[k])}</span></td>
      <td class=num>${d.stats[k].hp}</td><td class=num>${d.stats[k].atk}</td><td class=num>${d.stats[k].def}</td><td class=num>${d.stats[k].craft}</td>
      <td class=label>${esc(d.why[k].join("; "))}</td></tr>`).join("")}</table>`;
  const rank = (x) => x >= 4 ? "iv-perfect" : x === 3 ? "iv-high" : x < 0 ? "crit" : "value";
  $("plan-slots").innerHTML = "<tr><th>#</th><th>passive</th><th>does</th><th class=num>adds</th><th>get it</th><th></th></tr>" +
    d.planned.map((x, i) => `<tr class="${pl.slot === i ? "choosing" : ""}">
      <td class=dim>${i + 1}</td>
      <td>${x ? `<span class="${rank(x.rank)} bold tip" title="${esc(x.desc)}">${esc(x.name)}</span>` : '<span class=dim>(empty)</span>'}</td>
      <td class="label wrap">${x ? esc(x.effects.join(", ")) : ""}</td>
      <td class="num ${d.adds[i] > 0 ? "good" : d.adds[i] < 0 ? "crit" : "dim"}">${x ? pct(d.adds[i]) : ""}</td>
      <td>${x ? srcTags(x.src) : ""}</td>
      <td><button class="btn small ${pl.slot === i ? "active" : ""}" data-slot="${i}">change&hellip;</button>
        ${x ? `<button class="btn small" data-clear="${i}">clear</button>` : ""}</td></tr>`).join("");
  for (const b of $("plan-slots").querySelectorAll("button[data-slot]"))
    b.onclick = () => { const i = +b.dataset.slot; pl.slot = pl.slot === i ? null : i; pl.q = ""; $("plan-search").value = ""; loadPlan(); };
  for (const b of $("plan-slots").querySelectorAll("button[data-clear]"))
    b.onclick = () => { pl.p[+b.dataset.clear] = null; loadPlan(); };

  $("plan-choose-bar").classList.toggle("hidden", pl.slot == null);
  const box = $("plan-choose");
  box.classList.toggle("hidden", pl.slot == null || !d.options);
  if (pl.slot != null && d.options) {
    box.innerHTML = `<h3>slot ${pl.slot + 1}: pick a passive <button class="btn small" id="plan-close">close</button></h3>
      <table class=grid><tr><th>passive</th><th>does</th><th class=num>score</th><th>get it</th></tr>
      ${d.options.map((o, k) => `<tr class=click data-k="${k}">
        <td><span class="${rank(o.rank)} bold tip" title="${esc(o.desc)}">${esc(o.name)}</span></td>
        <td class="label wrap">${esc(o.effects.join(", "))}</td>
        <td class="num ${o.delta > 0.001 ? "good" : o.delta < -0.001 ? "crit" : "dim"}">${pct(o.delta)}</td>
        <td>${srcTags(o.src)}</td></tr>`).join("") || '<tr><td class=dim colspan=4>no passives match</td></tr>'}</table>`;
    $("plan-close").onclick = () => { pl.slot = null; loadPlan(); };
    for (const tr of box.querySelectorAll("tr.click"))
      tr.onclick = () => { pl.p[pl.slot] = d.options[+tr.dataset.k].id; pl.slot = null; pl.q = ""; loadPlan(); };
  }

  const need = [...new Set([...d.planned.filter(Boolean), ...d.best])].filter((x) => d.sources[x.id]);
  $("plan-info").innerHTML = `
    <h3>best set for ${esc(d.goals.find(([g]) => g === d.goal)[1])} ${sc.real ? `<span class=good>${rel(sc.best)}</span>` : ""}</h3>
    <div>${d.best.map((x) => `<span class="${rank(x.rank)} bold tip" title="${esc(x.desc)}">${esc(x.name)}</span> ${srcTags(x.src)}`).join(" &nbsp; ") || '<span class=dim>nothing helps for this goal</span>'}</div>
    ${need.length ? `<h3>where to get them</h3><div class=kv>${need.map((x) =>
      `<span class="${rank(x.rank)}">${esc(x.name)}</span><span class=label>${esc(d.sources[x.id].join(" · ") || "-")}</span>`).join("")}</div>` : ""}
    <p class=dim>Passives are passed on by breeding, so a child can get them from either parent. Scores are estimates
      for comparing sets, not the game's exact numbers; cooldown and stamina effects are not counted.</p>`;
}
function d_real() { return ""; }

$("plan-back").onclick = () => {
  const back = state.plan && state.plan.fromTeam;
  state.plan = null; renderPalPlayers();
  if (back) { if (state.mode === "real" && Object.keys(playerPlans()).length) state.mode = "plans"; loadTeam(); }
  else loadPals();
};
$("plan-level").onclick = () => { state.atLevel = !state.atLevel; loadPlan(); };
$("plan-goal").onchange = () => { state.plan.goal = $("plan-goal").value; state.plan.slot = null; loadPlan(); };
const stepPlanGoal = (dir) => {
  const opts = [...$("plan-goal").options].map((o) => o.value);
  state.plan.goal = opts[(opts.indexOf(state.plan.goal) + dir + opts.length) % opts.length];
  state.plan.slot = null; loadPlan();
};
$("plan-prev").onclick = () => stepPlanGoal(-1);
$("plan-next").onclick = () => stepPlanGoal(1);
$("plan-best").onclick = async () => {
  const pl = state.plan;
  const d = await api(`/api/players/${encodeURIComponent(state.player)}/pals/${encodeURIComponent(pl.id)}/plan?goal=${encodeURIComponent(pl.goal)}&level=${state.atLevel ? 1 : 0}&p=`);
  pl.p = d.best.map((x) => x.id); while (pl.p.length < 4) pl.p.push(null);
  pl.slot = null; loadPlan();
};
$("plan-reset").onclick = () => { state.plan.p = null; state.plan.slot = null; loadPlan(); };
$("plan-search").addEventListener("input", () => debounce(() => {
  state.plan.q = $("plan-search").value.trim(); loadPlan(); }));

// -- breeding helper --------------------------------------------------------

async function loadBreeding() {
  let d;
  try { d = await api(`/api/players/${encodeURIComponent(state.player)}/breeding`); }
  catch (e) { $("breed-body").textContent = e.message; return; }
  const palName = (p) => `<span class="${elClass(p.element)} bold">${esc(p.name)}</span> ${elLetters(p.els)} <span class=label>lv ${p.level}</span> ${stars(p.stars)}`;
  $("breed-body").innerHTML = d.roles.map((r) => `<div class=card>
    <h3>${esc(r.label)} <span class=dim>&middot; ${esc(r.what)} &middot; ${esc(r.where)}</span></h3>
    ${r.have.length ? `<table class=grid>${r.have.map((h, i) => `<tr>
        <td>${i === 0 ? '<span class=good>use</span>' : '<span class=dim>spare</span>'}</td>
        <td>${palName(h)}</td><td class=label>${esc(h.where)}</td>
        <td class="num ${i === 0 ? "value bold" : "label"}">${h.now}%</td>
        <td class=dim>${h.now < h.max ? `${h.max}% with 4 condenser stars` : "maxed"}</td></tr>`).join("")}</table>`
      : `<p class=warn>none of this player's Pals can do this</p>`}
    ${r.have.length > 1 && r.have[0].now < r.have[0].max && r.have.slice(1).some((h) => h.species === r.have[0].species)
      ? `<p class=syn>&#10022; tip: condense the spare ${esc(r.have[0].species)} into the one you use - more condenser stars raise it towards ${r.have[0].max}%</p>` : ""}
    ${r.missing.map((m) => `<div class=kv>
      <span class=label>${r.have.length ? "better" : "get"}</span><span><b>${esc(m.name)}</b>
        ${m.pairs.length ? `<br><span class=good>breed from your own:</span> ${m.pairs.map(([a, b]) => `${palName(a)} ${a.gender === "Male" ? "\u2642" : "\u2640"} + ${palName(b)} ${b.gender === "Male" ? "\u2642" : "\u2640"}`).join(" &nbsp;or&nbsp; ")}` : ""}
        ${m.recipes.length ? `<br><span class=label>special pair:</span> ${m.recipes.map((pair) => pair.map(([n, own]) => `${esc(n)}${own ? ' <span class=good>(yours)</span>' : ' <span class=dim>(not yours)</span>'}`).join(" + ")).join(" or ")}` : ""}
        ${m.others.length ? `<br><span class=label>on the server:</span> ${m.others.map(([who, n]) => `${esc(who)} has ${n}`).join(", ")}` : ""}
        ${!m.pairs.length && !m.recipes.length && !m.others.length ? '<br><span class=dim>catch one - no pair of your Pals breeds it</span>' : ""}
      </span></div>`).join("")}</div>`).join("") + `<div class=card>
    <h3>parents that breed faster <span class=dim>&middot; Philanthropist doubles breeding speed at the breeding farm</span></h3>
    ${d.parents.length ? `<table class=grid>${d.parents.map((p) => `<tr><td>${palName(p)}</td><td class=label>${esc(p.passives.join(", "))}</td><td class=dim>${esc(p.where)}</td></tr>`).join("")}</table>`
      : '<p class=dim>none yet - Philanthropist can be implanted at the surgery table</p>'}</div>`;
}
// -- boss planner -----------------------------------------------------------

const BOSS_KIND = {tower: "tower", raid: "raid", field: "field boss"};
const sharePct = (x) => x >= 10 ? "&gt;999%" : `${Math.round(x * 100)}%`;
const multTag = (m) => m > 1 ? `<span class=good>&times;${m}</span>` : m < 1 ? `<span class=warn>&times;${m}</span>` : "";

async function loadBosses() {
  if (!state.bosses || state.bosses.pid !== state.player) {
    let d;
    try { d = await api(`/api/players/${encodeURIComponent(state.player)}/bosses`); }
    catch (e) { $("boss-body").textContent = e.message; return; }
    d.pid = state.player;
    state.bosses = d;
    $("boss-hp").innerHTML = d.hp_times.map((n) => `<option value=${n}>${n}</option>`).join("");
    $("boss-hp").value = String(state.bossHp);
  }
  renderBossList();
  if (state.bossId) loadBossPlan();
  else $("boss-body").innerHTML = '<p class=dim>pick a boss on the left</p>';
}

function renderBossList() {
  const d = state.bosses;
  if (!d) return;
  const q = $("boss-search").value.trim().toLowerCase(), kind = $("boss-kind").value;
  const rows = d.bosses.filter((b) => (!kind || b.kind === kind || kind === "hard" && b.hard || kind === "normal" && !b.hard) && (!state.bossTodo || !b.mine) &&
    (!q || `${b.name} ${b.title} ${b.tower} ${b.region}`.toLowerCase().includes(q)));
  // like the quest list: a check mark and grey text for the ones the picked
  // player has beaten, and who has beaten each; where it is shows on a click
  const who = ((state.ov && state.ov.players) || []).find((p) => p.pid === state.player);
  const whoName = who ? who.name : "this player";
  $("boss-list").innerHTML = `<tr><td colspan=2 class="dim small-note"><span class="check mine-key">&#10003;</span> beaten by ${esc(whoName)}
      &nbsp; <span class="check others-key">&#10003;</span> beaten by someone else on the server</td></tr>` +
    rows.map((b) => `<tr class="click boss-row ${b.mine ? "done" : b.beaten.length ? "others" : ""} ${b.id === state.bossId ? "sel" : ""}" data-id="${esc(b.id)}">
      <td class=check-cell><span class="check" title="${b.mine ? `${esc(whoName)} has beaten it` : b.beaten.length ? `beaten by ${esc(b.beaten.join(", "))}, not by ${esc(whoName)} yet` : "nobody on the server has beaten it yet"}">${b.mine || b.beaten.length ? "&#10003;" : ""}</span></td>
      <td><span class="bname">${esc(b.name)}</span> <span class=dim>${esc(BOSS_KIND[b.kind])}${b.level ? ` &middot; lv ${b.level}` : ""}</span>${b.hard ? ' <span class="tag hard">hard</span>' : ""}
        ${b.beaten.length ? `<span class="beaten-by" title="beaten by ${esc(b.beaten.join(", "))}">${esc(b.beaten.join(", "))}</span>` : ""}</td></tr>`).join("")
    || '<tr><td class=dim>no boss matches</td></tr>';
  for (const tr of $("boss-list").querySelectorAll("tr[data-id]"))
    tr.onclick = () => { state.bossId = tr.dataset.id; renderBossList(); loadBossPlan(); };
}

async function loadBossPlan() {
  const id = state.bossId, lv = state.bossLevel[id];
  let d;
  try {
    d = await api(`/api/players/${encodeURIComponent(state.player)}/bosses/${encodeURIComponent(id)}` +
      `?hp=${state.bossHp}${lv ? `&lv=${lv}` : ""}${state.bossHard ? "&hard=1" : ""}`);
  } catch (e) { $("boss-body").textContent = e.message; return; }
  if (id !== state.bossId) return;
  if (d.error) { $("boss-body").textContent = d.error; return; }
  const b = d.boss;
  $("boss-level").value = b.level;
  const els = b.els_known ? `${elLetters(b.els)} ${esc(b.els.map((e) => EL_NAME[e] || e).join(" / "))}` : '<span class=dim>elements not in the data</span>';
  const team = d.rows.filter((r) => r.team);
  $("boss-body").innerHTML = `<div class=card>
    <h3>${esc(b.name)}${b.hard ? ' <span class="tag hard">hard mode</span>' : ""} <span class=dim>&middot; ${esc(b.kind === "tower" ? `${b.title}, ${b.tower}` : b.kind === "raid" ? "raid" : `field boss, ${b.region}`)}</span></h3>
    <div class=kv><span class=label>element</span><span>${els}</span></div>
    <div class=kv><span class=label>stats</span><span>lv ${b.level} &middot; HP ${b.hp}${b.hp_times > 1 ? ` (&times;${b.hp_times})` : ""} &middot; attack ${b.atk} &middot; defense ${b.defense}</span></div>
    ${b.weak.length ? `<div class=kv><span class=label>weak to</span><span>${b.weak.map(([e, m]) => `<b class="${elClass(e)}">${esc(EL_NAME[e] || e)}</b> <span class=good>&times;${m}</span>`).join(", ")}</span></div>` : ""}
    ${b.resist.length ? `<div class=kv><span class=label>resists</span><span>${b.resist.map(([e, m]) => `<b class="${elClass(e)}">${esc(EL_NAME[e] || e)}</b> <span class=warn>&times;${m}</span>`).join(", ")}</span></div>` : ""}
    <div class=kv><span class=label>skills</span><span>${b.skills.map((s) => `<b class="${elClass(s.element)}">${esc(s.name)}</b> <span class=dim>${s.power}</span>`).join(", ")}</span></div>
    ${b.statuses.length ? `<div class=kv><span class=label>can inflict</span><span>${b.statuses.map((s) => esc(s.name) + (s.immune.length ? ` <span class=dim>(${esc(s.immune.join(" and "))} Pals are immune)</span>` : "")).join(", ")}</span></div>` : ""}
    <div class=kv><span class=label>beaten by</span><span>${b.beaten.length ? esc(b.beaten.join(", ")) : '<span class=dim>nobody on the server yet</span>'}</span></div>
  </div>
  <div class=card><h3>suggested team <span class=dim>&middot; the five best, together ${sharePct(d.team_share)} of its HP</span></h3>
    ${team.length ? `<p>${team.map((r) => `<span class="${elClass(r.element)} bold">${esc(r.name)}</span> <span class=label>lv ${r.level}</span>`).join(" &middot; ")}</p>
    <p class=${d.team_share >= 1 ? "good" : "warn"}>${d.team_share >= 1 ? "should win, one after another" : "not enough on paper - level up, raise the boss's weakness, or pick a lower HP multiplier"}</p>` : '<p class=dim>no Pals to bring</p>'}</div>
  <div class=card><h3>this player's Pals <span class=dim>&middot; best ${d.rows.length} of ${d.count}</span></h3>
    <div class=list-wrap><table class=grid><tr><th>Pal</th><th class=num>dmg/turn</th><th class=num>turns</th><th class=num>share</th><th class=num>win</th><th>best skill</th><th>notes</th></tr>
    ${d.rows.map((r) => `<tr class="${r.team ? "sel" : ""}">
      <td><span class="${elClass(r.element)} bold">${esc(r.name)}</span> ${elLetters(r.els)} <span class=label>lv ${r.level}</span> ${stars(r.stars)}</td>
      <td class=num>${r.dealt}</td><td class=num>${r.turns >= 40 ? "40+" : r.turns}</td>
      <td class="num ${r.share >= 1 ? "good" : ""}">${sharePct(r.share)}</td>
      <td class=num>${r.win != null ? `${r.win}%` : '<span class=dim>-</span>'}</td>
      <td><b class="${elClass(r.best.element)}">${esc(r.best.name)}</b> ${multTag(r.best.mult)}</td>
      <td class="label wrap">${[r.worst > 1 ? `<span class=warn>takes &times;${r.worst}</span>` : r.worst < 1 ? `<span class=good>takes &times;${r.worst}</span>` : "",
        r.immune.length ? `<span class=good>immune to ${esc(r.immune.join(", "))}</span>` : "",
        r.learned ? `<span class=syn>equip ${esc(r.learned.name)}: +${r.learned.gain}%</span>` : "",
        r.hard ? `<span class=dim>${esc(r.hard)}</span>` : ""].filter(Boolean).join(" &middot; ")}</td></tr>`).join("")}</table></div>
    <p class=dim>paltop's own battle model, not the game's: bosses' extra HP and tower and raid levels are not in the game data.</p></div>`;
}
$("boss-search").addEventListener("input", () => renderBossList());
$("boss-kind").onchange = () => renderBossList();
$("boss-todo").onclick = () => { state.bossTodo = !state.bossTodo;
  $("boss-todo").classList.toggle("active", state.bossTodo); renderBossList(); };
$("boss-hard").onclick = () => { state.bossHard = !state.bossHard;
  $("boss-hard").classList.toggle("active", state.bossHard); if (state.bossId) loadBossPlan(); };
$("boss-hp").onchange = () => { state.bossHp = +$("boss-hp").value || 1; if (state.bossId) loadBossPlan(); };
$("boss-level").onchange = () => { const v = Math.max(1, Math.min(80, +$("boss-level").value || 1));
  if (state.bossId) { state.bossLevel[state.bossId] = v; loadBossPlan(); } };

$("breed-open").onclick = () => { state.breed = true; renderPalPlayers(); loadBreeding(); };
$("breed-back").onclick = () => { state.breed = false; renderPalPlayers(); loadPals(); };


// -- "include active skills" in searches: one setting for every search box,
// off unless turned on (then Burn finds every Pal with a skill that burns)

let searchSkills = (() => { try { return localStorage.getItem("paltop-search-skills") === "1"; } catch (e) { return false; } })();
function showSearchSkills() {
  for (const id of ["pal-skills", "choose-skills", "pedia-skills"]) $(id).classList.toggle("active", searchSkills);
}
for (const [id, reload] of [["pal-skills", () => loadPals()], ["choose-skills", () => loadTeam()], ["pedia-skills", () => loadPedia()]])
  $(id).onclick = () => {
    searchSkills = !searchSkills;
    try { localStorage.setItem("paltop-search-skills", searchSkills ? "1" : "0"); } catch (e) {}
    showSearchSkills();
    reload();
  };
showSearchSkills();

// -- palpedia --------------------------------------------------------------

const pedia = {sort: "paldeck", show: "all", kind: "any", q: "", page: 0, id: null, rows: [],
  ref: (() => { try { return localStorage.getItem("paltop-pedia-ref") || null; } catch (e) { return null; } })(),
  spoilers: (() => { try { return localStorage.getItem("paltop-spoilers") === "1"; } catch (e) { return false; } })()};
let pediaKey = "";
async function loadPedia() {
  const q = `sort=${encodeURIComponent(pedia.sort)}&show=${encodeURIComponent(pedia.show)}&kind=${encodeURIComponent(pedia.kind)}&q=${encodeURIComponent(pedia.q)}&spoilers=${pedia.spoilers ? 1 : 0}&skills=${searchSkills ? 1 : 0}`;
  if (q !== pediaKey) { pedia.page = 0; pediaKey = q; }
  let d;
  if (!pedia.rows.length) $("pedia-list").innerHTML = '<tr><td class=dim>loading the Palpedia...</td></tr>';
  try { d = await api(`/api/palpedia?${q}&page=${pedia.page}${pedia.ref ? "&ref=" + encodeURIComponent(pedia.ref) : ""}`); } catch (e) { return; }
  const ref = d.ref;
  $("pedia-ref-bar").classList.toggle("hidden", !ref);
  if (ref) {
    $("pedia-ref-bar").innerHTML = `<span class=label>comparing with</span> ${elLetters(ref.els)} <a href="#" class="pedia-link ${elClass(ref.els[0])} bold" data-id="${esc(ref.id)}">${esc(ref.name)}</a>
      <span class="dim grow">the list shows how each Pal does against it as ${esc(d.vs_goal)}</span>
      <button class="btn small" id="pedia-ref-stop">stop comparing</button>`;
    $("pedia-ref-stop").onclick = () => setPediaRef(null);
    $("pedia-ref-bar").querySelector("a").onclick = (ev) => { ev.preventDefault(); pedia.id = ref.id; loadPediaEntry(); };
  }
  pedia.rows = d.rows; pedia.page = d.page;
  const pick = (sel, items, cur) => {
    const key = items.map(([v]) => v).join("|");
    if (sel.dataset.items !== key) {
      sel.innerHTML = items.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("");
      sel.dataset.items = key;
    }
    sel.value = cur;
  };
  pick($("pedia-sort"), d.sorts, d.sort); pick($("pedia-show"), d.shows, d.show); pick($("pedia-kind"), d.kinds, d.kind);
  $("pedia-spoilers").classList.toggle("active", pedia.spoilers);
  $("pedia-spoilers").textContent = pedia.spoilers ? "spoilers on" : "spoilers off";
  $("pedia-count").textContent = `${d.found} of ${d.all} found on the server`;
  const vsCell = (r) => !ref ? "" : r.id === ref.id ? '<td class="num dim">=</td>'
    : `<td class="num ${r.vs == null ? "dim" : r.vs > 0.005 ? "good" : r.vs < -0.005 ? "crit" : "dim"}">${r.vs == null ? "-" : pct(r.vs)}</td>`;
  $("pedia-list").innerHTML = `<tr><th>#</th><th>pal</th><th>el</th><th>work</th><th title="caught by you">you</th><th>${d.ranked ? "score" : "rarity"}</th>${ref ? `<th class=num title="against ${esc(ref.name)} as ${esc(d.vs_goal)}">vs ${esc(ref.name)}</th>` : ""}</tr>` +
    d.rows.map((r, i) => r.hidden
      ? `<tr class=hidden-pal><td class=dim>${esc(r.no)}</td><td class=dim colspan=${ref ? 6 : 5}>??? <span class=dim>not found on the server yet</span></td></tr>`
      : `<tr class="click ${r.id === pedia.id ? "sel" : ""} ${ref && r.id === ref.id ? "is-ref" : ""}" data-i="${i}">
      <td class=dim>${esc(r.no)}</td>
      <td class="${elClass(r.els[0])}">${esc(r.name)}${r.found ? "" : ' <span class=warn title="nobody on the server has found it yet">&#9679;</span>'}</td>
      <td>${elLetters(r.els)}</td>
      <td class="label work-cell" title="${esc(r.work.map(([k, n]) => `${k} ${n}`).join(", "))}">${esc(r.work.slice(0, ref ? 2 : 3).map(([k, n]) => `${k} ${n}`).join(", "))}</td>
      <td>${r.caught ? `<span class=good title="you have caught it${r.owned ? `, you own ${r.owned}` : ""}">&#10003;${r.owned ? ` ${r.owned}` : ""}</span>` : '<span class=dim>-</span>'}</td>
      <td class="${d.ranked ? "value bold" : "warn"}">${d.ranked ? `${r.score ?? "-"} <span class=dim>#${r.place ?? "-"}</span>` : "&#9733;".repeat(Math.min(5, Math.ceil((r.rarity || 1) / 2)))}</td>${vsCell(r)}</tr>`).join("");
  for (const tr of $("pedia-list").querySelectorAll("tr.click"))
    tr.onclick = () => {
      pedia.id = pedia.rows[+tr.dataset.i].id;
      for (const r of $("pedia-list").querySelectorAll("tr.sel")) r.classList.remove("sel");
      tr.classList.add("sel");
      loadPediaEntry();
    };
  if (!d.rows.length)
    $("pedia-list").innerHTML += `<tr><td colspan=6 class=dim>${pedia.q ? "no Pals match “" + esc(pedia.q) + "”" : "no Pals here"}${d.hidden && !pedia.spoilers ? ` <span class=dim>(${d.hidden} not found yet are hidden - turn on spoilers)</span>` : ""}</td></tr>`;
  const first = d.page * d.per_page;
  const html = d.pages <= 1 ? "" : `
    <button class="btn small" data-go="0" ${d.page === 0 ? "disabled" : ""}>&laquo;</button>
    <button class="btn small" data-go="${d.page - 1}" ${d.page === 0 ? "disabled" : ""}>&lsaquo; previous</button>
    <span class="label grow">page <b class=value>${d.page + 1}</b> of ${d.pages}
      <span class=dim>&middot; ${first + 1}-${first + d.rows.length} of ${d.total}</span></span>
    <button class="btn small" data-go="${d.page + 1}" ${d.page >= d.pages - 1 ? "disabled" : ""}>next &rsaquo;</button>
    <button class="btn small" data-go="${d.pages - 1}" ${d.page >= d.pages - 1 ? "disabled" : ""}>&raquo;</button>`;
  for (const id of ["pedia-pager-top", "pedia-pager"]) {
    $(id).innerHTML = html;
    for (const b of $(id).querySelectorAll("button[data-go]"))
      b.onclick = () => { pedia.page = +b.dataset.go; pedia.id = null; $("pedia-list").closest(".list-wrap").scrollTop = 0; loadPedia(); };
  }
  if (!pedia.id || !d.rows.some((r) => r.id === pedia.id)) {
    const firstPal = d.rows.find((r) => !r.hidden);
    pedia.id = firstPal ? firstPal.id : null;
    const tr = firstPal && $("pedia-list").querySelector(`tr[data-i="${d.rows.indexOf(firstPal)}"]`);
    if (tr) tr.classList.add("sel");
  }
  loadPediaEntry();
}

async function loadPediaEntry() {
  if (!pedia.id) { $("pedia-detail").innerHTML = ""; return; }
  let p;
  try { p = await api(`/api/palpedia/${encodeURIComponent(pedia.id)}?spoilers=${pedia.spoilers ? 1 : 0}${pedia.ref ? "&ref=" + encodeURIComponent(pedia.ref) : ""}`); }
  catch (e) { $("pedia-detail").textContent = e.message; return; }
  if (p.hidden) { $("pedia-detail").innerHTML = `<h2>#${esc(p.no)} ???</h2><p class=dim>Nobody on the server has found this Pal yet. Turn on spoilers to see it.</p>`; return; }
  const kv = (k, v) => `<span class=label>${esc(k)}</span><span>${v}</span>`;
  const bar = (label, v, top) => `<div class=bar><span class=label>${label}</span><span class=meter><i style="width:${Math.round(100 * v / top)}%"></i></span><span class=value>${v}</span></div>`;
  const pal = (x) => x.id ? `<a href="#" class="pedia-link ${elClass((x.els || [])[0])}" data-id="${esc(x.id)}">${esc(x.name)}</a>${x.found ? "" : ' <span class=warn title="not found on the server yet">&#9679;</span>'}`
    : `<span class=dim>${esc(x.name)}</span>`;
  $("pedia-detail").innerHTML = `
    <div class=detail-head><h2><span class=dim>#${esc(p.no)}</span> ${elLetters(p.els)} <span class="${elClass(p.els[0])}">${esc(p.name)}</span></h2>
      ${pedia.ref === p.id ? '<button class="btn" id="pedia-compare">stop comparing</button>'
        : '<button class="btn primary" id="pedia-compare" title="show every other Pal in % against this one">compare others with this</button>'}</div>
    <div class=label>${esc(p.els.map((e) => EL_NAME[e] || e).join(" / "))} &middot; rarity ${p.rarity ?? "-"} &middot; ${p.male != null ? `${p.male}% male` : ""}</div>
    <div class=label>${p.found ? `<span class=good>found on the server</span>${p.owned_total ? ` &middot; ${p.owned_total} owned now` : ""}` : '<span class=warn>nobody on the server has found it yet</span>'}
      ${p.caught ? ` &middot; <span class=good>you have caught it${p.owned ? `, you own ${p.owned}` : ""}</span>` : ""}</div>
    ${p.first && p.first.known ? `<div class=label>first found by <b class=value>${esc(p.first.who.join(" and "))}</b>
      <span class=dim>${p.first.known === "estimated" ? "by " : ""}${new Date(p.first.ts * 1000).toLocaleDateString([], {day: "numeric", month: "short"})}${p.first.known === "estimated" ? " (estimated)" : ""}</span></div>` : ""}
    ${p.compare ? compareHtml(p) : ""}
    <h3>base stats</h3>${bar("HP", p.stats.hp, p.tops.hp)}${bar("ATK", p.stats.atk, p.tops.atk)}${bar("DEF", p.stats.def, p.tops.def)}${bar("WORK", p.stats.craft, p.tops.craft)}
    <div class=kv>${kv("work", esc(p.work.map(([k, n]) => `${k} ${n}`).join(", ") || "none"))}</div>
    ${p.ranks.length ? `<h3>best at <span class=dim>(among all Pals, lv ${p.rank_level}, perfect IVs)</span></h3>
      <div>${p.ranks.map(([label, place, pctv]) => `<div><span class="${place <= 3 ? "good bold" : "value"}">#${place}</span> <span class=label>${esc(label)}</span> <span class=dim>${pctv}%</span></div>`).join("")}</div>` : ""}
    <h3>partner skill</h3>
    <div>${p.partner.name ? `<span class="partner-name">${esc(p.partner.name)}</span><div class=partner-text>${scaleText(p.partner.text)}</div>${hasScale(p.partner.text) ? `<div class=dim>${SCALE_NOTE}</div>` : ""}` : '<span class=dim>none</span>'}</div>
    <h3>skills it learns</h3>
    <table class=grid><tr><th class=num>lv</th><th>skill</th><th class=num>power</th><th class=num>cooldown</th><th>status</th></tr>
    ${p.skills.map((k) => `<tr><td class="num dim">${k.level}</td><td class="${elClass(k.element)}">${esc(k.name)}</td><td class=num>${k.power}</td><td class="num label">${k.cooldown}s</td>
      <td class=label>${esc(k.effects.map(([n, v]) => `${n} ${v}`).join(", "))}</td></tr>`).join("") || '<tr><td colspan=5 class=dim>none</td></tr>'}</table>
    <h3>breeding <span class=dim>power ${p.combi ?? "-"} &middot; ${p.recipe_count} parent pairs give it</span></h3>
    <table class=grid>${p.recipes.map((r) => `<tr><td>${pal(r.a)}</td><td class=dim>+</td><td>${pal(r.b)}</td><td>${r.special ? '<span class=warn>special pair</span>' : ""}</td></tr>`).join("") || '<tr><td class=dim>cannot be bred</td></tr>'}</table>
    ${p.recipe_count > p.recipes.length ? `<div class=dim>and ${p.recipe_count - p.recipes.length} more</div>` : ""}
    <h3>on the server</h3>
    <table class=grid>${p.server.map((x) => `<tr><td class=value>${esc(x.name)}</td><td class=label>caught ${x.caught}</td><td class=label>${x.deck ? "in Paldeck" : ""}</td><td class=label>${x.owned ? `owns ${x.owned}` : ""}</td></tr>`).join("") || '<tr><td class=dim>nobody yet</td></tr>'}</table>`;
  $("pedia-compare").onclick = () => setPediaRef(pedia.ref === p.id ? null : p.id);
  for (const a of $("pedia-detail").querySelectorAll("a.pedia-link"))
    a.onclick = (ev) => { ev.preventDefault(); pedia.id = a.dataset.id;
      for (const r of $("pedia-list").querySelectorAll("tr.sel")) r.classList.remove("sel");
      loadPediaEntry(); };
}

function setPediaRef(id) {
  pedia.ref = id;
  try { if (id) localStorage.setItem("paltop-pedia-ref", id); else localStorage.removeItem("paltop-pedia-ref"); } catch (e) {}
  loadPedia();
}

// a partner skill text with its numbers for 0/1/2/3/4 condenser stars
// ("50/53/56/60/65%"); a Pal's own number comes wrapped in \x01..\x02
const SCALE_NOTE = "numbers for 0/1/2/3/4 condenser stars";
// the game's rich text (\x03kind|key\x04text\x05 pieces): elements in
// their colour, items in yellow (hover for what they are), statuses, Pals,
// work, and the numbers in green, a Pal's own number in bold yellow
const STATUS_EL = {Burn: "fire", Wetness: "water", Electrical: "electricity", Freeze: "ice",
  IvyCling: "leaf", Muddy: "earth", Darkness: "dark", Poison: "dark", Stun: "normal"};
function scaleText(t) {
  let h = esc(t || "");
  const marked = /\u0003/.test(h);
  h = h.replace(/\u0003(\w+)\|([^\u0004]*)\u0004([^\u0005]*)\u0005/g, (m, kind, key, text) => {
    if (kind === "el") return `<span class="${elClass(key)} bold">${text}</span>`;
    if (kind === "item") return `<span class="item-ref" data-item="${key}">${text}</span>`;
    if (kind === "status") return `<span class="el-${STATUS_EL[key] || "normal"} bold">${text}</span>`;
    if (kind === "num") return `<span class="good scale">${text}</span>`;
    if (kind === "pal") return `<span class="value bold">${text}</span>`;
    return `<span class="value">${text}</span>`;
  });
  if (!marked)                     // plain text: colour the scales it has
    h = h.replace(/[+-]?[\d.?\u0001\u0002]+(?:\/[\d.?\u0001\u0002]+){2,}%?/g, (m) => `<span class="good scale">${m}</span>`);
  return h.replace(/\u0001([^\u0002]*)\u0002/g, '<b class="warn" title="this Pal\'s condenser rank">$1</b>');
}

// hovering an item shows what it is: its name, kind, the game's description
let itemInfo = null, itemTip = null;
const ITEM_KIND = (k) => String(k || "").replace(/^(Material|Food|Consume|Essential)/, "")
  .replace(/^Monster$/, "from Pals").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
document.addEventListener("mouseover", async (ev) => {
  const el = ev.target.closest && ev.target.closest(".item-ref[data-item]");
  if (!el) { if (itemTip) itemTip.classList.add("hidden"); return; }
  if (!itemInfo) { try { itemInfo = await api("/api/items"); } catch (e) { itemInfo = {}; } }
  const it = itemInfo[el.dataset.item];
  if (!it) return;
  if (!itemTip) { itemTip = document.createElement("div"); itemTip.id = "item-tip"; document.body.appendChild(itemTip); }
  const kind = ITEM_KIND(it.kind);
  itemTip.innerHTML = `<div class="item-ref bold">${esc(it.name)}</div>
    <div class=dim>${esc([String(it.type || "item").toLowerCase(), kind].filter(Boolean).join(" · "))}</div>
    ${it.desc ? `<div class=tip-desc>${esc(it.desc)}</div>` : ""}
    <div class=label>${it.price ? `sells for ${it.price} gold` : ""}${it.price && it.weight ? " · " : ""}${it.weight ? `weight ${it.weight}` : ""}</div>`;
  const r = el.getBoundingClientRect();
  itemTip.classList.remove("hidden");
  const w = itemTip.offsetWidth, h = itemTip.offsetHeight;
  itemTip.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left)) + "px";
  itemTip.style.top = (r.bottom + 6 + h > window.innerHeight ? r.top - h - 6 : r.bottom + 6) + "px";
});
const hasScale = (t) => /\d\/\d|\d\/\?|\u0001/.test(t || "");

// both partner skills side by side; what one has and the other lacks stands out
function partnerCompare(p, c) {
  const [a, b] = c.partner;
  const side = (x, other) => !x.name ? '<span class=dim>no partner skill</span>' : `
    <div class="partner-name">${esc(x.name)}</div>
    <div>${(x.tags || []).filter((t) => t !== "base").map((t) => `<span class="tag ${other.tags.includes(t) ? "" : "tag-only"}" title="${other.tags.includes(t) ? "both have this" : "only this one"}">${esc(t)}</span>`).join(" ")}</div>
    <div class=partner-text>${scaleText(x.text)}</div>`;
  return `<h3>partner skills ${hasScale(a.text) || hasScale(b.text) ? `<span class=dim>(${SCALE_NOTE})</span>` : ""}</h3>
    <table class="grid pedia-partners"><tr><th>${esc(p.name)}</th><th>${esc(c.ref.name)}</th></tr>
    <tr><td>${side(a, b)}</td><td>${side(b, a)}</td></tr></table>`;
}

// this Pal against the one others are compared with
function compareHtml(p) {
  const c = p.compare, r = c.ref;
  const diff = (v) => `<td class="num ${v == null ? "dim" : v > 0.005 ? "good" : v < -0.005 ? "crit" : "dim"}">${v == null ? "-" : pct(v)}</td>`;
  // a goal only one of them can do at all has no % difference
  const goalDiff = (v, a, b) => v != null ? diff(v)
    : a > b ? `<td class="num good">only ${esc(p.name)}</td>` : `<td class="num crit">only ${esc(r.name)}</td>`;
  const goalRow = ([label, a, b, v, pa, pb]) => `<tr><td class=label>${esc(label)}</td>
    <td class=num>${pa ? `${a}% <span class=dim>#${pa}</span>` : '<span class=dim>-</span>'}</td><td class=num>${pb ? `${b}% <span class=dim>#${pb}</span>` : '<span class=dim>-</span>'}</td>${goalDiff(v, a, b)}</tr>`;
  const up = (g) => g[3] != null ? g[3] > 0.005 : g[1] > g[2];
  const down = (g) => g[3] != null ? g[3] < -0.005 : g[2] > g[1];
  const better = c.goals.filter(up), worse = c.goals.filter(down).sort((x, y) => (x[3] ?? -2) - (y[3] ?? -2));
  const head = `<tr><th></th><th class=num>${esc(p.name)}</th><th class=num>${esc(r.name)}</th><th class=num>difference</th></tr>`;
  return `<div class="role-box cmp"><h3>compared with <a href="#" class="pedia-link ${elClass(r.els[0])}" data-id="${esc(r.id)}">${esc(r.name)}</a></h3>
    <table class=grid>${head}
      ${c.stats.map(([label, a, b, v]) => `<tr><td class=label>${esc(label)}</td><td class=num>${a}</td><td class=num>${b}</td>${diff(v)}</tr>`).join("")}
      ${c.work.map(([label, a, b]) => `<tr><td class=label>${esc(label)}</td><td class=num>${a || "-"}</td><td class=num>${b || "-"}</td>
        <td class="num ${a > b ? "good" : a < b ? "crit" : "dim"}">${a === b ? "=" : (a > b ? "+" : "") + (a - b)}</td></tr>`).join("")}</table>
    ${partnerCompare(p, c)}
    <h3>better at <span class=dim>(score as % of the best Pal, and place)</span></h3>
    <table class=grid>${head}${better.slice(0, 6).map(goalRow).join("") || '<tr><td colspan=4 class=dim>nothing</td></tr>'}</table>
    <h3>worse at</h3>
    <table class=grid>${head}${worse.slice(0, 6).map(goalRow).join("") || '<tr><td colspan=4 class=dim>nothing</td></tr>'}</table>
    ${c.goals.length > 12 ? `<details class=help><summary>every goal</summary><table class=grid>${head}${c.goals.map(goalRow).join("")}</table></details>` : ""}
    <p class=dim>Species against species at level ${p.rank_level} with perfect IVs and no passives, the way the list is ranked.</p></div>`;
}

// -- achievements: bronze, silver and gold for what players have done -------

const MEDAL = ["", "&#x1F949;", "&#x1F948;", "&#x1F947;"];     // none, bronze, silver, gold
const TIER = ["", "bronze", "silver", "gold"];
let ach = {data: null, who: null};
async function loadAchievements() {
  try { ach.data = await api("/api/achievements"); } catch (e) { return; }
  const d = ach.data;
  if (!ach.who || !d.board.some((b) => b.pid === ach.who)) ach.who = (d.board.find((b) => b.pid === d.me) || d.board[0] || {}).pid;
  const when = (ts) => new Date(ts * 1000).toLocaleDateString([], {day: "numeric", month: "short"});
  const top = Math.max(1, ...d.board.map((b) => b.points));
  $("ach-count").textContent = `${d.board.length} players · ${d.max} points to win`;
  $("ach-board").innerHTML = "<tr><th></th><th>player</th><th>points</th><th class=num>&#x1F947;</th><th class=num>&#x1F948;</th><th class=num>&#x1F949;</th></tr>" +
    d.board.map((b, i) => {
      const n = (t) => b.rows.filter((r) => r.tier === t).length;
      return `<tr class="click ${b.pid === d.me ? "me" : ""}" data-pid="${esc(b.pid)}"><td>${["&#x1F947;", "&#x1F948;", "&#x1F949;"][i] || `<span class=dim>${i + 1}.</span>`}</td>
        <td class="value bold">${esc(b.name)}</td>
        <td><div class=race-bar><i style="width:${Math.round(100 * b.points / top)}%"></i><span>${b.points} <span class=dim>/ ${d.max}</span></span></div></td>
        <td class=num>${n(3) || "-"}</td><td class=num>${n(2) || "-"}</td><td class=num>${n(1) || "-"}</td></tr>`;
    }).join("");
  for (const tr of $("ach-board").querySelectorAll("tr.click")) tr.onclick = () => { ach.who = tr.dataset.pid; renderAchievements(); };
  $("ach-feed").innerHTML = d.feed.map((f) => `<div><span class=dim>${when(f.ts)}</span> <b>${esc(f.who)}</b> got
    <span class="ach-${f.tier}">${f.icon} ${esc(f.title)}</span> <span class=dim>(${f.tier})</span></div>`).join("")
    || '<span class=dim>none yet - new unlocks show here from now on</span>';
  $("ach-firsts").innerHTML = d.firsts.map((f) => `<div>&#x1F3C1; <b>${esc(f.who)}</b> was first to level <b class=value>${f.level}</b> <span class=dim>${when(f.ts)}</span></div>`).join("")
    || '<span class=dim>none yet - the next level anyone reaches first shows here</span>';
  $("ach-note").textContent = `Tiers reached before ${when(d.since)} have no date: paltop only sees what the save holds, and started keeping track then. Numbers come from each player's in-game records (bosses, towers, effigies, ...), their Pals, and paltop's own history (level, time played, the Paldeck race).`;
  renderAchievements();
}
function renderAchievements() {
  const d = ach.data;
  if (!d) return;
  $("ach-players").innerHTML = d.board.map((b) => `<button class="btn ${b.pid === ach.who ? "active" : ""}" data-pid="${esc(b.pid)}">${esc(b.name)} <span class=dim>${b.points}</span></button>`).join("");
  for (const b of $("ach-players").querySelectorAll("button")) b.onclick = () => { ach.who = b.dataset.pid; renderAchievements(); };
  const me = d.board.find((b) => b.pid === ach.who);
  if (!me) { $("ach-grid").innerHTML = ""; return; }
  const when = (ts) => new Date(ts * 1000).toLocaleDateString([], {day: "numeric", month: "short"});
  $("ach-grid").innerHTML = me.rows.slice().sort((a, b) => b.tier - a.tier).map((r) => {
    const lo = r.tier ? r.tiers[r.tier - 1] : 0, hi = r.next;
    const frac = hi ? Math.min(1, (r.value - lo) / Math.max(1, hi - lo)) : 1;
    return `<div class="ach-card ach-${TIER[r.tier] || "none"}">
      <div class=ach-icon>${r.icon}</div>
      <div class=ach-body><div class=ach-title>${esc(r.title)} ${r.tier ? `<span title="${TIER[r.tier]}">${MEDAL[r.tier]}</span>` : ""}</div>
        <div class=dim>${esc(r.desc)}</div>
        <div class=ach-bar><i style="width:${Math.round(frac * 100)}%"></i></div>
        <div class=label><b class=value>${r.value}</b>${hi ? ` <span class=dim>/ ${hi} for ${TIER[r.tier + 1]}</span>` : ' <span class=good>all tiers!</span>'}
          ${r.when ? ` <span class=dim>&middot; ${TIER[r.tier]} ${when(r.when)}</span>` : ""}</div></div></div>`;
  }).join("");
}

// -- the Paldeck race: who has found the most Pals, who was first ------------

async function loadRace() {
  let d;
  try { d = await api(`/api/palpedia/race?spoilers=${pedia.spoilers ? 1 : 0}`); } catch (e) { return; }
  const top = Math.max(1, ...d.board.map((b) => b.found));
  const when = (ts) => { const t = new Date(ts * 1000); return t.toLocaleDateString([], {day: "2-digit", month: "2-digit"}) + " " + t.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false}); };
  const medal = (i) => ["&#x1F947;", "&#x1F948;", "&#x1F949;"][i] || `<span class=dim>${i + 1}.</span>`;
  $("pedia-race").innerHTML = `
    <div class=race-head><h3>Paldeck race</h3><span class=dim>${d.server} of ${d.all} Pals found on the server</span>
      <button class="btn small" id="race-close">close</button></div>
    <table class="grid race-board"><tr><th></th><th>player</th><th>Pals found</th><th class=num title="the Pals they found before anyone else">first</th>
      <th class=num title="Pals nobody else on the server has">only them</th><th class=num>caught</th><th>latest find</th></tr>
    ${d.board.map((b, i) => `<tr class="${b.pid === d.me ? "me" : ""}"><td>${medal(i)}</td>
      <td class="value bold">${esc(b.name)}</td>
      <td><div class=race-bar><i style="width:${Math.round(100 * b.found / top)}%"></i><span>${b.found} <span class=dim>/ ${d.all} &middot; ${Math.round(100 * b.found / d.all)}%</span></span></div></td>
      <td class="num warn">${b.firsts || "-"}</td><td class="num good">${b.only || "-"}</td><td class="num label">${b.caught}</td>
      <td class=label>${b.latest ? `<a href="#" class=pedia-link data-id="${esc(b.latest.id)}">${esc(b.latest.name)}</a> <span class=dim>${when(b.latest.ts)}</span>` : '<span class=dim>-</span>'}</td></tr>`).join("")}</table>
    <h3>latest finds</h3>
    <div class=race-feed>${d.latest.map((x) => `<div><span class=dim>${when(x.ts)}</span> <b>${esc(x.who)}</b> found
      <a href="#" class="pedia-link ${elClass((x.els || [])[0])}" data-id="${esc(x.id)}">${esc(x.name)}</a>${x.first ? ' <span class=warn>first on the server!</span>' : ""}</div>`).join("")
      || '<span class=dim>none yet - new finds show here from now on</span>'}</div>
    <p class=dim>Pals found counts the in-game Paldeck and everything caught. The game keeps no dates in the Paldeck: firsts
      from before ${when(d.since)} are estimated from the oldest Pal of each kind a player still owns, and since then paltop
      notes each new find as it happens.</p>`;
  $("race-close").onclick = () => { $("pedia-race").classList.add("hidden"); };
  for (const a of $("pedia-race").querySelectorAll("a.pedia-link"))
    a.onclick = (ev) => { ev.preventDefault(); pedia.id = a.dataset.id; loadPediaEntry(); };
}
$("pedia-race-btn").onclick = () => {
  const box = $("pedia-race");
  box.classList.toggle("hidden");
  if (!box.classList.contains("hidden")) loadRace();
};

$("pedia-sort").onchange = () => { pedia.sort = $("pedia-sort").value; pedia.id = null; loadPedia(); };
$("pedia-show").onchange = () => { pedia.show = $("pedia-show").value; pedia.id = null; loadPedia(); };
$("pedia-kind").onchange = () => { pedia.kind = $("pedia-kind").value; pedia.id = null; loadPedia(); };
$("pedia-spoilers").onclick = () => {
  pedia.spoilers = !pedia.spoilers;
  try { localStorage.setItem("paltop-spoilers", pedia.spoilers ? "1" : "0"); } catch (e) {}
  loadPedia();
};
let pediaTimer = null;
$("pedia-search").addEventListener("input", () => { clearTimeout(pediaTimer); pediaTimer = setTimeout(() => {
  pedia.q = $("pedia-search").value.trim(); pedia.id = null; loadPedia(); }, 250); });

// -- start -----------------------------------------------------------------

let timer = null;
async function start() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  document.dispatchEvent(new Event("paltop-login"));         // battles may start
  let tab = "overview";
  try { tab = localStorage.getItem("paltop-tab") || tab; } catch (e) {}
  if (!$("tab-" + tab)) tab = "overview";       // a tab that is gone
  setTab(tab);             // before loading, so a quick click is not undone
  try {
    state.me = await api("/api/me");
    $("me").textContent = state.me.name;
    pollChat();
    toggleChat(false);             // the chat button in the corner
    if (state.me.linked_now && state.me.player) await notice("linked", linkedText(state.me.player));
    if (!state.player && state.me.pid) state.player = state.me.pid;   // your own Pals first
  } catch (e) { state.me = null; }
  await loadOverview();
  if (state.tab === "pals") openPals();
  clearInterval(timer);
  timer = setInterval(loadOverview, 15000);
}

(async () => {
  try { await api("/api/overview"); start(); } catch (e) { showLogin(); }
})();

// -- battle music (music.js): only while the battles tab is open and the
// window has focus; the viewer turns it on and sets how loud
function musicWanted() {
  if (!window.paltopMusic) return;
  window.paltopMusic.want(state.tab === "battles" && document.hasFocus() && !document.hidden &&
    !$("app").classList.contains("hidden"));
}
window.addEventListener("focus", musicWanted);
window.addEventListener("blur", musicWanted);
document.addEventListener("visibilitychange", musicWanted);
if (window.paltopMusic && $("music-on")) {
  const m = window.paltopMusic;
  $("music-on").classList.toggle("active", m.enabled);
  $("music-vol").value = Math.round(m.volume * 100);
  $("music-on").onclick = () => { m.setEnabled(!m.enabled); $("music-on").classList.toggle("active", m.enabled); musicWanted(); };
  $("music-vol").addEventListener("input", () => m.setVolume($("music-vol").value / 100));
  $("sounds-on").classList.toggle("active", m.sfx.enabled);
  $("sounds-on").onclick = () => { m.sfx.setEnabled(!m.sfx.enabled); $("sounds-on").classList.toggle("active", m.sfx.enabled); };
}
