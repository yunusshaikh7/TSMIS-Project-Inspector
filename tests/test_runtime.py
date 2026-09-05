import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from core import export_bundle
from runtime import ScanRunner


class RunnerTests(unittest.TestCase):
    def wait(self, runner):
        deadline = time.monotonic() + 12
        while runner.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.04)
        self.assertFalse(runner.snapshot()["running"])

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
            runner = ScanRunner()
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

    def test_api_exposes_only_actions(self):
        # pywebview recursively inspects public attributes. Internal window and
        # runner objects must stay private or bridge initialization can hang.
        from app import Api
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}):
            api = Api()
            public = [name for name in dir(api) if not name.startswith("_")]
            self.assertTrue(all(callable(getattr(api, name)) for name in public))
            self.assertIn("start_scan", public)
            self.assertIn("save_results", public)


if __name__ == "__main__":
    unittest.main()
