# Changelog

## v0.1.0 — 2026-09-04

First release.

- **Scan a folder of ArcGIS Pro projects** and list, per `.aprx`, the branch
  version(s) its layers are opened on — read straight from the project file
  (a zip of CIM JSON), so no ArcGIS Pro or arcpy is needed.
- **Excel workbook per scan** (`output\<date time>\branch_versions.xlsx`):
  Projects, Layers, Versions and Scan sheets; passwords in connection strings
  are removed before anything is written.
- **Diagnostics file** beside the workbook recording what the reader saw
  inside every file (archive members, CIM types, every connection string with
  its JSON path) — send it to the maintainer when a project reads as "no data
  connections", so the reader can be taught that layout.
- Subfolders included by default; ArcGIS Pro's `.backups` copies skipped;
  optional `.mapx` / `.lyrx` reading; OneDrive cloud-only files are downloaded
  as they are read and counted.
- **Desktop window** (Edge WebView2) with a live activity log, progress,
  results table, light/dark theme; plus a console fallback (`scan (console).bat`).
- **One-click updates** from this repo's GitHub releases (checksum-verified,
  two-phase swap, works from a user-writable folder without PowerShell).
