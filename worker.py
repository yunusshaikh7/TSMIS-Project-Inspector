"""Read-only ArcPy worker, executed by the WORK PC's ArcGIS Python."""
import argparse
import gc
import json
import os
import platform
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import CHILD_FIELDS, SAFE_FIELDS, clean_url, interpret, safe_metadata, summarize
from version import VERSION


def cim_connections(obj, depth=0):
    """Read documented CIM connection properties without serializing project data."""
    if obj is None or depth > 16:
        return {}
    if isinstance(obj, (list, tuple)):
        return [cim_connections(x, depth + 1) for x in obj]
    result = {"type": type(obj).__name__}
    names = (SAFE_FIELDS | CHILD_FIELDS | {"workspaceConnectionString", "workspaceFactory", "URL",
             "featureTable", "dataConnection", "sourceTable", "destinationTable", "rasterDataConnection",
             "featureDataset", "dataConnections"}) - {"source", "destination", "connection_info"}
    for name in sorted(names):
        try:
            value = getattr(obj, name, None)
        except Exception:
            continue
        if value is None or callable(value):
            continue
        if name.lower() in CHILD_FIELDS:
            nested = cim_connections(value, depth + 1)
            if nested:
                result[name] = nested
        elif isinstance(value, (str, int, float, bool)):
            result[name] = safe_metadata(value, name)
    return result


def read_layer(layer, map_name, kind, match, diagnostics, cim_version):
    name = str(getattr(layer, "longName", None) or getattr(layer, "name", "Unnamed"))
    base = {"map": map_name, "layer": name, "kind": kind, "error": ""}
    properties, cim, issues = {}, {}, []
    supports = kind == "Table"
    if not supports:
        try:
            supports = layer.supports("CONNECTIONPROPERTIES")
        except Exception:
            pass
    if supports:
        try:
            properties = safe_metadata(layer.connectionProperties or {})
        except Exception as exc:
            issues.append("Connection properties unavailable (" + type(exc).__name__ + ")")
    try:
        cim = cim_connections(layer.getDefinition(cim_version))
    except Exception as exc:
        if supports:
            issues.append("CIM connection unavailable (" + type(exc).__name__ + ")")
    rows = interpret(properties, cim, match)
    if not rows:
        # Some service layer types expose only dataSource. Do not invent a version.
        try:
            source = str(layer.dataSource)
            fallback = {"workspaceConnectionString": source} if "=" in source and not source.lower().startswith("http") else {"connection_info": {"url": clean_url(source)}}
            if clean_url(source) or "url=" in source.lower():
                rows = interpret(safe_metadata(fallback), {}, match)
        except Exception:
            pass
    base["error"] = "; ".join(issues)
    if not rows:
        rows = [{"status": "Connection unavailable" if supports else "No inspectable connection",
                 "is_tsmis": False, "version": "", "versions": [], "environment": "Unknown"}]
    rows = [dict(row, **base) for row in rows]
    snapshot = dict(base, connection_properties=properties, cim_connections=cim) if diagnostics else None
    return rows, snapshot


def read_project(path, arcpy, match="tsmis", diagnostics=False, cim_version="V3"):
    project = {"path": str(path), "name": Path(path).name, "rows": [], "errors": [], "open_error": False}
    if diagnostics:
        project["connection_metadata"] = []
    aprx = None
    try:
        aprx = arcpy.mp.ArcGISProject(str(path))
        maps = aprx.listMaps()
    except Exception as exc:
        project["open_error"] = True
        project["errors"].append("Project could not be read (" + type(exc).__name__ + "). Check file availability and ArcGIS Pro compatibility.")
        return summarize(project)
    try:
        for map_obj in maps:
            map_name = str(map_obj.name)
            for method, kind in (("listLayers", "Layer"), ("listTables", "Table")):
                try:
                    objects = getattr(map_obj, method)()
                except Exception as exc:
                    project["errors"].append(map_name + ": cannot list " + kind.lower() + "s (" + type(exc).__name__ + ")")
                    continue
                # listLayers already includes members of group layers.
                for layer in objects:
                    try:
                        if kind == "Layer" and getattr(layer, "isGroupLayer", False):
                            continue
                        rows, snapshot = read_layer(layer, map_name, kind, match, diagnostics, cim_version)
                        project["rows"].extend(rows)
                        if snapshot is not None:
                            project["connection_metadata"].append(snapshot)
                    except Exception as exc:
                        project["errors"].append(map_name + ": an item could not be read (" + type(exc).__name__ + ")")
    finally:
        del aprx
        gc.collect()
    return summarize(project)


def discover(root, recursive=True):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Choose an existing project folder.")
    errors, projects = [], []

    def inaccessible(exc):
        errors.append("Cannot read folder: " + str(exc.filename or root))

    def allowed(directory, name):
        if name.lower() in {".backups", ".git"} or name.lower().endswith(".gdb"):
            return False
        try:
            info = Path(directory, name).lstat()
            # Junctions can loop or lead outside the chosen tree. OneDrive cloud
            # placeholders have different tags and must remain discoverable.
            return not stat.S_ISLNK(info.st_mode) and getattr(info, "st_reparse_tag", 0) != 0xA0000003
        except OSError as exc:
            inaccessible(exc)
            return False

    for directory, dirs, files in os.walk(root, onerror=inaccessible, followlinks=False):
        dirs[:] = sorted(d for d in dirs if allowed(directory, d)) if recursive else []
        projects.extend(str(Path(directory, f)) for f in sorted(files) if f.lower().endswith(".aprx"))
    return sorted(projects, key=str.casefold), errors


def run(request, emit, arcpy_module=None):
    result = {"app_version": VERSION, "scan_time": datetime.now(timezone.utc).isoformat(),
              "root": request["root"], "recursive": request.get("recursive", True),
              "match": request.get("match", "tsmis"), "diagnostic_scan": request.get("diagnostics", False),
              "python_version": platform.python_version(), "projects": [], "warnings": [], "complete": False}
    emit({"type": "begin", "metadata": {k: v for k, v in result.items() if k != "projects"}})
    files, errors = discover(request["root"], request.get("recursive", True))
    emit({"type": "discovered", "total": len(files), "warnings": errors})
    if not files:
        emit({"type": "done", "arcgis_version": "Not needed; no projects found", "complete": not errors})
        return
    emit({"type": "progress", "message": "Starting ArcGIS Pro's Python reader…"})
    try:
        if arcpy_module is None:
            import arcpy as arcpy_module
        install = arcpy_module.GetInstallInfo()
        arcgis_version = str(install.get("Version", "Unknown"))
        cim_version = "V2" if arcgis_version.startswith("2.") else "V3"
    except Exception as exc:
        emit({"type": "fatal", "message": "ArcGIS Python could not initialize (" + type(exc).__name__ +
              "). Open ArcGIS Pro and sign in, then retry. If you use a cloned environment, select its python.exe in Settings."})
        return
    emit({"type": "runtime", "arcgis_version": arcgis_version})
    for index, path in enumerate(files):
        emit({"type": "progress", "message": "Reading " + Path(path).name, "current": index + 1})
        project = read_project(path, arcpy_module, result["match"], result["diagnostic_scan"], cim_version)
        emit({"type": "project", "project": project})
    emit({"type": "done", "arcgis_version": arcgis_version, "complete": not errors})


def main():
    parser = argparse.ArgumentParser(description="Read saved ArcGIS Pro project connections; never saves projects.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--events", required=True)
    args = parser.parse_args()
    with open(args.events, "w", encoding="utf-8", buffering=1) as events:
        def emit(event):
            events.write(json.dumps(event, ensure_ascii=True) + "\n")
            events.flush()
        try:
            run(json.loads(Path(args.request).read_text(encoding="utf-8")), emit)
        except Exception as exc:
            emit({"type": "fatal", "message": "Scan stopped (" + type(exc).__name__ + "). Check the selected folder and file permissions."})


if __name__ == "__main__":
    main()
