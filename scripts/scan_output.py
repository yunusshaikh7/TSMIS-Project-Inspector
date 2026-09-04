"""Turn a ScanResult into what people read: the Excel workbook, the
diagnostics JSON beside it, the summary lines, and the rows the GUI table
shows. Console-free.

The workbook is the deliverable (Projects / Layers / Versions / Scan sheets).
The diagnostics file records what the reader SAW inside every file — archive
members, CIM types, every connection string with its JSON path — so a run on
a real project library is enough to refine the parser without the projects
themselves ever leaving the PC. Passwords are removed before anything is
written.
"""
import json
import logging
from pathlib import Path

from paths import new_run_dir
from version import APP_NAME, __version__

log = logging.getLogger("tsmis.scan")

WORKBOOK_NAME = "branch_versions.xlsx"
DIAGNOSTICS_NAME = "diagnostics.json"

PROJECT_COLUMNS = ("Project", "Folder", "Status", "Versions", "Services", "Layers with data",
                   "Maps", "Type", "Cloud-only", "Size (KB)", "Modified", "Note")
LAYER_COLUMNS = ("Project", "Map", "Layer", "Layer type", "Connection type", "Version",
                 "Version GUID", "Service / workspace", "Dataset",
                 "Connection string (passwords removed)", "Found in")
VERSION_COLUMNS = ("Version", "Projects", "Layers", "Project files")
_MAX_COL_WIDTH = 70


def save(result, out_dir=None):
    """Write the workbook + diagnostics into `out_dir` (a fresh run folder by
    default). Returns (workbook_path, diagnostics_path)."""
    out_dir = Path(out_dir) if out_dir else new_run_dir(result.started)
    out_dir.mkdir(parents=True, exist_ok=True)
    workbook = out_dir / WORKBOOK_NAME
    diagnostics = out_dir / DIAGNOSTICS_NAME
    write_diagnostics(result, diagnostics)
    write_workbook(result, workbook)
    log.info("scan results saved: %s", workbook)
    return workbook, diagnostics


def project_row(p):
    return (p.path.name, str(p.path.parent), p.status_text, " | ".join(p.versions()),
            " | ".join(p.sources()), sum(1 for c in p.connections if c.source or c.version),
            ", ".join(p.maps), p.kind, "yes" if p.cloud_only else "",
            round(p.size / 1024, 1), p.modified, p.message)


def layer_rows(p):
    for c in p.connections:
        yield (p.path.name, c.map, c.layer, c.layer_type, c.connection_type, c.version,
               c.version_guid, c.source, c.dataset, c.connection,
               f"{c.member} · {c.json_path}")


def write_workbook(result, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDE7F3")

    def sheet(title, columns, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append(list(columns))
        widths = [len(c) for c in columns]
        for row in rows:
            ws.append(list(row))
            for i, v in enumerate(row):
                widths[i] = max(widths[i], min(_MAX_COL_WIDTH, len(str(v)) if v is not None else 0))
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w + 2
        for cell in ws[1]:
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        return ws

    projects = sorted(result.projects, key=lambda p: (str(p.path.parent).lower(), p.path.name.lower()))
    sheet("Projects", PROJECT_COLUMNS, (project_row(p) for p in projects), first=True)
    sheet("Layers", LAYER_COLUMNS, (r for p in projects for r in layer_rows(p)))
    tally = result.version_tally()
    sheet("Versions", VERSION_COLUMNS,
          ((v, len(t["projects"]), t["layers"], ", ".join(t["projects"]))
           for v, t in sorted(tally.items(), key=lambda kv: -len(kv[1]["projects"]))))
    sheet("Scan", ("Item", "Value"), scan_facts(result))
    wb.save(str(path))


def scan_facts(result):
    c = result.counts()
    return [
        ("App", f"{APP_NAME} v{__version__}"),
        ("Folder scanned", str(result.root)),
        ("Subfolders included", "yes" if result.recursive else "no"),
        ("Map/layer files (.mapx/.lyrx) included", "yes" if result.include_map_layer_files else "no"),
        ("Started", result.started.strftime("%Y-%m-%d %H:%M:%S")),
        ("Finished", result.finished.strftime("%Y-%m-%d %H:%M:%S") if result.finished else ""),
        ("Files read", c["total"]),
        ("With a version", c["ok"]),
        ("Without a version", c["no_versions"]),
        ("Without data connections", c["no_connections"]),
        ("Errors", c["error"]),
        ("OneDrive cloud-only files (downloaded to read)", c["cloud_only"]),
        (".backups folders skipped", result.skipped_dirs),
        ("Folders that could not be listed", result.unreadable_dirs),
        ("Cancelled before the end", "yes" if result.cancelled else "no"),
    ]


def write_diagnostics(result, path):
    payload = {
        "app": f"{APP_NAME} v{__version__}",
        "scan": {k: str(v) for k, v in scan_facts(result)},
        "files": [{
            "path": str(p.path), "kind": p.kind, "status": p.status, "message": p.message,
            "size": p.size, "cloud_only": p.cloud_only, "seconds": round(p.seconds, 3),
            "maps": p.maps, "members": p.members, "types_seen": p.types_seen,
            "connections": [{
                "map": c.map, "layer": c.layer, "layer_type": c.layer_type,
                "factory": c.factory, "version": c.version, "version_guid": c.version_guid,
                "source": c.source, "dataset": c.dataset, "connection": c.connection,
                "member": c.member, "json_path": c.json_path,
            } for c in p.connections],
        } for p in result.projects],
    }
    Path(path).write_text(json.dumps(payload, indent=1), encoding="utf-8")


def summary_lines(result, workbook=None):
    c = result.counts()
    lines = [f"Read {c['total']} file(s) under {result.root}: {c['ok']} with a version, "
             f"{c['no_versions']} without a version, {c['no_connections']} without data "
             f"connections, {c['error']} error(s)." + (" (cancelled)" if result.cancelled else "")]
    tally = result.version_tally()
    if tally:
        parts = [f"{v} ({len(t['projects'])} project{'s' if len(t['projects']) != 1 else ''})"
                 for v, t in sorted(tally.items(), key=lambda kv: -len(kv[1]["projects"]))]
        lines.append("Versions found: " + ", ".join(parts))
    if workbook:
        lines.append(f"Workbook saved: {workbook}")
    return lines


def table_rows(result):
    """JSON-safe rows for the GUI results table."""
    return [{
        "name": p.path.name, "folder": str(p.path.parent), "status": p.status,
        "status_text": p.status_text, "versions": p.versions(),
        "layers": len(p.connections), "message": p.message,
    } for p in sorted(result.projects,
                      key=lambda p: (str(p.path.parent).lower(), p.path.name.lower()))]
