import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Api
from core import interpret, summarize
from history import SavedLists, folder_key
from runtime import ScanRunner


def result(root, name="Roads.aprx"):
    row = interpret({"connection_info": {"url": "https://gis-prod.example.org/server/rest/services/TSMIS/lrs_tsmis_prod/FeatureServer", "version": "editor.Work"}}, {})[0]
    project = summarize({"path": str(Path(root, name)), "name": name, "rows": [row], "errors": []})
    return {"root": str(root), "complete": True, "projects": [project], "recursive": True, "match": "tsmis", "warnings": []}


class SavedListTests(unittest.TestCase):
    def wait(self, runner):
        deadline = time.monotonic() + 15
        while runner.snapshot()["running"] and time.monotonic() < deadline:
            time.sleep(0.04)
        self.assertFalse(runner.snapshot()["running"])

    def test_two_folders_restore_their_own_details_and_time(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = SavedLists(root / "Data" / "Lists")
            first, second = root / "A", root / "B"
            stamp = store.save(result(first, "First.aprx"))
            store.save(result(second, "Second.aprx"))
            reopened = SavedLists(store.directory)
            record = reopened.load(str(first) + os.sep)
            self.assertEqual(record["refreshed_at"], stamp)
            self.assertEqual(record["result"]["projects"][0]["name"], "First.aprx")
            self.assertEqual(record["result"]["projects"][0]["rows"][0]["service"], "lrs_tsmis_prod")
            self.assertEqual(reopened.load(second)["result"]["projects"][0]["name"], "Second.aprx")
            self.assertEqual(len(reopened.paths()), 2)
            self.assertIsNone(reopened.load(root / "Unscanned"))
            if os.name == "nt":
                self.assertEqual(folder_key(str(first).upper()), folder_key(str(first).replace(os.sep, "/")))

    def test_api_reopens_offline_list_and_clear_only_removes_selected_path(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder, "Data"); data.mkdir()
            first, second = Path(folder, "Offline A"), Path(folder, "Offline B")
            store = SavedLists(data / "Lists")
            store.save(result(first)); store.save(result(second, "Other.aprx"))
            (data / "settings.json").write_text(json.dumps({"root": str(first)}))
            with patch("app.data_dir", return_value=data):
                api = Api()
                self.assertEqual(api.get_initial_state()["settings"]["root"], str(first))
                self.assertTrue(api.get_scan_state()["has_result"])
                self.assertEqual(api.get_project(0)["name"], "Roads.aprx")
                self.assertTrue(api.select_path(str(second))["ok"])
                self.assertEqual(api.get_project(0)["name"], "Other.aprx")
                api.select_path(str(first))
                self.assertFalse(api.clear_list(str(second))["ok"])
                self.assertTrue(api.clear_list(str(first))["ok"])
                self.assertFalse(api.get_scan_state()["has_result"])
                self.assertIsNone(store.load(first))
                self.assertIsNotNone(store.load(second))
                api.select_path(str(second))
                reopened = Api()
                self.assertEqual(reopened.get_project(0)["name"], "Other.aprx")

    def test_completed_empty_scan_saves_without_ui_polling(self):
        with tempfile.TemporaryDirectory() as folder:
            scan = Path(folder, "Projects"); scan.mkdir()
            data = Path(folder, "Data"); data.mkdir()
            with patch("app.data_dir", return_value=data), patch("runtime.data_dir", return_value=data):
                api = Api()
                response = api.start_scan({"root": str(scan), "python": sys.executable, "recursive": True, "match": "tsmis"})
                self.assertTrue(response["ok"])
                self.wait(api._runner)
                self.assertTrue(api.get_scan_state()["complete"])
                self.assertTrue(api.get_scan_state()["last_refreshed"])
                saved = SavedLists(data / "Lists").load(scan)
                self.assertEqual(saved["result"]["projects"], [])
                self.assertTrue(Api().get_scan_state()["has_result"])

    def test_failed_refresh_preserves_last_saved_list_and_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SavedLists(Path(folder, "Lists"))
            previous_time = store.save(result(folder))
            runner = ScanRunner(on_complete=store.save)
            runner.restore(store.load(folder))
            Path(folder, "worker.py").write_text("raise SystemExit(1)")
            with patch("runtime.assets", return_value=Path(folder)), patch("runtime.data_dir", return_value=Path(folder)):
                runner.start(sys.executable, {"root": folder, "match": "tsmis"})
                self.wait(runner)
            self.assertFalse(runner.snapshot()["complete"])
            self.assertEqual(runner.snapshot()["last_refreshed"], previous_time)
            self.assertEqual(store.load(folder)["refreshed_at"], previous_time)
            self.assertEqual(store.load(folder)["result"]["projects"][0]["name"], "Roads.aprx")

    def test_failed_atomic_write_keeps_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SavedLists(Path(folder, "Lists"))
            store.save(result(folder, "Original.aprx"))
            with patch("pathlib.Path.replace", side_effect=PermissionError("read-only")):
                with self.assertRaises(PermissionError):
                    store.save(result(folder, "Replacement.aprx"))
            self.assertEqual(store.load(folder)["result"]["projects"][0]["name"], "Original.aprx")
            self.assertFalse(list(store.directory.glob("*.tmp")))
            with self.assertRaises(ValueError):
                store.save(dict(result(folder), complete=False))

    def test_damaged_list_does_not_hide_other_saved_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder, "Data"); data.mkdir()
            store = SavedLists(data / "Lists")
            first, second = Path(folder, "A"), Path(folder, "B")
            store.save(result(first)); store.save(result(second))
            (store.directory / (folder_key(first) + ".json")).write_text("invalid json")
            with patch("app.data_dir", return_value=data):
                response = Api().select_path(str(first))
                self.assertTrue(response["ok"])
                self.assertIn("could not be read", response["warning"])
                self.assertFalse(response["state"]["has_result"])
                self.assertEqual([p["root"] for p in response["saved_paths"]], [str(second)])

    def test_full_service_name_is_separate_from_server_folder(self):
        project = result("C:/Projects")["projects"][0]
        self.assertEqual(project["folders"], ["TSMIS"])
        self.assertEqual(project["services"], ["lrs_tsmis_prod"])


if __name__ == "__main__":
    unittest.main()
