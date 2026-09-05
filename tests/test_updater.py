import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from history import SavedLists
import updater
from version import APP_NAME


class UpdateTests(unittest.TestCase):
    def archive(self, extra=None):
        out = io.BytesIO()
        with ZipFile(out, "w") as archive:
            archive.writestr(APP_NAME + "/" + APP_NAME + ".exe", b"test binary")
            archive.writestr(APP_NAME + "/_internal/ui/index.html", b"test UI")
            if extra:
                archive.writestr(extra, b"unexpected")
        return out.getvalue()

    def test_version_comparison(self):
        self.assertGreater(updater.version_tuple("v0.10.0"), updater.version_tuple("0.2.0"))
        with self.assertRaises(ValueError):
            updater.version_tuple("../escape")

    def test_rejects_traversal_backslashes_drives_and_unrelated_files(self):
        for extra in ("../escape.exe", APP_NAME + "/../escape.exe", "C:/escape.exe", APP_NAME + "/_internal/evil:stream", APP_NAME + "\\evil.exe", APP_NAME + "/run.bat"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder, "release.zip")
                archive.write_bytes(self.archive(extra))
                with self.assertRaises(ValueError):
                    updater.extract_verified(archive, Path(folder, "app"))
                self.assertFalse(Path(folder, "app").exists())

    def test_checksum_before_extraction_and_previous_install_preserved(self):
        archive = self.archive()
        release = {"version": "0.2.0", "checksum_url": "checksum", "url": "archive"}
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder, "old.exe")
            previous.write_text("old")
            lists = SavedLists(Path(folder, "Lists"))
            lists.save({"root": str(Path(folder, "Projects")), "complete": True, "projects": []})
            with patch.object(updater, "_request", side_effect=lambda url: io.BytesIO(hashlib.sha256(archive).hexdigest().encode() if url == "checksum" else archive)):
                exe = updater.download_release(release, Path(folder, "updates"), {"match": "tsmis"}, lists.directory)
            self.assertEqual((exe.parent / "Data" / "settings.json").read_text(), '{\n  "match": "tsmis"\n}')
            self.assertEqual(exe.read_bytes(), b"test binary")
            self.assertEqual(previous.read_text(), "old")
            self.assertEqual(SavedLists(exe.parent / "Data" / "Lists").load(Path(folder, "Projects"))["result"]["projects"], [])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(updater, "_request", side_effect=lambda url: io.BytesIO(b"0" * 64 if url == "checksum" else archive)):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    updater.download_release(release, Path(folder))
            self.assertEqual(list(Path(folder).rglob("*.exe")), [])


if __name__ == "__main__":
    unittest.main()
