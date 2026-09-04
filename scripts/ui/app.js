/* TSMIS Branch Identifier — frontend logic.
 *
 * Talks to Python through the pywebview js_api bridge (gui_api.GuiApi):
 *   JS -> Python : api.<method>(...) (all return Promises)
 *   Python -> JS : window.__tsmis.dispatch([{t: "state"|"log"|"progress"|
 *                  "run_started"|"run_ended"|"modal", ...}, ...])
 *
 * Python owns app state and pushes full snapshots; this file owns only
 * presentation + form fields. Log lines are never invented here — anything
 * worth showing goes through Python so the file log stays complete.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { init: null, st: null, logLines: 0, logPinned: true, running: false };
const LOG_MAX_LINES = 3000;
const ACTIVITY_KEY = "tsmis-bi-activity";
let api = null;

function icon(name, cls = "ic") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#" + name);
  svg.appendChild(use);
  return svg;
}

// ---------------------------------------------------------------- theme ----
const THEME_KEY = "tsmis-bi-theme";
function themePref() {
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (_) { return "auto"; }
}
function applyTheme() {
  const pref = themePref();
  const dark = pref === "dark" || (pref !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
function renderThemeButton() {
  const pref = themePref();
  const name = pref === "light" ? "i-sun" : pref === "dark" ? "i-moon" : "i-monitor";
  $("btnTheme").querySelector("use").setAttribute("href", "#" + name);
  $("btnTheme").title = "Theme: " + (pref === "auto" ? "System" : pref === "light" ? "Light" : "Dark") + " (click to change)";
}
function withThemeTransition(fn) {
  document.documentElement.classList.add("theme-anim");
  fn();
  setTimeout(() => document.documentElement.classList.remove("theme-anim"), 550);
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (themePref() === "auto") applyTheme(); });

// ------------------------------------------------------------- building ----
function buildStatic() {
  const init = S.init;
  $("appName").textContent = init.app_name;
  $("appVersion").textContent = "v" + init.version;
  document.title = init.app_name;
  fillSettings(init.settings);
}

function fillSettings(settings) {
  const v = settings.values || {};
  $("scanRoot").value = v.scan_root || settings.scan_root_effective || "";
  setChecked($("optRecursive"), v.recursive !== false);
  setChecked($("optMapLayer"), !!v.include_map_layer_files);
  setChecked($("setDebugLog"), !!v.debug_logging);
  setChecked($("setDevtools"), !!v.ui_devtools);
  const m = settings.meta || {};
  $("aboutVersion").textContent = "v" + (m.version || "");
  $("aboutBuild").textContent = m.build || "";
  $("aboutData").textContent = m.data_root || "";
  $("aboutOutput").textContent = m.output_root || "";
  $("aboutLog").textContent = m.log_file || "";
  $("updateNote").textContent = m.update_support === "off"
    ? "Updates are checked in the packaged app only."
    : m.update_support === "link"
      ? "This folder is read-only, so updates open the download page instead of installing."
      : "Checked automatically at startup.";
}

function setChecked(cb, on) {
  cb.checked = !!on;
  const row = cb.closest(".option-row");
  if (row) row.classList.toggle("checked", !!on);
}

// ------------------------------------------------------------- panels -----
function setActivity(on) {
  $("activityDrawer").classList.toggle("hidden", !on);
  $("btnActivity").setAttribute("aria-pressed", String(!!on));
  try { localStorage.setItem(ACTIVITY_KEY, on ? "1" : "0"); } catch (_) { /* session only */ }
  if (on) scrollLogToEnd();
}
function activityWanted() {
  try { return localStorage.getItem(ACTIVITY_KEY) === "1"; } catch (_) { return false; }
}
function setSettings(on) {
  $("paneSettings").classList.toggle("hidden", !on);
  $("resultsCard").classList.toggle("hidden", !!on);
  $("btnSettings").setAttribute("aria-pressed", String(!!on));
}

// -------------------------------------------------------------- renders ----
function renderState() {
  const st = S.st;
  if (!st) return;
  const locked = st.task != null;
  document.body.classList.toggle("busy", locked);
  document.querySelectorAll("[data-lock-when-busy]").forEach((el) => { el.disabled = locked; });
  ["optRecursive", "optMapLayer"].forEach((id) => { $(id).disabled = locked; });
  $("btnCancel").disabled = st.task !== "scan";
  const last = st.last_scan;
  $("btnOpenWorkbook").disabled = !(last && last.workbook);
  $("btnOpenRun").disabled = !(last && last.run_dir);
  renderResults(last);
  renderUpdate(st.update);
  $("progressStrip").classList.toggle("hidden", !locked);
  if (locked && st.scan) renderProgress(st.scan);
}

function renderResults(last) {
  const wrap = $("resultsWrap"), body = $("resultsBody");
  if (!last || !last.ok) {
    wrap.classList.add("hidden");
    $("resultsMeta").textContent = "No scan yet";
    $("resultsSummary").textContent = "Pick the folder that holds the projects and click Scan.";
    return;
  }
  const c = last.counts || {};
  $("resultsMeta").textContent = `${c.total || 0} file${c.total === 1 ? "" : "s"}`
    + (last.cancelled ? " (cancelled)" : "");
  $("resultsSummary").textContent = `${c.ok || 0} with a version · ${c.no_versions || 0} without a version · `
    + `${c.no_connections || 0} without data connections · ${c.error || 0} error${c.error === 1 ? "" : "s"}`
    + ` — ${last.root}`;
  body.textContent = "";
  (last.rows || []).forEach((r) => {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    const n = document.createElement("span"); n.className = "r-name"; n.textContent = r.name;
    const f = document.createElement("span"); f.className = "r-folder"; f.textContent = r.folder;
    name.append(n, f);
    const env = document.createElement("td"); env.className = "r-env";
    env.textContent = (r.environments || []).join(", ") || "—";
    const ver = document.createElement("td"); ver.className = "r-versions";
    ver.textContent = (r.versions || []).join(", ") || "—";
    const layers = document.createElement("td"); layers.className = "num"; layers.textContent = r.layers;
    const status = document.createElement("td"); status.className = "st-" + r.status;
    status.textContent = r.status_text;
    if (r.message) status.title = r.message;
    tr.append(name, env, ver, layers, status);
    body.appendChild(tr);
  });
  wrap.classList.remove("hidden");
}

function renderUpdate(up) {
  const b = $("btnUpdate");
  up = up || { phase: "idle" };
  const locked = S.st && S.st.task != null;
  let show = true, label = "", disabled = false, title = "";
  switch (up.phase) {
    case "available":
      if (up.can_apply) { label = `Update to v${up.version}`; title = "Download and install the new version"; }
      else { label = `v${up.version} available`; title = "Open the download page (this folder is read-only, so the app can't update itself)"; }
      break;
    case "downloading": label = `Downloading… ${up.progress || 0}%`; disabled = true; break;
    case "staged":
      label = "Restart to update"; disabled = locked;
      title = locked ? "Finish or cancel the running scan first" : `Install v${up.version} — the app closes and reopens by itself`;
      break;
    case "applying": label = "Restarting…"; disabled = true; break;
    default: show = false;
  }
  b.classList.toggle("hidden", !show);
  b.textContent = label; b.disabled = disabled; b.title = title;
}

async function onUpdateClick() {
  const up = (S.st && S.st.update) || {};
  if (up.phase === "available" && !up.can_apply) { api.open_release_page(); return; }
  if (up.phase === "available") {
    const res = await api.update_start();
    if (res && res.error) showMessage("error", "Could not start the update", res.error);
    return;
  }
  if (up.phase === "staged") {
    const ok = await showConfirm({
      title: "Restart and update?",
      message: `The app will close, install v${up.version} (takes a few seconds), and reopen by itself.\n\nYour scan results and settings stay where they are.`,
      confirmLabel: "Restart now",
    });
    if (!ok) return;
    const res = await api.update_apply();
    if (res && res.error) showMessage("error", "Could not update", res.error);
  }
}

// ------------------------------------------------------------- progress ----
function renderProgress(p) {
  const total = p.total || 0, done = p.done || 0;
  const pct = total ? Math.round(done * 100 / total) : 0;
  $("progressPct").textContent = total ? `${done} / ${total}` : "";
  $("progressFill").style.width = pct + "%";
  $("progressBar").classList.toggle("indeterminate", !total);
  $("progressText").textContent = total ? `Reading file ${Math.min(done + 1, total)} of ${total}` : "Looking for project files…";
  const cur = p.current || "";
  $("progressSub").textContent = cur; $("progressSub").title = cur;
}

function startRunUi() {
  S.running = true;
  $("progressStrip").classList.remove("hidden");
  renderProgress({ done: 0, total: 0, current: "" });
}

function endRunUi() {
  S.running = false;
  $("progressStrip").classList.add("hidden");
  $("progressBar").classList.remove("indeterminate");
  $("progressFill").style.width = "0%";
  $("progressPct").textContent = "";
  $("progressSub").textContent = "";
}

// ------------------------------------------------------------------ log ----
function appendLog(text) {
  const body = $("logBody");
  const ph = body.querySelector(".log-placeholder");
  if (ph) ph.remove();
  const line = document.createElement("div");
  line.className = "log-line" + (/^\s*ERROR|failed|cancelled/i.test(text) ? " err" : /saved:|^Read \d/.test(text) ? " ok" : "");
  line.textContent = text;
  body.appendChild(line);
  S.logLines++;
  while (S.logLines > LOG_MAX_LINES && body.firstChild) { body.removeChild(body.firstChild); S.logLines--; }
}
function scrollLogToEnd() {
  const body = $("logBody");
  if (S.logPinned) body.scrollTop = body.scrollHeight;
}
function clearLog() {
  $("logBody").textContent = "";
  S.logLines = 0;
  const ph = document.createElement("span"); ph.className = "log-placeholder"; ph.textContent = "Nothing yet.";
  $("logBody").appendChild(ph);
}

// --------------------------------------------------------------- modals ----
function buildModal({ kind, title, message, actions }) {
  const overlay = $("modalOverlay");
  overlay.textContent = "";
  const modal = document.createElement("div"); modal.className = "modal";
  const head = document.createElement("div"); head.className = "modal-head " + (kind || "info");
  head.appendChild(icon(kind === "error" || kind === "warning" ? "i-warn" : "i-check"));
  head.appendChild(Object.assign(document.createElement("span"), { textContent: title }));
  const body = document.createElement("div"); body.className = "modal-body"; body.textContent = message;
  const row = document.createElement("div"); row.className = "modal-actions";
  actions.forEach(({ label, primary, onClick }) => {
    const b = document.createElement("button");
    b.className = "btn " + (primary ? "btn-accent" : "btn-subtle");
    b.textContent = label;
    b.onclick = () => { overlay.classList.add("hidden"); overlay.textContent = ""; onClick(); };
    row.appendChild(b);
  });
  modal.append(head, body, row);
  overlay.appendChild(modal);
  overlay.classList.remove("hidden");
  const first = row.querySelector(".btn-accent") || row.querySelector("button");
  if (first) first.focus();
}
function showMessage(kind, title, message) {
  buildModal({ kind, title, message, actions: [{ label: "OK", primary: true, onClick: () => {} }] });
}
function showConfirm({ title, message, confirmLabel }) {
  return new Promise((resolve) => buildModal({
    kind: "info", title, message,
    actions: [{ label: "Cancel", primary: false, onClick: () => resolve(false) },
              { label: confirmLabel || "OK", primary: true, onClick: () => resolve(true) }],
  }));
}
function showFatal(title, detail) {
  let el = $("fatalBanner");
  if (!el) {
    el = document.createElement("div"); el.id = "fatalBanner"; el.className = "fatal-banner";
    const t = document.createElement("div"); t.className = "fatal-title"; t.id = "fatalTitle";
    const d = document.createElement("div"); d.className = "fatal-detail"; d.id = "fatalDetail";
    el.append(t, d); document.body.appendChild(el);
  }
  const t = $("fatalTitle"); t.textContent = ""; t.appendChild(icon("i-warn"));
  t.appendChild(Object.assign(document.createElement("span"), { textContent: title }));
  $("fatalDetail").textContent = detail;
}
function hideFatal() { const el = $("fatalBanner"); if (el) el.remove(); }

// ------------------------------------------------------- event dispatch ----
function dispatch(events) {
  let sawLog = false, lastStateAt = -1;
  for (let i = 0; i < events.length; i++) if (events[i] && events[i].t === "state") lastStateAt = i;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    try {
      switch (ev.t) {
        case "state": S.st = ev.s; if (i === lastStateAt) renderState(); break;
        case "log": appendLog(ev.text); sawLog = true; break;
        case "progress": if (S.running) renderProgress(ev.p); break;
        case "run_started": startRunUi(); break;
        case "run_ended": endRunUi(); break;
        case "modal": showMessage(ev.kind, ev.title, ev.message); break;
        default: console.warn("dispatch: unhandled event type", ev.t, ev);
      }
    } catch (e) {
      console.error("dispatch failed for", ev, e);
      try { api && api.log_js_error(`dispatch(${ev && ev.t}): ${e}`); } catch (_) { /* best-effort */ }
    }
  }
  if (sawLog) scrollLogToEnd();
}

// --------------------------------------------------------- user actions ----
function scanArgs() {
  return [$("scanRoot").value.trim(), $("optRecursive").checked, $("optMapLayer").checked];
}

async function startScan() {
  const [root] = scanArgs();
  if (!root) { showMessage("warning", "Pick a folder", "Type or browse to the folder that holds the ArcGIS Pro projects."); return; }
  const res = await api.start_scan(...scanArgs());
  if (res && res.error) showMessage("warning", "Can't start the scan", res.error);
}

async function exportDiagnostics() {
  const [root] = scanArgs();
  if (!root) { showMessage("warning", "Pick a folder", "Type or browse to the folder that holds the ArcGIS Pro projects first."); return; }
  const ok = await showConfirm({
    title: "Save a diagnostics bundle?",
    message: "This scans the folder like a normal scan and then saves one zip holding every project's "
      + "structure (passwords removed), the results workbook and the app's log — the file to send to "
      + "the maintainer so the reader can be checked against real projects.\n\nA Save dialog asks where to put it.",
    confirmLabel: "Choose where to save…",
  });
  if (!ok) return;
  setSettings(false);
  const res = await api.export_diagnostics(...scanArgs());
  if (res && res.error) showMessage("warning", "Can't start", res.error);
}

async function onSettingToggle(id, key) {
  const cb = $(id);
  setChecked(cb, cb.checked);
  const res = await api.set_setting(key, cb.checked);
  if (res && res.error) { showMessage("error", "Setting not saved", res.error); setChecked(cb, !cb.checked); }
}

function bindEvents() {
  renderThemeButton();
  $("btnTheme").onclick = () => {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(themePref()) + 1) % order.length];
    try { localStorage.setItem(THEME_KEY, next); } catch (_) { /* keep for session */ }
    withThemeTransition(() => { applyTheme(); renderThemeButton(); });
    api.ui_event("theme:" + next);
  };
  $("btnActivity").onclick = () => setActivity($("activityDrawer").classList.contains("hidden"));
  $("btnSettings").onclick = () => setSettings($("paneSettings").classList.contains("hidden"));
  $("btnUpdate").onclick = onUpdateClick;
  $("appVersion").onclick = () => api.check_updates();
  $("btnCheckUpdates").onclick = () => api.check_updates();

  $("btnBrowse").onclick = async () => {
    const r = await api.pick_scan_folder($("scanRoot").value.trim());
    if (r && r.folder) $("scanRoot").value = r.folder;
  };
  $("btnScan").onclick = startScan;
  $("scanRoot").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("btnScan").disabled) startScan(); });
  $("btnCancel").onclick = () => api.cancel_scan();
  $("btnOpenOutput").onclick = () => api.open_output_folder();
  $("btnOpenWorkbook").onclick = async () => {
    const r = await api.open_workbook();
    if (r && r.error) showMessage("info", "No workbook", r.error);
  };
  $("btnOpenRun").onclick = () => api.open_run_folder();
  $("btnOpenLogs").onclick = () => api.open_logs_folder();
  $("btnDiagnostics").onclick = exportDiagnostics;
  $("setDebugLog").addEventListener("change", () => onSettingToggle("setDebugLog", "debug_logging"));
  $("setDevtools").addEventListener("change", () => onSettingToggle("setDevtools", "ui_devtools"));

  $("btnClearLog").onclick = clearLog;
  $("btnCopyLog").onclick = async () => {
    const text = [...$("logBody").querySelectorAll(".log-line")].map((l) => l.textContent).join("\n");
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      $("btnCopyLog").textContent = "Copied";
      setTimeout(() => { $("btnCopyLog").textContent = "Copy"; }, 1200);
    } catch (_) { /* clipboard can be blocked */ }
  };
  const logBody = $("logBody");
  logBody.addEventListener("scroll", () => {
    S.logPinned = logBody.scrollHeight - logBody.scrollTop - logBody.clientHeight < 30;
  });
  window.addEventListener("error", (e) => {
    try { api && api.log_js_error(String(e.message || e.error)); } catch (_) { /* best-effort */ }
  });
}

// ----------------------------------------------------------- bootstrap -----
let booted = false;
async function boot(realApi) {
  if (booted) return;
  booted = true;
  api = realApi;
  // A cold WebView2 can expose the api OBJECT before its method stubs are
  // injected; retry patiently, re-grabbing the bridge each round.
  let init = null, lastErr = null;
  for (let attempt = 1; attempt <= 6; attempt++) {
    try { init = await api.get_initial_state(); lastErr = null; break; }
    catch (e) {
      lastErr = e;
      if (!WANT_MOCK && window.pywebview && window.pywebview.api) api = window.pywebview.api;
      await new Promise((r) => setTimeout(r, Math.min(1000 * attempt, 3000)));
    }
  }
  if (lastErr) init = { error: String(lastErr) };
  if (!init || init.error) {
    showFatal("The app couldn't load its settings",
              (init && init.error) || "No response from the app engine. Details are in the log file.");
    try { api.log_js_error("boot aborted: get_initial_state failed"); } catch (_) { /* broken bridge */ }
    return;
  }
  hideFatal();
  S.init = init;
  buildStatic();
  bindEvents();
  setActivity(activityWanted());
  S.st = init.state;
  renderState();
  window.__tsmis = {
    dispatch,
    test_state: () => JSON.stringify({ init: !!S.init, task: S.st && S.st.task, lines: S.logLines }),
  };
  await api.ui_ready();
}

// The mock preview is OPT-IN (index.html#mock) and must never race the real
// bridge: without #mock the page only ever waits for pywebview.
const WANT_MOCK = /[?#&]mock\b/.test(location.search + location.hash);
const bridgeReady = () =>
  !!(window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_initial_state === "function");

if (!WANT_MOCK) {
  window.addEventListener("pywebviewready", () => { if (bridgeReady()) boot(window.pywebview.api); });
  const poll = setInterval(() => {
    if (booted) { clearInterval(poll); return; }
    if (bridgeReady()) { clearInterval(poll); boot(window.pywebview.api); }
  }, 150);
  setTimeout(() => {
    if (!booted) showFatal("Still starting…",
      "The interface is waiting for the app engine. The first launch after an update can take "
      + "noticeably longer while Windows checks the new files — this goes away by itself.");
  }, 8000);
  setTimeout(() => {
    if (!booted) showFatal("The app's interface couldn't connect to its engine",
      "The page loaded but the pywebview bridge never arrived. Close the app and try again; "
      + "details are in the log file.");
  }, 60000);
}
