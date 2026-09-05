"""Replace only the portable executable/support files; retain Data and other files.

The staged executable runs this helper without WebView2 or any external shell.
It waits for the original process, keeps a rollback copy, and restarts the same
path. The restarted app cleans staging only after its interface is ready.
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from runtime import start_external
from version import APP_NAME

APP_FILES = (APP_NAME + ".exe", "_internal")


def _job(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    root, job = target.parent, source.parent.parent
    updates = root / "Data" / "Updates"
    if (target.name != APP_FILES[0] or source.name != APP_FILES[0]
            or source.parent.name != APP_NAME or source == target
            or not updates.resolve().is_relative_to(root)
            or not job.is_relative_to(updates.resolve())):
        raise ValueError("The update must be staged inside this app's Data/Updates folder.")
    parts = job.relative_to(updates.resolve()).parts
    if len(parts) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+", parts[0]) or not parts[1].startswith("app-"):
        raise ValueError("Unrecognized update staging folder.")
    for name in APP_FILES:
        if not (root / name).resolve().is_relative_to(root) or not (root / name).exists():
            raise ValueError("The original app files are missing or point outside the app folder.")
    return source, target, job


def _manifest(folder):
    result = {}
    for file in Path(folder).rglob("*"):
        if not file.resolve().is_relative_to(Path(folder).resolve()) or file.is_symlink():
            raise ValueError("An update file points outside its package.")
        if file.is_file():
            relative = file.relative_to(folder)
            if relative.parts[0] not in APP_FILES:
                raise ValueError("Unexpected file in the staged update.")
            result[relative.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    if APP_FILES[0] not in result or "_internal/ui/index.html" not in result:
        raise ValueError("The update is incomplete.")
    return result


def write_manifest(exe):
    exe = Path(exe)
    (exe.parent.parent / "manifest.json").write_text(json.dumps(_manifest(exe.parent)), encoding="utf-8")


def _verified(source, job):
    if _manifest(source.parent) != json.loads((job / "manifest.json").read_text(encoding="utf-8")):
        raise ValueError("The downloaded update changed. Download it again.")


class ProcessWait:
    """A Windows process handle avoids guessing when its executable is unlocked."""
    def __init__(self, pid):
        from ctypes import wintypes
        if os.name != "nt" or int(pid) <= 0 or int(pid) == os.getpid():
            raise ValueError("Invalid update process.")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x100000, False, int(pid))  # SYNCHRONIZE
        if not self.handle and ctypes.get_last_error() != 87:  # process already exited
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, milliseconds=60000):
        result = self.kernel.WaitForSingleObject(self.handle, milliseconds) if self.handle else 0
        if result not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _launch(exe, args=()):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return start_external([str(exe), *map(str, args)], cwd=Path(exe).parent, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def begin_install(source, target, parent_pid=None, restart_args=()):
    source, target, job = _job(source, target)
    _verified(source, job)
    for name in ("ready", "error", "started"):
        (job / name).unlink(missing_ok=True)
    process = _launch(source, ["--apply-update", str(target), str(parent_pid or os.getpid()), *restart_args])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if (job / "error").exists():
            raise RuntimeError((job / "error").read_text(encoding="utf-8"))
        if (job / "ready").exists():
            return process
        if process.poll() is not None:
            raise RuntimeError("Windows could not start the updater. The current app is unchanged.")
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError("The updater did not become ready. The current app is unchanged.")


def _move(source, target):
    # Antivirus and an exiting WebView2 process may briefly hold app files.
    for attempt in range(30):
        try:
            Path(source).rename(target)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(0.2)


def replace_files(prepared, root, job, restart):
    """A failed replacement or failed restart restores the previous app files."""
    prepared, root, job = Path(prepared).resolve(), Path(root).resolve(), Path(job).resolve()
    if not job.is_relative_to(root / "Data" / "Updates") or not prepared.is_relative_to(job):
        raise ValueError("Update paths escaped the portable app.")
    previous = job / "previous"
    previous.mkdir()
    old, installed = [], []
    try:
        for name in APP_FILES:
            if not (root / name).resolve().is_relative_to(root):
                raise ValueError("An installed app file points outside the app folder.")
            _move(root / name, previous / name)
            old.append(name)
        for name in APP_FILES:
            _move(prepared / name, root / name)
            installed.append(name)
        restart()
    except Exception as exc:
        failed = job / "failed"
        failed.mkdir(exist_ok=True)
        try:
            for name in reversed(installed):
                _move(root / name, failed / name)
            for name in reversed(old):
                _move(previous / name, root / name)
        except Exception as recovery:
            raise RuntimeError("Update stopped. Previous app files are in " + str(previous) +
                               ". Your saved lists and settings are unchanged.") from recovery
        raise RuntimeError("Update could not finish; the previous app was restored. " + str(exc)) from exc


def apply_update(source, target, parent_pid, restart_args=()):
    source, target, job = _job(source, target)
    parent = ProcessWait(parent_pid)
    closed = False
    try:
        _verified(source, job)
        prepared = job / "prepared"
        if prepared.exists():
            raise ValueError("This update was already attempted. Download it again.")
        shutil.copytree(source.parent, prepared)
        if _manifest(prepared) != _manifest(source.parent):
            raise ValueError("The update could not be prepared completely.")
        (job / "ready").touch()
        if not parent.wait():
            raise RuntimeError("The app is still open. Close it and try updating again.")
        closed = True

        def restart():
            process = _launch(target, ["--updated", str(job), str(os.getpid()), *restart_args])
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if (job / "started").exists():
                        return
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                raise RuntimeError("The updated app did not start successfully.")
            except Exception:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                raise

        try:
            replace_files(prepared, target.parent, job, restart)
        except Exception:
            # Reopen only after a complete rollback, never a partially restored app.
            if not any((job / "previous" / name).exists() for name in APP_FILES):
                _launch(target, restart_args)
            raise
        return 0
    except Exception as exc:
        message = str(exc)
        (job / "error").write_text(message, encoding="utf-8")
        if closed and "--smoke-test" not in restart_args:
            ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
        return 1
    finally:
        parent.close()


def finish_update(target, job, helper_pid):
    target, job = Path(target).resolve(), Path(job).resolve()
    _job(job / APP_NAME / APP_FILES[0], target)
    helper = ProcessWait(helper_pid)
    (job / "started").touch()

    def cleanup():
        try:
            if not helper.wait():
                return
            for attempt in range(30):
                try:
                    # This exact staging folder was validated above; Data is retained.
                    if not job.resolve().is_relative_to(target.parent / "Data" / "Updates"):
                        return
                    shutil.rmtree(job)
                    try:
                        job.parent.rmdir()
                    except OSError:
                        pass
                    return
                except OSError:
                    time.sleep(0.2)
        finally:
            helper.close()

    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()
    return thread
