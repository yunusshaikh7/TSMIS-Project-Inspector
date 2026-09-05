"""A small local WebView2 window. ArcPy always runs in a separate interpreter."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from core import export_bundle
from runtime import ScanRunner, assets, data_dir, default_folder, find_arcgis_python, start_external
from version import APP_NAME, VERSION


class Api:
    def __init__(self):
        self._window = None
        self._runner = ScanRunner()
        self._settings_path = data_dir() / "settings.json"
        self._settings = {"root": default_folder(), "python": find_arcgis_python(), "recursive": True, "match": "tsmis"}
        try:
            saved = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key in self._settings:
                    if isinstance(saved.get(key), type(self._settings[key])):
                        self._settings[key] = saved[key]
        except (OSError, ValueError):
            pass
        if not self._settings["python"] or not Path(self._settings["python"]).is_file():
            self._settings["python"] = find_arcgis_python()
        self._release = None
        self._downloaded = None
        self._update_lock = threading.Lock()

    def get_initial_state(self):
        return {"version": VERSION, "settings": self._settings, "arcgis_found": bool(self._settings["python"])}

    def choose_folder(self):
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return selected[0] if selected else None

    def choose_python(self):
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Python (python.exe)",))
        return selected[0] if selected else None

    def save_settings(self, values):
        try:
            settings = {"root": str(values.get("root", self._settings["root"])).strip(),
                        "python": str(values.get("python", self._settings["python"])).strip(),
                        "recursive": bool(values.get("recursive", True)),
                        "match": str(values.get("match", "tsmis")).strip()}
            if not settings["match"]:
                raise ValueError("Enter the TSMIS identifier, usually tsmis.")
            self._settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            self._settings = settings
            return {"ok": True}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def start_scan(self, values, diagnostics=False):
        if self._runner.snapshot()["running"]:
            return {"ok": False, "error": "A scan is already running."}
        saved = self.save_settings(values)
        if not saved["ok"]:
            return saved
        try:
            request = {k: v for k, v in self._settings.items() if k != "python"}
            self._runner.start(self._settings["python"], dict(request, diagnostics=bool(diagnostics)))
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def get_scan_state(self):
        return self._runner.snapshot()

    def stop_scan(self):
        self._runner.stop()

    def get_project(self, index):
        with self._runner.lock:
            result = self._runner.state["result"]
            if result and isinstance(index, int) and 0 <= index < len(result["projects"]):
                return {k: v for k, v in result["projects"][index].items() if k != "connection_metadata"}
        return None

    def save_results(self, diagnostics=False):
        import webview
        with self._runner.lock:
            result = self._runner.state["result"]
            if self._runner.state["running"] or not result:
                return {"ok": False, "error": "Finish or stop the scan before saving."}
            if diagnostics and not result.get("diagnostic_scan"):
                return {"ok": False, "error": "Run Test / diagnostics first to collect connection metadata."}
            # Export an immutable snapshot; a later scan must not replace it.
            result = json.loads(json.dumps(result))
        filename = "TSMIS-diagnostics.zip" if diagnostics else "TSMIS-results.zip"
        selected = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename,
                                                  file_types=("ZIP archive (*.zip)",))
        if not selected:
            return {"ok": False, "cancelled": True}
        path = Path(selected if isinstance(selected, str) else selected[0])
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(path.suffix + ".zip")
        try:
            export_bundle(path, result, bool(diagnostics))
            return {"ok": True, "path": str(path)}
        except OSError:
            return {"ok": False, "error": "Could not save this file. Choose a writable folder and try again."}

    def check_updates(self):
        import updater
        if not self._update_lock.acquire(blocking=False):
            return {"ok": False, "error": "An update operation is already running."}
        try:
            self._release = updater.check_release()
            return dict(self._release, ok=True)
        except Exception as exc:
            self._release = None
            return {"ok": False, "error": str(exc)}
        finally:
            self._update_lock.release()

    def download_update(self):
        import updater
        if not self._release or not self._release.get("available"):
            return {"ok": False, "error": "Check for an available update first."}
        if not self._update_lock.acquire(blocking=False):
            return {"ok": False, "error": "An update operation is already running."}
        try:
            self._downloaded = updater.download_release(self._release, data_dir() / "updates")
            return {"ok": True, "message": "Update ready. Open the updated app to continue. The previous copy remains available."}
        except Exception as exc:
            return {"ok": False, "error": "Update could not be downloaded: " + str(exc)}
        finally:
            self._update_lock.release()

    def open_update(self):
        if self._runner.snapshot()["running"]:
            return {"ok": False, "error": "Finish or stop your scan before opening the update."}
        if not self._downloaded or not self._downloaded.is_file():
            return {"ok": False, "error": "Download the update first."}
        try:
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
            start_external([str(self._downloaded)], cwd=self._downloaded.parent, env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            os.startfile(str(self._downloaded.parent))
            return {"ok": True, "message": "Updated app opened. Save any results here before closing this window. Use the new folder's app next time."}
        except OSError:
            return {"ok": False, "error": "Windows could not open the update. Check your work PC's application restrictions."}


def main():
    # The hidden smoke run exercises the actual packaged UI without ArcGIS.
    smoke = "--smoke-test" in sys.argv
    if "--self-test" in sys.argv:
        from core import interpret
        assert (assets() / "ui" / "index.html").is_file()
        assert interpret({"connection_info": {"url": "https://example-prod.test/server/rest/services/TSMIS/Roads/FeatureServer", "version": "sde.DEFAULT"}}, {})[0]["version"] == "sde.DEFAULT"
        return
    try:
        import webview
        api = Api()
        window = webview.create_window(APP_NAME, str(assets() / "ui" / "index.html"), js_api=api,
                                      width=900, height=600, min_size=(760, 460), background_color="#f5f7f6", hidden=smoke)
        api._window = window
        window.events.closed += api._runner.stop

        def verify_window():
            import time
            target = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if window.evaluate_js("document.body.dataset.ready === 'true'"):
                        if "--test-python" in sys.argv:
                            import tempfile
                            python = sys.argv[sys.argv.index("--test-python") + 1]
                            with tempfile.TemporaryDirectory(prefix="tsmis-package-probe-") as folder:
                                api._runner.start(python, {"root": folder, "match": "tsmis", "recursive": True})
                                deadline = time.monotonic() + 15
                                while api._runner.snapshot()["running"] and time.monotonic() < deadline:
                                    time.sleep(0.05)
                                if api._runner.snapshot()["running"] or not api._runner.state["result"]["complete"]:
                                    raise RuntimeError("Packaged worker failed: " + api._runner.snapshot()["error"])
                        target.write_text("Packaged WebView2 interface, Python bridge, and external worker loaded successfully.", encoding="utf-8")
                        break
                    time.sleep(0.2)
                else:
                    target.write_text("FAILED: interface did not become ready.", encoding="utf-8")
            except Exception as exc:
                target.write_text("FAILED: " + str(exc), encoding="utf-8")
            finally:
                window.destroy()
        webview.start(verify_window if smoke else None, gui="edgechromium")
    except Exception as exc:
        message = "The app could not start (" + type(exc).__name__ + ").\n\nIf downloaded, unblock the ZIP in Properties and extract it again. Microsoft Edge WebView2 Runtime is required.\n\n" + str(exc)
        if smoke:
            Path(sys.argv[sys.argv.index("--smoke-test") + 1]).write_text("FAILED: " + message, encoding="utf-8")
            raise SystemExit(1)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
        else:
            raise


if __name__ == "__main__":
    main()
