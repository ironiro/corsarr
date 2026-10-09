"use strict";
// Corsarr admin GUI: status, event log and configuration. No build step, no dependencies.
// Every server-provided string is inserted as text (never as HTML): log lines contain chat messages.

let T = {};                 // GUI texts in the active language, from /api/i18n
let tab = "status";
let status = null;
let configData = null;
const dirty = {};           // field name -> new value
const resets = new Set();   // fields whose GUI value should be dropped
let events = [];
let lastEventId = 0;
let timers = [];

// --- helpers -------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function tr(key, vars) {
  let s = T[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, v);
  return s;
}

async function api(path, options = {}) {
  const opts = { ...options, headers: { "X-Corsarr": "1", ...(options.headers || {}) } };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { showLogin(); throw new Error("unauthorized"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(data.error || res.statusText); e.data = data; throw e; }
  return data;
}

function ago(ts, now) {
  if (!ts) return T.never;
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return tr("ago_s", { n: s });
  if (s < 3600) return tr("ago_m", { n: Math.round(s / 60) });
  if (s < 86400) return tr("ago_h", { n: Math.round(s / 3600) });
  return tr("ago_d", { n: Math.round(s / 86400) });
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function clearTimers() { timers.forEach(clearInterval); timers = []; }

async function loadTexts() {
  const data = await fetch("/api/i18n").then(r => r.json());
  T = data.texts;
  document.documentElement.lang = data.lang;
  document.title = T.title;
}

// --- login ----------------------------------------------------------------------------
function showLogin(error) {
  clearTimers();
  const pw = h("input", { type: "password", id: "pw", autocomplete: "current-password", required: true });
  const msg = h("div", { class: error ? "notice error" : "" }, error || "");
  const form = h("form", {
    onsubmit: async e => {
      e.preventDefault();
      try {
        await api("/api/login", { method: "POST", body: { password: pw.value } });
        showApp();
      } catch (err) {
        showLogin(err.message === "unauthorized" ? T.login_failed : err.message);
      }
    },
  },
    h("label", { for: "pw" }, T.password), pw,
    h("button", { class: "btn primary", type: "submit" }, T.login), msg);
  document.getElementById("app").replaceChildren(
    h("div", { class: "login" }, h("div", { class: "card" },
      h("h1", {}, "🎬 ", T.title), form, h("p", { class: "hint", style: "margin-top:14px" }, T.login_hint))));
  pw.focus();
}

// --- shell ---------------------------------------------------------------------------
function showApp() {
  clearTimers();
  const tabs = [["status", T.tab_status], ["events", T.tab_events], ["config", T.tab_config]];
  const nav = h("nav", { role: "tablist" }, tabs.map(([id, label]) =>
    h("button", { role: "tab", "aria-selected": String(tab === id), onclick: () => { tab = id; showApp(); } }, label)));
  const header = h("header", {}, h("div", { class: "bar" },
    h("div", { class: "brand" }, "🎬 ", T.title, h("span", { id: "statepill" })), nav,
    h("button", { class: "link", id: "logout", hidden: true, onclick: logout }, T.logout)));
  const main = h("main", { id: "main" });
  document.getElementById("app").replaceChildren(header, main);
  ({ status: viewStatus, events: viewEvents, config: viewConfig })[tab](main);
  refreshStatus();
  timers.push(setInterval(refreshStatus, 10000));
}

async function logout() {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  showLogin();
}

function stateInfo(s) {
  if (s.state === "running" && s.outage) return ["warn", T.state_outage];
  return {
    running: ["ok", T.state_running], starting: ["warn", T.state_starting], stopped: ["", T.state_stopped],
    unconfigured: ["warn", T.state_unconfigured], error: ["error", T.state_error],
  }[s.state] || ["", s.state];
}

async function refreshStatus() {
  try {
    status = await api("/api/status");
  } catch (e) {
    if (e.message !== "unauthorized") renderNetworkError();
    return;
  }
  document.getElementById("neterr")?.remove();
  const logoutBtn = document.getElementById("logout");
  if (logoutBtn) logoutBtn.hidden = !status.auth;  // no login configured = nothing to sign out of
  const [cls, label] = stateInfo(status);
  const pill = document.getElementById("statepill");
  if (pill) pill.replaceChildren(h("span", { class: `pill ${cls}` }, label));
  if (tab === "status") renderStatus();
}

function renderNetworkError() {
  const pill = document.getElementById("statepill");
  if (pill) pill.replaceChildren(h("span", { class: "pill error" }, T.network_error));
  const main = document.getElementById("main");
  if (main && !document.getElementById("neterr")) {
    const banner = loadError(() => showApp());
    banner.id = "neterr";
    banner.style.margin = "0 0 16px";
    main.prepend(banner);
  }
}

// --- status tab ------------------------------------------------------------------------
function viewStatus(main) {
  main.append(h("div", { id: "status" }));
  if (status) renderStatus();
}

function renderStatus() {
  const box = document.getElementById("status");
  if (!box || !status) return;
  const s = status;
  const [cls, label] = stateInfo(s);
  const checkBtn = h("button", { class: "btn", onclick: e => run(e.target, T.checking, "/api/check") }, T.check_now);
  const restartBtn = h("button", { class: "btn", onclick: e => run(e.target, T.restarting, "/api/restart") }, T.restart);

  const notices = [];
  if (s.missing.length) {
    notices.push(h("div", { class: "notice warn" }, tr("missing_fields", { fields: s.missing.join(", ") }), " ",
      h("button", { class: "link", onclick: () => { tab = "config"; showApp(); } }, "→ " + T.tab_config)));
  }
  if (s.state === "error" && s.state_detail) notices.push(h("div", { class: "notice error" }, s.state_detail));

  const botCard = h("div", { class: "card" },
    h("div", { class: "row" },
      h("h2", {}, T.bot_state, s.bot_username ? ` @${s.bot_username}` : ""),
      h("span", { class: `pill ${cls}` }, label), h("span", { class: "spacer" }), checkBtn, restartBtn),
    notices,
    h("dl", { class: "facts" },
      h("dt", {}, T.uptime), h("dd", {}, new Date(s.started_at * 1000).toLocaleString()),
      h("dt", {}, T.data_dir), h("dd", {}, s.data_dir),
      h("dt", {}, T.log_file), h("dd", {}, s.log_file)));

  const services = h("div", { class: "services" },
    ["telegram", "claude", "jellyfin", "jellyseerr", "webhook", "sonarr", "radarr"].map(name => {
      const st = s.services[name];
      const pillCls = { ok: "ok", error: "error" }[st.status] || "";
      const text = { ok: T.status_ok, error: T.status_error, disabled: T.status_disabled }[st.status] || T.status_unknown;
      return h("div", { class: `service ${pillCls}` },
        h("div", { class: "row" }, h("h3", {}, T["svc_" + name]), h("span", { class: "spacer" }),
          h("span", { class: `pill ${pillCls}` }, text)),
        h("div", { class: "detail" }, st.detail || "–"),
        h("div", { class: "meta" },
          `${T.last_check}: ${ago(st.checked_at, s.now)}`,
          st.status === "error" ? ` · ${T.last_ok}: ${ago(st.last_ok, s.now)}` : ""));
    }));

  box.replaceChildren(botCard, h("h2", { style: "font-size:16px;margin:24px 0 12px" }, T.connections), services);
}

async function run(btn, busyLabel, path) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyLabel;
  try {
    status = await api(path, { method: "POST" });
    renderStatus();
  } catch (e) { /* status refresh shows the problem */ }
  btn.disabled = false;
  btn.textContent = old;
  refreshStatus();
}

// --- events tab -------------------------------------------------------------------------
const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };
let levelFilter = 20;
let search = "";
let follow = true;

function viewEvents(main) {
  const level = h("select", { onchange: e => { levelFilter = +e.target.value; renderEvents(); } },
    [[0, T.level_all], [20, T.level_info], [30, T.level_warning], [40, T.level_error]].map(([v, l]) =>
      h("option", { value: v, selected: v === levelFilter }, l)));
  const input = h("input", { type: "text", placeholder: T.search, value: search,
    oninput: e => { search = e.target.value.toLowerCase(); renderEvents(); } });
  const followBox = h("input", { type: "checkbox", checked: follow, onchange: e => { follow = e.target.checked; } });
  main.append(
    h("div", { class: "toolbar" }, level, input, h("label", {}, followBox, T.autoscroll)),
    h("div", { class: "log", id: "log" }),
    h("p", { class: "hint", style: "margin-top:8px" }, T.events_hint));
  renderEvents();
  pollEvents();
  timers.push(setInterval(pollEvents, 3000));
}

async function pollEvents() {
  try {
    const data = await api(`/api/events?after=${lastEventId}`);
    if (data.events.length) {
      events = events.concat(data.events).slice(-1000);
      lastEventId = events[events.length - 1].id;
      renderEvents();
    }
  } catch (e) { /* shown via status pill */ }
}

function renderEvents() {
  const log = document.getElementById("log");
  if (!log) return;
  const shown = events.filter(e => (LEVELS[e.level] || 0) >= levelFilter &&
    (!search || e.message.toLowerCase().includes(search) || e.logger.toLowerCase().includes(search)));
  const rows = shown.map(e => h("div", { class: `ev ${e.level}` },
    h("span", { class: "ts", title: new Date(e.ts * 1000).toLocaleString() }, clock(e.ts)),
    h("span", { class: "lv" }, e.level),
    h("span", { class: "msg" }, h("span", { class: "src" }, e.logger), e.message)));
  log.replaceChildren(...(rows.length ? rows : [h("div", { class: "empty" }, T.no_events)]));
  if (follow) log.scrollTop = log.scrollHeight;
}

// --- config tab ---------------------------------------------------------------------------
const GROUPS = ["telegram", "claude", "jellyfin", "jellyseerr", "web", "system"];

async function viewConfig(main) {
  const box = h("div", { id: "config" });
  main.append(box);
  try {
    configData = await api("/api/config");
  } catch (e) {
    if (e.message !== "unauthorized") renderNetworkError();  // banner with "try again" instead of an empty page
    return;
  }
  renderConfig();
}

// Shown instead of an empty page when the bot cannot be reached (stopped, restarting, network).
function loadError(retry) {
  return h("div", { class: "notice error row" }, T.network_error, h("span", { class: "spacer" }),
    h("button", { class: "btn small", onclick: retry }, T.retry));
}

function renderConfig(message) {
  const box = document.getElementById("config");
  if (!box || !configData) return;

  // Behaviour settings: saved immediately.
  const behaviour = h("div", { class: "card" }, h("h2", {}, T.behaviour), h("p", { class: "hint" }, T.behaviour_hint),
    configData.settings.map(s => {
      const label = T["s_" + s.key] || s.key;
      if (s.kind === "bool") {
        return h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: s.value, onchange: e => saveSetting(s.key, e.target.checked) }), label);
      }
      return h("div", { class: "field" }, h("label", {}, label),
        h("input", { type: "number", min: s.min, max: s.max, value: s.value,
          onchange: e => saveSetting(s.key, e.target.value) }));
    }));

  // Connection and system fields: saved together, restart the bot.
  const groups = GROUPS.map(g => {
    const fields = configData.fields.filter(f => f.group === g);
    return h("div", { class: "group" }, h("h3", {}, T["group_" + g]), fields.map(fieldRow));
  });
  const saveBtn = h("button", { class: "btn primary", onclick: e => saveConfig(e.target) }, T.save);
  const savebar = h("div", { class: "savebar" }, saveBtn, message || "");
  const conn = h("div", { class: "card" }, h("h2", {}, T.connection_settings),
    h("p", { class: "hint" }, T.connection_hint), groups, savebar);

  box.replaceChildren(behaviour, conn);
}

function fieldRow(f) {
  const id = "f_" + f.name;
  const current = f.name in dirty ? dirty[f.name] : (resets.has(f.name) ? "" : f.value);
  let input;
  const onInput = e => { dirty[f.name] = e.target.value; resets.delete(f.name); };
  if (!f.editable) {
    input = h("input", { type: "text", id, value: f.value, disabled: true });
  } else if (f.kind === "choice") {
    input = h("select", { id, onchange: onInput },
      f.choices.map(c => h("option", { value: c, selected: c === current }, c)));
  } else {
    input = h("input", {
      id, type: f.secret ? "password" : (f.kind === "int" ? "text" : "text"),
      inputmode: f.kind === "int" ? "numeric" : null, value: f.secret ? (dirty[f.name] || "") : current,
      placeholder: f.secret ? (f.is_set ? T.secret_set : T.secret_unset) : (f.default || ""),
      autocomplete: f.secret ? "new-password" : "off", spellcheck: "false", oninput: onInput,
      class: f.error ? "invalid" : null,
    });
  }
  const info = [];
  if (!f.editable) info.push(T.readonly);
  else if (f.source === "gui" && !resets.has(f.name)) {
    info.push(T.from_gui);
    info.push(h("button", { class: "link", title: T.reset_hint, onclick: () => {
      resets.add(f.name); delete dirty[f.name]; renderConfig();
    } }, T.reset));
  } else if (f.source === "env") info.push(T.from_env);
  return h("div", { class: "field" },
    h("label", { for: id }, T["f_" + f.name] || f.name, f.required ? h("span", { class: "req", title: T.required }, " *") : "",
      h("span", { class: "name" }, f.name)),
    h("div", {}, input,
      info.length ? h("div", { class: "info" }, info.map(i => typeof i === "string" ? h("span", {}, i) : i)) : null,
      f.error ? h("div", { class: "err" }, f.error) : null));
}

async function saveSetting(key, value) {
  try {
    configData = await api("/api/settings", { method: "PUT", body: { [key]: value } });
    renderConfig(h("span", { class: "pill ok" }, T.saved));
  } catch (e) {
    renderConfig(h("span", { class: "pill error" }, T.save_failed + ": " + Object.values(e.data?.errors || {}).join(", ")));
  }
}

async function saveConfig(btn) {
  btn.disabled = true;
  btn.textContent = T.saving;
  const oldLang = document.documentElement.lang;
  try {
    const res = await api("/api/config", { method: "PUT", body: { values: dirty, reset: [...resets] } });
    Object.keys(dirty).forEach(k => delete dirty[k]);
    resets.clear();
    configData = res;
    await loadTexts();
    if (document.documentElement.lang !== oldLang) { showApp(); return; }
    let msg = T.saved;
    if (res.app_restart) msg = T.saved_app_restart;
    else if (res.restart) msg = T.saved_restart;
    renderConfig(h("span", { class: res.app_restart ? "pill warn" : "pill ok" }, msg));
    refreshStatus();
  } catch (e) {
    const errs = e.data?.errors || {};
    for (const f of configData.fields) f.error = errs[f.name] || f.error;
    renderConfig(h("span", { class: "pill error" }, T.save_failed));
  }
}

// --- start ---------------------------------------------------------------------------------
(async () => {
  await loadTexts();
  try {
    await api("/api/status");
    showApp();
  } catch (e) {
    if (e.message !== "unauthorized") showLogin(T.network_error);
  }
})();
