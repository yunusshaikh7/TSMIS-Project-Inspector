"""Assemble the GitHub release notes for one version: the shared header
(build/release_notes_header.md) + that version's section from CHANGELOG.md.
Exits non-zero (failing the release) if the version has no CHANGELOG section.

    python build/gen_release_notes.py v0.1.0 -o notes.md
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section_for(tag, changelog):
    lines = changelog.splitlines()
    head = re.compile(r"^##\s+" + re.escape(tag) + r"(?:\s|$)")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        out.append(ln)
    return "\n".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    header = (ROOT / "build" / "release_notes_header.md").read_text(encoding="utf-8").strip()
    section = section_for(args.tag, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    if not section:
        sys.stderr.write(f"ERROR: no '## {args.tag}' section in CHANGELOG.md.\n")
        return 1
    notes = f"{header}\n\n## What's new in {args.tag}\n\n{section}\n"
    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
