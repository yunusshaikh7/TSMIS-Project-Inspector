"""Application-wide file logging.

One rotating log under LOG_DIR captures diagnostics from every entry point
(the GUI and the console flow). File-only — no stream handler — so it never
interferes with the console output or the windowed GUI. Every line is tagged
with its thread name, uncaught exceptions from any thread land here with a
full traceback (a windowed app has no console for them), and a startup banner
pins down which build ran where.
"""
import faulthandler
import logging
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler

from paths import DATA_ROOT, LOG_DIR, OUTPUT_ROOT, is_frozen

CRASH_FILE = LOG_DIR / "crash.log"
_log_file = None
_configured = False
_crash_file_handle = None


def active_log_file():
    return _log_file if _log_file is not None else LOG_DIR / "tsmis.log"


def _install_excepthooks():
    log = logging.getLogger("tsmis.crash")
    prev_sys_hook = sys.excepthook

    def sys_hook(exc_type, exc, tb):
        log.critical("uncaught exception on the main thread", exc_info=(exc_type, exc, tb))
        prev_sys_hook(exc_type, exc, tb)

    def thread_hook(args):
        log.critical("uncaught exception in thread %r",
                     args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def _log_banner(log):
    try:
        from version import __version__ as app_version
    except Exception:  # silent-ok: the banner still prints, with version 'unknown'
        app_version = "unknown"
    log.info("=== logging started (app v%s) -> %s ===", app_version, active_log_file())
    log.info("env: %s build | python %s | %s", "frozen" if is_frozen() else "dev",
             platform.python_version(), platform.platform())
    log.info("env: exe=%s", sys.executable)
    log.info("env: data_root=%s | output=%s", DATA_ROOT, OUTPUT_ROOT)


def set_debug_logging(on):
    """Switch the root logger between DEBUG and INFO live."""
    logging.getLogger().setLevel(logging.DEBUG if on else logging.INFO)
    logging.getLogger("tsmis").info("file logging level set to %s", "DEBUG" if on else "INFO")


def setup_logging(level=logging.INFO, enable_faulthandler=True, name=""):
    """Configure the root logger's rotating file handler once; returns the log
    file. `name` picks a per-entry-point file (tsmis-<name>.log) so the GUI and
    the console flow never share one rotating file.

    enable_faulthandler=False is for the GUI process ONLY: faulthandler's
    Windows handler intercepts the .NET CLR's routine first-chance access
    violations (pythonnet, which pywebview's WebView2 backend runs on) and
    deadlocks the window. Console entry points keep the crash dumps."""
    global _configured, _log_file
    if _configured:
        return active_log_file()
    try:
        import settings
        if settings.get("debug_logging"):
            level = logging.DEBUG
    except Exception:  # silent-ok: settings must never block logging setup
        pass
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_file = LOG_DIR / (f"tsmis-{name}.log" if name else "tsmis.log")
    handler = RotatingFileHandler(_log_file, maxBytes=2_000_000, backupCount=5,
                                  encoding="utf-8")

    # Show the main thread as [main] in the LOG RECORD only. Never rename the
    # actual thread: pywebview detects "is start() already running" by the
    # main thread's NAME and would block forever in create_window.
    class _MainThreadTag(logging.Filter):
        def filter(self, record):
            if record.threadName == "MainThread":
                record.threadName = "main"
            return True

    handler.addFilter(_MainThreadTag())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _configured = True
    _log_banner(logging.getLogger("tsmis"))
    _install_excepthooks()
    if enable_faulthandler:
        global _crash_file_handle
        try:
            _crash_file_handle = open(CRASH_FILE, "a", encoding="utf-8")
            faulthandler.enable(file=_crash_file_handle, all_threads=True)
        except Exception as e:
            logging.getLogger("tsmis").info("faulthandler disabled (%s: %s)",
                                            type(e).__name__, e)
    else:
        logging.getLogger("tsmis").info(
            "faulthandler disabled in this process (incompatible with the CLR)")
    return _log_file
