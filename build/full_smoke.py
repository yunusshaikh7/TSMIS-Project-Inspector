"""Dev/venv run of the shared self-test (the same body the frozen exe runs
under --self-test). Not part of the check_* gate because it opens a hidden
WebView2 window.

    python build\\full_smoke.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT)]

import self_test  # noqa: E402

if __name__ == "__main__":
    sys.exit(self_test.run())
