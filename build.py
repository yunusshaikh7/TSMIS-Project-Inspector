"""Build and test a minimal portable Windows 10/11 app; ArcGIS is not bundled."""
import hashlib
import importlib.metadata
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from runtime import interface_html
from updater import release_asset_name
from version import APP_NAME, VERSION

ROOT = Path(__file__).resolve().parent
ASSEMBLIES = ("pythonnet/runtime/Python.Runtime.dll", "webview/lib/Microsoft.Web.WebView2.Core.dll",
              "webview/lib/Microsoft.Web.WebView2.WinForms.dll")


def package_file(relative):
    package, rest = relative.split("/", 1)
    return Path(importlib.util.find_spec(package).origin).parent / rest


def tls_libraries(work):
    # Python's signed ABI-compatible security fix, pinned to its upstream commit
    # and exact bytes. Build-only download; no extra end-user dependency.
    from urllib.request import urlopen
    source = "https://raw.githubusercontent.com/python/cpython-bin-deps/763bc5ab30fe645cf70e3bacb0c8c04908a46620/amd64/"
    hashes = {"libcrypto-3.dll": "acf285a10e428256dfefc489895fa104f3a49764a16c9a760748321397bab8e4",
              "libssl-3.dll": "819b8cf3c984ab7465d32ebf85664ff66f1e74886cb1d3a6afa40dbd9cb245ba",
              "LICENSE.txt": "ed72ce2b51ee58f117e5a021e2e04af158857f40269fbc03491f0b2a99dbcc96"}
    folder = work / "openssl-3.5.8"
    folder.mkdir(exist_ok=True)
    for name, expected in hashes.items():
        path = folder / name
        if not path.is_file():
            with urlopen(source + name, timeout=30) as response:
                path.write_bytes(response.read(8 * 1024 * 1024))
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("The build's TLS library did not match its pinned hash: " + name)


def license_notices(work):
    notices = [("Python " + sys.version.split()[0], (Path(sys.base_prefix) / "LICENSE.txt").read_text(encoding="utf-8"))]
    for name in ("pywebview", "pythonnet", "clr_loader", "cffi", "pycparser", "bottle", "typing_extensions", "pyinstaller"):
        distribution = importlib.metadata.distribution(name)
        for file in distribution.files or []:
            if Path(file).name.lower() in {"license", "license.txt", "copying.txt"}:
                notices.append((name + " " + distribution.version, distribution.locate_file(file).read_text(encoding="utf-8")))
    notices.append(("proxy_tools 0.1.0", (ROOT / "licenses" / "proxy_tools.txt").read_text(encoding="utf-8")))
    notices.append(("Microsoft WebView2 SDK", (ROOT / "licenses" / "WebView2.txt").read_text(encoding="utf-8")))
    notices.append(("OpenSSL 3.5.8", (work / "openssl-3.5.8" / "LICENSE.txt").read_text(encoding="utf-8")))
    path = work / "Third Party Notices.txt"
    path.write_text("\n\n".join(name + "\n" + "=" * len(name) + "\n" + body for name, body in notices), encoding="utf-8")
    return path


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32 or sys.version_info[:3] != (3, 14, 7):
        raise SystemExit("Build on Windows with 64-bit Python 3.14.7.")
    os.chdir(ROOT)
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], check=True)
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    hashes = {p: hashlib.sha256(package_file(p).read_bytes()).hexdigest() for p in ASSEMBLIES}
    (work / "bundle_info.py").write_text("ASSEMBLIES = " + repr(hashes) + "\n", encoding="utf-8")
    tls_libraries(work)
    license_notices(work)
    # The sample data belongs only to the source-code preview, never the release.
    ui = work / "release-ui"
    ui.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "ui" / "app.ico", ui / "app.ico")
    (ui / "index.html").write_text(interface_html(ROOT / "ui"), encoding="utf-8")
    spec = work / "app.spec"
    spec.write_text('''from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
from pathlib import Path
''' + f"ROOT = {str(ROOT)!r}\nAPP_NAME = {APP_NAME!r}\nVERSION = {VERSION!r}\nASSEMBLIES = {ASSEMBLIES!r}\n" + '''
root = Path(ROOT)
work = root / "build"
datas = [(str(work / "release-ui" / name), "ui") for name in ("app.ico", "index.html")]
datas += [(str(root / f), "worker") for f in ("worker.py", "core.py", "version.py")]
datas += [(str(work / "Third Party Notices.txt"), ".")]
datas += collect_data_files("webview", includes=["js/*.js"])
datas += collect_data_files("pythonnet", includes=["runtime/Python.Runtime.dll"])
parts = tuple(int(x) for x in VERSION.split(".")) + (0,)
version_info = VSVersionInfo(ffi=FixedFileInfo(filevers=parts, prodvers=parts, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable("040904B0", [StringStruct("FileDescription", "Inspect saved TSMIS project connections"), StringStruct("ProductName", APP_NAME), StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", APP_NAME + ".exe")])]), VarFileInfo([VarStruct("Translation", [1033,1200])])])
a = Analysis([str(root / "app.py")], pathex=[ROOT, str(work)], datas=datas,
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms", "clr", "pythonnet", "bundle_info"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gi", "cefpython3", "numpy", "pandas", "matplotlib", "pytest", "IPython", "PIL", "cryptography", "OpenSSL", "setuptools", "pkg_resources", "_distutils_hack", "bz2", "lzma", "compression.zstd"], noarchive=False)
# The library hooks collect every platform and old .NET facade. Keep the Windows
# x64 Edge backend. Windows 10/11 supply UCRT and .NET Framework facade assemblies.
keep_runtime = set(ASSEMBLIES) | {"clr_loader/ffi/dlls/amd64/ClrLoader.dll", "webview/lib/runtimes/win-x64/native/WebView2Loader.dll"}
keep_runtime = {p.lower() for p in keep_runtime}
def needed(entry):
    path = entry[0].replace(chr(92), "/").lower()
    if path.startswith(("webview/lib/", "pythonnet/runtime/", "clr_loader/ffi/dlls/")):
        return path in keep_runtime
    return not (path.startswith("api-ms-win-") or path == "ucrtbase.dll")
a.binaries = [(entry[0], str(work / "openssl-3.5.8" / entry[0]), entry[2])
              if entry[0] in {"libcrypto-3.dll", "libssl-3.dll"} else entry
              for entry in a.binaries if needed(entry)]
a.datas = [entry for entry in a.datas if needed(entry)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, console=False, upx=False,
          icon=str(root / "ui" / "app.ico"), version=version_info)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)
''', encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "dist"),
                    "--workpath", str(work / "pyinstaller"), str(spec)], check=True)
    bundle = ROOT / "dist" / APP_NAME
    # pywebview resolves these directories even on x64; their unused DLLs aren't needed.
    for arch in ("win-arm64", "win-x86"):
        (bundle / "_internal" / "webview" / "lib" / "runtimes" / arch / "native").mkdir(parents=True, exist_ok=True)
    proof = work / "desktop-smoke.txt"
    proof.unlink(missing_ok=True)
    # Test a copied app with Internet-zone DLLs and no prepared user profile.
    # All mutable data stays in this disposable copy, never the release ZIP.
    with tempfile.TemporaryDirectory(prefix="package-test-", dir=work, ignore_cleanup_errors=True) as folder:
        copy = Path(folder) / APP_NAME
        shutil.copytree(bundle, copy)
        for dll in (copy / "_internal").rglob("*.dll"):
            Path(str(dll) + ":Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n", encoding="ascii")
        exe = copy / (APP_NAME + ".exe")
        env = dict(os.environ, APPDATA=str(Path(folder) / "unused-roaming-profile"),
                   LOCALAPPDATA=str(Path(folder) / "unused-profile"), PYINSTALLER_RESET_ENVIRONMENT="1")
        subprocess.run([str(exe), "--self-test"], env=env, check=True, timeout=30)
        # Exercise the real update handoff: current window -> staged helper ->
        # replacement at the original path -> successful restart -> cleanup.
        from history import SavedLists
        from installer import write_manifest
        saved_root = str(Path(folder) / "Previously scanned projects")
        store = SavedLists(copy / "Data" / "Lists")
        store.save({"root": saved_root, "complete": True, "projects": []})
        saved_file = next(store.directory.glob("*.json"))
        saved_bytes = saved_file.read_bytes()
        job = copy / "Data" / "Updates" / ("v" + VERSION) / "app-smoke"
        staged = job / APP_NAME
        shutil.copytree(bundle, staged)
        write_manifest(staged / exe.name)
        (copy / "_internal" / "obsolete-test-file.txt").write_text("Remove on update")
        initial_proof = work / "before-update-smoke.txt"
        initial_proof.unlink(missing_ok=True)
        subprocess.run([str(exe), "--smoke-test", str(initial_proof), "--test-python", sys.executable,
                        "--stage-update", str(staged / exe.name), str(proof)], env=env, check=True, timeout=60)
        assert initial_proof.read_text().startswith("PASS"), initial_proof.read_text()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and (not proof.exists() or job.exists()):
            error = job / "error"
            if error.exists():
                raise RuntimeError(error.read_text(encoding="utf-8"))
            time.sleep(0.2)
        assert not job.exists(), "Successful update must remove staging and rollback copies"
        assert not (copy / "_internal" / "obsolete-test-file.txt").exists(), "Update must remove obsolete support files"
        assert saved_file.read_bytes() == saved_bytes, "Saved lists must stay unchanged during replacement"
        if not proof.is_file() or not proof.read_text(encoding="utf-8").startswith("PASS"):
            raise SystemExit("Desktop smoke failed: " + (proof.read_text(encoding="utf-8") if proof.exists() else "no result"))
        assert (copy / "Data" / "settings.json").is_file(), "Settings must stay beside the app"
        assert not (Path(folder) / "unused-profile").exists(), "App must not write to Local AppData"
        assert not (Path(folder) / "unused-roaming-profile").exists(), "App must not write to Roaming AppData"
        assert not list(copy.rglob("__pycache__")), "Worker must not write into app support files"
    files = [p for p in bundle.rglob("*") if p.is_file()]
    for file in files:
        relative = file.relative_to(bundle)
        assert not any(part.lower() in {"data", "tests", "__pycache__"} or part.endswith(".dist-info") for part in relative.parts), relative
        assert file.suffix.lower() not in {".md", ".rst", ".pdb", ".pyi"} and file.name != "demo.js", relative
        assert file.suffix.lower() != ".txt" or relative.as_posix() == "_internal/Third Party Notices.txt", relative
    (work / "file-inventory.txt").write_text("\n".join(f"{p.stat().st_size:>10}  {p.relative_to(bundle)}" for p in files), encoding="utf-8")
    name = release_asset_name(VERSION)
    path = Path(shutil.make_archive(str(ROOT / "dist" / name[:-4]), "zip", ROOT / "dist", APP_NAME))
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(checksum + "  " + name + "\n", encoding="ascii")
    print(f"Ready: {path} ({path.stat().st_size:,} bytes; {len(files)} files; {sum(p.stat().st_size for p in files):,} bytes extracted)")
    print(proof.read_text(encoding="utf-8"))
    print("PASS: packaged in-place update, restart at the original path, saved-list preservation, and staging cleanup.")


if __name__ == "__main__":
    main()
