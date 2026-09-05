import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer
from version import APP_NAME


class InstallerTests(unittest.TestCase):
    def make(self, folder):
        root = Path(folder).resolve() / "My portable app"
        job = root / "Data" / "Updates" / "v0.4.0" / "app-test"
        source = job / APP_NAME / (APP_NAME + ".exe")
        target = root / source.name
        for file, content in ((target, "old"), (root / "_internal" / "ui" / "index.html", "old UI"),
                              (root / "_internal" / "obsolete.dll", "old dependency"),
                              (root / "Data" / "settings.json", '{"match":"tsmis"}'),
                              (root / "Data" / "Lists" / "saved.json", "saved list"),
                              (root / "notes.txt", "user file"),
                              (source, "new"), (source.parent / "_internal" / "ui" / "index.html", "new UI")):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content)
        installer.write_manifest(source)
        prepared = job / "prepared"
        shutil.copytree(source.parent, prepared)
        return root, job, source, target, prepared

    def assert_data(self, root):
        self.assertEqual((root / "Data" / "settings.json").read_text(), '{"match":"tsmis"}')
        self.assertEqual((root / "Data" / "Lists" / "saved.json").read_text(), "saved list")
        self.assertEqual((root / "notes.txt").read_text(), "user file")

    def test_replacement_removes_stale_runtime_and_preserves_saved_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            def restart():
                self.assertEqual(target.read_text(), "new")
                self.assertEqual((root / "_internal" / "ui" / "index.html").read_text(), "new UI")
                self.assertFalse((root / "_internal" / "obsolete.dll").exists())
                self.assert_data(root)
            installer.replace_files(prepared, root, job, restart)
            self.assertEqual((job / "previous" / target.name).read_text(), "old")
            self.assert_data(root)

    def test_failed_restart_restores_previous_executable_and_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            def restart():
                raise RuntimeError("new app did not start")
            with self.assertRaisesRegex(RuntimeError, "previous app was restored"):
                installer.replace_files(prepared, root, job, restart)
            self.assertEqual(target.read_text(), "old")
            self.assertEqual((root / "_internal" / "obsolete.dll").read_text(), "old dependency")
            self.assert_data(root)

    def test_file_lock_halfway_through_swap_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            move = installer._move
            def locked(a, b):
                if Path(a) == root / "_internal":
                    raise PermissionError("file in use")
                move(a, b)
            with patch.object(installer, "_move", side_effect=locked):
                with self.assertRaisesRegex(RuntimeError, "previous app was restored"):
                    installer.replace_files(prepared, root, job, lambda: self.fail("must not restart"))
            self.assertEqual(target.read_text(), "old")
            self.assert_data(root)

    def test_rejects_installation_outside_original_app_and_changed_download(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            self.assertEqual(installer._job(source, target)[2], job)
            with self.assertRaises(ValueError):
                installer._job(source, Path(folder) / target.name)
            source.write_text("changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                installer._verified(source, job)
            self.assertEqual(target.read_text(), "old")

    def test_cleanup_waits_for_helper_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            with patch.object(installer, "ProcessWait") as waiting:
                waiting.return_value.wait.return_value = True
                thread = installer.finish_update(target, job, 12345)
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                waiting.return_value.wait.assert_called_once()
                waiting.return_value.close.assert_called_once()
            self.assertFalse(job.exists())
            self.assertTrue(target.is_file())
            self.assert_data(root)

    def test_open_helper_does_not_cleanup_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job, source, target, prepared = self.make(folder)
            with patch.object(installer, "ProcessWait") as waiting:
                waiting.return_value.wait.return_value = False
                installer.finish_update(target, job, 12345).join(timeout=5)
            self.assertTrue(job.exists())
            self.assert_data(root)


if __name__ == "__main__":
    unittest.main()
