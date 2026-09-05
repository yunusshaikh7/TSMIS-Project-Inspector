import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from core import environment, export_bundle, interpret, pairs, safe_metadata, service_details, summarize
from runtime import ScanRunner, worker_environment
from worker import discover, read_project, run

URL = "https://rhapps-prod.example.org/server/rest/services/TSMIS/Roads/FeatureServer"


def connection(version="editor.Branch_A", url=URL, dataset="0"):
    return {"connection_info": {"url": url, "version": version}, "dataset": dataset, "workspace_factory": "FeatureService"}


class FakeLayer:
    def __init__(self, name, properties, version="", group=False):
        self.name = self.longName = name
        self.connectionProperties = properties
        self.isGroupLayer = group
        self.cim = SimpleNamespace(featureTable=SimpleNamespace(dataConnection=SimpleNamespace(
            workspaceConnectionString='URL=' + URL + ';VERSION=' + version + ';PASSWORD=never-export-this',
            workspaceFactory="FeatureService", dataset="0"))) if version else SimpleNamespace()

    def supports(self, prop):
        assert prop == "CONNECTIONPROPERTIES"
        return True

    def getDefinition(self, version):
        assert version in {"V2", "V3"}
        return self.cim


class ScannerTests(unittest.TestCase):
    def test_quoted_connection_values(self):
        self.assertEqual(pairs('URL=https://example.com;VERSION="user.a;b";PASSWORD="secret;123"')["version"], "user.a;b")

    def test_join_versions_stay_with_their_sources(self):
        cp = {"source": connection("editor.Prod"), "destination": connection("editor.Test", URL.replace("-prod", "-test"), "2")}
        rows = interpret(cp, {})
        self.assertEqual({(r["environment"], r["version"], r["dataset"]) for r in rows}, {("Prod", "editor.Prod", "0"), ("Test", "editor.Test", "2")})

    def test_cim_completes_missing_version_and_merges_duplicate(self):
        cim = {"featureTable": {"dataConnection": {"workspaceConnectionString": "URL=" + URL + ";VERSION=sde.DEFAULT", "dataset": "0"}}}
        rows = interpret(connection(""), cim)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version"], "sde.DEFAULT")
        rows = interpret(connection("editor.Other"), cim)
        self.assertEqual(rows[0]["status"], "Conflicting versions")
        self.assertEqual(set(rows[0]["versions"]), {"sde.DEFAULT", "editor.Other"})

    def test_missing_version_is_never_default(self):
        row = interpret(connection(""), {})[0]
        self.assertEqual(row["version"], "")
        self.assertEqual(row["status"], "Version not exposed")

    def test_environment_uses_host_before_folders_and_never_branch(self):
        self.assertEqual(environment("gis-prod.example.org", "server", "TSMIS_TEST")[0], "Prod")
        self.assertEqual(environment("gis.example.org", "server", "TSMIS_DEV")[0], "Dev")
        self.assertEqual(environment("gis-dev-test.example.org")[0], "Unknown")
        row = interpret(connection("editor.PROD", URL.replace("rhapps-prod", "gis")), {})[0]
        self.assertEqual(row["environment"], "Unknown")
        self.assertEqual(service_details(URL + "/4")["folder"], "TSMIS")

    def test_database_version_is_not_labeled_branch(self):
        row = interpret({"connection_info": {"database": "TSMIS", "version": "sde.DEFAULT"}, "dataset": "roads", "workspace_factory": "SDE"}, {})[0]
        self.assertEqual(row["version_kind"], "Database version")
        self.assertTrue(row["is_tsmis"])

    def test_direct_service_url_does_not_mistake_protocol_version(self):
        rows = interpret({}, {"URL": URL, "version": "1.3.0"})
        self.assertEqual(rows[0]["version"], "")
        self.assertTrue(rows[0]["is_tsmis"])

    def test_diagnostics_allowlist_withholds_nested_credentials(self):
        secret = "NEVER_EXPORT_VALUE"
        data = {"connection_info": {"url": "https://user:" + secret + "@example.org/TSMIS?token=" + secret + "#" + secret,
                "password": secret, "encrypted_password_utf8": secret, "customParameters": {"version": secret},
                "oauth": [{"name": secret}], "newSecretField": {"name": secret}, "version": "editor.Branch"},
                "workspaceConnectionString": 'URL=' + URL + ';PASSWORD="' + secret + ';secret";VERSION=sde.DEFAULT'}
        serialized = json.dumps(safe_metadata(data))
        self.assertNotIn(secret, serialized)
        self.assertIn("editor.Branch", serialized)
        self.assertIn("sde.DEFAULT", serialized)

    def test_project_reads_maps_groups_and_standalone_tables_without_saving(self):
        layers = [FakeLayer("Group", {}, group=True), FakeLayer("Group\\Roads", connection(""), "editor.Work")]
        tables = [FakeLayer("Lookup", connection("editor.Work"))]
        obj = SimpleNamespace(listMaps=lambda: [SimpleNamespace(name="Map A", listLayers=lambda: layers, listTables=lambda: tables)])
        arcpy = SimpleNamespace(mp=SimpleNamespace(ArcGISProject=lambda path: obj))
        project = read_project("Example.aprx", arcpy, diagnostics=True)
        self.assertEqual(project["versions"], ["editor.Work"])
        self.assertEqual(project["tsmis_connections"], 2)
        self.assertEqual(project["status"], "Identified")
        self.assertNotIn("never-export-this", json.dumps(project))
        self.assertEqual(len(project["connection_metadata"]), 2)

    def test_failed_project_does_not_abort_next_project(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "bad.aprx").touch()
            Path(folder, "good.aprx").touch()
            def open_project(path):
                if path.endswith("bad.aprx"):
                    raise ValueError("token=private")
                return SimpleNamespace(listMaps=lambda: [])
            arcpy = SimpleNamespace(GetInstallInfo=lambda: {"Version": "3.4"}, mp=SimpleNamespace(ArcGISProject=open_project))
            events = []
            run({"root": folder}, events.append, arcpy)
            projects = [e["project"] for e in events if e["type"] == "project"]
            self.assertEqual(len(projects), 2)
            self.assertEqual(projects[0]["status"], "Could not open")
            self.assertEqual(projects[1]["status"], "No TSMIS connections")
            self.assertEqual(events[-1]["type"], "done")
            self.assertNotIn("private", json.dumps(events))

    def test_discovery_recurses_and_skips_pro_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            for filename in ("one.aprx", "nested/two.APRX", ".backups/old.aprx", "db.gdb/other.aprx"):
                target = Path(folder, filename)
                target.parent.mkdir(exist_ok=True)
                target.touch()
            self.assertEqual(len(discover(folder)[0]), 2)
            self.assertEqual(len(discover(folder, False)[0]), 1)

    def test_partial_project_needs_review_and_export_marks_incomplete(self):
        project = summarize({"path": "=1+1.aprx", "rows": [dict(interpret(connection(), {})[0], error="Could not read one property")], "errors": []})
        self.assertEqual(project["status"], "Needs review")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "results.zip")
            export_bundle(path, {"complete": False, "cancelled": True, "projects": [project]}, True)
            with ZipFile(path) as archive:
                self.assertFalse(json.loads(archive.read("diagnostics.json"))["complete"])
                self.assertIn("'=1+1.aprx", archive.read("projects.csv").decode("utf-8-sig"))

    def test_actual_worker_process_handles_empty_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = ScanRunner()
            runner.start(sys.executable, {"root": folder, "recursive": True, "match": "tsmis", "diagnostics": True})
            deadline = time.monotonic() + 15
            while runner.snapshot()["running"] and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(runner.snapshot()["running"])
            self.assertEqual(runner.snapshot()["error"], "")
            self.assertTrue(runner.state["result"]["complete"])

    def test_worker_isolation_removes_parent_python_settings(self):
        with patch.dict(os.environ, {"PYTHONHOME": "wrong", "PYTHONPATH": "wrong", "_PYI_APPLICATION_HOME_DIR": "wrong"}):
            env = worker_environment(sys.executable)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)


if __name__ == "__main__":
    unittest.main()
