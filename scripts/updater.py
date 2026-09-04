"""Self-update from GitHub releases (the GUI's one-click update).

Console-free core: raises UpdateError with UI-neutral messages, reports
progress through a callback, and logs every decision to `tsmis.update`.
gui_worker.UpdateWorker drives it from a worker thread.

How an update happens:

  1. check_for_update() asks the GitHub Releases API for the latest tag and
     compares it to version.__version__. TLS uses ssl.create_default_context(),
     which trusts the WINDOWS certificate store — corporate TLS inspection
     keeps working where a bundled CA list would reject the connection, and
     urllib picks up the system proxy. Never switch this to requests/certifi.
  2. download_and_stage() streams the release zip into data\\update\\,
     verifies its SHA-256 against the published <asset>.sha256 (fail-closed:
     no checksum, no install), rejects zip-slip members, extracts to
     data\\update\\staged and records a digest over the whole staged tree.
     Writing the bytes ourselves means no Mark-of-the-Web is ever applied.
  3. apply_update_and_restart() re-verifies the staged tree and launches the
     STAGED NEW EXE in swap mode (--apply-update). Windows locks a running
     exe, so the swap runs from outside: the new app applies itself. It must
     prove it is holding this process's handle (a one-use nonce) before the
     old app closes. Then it waits for the old PID to exit, COPIES every
     bundle piece in as <name>.new (the slow, failure-prone part, with the
     installed app untouched), and finally RENAMES live -> .old, .new -> live
     — instant, and rolled back with renames if anything fails.
     Why an exe and not a script: locked-down work PCs block PowerShell for
     standard users. The one capability this needs — "exes run from
     user-writable folders" — is proven wherever the app itself runs.
  4. cleanup_leftovers() (every GUI launch) removes *.old / *.new pieces and
     stale staging, and drops the WebView2 HTTP cache so the UI on screen is
     always the one on disk.

Only a packaged build whose folder is writable can update itself; a read-only
install gets a link to the release page instead (update_support()).
"""
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from paths import DATA_ROOT, LOG_DIR, UPDATE_DIR, is_frozen
from version import APP_NAME, __version__

log = logging.getLogger("tsmis.update")

GITHUB_REPO = "yunusshaikh7/TSMIS-Branch-Identifier"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
VARIANT = "win64"                      # the one release zip: *-win64.zip
_EXE_NAME = APP_NAME + ".exe"
# The ONLY items a swap installs / a cleanup removes. Everything else next to
# the exe (output\\, data\\) is user data and is never touched.
_BUNDLE_ITEMS = (_EXE_NAME, "_internal", "Start Here.txt")
_CHUNK = 256 * 1024
_API_TIMEOUT_S = 20
_DL_TIMEOUT_S = 60
_DL_RETRY_ATTEMPTS = 3

SWAP_FLAG = "--apply-update"
_SWAP_TIMEOUT_S = 120                 # max wait for the old app's PID to exit
_RETRY_ATTEMPTS = 12                  # Defender / slow handle release after exit
_RETRY_DELAY_S = 0.5
_HELPER_READY_TIMEOUT_S = 15.0
_HELPER_READY_INTERVAL_S = 0.05
_HELPER_LOG_MAX_BYTES = 256 * 1024


class UpdateError(Exception):
    """An update step failed; the message is user-safe and UI-neutral."""


@dataclass
class UpdateInfo:
    version: str
    tag: str
    asset_name: str
    asset_url: str
    asset_size: int
    release_url: str
    asset_digest: str = ""
    asset_sha256_url: str = ""


# ------------------------------------------------------------ environment ---

def install_dir():
    return Path(sys.executable).resolve().parent


def update_support():
    """('ok' | 'link' | 'off', reason) — what this installation can do."""
    if not is_frozen():
        return "off", "not a packaged build"
    if DATA_ROOT != install_dir():
        return "link", "the app folder is not writable (data redirected to %LOCALAPPDATA%)"
    return "ok", None


def safe_release_url(url):
    """Only an https://github.com/<this repo>/… link is ever handed to a
    browser; anything else falls back to the hardcoded releases page."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        if (parts.scheme == "https" and (parts.hostname or "").lower() == "github.com"
                and parts.path.lstrip("/").lower().startswith(GITHUB_REPO.lower() + "/")):
            return url
    except (ValueError, TypeError):
        pass
    return RELEASES_PAGE


def parse_version(text):
    m = re.match(r"v?(\d+(?:\.\d+)*)$", str(text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(remote, current):
    if not remote or not current:
        return False
    n = max(len(remote), len(current))
    return (remote + (0,) * (n - len(remote))) > (current + (0,) * (n - len(current)))


# -------------------------------------------------------------- the check ---

def _http_get(url, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME.replace(' ', '-')}/{__version__} (self-update)",
        "Accept": "application/vnd.github+json",
    })
    # Default context = the Windows certificate store; default opener = the
    # system proxy. Load-bearing on corporate networks.
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def check_for_update(current_version=None):
    """An UpdateInfo when a newer full release exists, else None. Raises
    UpdateError when the check itself cannot be completed."""
    cur = parse_version(current_version or __version__)
    log.info("update check: current v%s -> %s", current_version or __version__, _API_LATEST)
    try:
        with _http_get(_API_LATEST, _API_TIMEOUT_S) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.info("update check: no releases published (HTTP 404)")
            return None
        raise UpdateError(f"the update service answered with an error (HTTP {e.code})") from e
    except (OSError, ValueError) as e:
        log.warning("update check failed: %s: %s", type(e).__name__, e)
        raise UpdateError("could not reach github.com to check for updates — "
                          "check the internet connection") from e
    tag = data.get("tag_name") or ""
    remote = parse_version(tag)
    if remote is None:
        log.warning("update check: unrecognized release tag %r", tag)
        return None
    if not is_newer(remote, cur):
        log.info("update check: up to date (latest release is %s)", tag)
        return None
    info = _asset_info_from_release(data, remote, tag)
    log.info("update available: %s -> %s (%s, %.0f MB)", __version__, info.tag,
             info.asset_name, info.asset_size / 1e6)
    return info


def _asset_info_from_release(release, remote, tag):
    suffix = f"-{VARIANT}.zip"
    assets = release.get("assets") or []
    asset = next((a for a in assets if (a.get("name") or "").endswith(suffix)), None)
    if asset is None:
        raise UpdateError(f"version {tag} is out, but its download package isn't "
                          "available yet — try again later")
    sha_name = (asset.get("name") or "") + ".sha256"
    sha_asset = next((a for a in assets if (a.get("name") or "") == sha_name), None)
    return UpdateInfo(
        version=".".join(str(p) for p in remote), tag=tag,
        asset_name=asset.get("name") or "",
        asset_url=asset.get("browser_download_url") or "",
        asset_size=int(asset.get("size") or 0),
        release_url=release.get("html_url") or RELEASES_PAGE,
        asset_digest=asset.get("digest") or "",
        asset_sha256_url=(sha_asset.get("browser_download_url") if sha_asset else "") or "")


# ------------------------------------------------------- download + stage ---

def _expected_sha256(info):
    """The published SHA-256 for the asset (the companion .sha256 file first,
    then the API's own digest), or None when nothing is published."""
    if info.asset_sha256_url:
        try:
            with _http_get(info.asset_sha256_url, _API_TIMEOUT_S) as resp:
                text = resp.read(4096).decode("utf-8", "replace")
            token = (text.strip().split() or [""])[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", token):
                return token
            log.warning("update: published .sha256 content was unrecognized")
        except (OSError, ValueError) as e:
            log.warning("update: could not fetch the .sha256 file (%s: %s)", type(e).__name__, e)
    digest = (info.asset_digest or "").strip().lower()
    if digest.startswith("sha256:"):
        token = digest.split(":", 1)[1]
        if re.fullmatch(r"[0-9a-f]{64}", token):
            return token
    return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _bundle_digest(staged):
    """One SHA-256 over every file of every bundle item in the staged tree,
    folded in by sorted relative path. None when the tree can't be read."""
    staged = Path(staged)
    entries = []
    try:
        for name in _BUNDLE_ITEMS:
            item = staged / name
            if item.is_dir():
                entries.extend((p.relative_to(staged).as_posix(), p)
                               for p in item.rglob("*") if p.is_file())
            elif item.is_file():
                entries.append((item.relative_to(staged).as_posix(), item))
        entries.sort(key=lambda t: t[0])
        h = hashlib.sha256()
        for rel, p in entries:
            h.update(rel.encode("utf-8") + b"\0" + _sha256_file(p).encode("ascii") + b"\0")
    except OSError:
        return None
    return h.hexdigest()


def _write_staged_record(digest):
    (UPDATE_DIR / "staged.sha256").write_text(digest, encoding="ascii")


def _staged_hash(staged):
    try:
        token = (Path(staged).parent / "staged.sha256").read_text(encoding="ascii").strip().lower()
    except OSError:
        return None
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else None


def _stream_to_file(info, zip_path, on_progress):
    done = 0
    hasher = hashlib.sha256()
    with _http_get(info.asset_url, _DL_TIMEOUT_S) as resp, open(zip_path, "wb") as out:
        total = info.asset_size or int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            out.write(chunk)
            hasher.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    return hasher.hexdigest(), done


def _safe_zip_members(zf, dest):
    """Refuse the whole package if any member would extract outside `dest`."""
    dest_resolved = dest.resolve()
    for name in zf.namelist():
        try:
            (dest / name).resolve().relative_to(dest_resolved)
        except ValueError:
            log.warning("update: refusing package — member escapes the extract dir: %r", name)
            raise UpdateError("the downloaded package contains an unsafe file path "
                              "and was rejected") from None


def download_and_stage(info, on_progress=None):
    """Download the release zip, verify it, extract it to UPDATE_DIR/staged
    (returned). Heavy — run on a worker thread."""
    mode, why = update_support()
    if mode != "ok":
        raise UpdateError(f"this installation cannot update itself ({why})")
    if UPDATE_DIR.exists():
        try:
            shutil.rmtree(UPDATE_DIR)
        except OSError as e:
            raise UpdateError("could not clear the update folder — close any window "
                              "showing it and try again") from e
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    if info.asset_size:
        free = shutil.disk_usage(UPDATE_DIR).free
        need = info.asset_size * 3
        if free < need:
            raise UpdateError(f"not enough free disk space for the update (needs about "
                              f"{need // 1_000_000} MB free, this drive has {free // 1_000_000} MB)")

    zip_path = UPDATE_DIR / (info.asset_name or "update.zip")
    log.info("downloading %s (%d bytes) -> %s", info.asset_url, info.asset_size, zip_path)
    actual = done = None
    last_err = None
    for attempt in range(1, _DL_RETRY_ATTEMPTS + 1):
        try:
            actual, done = _stream_to_file(info, zip_path, on_progress)
            break
        except OSError as e:
            last_err = e
            log.warning("download attempt %d/%d failed: %s: %s", attempt, _DL_RETRY_ATTEMPTS,
                        type(e).__name__, e)
            zip_path.unlink(missing_ok=True)
    if actual is None:
        raise UpdateError("the update download failed — check the internet connection "
                          "and try again") from last_err
    if info.asset_size and done != info.asset_size:
        raise UpdateError(f"the update download was incomplete ({done // 1_000_000} of "
                          f"{info.asset_size // 1_000_000} MB) — try again")

    expected = _expected_sha256(info)
    if not expected:
        zip_path.unlink(missing_ok=True)
        log.warning("update: no published checksum for %s — refusing unverified bytes", info.asset_name)
        raise UpdateError("this update could not be verified (no published checksum), so it "
                          "was not installed — install it manually from the releases page")
    if actual != expected:
        zip_path.unlink(missing_ok=True)
        log.warning("update: SHA-256 mismatch (expected %s, got %s)", expected, actual)
        raise UpdateError("the downloaded update didn't match its published checksum "
                          "(it may be corrupted) — please try again")
    log.info("update: SHA-256 verified (%s)", actual)

    extract_dir = UPDATE_DIR / "extract"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_zip_members(zf, extract_dir)
            zf.extractall(extract_dir)
    except UpdateError:
        zip_path.unlink(missing_ok=True)
        raise
    except (zipfile.BadZipFile, OSError) as e:
        log.warning("extract failed: %s: %s", type(e).__name__, e)
        raise UpdateError("the downloaded file is not a valid app package") from e
    zip_path.unlink(missing_ok=True)

    root = _bundle_root(extract_dir)
    staged = UPDATE_DIR / "staged"
    _retry(lambda: root.rename(staged))         # Defender may hold the fresh tree briefly
    if extract_dir.exists() and root != extract_dir:
        shutil.rmtree(extract_dir, ignore_errors=True)
    if not (staged / _EXE_NAME).is_file() or not (staged / "_internal").is_dir():
        raise UpdateError("the downloaded package is missing expected app files")
    digest = _bundle_digest(staged)
    if digest is None:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)
        raise UpdateError("the downloaded update could not be verified after extraction — "
                          "please try again")
    try:
        _write_staged_record(digest)
    except OSError as e:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)
        raise UpdateError("the downloaded update could not be secured after extraction — "
                          "please try again") from e
    log.info("update %s staged at %s (bundle digest %s)", info.tag, staged, digest[:12])
    return staged


def _bundle_root(extract_dir):
    """Compress-Archive wraps the bundle in one top-level folder; find it."""
    if (extract_dir / _EXE_NAME).is_file():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / _EXE_NAME).is_file():
            return child
    raise UpdateError("the downloaded package does not contain the app")


# ------------------------------------------------------------- the swap -----

def _launch_detached(cmd, cwd, flags):
    return subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=str(cwd),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _write_helper_ready(ready_file, token, state):
    temp = ready_file.with_name(ready_file.name + ".tmp")
    temp.write_text(f"{state}:{token}", encoding="ascii")
    os.replace(temp, ready_file)


def _stop_unready_helper(proc):
    try:
        if proc.poll() is None:
            proc.terminate()
            log.warning("swap process terminated after readiness failed")
    except OSError as e:
        log.warning("swap process could not be terminated: %s: %s", type(e).__name__, e)


def _wait_for_helper_ready(proc, ready_file, token):
    """Block until the staged helper publishes `ready:<token>` — which it does
    only after opening a handle to this still-running process — so a helper
    that never starts, dies, or is blocked by policy leaves the old app open
    instead of stranding the user with no app and no swap."""
    deadline = time.monotonic() + _HELPER_READY_TIMEOUT_S
    confirmed = False
    read_error = None
    try:
        while True:
            try:
                observed = ready_file.read_text(encoding="ascii")
                read_error = None
            except FileNotFoundError:
                observed = ""
            except OSError as e:          # a scanner may hold the marker for a moment
                observed, read_error = "", e
            if secrets.compare_digest(observed, f"ready:{token}"):
                confirmed = True
                return
            if observed and not secrets.compare_digest(observed, f"starting:{token}"):
                raise UpdateError("the update process could not confirm it was ready — "
                                  "install the new version manually from the releases page")
            rc = proc.poll()
            if rc is not None:
                log.warning("swap process exited before readiness (code %s)", rc)
                raise UpdateError("the update process exited before it could start — "
                                  "install the new version manually from the releases page")
            if time.monotonic() >= deadline:
                if read_error is not None:
                    log.warning("swap readiness marker unreadable: %s: %s",
                                type(read_error).__name__, read_error)
                raise UpdateError("the update process did not become ready — install the "
                                  "new version manually from the releases page")
            time.sleep(_HELPER_READY_INTERVAL_S)
    finally:
        try:
            ready_file.unlink(missing_ok=True)
        except OSError:
            pass
        if not confirmed:
            _stop_unready_helper(proc)


def apply_update_and_restart(staged_dir):
    """Launch the staged exe in swap mode. The CALLER then closes the app; the
    swap waits for this PID before touching anything. Raises UpdateError (with
    the app still open on the old version) if the helper can't start."""
    mode, why = update_support()
    if mode != "ok":
        raise UpdateError(f"this installation cannot update itself ({why})")
    staged = Path(staged_dir)
    new_exe = staged / _EXE_NAME
    if not new_exe.is_file():
        raise UpdateError("the downloaded update is no longer on disk — download it again")
    expected = _staged_hash(staged)
    if expected is None:
        raise UpdateError("the staged update could not be verified (its security record is "
                          "missing) and was not applied — download it again")
    actual = _bundle_digest(staged)
    if actual != expected:
        log.warning("update: staged bundle changed since download — refusing to launch it")
        raise UpdateError("the staged update changed on disk and was not applied — "
                          "download it again")
    helper_log = LOG_DIR / "update_helper.log"
    token = secrets.token_hex(24)
    ready_file = staged.parent / f"swap-ready-{os.getpid()}-{token[:12]}.txt"
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = _launch_detached([str(new_exe), SWAP_FLAG, str(install_dir()), str(os.getpid()),
                                 str(helper_log), str(ready_file), token], staged, flags)
    except OSError as e:
        log.warning("swap process failed to start: %s: %s", type(e).__name__, e)
        raise UpdateError("the update process could not be started — install the new "
                          "version manually from the releases page") from e
    _wait_for_helper_ready(proc, ready_file, token)
    log.info("swap process ready: %s (holds pid %d, installs into %s)", new_exe, os.getpid(),
             install_dir())
    return str(new_exe)


def run_swap_mode(argv):
    """`<exe> --apply-update <app_dir> <pid> <log> <ready_file> <token>`.
    Called by gui_main BEFORE logging/paths/CLR setup: this process runs from
    the staged tree, so every path is explicit. Never returns."""
    try:
        i = argv.index(SWAP_FLAG)
        app_dir, pid, log_file = Path(argv[i + 1]), int(argv[i + 2]), Path(argv[i + 3])
        ready_file, token = Path(argv[i + 4]), argv[i + 5]
        if len(token) < 16:
            raise ValueError("bad token")
    except (ValueError, IndexError):
        os._exit(2)
    staged = Path(sys.executable).resolve().parent
    ok = perform_swap(staged, app_dir, pid, log_file, ready_file=ready_file, ready_token=token)
    os._exit(0 if ok else 1)


def _swap_log(log_file, message):
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.is_file() and log_file.stat().st_size >= _HELPER_LOG_MAX_BYTES:
            backup = log_file.with_name(log_file.name + ".1")
            backup.unlink(missing_ok=True)
            log_file.rename(backup)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def _wait_pid_exit(pid, timeout_s, on_waiting=None):
    """True once `pid` has exited; False on timeout. ctypes only. The held
    process handle keeps the PID from being recycled under the wait, and
    `on_waiting` (the readiness marker) fires only after it is held."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))       # SYNCHRONIZE
    if not handle:
        if on_waiting is not None:
            raise OSError("could not open the original app process")
        return True
    try:
        if on_waiting is not None:
            on_waiting()
        return kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) != 0x00000102
    finally:
        kernel32.CloseHandle(handle)


def _retry(fn):
    """Run `fn` with the Defender cadence; the last attempt's error surfaces."""
    for _ in range(_RETRY_ATTEMPTS - 1):
        try:
            return fn()
        except OSError:
            time.sleep(_RETRY_DELAY_S)
    return fn()


def _remove_tree(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def perform_swap(staged, app_dir, pid, log_file, *, relaunch=True,
                 wait_timeout_s=_SWAP_TIMEOUT_S, show_dialog=True,
                 ready_file=None, ready_token=None):
    """The swap itself, in two phases so a mixed-version tree is impossible:
    COPY every staged piece in as <name>.new (installed app untouched), then
    pure RENAMES live -> .old, .new -> live — rolled back with renames on any
    failure. Returns True when the new version is in place."""
    handshake = ready_file is not None and bool(ready_token)
    if handshake:
        ready_file = Path(ready_file)
        try:
            _write_helper_ready(ready_file, ready_token, "starting")
        except OSError as e:
            _swap_log(log_file, f"readiness handshake FAILED: {type(e).__name__}: {e} - update NOT applied")
            return False
    _swap_log(log_file, f"swap started: waiting for the app (pid {pid}) to exit")
    try:
        exited = _wait_pid_exit(
            pid, wait_timeout_s,
            (lambda: _write_helper_ready(ready_file, ready_token, "ready")) if handshake else None)
    except OSError as e:
        _swap_log(log_file, f"readiness handshake FAILED: {type(e).__name__}: {e} - update NOT applied")
        return False
    if not exited:
        _swap_log(log_file, f"app still running after {wait_timeout_s}s - update NOT applied")
        return False
    time.sleep(0.6)
    if not (staged / _EXE_NAME).is_file():
        _swap_log(log_file, f"staged update is incomplete (no {_EXE_NAME}) - update NOT applied")
        return False
    expected = _staged_hash(staged)
    actual = _bundle_digest(staged)
    if expected is None or actual != expected:
        _swap_log(log_file, "staged bundle failed re-verification - update NOT applied")
        return False
    _swap_log(log_file, f"staged bundle re-verified pre-install ({actual[:12]})")

    # Phase 1: copy in as *.new — only the allowlisted bundle items.
    try:
        present = {p.name for p in staged.iterdir()}
    except OSError:
        present = set()
    extra = sorted(present - set(_BUNDLE_ITEMS))
    if extra:
        _swap_log(log_file, f"ignoring unexpected staged item(s): {extra}")
    news = []
    for name in _BUNDLE_ITEMS:
        item = staged / name
        if not item.exists():
            continue
        new = app_dir / (name + ".new")
        try:
            _retry(lambda n=new: _remove_tree(n))
            if item.is_dir():
                shutil.copytree(item, new)
            else:
                shutil.copy2(item, new)
            news.append((name, new))
            _swap_log(log_file, f"prepared: {name}.new")
        except OSError as e:
            _swap_log(log_file, f"swap ABORTED preparing {name}.new: {type(e).__name__}: {e}")
            for _n, n in news:
                try:
                    _remove_tree(n)
                except OSError:
                    pass
            _swap_log(log_file, "nothing was changed - the installed version is untouched; "
                                "update NOT applied")
            if show_dialog:
                _message_box(f"The update could not be prepared, so nothing was changed.\n"
                             f"Details: {log_file}")
            if relaunch:
                _relaunch(app_dir, log_file)
            return False

    # Phase 2: rename-swap each piece; rollback = renames only.
    moved, failed = [], None
    for name, new in news:
        dest, bak = app_dir / name, app_dir / (name + ".old")
        try:
            if bak.exists():
                _retry(lambda b=bak: _remove_tree(b))
            if dest.exists():
                _retry(lambda d=dest, b=bak: d.rename(b))
                moved.append((dest, bak))
            _retry(lambda n=new, d=dest: n.rename(d))
            _swap_log(log_file, f"installed: {name}")
        except OSError as e:
            failed = f"{name}: {type(e).__name__}: {e}"
            _swap_log(log_file, f"swap FAILED on {failed}")
            break
    restored = True
    if failed:
        _swap_log(log_file, "rolling back (renames only)")
        for dest, bak in reversed(moved):
            try:
                if dest.exists():
                    _retry(lambda d=dest: d.rename(d.with_name(d.name + ".new")))
                _retry(lambda b=bak, d=dest: b.rename(d))
            except OSError as e:
                restored = False
                _swap_log(log_file, f"rollback of {dest.name} FAILED: {type(e).__name__}: {e}")
        _swap_log(log_file, "previous version restored" if restored else
                  "previous version PARTIALLY restored - reinstall the app from the releases page")
        if show_dialog:
            _message_box(_rollback_dialog_text(restored, log_file))
    if relaunch and restored:
        _relaunch(app_dir, log_file)
    elif relaunch:
        _swap_log(log_file, "relaunch SKIPPED because rollback left a partial install")
    _swap_log(log_file, "swap done" if failed is None else "swap failed")
    return failed is None


def _relaunch(app_dir, log_file):
    try:
        subprocess.Popen([str(app_dir / _EXE_NAME)], cwd=str(app_dir), close_fds=True,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        _swap_log(log_file, "app relaunched")
    except OSError as e:
        _swap_log(log_file, f"relaunch FAILED: {type(e).__name__}: {e} - start the app manually")


def _rollback_dialog_text(restored, log_file):
    if restored:
        return f"The update could not be applied, so the previous version was kept.\nDetails: {log_file}"
    return ("The update could not be applied, and the previous version was only partially "
            f"restored. Please reinstall the app from the releases page.\nDetails: {log_file}")


def _message_box(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, 0x30)   # MB_ICONWARNING
    except Exception:
        pass


def last_swap_failure(max_age_hours=48):
    """One line describing a RECENT failed swap from update_helper.log, or
    None — so a rolled-back update is announced instead of being a mystery."""
    path = LOG_DIR / "update_helper.log"
    try:
        if not path.is_file() or time.time() - path.stat().st_mtime > max_age_hours * 3600:
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines[-60:]):
        if "swap done" in line:
            return None
        if "swap FAILED" in line or "update NOT applied" in line:
            return line.strip()
    return None


# --------------------------------------------------------------- cleanup ----

def _clear_webview_caches():
    """Drop the WebView2 HTTP caches (never Local Storage — the theme lives
    there) so a just-updated app can't show the OLD interface under the NEW
    version number."""
    try:
        from paths import WEBVIEW_PROFILE_DIR
        profile = Path(WEBVIEW_PROFILE_DIR)
        if not profile.is_dir():
            return
        for pattern in ("Cache", "Code Cache", "GPUCache", "Service Worker",
                        "*/Cache", "*/Code Cache", "*/GPUCache", "*/*/Cache",
                        "*/*/Code Cache", "*/*/GPUCache", "*/*/Service Worker"):
            for hit in profile.glob(pattern):
                if hit.is_dir():
                    shutil.rmtree(hit, ignore_errors=True)
    except Exception as e:                      # noqa: BLE001 — never block startup over a cache
        log.info("webview cache clear skipped (%s)", type(e).__name__)


def cleanup_leftovers():
    """Remove what a finished (or abandoned) update leaves behind. Frozen
    builds only; best-effort; called on every GUI launch before the CLR loads."""
    if not is_frozen():
        return
    _clear_webview_caches()
    targets = [UPDATE_DIR] + [install_dir() / (name + suffix)
                              for name in _BUNDLE_ITEMS for suffix in (".old", ".new")]
    removed, failed = [], []
    for t in targets:
        try:
            if t.is_dir():
                shutil.rmtree(t)
                removed.append(t.name)
            elif t.is_file():
                t.unlink()
                removed.append(t.name)
        except OSError as e:
            failed.append(f"{t.name} ({type(e).__name__})")
    if removed or failed:
        log.info("update cleanup: removed %s%s", ", ".join(removed) or "nothing",
                 ("; could not remove " + ", ".join(failed) + " (will retry next launch)")
                 if failed else "")
