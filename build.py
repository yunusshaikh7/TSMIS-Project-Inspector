"""Build a portable Windows folder and release ZIP; no ArcGIS in the bundle."""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from updater import release_asset_name
from version import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != "win32":
        raise SystemExit("Build this Windows app on Windows with Python 3.11.")
    os.chdir(ROOT)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], check=True)
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    spec = work / "app.spec"
    spec.write_text('''from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
''' + f"ROOT = {str(ROOT)!r}\nAPP_NAME = {APP_NAME!r}\nVERSION = {VERSION!r}\n" + '''
from pathlib import Path
root = Path(ROOT)
datas = [(str(root / "ui"), "ui")]
datas += [(str(root / f), "worker") for f in ("worker.py", "core.py", "version.py")]
binaries = []
for package in ("webview", "pythonnet", "clr_loader"):
    datas += collect_data_files(package)
    binaries += collect_dynamic_libs(package)
parts = tuple(int(x) for x in VERSION.split(".")) + (0,)
version_info = VSVersionInfo(ffi=FixedFileInfo(filevers=parts, prodvers=parts, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable("040904B0", [StringStruct("FileDescription", "Inspect saved TSMIS project connections"), StringStruct("ProductName", APP_NAME), StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", APP_NAME + ".exe")])]), VarFileInfo([VarStruct("Translation", [1033,1200])])])
a = Analysis([str(root / "app.py")], pathex=[ROOT], binaries=binaries, datas=datas,
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms", "clr", "pythonnet"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi", "cefpython3", "numpy", "pandas", "matplotlib", "pytest", "IPython", "PIL"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, console=False, upx=False, version=version_info)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)
''', encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(ROOT / "dist"),
                    "--workpath", str(work / "pyinstaller"), str(spec)], check=True)
    bundle = ROOT / "dist" / APP_NAME
    shutil.copy2(ROOT / "Start Here.txt", bundle / "Start Here.txt")
    exe = bundle / (APP_NAME + ".exe")
    subprocess.run([str(exe), "--self-test"], check=True, timeout=30)
    # Keep the build's smoke settings inside the workspace, separate from users.
    proof = work / "desktop-smoke.txt"
    proof.unlink(missing_ok=True)
    env = dict(os.environ, LOCALAPPDATA=str(work / "smoke-profile"), PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.run([str(exe), "--smoke-test", str(proof), "--test-python", sys.executable], env=env, check=True, timeout=60)
    if not proof.is_file() or proof.read_text(encoding="utf-8").startswith("FAILED"):
        raise SystemExit("Desktop smoke failed: " + (proof.read_text(encoding="utf-8") if proof.exists() else "no result"))
    name = release_asset_name(VERSION)
    path = Path(shutil.make_archive(str(ROOT / "dist" / name[:-4]), "zip", ROOT / "dist", APP_NAME))
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(checksum + "  " + name + "\n", encoding="ascii")
    print("Ready:", path)
    print(proof.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
