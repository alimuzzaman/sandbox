"""Focused tests for the deployable Hermes authorization dashboard bundle."""
from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.core import _hermes as hermes


ROOT = Path(__file__).resolve().parent.parent
CORE_PATH = ROOT / "sandbox/hermes/dashboard_authorizations/authorization_core.py"
SPEC = importlib.util.spec_from_file_location("dashboard_authorization_core", CORE_PATH)
core = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(core)


class TestDashboardAuthorizationCore(unittest.TestCase):
    def test_request_is_scoped_audited_and_persisted_atomically(self):
        state = core.new_state()
        catalog = {"job": {"name": "job", "kind": "agent", "enabled": True, "prompt": "safe"}}
        item = core.create_request(state, catalog, "job", "preview-overlay", "https://Lenzora.dev/",
                                   "dev-only test", 60, "operator")
        self.assertEqual(item["replay_origin"], "https://lenzora.dev")
        self.assertEqual(state["authorizations"]["audit"][-1]["actor"], "operator")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            core.write_state(path, state)
            self.assertEqual(core.read_state(path)["authorizations"]["requests"][item["id"]]["scope"], "preview-overlay")

    def test_invalid_secret_origin_and_uncataloged_job_are_rejected(self):
        state = core.new_state()
        catalog = {"job": {"name": "job", "kind": "agent", "enabled": True, "prompt": "safe"}}
        with self.assertRaises(core.AuthorizationError):
            core.create_request(state, catalog, "job", "preview-overlay", "https://lenzora.dev/path", "safe", 60, "op")
        with self.assertRaises(core.AuthorizationError):
            core.create_request(state, catalog, "job", "preview-overlay", "https://lenzora.dev", "token=not-safe", 60, "op")
        with self.assertRaises(core.AuthorizationError):
            core.create_request(state, catalog, "other", "preview-overlay", "https://lenzora.dev", "safe", 60, "op")


class TestDashboardAuthorizationInstaller(unittest.TestCase):
    def test_default_catalog_contains_only_enabled_agent_jobs(self):
        catalog = hermes._dashboard_authorization_catalog()
        source = {item.name for item in __import__("sandbox.hermes.scheduler", fromlist=["load_catalog"]).load_catalog()["jobs"]
                  if item.kind == "agent" and item.enabled}
        self.assertEqual({item["name"] for item in catalog["jobs"]}, source)

    def test_custom_catalog_rejects_duplicate_or_secret_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({"schema_version": 1, "jobs": [
                {"name": "same", "kind": "agent", "enabled": True, "prompt": "safe"},
                {"name": "same", "kind": "agent", "enabled": True, "prompt": "token=not-safe"},
            ]}))
            with self.assertRaises(hermes.HermesError):
                hermes._dashboard_authorization_catalog(str(path))

    def test_archive_has_manifest_router_and_generated_catalog(self):
        catalog = hermes._dashboard_authorization_catalog()
        archive = hermes._dashboard_authorization_archive(catalog, "/home/test/runtime/hermes.json")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            names = set(bundle.getnames())
            self.assertIn("sandbox-authorizations/dashboard/manifest.json", names)
            self.assertIn("sandbox-authorizations/dashboard/plugin_api.py", names)
            self.assertIn("sandbox-authorizations/catalog.json", names)
            config = json.load(bundle.extractfile("sandbox-authorizations/sandbox-authorization-config.json"))
            self.assertEqual(config["state_path"], "/home/test/runtime/hermes.json")

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_status_is_read_only_and_reports_missing_plugin(self, get_remote, paths, ssh):
        get_remote.return_value = {"ssh": "u@example.test"}
        paths.return_value = {"state": "/home/u/runtime/hermes.json"}
        ssh.return_value = type("Result", (), {"returncode": 1})()
        result = hermes.dashboard_ui_action("test", "status")
        self.assertEqual(result["status"], "not_installed")
        self.assertEqual(ssh.call_count, 1)

    def test_install_and_uninstall_require_confirmation(self):
        with self.assertRaises(hermes.HermesError) as install:
            hermes.dashboard_ui_action("test", "install")
        self.assertEqual(install.exception.code, "confirmation_required")
        with self.assertRaises(hermes.HermesError) as uninstall:
            hermes.dashboard_ui_action("test", "uninstall")
        self.assertEqual(uninstall.exception.code, "confirmation_required")


if __name__ == "__main__":
    unittest.main()
