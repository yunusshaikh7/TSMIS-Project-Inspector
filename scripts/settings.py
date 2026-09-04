"""Persisted user settings (config.json under the app's private data dir).

Consumers read through get() at RUN time, so a change applies to the next
scan without a restart. Tolerant by design: a missing or broken config.json
means "defaults" (a corrupt file is moved aside first, never overwritten),
writes go through a temp file + os.replace, and unknown keys survive
round-trips. Stdlib + paths only.
"""
import json
import logging
import os
import tempfile

from paths import CONFIG_FILE

log = logging.getLogger("tsmis.settings")

DEFAULTS = {
    "scan_root": "",                     # "" = paths.default_scan_root()
    "recursive": True,                   # include subfolders
    "include_map_layer_files": False,    # also read .mapx / .lyrx files
    "debug_logging": False,              # verbose (DEBUG) file logging
    "ui_devtools": False,                # open WebView2 DevTools on the next launch
}

_cache = None
_cache_mtime = None


def _read_file():
    """The raw dict from config.json ({} when missing/broken), cached by mtime."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        _cache, _cache_mtime = {}, None
        return _cache
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        log.warning("settings: %s is corrupt (%s); moving it aside", CONFIG_FILE, e)
        bad = CONFIG_FILE.parent / (CONFIG_FILE.name + ".corrupt")
        try:
            os.replace(CONFIG_FILE, bad)
        except OSError as e2:
            log.warning("settings: could not back up the corrupt config (%s: %s)",
                        type(e2).__name__, e2)
        _cache = {}
    except OSError as e:
        log.warning("settings: could not read %s (%s: %s); using defaults",
                    CONFIG_FILE, type(e).__name__, e)
        _cache = {}
    _cache_mtime = mtime
    return _cache


def _atomic_write(data):
    global _cache, _cache_mtime
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, CONFIG_FILE)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache, _cache_mtime = None, None


def get(key):
    """The effective value of one setting. Unknown keys raise KeyError — a
    typo'd key is a bug, not user state."""
    default = DEFAULTS[key]
    raw = _read_file().get(key, default)
    if isinstance(default, bool):
        return bool(raw)
    if isinstance(default, str):
        return raw if isinstance(raw, str) else default
    return raw


def all_settings():
    return {k: get(k) for k in DEFAULTS}


def update(changes):
    """Validate + persist `changes` (known keys only), returning the new
    effective settings."""
    data = dict(_read_file())
    for key, value in (changes or {}).items():
        if key not in DEFAULTS:
            log.info("settings: ignoring unknown key %r", key)
            continue
        default = DEFAULTS[key]
        if isinstance(default, bool):
            data[key] = bool(value)
        elif isinstance(default, str):
            data[key] = str(value or "")
        else:
            data[key] = value
    _atomic_write(data)
    log.info("settings: saved %s -> %s", dict(changes or {}), CONFIG_FILE)
    return all_settings()
