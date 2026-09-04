"""The GUI's worker threads and the worker -> GUI message vocabulary.

Workers post (kind, payload) tuples onto the GuiApi queue; gui_api._handle
dispatches them. ScanWorker posts exactly one terminal message (SCAN_DONE),
UpdateWorker posts UPDATE_STATUS snapshots. Nothing here touches the window.
"""
import logging
import threading

from aprx_scan import ScanError, run_scan
from events import Events
from scan_output import save, summary_lines, table_rows

log = logging.getLogger("tsmis.gui")


class Msg:
    LOG = "log"
    PROGRESS = "progress"
    SCAN_DONE = "scan_done"          # terminal: frees the single-task gate
    UPDATE_STATUS = "update_status"


class ScanWorker(threading.Thread):
    """Runs one scan, translating engine Events into GUI messages."""

    def __init__(self, queue, root, recursive, include_map_layer_files, cancel_event):
        super().__init__(daemon=True, name="scan")
        self.q = queue
        self.root = root
        self.recursive = recursive
        self.include_map_layer_files = include_map_layer_files
        self.cancel = cancel_event

    def run(self):
        events = Events(
            on_log=lambda text: self.q.put((Msg.LOG, text)),
            on_progress=lambda done, total, current: self.q.put(
                (Msg.PROGRESS, {"done": done, "total": total, "current": current})),
            is_cancelled=self.cancel.is_set)
        try:
            result = run_scan(self.root, recursive=self.recursive,
                              include_map_layer_files=self.include_map_layer_files,
                              events=events)
            workbook, diagnostics = save(result)
        except ScanError as e:
            self.q.put((Msg.SCAN_DONE, {"ok": False, "message": str(e)}))
            return
        except Exception as e:                  # noqa: BLE001 — the gate must always be freed
            log.exception("scan worker crashed")
            self.q.put((Msg.SCAN_DONE, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"}))
            return
        self.q.put((Msg.SCAN_DONE, {
            "ok": True, "cancelled": result.cancelled, "counts": result.counts(),
            "summary": summary_lines(result, workbook), "rows": table_rows(result),
            "workbook": str(workbook), "diagnostics": str(diagnostics),
            "run_dir": str(workbook.parent), "root": str(result.root),
        }))


class UpdateWorker(threading.Thread):
    """Drives updater.py off the GUI thread: "check" compares the latest
    release to this build; "download" streams + stages it. Posts
    (UPDATE_STATUS, {phase, ...}) — the dict is the GUI's whole update state."""

    def __init__(self, queue, action, manual=False, info=None):
        super().__init__(daemon=True, name="update")
        self.q = queue
        self.action = action
        self.manual = manual
        self.info = info

    def run(self):
        import updater
        try:
            if self.action == "check":
                info = updater.check_for_update()
                if info is None:
                    self.q.put((Msg.UPDATE_STATUS, {"phase": "none", "manual": self.manual}))
                    return
                self.q.put((Msg.UPDATE_STATUS, {
                    "phase": "available", "version": info.version, "url": info.release_url,
                    "size_mb": round(info.asset_size / 1e6) or None,
                    "can_apply": updater.update_support()[0] == "ok",
                    "manual": self.manual, "_info": info}))
                return
            last_pct = -1

            def on_progress(done, total):
                nonlocal last_pct
                pct = min(100, int(done * 100 / total)) if total else 0
                if pct != last_pct:
                    last_pct = pct
                    self.q.put((Msg.UPDATE_STATUS, {
                        "phase": "downloading", "progress": pct, "version": self.info.version,
                        "url": self.info.release_url, "can_apply": True}))

            staged = updater.download_and_stage(self.info, on_progress=on_progress)
            self.q.put((Msg.UPDATE_STATUS, {
                "phase": "staged", "version": self.info.version, "url": self.info.release_url,
                "can_apply": True, "staged": str(staged)}))
        except updater.UpdateError as e:
            log.warning("update %s failed: %s", self.action, e)
            self.q.put((Msg.UPDATE_STATUS, {"phase": "failed", "note": str(e),
                                            "manual": self.manual or self.action == "download"}))
        except Exception as e:                  # noqa: BLE001
            log.exception("update worker crashed (%s)", self.action)
            self.q.put((Msg.UPDATE_STATUS, {"phase": "failed", "note": f"{type(e).__name__}: {e}",
                                            "manual": True}))
