"use strict";
// Corsarr admin GUI: status, event log and configuration. No build step, no dependencies.
// Every server-provided string is inserted as text (never as HTML): log lines contain chat messages.

let T = {};                 // GUI texts in the active language, from /api/i18n
let tab = "status";
let status = null;
let configData = null;
let updateInfo = null;      // /api/update: installed vs. latest version
let updateTimer = null;
let loadedVersion = null;   // version this page was loaded with – reload once the server runs another
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
  // After an update (button, command line or automatic) load the new interface instead of the old one.
  if (loadedVersion === null) loadedVersion = status.version || "";
  else if (status.version && status.version !== loadedVersion) { location.reload(); return; }
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
    ["telegram", "llm", "jellyfin", "jellyseerr", "webhook", "sonarr", "radarr"].map(name => {
      const st = s.services[name];
      const title = name === "llm" && s.llm ? s.llm.name : T["svc_" + name];
      const untested = name === "llm" && s.llm && !s.llm.recommended
        ? h("span", { class: "pill warn", title: tr("provider_warn", { name: s.llm.name }) }, T.untested) : null;
      const pillCls = { ok: "ok", error: "error" }[st.status] || "";
      const text = { ok: T.status_ok, error: T.status_error, disabled: T.status_disabled }[st.status] || T.status_unknown;
      return h("div", { class: `service ${pillCls}` },
        h("div", { class: "row" }, h("h3", {}, title), untested, h("span", { class: "spacer" }),
          h("span", { class: `pill ${pillCls}` }, text)),
        h("div", { class: "detail" }, st.detail || "–"),
        h("div", { class: "meta" },
          `${T.last_check}: ${ago(st.checked_at, s.now)}`,
          st.status === "error" ? ` · ${T.last_ok}: ${ago(st.last_ok, s.now)}` : ""));
    }));

  box.replaceChildren(botCard, h("div", { id: "version" }),
    h("h2", { style: "font-size:16px;margin:24px 0 12px" }, T.connections), services,
    h("p", { class: "disclaimer" }, T.cost_disclaimer));
  renderVersion();
  if (!updateInfo) loadUpdate(false);
}

// --- version and updates -----------------------------------------------------------------
const short = sha => (sha || "").slice(0, 7);
const fmtVersion = v => !v ? T.update_unknown : /^v\d/.test(v) ? v : short(v);  // release tag or commit

async function loadUpdate(force) {
  try {
    updateInfo = await api(`/api/update${force ? "?force=1" : ""}`);
  } catch (e) { return; }
  renderVersion();
  if (updateInfo.updating) watchUpdate();
}

function renderVersion() {
  const box = document.getElementById("version");
  if (!box) return;
  const u = updateInfo;
  if (!u) { box.replaceChildren(); return; }
  const checkBtn = h("button", { class: "btn", onclick: async e => {
    e.target.disabled = true; e.target.textContent = T.checking; await loadUpdate(true);
  } }, T.check_updates);
  const rows = [];
  rows.push(h("dl", { class: "facts" },
    h("dt", {}, T.version_current), h("dd", {}, fmtVersion(u.current)),
    u.latest ? [h("dt", {}, T.version_latest), h("dd", {}, fmtVersion(u.latest))] : null,
    h("dt", {}, T.channel), h("dd", {}, T["channel_" + u.channel] || u.channel)));

  let pill;
  const offer = u.target && (u.behind > 0 || u.downgrade);
  if (u.error) {
    pill = h("span", { class: "pill error" }, T.status_error);
    rows.push(h("div", { class: "notice error" }, tr("update_check_failed", { error: u.error })));
  } else if (u.updating) {
    pill = h("span", { class: "pill warn" }, T.updating_short);
    rows.push(h("div", { class: "notice warn" }, T.updating));
  } else if (offer) {
    pill = h("span", { class: "pill warn" }, u.channel === "dev" ? tr("update_available", { n: u.behind })
      : tr(u.downgrade ? "update_older" : "update_release", { version: u.target }));
    if (u.downgrade) rows.push(h("div", { class: "notice warn" }, tr("update_downgrade_confirm", { version: u.target })));
    if (u.commits.length) {
      rows.push(h("ul", { class: "changes" }, u.commits.map(c =>
        h("li", {}, h("code", {}, short(c.sha)), " ", c.message, h("span", { class: "muted" }, " · " + c.date.slice(0, 10))))));
    }
    for (const r of u.releases) {
      rows.push(h("details", { class: "release", open: r === u.releases[0] },
        h("summary", {}, r.name, r.prerelease ? h("span", { class: "pill warn" }, "beta") : "",
          h("span", { class: "muted" }, " · " + r.date.slice(0, 10))),
        h("pre", { class: "notes" }, r.notes || "–")));
    }
    if (u.kind === "service") {
      rows.push(h("button", { class: "btn primary", onclick: startUpdate }, T.update_now));
    } else if (u.kind === "docker") {
      rows.push(h("p", { class: "hint" }, T.update_docker, " ", h("code", {}, "docker compose pull && docker compose up -d")));
    } else {
      rows.push(h("p", { class: "hint" }, T.update_manual, " ",
        h("code", {}, u.channel === "dev" ? "git pull" : `git fetch --tags && git checkout ${u.target}`)));
    }
  } else if (u.latest) {
    pill = h("span", { class: "pill ok" }, T.up_to_date);
  } else if (u.channel !== "dev") {
    rows.push(h("p", { class: "hint" }, tr("no_release", { channel: T["channel_" + u.channel] || u.channel })));
  }
  if (u.log && (u.updating || offer || u.error)) {
    rows.push(h("details", { open: u.updating }, h("summary", {}, T.update_log), h("pre", { class: "log-tail" }, u.log)));
  }
  box.replaceChildren(h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", {}, T.version), pill || "", h("span", { class: "spacer" }), checkBtn), rows));
}

async function startUpdate(e) {
  const downgrade = !!updateInfo.downgrade;  // already explained in the card; confirm once more
  if (!confirm(downgrade ? tr("update_downgrade_confirm", { version: updateInfo.target }) : T.update_confirm)) return;
  e.target.disabled = true;
  try {
    await api("/api/update", { method: "POST", body: { downgrade } });
  } catch (err) {
    alert(err.message);
    e.target.disabled = false;
    return;
  }
  updateInfo = { ...updateInfo, updating: true };
  renderVersion();
  watchUpdate();
}

// The bot restarts during the update, so requests fail for a while – keep polling until it is back
// and reports a new version (or the update finished without one).
function watchUpdate() {
  if (updateTimer) return;
  updateTimer = setInterval(async () => {
    let u;
    try { u = await api("/api/update?force=1"); } catch (e) { return; }
    updateInfo = u;
    renderVersion();
    if (!u.updating) {
      clearInterval(updateTimer);
      updateTimer = null;
      location.reload();  // the update may have changed the interface itself
    }
  }, 4000);
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
const GROUPS = ["telegram", "llm", "jellyfin", "jellyseerr", "web", "system"];

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
  loadOptions("models");
  loadOptions("users");
}

// Shown instead of an empty page when the bot cannot be reached (stopped, restarting, network).
function loadError(retry) {
  return h("div", { class: "notice error row" }, T.network_error, h("span", { class: "spacer" }),
    h("button", { class: "btn small", onclick: retry }, T.retry));
}

// --- language model provider: the selected one decides which fields are shown and used ----------------
function fieldValue(name) {
  if (name in dirty) return dirty[name];
  const f = configData.fields.find(f => f.name === name);
  return f && !resets.has(name) ? f.value || f.default : (f ? f.default : "");
}
const providerId = () => fieldValue("LLM_PROVIDER") || "claude";
const providerInfo = id => (configData.providers || []).find(p => p.id === id) || { id, name: id, fields: {} };

function providerNotice(id) {
  const p = providerInfo(id);
  if (p.recommended) return null;
  return [h("div", { class: "notice warn" }, tr("provider_warn", { name: p.name })),
          p.local ? h("div", { class: "notice" }, T.provider_local_hint) : null];
}

// --- pickers: Jellyfin accounts and models, loaded live (also with values not saved yet) ---------
const opts = { models: null, modelsError: "", modelsFor: "", users: null, usersError: "" };
let savedSetting = null;  // behaviour setting that was just saved – shows "✓ saved" next to it

async function loadOptions(which) {
  let body, provider;
  if (which === "models") {
    provider = providerId();
    const pf = providerInfo(provider).fields;
    body = { provider, api_key: (pf.key && dirty[pf.key]) || "", url: (pf.url && dirty[pf.url]) || "" };
  } else {
    body = { url: dirty.JELLYFIN_URL || "", api_key: dirty.JELLYFIN_API_KEY || "" };
  }
  try {
    const res = await api(`/api/options/${which === "models" ? "models" : "jellyfin-users"}`, { method: "POST", body });
    if (which === "models" && provider !== providerId()) return;  // provider changed meanwhile
    opts[which] = res[which] && res[which].length ? res[which] : null;
    opts[which + "Error"] = res.error || "";
  } catch (e) {
    opts[which] = null;
    opts[which + "Error"] = e.message;
  }
  if (which === "models") opts.modelsFor = provider;
  renderConfig();
}

const fmtUsd = x => {
  const v = x >= 0.1 ? x.toFixed(2) : x.toFixed(3);
  return document.documentElement.lang === "de" ? v.replace(".", ",") : v;
};
const modelInfo = id => (opts.models || []).find(m => m.id === id);

function modelWarning(id) {
  const m = modelInfo(id);
  if (!m || m.recommended || !m.cost) return null;
  const c = m.cost;
  const text = tr("model_warn", { name: m.name, factor: c.factor, cost: fmtUsd(c.per_suggestion),
                                  n: Math.max(1, Math.round(5 / c.per_suggestion)).toLocaleString() });
  return h("div", { class: c.factor >= 100 ? "notice error" : "notice warn" }, c.factor >= 100 ? T.model_warn_strong + " " : "", text);
}

function pickerInput(f, id, current, onInput) {
  if (f.name === "LLM_PROVIDER") {
    const options = (configData.providers || []).map(p => h("option", { value: p.id, selected: p.id === current },
      tr(p.recommended ? "provider_recommended" : "provider_untested", { name: p.name })));
    return h("select", { id, onchange: e => {
      onInput(e); opts.models = null; opts.modelsError = ""; renderConfig(); loadOptions("models");
    } }, options);
  }
  if (f.name === providerInfo(providerId()).fields.model && opts.models && opts.modelsFor === providerId()) {
    const ids = opts.models.map(m => m.id);
    const label = m => !m.cost ? m.name
      : m.recommended ? tr("model_option_recommended", { name: m.name, cost: fmtUsd(m.cost.per_suggestion) })
      : tr("model_option", { name: m.name, factor: m.cost.factor, cost: fmtUsd(m.cost.per_suggestion) });
    const options = opts.models.map(m => h("option", { value: m.id, selected: m.id === current }, label(m)));
    if (!current) options.unshift(h("option", { value: "", selected: true }, "–"));
    else if (!ids.includes(current)) options.unshift(h("option", { value: current, selected: true }, current));
    return h("select", { id, onchange: e => { onInput(e); renderConfig(); } }, options);
  }
  if (f.name === "JELLYFIN_USER" && opts.users) {
    const options = opts.users.map(u => h("option", { value: u, selected: u === current }, u));
    if (!current) options.unshift(h("option", { value: "", selected: true }, "–"));
    else if (!opts.users.includes(current)) options.unshift(h("option", { value: current, selected: true }, tr("user_not_found", { name: current })));
    return h("select", { id, onchange: onInput }, options);
  }
  return null;
}

// --- rendering --------------------------------------------------------------------------------------
function renderConfig(message) {
  const box = document.getElementById("config");
  if (!box || !configData) return;

  // Behaviour settings: saved immediately – the confirmation appears right next to the changed setting.
  const savedMark = key => key === savedSetting ? h("span", { class: "saved-mark" }, T.setting_saved) : null;
  const behaviour = h("div", { class: "card" }, h("h2", {}, T.behaviour), h("p", { class: "hint" }, T.behaviour_hint),
    configData.settings.map(s => {
      const label = T["s_" + s.key] || s.key;
      if (s.kind === "bool") {
        return h("label", { class: "switch" },
          h("input", { type: "checkbox", checked: s.value, onchange: e => saveSetting(s.key, e.target.checked) }), label,
          savedMark(s.key));
      }
      return h("div", { class: "field" }, h("label", {}, label),
        h("div", { class: "row" },
          h("input", { type: "number", min: s.min, max: s.max, value: s.value, style: "max-width:140px",
                       onchange: e => saveSetting(s.key, e.target.value) }), savedMark(s.key)));
    }));

  // Connection and system fields: only saved with the button, then the bot restarts.
  const groups = GROUPS.map(g => {
    // Fields of providers that are not selected stay hidden (and are not used).
    const fields = configData.fields.filter(f => f.group === g && (!f.provider || f.provider === providerId()));
    return h("div", { class: "group" }, h("h3", {}, T["group_" + g]), fields.map(fieldRow));
  });
  const saveBtn = h("button", { class: "btn primary", id: "savebtn", onclick: e => saveConfig(e.target) }, T.save);
  const savebar = h("div", { class: "savebar" }, saveBtn, h("span", { id: "unsaved", class: "muted" }), message || "");
  const conn = h("div", { class: "card" }, h("h2", {}, T.connection_settings),
    h("p", { class: "hint" }, T.connection_hint), groups, savebar);

  box.replaceChildren(behaviour, conn);
  updateSaveBar();
}

function changeCount() {
  return Object.keys(dirty).length + resets.size;
}

function updateSaveBar() {
  const btn = document.getElementById("savebtn");
  const note = document.getElementById("unsaved");
  if (!btn || btn.dataset.busy) return;
  const n = changeCount();
  btn.disabled = n === 0;
  if (note) note.textContent = n ? tr("unsaved", { n }) : "";
}

function fieldRow(f) {
  const id = "f_" + f.name;
  const current = f.name in dirty ? dirty[f.name] : (resets.has(f.name) ? "" : f.value);
  let input;
  const onInput = e => {
    dirty[f.name] = e.target.value; resets.delete(f.name);
    // The red error is from the saved state; it is checked again on save, so drop it while editing.
    e.target.classList.remove("invalid");
    e.target.closest(".field")?.querySelector(".err")?.remove();
    updateSaveBar();
  };
  // The pickers depend on these – reload them once a new address or key has been typed.
  const pf = providerInfo(providerId()).fields;
  const reloads = f.name === pf.key || f.name === pf.url ? "models"
    : { JELLYFIN_URL: "users", JELLYFIN_API_KEY: "users" }[f.name];
  if (!f.editable) {
    input = h("input", { type: "text", id, value: f.value, disabled: true });
  } else if ((input = pickerInput(f, id, current, onInput))) {
    // select built above
  } else if (f.kind === "choice") {
    input = h("select", { id, onchange: onInput },
      f.choices.map(c => h("option", { value: c, selected: c === current }, c)));
  } else {
    input = h("input", {
      id, type: f.secret ? "password" : "text",
      inputmode: f.kind === "int" ? "numeric" : null, value: f.secret ? (dirty[f.name] || "") : current,
      placeholder: f.secret ? (f.is_set ? T.secret_set : T.secret_unset) : (f.default || ""),
      autocomplete: f.secret ? "new-password" : "off", spellcheck: "false", oninput: onInput,
      onchange: reloads ? () => loadOptions(reloads) : null,
      class: f.error && !(f.name in dirty) ? "invalid" : null,
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
  const pickerError = { [pf.model]: opts.modelsError, JELLYFIN_USER: opts.usersError }[f.name];
  if (pickerError && input.tagName !== "SELECT") info.push(tr("options_fallback", { error: pickerError }));
  return h("div", { class: "field" },
    h("label", { for: id }, T["f_" + f.name] || f.name, f.required ? h("span", { class: "req", title: T.required }, "\u00a0*") : "",
      h("span", { class: "name" }, f.name)),
    h("div", {}, input,
      f.name === "CLAUDE_MODEL" ? modelWarning(current || f.default) : null,
      f.name === "LLM_PROVIDER" ? providerNotice(current || f.default) : null,
      info.length ? h("div", { class: "info" }, info.map(i => typeof i === "string" ? h("span", {}, i) : i)) : null,
      f.error && !(f.name in dirty) ? h("div", { class: "err" }, f.error) : null));
}

async function saveSetting(key, value) {
  try {
    configData = await api("/api/settings", { method: "PUT", body: { [key]: value } });
    savedSetting = key;
    renderConfig();
    setTimeout(() => { if (savedSetting === key) { savedSetting = null; renderConfig(); } }, 2500);
  } catch (e) {
    renderConfig(h("span", { class: "pill error" }, T.save_failed + ": " + Object.values(e.data?.errors || {}).join(", ")));
  }
}

async function saveConfig(btn) {
  const p = dirty.LLM_PROVIDER && providerInfo(dirty.LLM_PROVIDER);
  if (p && !p.recommended && !confirm(tr("provider_confirm", { name: p.name }))) return;
  const m = providerId() === "claude" && dirty.CLAUDE_MODEL && modelInfo(dirty.CLAUDE_MODEL);
  if (m && m.cost && !m.recommended && !confirm(tr("model_confirm", { name: m.name, factor: m.cost.factor, cost: fmtUsd(m.cost.per_suggestion) }))) {
    return;
  }
  btn.disabled = true;
  btn.dataset.busy = "1";
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
