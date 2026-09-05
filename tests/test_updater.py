import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_LZMA, ZipFile

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

    def test_checks_the_renamed_repository_and_package(self):
        tag = "v9.0.0"
        name = updater.release_asset_name(tag)
        self.assertEqual(name, "TSMIS-Project-Inspector-v9.0.0-win64.zip")
        prefix = "https://github.com/yunusshaikh7/TSMIS-Project-Inspector/releases/download/" + tag + "/"
        release = {"tag_name": tag, "assets": [{"name": file, "browser_download_url": prefix + file}
                   for file in (name, name + ".sha256")]}
        with patch.object(updater, "_request", return_value=io.BytesIO(json.dumps(release).encode())) as request:
            response = updater.check_release()
        request.assert_called_once_with("https://api.github.com/repos/yunusshaikh7/TSMIS-Project-Inspector/releases/latest")
        self.assertTrue(response["available"])
        self.assertEqual(response["url"], prefix + name)

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
            with patch.object(updater, "_request", side_effect=lambda url: io.BytesIO(hashlib.sha256(archive).hexdigest().encode() if url == "checksum" else archive)):
                exe = updater.download_release(release, Path(folder, "updates"))
            self.assertFalse((exe.parent / "Data").exists())
            self.assertTrue((exe.parent.parent / "manifest.json").is_file())
            self.assertEqual(exe.read_bytes(), b"test binary")
            self.assertEqual(previous.read_text(), "old")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(updater, "_request", side_effect=lambda url: io.BytesIO(b"0" * 64 if url == "checksum" else archive)):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    updater.download_release(release, Path(folder))
            self.assertEqual(list(Path(folder).rglob("*.exe")), [])


    def test_rejects_windows_device_names_and_normalized_aliases(self):
        for leaf in ("CON", "NUL.txt", "COM1.log", "LPT9", "file. ", "ui/./index.html",
                     "ui//index.html", "ui/index.html.", "bad?.dll", "bad\x01.dll", "ui/INDEX.HTML"):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder, "release.zip")
                archive.write_bytes(self.archive(APP_NAME + "/_internal/" + leaf))
                target = Path(folder, "unpacked")
                with self.assertRaises(ValueError):
                    updater.extract_verified(archive, target)
                self.assertFalse(target.exists())

    def test_unsupported_compression_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder, "release.zip")
            with ZipFile(archive, "w", compression=ZIP_LZMA) as z:
                z.writestr(APP_NAME + "/" + APP_NAME + ".exe", b"test")
            with self.assertRaisesRegex(ValueError, "Unsupported compression"):
                updater.extract_verified(archive, Path(folder, "app"))
            self.assertFalse(Path(folder, "app").exists())


if __name__ == "__main__":
    unittest.main()
