"""Python side of the desktop window (pywebview / Edge WebView2).

The window is a WebView rendering scripts/ui/ (plain HTML/CSS/JS); this
module is everything behind it:

  * GuiApi — the js_api bridge. Every public method is callable from JS as
    `window.pywebview.api.<name>(...)`. Methods validate, mutate the small
    GUI state, and start worker threads; the scanner stays console-free
    behind the Events seam.
  * the worker-queue pump — translates (kind, payload) worker messages into
    JSON events pushed to JS through ONE ordered sender thread, so log lines,
    progress and state snapshots never interleave out of order.
  * run() — creates the window and starts the webview loop.

Python owns all app state and pushes full snapshots; JS owns presentation.
Every line shown in the log pane is mirrored to the `tsmis.ui` logger.
"""
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

import webview

import settings
import updater
from gui_worker import Msg, ScanWorker, UpdateWorker
from logging_setup import active_log_file, set_debug_logging
from paths import DATA_ROOT, LOG_DIR, OUTPUT_ROOT, WEBVIEW_PROFILE_DIR, default_scan_root, is_frozen
from version import APP_NAME, __version__
import gui_win32

log = logging.getLogger("tsmis.gui")
ui_log = logging.getLogger("tsmis.ui")

_SHUTDOWN = object()


def _api_method(fn):
    """Wrap a js_api method: an uncaught exception in a windowed .exe would
    vanish and leave the UI hanging on a dead Promise, so log the traceback
    and hand JS a structured error instead."""
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:                    # noqa: BLE001
            logging.getLogger("tsmis.crash").critical(
                "uncaught exception in GUI api %s", fn.__name__, exc_info=True)
            try:
                self._emit_log(f"ERROR: {type(e).__name__}: {e} (details in the log file)")
            except Exception:
                pass
            return {"error": f"{type(e).__name__}: {e} (details are in the log file)"}
    wrapper.__name__ = fn.__name__
    return wrapper


def _app_icon_path():
    base = getattr(sys, "_MEIPASS", None)
    candidates = ([Path(base) / "app.ico"] if base else []) + [
        Path(__file__).resolve().parent.parent / "build" / "app.ico"]
    return next((c for c in candidates if c.exists()), None)


def _ui_index_path():
    base = getattr(sys, "_MEIPASS", None)
    if base and (Path(base) / "ui" / "index.html").exists():
        return Path(base) / "ui" / "index.html"
    return Path(__file__).resolve().parent / "ui" / "index.html"


def _pick_folder(window, start):
    """One native folder dialog -> a path string, or None when cancelled.
    pywebview returns a list/tuple on some backends and a string on others."""
    kwargs = {"directory": str(start)} if start and Path(start).is_dir() else {}
    picked = window.create_file_dialog(webview.FOLDER_DIALOG, **kwargs)
    if not picked:
        return None
    return str(picked[0] if isinstance(picked, (list, tuple)) else picked)


class GuiApi:
    """State + bridge behind the WebView UI. Public methods = the JS api."""

    def __init__(self):
        self._window = None
        self._lock = threading.RLock()
        self._q = Queue()             # worker -> GUI messages
        self._out = Queue()           # GUI -> JS events (ordered)
        self._ready = threading.Event()
        self._started = False
        self._task = None             # the single-flight gate: None | "scan"
        self._scan = None             # live progress {done, total, current}
        self._last_scan = None        # the last SCAN_DONE payload (JSON-safe)
        self._update = {"phase": "idle"}
        self._update_info = None
        self.cancel_event = threading.Event()
        self._dispatch = {
            Msg.LOG: self._emit_log,
            Msg.PROGRESS: self._on_progress,
            Msg.SCAN_DONE: self._on_scan_done,
            Msg.UPDATE_STATUS: self._on_update_status,
        }
        threading.Thread(target=self._worker_pump, daemon=True, name="gui-pump").start()
        threading.Thread(target=self._sender, daemon=True, name="gui-send").start()

    # ---- plumbing: Python -> JS ---------------------------------------------

    def attach(self, window):
        self._window = window
        # No 'shown' handler on purpose: pywebview fires window events on the
        # WinForms STA thread while WebView2 is still initializing, and any
        # work there can deadlock the window. The icon is applied from a
        # plain worker thread; 'closed' only fires after the loop is done.
        window.events.closed += self._on_closed
        threading.Thread(target=self._set_window_icon_late, daemon=True, name="gui-icon").start()

    def _emit(self, event):
        self._out.put(event)

    def _emit_log(self, text):
        if str(text).strip():
            ui_log.info("%s", text)
        self._emit({"t": "log", "text": str(text)})

    def _emit_modal(self, kind, title, message):
        ui_log.info("dialog (%s) %s: %s", kind, title, message)
        self._emit({"t": "modal", "kind": kind, "title": title, "message": message})

    def _state_snapshot(self):
        with self._lock:
            return {
                "task": self._task,
                "scan": dict(self._scan) if self._scan else None,
                "last_scan": self._last_scan,
                "update": dict(self._update),
            }

    def _push_state(self):
        self._emit({"t": "state", "s": self._state_snapshot()})

    def _sender(self):
        """The single ordered path to JS: batch whatever is queued and dispatch."""
        self._ready.wait()
        while True:
            ev = self._out.get()
            if ev is _SHUTDOWN:
                return
            batch = [ev]
            try:
                while len(batch) < 200:
                    nxt = self._out.get_nowait()
                    if nxt is _SHUTDOWN:
                        return
                    batch.append(nxt)
            except Empty:
                pass
            try:
                self._window.evaluate_js(
                    "window.__tsmis && window.__tsmis.dispatch(%s)"
                    % json.dumps(batch, default=str))
            except Exception as e:                # noqa: BLE001 — window torn down mid-run
                log.info("sender: evaluate_js failed (%s: %s)", type(e).__name__, e)

    def _set_window_icon_late(self):
        try:
            ico = _app_icon_path()
            if not ico:
                return
            hwnd = None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not hwnd:
                hwnd = gui_win32.find_own_window(APP_NAME)
                if not hwnd:
                    time.sleep(0.5)
            if hwnd:
                gui_win32.set_window_icon(hwnd, ico)
        except Exception as e:                    # noqa: BLE001 — cosmetic only
            log.info("window icon not set (%s: %s)", type(e).__name__, e)

    def _on_closed(self):
        ui_log.info("window closed by user%s", " (scan still running)" if self._task else "")
        self.cancel_event.set()
        self._out.put(_SHUTDOWN)
        self._ready.set()

    # ---- worker-queue pump ----------------------------------------------------

    def _worker_pump(self):
        while True:
            kind, payload = self._q.get()
            try:
                handler = self._dispatch.get(kind)
                if handler is None:
                    log.warning("unhandled worker event kind %r (payload dropped)", kind)
                    continue
                handler(payload)
            except Exception:                     # noqa: BLE001
                logging.getLogger("tsmis.crash").critical(
                    "uncaught exception handling worker message %r", kind, exc_info=True)

    def _on_progress(self, payload):
        with self._lock:
            self._scan = payload
        self._emit({"t": "progress", "p": payload})

    def _on_scan_done(self, payload):
        if payload.get("ok"):
            self._emit_log("")
            for line in payload.get("summary", []):
                self._emit_log(line)
            with self._lock:
                self._last_scan = {k: v for k, v in payload.items() if k != "summary"}
        else:
            self._emit_log(f"ERROR: {payload.get('message')}")
            self._emit_modal("error", "Scan failed", str(payload.get("message")))
        self._end_task()

    def _end_task(self):
        with self._lock:
            self._task = None
            self._scan = None
        self.cancel_event.clear()
        self._emit({"t": "run_ended"})
        self._push_state()

    # ---- the JS-callable api ---------------------------------------------------

    @_api_method
    def get_initial_state(self):
        if not self._started:
            self._started = True
            self._start_update_check()
        return {
            "app_name": APP_NAME,
            "version": __version__,
            "output_root": str(OUTPUT_ROOT),
            "settings": self.get_settings(),
            "state": self._state_snapshot(),
        }

    @_api_method
    def ui_ready(self):
        self._ready.set()
        log.info("ui ready (first render done)")
        return True

    @_api_method
    def ui_event(self, name):
        ui_log.info("ui: %s", name)
        return True

    @_api_method
    def log_js_error(self, message):
        logging.getLogger("tsmis.crash").error("uncaught JS error: %s", message)
        return True

    @_api_method
    def get_settings(self):
        values = settings.all_settings()
        return {
            "values": values,
            "scan_root_effective": values["scan_root"] or str(default_scan_root()),
            "meta": {
                "version": __version__,
                "build": "portable app" if is_frozen() else "development run",
                "data_root": str(DATA_ROOT),
                "output_root": str(OUTPUT_ROOT),
                "log_file": str(active_log_file()),
                "update_support": updater.update_support()[0],
            },
        }

    @_api_method
    def set_setting(self, key, value):
        if key not in settings.DEFAULTS:
            ui_log.warning("settings: refused unknown key %r", key)
            return {"error": f"'{key}' isn't one of the saved settings."}
        new = settings.update({key: value})
        if key == "debug_logging":
            set_debug_logging(new["debug_logging"])
        ui_log.info("settings: %s = %r", key, new.get(key))
        return {"ok": True, "values": new}

    @_api_method
    def pick_scan_folder(self, current=""):
        start = current or settings.get("scan_root") or str(default_scan_root())
        picked = _pick_folder(self._window, start)
        if not picked:
            return {"cancelled": True}
        settings.update({"scan_root": picked})
        ui_log.info("scan folder picked: %s", picked)
        return {"folder": picked}

    @_api_method
    def start_scan(self, root, recursive=True, include_map_layer_files=False):
        root = str(root or "").strip()
        if not root or not Path(root).is_dir():
            return {"error": "Pick a folder that exists first."}
        with self._lock:
            if self._task:
                return {"error": "A scan is already running."}
            self._task = "scan"
            self._scan = {"done": 0, "total": 0, "current": ""}
        self.cancel_event.clear()
        settings.update({"scan_root": root, "recursive": bool(recursive),
                         "include_map_layer_files": bool(include_map_layer_files)})
        ui_log.info("scan: user started (%s, subfolders=%s, map/layer files=%s)",
                    root, bool(recursive), bool(include_map_layer_files))
        self._emit({"t": "run_started"})
        self._push_state()
        ScanWorker(self._q, root, bool(recursive), bool(include_map_layer_files),
                   self.cancel_event).start()
        return {"ok": True}

    @_api_method
    def cancel_scan(self):
        if self._task != "scan":
            return {"error": "No scan is running."}
        ui_log.info("scan: user cancelled")
        self.cancel_event.set()
        self._emit_log("Cancelling after the current file…")
        return {"ok": True}

    # ---- folders ----------------------------------------------------------------

    def _open(self, target, create=False):
        try:
            if create:
                Path(target).mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
            ui_log.info("opened: %s", target)
        except Exception as e:                    # noqa: BLE001
            log.warning("could not open %s (%s: %s)", target, type(e).__name__, e)
            self._emit_modal("error", "Could not open", f"{target}\n\n{e}")

    @_api_method
    def open_output_folder(self):
        self._open(OUTPUT_ROOT, create=True)
        return {"ok": True}

    @_api_method
    def open_logs_folder(self):
        self._open(LOG_DIR, create=True)
        return {"ok": True}

    @_api_method
    def open_run_folder(self):
        last = self._last_scan or {}
        if not last.get("run_dir") or not Path(last["run_dir"]).is_dir():
            return {"error": "No scan results yet."}
        self._open(last["run_dir"])
        return {"ok": True}

    @_api_method
    def open_workbook(self):
        last = self._last_scan or {}
        if not last.get("workbook") or not Path(last["workbook"]).is_file():
            return {"error": "No workbook yet — run a scan first."}
        self._open(last["workbook"])
        return {"ok": True}

    # ---- one-click update -------------------------------------------------------

    def _start_update_check(self, manual=False):
        mode, why = updater.update_support()
        if mode == "off":
            log.info("update check skipped: %s", why)
            if manual:
                self._emit_log("Update check: not available in a development run.")
            return
        with self._lock:
            self._update = {"phase": "checking"}
        if manual:
            self._emit_log("Checking for updates…")
        self._push_state()
        UpdateWorker(self._q, "check", manual=manual).start()

    def _on_update_status(self, payload):
        manual = payload.pop("manual", False)
        info = payload.pop("_info", None)
        with self._lock:
            if info is not None:
                self._update_info = info
            self._update = payload
        phase, ver = payload.get("phase"), payload.get("version")
        if phase == "available":
            if payload.get("can_apply"):
                size = f" ({payload['size_mb']} MB)" if payload.get("size_mb") else ""
                self._emit_log(f"Update available: v{ver}{size} — click ‘Update to v{ver}’ "
                               "in the title bar to install it.")
                fail = updater.last_swap_failure()
                if fail:
                    log.warning("update: previous swap rolled back: %s", fail)
                    self._emit_log("Heads-up: the previous update attempt could not be applied "
                                   "and the old version was restored. Trying again usually works.")
            else:
                self._emit_log(f"Update available: v{ver}. This app folder isn't writable, so "
                               "the title-bar button opens the download page instead.")
        elif phase == "none" and manual:
            self._emit_log(f"You're on the latest version (v{__version__}).")
        elif phase == "staged":
            self._emit_log(f"Update v{ver} is downloaded and ready — click ‘Restart to update’ "
                           "when you're done (the app closes, updates itself, and reopens).")
        elif phase == "failed" and manual:
            self._emit_log(f"Update problem: {payload.get('note')} (details are in the log file)")
        self._push_state()

    @_api_method
    def check_updates(self):
        with self._lock:
            phase = self._update.get("phase")
        if phase in ("checking", "downloading", "applying"):
            return {"ok": True}
        if phase == "staged":
            self._emit_log("A download is already ready — click ‘Restart to update’ in the title bar.")
            return {"ok": True}
        self._start_update_check(manual=True)
        return {"ok": True}

    @_api_method
    def update_start(self):
        with self._lock:
            if self._update.get("phase") != "available" or not self._update.get("can_apply"):
                return {"error": "No update is ready to install."}
            info = self._update_info
            if info is None:
                return {"error": "No update is ready to install."}
            self._update = {"phase": "downloading", "progress": 0, "version": info.version,
                            "url": info.release_url, "can_apply": True}
        size = f" ({round(info.asset_size / 1e6)} MB)" if info.asset_size else ""
        self._emit_log(f"Downloading update v{info.version}{size}…")
        self._push_state()
        UpdateWorker(self._q, "download", info=info).start()
        return {"ok": True}

    @_api_method
    def update_apply(self):
        with self._lock:
            if self._task:
                return {"error": "Finish or cancel the running scan first."}
            if self._update.get("phase") != "staged":
                return {"error": "No downloaded update is ready."}
            staged = self._update.get("staged")
            self._update = dict(self._update, phase="applying")
        ui_log.info("update: user chose Restart to update")
        try:
            updater.apply_update_and_restart(staged)
        except updater.UpdateError as e:
            with self._lock:
                self._update = {"phase": "failed", "note": str(e)}
            self._emit_log(f"Update problem: {e} (details are in the log file)")
            self._push_state()
            return {"error": str(e)}
        self._emit_log("Restarting to finish the update — the app closes and reopens by itself…")
        self._push_state()
        threading.Thread(target=self._close_for_update, daemon=True, name="update-restart").start()
        return {"ok": True}

    def _close_for_update(self):
        time.sleep(1.2)                   # let the sender flush the goodbye line
        try:
            self._window.destroy()        # webview.start() returns; the process exits
        except Exception:                 # noqa: BLE001
            log.warning("window destroy failed; force-exiting so the swap can proceed", exc_info=True)
            os._exit(0)

    @_api_method
    def open_release_page(self):
        import webbrowser
        url = updater.safe_release_url(self._update.get("url"))
        ui_log.info("opening release page: %s", url)
        webbrowser.open(url)
        return {"ok": True}


# ============================== window bootstrap ==============================

def _fatal_box(text):
    try:
        gui_win32.message_box(text, APP_NAME)
    except Exception:
        pass


def run():
    """Create the window and run the GUI (blocks until the window closes)."""
    api = GuiApi()
    index = _ui_index_path()
    if not index.exists():
        log.critical("UI assets missing: %s", index)
        _fatal_box("The app's interface files are missing, so the window can't open. "
                   f"Re-extract the app folder, or reinstall.\n\n(Expected at: {index})")
        raise SystemExit(1)
    try:
        screen = webview.screens[0]
        width = min(1120, int(screen.width * 0.9))
        height = min(740, int(screen.height * 0.9))
    except Exception:                             # noqa: BLE001
        width, height = 1040, 700
    window = webview.create_window(APP_NAME, str(index), js_api=api, width=width, height=height,
                                   min_size=(560, 460), text_select=True)
    api.attach(window)
    debug = os.environ.get("TSMIS_UI_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if not debug:
        try:
            debug = bool(settings.get("ui_devtools"))
        except Exception:                         # noqa: BLE001
            pass
    log.info("starting webview (window %dx%d, ui=%s, profile=%s%s)", width, height, index,
             WEBVIEW_PROFILE_DIR, ", debug" if debug else "")
    try:
        # A persistent app-owned profile: pywebview's default private mode
        # writes a fresh Chromium profile into %TEMP% on EVERY launch.
        # gui="edgechromium" is forced so a missing runtime fails loudly.
        webview.start(gui="edgechromium", debug=debug, private_mode=False,
                      storage_path=str(WEBVIEW_PROFILE_DIR))
    except Exception as e:                        # noqa: BLE001
        log.critical("webview failed to start", exc_info=True)
        _fatal_box("The app window could not be created.\n\n"
                   "This tool shows its interface with Microsoft Edge WebView2, which is part "
                   "of Windows 10/11. Installing or updating Microsoft Edge restores it.\n\n"
                   "If this folder came from a downloaded zip, the zip's 'blocked' flag can "
                   "also cause this: right-click the zip → Properties → tick Unblock → "
                   "extract again into a folder you can write to.\n\n"
                   f"Details: {type(e).__name__}: {e}\nLog file: {LOG_DIR}")
        raise SystemExit(1)
