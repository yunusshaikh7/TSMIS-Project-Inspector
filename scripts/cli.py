"""Console driver: the same scan the window runs, printed.

    python scripts\\cli.py                       scan the default folder
    python scripts\\cli.py "C:\\path\\to\\projects" --no-subfolders --map-layer-files

The only module besides gui_*.py that touches print/sys.exit.
"""
import argparse
import sys
from pathlib import Path


def _bootstrap():
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(here.parent))          # version.py


_bootstrap()


def main(argv=None):
    from logging_setup import setup_logging
    setup_logging(name="cli")
    import settings
    from aprx_scan import ScanError, run_scan
    from events import Events
    from paths import default_scan_root
    from scan_output import save, summary_lines

    ap = argparse.ArgumentParser(description="List the TSMIS branch version each ArcGIS Pro project uses.")
    ap.add_argument("folder", nargs="?", help="folder to scan (default: the saved/ default folder)")
    ap.add_argument("--no-subfolders", action="store_true", help="scan only the folder itself")
    ap.add_argument("--map-layer-files", action="store_true", help="also read .mapx / .lyrx files")
    args = ap.parse_args(argv)

    root = Path(args.folder or settings.get("scan_root") or default_scan_root())
    recursive = not args.no_subfolders and (settings.get("recursive") or bool(args.folder))
    extras = args.map_layer_files or settings.get("include_map_layer_files")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # silent-ok: an odd console keeps its own encoding
        pass
    events = Events(on_log=print)
    try:
        result = run_scan(root, recursive=recursive, include_map_layer_files=extras, events=events)
    except ScanError as e:
        print(f"ERROR: {e}")
        return 2
    workbook, diagnostics = save(result)
    print()
    for line in summary_lines(result, workbook):
        print(line)
    print(f"Diagnostics saved: {diagnostics}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
