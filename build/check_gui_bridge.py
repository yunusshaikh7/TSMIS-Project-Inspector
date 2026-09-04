"""GUI bridge checks: build the real GuiApi (no window), drive a real scan
and a diagnostics bundle through ScanWorker + the message pump, and prove the
single-task gate.

    python build\\check_gui_bridge.py
"""
import sys
import time
import zipfile

from _checklib import Checker, patch, scripts_path, temp_dir

scripts_path()

import gui_api  # noqa: E402
import settings  # noqa: E402
from self_test import synthetic_aprx  # noqa: E402


class _NoWorker:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def _drain(api):
    """Every event queued for JS so far (the sender never runs here)."""
    out = []
    while not api._out.empty():
        out.append(api._out.get_nowait())
    return out


def _wait_idle(api, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if api._task is None:
            return True
        time.sleep(0.05)
    return False


def main():
    c = Checker()
    with temp_dir("tsmis_bridge_") as tmp, patch(gui_api, "UpdateWorker", _NoWorker), \
            patch(settings, "CONFIG_FILE", tmp / "config.json"):
        api = gui_api.GuiApi()
        init = api.get_initial_state()
        print("initial state:")
        c.check("carries app name, version, settings + a state snapshot",
                init["app_name"] and init["version"] and init["settings"]["values"]
                and init["state"]["task"] is None and init["state"]["update"]["phase"] == "idle", init)
        c.check("settings meta names the paths", set(init["settings"]["meta"]) >= {"data_root", "output_root", "log_file"})
        c.check("set_setting refuses an unknown key", api.set_setting("nope", 1).get("error"))
        c.check("set_setting persists a known key", api.set_setting("recursive", False)["values"]["recursive"] is False)
        c.check("cancel when idle is refused", api.cancel_scan().get("error"))
        c.check("open_workbook before any scan is refused", api.open_workbook().get("error"))

        print("scan through the bridge:")
        c.check("a missing folder is refused without claiming the gate",
                api.start_scan(str(tmp / "missing")).get("error") and api._task is None)
        projects = tmp / "projects"
        projects.mkdir()
        synthetic_aprx(projects / "One.aprx", version="OWNER.One")
        synthetic_aprx(projects / "Two.aprx", version="OWNER.Two")
        with patch(gui_api, "OUTPUT_ROOT", tmp / "output"), patch(sys.modules["scan_output"], "new_run_dir",
                                                                lambda now=None: tmp / "output" / "run"):
            res = api.start_scan(str(projects), True, False)
            c.check("start_scan claims the gate", res.get("ok") and api._task == "scan")
            c.check("a second start is refused while running", api.start_scan(str(projects)).get("error"))
            c.check("scan finishes and frees the gate", _wait_idle(api))
        last = api._last_scan
        c.check("last_scan carries counts, rows (with environments) and the workbook, no bundle",
                last and last["ok"] and last["counts"]["ok"] == 2 and len(last["rows"]) == 2
                and last["rows"][0]["environments"] == ["Prod"]
                and last["workbook"].endswith("branch_versions.xlsx") and last["bundle"] is None, last)
        c.check("the scan persisted its settings", settings.get("scan_root") == str(projects)
                and settings.get("recursive") is True)
        kinds = [e["t"] for e in _drain(api)]
        c.check("JS saw run_started, progress, logs, run_ended and states in that shape",
                kinds[0] == "run_started" and "progress" in kinds and "log" in kinds
                and kinds[-2:] == ["run_ended", "state"], kinds)
        c.check("a fresh scan can start again after the first", api.start_scan(str(projects)).get("ok")
                and _wait_idle(api))

        print("diagnostics bundle through the bridge:")
        with patch(gui_api, "_pick_save", lambda window, default: None):
            c.check("a cancelled Save dialog claims nothing",
                    api.export_diagnostics(str(projects)).get("cancelled") and api._task is None)
        bundle_path = tmp / "bundle" / "diag"          # no .zip on purpose
        with patch(gui_api, "_pick_save", lambda window, default: str(bundle_path)), \
                patch(gui_api, "OUTPUT_ROOT", tmp / "output"), \
                patch(sys.modules["scan_output"], "new_run_dir", lambda now=None: tmp / "output" / "run2"):
            _drain(api)
            res = api.export_diagnostics(str(projects), True, False)
            c.check("export starts a scan under the gate", res.get("ok") and api._task == "scan")
            c.check("...and finishes", _wait_idle(api))
        last = api._last_scan
        c.check("the bundle was written with a .zip suffix and recorded",
                last and last["bundle"] == str(bundle_path) + ".zip" and (tmp / "bundle" / "diag.zip").is_file(), last)
        with zipfile.ZipFile(tmp / "bundle" / "diag.zip") as zf:
            names = zf.namelist()
        c.check("bundle carries summary, workbook and the raw documents",
                "summary.json" in names and "branch_versions.xlsx" in names
                and any(n.startswith("raw/001 One/") for n in names), names)
        evs = _drain(api)
        c.check("JS got the saved-bundle modal", any(e["t"] == "modal" and "bundle" in e["title"].lower() for e in evs))
        c.check("update endpoints refuse when nothing is staged",
                api.update_start().get("error") and api.update_apply().get("error"))
    raise SystemExit(c.summary())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
