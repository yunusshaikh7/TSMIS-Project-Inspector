"""Desktop paths, ArcGIS Python discovery, and an isolated read-only worker."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def assets():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def data_dir():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = base / "TSMIS Branch Identifier"
    path.mkdir(parents=True, exist_ok=True)
    # Carry preferences over from the earlier preview build.
    previous = base / "TSMIS Branch Identifier Codex" / "settings.json"
    settings = path / "settings.json"
    if not settings.exists() and previous.is_file():
        try:
            shutil.copyfile(previous, settings)
        except OSError:
            pass
    return path


def default_folder():
    documents = Path.home() / "Documents"
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                documents = Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
        except OSError:
            pass
    return str(documents / "ArcGIS")


def find_arcgis_python():
    roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ArcGIS" / "Pro",
             Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "ArcGIS" / "Pro"]
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\ESRI\ArcGISPro", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                    roots.insert(0, Path(winreg.QueryValueEx(key, "InstallDir")[0]))
            except OSError:
                pass
    for root in roots:
        candidate = root / "bin" / "Python" / "envs" / "arcgispro-py3" / "python.exe"
        if candidate.is_file():
            return str(candidate)
    return ""


def worker_environment(python):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "_PYI"))}
    prefix = Path(python).parent
    paths = [prefix, prefix / "Library" / "bin", prefix / "Scripts"]
    # Direct python.exe avoids requiring a shell on restricted work PCs.
    # The environment's ArcGISPro.pth supplies ArcPy; PATH supplies native DLLs.
    for p in prefix.parents:
        if p.name.lower() == "pro" and (p / "bin").is_dir():
            paths.append(p / "bin")
            break
    inherited = env.get("PATH", "").split(os.pathsep)
    if getattr(sys, "frozen", False):
        inherited = [p for p in inherited if p and not Path(p).is_relative_to(assets())]
    env["PATH"] = os.pathsep.join(str(p) for p in paths) + os.pathsep + os.pathsep.join(inherited)
    env["CONDA_PREFIX"] = str(prefix)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env



_spawn_lock = threading.Lock()


def start_external(command, **kwargs):
    # Reset PyInstaller's inherited DLL directory so ArcGIS loads its own DLLs.
    with _spawn_lock:
        if os.name == "nt" and getattr(sys, "frozen", False):
            import ctypes
            kernel = ctypes.windll.kernel32
            old = ctypes.create_unicode_buffer(32768)
            kernel.GetDllDirectoryW(len(old), old)
            kernel.SetDllDirectoryW(None)
            try:
                return subprocess.Popen(command, **kwargs)
            finally:
                kernel.SetDllDirectoryW(old.value or None)
        return subprocess.Popen(command, **kwargs)


class ScanRunner:
    def __init__(self):
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.process = None
        self.state = {"running": False, "message": "Ready to scan", "total": 0, "completed": 0,
                      "error": "", "result": None}

    def snapshot(self):
        with self.lock:
            # Poll summaries; layer detail is fetched only when a project is selected.
            state = {k: v for k, v in self.state.items() if k != "result"}
            result = self.state["result"]
            state["projects"] = [{k: v for k, v in p.items() if k not in {"rows", "connection_metadata"}}
                                  for p in result["projects"]] if result else []
            state["warnings"] = result.get("warnings", []) if result else []
            state["diagnostic_scan"] = bool(result and result.get("diagnostic_scan"))
            return state

    def start(self, python, request):
        with self.lock:
            if self.state["running"]:
                raise ValueError("A scan is already running.")
            if not Path(request["root"]).is_dir():
                raise ValueError("Choose an existing folder containing your ArcGIS Pro projects.")
            if not python or not Path(python).is_file():
                raise ValueError("ArcGIS Pro was not found on this PC. On the work PC, open Settings to choose ArcGIS Pro's python.exe if it isn't detected.")
            if not request.get("match", "").strip():
                raise ValueError("Enter the TSMIS identifier in Settings (usually tsmis).")
            self.cancelled.clear()
            self.state.update(running=True, message="Finding projects…", total=0, completed=0, error="",
                              result={**request, "python_executable": python, "projects": [], "warnings": [], "complete": False})
            threading.Thread(target=self._run, args=(python, request), daemon=True).start()

    def stop(self):
        self.cancelled.set()

    def _event(self, event):
        with self.lock:
            result = self.state["result"]
            kind = event.get("type")
            if kind == "begin":
                result.update(event["metadata"])
            elif kind == "discovered":
                self.state["total"] = event["total"]
                result["warnings"] = event["warnings"]
                result["projects_found"] = event["total"]
            elif kind == "progress":
                self.state["message"] = event["message"]
            elif kind == "project":
                result["projects"].append(event["project"])
                self.state["completed"] = len(result["projects"])
            elif kind == "runtime":
                result["arcgis_version"] = event["arcgis_version"]
            elif kind == "done":
                result.update(complete=True, arcgis_version=event["arcgis_version"])
                self.state["message"] = "Scan complete" if self.state["total"] else "No .aprx projects found in this folder"
            elif kind == "fatal":
                self.state["error"] = event["message"]
                result["error"] = event["message"]

    def _run(self, python, request):
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="tsmis-scan-") as temp:
                folder = Path(temp)
                request_path, event_path = folder / "request.json", folder / "events.jsonl"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                event_path.touch()
                script = assets() / "worker" / "worker.py" if getattr(sys, "frozen", False) else assets() / "worker.py"
                process = start_external([python, "-u", str(script), "--request", str(request_path), "--events", str(event_path)],
                                           cwd=script.parent, env=worker_environment(python), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.process = process
                started = last_event = time.monotonic()
                with event_path.open(encoding="utf-8") as stream:
                    while True:
                        if self.cancelled.is_set():
                            process.terminate()
                            with self.lock:
                                self.state["message"] = "Scan stopped. Completed projects are available."
                                self.state["result"]["cancelled"] = True
                            break
                        position = stream.tell()
                        line = stream.readline()
                        if line and line.endswith("\n"):
                            self._event(json.loads(line))
                            last_event = time.monotonic()
                            continue
                        stream.seek(position)
                        if process.poll() is not None:
                            # A final read closes the race between EOF and worker exit.
                            for tail in stream:
                                if tail.strip():
                                    self._event(json.loads(tail))
                            break
                        if time.monotonic() - last_event > 300:
                            process.terminate()
                            raise RuntimeError("ArcGIS did not respond for five minutes. Completed projects are available; check the last project and retry.")
                        if time.monotonic() - started > 25 and self.state["completed"] == 0:
                            with self.lock:
                                self.state["message"] = "Waiting for ArcGIS Pro… You can stop the scan if it cannot sign in or read a project."
                        time.sleep(0.12)
                process.wait(timeout=10)
                with self.lock:
                    if not self.state["result"]["complete"] and not self.state["error"] and not self.cancelled.is_set():
                        self.state["error"] = "ArcGIS Python stopped before finishing. Open ArcGIS Pro and sign in, then verify its Python environment in Settings."
        except Exception as exc:
            with self.lock:
                self.state["error"] = str(exc) if isinstance(exc, RuntimeError) else "Could not run the ArcGIS reader (" + type(exc).__name__ + "). Check the Python path and folder permissions."
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            self.process = None
            with self.lock:
                self.state["running"] = False
                self.state["result"]["error"] = self.state["error"]
                self.state["result"]["projects_read"] = self.state["completed"]
