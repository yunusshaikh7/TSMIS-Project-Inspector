"""Updater regression checks: pure Python, no network, no real swap.

    python build\\check_updater.py
"""
import hashlib
import io
import sys
import zipfile
from pathlib import Path

from _checklib import Checker, patch, scripts_path, temp_dir

scripts_path()

import updater  # noqa: E402

c = Checker()


class _StreamResp:
    def __init__(self, data):
        self._data, self._pos = data, 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        chunk = self._data[self._pos:] if n < 0 else self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeProc:
    def __init__(self, poll_seq):
        self._seq = list(poll_seq) or [None]
        self._i = 0
        self.terminated = False

    def poll(self):
        v = self._seq[self._i] if self._i < len(self._seq) else self._seq[-1]
        self._i += 1
        return v

    def terminate(self):
        self.terminated = True


def _bundle_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"TSMIS Branch Identifier/{updater._EXE_NAME}", b"NEW-EXE")
        zf.writestr("TSMIS Branch Identifier/_internal/app.dll", b"NEW-DLL")
        zf.writestr("TSMIS Branch Identifier/Start Here.txt", b"hi")
    return buf.getvalue()


def _make_tree(base, exe, dll, extras=None):
    base.mkdir(parents=True, exist_ok=True)
    (base / updater._EXE_NAME).write_bytes(exe)
    (base / "_internal").mkdir(exist_ok=True)
    (base / "_internal" / "app.dll").write_bytes(dll)
    (base / "Start Here.txt").write_text("readme-" + exe.decode(), encoding="utf-8")
    for name, data in (extras or {}).items():
        (base / name).write_bytes(data)


def _bless(staged):
    (staged.parent / "staged.sha256").write_text(updater._bundle_digest(staged), encoding="ascii")


def _info(digest=""):
    return updater.UpdateInfo("1", "v1", "pkg.zip", "http://x/pkg.zip", 0, "", asset_digest=digest)


def test_basics():
    print("versions / urls / checksum parsing:")
    c.check("parse v0.10.4", updater.parse_version("v0.10.4") == (0, 10, 4))
    c.check("0.10.10 newer than 0.10.4 (numeric)", updater.is_newer((0, 10, 10), (0, 10, 4)))
    c.check("equal is not newer", not updater.is_newer((0, 1, 0), (0, 1, 0)))
    repo, page = updater.GITHUB_REPO, updater.RELEASES_PAGE
    good = f"https://github.com/{repo}/releases/tag/v0.1.1"
    c.check("own release URL passes", updater.safe_release_url(good) == good)
    for bad in ("", None, "https://github.com/evil/repo/x", f"https://github.com.evil.test/{repo}/x",
                f"https://github.com@evil.test/{repo}/x", f"http://github.com/{repo}/x",
                "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)"):
        c.check(f"rejected -> releases page: {bad!r}", updater.safe_release_url(bad) == page)
    h = "a" * 64
    c.check("API digest parsed + lowercased", updater._expected_sha256(_info("sha256:" + h.upper())) == h)
    c.check("bad digest -> None", updater._expected_sha256(_info("sha256:nothex")) is None)
    c.check("nothing published -> None", updater._expected_sha256(_info()) is None)
    rel = {"tag_name": "v0.2.0", "html_url": f"https://github.com/{repo}/releases/tag/v0.2.0",
           "assets": [{"name": "TSMIS-Branch-Identifier-v0.2.0-win64.zip", "browser_download_url": "http://x/z",
                       "size": 10, "digest": ""},
                      {"name": "TSMIS-Branch-Identifier-v0.2.0-win64.zip.sha256", "browser_download_url": "http://x/s"}]}
    info = updater._asset_info_from_release(rel, (0, 2, 0), "v0.2.0")
    c.check("asset + companion .sha256 resolved", info.asset_url == "http://x/z" and info.asset_sha256_url == "http://x/s")


def test_download_and_stage():
    print("download_and_stage:")
    zip_bytes = _bundle_zip()
    good = hashlib.sha256(zip_bytes).hexdigest()
    with temp_dir("tsmis_dl_") as tmp, patch(updater, "update_support", lambda: ("ok", None)), \
            patch(updater, "UPDATE_DIR", tmp / "update"):
        with patch(updater, "_http_get", lambda url, timeout: _StreamResp(zip_bytes)):
            err = None
            try:
                updater.download_and_stage(_info("sha256:" + "0" * 64))
            except updater.UpdateError as e:
                err = str(e)
            c.check("checksum mismatch refused + zip deleted + nothing staged",
                    err and "checksum" in err and not (tmp / "update" / "pkg.zip").exists()
                    and not (tmp / "update" / "staged").exists(), err)
            err = None
            try:
                updater.download_and_stage(_info())
            except updater.UpdateError as e:
                err = str(e)
            c.check("no published checksum -> refused (fail-closed)", err and "verified" in err, err)
            staged = updater.download_and_stage(_info("sha256:" + good))
            c.check("valid package staged with exe + _internal",
                    (staged / updater._EXE_NAME).read_bytes() == b"NEW-EXE" and (staged / "_internal").is_dir())
            rec = (tmp / "update" / "staged.sha256").read_text(encoding="ascii")
            c.check("trust digest recorded (64 hex) and covers _internal",
                    len(rec) == 64 and rec == updater._bundle_digest(staged))
            (staged / "_internal" / "app.dll").write_bytes(b"X")
            c.check("digest changes when _internal changes", updater._bundle_digest(staged) != rec)
        # zip-slip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"TSMIS Branch Identifier/{updater._EXE_NAME}", b"NEW-EXE")
            zf.writestr("../evil.txt", b"PWN")
        slip = buf.getvalue()
        with patch(updater, "_http_get", lambda url, timeout: _StreamResp(slip)):
            err = None
            try:
                updater.download_and_stage(_info("sha256:" + hashlib.sha256(slip).hexdigest()))
            except updater.UpdateError as e:
                err = str(e)
            c.check("zip-slip member refused, nothing escaped",
                    err and "unsafe" in err and not (tmp / "evil.txt").exists(), err)
        # transient network failure retried
        state = {"n": 0}

        def flaky(url, timeout):
            state["n"] += 1
            if state["n"] < 3:
                raise OSError("connection reset")
            return _StreamResp(zip_bytes)
        with patch(updater, "_http_get", flaky):
            staged = updater.download_and_stage(_info("sha256:" + good))
            c.check("transient download failures retried (3 attempts)", state["n"] == 3 and staged.is_dir())


def test_apply_reverify():
    print("apply_update_and_restart re-verifies + readiness handshake:")
    with temp_dir("tsmis_apply_") as tmp, patch(updater, "update_support", lambda: ("ok", None)), \
            patch(updater, "install_dir", lambda: tmp / "app"), patch(updater, "LOG_DIR", tmp), \
            patch(updater, "_HELPER_READY_TIMEOUT_S", 0.3), patch(updater, "_HELPER_READY_INTERVAL_S", 0.002):
        (tmp / "app").mkdir()
        update_dir = tmp / "update"
        staged = update_dir / "staged"
        _make_tree(staged, b"STAGED-EXE", b"DLL")
        _bless(staged)
        launches = {"n": 0}

        def ready_launch(cmd, cwd, flags):
            launches["n"] += 1
            Path(cmd[5]).write_text(f"ready:{cmd[6]}", encoding="ascii")
            return _FakeProc([None])
        with patch(updater, "_launch_detached", ready_launch):
            res = updater.apply_update_and_restart(staged)
            c.check("matching bundle launches the helper", res.endswith(updater._EXE_NAME) and launches["n"] == 1)
            (staged / "_internal" / "app.dll").write_bytes(b"PWNED")
            err = None
            try:
                updater.apply_update_and_restart(staged)
            except updater.UpdateError as e:
                err = str(e)
            c.check("tampered _internal refused, no launch", err and "changed" in err and launches["n"] == 1)
            (staged / "_internal" / "app.dll").write_bytes(b"DLL")
            (update_dir / "staged.sha256").unlink()
            err = None
            try:
                updater.apply_update_and_restart(staged)
            except updater.UpdateError as e:
                err = str(e)
            c.check("missing trust record fails closed", err and "security record" in err and launches["n"] == 1)
            _bless(staged)
        dead = []

        def dying_launch(cmd, cwd, flags):
            p = _FakeProc([None, None, 9])
            dead.append(p)
            return p
        with patch(updater, "_launch_detached", dying_launch):
            err = None
            try:
                updater.apply_update_and_restart(staged)
            except updater.UpdateError as e:
                err = str(e)
            c.check("a helper that dies before readiness is caught", err and "exited before" in err, err)
        silent = []

        def silent_launch(cmd, cwd, flags):
            p = _FakeProc([None])
            silent.append(p)
            return p
        with patch(updater, "_launch_detached", silent_launch):
            err = None
            try:
                updater.apply_update_and_restart(staged)
            except updater.UpdateError as e:
                err = str(e)
            c.check("a helper that never reports ready times out and is terminated",
                    err and "did not become ready" in err and silent[0].terminated, err)


def test_swap():
    print("perform_swap (fake trees, pid wait stubbed):")
    with temp_dir("tsmis_swap_") as tmp:
        app, staged, log = tmp / "app", tmp / "staged", tmp / "swap.log"
        _make_tree(app, b"OLD-EXE", b"OLD-DLL")
        (app / "data").mkdir()
        (app / "data" / "config.json").write_text("{}", encoding="utf-8")
        _make_tree(staged, b"NEW-EXE", b"NEW-DLL", extras={"evil.dll": b"PWN"})
        _bless(staged)
        observed = []

        def wait(pid, timeout_s, on_waiting=None):
            observed.append(on_waiting is not None)
            if on_waiting:
                on_waiting()
            return True
        with patch(updater, "_wait_pid_exit", wait):
            ready = tmp / "ready.txt"
            ok = updater.perform_swap(staged, app, pid=1, log_file=log, relaunch=False, show_dialog=False,
                                      ready_file=ready, ready_token="one-use-token-0123456789")
        c.check("swap succeeded", ok)
        c.check("readiness published after the pid handle was taken",
                observed == [True] and ready.read_text(encoding="ascii") == "ready:one-use-token-0123456789")
        c.check("exe + _internal + readme replaced", (app / updater._EXE_NAME).read_bytes() == b"NEW-EXE"
                and (app / "_internal" / "app.dll").read_bytes() == b"NEW-DLL"
                and (app / "Start Here.txt").read_text(encoding="utf-8") == "readme-NEW-EXE")
        c.check("unexpected staged item NOT installed", not (app / "evil.dll").exists())
        c.check("user data untouched", (app / "data" / "config.json").read_text(encoding="utf-8") == "{}")
        c.check("swap log says done", "swap done" in log.read_text(encoding="utf-8"))

        # a still-running app: no swap
        app2, staged2 = tmp / "app2", tmp / "staged2"
        _make_tree(app2, b"OLD-EXE", b"OLD-DLL")
        _make_tree(staged2, b"NEW-EXE", b"NEW-DLL")
        _bless(staged2)
        with patch(updater, "_wait_pid_exit", lambda pid, t, on_waiting=None: False):
            ok = updater.perform_swap(staged2, app2, pid=1, log_file=tmp / "s2.log", relaunch=False,
                                      show_dialog=False, wait_timeout_s=0)
        c.check("app still running -> nothing touched", ok is False
                and (app2 / updater._EXE_NAME).read_bytes() == b"OLD-EXE")

        # an incomplete staged tree
        app3, staged3 = tmp / "app3", tmp / "staged3"
        _make_tree(app3, b"OLD-EXE", b"OLD-DLL")
        staged3.mkdir()
        (staged3 / "_internal").mkdir()
        _bless(staged3)
        with patch(updater, "_wait_pid_exit", lambda pid, t, on_waiting=None: True):
            ok = updater.perform_swap(staged3, app3, pid=1, log_file=tmp / "s3.log", relaunch=False, show_dialog=False)
        c.check("missing staged exe aborts, old exe intact", ok is False
                and (app3 / updater._EXE_NAME).read_bytes() == b"OLD-EXE")

        # phase-2 failure rolls back with renames and reports the right wording
        app4, staged4 = tmp / "app4", tmp / "staged4"
        _make_tree(app4, b"OLD-EXE", b"OLD-DLL")
        _make_tree(staged4, b"NEW-EXE", b"NEW-DLL")
        _bless(staged4)
        msgs, state = [], {"installed": 0, "raised": False}
        orig_log, orig_retry = updater._swap_log, updater._retry

        def spy_log(log_file, message):
            if message.startswith("installed:"):
                state["installed"] += 1
            orig_log(log_file, message)

        def boom_retry(fn):
            if state["installed"] >= 1 and not state["raised"]:
                state["raised"] = True
                raise OSError(5, "Access is denied")
            return orig_retry(fn)
        with patch(updater, "_wait_pid_exit", lambda pid, t, on_waiting=None: True), \
                patch(updater, "_swap_log", spy_log), patch(updater, "_retry", boom_retry), \
                patch(updater, "_message_box", lambda text: msgs.append(text)), \
                patch(updater, "_relaunch", lambda a, l: msgs.append("RELAUNCH")):
            ok = updater.perform_swap(staged4, app4, pid=1, log_file=tmp / "s4.log", relaunch=True, show_dialog=True)
        c.check("phase-2 failure -> rolled back, old version intact",
                ok is False and (app4 / updater._EXE_NAME).read_bytes() == b"OLD-EXE"
                and (app4 / "_internal" / "app.dll").read_bytes() == b"OLD-DLL")
        c.check("one dialog with the full-restore wording + relaunch of the restored app",
                len([m for m in msgs if m != "RELAUNCH"]) == 1 and "was kept" in msgs[0] and "RELAUNCH" in msgs)
        c.check("last_swap_failure reads the failure back",
                "swap FAILED" in ((tmp / "s4.log").read_text(encoding="utf-8")))

    print("_retry + cleanup:")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(5, "denied")
        return "ok"
    with patch(updater, "_RETRY_DELAY_S", 0):
        c.check("_retry recovers a transient OSError", updater._retry(flaky) == "ok" and calls["n"] == 3)
    with temp_dir("tsmis_clean_") as tmp, patch(updater, "install_dir", lambda: tmp), \
            patch(updater, "UPDATE_DIR", tmp / "update"), patch(updater, "_clear_webview_caches", lambda: None):
        (tmp / "update").mkdir()
        (tmp / (updater._EXE_NAME + ".old")).write_bytes(b"x")
        (tmp / "_internal.new").mkdir()
        (tmp / "data").mkdir()
        with patch(updater, "is_frozen", lambda: False):
            updater.cleanup_leftovers()
            c.check("dev launch leaves everything alone", (tmp / "update").exists())
        with patch(updater, "is_frozen", lambda: True):
            updater.cleanup_leftovers()
            c.check("frozen launch removes staging + .old/.new, keeps data",
                    not (tmp / "update").exists() and not (tmp / (updater._EXE_NAME + ".old")).exists()
                    and not (tmp / "_internal.new").exists() and (tmp / "data").is_dir())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    test_basics()
    test_download_and_stage()
    test_apply_reverify()
    test_swap()
    raise SystemExit(c.summary())
