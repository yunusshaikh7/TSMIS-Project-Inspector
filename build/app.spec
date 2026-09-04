# PyInstaller spec for TSMIS Branch Identifier (portable onefolder).
#
# Driven by build\build.ps1, which sets:
#   TSMIS_ENTRY     path to the entry-point .py (scripts\gui_main.py)
#   TSMIS_APP_NAME  output folder / exe name
#   TSMIS_CONSOLE   "1" for a console window, "0" for the windowed GUI app
#
# The exe carries a version-info resource (from version.py), an icon and a
# manifest (asInvoker), and is NOT UPX-packed — trust signals that reduce
# Defender / SmartScreen false positives on an unsigned build.
import os
import sys
from PyInstaller.utils.hooks import collect_all

ENTRY = os.environ.get("TSMIS_ENTRY", os.path.join(SPECPATH, "..", "scripts", "gui_main.py"))
APP_NAME = os.environ.get("TSMIS_APP_NAME", "TSMIS Branch Identifier")
CONSOLE = os.environ.get("TSMIS_CONSOLE", "0") == "1"

REPO_ROOT = os.path.dirname(SPECPATH)
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, REPO_ROOT)
from version import __version__ as APP_VERSION       # noqa: E402

_parts = (APP_VERSION.split(".") + ["0", "0", "0", "0"])[:4]
_vtuple = tuple(int(p) if p.isdigit() else 0 for p in _parts)

from PyInstaller.utils.win32.versioninfo import (   # noqa: E402
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_vtuple, prodvers=_vtuple, mask=0x3F, flags=0x0,
                      OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "TSMIS Branch Identifier"),
            StringStruct("FileDescription", "Lists the TSMIS branch version each ArcGIS Pro project uses"),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("InternalName", APP_NAME),
            StringStruct("LegalCopyright", "Internal tool. Provided as-is, no warranty."),
            StringStruct("OriginalFilename", APP_NAME + ".exe"),
            StringStruct("ProductName", "TSMIS Branch Identifier"),
            StringStruct("ProductVersion", APP_VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

ICON = os.path.join(SPECPATH, "app.ico")
MANIFEST = os.path.join(SPECPATH, "app.manifest")

# Every flat scripts/ module (several are imported lazily); build/check_app_modules.py
# keeps this list complete.
APP_MODULES = [
    "version", "paths", "settings", "logging_setup", "events", "aprx_scan", "scan_output",
    "updater", "cli", "gui_win32", "gui_worker", "gui_api", "gui_main", "self_test",
]

datas, binaries, hiddenimports = [], [], list(APP_MODULES)
if os.path.exists(ICON):
    datas += [(ICON, ".")]

# The web assets ship as data files under _internal/ui (gui_api resolves them
# via sys._MEIPASS). Extension allowlist so a stray editor backup never rides in.
UI_DIR = os.path.join(SCRIPTS, "ui")
_UI_ASSET_EXTS = {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"}
datas += [(os.path.join(UI_DIR, f), "ui") for f in os.listdir(UI_DIR)
          if os.path.isfile(os.path.join(UI_DIR, f))
          and os.path.splitext(f)[1].lower() in _UI_ASSET_EXTS]

# pywebview's Windows backend needs its package DATA (Python.Runtime.dll, the
# ClrLoader natives, the WebView2 assemblies, pywebview's js/). Never the .py
# files: those are already compiled into the archive, and collect_all's default
# (include_py_files=True) shipped ~250 loose duplicates. openpyxl needs nothing
# beyond normal analysis + its contrib hook. prune_bundle.ps1 then drops the
# pieces this app never loads on Windows 10/11.
for _pkg in ("webview", "pythonnet", "clr_loader"):
    _d, _b, _h = collect_all(_pkg, include_py_files=False)
    datas += _d; binaries += _b; hiddenimports += _h
hiddenimports += ["clr"]

# No Tk (the UI is WebView2); no bz2/lzma (an .aprx is a deflate zip, and
# zipfile/shutil tolerate their absence).
EXCLUDES = ["tkinter", "_tkinter", "bz2", "_bz2", "lzma", "_lzma"]
_excl = set(EXCLUDES)
hiddenimports = [h for h in hiddenimports if h.split(".")[0] not in _excl]

a = Analysis([ENTRY], pathex=[SCRIPTS, REPO_ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=CONSOLE,
          disable_windowed_traceback=False, icon=(ICON if os.path.exists(ICON) else None),
          version=VERSION_INFO, manifest=(MANIFEST if os.path.exists(MANIFEST) else None))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name=APP_NAME)
