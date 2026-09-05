# TSMIS Branch Identifier

[Download the Windows app](https://github.com/yunusshaikh7/TSMIS-Branch-Identifier/releases/latest)

A small Windows app that finds the saved TSMIS versions and environments in
ArcGIS Pro projects under a chosen folder. Python + a local HTML interface in
WebView2, packaged as a portable folder. One runtime dependency: `pywebview`.

## First work-PC test

Copy the extracted **TSMIS Branch Identifier** folder from `dist` to the work PC
and open **TSMIS Branch Identifier.exe**. Select any project folder; the default
is the Windows Documents folder (including redirected OneDrive Documents)
followed by `ArcGIS`. Subfolders are included by default; `.backups`, `.git` and
geodatabase folders are skipped.

Click **Settings → Test / diagnostics → Run diagnostic scan**, then **Settings → Test / diagnostics →
Save diagnostic ZIP**. Bring back that ZIP to confirm the real environment and
branch naming. It includes project and layer CSVs, limited connection metadata,
and read errors. It includes internal server names, local paths and branch owner
names. Unknown and credential property values are withheld. It does not include
project files, complete layer definitions, feature data, passwords or URL tokens.

**Real ArcGIS integration has not been tested on the development PC, which has
no ArcGIS Pro.** Offline tests exercise extraction, grouping, failures, exports,
worker execution, and update validation; they do not establish real-project
compatibility. The first diagnostic run is the integration test.

## What it reports

- One project summary with saved versions, Dev / Test / Prod, service folders,
  and a status. Select a project for individual layer and standalone table
  connections, including both sides of joins. The layer path preserves map
  group names. Service folders are separate from map groups.
- Multiple versions/environments are flagged as **Mixed connections**, which
  can be intentional. Read errors, unexposed versions and unknown environments
  are **Needs review**. A project that cannot open does not stop later projects.
- **Export** writes a ZIP containing two CSVs that open in Excel.
  Partial or cancelled scans are marked incomplete in the accompanying JSON.
- **Settings** lets you select ArcGIS Python and change the TSMIS substring used
  to match service URLs, workspace names and datasets (default `tsmis`). All
  inspected connections are available in layer details and diagnostics.

Environment inference uses whole Dev / Test / Prod tokens (and development,
production, QA, UAT synonyms) in the server hostname first, then the server site
or service folder. It never infers environment from a branch name. Unknown or
conflicting naming stays **Unknown** until the work-PC evidence establishes it.
The UI and export retain the source URL and inference evidence for review.

## ArcGIS reader

The desktop app locates ArcGIS Pro's Python through its installation registry
entry or standard installation paths, with a manual choice for cloned/custom
environments. It launches `worker.py` in that interpreter with a separate process
and its own DLL search environment. No packages are installed into ArcGIS.

The worker opens each `.aprx` using `arcpy.mp.ArcGISProject(path)` and inspects
`listMaps()`, `listLayers()` and `listTables()`. It reads `connectionProperties`
and CIM `getDefinition('V3')` (`V2` for Pro 2.x), including feature table and joined
data connections. It does not parse the undocumented APRX archive structure.
Only metadata is read: there are no `save`, `setDefinition`, geoprocessing edits,
version changes, or writes to project files. The selected files must be available
locally; OneDrive may need to hydrate them. Live server availability is not checked.

**Saved version** is deliberately precise: missing values never become
`sde.DEFAULT`, and a database connection's version is labeled **Database version**
rather than assumed to be branch versioning. The same source/dataset reported by
both APIs is merged; contradictory versions are retained and flagged.
Changes that haven't been saved in ArcGIS Pro are outside the scan.

ArcGIS Pro must be installed and licensed on the work PC. If initialization fails,
open Pro and sign in; choose your active cloned environment in Settings if needed.
The app cannot supply an ArcGIS license or bundle ArcPy. A scan can be stopped;
a worker that produces no progress for five minutes is stopped with partial
results retained.

Primary references:

- [Esri: connection properties and joined sources](https://doc.esri.com/en/arcgis-pro/latest/arcpy/mapping/updatingandfixingdatasources.html)
- [Esri: Python CIM access](https://doc.esri.com/en/arcgis-pro/latest/arcpy/mapping/python-cim-access.html)
- [Esri: stand-alone Python scripts and licensing](https://doc.esri.com/en/arcgis-pro/latest/arcpy/get-started/using-conda-with-arcgis-pro.html)
- [PyInstaller: launching external programs](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)

## Updates

**Settings → Check for updates** reads public GitHub releases from
`yunusshaikh7/TSMIS-Branch-Identifier`. **Download update** verifies its
SHA-256 checksum and extracts it under `%LOCALAPPDATA%\TSMIS Branch Identifier\updates`. **Open updated app** opens the new executable and its folder.
Use that copy on future launches; the old app remains for rollback. This avoids
file replacement while running and requires no admin, PowerShell, or batch
script on the work PC. Settings share the same Local AppData folder. Previously
downloaded app versions remain until you remove them manually.

The updater only contacts GitHub when clicked. It works once this repository has
public releases with the expected ZIP and `.sha256` assets. No release is
published just by building locally. For a private repo or blocked GitHub access,
copy a new ZIP to the work PC manually.

## Development

Python 3.11 on Windows:

```text
python -m pip install -r requirements-build.txt
python app.py
python -m unittest discover -s tests -v
python build.py
```

`build.py` runs the tests, creates the portable app, checks the packaged window
and Python bridge in a hidden WebView2 run, then writes the versioned ZIP and
checksum into `dist`. ArcPy is never packaged. The work PC does not need pip or
a separate Python installation. See **Start Here.txt** inside the release.

To view the browser-only sample interface, serve `ui` locally and open
`index.html#demo`. This explicitly labeled preview uses sample projects and never
runs as a fallback for a real scan.

Release: update `version.py` and `Start Here.txt`, commit, tag `v0.2.0` (or the
matching new version), and push that tag when ready to publish. The release
workflow builds and uploads the ZIP and checksum. Normal pushes only run tests.

The app is deliberately flat: `app.py` is the window/bridge, `runtime.py` the
worker runner, `worker.py` the ArcPy reader, `core.py` interpretation/CSV exports,
`updater.py` release downloads, and `ui/` the interface. No server, report engine,
database, login system, or frontend build tool is required.
