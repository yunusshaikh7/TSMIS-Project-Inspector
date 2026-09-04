"""GUI entry point.

Run in dev:   python scripts\\gui_main.py   (or "run app.bat")
Packaged:     the PyInstaller entry (build\\build.ps1 sets TSMIS_ENTRY here).

The window is a pywebview (Edge WebView2) shell rendering scripts/ui/; the
GUI logic lives in gui_api.py. Order of business here matters and is
explained inline: swap mode first, then logging, then the Mark-of-the-Web
strip (before the .NET CLR loads), then update housekeeping, then the window.
"""
import logging
import os
import sys
from pathlib import Path


def _bootstrap():
    # Dev only: make scripts/ and the repo-root version.py importable
    # regardless of the working directory. Frozen builds bundle these.
    if not getattr(sys, "frozen", False):
        here = Path(__file__).resolve().parent
        sys.path.insert(0, str(here))
        sys.path.insert(0, str(here.parent))


_bootstrap()


def _unblock_dotnet_assemblies():
    """Frozen builds: strip the Mark-of-the-Web from the bundled .NET files.
    A release zip extracted without Unblock tags every file with an NTFS
    Zone.Identifier stream, and the .NET Framework then REFUSES to load those
    assemblies — the window dies at startup. Deleting the stream is exactly
    what right-click → Properties → Unblock does; only the CLR cares."""
    if not getattr(sys, "frozen", False):
        return
    internal = Path(sys.executable).resolve().parent / "_internal"
    removed = errors = 0
    for sub in ("pythonnet", "clr_loader", "webview"):
        root = internal / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                os.remove(f"{p}:Zone.Identifier")
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                errors += 1
    if removed or errors:
        logging.getLogger("tsmis.gui").info(
            "unblocked %d bundled .NET file(s)%s", removed,
            f"; {errors} could not be unblocked" if errors else "")


def _run_self_test():
    """`--self-test`: prove the EXACT shipped exe works before any window. A
    windowed exe has no console, so the output mirrors to the log and — when
    TSMIS_SELFTEST_OUT names a path — to that file. The exit CODE is the gate."""
    log = logging.getLogger("tsmis.selftest")
    lines = []

    def emit(line=""):
        text = str(line)
        lines.append(text)
        if text.strip():
            log.info("%s", text)
        if sys.stderr:
            try:
                print(text, file=sys.stderr)
            except Exception:
                pass

    code = 0
    try:
        import self_test
        code = self_test.run(emit=emit)
    except Exception as e:                        # noqa: BLE001
        emit(f"SELF-TEST FAILED: {type(e).__name__}: {e}")
        log.exception("self-test failed")
        code = 1
    out = os.environ.get("TSMIS_SELFTEST_OUT")
    if out:
        try:
            Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:                         # noqa: BLE001
            log.warning("could not write TSMIS_SELFTEST_OUT=%s", out, exc_info=True)
    return code


def main():
    # Self-update swap mode: the one-click update launches the STAGED new exe
    # with this flag; it swaps itself into the install folder and exits.
    # Branch BEFORE logging/paths setup (this process runs from data\update\
    # staged, so normal path resolution would aim at the wrong tree) and
    # before the CLR loads (no window is ever created here). Never returns.
    import updater
    if updater.SWAP_FLAG in sys.argv:
        updater.run_swap_mode(sys.argv)

    from logging_setup import setup_logging
    # No faulthandler: it intercepts the CLR's routine first-chance access
    # violations (pythonnet/WebView2) and deadlocks the window.
    setup_logging(enable_faulthandler=False, name="gui")
    _unblock_dotnet_assemblies()                  # must run BEFORE the CLR loads
    if "--self-test" in sys.argv:
        raise SystemExit(_run_self_test())
    try:
        updater.cleanup_leftovers()
    except Exception:                             # noqa: BLE001
        logging.getLogger("tsmis.gui").warning("update leftover cleanup failed", exc_info=True)
    try:
        import gui_api
    except ImportError as e:
        msg = (f"The app could not start: a required component is missing ({e.name or e}).\n\n"
               "Run \"setup (one time).bat\" (pip install -r requirements.txt) and try again.")
        if sys.stderr:
            print(msg, file=sys.stderr)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "TSMIS Branch Identifier", 0x10)
        except Exception:
            pass
        raise SystemExit(1)
    gui_api.run()


if __name__ == "__main__":
    main()
