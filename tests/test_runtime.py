import json
import hashlib
from types import SimpleNamespace
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

from core import export_bundle
from history import SavedLists
from runtime import ScanRunner, app_dir, check_webview_runtime, data_dir, prepare_desktop, worker_environment


class RunnerTests(unittest.TestCase):
    def wait(self, runner):
        deadline = time.monotonic() + 12
        while runner.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.04)
        self.assertFalse(runner.snapshot()["running"])

    def test_portable_paths_follow_executable_and_ignore_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder, "Copied app", "TSMIS Branch Identifier.exe")
            with patch("sys.frozen", True, create=True), patch("sys.executable", str(exe)), patch.dict(os.environ, {"LOCALAPPDATA": "Z:/unavailable"}):
                self.assertEqual(app_dir(), exe.parent.resolve())
                self.assertEqual(data_dir(), exe.parent.resolve() / "Data")
                self.assertTrue(data_dir().is_dir())

    def test_worker_avoids_bytecode_and_inherited_python_settings(self):
        with patch.dict(os.environ, {"PYTHONPATH": "unrelated", "_PYI_APPLICATION_HOME_DIR": "unrelated"}):
            env = worker_environment(sys.executable)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    @unittest.skipUnless(os.name == "nt", "Windows download streams")
    def test_download_fix_only_trusts_unchanged_bundled_assemblies(self):
        with tempfile.TemporaryDirectory() as folder:
            dll = Path(folder, "shipped.dll")
            dll.write_bytes(b"expected assembly")
            marker = Path(str(dll) + ":Zone.Identifier")
            unrelated = Path(folder, "unrelated.dll")
            unrelated.write_bytes(b"unrelated")
            other_marker = Path(str(unrelated) + ":Zone.Identifier")
            marker.write_text("[ZoneTransfer]\nZoneId=3\n")
            other_marker.write_text(marker.read_text())
            info = SimpleNamespace(ASSEMBLIES={dll.name: hashlib.sha256(dll.read_bytes()).hexdigest()})
            with patch("sys.frozen", True, create=True), patch("runtime.assets", return_value=Path(folder)), patch.dict(sys.modules, {"bundle_info": info}):
                prepare_desktop()
                self.assertFalse(marker.exists())
                self.assertTrue(other_marker.exists())
                dll.write_bytes(b"damaged assembly")
                marker.write_text("[ZoneTransfer]\nZoneId=3\n")
                with self.assertRaisesRegex(RuntimeError, "missing or damaged"):
                    prepare_desktop()
                self.assertTrue(marker.exists())
                dll.unlink()
                with self.assertRaisesRegex(RuntimeError, "missing or damaged"):
                    prepare_desktop()

    @unittest.skipUnless(os.name == "nt", "Windows runtime check")
    def test_missing_webview_gives_a_clear_next_step(self):
        loader = SimpleNamespace(GetAvailableCoreWebView2BrowserVersionString=Mock(return_value=-1))
        with patch("sys.frozen", True, create=True), patch("pathlib.Path.is_file", return_value=True), patch("ctypes.WinDLL", return_value=loader):
            with self.assertRaisesRegex(RuntimeError, "Ask IT to install or repair"):
                check_webview_runtime()

    def test_copied_preferences_recover_unavailable_previous_pc_paths(self):
        from app import Api
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "settings.json").write_text(json.dumps({"root": str(Path(folder, "old-pc-projects")), "python": str(Path(folder, "old-python.exe")), "match": "custom-tsmis"}))
            with patch("app.data_dir", return_value=Path(folder)), patch("app.default_folder", return_value=folder), patch("app.find_arcgis_python", return_value=sys.executable):
                settings = Api().get_initial_state()["settings"]
            self.assertEqual(settings["root"], folder)
            self.assertEqual(settings["python"], sys.executable)
            self.assertEqual(settings["match"], "custom-tsmis")

    def test_reader_boot_failure_is_in_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as folder:
            fake_worker = Path(folder, "worker.py")
            fake_worker.write_text('import sys; raise SystemExit(1)', encoding="utf-8")
            runner = ScanRunner()
            with patch("runtime.assets", return_value=Path(folder)):
                runner.start(sys.executable, {"root": folder, "match": "tsmis", "diagnostics": True})
                self.wait(runner)
            self.assertIn("stopped before finishing", runner.snapshot()["error"])
            output = Path(folder, "diagnostic.zip")
            export_bundle(output, runner.state["result"], True)
            with ZipFile(output) as archive:
                data = json.loads(archive.read("diagnostics.json"))
            self.assertFalse(data["complete"])
            self.assertIn("stopped before finishing", data["error"])
            self.assertEqual(data["python_executable"], sys.executable)

    def test_cancel_retains_completed_projects_and_stops_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "worker.py").write_text('''import sys, json, time
path = sys.argv[sys.argv.index("--events") + 1]
with open(path, "w", encoding="utf-8", buffering=1) as out:
    out.write(json.dumps({"type": "project", "project": {"path": "completed.aprx", "name": "completed.aprx", "rows": [], "errors": []}}) + "\\n")
    out.flush()
    time.sleep(20)
''', encoding="utf-8")
            store = SavedLists(Path(folder, "Lists"))
            stamp = store.save({"root": folder, "complete": True, "projects": []})
            runner = ScanRunner(on_complete=store.save)
            runner.restore(store.load(folder))
            with patch("runtime.assets", return_value=Path(folder)):
                runner.start(sys.executable, {"root": folder, "match": "tsmis"})
                deadline = time.monotonic() + 8
                while runner.snapshot()["completed"] < 1 and time.monotonic() < deadline:
                    time.sleep(0.04)
                runner.stop()
                self.wait(runner)
            self.assertEqual(runner.snapshot()["completed"], 1)
            self.assertTrue(runner.state["result"]["cancelled"])
            self.assertFalse(runner.state["result"]["complete"])
            self.assertEqual(store.load(folder)["refreshed_at"], stamp)
            self.assertEqual(store.load(folder)["result"]["projects"], [])

    def test_api_exposes_only_actions(self):
        # pywebview recursively inspects public attributes. Internal window and
        # runner objects must stay private or bridge initialization can hang.
        from app import Api
        with tempfile.TemporaryDirectory() as folder, patch("app.data_dir", return_value=Path(folder)):
            api = Api()
            public = [name for name in dir(api) if not name.startswith("_")]
            self.assertTrue(all(callable(getattr(api, name)) for name in public))
            self.assertIn("start_scan", public)
            self.assertIn("save_results", public)


if __name__ == "__main__":
    unittest.main()
