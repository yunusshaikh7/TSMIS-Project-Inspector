"""A small local WebView2 window. ArcPy always runs in a separate interpreter."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from core import export_bundle
from history import SavedLists, folder_key, folder_path
from runtime import ScanRunner, assets, check_webview_runtime, data_dir, default_folder, find_arcgis_python, prepare_desktop
from version import APP_NAME, VERSION


class Api:
    def __init__(self):
        self._window = None
        self._lists = SavedLists(data_dir() / "Lists")
        self._runner = ScanRunner(on_complete=self._lists.save)
        self._list_warning = ""
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
        record = self._load_list(self._settings["root"])
        if not record and not self._list_warning and not Path(self._settings["root"]).is_dir():
            self._settings["root"] = default_folder()
            record = self._load_list(self._settings["root"])
        self._runner.restore(record)
        self._release = None
        self._downloaded = None
        self._installing = False
        self._restart_args = ()
        self._update_lock = threading.Lock()

    def _load_list(self, root):
        self._list_warning = ""
        try:
            return self._lists.load(root)
        except ValueError as exc:
            self._list_warning = str(exc)
            return None

    def get_initial_state(self):
        return {"version": VERSION, "settings": self._settings, "arcgis_found": bool(self._settings["python"]),
                "saved_paths": self._lists.paths(), "state": self._runner.snapshot(), "warning": self._list_warning}

    def get_saved_paths(self):
        return self._lists.paths()

    def select_path(self, root):
        with self._runner.lock:
            if self._runner.state["running"] or self._installing:
                return {"ok": False, "error": "Finish or stop the refresh before switching folders."}
            try:
                root = folder_path(root)
                record = self._load_list(root)
                values = dict(self._settings, root=record["root"] if record else root)
                if record:
                    for key in ("recursive", "match"):
                        values[key] = record["result"].get(key, values[key])
                saved = self.save_settings(values)
                if not saved["ok"]:
                    return saved
                self._runner.restore(record)
                return {"ok": True, "settings": self._settings, "state": self._runner.snapshot(),
                        "saved_paths": self._lists.paths(), "warning": self._list_warning}
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}

    def clear_list(self, root):
        with self._runner.lock:
            if self._runner.state["running"] or self._installing:
                return {"ok": False, "error": "Stop the refresh before clearing this list."}
            try:
                if folder_key(root) != folder_key(self._settings["root"]):
                    raise ValueError("Select this folder before clearing its saved list.")
                self._lists.clear(root)
                self._runner.restore()
                return {"ok": True, "state": self._runner.snapshot(), "saved_paths": self._lists.paths()}
            except (OSError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}

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
        with self._runner.lock:
            if self._runner.state["running"] or self._installing:
                return {"ok": False, "error": "A refresh is already running."}
            saved = self.save_settings(values)
            if not saved["ok"]:
                return saved
            try:
                self._runner.restore(self._load_list(self._settings["root"]))
                request = {k: v for k, v in self._settings.items() if k != "python"}
                self._runner.start(self._settings["python"], dict(request, diagnostics=bool(diagnostics)))
                return {"ok": True}
            except (OSError, ValueError) as exc:
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
            if not getattr(sys, "frozen", False):
                raise RuntimeError("Use the packaged Windows app to install updates.")
            self._downloaded = updater.download_release(self._release, data_dir() / "Updates")
            return {"ok": True, "message": "Ready. Restart to replace this app and keep your saved lists and settings."}
        except Exception as exc:
            return {"ok": False, "error": "Update could not be downloaded: " + str(exc)}
        finally:
            self._update_lock.release()

    def install_update(self):
        import installer
        if not self._update_lock.acquire(blocking=False):
            return {"ok": False, "error": "An update operation is already running."}
        try:
            with self._runner.lock:
                if self._runner.state["running"] or self._installing:
                    return {"ok": False, "error": "Finish or stop your refresh before updating."}
                if not self._downloaded or not self._downloaded.is_file():
                    return {"ok": False, "error": "Download the update first."}
                installer.begin_install(self._downloaded, Path(sys.executable), restart_args=self._restart_args)
                self._installing = True
                threading.Timer(0.25, self._window.destroy).start()
                return {"ok": True, "message": "Restarting and updating this appâ€¦"}
        except Exception as exc:
            return {"ok": False, "error": "Update could not start: " + str(exc)}
        finally:
            self._update_lock.release()


def main():
    if "--apply-update" in sys.argv:
        import installer
        index = sys.argv.index("--apply-update")
        raise SystemExit(installer.apply_update(Path(sys.executable), sys.argv[index + 1],
                                               int(sys.argv[index + 2]), sys.argv[index + 3:]))
    updated = "--updated" in sys.argv
    # The hidden smoke run exercises the actual packaged UI without ArcGIS.
    smoke = "--smoke-test" in sys.argv
    if "--self-test" in sys.argv:
        from core import interpret
        assert (assets() / "ui" / "index.html").is_file()
        assert interpret({"connection_info": {"url": "https://example-prod.test/server/rest/services/TSMIS/Roads/FeatureServer", "version": "sde.DEFAULT"}}, {})[0]["version"] == "sde.DEFAULT"
        return
    session = None
    try:
        prepare_desktop()
        check_webview_runtime()
        session = tempfile.TemporaryDirectory(prefix="webview-", dir=data_dir(), ignore_cleanup_errors=True)
        from pythonnet import load
        load("netfx")
        import webview
        api = Api()
        window = webview.create_window(APP_NAME, str(assets() / "ui" / "index.html"), js_api=api,
                                      width=900, height=600, min_size=(760, 460), background_color="#f5f7f6", hidden=smoke)
        api._window = window
        window.events.closed += api._runner.stop

        def after_start():
            import time
            target = Path(sys.argv[sys.argv.index("--smoke-test") + 1]) if smoke else None
            cleanup = None
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if window.evaluate_js("document.body.dataset.ready === 'true'"):
                        if updated:
                            import installer
                            index = sys.argv.index("--updated")
                            cleanup = installer.finish_update(Path(sys.executable), sys.argv[index + 1], int(sys.argv[index + 2]))
                        if not smoke:
                            return
                        if "--test-python" in sys.argv:
                            python = sys.argv[sys.argv.index("--test-python") + 1]
                            with tempfile.TemporaryDirectory(prefix="probe-", dir=data_dir()) as folder:
                                original = dict(api._settings)
                                roots = [str(Path(folder) / name) for name in ("First", "Second")]
                                stamps = {}
                                for root in roots:
                                    Path(root).mkdir()
                                    if not api.select_path(root)["ok"]:
                                        raise RuntimeError("Could not select a portable folder.")
                                    response = api.start_scan(dict(api._settings, python=python))
                                    if not response["ok"]:
                                        raise RuntimeError(response["error"])
                                    deadline = time.monotonic() + 15
                                    while api._runner.snapshot()["running"] and time.monotonic() < deadline:
                                        time.sleep(0.05)
                                    state = api.get_scan_state()
                                    if state["running"] or not state["complete"] or not state["last_refreshed"]:
                                        raise RuntimeError("Packaged worker/list save failed: " + (state["error"] or state["save_error"]))
                                    stamps[root] = state["last_refreshed"]
                                restored = api.select_path(roots[0])["state"]
                                if restored["last_refreshed"] != stamps[roots[0]] or not Api().get_scan_state()["has_result"]:
                                    raise RuntimeError("Saved list did not survive reopening.")
                                if not api.clear_list(roots[0])["ok"] or api._lists.load(roots[0]) is not None or api._lists.load(roots[1]) is None:
                                    raise RuntimeError("Clearing a list affected the wrong folder.")
                                api.select_path(original["root"])
                                api.save_settings(original)
                        if not Path(str(window.native.browser.user_data_folder)).is_relative_to(session.name):
                            raise RuntimeError("Browser data escaped the portable folder.")
                        from System.Drawing import Icon
                        expected_icon = Icon(str(assets() / "ui" / "app.ico")).ToBitmap()
                        actual_icon = window.native.Icon.ToBitmap()
                        if (expected_icon.Size != actual_icon.Size or
                                any(expected_icon.GetPixel(x, y) != actual_icon.GetPixel(x, y)
                                    for x in range(actual_icon.Width) for y in range(actual_icon.Height))):
                            raise RuntimeError("The native window icon does not match the app icon.")
                        actual_icon.Dispose()
                        expected_icon.Dispose()
                        if not api.save_settings(api._settings)["ok"]:
                            raise RuntimeError("Portable settings could not be saved.")
                        if "--stage-update" in sys.argv:
                            index = sys.argv.index("--stage-update")
                            api._downloaded = Path(sys.argv[index + 1])
                            api._restart_args = ("--smoke-test", sys.argv[index + 2], "--test-python", python)
                            response = api.install_update()
                            if not response["ok"]:
                                raise RuntimeError(response["error"])
                        target.write_text("PASS: downloaded DLLs, WebView2, Python bridge, external worker, per-folder saved lists/reopening/Clear, portable cache, and window icon.", encoding="utf-8")
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("The interface did not become ready.")
            except Exception as exc:
                if target:
                    target.write_text("FAILED: " + str(exc), encoding="utf-8")
                elif updated:
                    window.destroy()
            finally:
                if smoke:
                    if cleanup:
                        cleanup.join(timeout=15)
                    window.destroy()
        webview.start(after_start if smoke or updated else None, gui="edgechromium", icon=str(assets() / "ui" / "app.ico"),
                      private_mode=True, storage_path=session.name)
    except Exception as exc:
        message = "The app could not start.\n\n" + str(exc)
        if "Failed to resolve Python.Runtime" in str(exc):
            message += "\n\nExtract the complete ZIP to a local folder. If this continues, ask IT to check .NET Framework and application restrictions."
        elif isinstance(exc, PermissionError):
            message += "\n\nMove the complete app folder to a location you can write to, such as Documents or a USB drive."
        if smoke:
            Path(sys.argv[sys.argv.index("--smoke-test") + 1]).write_text("FAILED: " + message, encoding="utf-8")
            raise SystemExit(1)
        if updated:
            raise SystemExit(1)
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
        else:
            raise
    finally:
        if session:
            session.cleanup()


if __name__ == "__main__":
    main()
