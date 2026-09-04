"""Frozen-aware filesystem paths.

One place that decides WHERE the app reads and writes, so the rest of the code
never cares whether it runs as a dev script or as the packaged portable .exe.

  * Packaged build (sys.frozen): write next to the .exe. If that folder is not
    writable (unzipped into Program Files, a read-only share), fall back to
    %LOCALAPPDATA%\\<app name> so the app still runs. The updater treats that
    fallback as a read-only install (see updater.update_support).
  * Dev run (not frozen): the repo root.
"""
import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path

from version import APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _writable(directory: Path) -> bool:
    """True if we can create a file in `directory` (creating it if needed)."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _localappdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def _resolve_data_root() -> Path:
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        if _writable(exe_dir):
            return exe_dir
        fallback = _localappdata_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return Path(__file__).resolve().parent.parent


DATA_ROOT = _resolve_data_root()
OUTPUT_ROOT = DATA_ROOT / "output"

# App-private data (logs, settings, update staging, the WebView2 profile).
_PRIVATE = DATA_ROOT / "data" if is_frozen() else DATA_ROOT
LOG_DIR = _PRIVATE / "logs"
CONFIG_FILE = _PRIVATE / "config.json"
UPDATE_DIR = _PRIVATE / "update"
WEBVIEW_PROFILE_DIR = _PRIVATE / "webview2"


def new_run_dir(now=None):
    """output/<YYYY-MM-DD HH-MM-SS>/ for one scan (not created here)."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H-%M-%S")
    return OUTPUT_ROOT / stamp


def documents_folder() -> Path:
    """The user's Documents folder as Windows knows it — including a OneDrive
    Known-Folder-Move redirection, which is exactly the work-PC layout
    (…\\OneDrive - <org>\\Documents). Falls back to ~/Documents."""
    try:
        from ctypes import wintypes
        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                   (wintypes.BYTE * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        shell = ctypes.windll.shell32
        if shell.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
            try:
                return Path(out.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:  # silent-ok: any failure means "use the plain default"
        pass
    return Path.home() / "Documents"


def default_scan_root() -> Path:
    """Where a first scan looks: ArcGIS Pro's default project folder under
    Documents when it exists, else Documents itself."""
    docs = documents_folder()
    projects = docs / "ArcGIS" / "Projects"
    return projects if projects.is_dir() else docs
