"""Read ArcGIS Pro project files WITHOUT ArcGIS Pro.

An .aprx is a zip archive of JSON documents in Esri's CIM (Cartographic
Information Model) — the same JSON a .mapx / .lyrx file holds in the open.
Every layer that draws data carries a data connection whose
`workspaceConnectionString` names its workspace as `KEY=value;KEY=value;…`,
and a branch-versioned feature service (what TSMIS publishes) names the
version it is opened on right there, e.g.

    URL=https://gis-prod.example.org/server/rest/services/TSMIS/lrs_tsmis/FeatureServer;VERSION=sde.DEFAULT;VERSIONGUID={…}

So the version each project works in — and the environment (the host) and
service folder it comes from — can be read straight from the file: no arcpy,
no ArcGIS Pro, no licence.

The reader is deliberately layout-agnostic: it parses EVERY JSON document in
the archive (sniffed by content, not by file name) and walks it recursively,
so it does not depend on which folder Pro keeps its maps in. What it saw —
every member, every CIM type, every connection string with its JSON path, and
(in bundle mode) every document itself, secrets removed — is recorded per
file, which is how a run on a real project library refines this parser.

Console-free: reports through an Events sink and never prints. A bad file
never raises; its row says what went wrong.
"""
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from events import Events

log = logging.getLogger("tsmis.scan")

PROJECT_EXTENSIONS = (".aprx",)
MAP_LAYER_EXTENSIONS = (".mapx", ".lyrx")
KINDS = {".aprx": "project", ".mapx": "map file", ".lyrx": "layer file"}

# ArcGIS Pro keeps autosave copies of a project in <project>\.backups\ — the
# same project, older, so they would only duplicate rows.
SKIP_DIRS = frozenset({".backups", "$recycle.bin", "system volume information"})

MAX_MEMBER_BYTES = 256 * 1024 * 1024        # never load a bigger archive member
# Bundle mode keeps the (redacted) documents themselves; bounded so a giant
# project library still yields a sendable file.
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS_PER_FILE_BYTES = 40 * 1024 * 1024

# OneDrive Files On-Demand placeholders: reading one downloads it first.
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

_SECRET_KEY = re.compile(r"PASSWORD|PASSWD|PWD|TOKEN|SECRET", re.I)
_SECRET_IN_TEXT = re.compile(r"(PASSWORD|PASSWD|PWD|TOKEN|SECRET)\s*=", re.I)

# Environment words as they appear in a TSMIS host name (gis-prod.example.org),
# a server site name, or a service folder. First match wins; the bundle from a
# real project library is what settles the final rule.
_ENV_TOKENS = (
    ("prod", "Prod"), ("prd", "Prod"), ("production", "Prod"),
    ("dev", "Dev"), ("development", "Dev"),
    ("test", "Test"), ("tst", "Test"), ("uat", "Test"), ("qa", "Test"),
    ("stage", "Test"), ("staging", "Test"),
)
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

FACTORY_LABELS = {
    "featureservice": "Feature service",
    "sde": "Enterprise geodatabase",
    "filegdb": "File geodatabase",
    "shapefile": "Shapefile",
    "raster": "Raster",
    "sql": "Database",
    "oracle": "Database",
    "custom": "Custom",
}

STATUS_TEXT = {
    "ok": "OK",
    "no_versions": "No version found",
    "no_connections": "No data connections",
    "error": "Error",
}


class ScanError(Exception):
    """A scan could not start; the message is user-safe."""


# ------------------------------------------------------------ results ------

@dataclass
class Connection:
    """One data connection found inside a project (usually one layer)."""
    map: str = ""
    layer: str = ""
    layer_type: str = ""          # the CIM type minus its "CIM" prefix
    factory: str = ""             # workspaceFactory as written in the file
    version: str = ""             # VERSION=… from the connection string
    version_guid: str = ""        # VERSIONGUID=… when present
    source: str = ""              # service URL / server + database / file path
    environment: str = ""         # Prod / Dev / Test, read off the host or folder
    host: str = ""                # the service host (or the SERVER= of a geodatabase)
    folder: str = ""              # the ArcGIS Server folder(s) between rest/services and the service
    service: str = ""             # the service name
    dataset: str = ""
    connection: str = ""          # the connection string, secrets removed
    member: str = ""              # the archive member (or the file itself)
    json_path: str = ""

    @property
    def connection_type(self):
        return FACTORY_LABELS.get(self.factory.lower(), self.factory)

    @property
    def environment_label(self):
        """What the GUI shows: the environment, else the raw host so an
        unclassified source is still visible."""
        return self.environment or self.host


@dataclass
class ProjectResult:
    path: Path
    kind: str                     # project | map file | layer file
    status: str = "ok"            # ok | no_versions | no_connections | error
    message: str = ""
    size: int = 0
    modified: str = ""
    cloud_only: bool = False
    maps: list = field(default_factory=list)
    connections: list = field(default_factory=list)
    members: list = field(default_factory=list)        # diagnostics
    types_seen: dict = field(default_factory=dict)     # diagnostics
    documents: list = field(default_factory=list)      # bundle mode: [(member, redacted json text)]
    seconds: float = 0.0

    @property
    def status_text(self):
        return STATUS_TEXT.get(self.status, self.status)

    def versions(self):
        """Distinct version names, first-seen spelling, case-insensitive."""
        return _distinct(c.version for c in self.connections)

    def environments(self):
        return _distinct(c.environment_label for c in self.connections)

    def sources(self):
        return _distinct(c.source for c in self.connections)

    def service_folders(self):
        return _distinct(c.folder for c in self.connections)


@dataclass
class ScanResult:
    root: Path
    recursive: bool
    include_map_layer_files: bool
    started: datetime
    finished: datetime = None
    projects: list = field(default_factory=list)
    skipped_dirs: int = 0
    unreadable_dirs: int = 0
    cancelled: bool = False

    def counts(self):
        by = {"ok": 0, "no_versions": 0, "no_connections": 0, "error": 0}
        for p in self.projects:
            by[p.status] = by.get(p.status, 0) + 1
        by["total"] = len(self.projects)
        by["cloud_only"] = sum(1 for p in self.projects if p.cloud_only)
        return by

    def version_tally(self):
        """{(environment, version): {"projects": [names], "layers": n}} across
        the whole scan — a branch version name is only unique within one
        environment, so the pair is the key."""
        tally = {}
        canon = {}
        for p in self.projects:
            for c in p.connections:
                if not c.version:
                    continue
                spelling = canon.setdefault(c.version.lower(), c.version)
                key = (c.environment_label, spelling)
                entry = tally.setdefault(key, {"projects": [], "layers": 0})
                entry["layers"] += 1
                if p.path.name not in entry["projects"]:
                    entry["projects"].append(p.path.name)
        return tally


def _distinct(values):
    seen, out = set(), []
    for v in values:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


# --------------------------------------------------- connection strings ----

def parse_connection_string(text):
    """`KEY=value;KEY=value` -> {KEY: value}. Keys upper-cased; a value keeps
    any '=' it contains (base64 passwords, URL queries)."""
    props = {}
    for part in (text or "").split(";"):
        key, sep, value = part.partition("=")
        key = key.strip().upper()
        if sep and key:
            props[key] = value.strip()
    return props


def redact_connection_string(text):
    """The same string with every password/token-like value replaced."""
    out = []
    for part in (text or "").split(";"):
        key, sep, _value = part.partition("=")
        if sep and _SECRET_KEY.search(key.strip()):
            part = f"{key}=<removed>"
        out.append(part)
    return ";".join(out)


def describe_source(props):
    """What the connection points at: the service URL, else server / instance /
    database (which is the .gdb path for a file geodatabase)."""
    url = props.get("URL")
    if url:
        return url.split("?", 1)[0]
    parts = [props[k] for k in ("SERVER", "INSTANCE", "DATABASE") if props.get(k)]
    return " · ".join(parts)


def classify_environment(*texts):
    """Prod / Dev / Test from the first environment word found in `texts`
    (each split on every non-alphanumeric character, so `rhapps-prod` and
    `TSMIS_DEV` both read), else ''."""
    for text in texts:
        tokens = _TOKEN_SPLIT.split((text or "").lower())
        for token, label in _ENV_TOKENS:
            if token in tokens:
                return label
    return ""


def parse_service_url(url):
    """host / site / folder / service / environment for an ArcGIS REST URL:
    https://gis-prod.example.org/server/rest/services/TSMIS/lrs_tsmis/FeatureServer
    -> host gis-prod.example.org, site server, folder TSMIS, service lrs_tsmis,
    environment Prod. Anything that is not such a URL yields blanks."""
    out = {"host": "", "site": "", "folder": "", "service": "", "environment": ""}
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return out
    out["host"] = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    lower = [s.lower() for s in segs]
    if "rest" in lower and "services" in lower[lower.index("rest") + 1:lower.index("rest") + 2]:
        i = lower.index("rest")
        out["site"] = "/".join(segs[:i])
        rest = segs[i + 2:]
        # drop the server type + anything after it (FeatureServer/0, MapServer…)
        stop = next((k for k, s in enumerate(rest) if s.lower().endswith("server")), len(rest))
        rest = rest[:stop]
        if rest:
            out["service"] = rest[-1]
            out["folder"] = "/".join(rest[:-1])
    out["environment"] = classify_environment(out["host"], out["site"], out["folder"])
    return out


# ------------------------------------------------------- the JSON walk ------

def _parse_json(data):
    """The parsed document when `data` is JSON text, else None (binary
    members, thumbnails, indexes). Sniffs the content — never the name."""
    text = data.decode("utf-8-sig", errors="replace").lstrip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _document_map_name(doc):
    if not isinstance(doc, dict):
        return ""
    md = doc.get("mapDefinition")
    if isinstance(md, dict) and isinstance(md.get("name"), str):
        return md["name"]
    if doc.get("type") == "CIMMap" and isinstance(doc.get("name"), str):
        return doc["name"]
    return ""


def _connection_from(node, text, member, map_name, owner, path):
    props = parse_connection_string(text)
    name, ctype = owner if owner else ("", "")
    dataset = node.get("dataset", "")
    source = describe_source(props)
    svc = parse_service_url(props["URL"]) if props.get("URL") else None
    if svc is None:
        host = (props.get("SERVER") or "").lower()
        environment = classify_environment(host, props.get("INSTANCE"), props.get("DATABASE"))
        folder = service = ""
    else:
        host, folder, service, environment = svc["host"], svc["folder"], svc["service"], svc["environment"]
    return Connection(
        map=map_name, layer=name,
        layer_type=ctype[3:] if ctype.startswith("CIM") else ctype,
        factory=str(node.get("workspaceFactory") or ""),
        version=props.get("VERSION", ""),
        version_guid=props.get("VERSIONGUID", ""),
        source=source, environment=environment, host=host, folder=folder, service=service,
        dataset=dataset if isinstance(dataset, str) else str(dataset),
        connection=redact_connection_string(text),
        member=member, json_path=path)


def _collect(node, member, map_name, out, types, ancestors=(), path=""):
    """Recursively harvest every data connection under `node`, tagging each
    with the nearest named + typed ancestor (the layer or table that owns
    it) and counting every CIM `type` seen."""
    if isinstance(node, dict):
        ctype = node.get("type")
        if isinstance(ctype, str):
            types[ctype] = types.get(ctype, 0) + 1
        text = node.get("workspaceConnectionString")
        if isinstance(text, str):
            owner = ancestors[-1] if ancestors else None
            out.append(_connection_from(node, text, member, map_name, owner, path))
        if isinstance(node.get("name"), str) and isinstance(ctype, str):
            ancestors = ancestors + ((node["name"], ctype),)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _collect(value, member, map_name, out, types, ancestors,
                         f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, (dict, list)):
                _collect(value, member, map_name, out, types, ancestors, f"{path}[{i}]")


def redact_document(node):
    """A copy of a parsed document with every secret removed: values under
    password/token-like keys, and passwords/tokens inside connection strings.
    Runs before a document is kept for the bundle."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if isinstance(key, str) and _SECRET_KEY.search(key):
                out[key] = "<removed>"
            else:
                out[key] = redact_document(value)
        return out
    if isinstance(node, list):
        return [redact_document(v) for v in node]
    if isinstance(node, str) and _SECRET_IN_TEXT.search(node):
        return redact_connection_string(node)
    return node


def _harvest(doc, member, res, keep_documents):
    map_name = _document_map_name(doc)
    if map_name and map_name not in res.maps:
        res.maps.append(map_name)
    _collect(doc, member, map_name, res.connections, res.types_seen)
    if keep_documents:
        text = json.dumps(redact_document(doc), indent=1)
        kept = sum(len(t) for _m, t in res.documents)
        if len(text) > MAX_DOCUMENT_BYTES:
            res.documents.append((member, json.dumps({"note": "document not kept: too large",
                                                      "bytes": len(text)})))
        elif kept + len(text) > MAX_DOCUMENTS_PER_FILE_BYTES:
            res.documents.append((member, json.dumps({"note": "document not kept: per-file cap reached",
                                                      "bytes": len(text)})))
        else:
            res.documents.append((member, text))


# ------------------------------------------------------------- readers -----

def _is_cloud_only(st):
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (_FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS))


def _read_archive(path, res, keep_documents):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entry = {"name": info.filename, "size": info.file_size, "json": False}
            res.members.append(entry)
            if info.file_size > MAX_MEMBER_BYTES:
                entry["note"] = "skipped: too large"
                continue
            data = zf.read(info)
            doc = _parse_json(data)
            if doc is None:
                entry["head"] = data[:16].hex()          # what a non-JSON member starts with
                continue
            entry["json"] = True
            if isinstance(doc, dict):
                entry["root_type"] = doc.get("type") or ""
                entry["root_keys"] = sorted(doc.keys())[:40]
                if doc.get("version"):
                    entry["cim_version"] = str(doc.get("version"))
            _harvest(doc, info.filename, res, keep_documents)


def _read_json_file(path, res, keep_documents):
    entry = {"name": path.name, "size": res.size, "json": False}
    res.members.append(entry)
    doc = _parse_json(path.read_bytes())
    if doc is None:
        raise ValueError("not a JSON document")
    entry["json"] = True
    if isinstance(doc, dict):
        entry["root_type"] = doc.get("type") or ""
        entry["root_keys"] = sorted(doc.keys())[:40]
    _harvest(doc, path.name, res, keep_documents)


def read_project(path, keep_documents=False):
    """Read ONE .aprx / .mapx / .lyrx into a ProjectResult. Never raises.
    `keep_documents` keeps every JSON document (secrets removed) for the
    diagnostics bundle."""
    path = Path(path)
    res = ProjectResult(path=path, kind=KINDS.get(path.suffix.lower(), "project"))
    t0 = time.monotonic()
    try:
        st = path.stat()
        res.size = st.st_size
        res.modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        res.cloud_only = _is_cloud_only(st)
        if path.suffix.lower() == ".aprx":
            _read_archive(path, res, keep_documents)
        else:
            _read_json_file(path, res, keep_documents)
    except zipfile.BadZipFile:
        res.status, res.message = "error", "not a valid project file (not a zip archive)"
    except ValueError as e:
        res.status, res.message = "error", str(e)
    except OSError as e:
        res.status = "error"
        res.message = f"could not read the file ({type(e).__name__}: {str(e).splitlines()[0]})"
    except Exception as e:                       # noqa: BLE001 — a bad file must not stop the scan
        log.exception("unexpected failure reading %s", path)
        res.status, res.message = "error", f"{type(e).__name__}: {e}"
    else:
        json_members = sum(1 for m in res.members if m.get("json"))
        if not res.connections:
            res.status = "no_connections"
            res.message = (f"no data connections found ({len(res.members)} files inside, "
                           f"{json_members} JSON)" if res.kind == "project"
                           else "no data connections found")
        elif not res.versions():
            res.status = "no_versions"
            res.message = "data connections found, but none names a version"
    res.seconds = time.monotonic() - t0
    if res.status == "error":
        log.warning("scan: %s -> %s", path, res.message)
    else:
        log.info("scan: %s -> %s (%d connections, %.2fs)", path, res.status,
                 len(res.connections), res.seconds)
    return res


# -------------------------------------------------------------- the scan ---

def find_files(root, recursive=True, extensions=PROJECT_EXTENSIONS, result=None):
    """Every file under `root` with one of `extensions`, sorted. An unreadable
    subfolder is logged and counted, never fatal."""
    root = Path(root)
    exts = {e.lower() for e in extensions}

    def onerror(err):
        log.warning("scan: cannot list %s (%s)", getattr(err, "filename", "?"), err)
        if result is not None:
            result.unreadable_dirs += 1

    if not recursive:
        try:
            entries = sorted(root.iterdir())
        except OSError as e:
            onerror(e)
            return []
        return [p for p in entries if p.suffix.lower() in exts and p.is_file()]
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
        kept = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        if result is not None:
            result.skipped_dirs += len(dirnames) - len(kept)
        dirnames[:] = sorted(kept)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in exts:
                out.append(Path(dirpath) / name)
    return out


def _file_line(res):
    if res.status == "ok":
        envs = ", ".join(res.environments())
        return f"  {res.path.name}: {', '.join(res.versions())}" + (f"  [{envs}]" if envs else "")
    return f"  {res.path.name}: {res.status_text} — {res.message}"


def run_scan(root, recursive=True, include_map_layer_files=False, events=None,
             keep_documents=False):
    """Scan `root` and return a ScanResult (nothing is written here — see
    scan_output.save). Raises ScanError when the folder cannot be scanned."""
    events = events or Events()
    root = Path(root)
    if not root.is_dir():
        raise ScanError(f"the folder does not exist: {root}")
    result = ScanResult(root=root, recursive=bool(recursive),
                        include_map_layer_files=bool(include_map_layer_files),
                        started=datetime.now())
    exts = PROJECT_EXTENSIONS + (MAP_LAYER_EXTENSIONS if include_map_layer_files else ())
    events.on_log(f"Looking for {' / '.join(exts)} files under {root}"
                  + (" and its subfolders…" if recursive else "…"))
    files = find_files(root, recursive, exts, result)
    events.on_log(f"Found {len(files)} file(s)."
                  + (f" Skipped {result.skipped_dirs} .backups folder(s)."
                     if result.skipped_dirs else "")
                  + (f" {result.unreadable_dirs} folder(s) could not be listed (see the log)."
                     if result.unreadable_dirs else ""))
    total = len(files)
    for i, path in enumerate(files):
        if events.is_cancelled():
            result.cancelled = True
            events.on_log("Scan cancelled.")
            break
        events.on_progress(i, total, str(path))
        res = read_project(path, keep_documents=keep_documents)
        result.projects.append(res)
        events.on_log(_file_line(res))
    events.on_progress(len(result.projects), total, "")
    cloud = result.counts()["cloud_only"]
    if cloud:
        events.on_log(f"Note: {cloud} file(s) were OneDrive cloud-only placeholders and were "
                      "downloaded to read them.")
    result.finished = datetime.now()
    return result
