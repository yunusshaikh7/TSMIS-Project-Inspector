"""Shared scaffolding for the build/check_*.py suite."""
import contextlib
import shutil
import sys
import tempfile
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent
ROOT = BUILD_DIR.parent


def scripts_path():
    """Make scripts/ (and the repo root, for version.py) importable."""
    for p in (str(ROOT / "scripts"), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)


class Checker:
    def __init__(self):
        self.failures = []

    def check(self, name, cond, detail=""):
        if cond:
            print(f"  ok: {name}")
        else:
            print(f"FAIL: {name}" + (f"\n      {detail}" if detail else ""))
            self.failures.append(name)

    def summary(self):
        if self.failures:
            print(f"\n{len(self.failures)} check(s) FAILED")
            return 1
        print("\nall good")
        return 0


@contextlib.contextmanager
def patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def temp_dir(prefix="tsmis_check_"):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
