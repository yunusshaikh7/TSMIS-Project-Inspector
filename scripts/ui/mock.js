/* Browser-preview mock (index.html#mock only). Stands in for gui_api.GuiApi so
 * the layout and flows can be exercised without launching the real app. Never
 * loaded in production — index.html appends this script only under #mock. */
"use strict";

function makeMockApi() {
  const d = (evs) => window.__tsmis && window.__tsmis.dispatch(evs);
  const state = { task: null, scan: null, last_scan: null, update: { phase: "idle" } };
  const push = () => d([{ t: "state", s: JSON.parse(JSON.stringify(state)) }]);
  const settings = {
    values: { scan_root: "C:\\Users\\sample\\OneDrive - Example Organization\\Documents\\01_Projects\\TSNR\\GIS_Projects",
              recursive: true, include_map_layer_files: false, debug_logging: false, ui_devtools: false },
    scan_root_effective: "C:\\Users\\sample\\Documents\\ArcGIS\\Projects",
    meta: { version: "0.1.0", build: "browser preview", data_root: "C:\\Apps\\TSMIS Branch Identifier",
            output_root: "C:\\Apps\\TSMIS Branch Identifier\\output",
            log_file: "C:\\Apps\\TSMIS Branch Identifier\\data\\logs\\tsmis-gui.log", update_support: "ok" },
  };
  const files = ["D07_I5_Widening.aprx", "TSNR_QA_2026.aprx", "Ramp_Inventory.aprx", "Old_Template.aprx", "Broken.aprx"];
  const rows = [
    { name: files[0], folder: "…\\GIS_Projects\\D07", status: "ok", status_text: "OK", environments: ["Prod"], versions: ["KELLY.D07_I5_2026"], layers: 14, message: "" },
    { name: files[1], folder: "…\\GIS_Projects\\QA", status: "ok", status_text: "OK", environments: ["Test", "Prod"], versions: ["sde.DEFAULT", "KELLY.QA_check"], layers: 22, message: "" },
    { name: files[2], folder: "…\\GIS_Projects", status: "no_versions", status_text: "No version found", environments: [], versions: [], layers: 3, message: "data connections found, but none names a version" },
    { name: files[3], folder: "…\\GIS_Projects\\archive", status: "no_connections", status_text: "No data connections", environments: [], versions: [], layers: 0, message: "no data connections found (6 files inside, 4 JSON)" },
    { name: files[4], folder: "…\\GIS_Projects", status: "error", status_text: "Error", environments: [], versions: [], layers: 0, message: "not a valid project file (not a zip archive)" },
  ];
  let cancelled = false;

  function fakeScan(root, bundle) {
    if (state.task) return { error: "A scan is already running." };
    state.task = "scan"; state.scan = { done: 0, total: 0, current: "" }; cancelled = false;
    d([{ t: "run_started" }]); push();
    d([{ t: "log", text: `Looking for .aprx files under ${root} and its subfolders…` }]);
    setTimeout(() => d([{ t: "log", text: `Found ${files.length} file(s). Skipped 2 .backups folder(s).` }]), 500);
    files.forEach((f, i) => setTimeout(() => {
      if (cancelled) return;
      state.scan = { done: i, total: files.length, current: root + "\\" + f };
      d([{ t: "progress", p: state.scan }]);
      const r = rows[i];
      setTimeout(() => d([{ t: "log", text: r.status === "ok" ? `  ${f}: ${r.versions.join(", ")}  [${r.environments.join(", ")}]` : `  ${f}: ${r.status_text} — ${r.message}` }]), 350);
    }, 900 + i * 700));
    setTimeout(() => {
      if (cancelled) return;
      state.task = null; state.scan = null;
      const run = settings.meta.output_root + "\\2026-09-04 14-30-12";
      state.last_scan = { ok: true, cancelled: false, root, rows,
        counts: { total: 5, ok: 2, no_versions: 1, no_connections: 1, error: 1, cloud_only: 3 },
        workbook: run + "\\branch_versions.xlsx", run_dir: run, bundle: bundle || null };
      const evs = [{ t: "log", text: "" },
        { t: "log", text: "Read 5 file(s) under " + root + ": 2 with a version, 1 without a version, 1 without data connections, 1 error(s)." },
        { t: "log", text: "Versions found: sde.DEFAULT (Test): 1 project; KELLY.D07_I5_2026 (Prod): 1 project; KELLY.QA_check (Prod): 1 project" },
        { t: "log", text: "Workbook saved: " + state.last_scan.workbook }];
      if (bundle) evs.push({ t: "log", text: "Diagnostics bundle saved: " + bundle },
        { t: "modal", kind: "info", title: "Diagnostics bundle saved", message: bundle + "\n\nSend this file to the maintainer. It holds the structure of every project read (passwords removed), the results workbook, and the app's log." });
      evs.push({ t: "run_ended" });
      d(evs);
      push();
    }, 900 + files.length * 700 + 500);
    return { ok: true };
  }

  return {
    get_initial_state: async () => ({ app_name: "TSMIS Branch Identifier", version: "0.1.0",
      output_root: settings.meta.output_root, settings, state: JSON.parse(JSON.stringify(state)) }),
    ui_ready: async () => true,
    ui_event: async () => true,
    log_js_error: async (m) => { console.error("mock log_js_error", m); return true; },
    get_settings: async () => settings,
    set_setting: async (k, v) => { settings.values[k] = v; return { ok: true, values: settings.values }; },
    pick_scan_folder: async () => ({ folder: "C:\\Users\\sample\\Documents\\ArcGIS\\Projects" }),
    start_scan: async (root) => fakeScan(root, null),
    export_diagnostics: async (root) => fakeScan(root, "C:\\Users\\sample\\Desktop\\tsmis_branch_diagnostics_20260904_143012.zip"),
    cancel_scan: async () => {
      cancelled = true; state.task = null; state.scan = null;
      d([{ t: "log", text: "Scan cancelled." }, { t: "run_ended" }]); push();
      return { ok: true };
    },
    open_output_folder: async () => ({ ok: true }),
    open_run_folder: async () => ({ ok: true }),
    open_workbook: async () => (state.last_scan ? { ok: true } : { error: "No workbook yet — run a scan first." }),
    open_logs_folder: async () => ({ ok: true }),
    check_updates: async () => {
      d([{ t: "log", text: "Checking for updates…" }]);
      state.update = { phase: "checking" }; push();
      setTimeout(() => { state.update = { phase: "available", version: "0.2.0", can_apply: true, size_mb: 42, url: "" };
        d([{ t: "log", text: "Update available: v0.2.0 (42 MB) — click ‘Update to v0.2.0’ in the title bar to install it." }]); push(); }, 800);
      return { ok: true };
    },
    update_start: async () => {
      let pct = 0;
      const tick = () => { pct += 20; state.update = { phase: pct >= 100 ? "staged" : "downloading", progress: pct, version: "0.2.0", can_apply: true }; push(); if (pct < 100) setTimeout(tick, 300); };
      tick();
      return { ok: true };
    },
    update_apply: async () => { state.update = { phase: "applying" }; push(); return { ok: true }; },
    open_release_page: async () => ({ ok: true }),
  };
}

boot(makeMockApi());
