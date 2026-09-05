"""Connection interpretation and exports. No ArcGIS or desktop dependency."""
import csv
import io
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZipFile


def pairs(text):
    """Read Esri workspace strings, including quoted semicolons."""
    result = {}
    pattern = r'(?:^|;)\s*([\w_]+)\s*=\s*("(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|[^;]*)'
    for match in re.finditer(pattern, str(text)):
        value = match[2].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].replace(value[0] * 2, value[0])
        result[match[1].lower()] = value
    return result


def clean_url(value):
    """Keep location; never retain URL credentials, query parameters or fragments."""
    try:
        url = urlsplit(str(value))
        if url.scheme.lower() not in {"http", "https"} or not url.hostname:
            return ""
        host = url.hostname
        if ":" in host:
            host = "[" + host + "]"
        if url.port:
            host += ":" + str(url.port)
        return urlunsplit((url.scheme.lower(), host, url.path, "", ""))
    except ValueError:
        return ""


# Diagnostics deliberately capture connection metadata only. Unknown property
# names are retained, but their values are withheld until explicitly supported.
SAFE_FIELDS = {
    "type", "name", "dataset", "workspace_factory", "workspacefactory",
    "database", "server", "instance", "dbclient", "db_connection_properties",
    "version", "versionguid", "version_guid", "authentication_mode",
    "branch", "branchversion", "branch_version", "featuredataset",
}
CHILD_FIELDS = {"connection_info", "source", "destination", "sourcetable",
                "destinationtable", "dataconnection", "featuretable",
                "rasterdataconnection", "dataconnections"}


def safe_metadata(value, key=""):
    lower = key.lower()
    if key and lower not in SAFE_FIELDS | CHILD_FIELDS | {"url", "uri", "workspaceconnectionstring"}:
        return "[withheld]"
    if lower == "workspaceconnectionstring":
        return safe_metadata(value if isinstance(value, dict) else pairs(value), "connection_info")
    if lower in {"url", "uri"}:
        return clean_url(value)
    if isinstance(value, dict):
        return {str(k): safe_metadata(v, str(k)) for k, v in value.items()} if not key or lower in CHILD_FIELDS else "[withheld]"
    if isinstance(value, (list, tuple)):
        return [safe_metadata(v, key) for v in value] if lower in CHILD_FIELDS else "[withheld]"
    if lower in SAFE_FIELDS:
        text = str(value) if value is not None else ""
        # Do not let an embedded connection string bypass the field allowlist.
        if re.search(r"(?i)(?:password|pwd|token|secret|credential|customparameters)\s*=", text):
            return "[withheld]"
        if "https://" in text or "http://" in text:
            return clean_url(text)
        return text
    return "[withheld]"


def service_details(url):
    parsed = urlsplit(clean_url(url))
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    lower = [p.lower() for p in parts]
    folder = service = site = ""
    for i in range(len(parts) - 1):
        if lower[i:i + 2] == ["rest", "services"]:
            site = "/".join(parts[:i])
            tail = parts[i + 2:]
            end = next((j for j, p in enumerate(tail) if p.lower() in {"featureserver", "mapserver"}), len(tail))
            if end:
                service = tail[end - 1]
                folder = "/".join(tail[:end - 1])
            break
    return {"host": parsed.hostname or "", "folder": folder, "service": service, "site": site}


def environment(host, site="", folder=""):
    names = {"dev": "Dev", "development": "Dev", "test": "Test", "testing": "Test",
             "uat": "Test", "qa": "Test", "prod": "Prod", "production": "Prod"}
    # Host is authoritative over folders. Never infer from a branch or layer name.
    for evidence in (host, "/".join([site, folder])):
        hits = {names[token] for token in re.split(r"[^a-z0-9]+", evidence.lower()) if token in names}
        if hits:
            return (next(iter(hits)), evidence) if len(hits) == 1 else ("Unknown", "Conflicting environment tokens: " + evidence)
    return "Unknown", "No recognized environment in server or service folder"


def _leaves(obj, path):
    if isinstance(obj, dict):
        lower = {k.lower(): v for k, v in obj.items()}
        info = lower.get("connection_info")
        workspace = lower.get("workspaceconnectionstring")
        direct_url = clean_url(lower.get("url", ""))
        if isinstance(info, dict) or isinstance(workspace, (dict, str)) or direct_url:
            values = info if isinstance(info, dict) else workspace if workspace is not None else {"url": direct_url}
            if isinstance(values, str):
                values = pairs(values)
            values = {k.lower(): v for k, v in values.items()}
            yield {"url": clean_url(values.get("url", "")),
                   "workspace": str(values.get("database") or values.get("server") or values.get("instance") or ""),
                   "dataset": str(lower.get("dataset", "")),
                   "factory": str(lower.get("workspace_factory") or lower.get("workspacefactory") or ""),
                   "version": str(values.get("version") or values.get("branchversion") or values.get("branch_version") or ""),
                   "version_guid": str(values.get("versionguid") or values.get("version_guid") or ""),
                   "evidence": path}
        for key, val in obj.items():
            if key.lower() not in {"connection_info", "workspaceconnectionstring"}:
                yield from _leaves(val, path + "." + key)
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            yield from _leaves(val, path + "[" + str(i) + "]")


def interpret(properties, cim, match="tsmis"):
    grouped = {}
    for item in list(_leaves(properties, "connectionProperties")) + list(_leaves(cim, "CIM")):
        # Same connection can be reported by both APIs. Merge only when source
        # and dataset match; joined sources must retain their own versions.
        identity = ((item["url"].rstrip("/") or item["workspace"]).lower(), item["dataset"].lower())
        current = grouped.setdefault(identity, dict(item, versions=[], evidence_paths=[]))
        if item["version"] and item["version"] not in current["versions"]:
            current["versions"].append(item["version"])
        current["evidence_paths"].append(item["evidence"])
        for key in ("factory", "version_guid", "workspace"):
            if not current[key]:
                current[key] = item[key]
    for item in grouped.values():
        item.update(service_details(item["url"]))
        item["environment"], item["environment_evidence"] = environment(item["host"], item["site"], item["folder"])
        identifiers = ("tsmis", "tsnr") if match.strip().casefold() in {"tsmis", "tsnr"} else (match.casefold(),)
        source_text = " ".join([item["url"], item["workspace"], item["dataset"]]).casefold()
        item["is_tsmis"] = any(identifier in source_text for identifier in identifiers)
        item["version"] = " | ".join(item["versions"])
        item["version_kind"] = "Service version" if "featureserver" in item["url"].lower() else "Database version" if item["version"] else ""
        item["status"] = "Conflicting versions" if len(item["versions"]) > 1 else "Version found" if item["version"] else "Version not exposed"
    return list(grouped.values())


def summarize(project):
    connections = [row for row in project["rows"] if row.get("is_tsmis")]
    versions = sorted({v for row in connections for v in row.get("versions", [])}, key=str.casefold)
    environments = sorted({row["environment"] for row in connections})
    issues = bool(project.get("errors")) or any(row.get("error") for row in project["rows"])
    if project.get("open_error"):
        status = "Could not open"
    elif issues:
        status = "Needs review"
    elif not connections:
        status = "No TSMIS connections"
    elif any(r["status"] == "Conflicting versions" for r in connections):
        status = "Needs review"
    elif any(not r["version"] or r["environment"] == "Unknown" for r in connections):
        status = "Needs review"
    elif len(versions) > 1 or len(environments) > 1:
        status = "Mixed connections"
    else:
        status = "Identified"
    project.update(versions=versions, environments=environments, status=status,
                   tsmis_connections=len(connections), folders=sorted({r["folder"] for r in connections if r["folder"]}),
                   services=sorted({r["service"] for r in connections if r.get("service")}, key=str.casefold))
    return project


def _csv_value(value):
    text = " | ".join(map(str, value)) if isinstance(value, list) else "" if value is None else str(value)
    # Protect CSVs opened in Excel from formula interpretation in project names.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def csv_text(rows, fields):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([title for _, title in fields])
    for row in rows:
        writer.writerow([_csv_value(row.get(key, "")) for key, _ in fields])
    return "\ufeff" + out.getvalue()


PROJECT_FIELDS = [("path", "Project path"), ("status", "Status"), ("environments", "Environments"),
                  ("versions", "Saved versions"), ("services", "Service names"), ("folders", "Service folders"),
                  ("tsmis_connections", "TSMIS connections"), ("errors", "Read issues")]
LAYER_FIELDS = [("project", "Project path"), ("map", "Map"), ("layer", "Layer / table"),
                ("kind", "Type"), ("is_tsmis", "Matches TSMIS"), ("environment", "Environment"),
                ("folder", "Service folder"), ("service", "Service"), ("version", "Saved version"),
                ("version_kind", "Version type"), ("status", "Status"), ("url", "Service URL"),
                ("workspace", "Workspace"), ("dataset", "Dataset"), ("environment_evidence", "Environment evidence"),
                ("evidence_paths", "Version evidence"), ("error", "Read issue")]


def export_bundle(path, result, diagnostics=False):
    projects = result.get("projects", [])
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("projects.csv", csv_text(projects, PROJECT_FIELDS))
        archive.writestr("layers.csv", csv_text([dict(r, project=p["path"]) for p in projects for r in p["rows"]], LAYER_FIELDS))
        metadata = {k: v for k, v in result.items() if k != "projects"}
        if diagnostics:
            metadata["projects"] = projects
        archive.writestr("diagnostics.json" if diagnostics else "scan.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        archive.writestr("Read me.txt", "TSMIS Project Inspector\n\nResults describe saved project connections at scan time. Projects are not changed.\n"
                          "Missing versions are not assumed to be DEFAULT. Unknown environments need review.\n"
                          "Multiple environments or versions may legitimately be used in a project.\n"
                          "CSV files open in Excel. Connections from joins are listed separately.\n"
                          "Diagnostics contain local paths, layer names, branch owners and internal server names.\n"
                          "Only allowlisted connection metadata is collected; no project files or feature data are included.\n")
