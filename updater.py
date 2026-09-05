"""Verified GitHub downloads staged inside the portable app for replacement updates."""
import hashlib
import json
import re
import shutil
import stat
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from version import APP_NAME, REPOSITORY, VERSION


def version_tuple(value):
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("Unrecognized release version.")
    return tuple(map(int, value.lstrip("v").split(".")))


def release_asset_name(version):
    return "TSMIS-Project-Inspector-v" + version.lstrip("v") + "-win64.zip"


def _request(url):
    # urllib uses the Windows trusted certificate stores, including corporate CAs.
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TSMIS-Project-Inspector/" + VERSION}), timeout=30)


def check_release():
    try:
        with _request("https://api.github.com/repos/" + REPOSITORY + "/releases/latest") as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"available": False, "message": "No public release is available yet."}
        raise RuntimeError("GitHub could not be reached. Try again later or check your work network.") from exc
    except OSError as exc:
        raise RuntimeError("Cannot check for updates. Check your internet connection and try again.") from exc
    tag = release.get("tag_name", "")
    if version_tuple(tag) <= version_tuple(VERSION):
        return {"available": False, "message": "You have the latest version (" + VERSION + ")."}
    name = release_asset_name(tag)
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    prefix = "https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/"
    for file in (name, name + ".sha256"):
        if assets.get(file) != prefix + file:
            raise RuntimeError("This release does not have the expected Windows package and checksum.")
    return {"available": True, "version": tag.lstrip("v"), "url": assets[name],
            "checksum_url": assets[name + ".sha256"], "name": name,
            "message": "Version " + tag.lstrip("v") + " is available."}


def extract_verified(archive_path, destination):
    """Reject unsafe archive paths and unrelated payloads before writing files."""
    destination = Path(destination).resolve()
    with ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > 15000 or sum(i.file_size for i in members) > 600 * 1024 * 1024:
            raise ValueError("Update package is unexpectedly large.")
        names = set()
        for info in members:
            name = info.filename
            path = PurePosixPath(name)
            if ("\\" in name or ":" in name or path.is_absolute() or ".." in path.parts
                    or not path.parts or path.parts[0] != APP_NAME
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Unsafe path in update package.")
            if len(path.parts) > 1 and path.parts[1] not in {APP_NAME + ".exe", "_internal"}:
                raise ValueError("Unexpected content in update package.")
            resolved = destination.joinpath(*path.parts).resolve()
            if not resolved.is_relative_to(destination) or name.lower() in names:
                raise ValueError("Invalid or duplicate update path.")
            names.add(name.lower())
        if (APP_NAME + "/" + APP_NAME + ".exe").lower() not in names:
            raise ValueError("The update is missing the application.")
        if not any(n.startswith((APP_NAME + "/_internal/ui/").lower()) for n in names):
            raise ValueError("The update is missing its interface.")
        archive.extractall(destination)
    return destination / APP_NAME / (APP_NAME + ".exe")


def download_release(release, update_root):
    version_tuple(release["version"])
    destination = Path(update_root) / ("v" + release["version"])
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "download.zip"
    with _request(release["checksum_url"]) as response:
        checksum = response.read(512).decode("ascii").strip().split()[0]
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError("The release checksum is invalid.")
    digest = hashlib.sha256()
    size = 0
    try:
        with _request(release["url"]) as response, archive_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    raise ValueError("Update download is unexpectedly large.")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest().lower() != checksum.lower():
            raise ValueError("The update checksum did not match. Please download again.")
        # Stage first; the installer replaces app files only after the window closes.
        import tempfile
        target = Path(tempfile.mkdtemp(prefix="app-", dir=destination))
        try:
            exe = extract_verified(archive_path, target)
            from installer import write_manifest
            write_manifest(exe)
            return exe
        except Exception:
            shutil.rmtree(target)
            raise
    finally:
        archive_path.unlink(missing_ok=True)
