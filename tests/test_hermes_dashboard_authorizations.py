"""Focused tests for the deployable Hermes authorization dashboard bundle."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
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
        with self.assertRaises(core.AuthorizationError):
            core.valid_origin("https://lenzora.dev\r\nX-Injected: value")
        with self.assertRaises(core.AuthorizationError):
            core.valid_reason("review\tforbidden")

    def test_equivalent_pending_request_is_reused(self):
        state = core.new_state()
        catalog = {"job": {"name": "job", "kind": "agent", "enabled": True, "prompt": "safe"}}
        first, created = core.ensure_request(state, catalog, "job", "preview-overlay", "https://lenzora.dev", "safe", 60, "cron:job")
        second, repeated = core.ensure_request(state, catalog, "job", "preview-overlay", "https://lenzora.dev", "safe", 60, "cron:job")
        self.assertTrue(created)
        self.assertFalse(repeated)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(state["authorizations"]["requests"]), 1)
        with self.assertRaises(core.AuthorizationError):
            core.ensure_request(state, {}, "job", "preview-overlay", "https://lenzora.dev", "safe", 60, "cron:job")

    def test_approval_prompt_carries_the_reviewed_expiry(self):
        state = core.new_state()
        job = {"name": "job", "kind": "agent", "enabled": True, "prompt": "safe base prompt"}
        item = core.create_request(state, {"job": job}, "job", "preview-overlay", "https://lenzora.dev",
                                   "safe", 60, "operator")
        prompt = core.approval_prompt(job, item)
        self.assertIn(item["expires_at"], prompt)
        self.assertIn("at or after expiry", prompt)

    def test_expiry_rejects_malformed_timestamp(self):
        state = core.new_state()
        state["authorizations"]["requests"]["a" * 16] = {
            "id": "a" * 16, "status": "pending", "expires_at": "not-a-timestamp",
            "fingerprint": "b" * 64,
        }
        with self.assertRaises(core.AuthorizationError):
            core.expire(state)

    def test_state_rejects_malformed_record_shape(self):
        with self.assertRaises(core.AuthorizationError):
            core.normalize_state({"authorizations": {"requests": {"a" * 16: []}, "audit": []}})
        with self.assertRaises(core.AuthorizationError):
            core.normalize_state({"authorizations": {"requests": {"a" * 16: {
                "id": "a" * 16, "status": "pending", "created_at": "now",
                "expires_at": "later"}}, "audit": []}})

    def test_expiry_companion_revokes_expired_approval_and_refreshes_current_one(self):
        companion = ROOT / "sandbox/hermes/dashboard_authorizations/expire.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, catalog, config = root / "state.json", root / "catalog.json", root / "config.json"
            home, edits, hermes = root / "hermes-home", root / "edits.log", root / "hermes"
            (home / "cron").mkdir(parents=True)
            expired_job = {"name": "expired-job", "kind": "agent", "enabled": True, "prompt": "expired base prompt"}
            current_job = {"name": "current-job", "kind": "agent", "enabled": True, "prompt": "current base prompt"}
            catalog.write_text(json.dumps({"jobs": [expired_job, current_job]}))
            value = core.new_state()
            expired = core.create_request(value, {"expired-job": expired_job}, "expired-job", "preview-overlay", "https://lenzora.dev",
                                          "expired", 60, "operator")
            current = core.create_request(value, {"current-job": current_job}, "current-job", "preview-overlay", "https://lenzora.dev",
                                          "current", 60, "operator")
            expired.update({"status": "approved", "expires_at": "2000-01-01T00:00:00+00:00"})
            current.update({"status": "approved", "expires_at": "2999-01-01T00:00:00+00:00"})
            core.write_state(state, value)
            config.write_text(json.dumps({"state_path": str(state), "catalog_path": str(catalog)}))
            (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
                {"id": "deadbeef1234", "name": "expired-job", "enabled": True},
                {"id": "cafefeed1234", "name": "current-job", "enabled": True},
            ]}))
            hermes.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HERMES_EDIT_LOG\"\n")
            hermes.chmod(0o700)
            environment = {**os.environ, "SANDBOX_AUTHORIZATION_CONFIG": str(config), "HERMES_HOME": str(home),
                           "HERMES_BIN": str(hermes), "HERMES_EDIT_LOG": str(edits)}
            result = subprocess.run([sys.executable, str(companion), "--refresh"], env=environment,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"expired_count": 1, "refreshed_count": 1})
            persisted = core.read_state(state)
            self.assertEqual(persisted["authorizations"]["requests"][expired["id"]]["status"], "expired")
            self.assertIn({"request_id": expired["id"], "event": "expired", "at": persisted["authorizations"]["audit"][-1]["at"],
                           "fingerprint": expired["fingerprint"], "actor": "authorization-expiry"}, persisted["authorizations"]["audit"])
            output = edits.read_text()
            self.assertIn("expired base prompt", output)
            self.assertIn("Expires at 2999-01-01T00:00:00+00:00", output)

    def test_companion_creates_only_a_shipped_template(self):
        companion = ROOT / "sandbox/hermes/dashboard_authorizations/request.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, catalog, templates, config = root / "state.json", root / "catalog.json", root / "templates.json", root / "config.json"
            catalog.write_text(json.dumps({"jobs": [{"name": "job", "kind": "agent", "enabled": True, "prompt": "safe"}]}))
            templates.write_text(json.dumps({"templates": [{"id": "fixed", "job_name": "job", "scope": "preview-overlay", "replay_origin": "https://lenzora.dev", "rationale": "safe", "expires_in_minutes": 60}]}))
            config.write_text(json.dumps({"state_path": str(state), "catalog_path": str(catalog)}))
            environment = {**os.environ, "SANDBOX_AUTHORIZATION_CONFIG": str(config), "SANDBOX_AUTHORIZATION_TEMPLATES": str(templates)}
            first = subprocess.run([sys.executable, str(companion), "--template", "fixed"], env=environment, text=True, capture_output=True, check=False)
            second = subprocess.run([sys.executable, str(companion), "--template", "fixed"], env=environment, text=True, capture_output=True, check=False)
            denied = subprocess.run([sys.executable, str(companion), "--template", "other"], env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(json.loads(first.stdout)["created"])
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse(json.loads(second.stdout)["created"])
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(len(core.read_state(state)["authorizations"]["requests"]), 1)

    def test_expiry_companion_expires_an_approval_for_a_disabled_catalog_job(self):
        companion = ROOT / "sandbox/hermes/dashboard_authorizations/expire.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, catalog, config = root / "state.json", root / "catalog.json", root / "config.json"
            home = root / "hermes-home"
            (home / "cron").mkdir(parents=True)
            job = {"name": "disabled-job", "kind": "agent", "enabled": True, "prompt": "safe"}
            value = core.new_state()
            item = core.create_request(value, {"disabled-job": job}, "disabled-job", "preview-overlay",
                                       "https://lenzora.dev", "safe", 60, "operator")
            item["status"] = "approved"
            core.write_state(state, value)
            catalog.write_text(json.dumps({"jobs": []}))
            config.write_text(json.dumps({"state_path": str(state), "catalog_path": str(catalog)}))
            (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": []}))
            hermes = root / "hermes"
            hermes.write_text("#!/bin/sh\nexit 0\n")
            hermes.chmod(0o700)
            environment = {**os.environ, "SANDBOX_AUTHORIZATION_CONFIG": str(config), "HERMES_HOME": str(home),
                           "HERMES_BIN": str(hermes)}
            result = subprocess.run([sys.executable, str(companion), "--refresh"], env=environment,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"expired_count": 1, "refreshed_count": 0})
            self.assertEqual(core.read_state(state)["authorizations"]["requests"][item["id"]]["status"], "expired")


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
            self.assertIn("sandbox-authorizations/plugin.yaml", names)
            self.assertIn("sandbox-authorizations/__init__.py", names)
            self.assertIn("sandbox-authorizations/dashboard/manifest.json", names)
            self.assertIn("sandbox-authorizations/dashboard/plugin_api.py", names)
            self.assertIn("sandbox-authorizations/dashboard/dist/index.js", names)
            self.assertIn("sandbox-authorizations/request.py", names)
            self.assertIn("sandbox-authorizations/expire.py", names)
            self.assertIn("sandbox-authorizations/authorization-templates.json", names)
            self.assertIn("sandbox-authorizations/catalog.json", names)
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            config = json.load(bundle.extractfile("sandbox-authorizations/sandbox-authorization-config.json"))
            self.assertEqual(config["state_path"], "/home/test/runtime/hermes.json")

    def test_dashboard_bundle_has_valid_javascript_syntax(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to validate the dashboard bundle")
        bundle = ROOT / "sandbox/hermes/dashboard_authorizations/dashboard/dist/index.js"
        checked = subprocess.run([node, "--check", str(bundle)], text=True, capture_output=True, check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_dashboard_bundle_is_review_and_approve_only(self):
        bundle = (ROOT / "sandbox/hermes/dashboard_authorizations/dashboard/dist/index.js").read_text()
        self.assertNotIn("Request authorization", bundle)
        self.assertNotIn("Sync review-required output", bundle)
        self.assertIn("Review and approve", bundle)

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

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_uninstall_disables_the_plugin_before_removing_owned_files(self, get_remote, checked):
        get_remote.return_value = {"ssh": "u@example.test", "provisioned": False}
        result = hermes.dashboard_ui_action("standalone", "uninstall", confirm=True)
        self.assertEqual(result["status"], "uninstalled")
        command = checked.call_args.args[1]
        self.assertIn('"$hermes_bin" plugins disable sandbox-authorizations', command)
        self.assertIn('rm -rf "$target" "$config"', command)
        self.assertLess(command.index("plugins disable"), command.index("rm -rf"))

    @patch("sandbox.core._hermes._ssh_stdin")
    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_standalone_install_requires_catalog_and_uses_plugin_rescan(self, get_remote, checked, stdin):
        get_remote.return_value = {"ssh": "u@example.test", "provisioned": False}
        with self.assertRaises(hermes.HermesError) as missing:
            hermes.dashboard_ui_action("standalone", "install", confirm=True)
        self.assertEqual(missing.exception.code, "authorization_catalog_required")
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"schema_version": 1, "jobs": [
                {"name": "job", "kind": "agent", "enabled": True, "prompt": "safe base prompt"}
            ]}))
            stdin.return_value = type("Result", (), {"returncode": 0, "stdout": b"activation=rescanned\n", "stderr": b""})()
            result = hermes.dashboard_ui_action("standalone", "install", catalog_path=str(catalog), confirm=True)
        self.assertEqual(result["status"], "installed")
        command = stdin.call_args.args[1]
        self.assertIn('hermes_bin="${HERMES_BIN:-}"', command)
        self.assertIn('$HOME/.hermes/hermes-agent/venv/bin/hermes', command)
        self.assertIn('"$hermes_bin" dashboard --status', command)
        self.assertIn('plugins enable sandbox-authorizations --no-allow-tool-override', command)
        self.assertIn("/api/dashboard/plugins/rescan", command)
        self.assertIn("activation=restart_required", command)


class TestDashboardAuthorizationAuth(unittest.TestCase):
    def test_plugin_requires_hermes_authenticated_session(self):
        class Router:
            def get(self, *_args, **_kwargs): return lambda fn: fn
            def post(self, *_args, **_kwargs): return lambda fn: fn
        class HttpError(Exception):
            def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail
        fake_fastapi = types.ModuleType("fastapi")
        fake_fastapi.APIRouter, fake_fastapi.HTTPException, fake_fastapi.Request = Router, HttpError, object
        plugin_path = ROOT / "sandbox/hermes/dashboard_authorizations/dashboard/plugin_api.py"
        module_name = "dashboard_authorization_plugin_test"
        old_fastapi, old_module = sys.modules.get("fastapi"), sys.modules.get(module_name)
        sys.modules["fastapi"] = fake_fastapi
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            plugin = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(plugin)
            request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(auth_required=True)),
                                            state=types.SimpleNamespace(session=types.SimpleNamespace(user_id="reviewer")))
            self.assertEqual(plugin._actor(request), "reviewer")
            request.app.state.auth_required = False
            self.assertEqual(plugin._actor(request), "loopback-session")
            with tempfile.TemporaryDirectory() as directory:
                jobs = Path(directory) / "jobs.json"
                jobs.write_text(json.dumps({"jobs": [{"id": "cron-id", "name": "job", "enabled": True}]}))
                plugin.CRON_JOBS = jobs
                self.assertEqual(plugin._cron_jobs()[0]["name"], "job")
        finally:
            sys.modules.pop(module_name, None)
            if old_fastapi is None:
                sys.modules.pop("fastapi", None)
            else:
                sys.modules["fastapi"] = old_fastapi
            if old_module is not None:
                sys.modules[module_name] = old_module


if __name__ == "__main__":
    unittest.main()
