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
let eventsBoot = null;  // changes when the program restarted: its ids start anew
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
  for (const c of children.flat(Infinity)) {
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

// --- skins: one DOM, the look comes from style.css scoped by <html data-skin="…"> ---------------
// index.html applies the stored skin before the first paint; this only switches and remembers it.
const SKINS = ["arr", "terminal", "vhs", "soft"];
const DEFAULT_SKIN = "vhs";  // also in index.html
const currentSkin = () => document.documentElement.dataset.skin;

function setSkin(name) {
  document.documentElement.dataset.skin = SKINS.includes(name) ? name : DEFAULT_SKIN;
  try { localStorage.setItem("corsarr.skin", currentSkin()); } catch (e) { /* storage blocked: this page only */ }
}

// onChange re-renders where a skin needs a slightly different structure (e.g. the *arr toolbar).
function skinSelect(onChange) {
  return h("label", { class: "skin" }, h("span", {}, T.skin),
    h("select", { id: "skin", onchange: e => { setSkin(e.target.value); onChange(); } },
      SKINS.map(s => h("option", { value: s, selected: s === currentSkin() }, T["skin_" + s]))));
}

const logo = () => h("span", { class: "logo", "aria-hidden": "true" });

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
      h("h1", {}, logo(), T.title), form, h("p", { class: "hint", style: "margin-top:14px" }, T.login_hint),
      skinSelect(() => {}))));
  pw.focus();
}

// --- shell ---------------------------------------------------------------------------
function showApp() {
  clearTimers();
  const tabs = [["status", T.tab_status], ["events", T.tab_events], ["config", T.tab_config],
                ["setup", T.tab_setup], ["backup", T.tab_backup]];
  const nav = h("nav", { role: "tablist" }, tabs.map(([id, label]) =>
    h("button", { role: "tab", "data-tab": id, "aria-selected": String(tab === id), onclick: () => { tab = id; showApp(); } }, label)));
  const header = h("header", {}, h("div", { class: "bar" },
    h("div", { class: "brand" }, logo(), h("span", { class: "name" }, T.title), h("span", { id: "statepill" })), nav,
    h("div", { class: "tools" }, skinSelect(showApp),
      h("button", { class: "link", id: "logout", hidden: true, onclick: logout }, T.logout))));
  // Page title with toolbar – only visible in the *arr skin, which also moves the page actions there.
  const pagebar = h("div", { class: "pagebar" }, h("h1", {}, tabs.find(([id]) => id === tab)[1]),
    h("span", { class: "spacer" }), h("div", { id: "pageactions", class: "row" }));
  const main = h("main", { id: "main" });
  document.getElementById("app").replaceChildren(header, pagebar, main, footer());
  ({ status: viewStatus, events: viewEvents, config: viewConfig, setup: viewSetup, backup: viewBackup })[tab](main);
  refreshStatus();
  timers.push(setInterval(refreshStatus, 10000));
}

// Footer like on open source apps: name and running version, links to the project, the licence.
function footer() {
  const repo = `https://github.com/${status?.repo || "ironiro/corsarr"}`;
  const link = (href, label) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, label);
  return h("footer", { class: "appfoot" },
    h("span", {}, "Corsarr ", h("code", { id: "footver" }, status ? fmtVersion(status.version) : ""),
      " · ", link(`${repo}/blob/main/LICENSE`, T.foot_license)),
    h("nav", { "aria-label": T.foot_links },
      link(repo, "GitHub"), link(`${repo}/blob/main/docs/Home.md`, T.foot_docs),
      link(`${repo}/releases`, T.foot_releases), link(`${repo}/issues/new`, T.foot_issue)));
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
  const footVer = document.getElementById("footver");
  if (footVer) footVer.textContent = fmtVersion(status.version);
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
  const checkBtn = h("button", { class: "btn", "data-icon": "✓", onclick: e => run(e.target, T.checking, "/api/check") }, T.check_now);
  const restartBtn = h("button", { class: "btn", "data-icon": "↻", onclick: e => run(e.target, T.restarting, "/api/restart") }, T.restart);
  // The *arr skin shows the page actions as icon buttons in the top toolbar instead of the bot card.
  const toolbar = document.getElementById("pageactions");
  const inToolbar = currentSkin() === "arr" && toolbar;
  if (toolbar) toolbar.replaceChildren(...(inToolbar ? [checkBtn, restartBtn] : []));

  const notices = [];
  if (s.missing.length) {
    notices.push(h("div", { class: "notice warn" }, tr("missing_fields", { fields: s.missing.join(", ") }), " ",
      h("button", { class: "link", onclick: () => { tab = "config"; showApp(); } }, "→ " + T.tab_config)));
  }
  if (s.state === "error" && s.state_detail) notices.push(h("div", { class: "notice error" }, s.state_detail));

  const botCard = h("div", { class: "card" },
    h("div", { class: "row" },
      h("h2", {}, T.bot_state, s.bot_username ? ` @${s.bot_username}` : ""),
      h("span", { class: `pill ${cls}` }, label), h("span", { class: "spacer" }), inToolbar ? null : [checkBtn, restartBtn]),
    notices,
    h("dl", { class: "facts" },
      h("dt", {}, T.uptime), h("dd", {}, new Date(s.started_at * 1000).toLocaleString()),
      h("dt", {}, T.data_dir), h("dd", {}, s.data_dir),
      h("dt", {}, T.log_file), h("dd", {}, s.log_file)));

  // One row per service: a table in the *arr and terminal skins, cards (cassettes, tiles) in the others.
  const names = ["telegram", "llm", "jellyfin", "jellyseerr", "webhook", "sonarr", "radarr"];
  const head = h("div", { class: "service head", "aria-hidden": "true" }, h("div", { class: "name" }, T.col_service),
    h("div", { class: "state" }, T.col_status), h("div", { class: "detail" }, T.col_detail), h("div", { class: "meta" }, T.col_checked));
  const services = h("div", { class: "services" }, head,
    names.map(name => {
      const st = s.services[name];
      const title = name === "llm" && s.llm ? s.llm.name : T["svc_" + name];
      const untested = name === "llm" && s.llm && !s.llm.recommended
        ? h("span", { class: "pill warn", title: tr("provider_warn", { name: s.llm.name }) }, T.untested) : null;
      const pillCls = { ok: "ok", error: "error" }[st.status] || "";
      const text = { ok: T.status_ok, error: T.status_error, disabled: T.status_disabled }[st.status] || T.status_unknown;
      return h("div", { class: `service ${st.status}` },
        h("span", { class: "dot", "aria-hidden": "true" }),
        h("div", { class: "name" }, h("h3", {}, title), untested),
        h("div", { class: "state" }, h("span", { class: `pill ${pillCls}`, "data-status": st.status }, text)),
        h("div", { class: "detail" }, st.detail || "–"),
        h("div", { class: "meta" },
          `${T.last_check}: ${ago(st.checked_at, s.now)}`,
          st.status === "error" ? ` · ${T.last_ok}: ${ago(st.last_ok, s.now)}` : ""),
        // Tape window of the video store skin: the state in plain words between the reels
        h("div", { class: "reels", "aria-hidden": "true",
                   "data-label": { ok: T.deck_ok, error: T.deck_error, disabled: T.deck_off }[st.status] || T.deck_wait }, h("i"), h("i")));
    }));

  // Summary "n/7 ok" – drawn as a ring in the soft skin (green = ok, orange = error, rest = waiting).
  const count = st => names.filter(n => s.services[n].status === st).length;
  const pct = n => `${Math.round(n / names.length * 100)}%`;
  const ring = h("div", { class: "ring", style: `--ring-ok:${pct(count("ok"))};--ring-err:${pct(count("ok") + count("error"))}` },
    h("span", {}, tr("services_ok", { n: count("ok"), total: names.length })));

  box.replaceChildren(botCard, h("div", { id: "version" }), usageCard(s.usage),
    h("section", { class: "svc-box" }, h("div", { class: "svc-head" }, h("h2", {}, T.connections), ring), services),
    h("p", { class: "disclaimer" }, T.cost_disclaimer));
  renderVersion();
  if (!updateInfo) loadUpdate(false);
}

// Collapsible blocks on pages that redraw themselves every few seconds (status): remember which are open.
const openBlocks = new Set();
function keptDetails(key, open, ...children) {
  return h("details", { open: open || openBlocks.has(key),
                        ontoggle: e => { e.target.open ? openBlocks.add(key) : openBlocks.delete(key); } }, ...children);
}

// --- usage and costs -------------------------------------------------------------------------
function usageCard(u) {
  if (!u) return null;
  const num = n => Number(n || 0).toLocaleString();
  const cost = p => p.cost === null ? T.usage_cost_unknown
    : p.cost === 0 && p.calls && u.provider !== "claude" ? T.usage_free
    : tr("usage_cost", { cost: fmtUsd(p.cost) }) + (p.partial ? ` (${T.usage_partial})` : "");
  const row = (label, p) => [h("dt", {}, label),
    h("dd", {}, `${tr("usage_calls", { n: num(p.calls) })} · ${tr("usage_tokens", { n: num(p.tokens_in + p.tokens_out) })} · ${cost(p)}`)];
  const kinds = Object.entries(u.by_kind).sort((a, b) => b[1].calls - a[1].calls);
  let budget = h("p", { class: "hint" }, T.usage_no_budget);
  if (u.budget) {
    const pct = Math.round(100 * (u.budget_share || 0));
    budget = h("div", { class: "budget" },
      h("div", { class: "meter", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
        h("span", { class: pct >= 100 ? "error" : pct >= 80 ? "warn" : "ok", style: `width:${Math.min(pct, 100)}%` })),
      h("span", {}, tr("usage_budget", { cost: fmtUsd(u.month.cost || 0), budget: fmtUsd(u.budget), pct })));
  }
  return h("div", { class: "card", id: "usage" },
    h("div", { class: "row" }, h("h2", {}, T.usage)),
    h("dl", { class: "facts" }, row(T.usage_today, u.today), row(T.usage_month, u.month), row(T.usage_total, u.total)),
    budget,
    kinds.length ? keptDetails("usage-kinds", false, h("summary", {}, T.usage_kinds), h("ul", { class: "changes" }, kinds.map(([k, v]) =>
      h("li", {}, `${T["kind_" + k] || k}: ${tr("usage_calls", { n: num(v.calls) })}${v.cost === null ? "" : " · " + tr("usage_cost", { cost: fmtUsd(v.cost) })}`)))) : null,
    u.provider === "claude" ? h("p", { class: "hint" }, T.usage_hint) : null);
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
    // The new versions – only worth a list when there is more than the one already named in the pill,
    // or when a GitHub release was written for it (notes).
    if (u.releases.length > 1 || u.releases.some(r => r.notes)) {
      rows.push(h("ul", { class: "changes releases" }, u.releases.map(r => {
        const title = [h("code", {}, r.name), r.prerelease ? h("span", { class: "muted" }, " · " + T.prerelease) : "",
          r.date ? h("span", { class: "muted" }, " · " + r.date.slice(0, 10)) : ""];
        return h("li", {}, r.notes ? h("details", { class: "release", open: r === u.releases[0] },
          h("summary", {}, title), h("pre", { class: "notes" }, r.notes)) : title);
      })));
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
    rows.push(keptDetails("update-log", u.updating, h("summary", {}, T.update_log), h("pre", { class: "log-tail" }, u.log)));
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
    let data = await api(`/api/events?after=${lastEventId}`);
    if (data.boot !== eventsBoot) {
      if (eventsBoot !== null) data = await api("/api/events?after=0");
      eventsBoot = data.boot;
      events = [];
      lastEventId = 0;
      if (!data.events.length) renderEvents();
    }
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
  const shown = events.filter(e => e.restart || (LEVELS[e.level] || 0) >= levelFilter &&
    (!search || e.message.toLowerCase().includes(search) || e.logger.toLowerCase().includes(search)));
  // Restart dividers stay visible under every filter, but not several in a row or at the edges.
  const kept = shown.filter((e, i) => !e.restart || (i > 0 && !shown[i - 1].restart && i < shown.length - 1));
  const rows = kept.map(e => e.restart ? h("div", { class: "ev restart", title: new Date(e.ts * 1000).toLocaleString() }, T.events_restart) : h("div", { class: `ev ${e.level}` },
    h("span", { class: "ts", title: new Date(e.ts * 1000).toLocaleString() }, clock(e.ts)),
    h("span", { class: "lv" }, e.level),
    h("span", { class: "msg" }, h("span", { class: "src" }, e.logger), e.message)));
  log.replaceChildren(...(rows.length ? rows : [h("div", { class: "empty" }, T.no_events)]));
  if (follow) log.scrollTop = log.scrollHeight;
}

// --- config tab ---------------------------------------------------------------------------
const GROUPS = ["telegram", "llm", "costs", "jellyfin", "jellyseerr", "webhooks", "interface", "advanced"];
const openGroups = new Set();  // sections the user opened; those with errors open by themselves

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
  api("/api/options/admin-chats").then(res => { opts.adminChats = res.chats || []; renderConfig(); }).catch(() => {});
  loadOptions("streaming");
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
const opts = { models: null, modelsError: "", modelsFor: "", users: null, usersError: "", adminChats: [],
               streaming: null, streamingError: "" };
let savedSetting = null;  // behaviour setting that was just saved – shows "✓ saved" next to it

async function loadOptions(which) {
  let body, provider;
  if (which === "models") {
    provider = providerId();
    const pf = providerInfo(provider).fields;
    body = { provider, api_key: (pf.key && dirty[pf.key]) || "", url: (pf.url && dirty[pf.url]) || "" };
  } else if (which === "streaming") {
    body = { url: dirty.JELLYSEERR_URL || "", api_key: dirty.JELLYSEERR_API_KEY || "", region: fieldValue("STREAMING_REGION") };
  } else {
    body = { url: dirty.JELLYFIN_URL || "", api_key: dirty.JELLYFIN_API_KEY || "" };
  }
  const path = { models: "models", users: "jellyfin-users", streaming: "streaming" }[which];
  try {
    const res = await api(`/api/options/${path}`, { method: "POST", body });
    if (which === "models" && provider !== providerId()) return;  // provider changed meanwhile
    if (which === "streaming") {
      if (res.region !== fieldValue("STREAMING_REGION").toUpperCase()) return;  // country changed meanwhile
      opts.streaming = res.regions && res.regions.length ? res : null;
    } else {
      opts[which] = res[which] && res[which].length ? res[which] : null;
    }
    opts[which + "Error"] = res.error || "";
  } catch (e) {
    opts[which] = null;
    opts[which + "Error"] = e.message;
  }
  if (which === "models") opts.modelsFor = provider;
  renderConfig();
}

const fmtUsd = x => {
  const v = x >= 0.1 ? x.toFixed(2) : x >= 0.01 ? x.toFixed(3) : x.toFixed(4);
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

// --- streaming services: country select and a checkbox list of its services (stored as ids) ---------
const idList = v => (v || "").split(",").map(x => x.trim()).filter(Boolean);
let streamingFilter = "";  // search text above the list – kept when the page is redrawn

function regionSelect(id, data, current, onChange) {
  const code = (current || "").toUpperCase();
  const options = data.regions.map(r => h("option", { value: r.code, selected: r.code === code }, `${r.name} (${r.code})`));
  if (!data.regions.some(r => r.code === code)) options.unshift(h("option", { value: code, selected: true }, code || "–"));
  return h("select", { id, onchange: onChange }, options);
}

function providerChecklist(id, data, current, onChange) {
  const chosen = new Set(idList(current));
  const known = new Set(data.providers.map(p => String(p.id)));
  const count = () => chosen.size ? tr("streaming_chosen", { n: chosen.size }) : T.streaming_off;
  const counter = h("div", { class: "muted" }, count());
  const toggle = (pid, on) => {
    on ? chosen.add(pid) : chosen.delete(pid);
    counter.textContent = count();
    onChange([...chosen].join(","));
  };
  const item = (pid, name, logo) => h("label", { class: "switch", "data-name": name.toLowerCase() },
    h("input", { type: "checkbox", checked: chosen.has(pid), onchange: e => toggle(pid, e.target.checked) }),
    logo ? h("img", { src: logo, alt: "", loading: "lazy", width: 20, height: 20 }) : null, name);
  // Chosen services the country does not list (chosen for another country) stay visible and can be removed.
  const items = [...[...chosen].filter(pid => !known.has(pid)).map(pid => item(pid, tr("streaming_unknown", { id: pid }))),
                 ...data.providers.map(p => item(String(p.id), p.name, p.logo))];
  const list = h("div", { class: "providers", id }, items);
  const filter = () => {
    const q = streamingFilter.trim().toLowerCase();
    for (const el of list.children) el.hidden = !!q && !el.dataset.name.includes(q) && !el.querySelector("input").checked;
  };
  const search = h("input", { type: "text", placeholder: T.streaming_filter, spellcheck: "false", autocomplete: "off", value: streamingFilter,
                              oninput: e => { streamingFilter = e.target.value; filter(); } });
  filter();
  return h("div", { class: "streaming" }, counter, search, list);
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
  if (f.name === "ADMIN_CHAT_ID") {
    // Everyone who wrote to the bot privately; the id itself stays visible for checking
    const chats = opts.adminChats || [];
    const options = [h("option", { value: "", selected: !current }, chats.length ? "–" : T.admin_chat_none),
      ...chats.map(c => h("option", { value: String(c.id), selected: String(c.id) === current }, `${c.name} (${c.id})`))];
    if (current && !chats.some(c => String(c.id) === current)) options.push(h("option", { value: current, selected: true }, current));
    return h("select", { id, onchange: onInput }, options);
  }
  if (f.name === "STREAMING_REGION" && opts.streaming) {
    return regionSelect(id, opts.streaming, current, e => { onInput(e); renderConfig(); loadOptions("streaming"); });
  }
  if (f.name === "STREAMING_PROVIDERS" && opts.streaming) {
    return providerChecklist(id, opts.streaming, current, v => { dirty[f.name] = v; resets.delete(f.name); updateSaveBar(); });
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
  const setting = s => {
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
  };
  // The characters (<id>_enabled) as a grid of switches below the other settings
  const isCharacter = s => s.kind === "bool" && s.key.endsWith("_enabled");
  const behaviour = h("div", { class: "card" }, h("h2", {}, T.behaviour), h("p", { class: "hint" }, T.behaviour_hint),
    configData.settings.filter(s => !isCharacter(s)).map(setting),
    h("h3", { class: "subhead" }, T.characters), h("p", { class: "hint" }, T.characters_hint),
    h("div", { class: "characters" }, configData.settings.filter(isCharacter).map(setting)));

  // Connection and system fields: only saved with the button, then the bot restarts.
  // One collapsible section per group; closed it shows a one-line summary and whether something is missing.
  const groups = GROUPS.map(g => {
    // Fields of providers that are not selected stay hidden (and are not used).
    const fields = configData.fields.filter(f => f.group === g && (!f.provider || f.provider === providerId()));
    const missing = fields.filter(f => f.error && !(f.name in dirty)).length;
    const changed = fields.some(f => f.name in dirty || resets.has(f.name));
    const state = missing ? h("span", { class: "pill error" }, missing === 1 ? T.group_missing_one : tr("group_missing", { n: missing }))
      : changed ? h("span", { class: "pill warn" }, T.group_changed) : h("span", { class: "pill ok" }, T.group_ok);
    return h("details", { class: "group", open: missing > 0 || openGroups.has(g),
                          ontoggle: e => { e.target.open ? openGroups.add(g) : openGroups.delete(g); } },
      h("summary", {}, h("h3", {}, T["group_" + g]), state, h("span", { class: "summary muted" }, groupSummary(g, fields))),
      fields.map(fieldRow));
  });
  const saveBtn = h("button", { class: "btn primary", id: "savebtn", onclick: e => saveConfig(e.target) }, T.save);
  const savebar = h("div", { class: "savebar" }, saveBtn, h("span", { id: "unsaved", class: "muted" }), message || "");
  const conn = h("div", { class: "card" }, h("h2", {}, T.connection_settings),
    h("p", { class: "hint" }, T.connection_hint), groups, savebar);

  box.replaceChildren(behaviour, conn);
  updateSaveBar();
}

// "Claude · claude-haiku-5-5", "http://…:8096 · Kino" – the values that tell sections apart at a glance.
function groupSummary(g, fields) {
  const shown = name => {
    const f = fields.find(x => x.name === name);
    if (!f) return "";
    const v = f.name in dirty ? dirty[f.name] : f.value || f.default;
    if (f.secret) return f.is_set || dirty[f.name] ? T.secret_is_set : "";
    if (f.name === "STREAMING_PROVIDERS") {
      const n = idList(v).length;
      return n ? tr("streaming_summary", { n, region: fieldValue("STREAMING_REGION").toUpperCase() }) : "";
    }
    return choiceLabel(f, v);
  };
  if (g === "interface") {
    return [shown("LANGUAGE"), shown("UPDATE_CHANNEL"),
            fields.find(f => f.name === "ADMIN_PASSWORD")?.is_set ? T.password_set : T.password_none].join(" · ");
  }
  if (g === "webhooks") return shown("WEBHOOK_SECRET") ? T.secret_is_set : "";
  const names = { telegram: ["TELEGRAM_CHAT_ID"], llm: ["LLM_PROVIDER", providerInfo(providerId()).fields.model],
                  jellyfin: ["JELLYFIN_URL", "JELLYFIN_USER"], jellyseerr: ["JELLYSEERR_URL", "STREAMING_PROVIDERS"],
                  advanced: ["WEBHOOK_HOST", "WEBHOOK_PORT", "LOG_LEVEL"] }[g] || [];
  if (g === "costs") {
    const b = shown("MONTHLY_BUDGET_USD");
    return b ? tr("usage_cost", { cost: b }) : T.usage_no_budget;
  }
  return names.map(shown).filter(Boolean).join(" · ");
}

// Readable names for choice values (provider and channel); other values as they are.
function choiceLabel(f, v) {
  if (f.name === "LLM_PROVIDER") return providerInfo(v).name;
  if (f.name === "UPDATE_CHANNEL") return T["channel_" + v] || v;
  return v;
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
    : { JELLYFIN_URL: "users", JELLYFIN_API_KEY: "users", JELLYSEERR_URL: "streaming", JELLYSEERR_API_KEY: "streaming",
        STREAMING_REGION: "streaming" }[f.name];
  if (!f.editable) {
    input = h("input", { type: "text", id, value: f.value, disabled: true });
  } else if ((input = pickerInput(f, id, current, onInput))) {
    // select built above
  } else if (f.kind === "choice") {
    input = h("select", { id, onchange: onInput },
      f.choices.map(c => h("option", { value: c, selected: c === current }, choiceLabel(f, c))));
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
  if (T["h_" + f.name]) info.push(T["h_" + f.name]);
  if (f.editable && f.source === "gui" && !resets.has(f.name)) {
    info.push(h("button", { class: "link", title: T.reset_hint, onclick: () => {
      resets.add(f.name); delete dirty[f.name]; renderConfig();
    } }, T.reset_short));
  } else if (f.source === "env") info.push(h("span", { class: "tag", title: T.from_env_title }, T.env_tag));
  const pickerError = { [pf.model]: opts.modelsError, JELLYFIN_USER: opts.usersError,
                        STREAMING_PROVIDERS: opts.streamingError }[f.name];
  if (pickerError && input.tagName === "INPUT") info.push(tr("options_fallback", { error: pickerError }));
  return h("div", { class: "field" },
    // The technical name (for environment variables and the docs) only shows on hover.
    h("label", { for: id, title: f.name }, T["f_" + f.name] || f.name,
      f.required ? h("span", { class: "req", title: T.required }, "\u00a0*") : ""),
    h("div", {}, input,
      f.name === "CLAUDE_MODEL" ? modelWarning(current || f.default) : null,
      f.name === "LLM_PROVIDER" ? providerNotice(current || f.default) : null,
      info.length ? h("div", { class: "info" }, info.map(i => typeof i === "string" ? h("span", {}, i) : i)) : null,
      f.error && !(f.name in dirty)
        ? h("div", { class: "err" }, f.required && !f.is_set ? T.field_missing : f.error) : null));
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

// --- setup assistant -------------------------------------------------------------------------
// Step by step through the required settings. Every step is tested with the values typed in and only
// then saved, so leaving halfway keeps what already works. Also offers restoring a backup instead.
const WIZ_STEPS = ["start", "telegram", "llm", "jellyfin", "jellyseerr", "webhooks", "done"];
const wiz = { step: 0, values: {}, result: {}, error: "", busy: false, chats: null, models: null, users: null,
              hooks: null, arr: {} };
let wizTimer = null;

async function viewSetup(main) {
  main.append(h("div", { id: "setup" }));
  try {
    configData = await api("/api/config");
  } catch (e) {
    if (e.message !== "unauthorized") renderNetworkError();
    return;
  }
  renderSetup();
}

const field = name => configData.fields.find(f => f.name === name) || {};
// Value typed in this session, else the saved one (secrets are never sent to the browser: empty = keep).
const wizValue = name => name in wiz.values ? wiz.values[name] : (field(name).secret ? "" : field(name).value || "");

function wizInput(name, { placeholder, onchange } = {}) {
  const f = field(name);
  return h("div", { class: "field" },
    h("label", { for: "w_" + name }, T["f_" + name] || name, h("span", { class: "name" }, name)),
    h("input", {
      id: "w_" + name, type: f.secret ? "password" : "text", value: wizValue(name), spellcheck: "false",
      autocomplete: f.secret ? "new-password" : "off",
      placeholder: f.secret ? (f.is_set ? T.secret_set : T.secret_unset) : (placeholder || f.default || ""),
      oninput: e => { wiz.values[name] = e.target.value.trim(); wiz.result[WIZ_STEPS[wiz.step]] = null; },
      onchange,
    }));
}

function wizSelect(name, options, onchange) {
  const current = wizValue(name);
  const opts = options.map(([value, label]) => h("option", { value, selected: value === current }, label));
  if (!options.some(([value]) => value === current)) opts.unshift(h("option", { value: current, selected: true }, current || "–"));
  return h("div", { class: "field" },
    h("label", { for: "w_" + name }, T["f_" + name] || name, h("span", { class: "name" }, name)),
    h("select", { id: "w_" + name, onchange: e => {
      wiz.values[name] = e.target.value; wiz.result[WIZ_STEPS[wiz.step]] = null; (onchange || renderSetup)();
    } }, opts));
}

// Copy also works over plain http on the home network, where navigator.clipboard is not available.
function copyButton(text) {
  return h("button", { class: "btn small", onclick: e => {
    const done = () => { e.target.textContent = T.wiz_copied; setTimeout(() => { e.target.textContent = T.wiz_copy; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done); return; }
    const area = h("textarea", { style: "position:fixed;opacity:0" }, text);
    document.body.append(area); area.select();
    try { document.execCommand("copy"); done(); } finally { area.remove(); }
  } }, T.wiz_copy);
}

const copyRow = (label, text) => h("div", { class: "copyrow" }, h("span", { class: "muted" }, label),
  h("code", {}, text), copyButton(text));

async function wizCall(path, body) {
  wiz.busy = true; wiz.error = ""; renderSetup();
  try {
    return await api(path, { method: "POST", body });
  } catch (e) {
    wiz.error = e.message;
    return null;
  } finally {
    wiz.busy = false; renderSetup();
  }
}

// Save the values of the current step (the bot restarts with them) and go on.
async function wizSave(names) {
  const values = Object.fromEntries(names.filter(n => n in wiz.values && wiz.values[n] !== "").map(n => [n, wiz.values[n]]));
  if (Object.keys(values).length) {
    wiz.busy = true; renderSetup();
    try {
      configData = await api("/api/config", { method: "PUT", body: { values, reset: [] } });
      names.forEach(n => delete wiz.values[n]);
    } catch (e) {
      wiz.busy = false;
      wiz.error = T.save_failed + ": " + Object.values(e.data?.errors || {}).join(", ");
      renderSetup();
      return;
    }
    wiz.busy = false;
  }
  wizGo(wiz.step + 1);
}

function wizGo(step) {
  wiz.step = step; wiz.error = "";
  clearInterval(wizTimer); wizTimer = null;
  if (WIZ_STEPS[step] === "webhooks") {
    loadHooks();
    wizTimer = setInterval(loadHooks, 5000);  // turns green as soon as the first event arrives
    timers.push(wizTimer);
  }
  if (WIZ_STEPS[step] === "llm" && !wiz.models) loadWizModels();
  renderSetup();
}

async function loadHooks() {
  try { wiz.hooks = await api("/api/setup/webhooks"); } catch (e) { return; }
  if (WIZ_STEPS[wiz.step] === "webhooks") renderSetup();
}

const provider = () => wizValue("LLM_PROVIDER") || "claude";
const providerFields = () => providerInfo(provider()).fields || {};

async function loadWizModels() {
  const pf = providerFields();
  const res = await api("/api/options/models", { method: "POST", body: {
    provider: provider(), api_key: (pf.key && wiz.values[pf.key]) || "", url: (pf.url && wiz.values[pf.url]) || "" } })
    .catch(e => ({ models: [], error: e.message }));
  wiz.models = res.models && res.models.length ? res.models : null;
  renderSetup();
}

async function loadWizUsers() {
  const res = await api("/api/options/jellyfin-users", { method: "POST", body: {
    url: wiz.values.JELLYFIN_URL || "", api_key: wiz.values.JELLYFIN_API_KEY || "" } })
    .catch(e => ({ users: [], error: e.message }));
  wiz.users = res.users && res.users.length ? res.users : null;
  if (res.error) wiz.error = tr("options_fallback", { error: res.error });
  renderSetup();
}

async function wizTest(service, names) {
  const res = await wizCall("/api/setup/test", { service, values: Object.fromEntries(names.map(n => [n, wiz.values[n] || ""])) });
  if (res) wiz.result[service] = res.detail;
  renderSetup();
}

function stepTelegram() {
  const found = async () => {
    const res = await wizCall("/api/setup/telegram", { token: wiz.values.TELEGRAM_BOT_TOKEN || "" });
    if (!res) return;
    wiz.result.telegram = res;
    if (res.chats.length === 1 && !wiz.values.TELEGRAM_CHAT_ID) wiz.values.TELEGRAM_CHAT_ID = String(res.chats[0].id);
    renderSetup();
  };
  const r = wiz.result.telegram;
  const chatId = wizValue("TELEGRAM_CHAT_ID");
  const body = [h("p", {}, T.wiz_tg_intro), wizInput("TELEGRAM_BOT_TOKEN"),
    h("button", { class: "btn", onclick: found, disabled: wiz.busy }, r ? T.wiz_tg_again : T.wiz_tg_find)];
  if (r) {
    body.push(h("div", { class: "notice ok" }, tr("wiz_tg_bot", { name: r.username })));
    if (r.privacy) body.push(h("div", { class: "notice warn" }, T.wiz_privacy));
    if (!r.chats.length) body.push(h("div", { class: "notice" }, tr("wiz_tg_no_chats", { name: r.username })));
    else body.push(h("div", { class: "field" }, h("label", {}, T.wiz_tg_pick),
      h("div", { class: "choices" }, r.chats.map(c => h("label", { class: "switch" },
        h("input", { type: "radio", name: "chat", checked: String(c.id) === chatId,
                     onchange: () => { wiz.values.TELEGRAM_CHAT_ID = String(c.id); renderSetup(); } }),
        c.title || T.wiz_tg_current, h("code", {}, " " + c.id))))));
  }
  return { body, ready: !!r && !!chatId, save: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"] };
}

function stepLlm() {
  const pf = providerFields();
  const reload = () => { wiz.models = null; loadWizModels(); };
  const body = [h("p", {}, T.wiz_llm_intro),
    wizSelect("LLM_PROVIDER", (configData.providers || []).map(p =>
      [p.id, tr(p.recommended ? "provider_recommended" : "provider_untested", { name: p.name })]), () => { wiz.values[pf.model] = undefined; reload(); }),
    providerNotice(provider())];
  if (pf.key) body.push(wizInput(pf.key, { onchange: reload }));
  if (pf.url) body.push(wizInput(pf.url, { onchange: reload }));
  const fresh = providerFields();
  body.push(wiz.models
    ? wizSelect(fresh.model, wiz.models.map(m => [m.id, m.cost && m.recommended ? tr("model_option_recommended",
        { name: m.name, cost: fmtUsd(m.cost.per_suggestion) }) : m.name]))
    : wizInput(fresh.model));
  if (provider() === "claude") body.push(h("p", { class: "hint" }, T.wiz_llm_cost));
  const names = ["LLM_PROVIDER", fresh.key, fresh.url, fresh.model].filter(Boolean);
  body.push(h("button", { class: "btn", disabled: wiz.busy, onclick: () => wizTest("llm", names) }, T.wiz_test));
  return { body, ready: !!wiz.result.llm, ok: wiz.result.llm, save: names };
}

function stepJellyfin() {
  const names = ["JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER"];
  const body = [h("p", {}, T.wiz_jf_intro), wizInput("JELLYFIN_URL"), wizInput("JELLYFIN_API_KEY", { onchange: loadWizUsers }),
    wiz.users ? wizSelect("JELLYFIN_USER", wiz.users.map(u => [u, u])) : wizInput("JELLYFIN_USER"),
    h("div", { class: "row" },
      h("button", { class: "btn", disabled: wiz.busy, onclick: loadWizUsers }, T.wiz_load_users),
      h("button", { class: "btn", disabled: wiz.busy, onclick: () => wizTest("jellyfin", names) }, T.wiz_test))];
  return { body, ready: !!wiz.result.jellyfin, ok: wiz.result.jellyfin, save: names };
}

// Optional part of the Seerr step: the streaming services they subscribe to (see providerChecklist).
async function loadWizStreaming() {
  const res = await wizCall("/api/options/streaming", {
    url: wiz.values.JELLYSEERR_URL || "", api_key: wiz.values.JELLYSEERR_API_KEY || "", region: wizValue("STREAMING_REGION") });
  if (!res) return;
  wiz.streaming = res.regions && res.regions.length ? res : null;
  if (res.error) wiz.error = tr("options_fallback", { error: res.error });
  renderSetup();
}

function stepSeerr() {
  const names = ["JELLYSEERR_URL", "JELLYSEERR_API_KEY"];
  const body = [h("p", {}, T.wiz_seerr_intro), wizInput("JELLYSEERR_URL"), wizInput("JELLYSEERR_API_KEY"),
    h("button", { class: "btn", disabled: wiz.busy, onclick: () => wizTest("jellyseerr", names) }, T.wiz_test)];
  if (wiz.result.jellyseerr) {
    const label = name => h("label", { for: "w_" + name }, T["f_" + name], h("span", { class: "name" }, name));
    body.push(h("h3", {}, T.wiz_streaming_title), h("p", { class: "hint" }, T.wiz_streaming_intro));
    if (!wiz.streaming) {
      body.push(h("button", { class: "btn", disabled: wiz.busy, onclick: loadWizStreaming }, T.wiz_streaming_load));
    } else {
      body.push(h("div", { class: "field" }, label("STREAMING_REGION"),
          regionSelect("w_STREAMING_REGION", wiz.streaming, wizValue("STREAMING_REGION"), e => {
            wiz.values.STREAMING_REGION = e.target.value; loadWizStreaming(); })),
        h("div", { class: "field" }, label("STREAMING_PROVIDERS"),
          providerChecklist("w_STREAMING_PROVIDERS", wiz.streaming, wizValue("STREAMING_PROVIDERS"), v => {
            wiz.values.STREAMING_PROVIDERS = v; })));
    }
  }
  return { body, ready: !!wiz.result.jellyseerr, ok: wiz.result.jellyseerr,
           save: [...names, "STREAMING_REGION", "STREAMING_PROVIDERS"] };
}

function hookState(service) {
  const st = wiz.hooks?.services?.[service];
  return st && st.status === "ok" ? h("span", { class: "pill ok" }, T.wiz_received)
    : h("span", { class: "pill" }, T.wiz_waiting);
}

function stepWebhooks() {
  const w = wiz.hooks;
  if (!w) return { body: [h("p", {}, T.loading)], ready: true, save: [] };
  const body = [h("p", {}, T.wiz_hooks_intro)];
  if (/\/\/(localhost|127\.|\[::1\])/.test(w.jellyfin)) body.push(h("div", { class: "notice warn" }, T.wiz_hooks_local));
  body.push(h("h3", {}, "Jellyfin ", hookState("webhook")),
    h("p", { class: "hint" }, T.wiz_jf_hook_steps),
    copyRow("Webhook Url", w.jellyfin), copyRow(T.wiz_header, w.header), copyRow(T.wiz_header_value, w.secret),
    h("details", {}, h("summary", {}, T.wiz_template), h("pre", { class: "log-tail" }, w.template), copyButton(w.template)));
  for (const kind of ["sonarr", "radarr"]) {
    const name = kind[0].toUpperCase() + kind.slice(1);
    const KIND = kind.toUpperCase();
    const saved = field(KIND + "_API_KEY").is_set;
    // Remembered access (if any) fills the form; an empty key field then means "the saved key".
    const a = wiz.arr[kind] || (wiz.arr[kind] = { url: field(KIND + "_URL").value || "", key: "", remember: saved });
    const connect = async () => {
      if (!confirm(tr("wiz_arr_confirm", { name }))) return;
      const res = await wizCall("/api/setup/arr", { kind, url: a.url, api_key: a.key, base: w.jellyfin.replace(/\/jellyfin$/, "") });
      if (!res) return;
      a.done = res;
      if (a.remember) {  // takes effect without restarting the bot
        const values = { [KIND + "_URL"]: a.url };
        if (a.key) values[KIND + "_API_KEY"] = a.key;
        configData = await api("/api/config", { method: "PUT", body: { values, reset: [] } }).catch(() => configData);
      }
      renderSetup();
    };
    body.push(h("h3", {}, name, " ", hookState(kind)),
      h("p", { class: "hint" }, tr("wiz_arr_intro", { name })),
      h("div", { class: "row wrap" },
        h("input", { placeholder: tr("wiz_arr_url", { name }), value: a.url, oninput: e => { a.url = e.target.value.trim(); } }),
        h("input", { type: "password", placeholder: saved ? T.secret_set : T.wiz_arr_key, value: a.key,
                     autocomplete: "new-password", oninput: e => { a.key = e.target.value.trim(); } })),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: a.remember,
        onchange: e => { a.remember = e.target.checked; } }), tr("wiz_arr_remember", { name })),
      h("button", { class: "btn", disabled: wiz.busy, onclick: connect }, tr("wiz_arr_connect", { name })),
      a.done ? h("div", { class: "notice ok" }, tr("wiz_arr_done", { name })) : null,
      a.done?.telegram?.length ? h("div", { class: "notice warn" }, tr("wiz_arr_telegram", { name, names: a.done.telegram.join(", ") })) : null,
      h("details", {}, h("summary", {}, T.wiz_manual), copyRow("URL", w[kind])));
  }
  return { body, ready: true, save: [], optional: true };
}

function restoreForm(onDone) {
  const file = h("input", { type: "file", accept: ".zip,application/zip" });
  const pw = h("input", { type: "password", placeholder: T.restore_pw, autocomplete: "off" });
  const msg = h("div", {});
  const btn = h("button", { class: "btn primary", onclick: async () => {
    if (!file.files.length || !pw.value) { msg.replaceChildren(h("div", { class: "notice error" }, T.restore_missing)); return; }
    if (!confirm(T.restore_confirm)) return;
    btn.disabled = true; btn.textContent = T.restore_running;
    const form = new FormData();
    form.append("file", file.files[0]);
    form.append("password", pw.value);
    try {
      const res = await fetch("/api/restore", { method: "POST", headers: { "X-Corsarr": "1" }, body: form });
      if (res.status === 401) { showLogin(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || res.statusText);
      msg.replaceChildren(h("div", { class: "notice ok" }, tr("restore_done", { version: data.version || "?", created: (data.created || "").slice(0, 16).replace("T", " ") })));
      setTimeout(onDone, 2000);
    } catch (e) {
      msg.replaceChildren(h("div", { class: "notice error" }, e.message));
    } finally {
      btn.disabled = false; btn.textContent = T.restore_button;
    }
  } }, T.restore_button);
  return h("div", { class: "restore" }, h("div", { class: "field" }, h("label", {}, T.restore_file), file),
    h("div", { class: "field" }, h("label", {}, T.restore_pw), pw), btn, msg);
}

async function afterRestore() {
  await loadTexts();
  tab = "status";
  showApp();
}

function renderSetup() {
  const box = document.getElementById("setup");
  if (!box || !configData) return;
  const name = WIZ_STEPS[wiz.step];
  let content;
  if (name === "start") {
    content = { body: [h("p", {}, T.wiz_intro),
      h("div", { class: "choices big" },
        h("button", { class: "btn primary", onclick: () => wizGo(1) }, T.wiz_new),
        h("button", { class: "btn", onclick: () => { wiz.restoring = !wiz.restoring; renderSetup(); } }, T.wiz_restore)),
      wiz.restoring ? [h("p", { class: "hint" }, T.wiz_restore_hint), restoreForm(afterRestore)] : null], start: true };
  } else if (name === "done") {
    content = { body: [h("p", {}, T.wiz_done_text),
      h("button", { class: "btn primary", onclick: () => { tab = "status"; wiz.step = 0; showApp(); } }, T.wiz_to_status)], last: true };
  } else {
    content = { telegram: stepTelegram, llm: stepLlm, jellyfin: stepJellyfin, jellyseerr: stepSeerr, webhooks: stepWebhooks }[name]();
  }
  const total = WIZ_STEPS.length - 2;
  const head = h("div", { class: "row" }, h("h2", {}, T["wiz_" + name + "_title"]), h("span", { class: "spacer" }),
    !content.start && !content.last ? h("span", { class: "muted" }, tr("wiz_step", { n: wiz.step, total })) : null);
  const steps = h("ol", { class: "wizsteps" }, WIZ_STEPS.slice(1, -1).map((s, i) =>
    h("li", { class: i + 1 === wiz.step ? "on" : i + 1 < wiz.step ? "done" : "" }, T["wiz_" + s + "_short"])));
  const nav = content.start || content.last ? null : h("div", { class: "savebar wiznav" },
    h("button", { class: "btn", onclick: () => wizGo(wiz.step - 1), disabled: wiz.busy }, T.wiz_back),
    h("span", { class: "spacer" }),
    content.optional ? null : h("button", { class: "link", onclick: () => wizGo(wiz.step + 1) }, T.wiz_skip),
    h("button", { class: "btn primary", disabled: wiz.busy || !content.ready, onclick: () => wizSave(content.save) },
      wiz.busy ? T.wiz_testing : T.wiz_next));
  box.replaceChildren(h("div", { class: "card wizard" }, steps, head, content.body,
    content.ok ? h("div", { class: "notice ok" }, "✓ ", content.ok) : null,
    wiz.error ? h("div", { class: "notice error" }, wiz.error) : null, nav));
}

// --- backup tab ------------------------------------------------------------------------------
// Credentials a backup holds: every secret that is set, plus the webhook secret (see backup.create).
const CREDENTIALS = ["TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                     "JELLYFIN_API_KEY", "JELLYSEERR_API_KEY", "SONARR_API_KEY", "RADARR_API_KEY",
                     "WEBHOOK_SECRET", "ADMIN_PASSWORD"];

async function viewBackup(main) {
  try {
    configData = await api("/api/config");
  } catch (e) {
    if (e.message !== "unauthorized") renderNetworkError();
    return;
  }
  const included = CREDENTIALS.filter(n => configData.fields.find(f => f.name === n)?.is_set).map(n => T["cred_" + n]);
  const contents = h("ul", { class: "hint" },
    h("li", {}, included.length ? tr("backup_contains", { list: included.join(", ") }) : T.backup_contains_none),
    h("li", {}, T.backup_not_contained));
  const pw = h("input", { type: "password", autocomplete: "new-password", placeholder: T.backup_pw });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", placeholder: T.backup_pw2 });
  const msg = h("div", {});
  const download = h("button", { class: "btn primary", onclick: async () => {
    if (pw.value !== pw2.value) { msg.replaceChildren(h("div", { class: "notice error" }, T.backup_pw_mismatch)); return; }
    download.disabled = true; download.textContent = T.backup_downloading;
    try {
      const res = await fetch("/api/backup", { method: "POST", body: JSON.stringify({ password: pw.value }),
        headers: { "X-Corsarr": "1", "Content-Type": "application/json" } });
      if (res.status === 401) { showLogin(); return; }
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
      const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || "corsarr-backup.zip";
      const url = URL.createObjectURL(await res.blob());
      const a = h("a", { href: url, download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      msg.replaceChildren(h("div", { class: "notice ok" }, T.backup_done));
      pw.value = pw2.value = "";
    } catch (e) {
      msg.replaceChildren(h("div", { class: "notice error" }, e.message));
    } finally {
      download.disabled = false; download.textContent = T.backup_download;
    }
  } }, T.backup_download);
  const canBackup = status?.auth;
  main.append(
    h("div", { class: "card" }, h("h2", {}, T.backup_title), h("p", {}, T.backup_intro), contents,
      canBackup ? [h("div", { class: "field" }, h("label", {}, T.backup_pw), pw),
                   h("div", { class: "field" }, h("label", {}, T.backup_pw2), pw2), download, msg]
        : h("div", { class: "notice warn" }, T.backup_needs_pw, " ",
            h("button", { class: "link", onclick: () => { tab = "config"; showApp(); } }, "→ " + T.tab_config))),
    h("div", { class: "card" }, h("h2", {}, T.restore_title), h("p", {}, T.restore_intro), restoreForm(afterRestore)));
}

// --- start ---------------------------------------------------------------------------------
(async () => {
  if (!SKINS.includes(currentSkin())) document.documentElement.dataset.skin = DEFAULT_SKIN;
  await loadTexts();
  try {
    const first = await api("/api/status");
    if (first.state === "unconfigured") tab = "setup";  // new installation: start with the assistant
    showApp();
  } catch (e) {
    if (e.message !== "unauthorized") showLogin(T.network_error);
  }
})();
