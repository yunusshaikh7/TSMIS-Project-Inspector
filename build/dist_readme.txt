TSMIS Branch Identifier
=======================

WHAT IT DOES
  Scans a folder of ArcGIS Pro projects (.aprx files) and lists, for each one,
  the TSMIS branch version its layers are opened on. Results go into an Excel
  workbook (Projects / Layers / Versions sheets) plus a diagnostics file.

  It reads the project files directly -- an .aprx is a zip of JSON documents,
  and every layer's data connection names the version it uses -- so it needs
  neither ArcGIS Pro nor arcpy to be running. Projects are only read, never
  changed.

HOW TO RUN
  Double-click  "TSMIS Branch Identifier.exe"  in this folder.
  Keep this whole folder together -- the app needs the "_internal" folder next
  to the .exe. Nothing to install; you don't need Python.

  1. Pick the folder that holds the projects (Browse..., or type the path).
     Subfolders are included by default; the .backups folders ArcGIS Pro
     creates are skipped.
  2. Click Scan. Each file is listed as it is read.
  3. Open the workbook. Every scan is saved under  "output\<date time>\"  next
     to this app: branch_versions.xlsx and diagnostics.json.

  OneDrive: files that are "cloud-only" (not yet on this PC) are downloaded as
  they are read, so a first scan of a OneDrive folder can take a while.

GOOD TO KNOW
  * A project with "No version found" has layers, but none of their
    connections names a version (file geodatabases, shapefiles, or services
    opened without a version).
  * "No data connections" means nothing inside the file looked like a data
    connection -- send the diagnostics.json to the maintainer so the reader can
    be taught that project's layout.
  * The first time you run it, Windows may say the publisher is unknown. That's
    expected for an in-house, unsigned tool: choose "More info" -> "Run anyway".
  * If you received this as a .zip, right-click the zip -> Properties -> tick
    "Unblock" -> OK, BEFORE extracting it.
  * Updates: the app checks GitHub for a newer release when it starts and shows
    an "Update to v..." button in the title bar. It only ever downloads from the
    project's own public releases, verifies the download's checksum, and
    replaces only its own program files.
  * Logs are under  "data\logs"  (Settings -> Open logs folder). Include them if
    you report a problem.

FOR IT
  No installer, no admin rights, no services, no registry changes; runs as the
  signed-in user from this folder. Network: only api.github.com and GitHub's
  release downloads, for the update check. Files: reads the project files you
  point it at; writes only under this folder (output\ and data\). The interface
  is Microsoft Edge WebView2, which ships with Windows 10/11.
