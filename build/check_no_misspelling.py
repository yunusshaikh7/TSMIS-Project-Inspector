"""Guard: the product name is TSMIS. The transposition 'T' + 'MSIS' is a
recurring typo; this check fails if it appears anywhere in the tracked text.
The needle is assembled at runtime so this file does not match itself."""
import os
import re
import sys

NEEDLE = "T" + "MSIS"
_RX = re.compile(NEEDLE, re.IGNORECASE)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP_DIRS = {".git", ".venv", "dist", "pyi-work", "__pycache__", "output", "data", ".claude", "logs"}
_TEXT_EXT = {".py", ".md", ".txt", ".bat", ".ps1", ".spec", ".yml", ".yaml", ".js", ".css", ".html", ".json"}


def main():
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if name == os.path.basename(__file__) or os.path.splitext(name)[1].lower() not in _TEXT_EXT:
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if _RX.search(line):
                            offenders.append(f"{os.path.relpath(path, REPO)}:{lineno}: {line.strip()}")
            except OSError:
                continue
    if offenders:
        print(f"FAIL  product-name guard: found the '{NEEDLE}' misspelling:")
        print("\n".join("  " + o for o in offenders))
        return 1
    print("ok: no product-name misspelling")
    return 0


if __name__ == "__main__":
    sys.exit(main())
