"""The scanner + writer through the shipped path, over synthetic projects.

No ArcGIS Pro exists on the dev PC, so the fixtures are built here in the
shape Pro writes: zips of CIM JSON. Every assertion reads the FILE that comes
out (the workbook, the diagnostics), not an in-memory shortcut.

    python build\\check_scan.py
"""
import io
import json
import sys
import zipfile

from _checklib import Checker, scripts_path, temp_dir

scripts_path()

import aprx_scan  # noqa: E402
from aprx_scan import (describe_source, parse_connection_string,  # noqa: E402
                       redact_connection_string, run_scan)
from events import Events  # noqa: E402
from scan_output import save, summary_lines, table_rows  # noqa: E402

SERVICE = "https://gis.example.org/server/rest/services/TSMIS/FeatureServer"
SECRET = "hunter2-base64=="


def conn(text, factory="FeatureService", dataset="3"):
    return {"type": "CIMStandardDataConnection", "workspaceConnectionString": text,
            "workspaceFactory": factory, "dataset": dataset, "datasetType": "esriDTFeatureClass"}


def layer(name, connection, ctype="CIMFeatureLayer"):
    return {"type": ctype, "name": name, "uRI": f"CIMPATH=map/{name.lower()}.json",
            "featureTable": {"type": "CIMFeatureTable", "dataConnection": connection}}


def map_doc(name, layers, extra=None):
    doc = {"type": "CIMMapDocument", "version": "3.2.0", "build": 49743,
           "mapDefinition": {"type": "CIMMap", "name": name,
                             "layers": [ly["uRI"] for ly in layers if "uRI" in ly]},
           "layerDefinitions": layers}
    doc.update(extra or {})
    return doc


def write_aprx(path, members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            zf.writestr(name, payload if isinstance(payload, bytes) else json.dumps(payload))
    path.write_bytes(buf.getvalue())


def build_fixtures(root):
    # Alpha: a feature-service layer on a branch, a file-gdb layer with no
    # version, a standalone table on an SDE connection carrying a password, a
    # group layer, and a second map. GISProject.json + a binary thumbnail too.
    alpha_map = map_doc("Highways", [
        layer("Highways", conn(f"URL={SERVICE};VERSION=OWNER.Branch_A;VERSIONGUID={{ABC-123}}")),
        layer("Counties", conn("DATABASE=C:\\data\\base.gdb", factory="FileGDB", dataset="Counties")),
        {"type": "CIMGroupLayer", "name": "Reference", "layers": ["CIMPATH=map/counties.json"]},
    ])
    alpha_table = {"type": "CIMStandaloneTable", "name": "Inventory",
                   "dataConnection": conn(
                       "ENCRYPTED_PASSWORD=" + SECRET + ";SERVER=dbhost;INSTANCE=sde:sqlserver:dbhost;"
                       "DBCLIENT=sqlserver;DATABASE=tsmis;USER=reader;VERSION=sde.DEFAULT;"
                       "AUTHENTICATION_MODE=DBMS", factory="SDE", dataset="tsmis.dbo.Inventory")}
    alpha_map2 = map_doc("Ramps", [layer("Ramps", conn(f"URL={SERVICE};VERSION=owner.branch_a"))],
                         extra={"tableDefinitions": [alpha_table]})
    write_aprx(root / "Alpha.aprx", {
        "GISProject.json": {"type": "CIMGISProject", "version": "3.2.0", "projectItems": []},
        "Maps/m1.json": alpha_map, "Maps/m2.json": alpha_map2,
        "Index/thumb.png": b"\x89PNG\r\n\x1a\n\x00binary",
    })
    # Beta: two layers on two versions.
    write_aprx(root / "Beta.aprx", {"Maps/m.json": map_doc("Map", [
        layer("A", conn(f"URL={SERVICE};VERSION=SDE.DEFAULT")),
        layer("B", conn(f"URL={SERVICE};VERSION=KELLY.QA_2026")),
    ])})
    # Empty: JSON but no connections. Corrupt: not a zip.
    write_aprx(root / "Empty.aprx", {"GISProject.json": {"type": "CIMGISProject"}})
    (root / "Corrupt.aprx").write_bytes(b"this is not a zip archive")
    # Nested project, a .backups copy that must be skipped, a stray file, and
    # a plain-JSON layer file (only read when asked).
    (root / "Nested" / "sub").mkdir(parents=True)
    (root / "Nested" / ".backups").mkdir()
    write_aprx(root / "Nested" / "sub" / "Gamma.aprx",
               {"Maps/m.json": map_doc("Map", [layer("G", conn(f"URL={SERVICE};VERSION=OWNER.Gamma"))])})
    write_aprx(root / "Nested" / ".backups" / "Gamma.aprx",
               {"Maps/m.json": map_doc("Map", [layer("G", conn(f"URL={SERVICE};VERSION=OWNER.Old"))])})
    (root / "notes.txt").write_text("not a project", encoding="utf-8")
    (root / "Layer.lyrx").write_text(json.dumps({
        "type": "CIMLayerDocument", "version": "3.2.0",
        "layerDefinitions": [layer("Solo", conn(f"URL={SERVICE};VERSION=OWNER.Lyrx"))]}),
        encoding="utf-8")


def main():
    c = Checker()
    print("connection-string helpers:")
    props = parse_connection_string("URL=https://h/x?a=b=c;VERSION=sde.DEFAULT; user = me ")
    c.check("keys upper-cased, values keep '='", props == {"URL": "https://h/x?a=b=c",
                                                          "VERSION": "sde.DEFAULT", "USER": "me"}, props)
    red = redact_connection_string("ENCRYPTED_PASSWORD=abc==;SERVER=s;TOKEN=t;VERSION=v")
    c.check("passwords/tokens removed, the rest kept",
            red == "ENCRYPTED_PASSWORD=<removed>;SERVER=s;TOKEN=<removed>;VERSION=v", red)
    c.check("service URL wins as the source", describe_source({"URL": "https://h/FeatureServer?f=json",
                                                               "SERVER": "x"}) == "https://h/FeatureServer")
    c.check("server · instance · database otherwise",
            describe_source({"SERVER": "s", "INSTANCE": "i", "DATABASE": "d"}) == "s · i · d")

    with temp_dir("tsmis_scan_") as tmp:
        root = tmp / "projects"
        root.mkdir()
        build_fixtures(root)

        print("recursive scan of the fixtures:")
        lines = []
        res = run_scan(root, recursive=True, events=Events(on_log=lines.append))
        by = {p.path.name: p for p in res.projects}
        c.check("finds the 4 top-level + 1 nested .aprx (not the .backups copy, not .lyrx)",
                sorted(by) == ["Alpha.aprx", "Beta.aprx", "Corrupt.aprx", "Empty.aprx", "Gamma.aprx"], sorted(by))
        c.check("one .backups folder skipped", res.skipped_dirs == 1, res.skipped_dirs)
        a = by["Alpha.aprx"]
        c.check("Alpha ok", a.status == "ok", (a.status, a.message))
        c.check("Alpha versions de-duplicate case-insensitively, first spelling kept",
                a.versions() == ["OWNER.Branch_A", "sde.DEFAULT"], a.versions())
        c.check("Alpha maps", a.maps == ["Highways", "Ramps"], a.maps)
        c.check("Alpha has 4 connections (3 layers + 1 table)", len(a.connections) == 4, len(a.connections))
        hw = next(x for x in a.connections if x.layer == "Highways")
        c.check("layer context: name/type/map/factory/guid", (hw.layer_type, hw.map, hw.factory, hw.version_guid)
                == ("FeatureLayer", "Highways", "FeatureService", "{ABC-123}"), hw)
        c.check("connection type label", hw.connection_type == "Feature service")
        inv = next(x for x in a.connections if x.layer == "Inventory")
        c.check("standalone table context + SDE source", (inv.layer_type, inv.source, inv.version)
                == ("StandaloneTable", "dbhost · sde:sqlserver:dbhost · tsmis", "sde.DEFAULT"), inv)
        c.check("password redacted in the connection", SECRET not in inv.connection and "<removed>" in inv.connection)
        c.check("json path recorded", hw.json_path == "layerDefinitions[0].featureTable.dataConnection", hw.json_path)
        c.check("members + types recorded for diagnostics",
                len(a.members) == 4 and sum(1 for m in a.members if m["json"]) == 3
                and a.types_seen.get("CIMFeatureLayer") == 3, (a.members, a.types_seen))
        b = by["Beta.aprx"]
        c.check("Beta two versions", b.versions() == ["SDE.DEFAULT", "KELLY.QA_2026"], b.versions())
        c.check("Empty -> no_connections with a count", by["Empty.aprx"].status == "no_connections"
                and "1 files inside, 1 JSON" in by["Empty.aprx"].message, by["Empty.aprx"].message)
        c.check("Corrupt -> error (not a zip)", by["Corrupt.aprx"].status == "error"
                and "not a zip" in by["Corrupt.aprx"].message, by["Corrupt.aprx"].message)
        c.check("nested Gamma found", by["Gamma.aprx"].versions() == ["OWNER.Gamma"])
        c.check("per-file log lines", any(l.startswith("  Alpha.aprx: OWNER.Branch_A") for l in lines)
                and any("Corrupt.aprx: Error" in l for l in lines), lines)
        tally = res.version_tally()
        c.check("version tally across projects",
                tally["sde.DEFAULT"]["projects"] == ["Alpha.aprx", "Beta.aprx"] and tally["sde.DEFAULT"]["layers"] == 2
                and tally["OWNER.Branch_A"]["layers"] == 2, tally)
        c.check("counts", res.counts() == {"ok": 3, "no_versions": 0, "no_connections": 1, "error": 1,
                                           "total": 5, "cloud_only": 0}, res.counts())

        print("save through the writer:")
        workbook, diagnostics = save(res, tmp / "out")
        from openpyxl import load_workbook
        wb = load_workbook(str(workbook))
        c.check("four sheets", wb.sheetnames == ["Projects", "Layers", "Versions", "Scan"], wb.sheetnames)
        ws = wb["Projects"]
        rows = list(ws.iter_rows(values_only=True))
        c.check("Projects header", rows[0][:4] == ("Project", "Folder", "Status", "Versions"), rows[0])
        c.check("Projects sorted by folder then name; Alpha first",
                [r[0] for r in rows[1:]] == ["Alpha.aprx", "Beta.aprx", "Corrupt.aprx", "Empty.aprx", "Gamma.aprx"],
                [r[0] for r in rows[1:]])
        c.check("Alpha row: versions joined, status text",
                rows[1][2] == "OK" and rows[1][3] == "OWNER.Branch_A | sde.DEFAULT", rows[1])
        lay = list(wb["Layers"].iter_rows(values_only=True))
        c.check("Layers rows = every connection (4+2+1)", len(lay) == 8, len(lay))
        blob = json.dumps([list(r) for r in lay], default=str)
        c.check("no password anywhere in the Layers sheet", SECRET not in blob)
        ver = list(wb["Versions"].iter_rows(values_only=True))
        c.check("Versions sheet lists sde.DEFAULT with 2 projects",
                any(r[0] == "sde.DEFAULT" and r[1] == 2 and r[2] == 2 for r in ver[1:]), ver)
        diag = json.loads(diagnostics.read_text(encoding="utf-8"))
        c.check("diagnostics carry members/types/connections and no password",
                len(diag["files"]) == 5 and diag["files"][0]["types_seen"]
                and SECRET not in diagnostics.read_text(encoding="utf-8"))
        c.check("summary lines name the workbook",
                summary_lines(res, workbook)[0].startswith("Read 5 file(s)")
                and str(workbook) in summary_lines(res, workbook)[-1])
        c.check("table rows are JSON-safe + ordered", [r["name"] for r in table_rows(res)][0] == "Alpha.aprx"
                and json.dumps(table_rows(res)))

        print("options:")
        flat = run_scan(root, recursive=False)
        c.check("non-recursive: top level only", sorted(p.path.name for p in flat.projects)
                == ["Alpha.aprx", "Beta.aprx", "Corrupt.aprx", "Empty.aprx"])
        extra = run_scan(root, recursive=False, include_map_layer_files=True)
        ly = next((p for p in extra.projects if p.path.suffix == ".lyrx"), None)
        c.check(".lyrx read as a layer file when asked", ly is not None and ly.kind == "layer file"
                and ly.versions() == ["OWNER.Lyrx"], ly)
        calls = {"n": 0}

        def cancel_after_first():
            calls["n"] += 1
            return calls["n"] > 1
        cancelled = run_scan(root, recursive=True, events=Events(is_cancelled=cancel_after_first))
        c.check("cancel stops after the current file", cancelled.cancelled and len(cancelled.projects) == 1)
        try:
            run_scan(tmp / "missing")
            c.check("missing folder raises ScanError", False)
        except aprx_scan.ScanError:
            c.check("missing folder raises ScanError", True)
    raise SystemExit(c.summary())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
