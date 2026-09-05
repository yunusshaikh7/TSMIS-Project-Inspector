"""One portable JSON snapshot per project folder. No database or user profile."""
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path


def folder_path(value):
    value = str(value).strip()
    if not value:
        raise ValueError("Choose a project folder.")
    return os.path.abspath(os.path.expandvars(os.path.expanduser(value)))


def folder_key(value):
    # Windows paths are case insensitive; trailing and alternate separators are equivalent.
    return hashlib.sha256(os.path.normcase(folder_path(value)).encode("utf-8")).hexdigest()


class SavedLists:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._lock = threading.RLock()

    def _read(self, path):
        record = json.loads(path.read_text(encoding="utf-8"))
        result = record.get("result")
        if (record.get("schema") != 1 or not isinstance(record.get("root"), str) or not isinstance(result, dict)
                or not result.get("complete") or not isinstance(result.get("projects"), list)
                or folder_key(record.get("root", "")) != path.stem
                or folder_key(result.get("root", "")) != path.stem):
            raise ValueError("Invalid saved list.")
        datetime.fromisoformat(record["refreshed_at"])
        for project in result["projects"]:
            if (not isinstance(project, dict)
                    or not all(isinstance(project.get(k), list) for k in ("rows", "versions", "environments", "folders", "errors"))
                    or not all(isinstance(project.get(k), str) for k in ("name", "path", "status"))
                    or not all(isinstance(row, dict) for row in project["rows"])):
                raise ValueError("Invalid saved project.")
        return record

    def load(self, root):
        with self._lock:
            path = self.directory / (folder_key(root) + ".json")
            try:
                return self._read(path)
            except FileNotFoundError:
                return None
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError("The saved list could not be read. Refresh this folder to rebuild it.") from exc

    def paths(self):
        entries = []
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    record = self._read(path)
                    entries.append({"root": record["root"], "refreshed_at": record["refreshed_at"],
                                    "projects": len(record["result"]["projects"])})
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    continue
        return sorted(entries, key=lambda item: item["refreshed_at"], reverse=True)

    def _write(self, record):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (folder_key(record["root"]) + ".json")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".saving-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
            temporary.replace(path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def save(self, result):
        if not result.get("complete"):
            raise ValueError("Only a finished refresh can replace the saved list.")
        refreshed_at = datetime.now(timezone.utc).isoformat()
        record = {"schema": 1, "root": folder_path(result["root"]),
                  "refreshed_at": refreshed_at, "result": result}
        with self._lock:
            self._write(record)
        return refreshed_at

    def clear(self, root):
        with self._lock:
            (self.directory / (folder_key(root) + ".json")).unlink(missing_ok=True)

    def copy_to(self, directory):
        target = SavedLists(directory)
        with self._lock:
            for entry in self.paths():
                target._write(self.load(entry["root"]))
