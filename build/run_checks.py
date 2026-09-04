"""Run the FULL offline check suite with one command.

Globs every build/check_*.py and runs each with the SAME interpreter it was
invoked with (so the build venv is used when invoked as
`build\\.venv\\Scripts\\python.exe build\\run_checks.py`). checks.yml and
release.yml call this, so the check list lives in exactly one place: the glob.

    python build\\run_checks.py           # stop on first failure
    python build\\run_checks.py -k        # keep going, summarize
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent
ROOT = BUILD_DIR.parent


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--keep-going", action="store_true")
    ap.add_argument("--only", metavar="SUBSTR")
    args = ap.parse_args(argv)

    checks = [("compileall", [sys.executable, "-m", "compileall", "-q",
                              str(ROOT / "scripts"), str(ROOT / "build"), str(ROOT / "version.py")])]
    checks += [(p.stem, [sys.executable, str(p)]) for p in sorted(BUILD_DIR.glob("check_*.py"))]
    if args.only:
        checks = [c for c in checks if args.only in c[0]]
    failed, passed = [], 0
    t0 = time.monotonic()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for name, cmd in checks:
        t1 = time.monotonic()
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        ok = proc.returncode == 0
        print(f"[{'ok' if ok else 'FAIL':>4}] {name}  ({time.monotonic() - t1:.1f}s)")
        if ok:
            passed += 1
        else:
            print((proc.stdout or "") + (proc.stderr or ""))
            failed.append(name)
            if not args.keep_going:
                break
    print(f"\n{passed} passed, {len(failed)} failed of {len(checks)} ({time.monotonic() - t0:.0f}s)")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
