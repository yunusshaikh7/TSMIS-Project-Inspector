# TSMIS Branch Identifier

A small portable Windows app that scans a folder of ArcGIS Pro projects and
lists, for each `.aprx`, the **TSMIS branch version** its layers are opened on.
No ArcGIS Pro, no arcpy, no licence: it reads the project files directly.

It is the little sibling of the [TSMIS Reports Exporter](https://github.com/yunusshaikh7/TSMIS-Reports-Exporter)
and shares its architecture — a console-free Python core, an Edge WebView2
window, a portable single-folder build with bundled Python, a `.bat` console
fallback, and one-click updates from GitHub releases — with none of its
report machinery.

## Use it

1. Download `TSMIS-Branch-Identifier-v<x.y.z>-win64.zip` from the
   [latest release](https://github.com/yunusshaikh7/TSMIS-Branch-Identifier/releases/latest),
   right-click → Properties → **Unblock**, extract anywhere writable.
2. Double-click `TSMIS Branch Identifier.exe`. (First run: "More info → Run
   anyway" — it is an in-house unsigned tool.)
3. Browse to the folder that holds the projects — the default is
   `Documents\ArcGIS\Projects`, but any folder works, including a OneDrive one
   such as `…\OneDrive - Example Organization\Documents\01_Projects\TSNR\GIS_Projects`.
4. **Scan.** Subfolders are included; ArcGIS Pro's `.backups` copies are skipped.
5. **Open workbook.** Each scan is saved under `output\<date time>\` next to
   the app: `branch_versions.xlsx` and `diagnostics.json`.

`Start Here.txt` inside the zip says the same in user terms.

## What the workbook holds

| Sheet | One row per | Columns |
|---|---|---|
| **Projects** | project file | status, the distinct version(s) its layers use, the services they point at, counts, cloud-only flag, size, modified |
| **Layers** | data connection (usually a layer) | map, layer, layer type, connection type, version, version GUID, service / workspace, dataset, the connection string with passwords removed, and exactly where in the file it was found |
| **Versions** | distinct version | how many projects and layers use it, and which projects |
| **Scan** | — | the scan parameters and counts |

Statuses: **OK** (a version was found), **No version found** (data connections
exist but none names a version — file geodatabases, shapefiles, services opened
without a version), **No data connections**, **Error** (not a valid project
file, or it could not be read).

## How it works

An `.aprx` is a **zip archive of JSON documents** in Esri's CIM (Cartographic
Information Model) — the same JSON a `.mapx` / `.lyrx` file holds in the open.
Every layer that draws data carries a data connection whose
`workspaceConnectionString` names its workspace as `KEY=value;KEY=value;…`, and
a branch-versioned feature service (what TSMIS publishes) names the version it
is opened on right there:

```
URL=https://<host>/server/rest/services/TSMIS/FeatureServer;VERSION=OWNER.Branch_A;VERSIONGUID={…}
```

So the reader (`scripts/aprx_scan.py`) opens each `.aprx` as a zip, parses
every member that is JSON (sniffed by content, not by name), walks each
document recursively, and records every `workspaceConnectionString` with the
layer/table that owns it, the map it is in, and the parsed `VERSION`. It never
depends on which folder Pro keeps its maps in.

**The reader has not yet met a real project.** The dev PC has no ArcGIS Pro,
so the parser was built from the documented CIM shape and proved against
synthetic projects written in that shape (`build/check_scan.py`). The first run
on a real project library is what confirms or corrects it — which is why every
scan also writes `diagnostics.json`: for each file, the archive members, every
CIM `type` seen, and every connection string (passwords removed) with its JSON
path. If a project reads as "No data connections", send that file to the
maintainer; the projects themselves never need to leave the PC.

Things the reader assumes and the diagnostics will confirm:

- `.aprx` is a zip whose maps and layers are plain JSON members (UTF-8, optional BOM);
- data connections are dicts carrying `workspaceConnectionString` (+ `workspaceFactory`, `dataset`);
- the version rides that string as `VERSION=…` (and `VERSIONGUID=…`);
- the owning layer/table is the nearest ancestor with both `name` and `type`;
- a map's name is `mapDefinition.name` (or a root `CIMMap`'s `name`).

## Console fallback and development

```bat
setup (one time).bat        pip install -r requirements.txt (Python 3.11)
run app.bat                 the window, from source
scan (console).bat [folder] the same scan, printed
```

Verification (no test framework, just scripts):

```bat
python build\run_checks.py -k        the whole offline suite (scanner, writer, bridge, updater, packaging inventory)
python build\full_smoke.py           the shared self-test, incl. a hidden WebView2 window cycle
powershell -ExecutionPolicy Bypass -File build\build.ps1 -SelfTest
                                     the portable build + the EXACT exe's --self-test
```

A browser preview of the UI needs no Python: serve `scripts/ui` and open
`index.html#mock` (the `.claude/launch.json` entry does that on port 8767).

## Releasing

1. Bump `version.py`, add a `## v<x.y.z>` section to `CHANGELOG.md`, commit.
2. `git tag v<x.y.z>` and `git push origin refs/tags/v<x.y.z>`.
3. `release.yml` re-runs the checks, builds with `-SelfTest`, zips, publishes
   the zip + its `.sha256` as a GitHub release with the CHANGELOG section as
   the notes. Installed copies see it at their next start (or when the version
   chip is clicked) and update themselves.

## Repo layout

```
version.py                   app name + version (single source of truth)
scripts/
  aprx_scan.py               the reader: find files, open the zip, walk the JSON, parse connection strings
  scan_output.py             workbook + diagnostics + summary + GUI rows
  events.py paths.py settings.py logging_setup.py    the plumbing
  cli.py                     console driver
  gui_main.py gui_api.py gui_worker.py gui_win32.py  the window (pywebview / WebView2)
  updater.py                 one-click update from GitHub releases (checksum-verified, two-phase swap)
  self_test.py               the shared self-test body (frozen exe + dev)
  ui/                        index.html app.css app.js mock.js — no framework, no build step
build/                       app.spec, build.ps1, prune_bundle.ps1, the check_*.py suite, run_checks.py
.github/workflows/           checks.yml (every push), release.yml (v* tags)
```
