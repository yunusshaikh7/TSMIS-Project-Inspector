# Changelog

## v0.1.1 — 2026-09-04

A slimmer bundle, no behavior change. The zip went from 479 files to a fraction
of that by dropping what the app never loads on Windows 10/11:

- ~250 loose `.py` source files that duplicated the compiled archive
  (`collect_all` was shipping them alongside).
- pythonnet's 95 `System.*.dll` / `netstandard.dll` facade assemblies — shims
  for .NET Framework older than 4.7.2, which pythonnet itself already requires
  and which every Windows 10/11 provides in-box.
- The 41 Universal C Runtime forwarder DLLs + `ucrtbase.dll` (part of Windows
  10+ itself; bundled for Windows 7), the legacy MSHTML interop, the Android
  jar, the 32-bit / ARM WebView2 loaders, debug symbols, and the bz2/lzma
  modules an `.aprx` (a deflate zip) never needs.
- The prune now fails the build if a load-bearing file goes missing, and the
  frozen exe's own self-test (window included) gates every release.

## v0.1.0 — 2026-09-04

First release.

- **Scan a folder of ArcGIS Pro projects** and list, per `.aprx`, the branch
  version(s) its layers are opened on and the **environment** they come from —
  read straight from the project file (a zip of CIM JSON), so no ArcGIS Pro or
  arcpy is needed. The environment is read off the feature-service host
  (`rhapps-prod` / `-dev` / `-test`), with the ArcGIS Server folder and service
  name kept alongside.
- **Excel workbook per scan** (`output\<date time>\branch_versions.xlsx`):
  Projects, Layers, Versions (keyed by environment + version) and Scan sheets;
  passwords in connection strings are removed before anything is written.
- **Diagnostics bundle** (Settings → *Save diagnostics bundle…*): one zip for
  the maintainer holding every project's structure (JSON, passwords removed),
  the results workbook and the app log — what a first run on a real project
  library needs to confirm or correct the reader. The console flow has the
  same as `--bundle`.
- Bare-bones window: folder + Scan on top, the results table as the main
  view, the activity log behind a title-bar button, settings behind the gear.
- Subfolders included by default; ArcGIS Pro's `.backups` copies skipped;
  optional `.mapx` / `.lyrx` reading; OneDrive cloud-only files are downloaded
  as they are read and counted.
- **One-click updates** from this repo's GitHub releases (checksum-verified,
  two-phase swap, works from a user-writable folder without PowerShell).
