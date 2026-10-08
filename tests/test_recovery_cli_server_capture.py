"""CLI and MCP surfaces for server-first recovery capture (spec 058)."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from sandbox.commands import recovery as recovery_cli
from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.server_capture import ServerCaptureService
from sandbox.recovery.server_capture import slot_for
from sandbox.recovery.service import RecoveryService
from tests.server_capture_support import FakeTransport, fake_facts


ROOT = Path(__file__).resolve().parents[1]
REMOTE = "fixture-remote"
PROFILES = ["amarsonar-bangla-prod"]


def _wire(payload: dict) -> dict:
    """Normalize the Python MCP result to the JSON wire value."""
    return json.loads(json.dumps(payload, sort_keys=True))


class TestRecoveryCliServerCapture(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="sandbox-recovery-cli-")
        catalog = load_catalog(ROOT / "config" / "recovery-profiles.json")
        self.transport = FakeTransport()
        self.transport.add(fake_facts("status-a", remote=REMOTE))
        self.transport.add(fake_facts("failed-a", remote=REMOTE, state="failed",
                                      reason="dump_failed"))
        self.server_capture = ServerCaptureService(
            catalog, self.transport,
            environment={"SANDBOX_RECOVERY_DB_PASSWORD": "synthetic-db-password"},
            config={}, clock=lambda: 20_000.0, state_root=Path(self._temporary.name),
        )
        self.service = RecoveryService(catalog, server_capture=self.server_capture)
        self.parser = argparse.ArgumentParser()
        recovery_cli.configure_recovery(self.parser)
        self.factory_destinations = []

        app_stub = types.ModuleType("app")
        app_stub.SANDBOX_ROOT = str(ROOT)
        app_stub.mcp = types.SimpleNamespace(tool=lambda: (lambda function: function))
        with patch.dict(sys.modules, {"app": app_stub}):
            spec = importlib.util.spec_from_file_location(
                "server_capture_mcp_recovery_test",
                ROOT / "mcp" / "wp-server" / "tools" / "recovery.py",
            )
            self.mcp_recovery = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.mcp_recovery)
        self.mcp_recovery._service = self._service_factory

    def tearDown(self):
        self._temporary.cleanup()

    def _service_factory(self, *args, destination=None):
        if destination is None:
            if len(args) == 1:
                destination = args[0]
            elif len(args) > 1:
                destination = args[1]
        self.factory_destinations.append(destination)
        return self.service

    def _cli(self, argv, *, allow_error=False):
        args = self.parser.parse_args(argv)
        output = io.StringIO()
        with patch.object(recovery_cli, "recovery_service", side_effect=self._service_factory), \
                contextlib.redirect_stdout(output):
            try:
                recovery_cli.cmd_recovery(None, args)
            except SystemExit as error:
                if not allow_error or error.code != 1:
                    raise
        rendered = output.getvalue()
        payload = json.loads(rendered) if "--json" in argv else None
        return payload, rendered

    def test_capture_requires_confirmation_and_matches_mcp(self):
        args = ["capture", "--remote", REMOTE, "--backup-id", "capture-a",
                "--profile", PROFILES[0]]
        cli_result, _ = self._cli(args + ["--json"], allow_error=True)
        mcp_result = _wire(self.mcp_recovery.recovery_capture(
            REMOTE, "capture-a", PROFILES, False))
        self.assertEqual(cli_result, mcp_result)
        self.assertEqual(cli_result["error"]["code"], "confirmation_required")
        self.assertEqual(self.transport.started, [])

    def test_capture_start_matches_mcp_and_human_output_identifies_the_capture(self):
        args = ["capture", "--remote", REMOTE, "--backup-id", "capture-a",
                "--profile", PROFILES[0], "--confirm"]
        cli_result, _ = self._cli(args + ["--json"])
        mcp_result = _wire(self.mcp_recovery.recovery_capture(
            REMOTE, "capture-a", PROFILES, True))
        self.assertEqual(cli_result, mcp_result)
        self.assertEqual(cli_result["data"]["state"], "queued")

        _payload, human = self._cli(args)
        self.assertIn("backup_id: capture-a", human)
        self.assertIn(f"request_id: {cli_result['data']['request_id']}", human)
        self.assertIn("state: queued", human)

    def test_status_matches_mcp_without_a_revision_check(self):
        cli_result, _ = self._cli(
            ["status", "--remote", REMOTE, "--backup-id", "status-a", "--json"])
        mcp_result = _wire(self.mcp_recovery.recovery_capture_status(REMOTE, "status-a"))
        self.assertEqual(cli_result, mcp_result)
        self.assertEqual(cli_result["data"]["state"], "complete")
        self.assertEqual(cli_result["data"]["archive"]["size"], 13)

    def test_human_status_renders_only_bounded_public_detail(self):
        facts = self.transport.slots[slot_for(REMOTE, "status-a")]
        facts["state"]["detail"] = {
            "need_bytes": 100, "available_bytes": 90, "shortfall_bytes": 10,
            "missing_from_dump": ["wp_options"], "not_in_inventory": ["wp_posts"],
            "controller_path": "/private/controller.log",
        }
        _payload, human = self._cli(
            ["status", "--remote", REMOTE, "--backup-id", "status-a"])
        self.assertIn("need_bytes: 100", human)
        self.assertIn("shortfall_bytes: 10", human)
        self.assertIn("missing_from_dump: ['wp_options']", human)
        self.assertNotIn("controller_path", human)
        self.assertNotIn("/private/controller.log", human)

    def test_promote_confirmation_and_destination_match_mcp(self):
        destination = "reviewed-test:recovery"
        cli_result, _ = self._cli(
            ["promote", "--remote", REMOTE, "--backup-id", "status-a",
             "--destination", destination, "--json"], allow_error=True)
        mcp_result = _wire(self.mcp_recovery.recovery_promote(
            REMOTE, "status-a", destination, False))
        self.assertEqual(cli_result, mcp_result)
        self.assertEqual(cli_result["error"]["code"], "confirmation_required")
        self.assertEqual(self.factory_destinations[-1], destination)

    def test_postgres_missing_source_message_is_fixed_and_private(self):
        from sandbox.recovery.errors import RecoveryError
        from sandbox.recovery.postgres import PostgresRecovery
        from sandbox.transports.remote_postgres_recovery import RegisteredPostgresRecoveryTransport

        args = self.parser.parse_args([
            "postgres", "--postgres-operation", "readiness", "--remote", REMOTE,
            "--profile", "production-postgres",
        ])
        with patch.object(RegisteredPostgresRecoveryTransport, "__init__", return_value=None), \
             patch.object(PostgresRecovery, "readiness",
                          side_effect=RecoveryError("private path /outside/source.json",
                                                    "source_binding_missing")):
            payload = recovery_cli._postgres({}, args, self.service)
        self.assertEqual(payload["error"]["code"], "source_binding_missing")
        self.assertEqual(
            payload["error"]["message"],
            "no PostgreSQL source is registered for this profile; register its source binding before retrying",
        )
        self.assertNotIn("/outside/source.json", str(payload))

    def test_remote_list_retention_plan_and_confirmed_retire_match_mcp(self):
        cli_list, _ = self._cli(["list", "--remote", REMOTE, "--json"])
        mcp_list = _wire(self.mcp_recovery.recovery_list(REMOTE))
        self.assertEqual(cli_list, mcp_list)
        self.assertEqual(cli_list["data"]["drive"], {"configured": False})
        self.assertTrue(cli_list["data"]["server_captures"])

        cli_plan, _ = self._cli(["retention", "--remote", REMOTE, "--json"])
        mcp_plan = _wire(self.mcp_recovery.recovery_retention(REMOTE, None, False))
        self.assertEqual(cli_plan, mcp_plan)
        self.assertTrue(cli_plan["data"]["requires_confirmation"])
        self.assertNotIn("retire", self.transport.calls)

        cli_retire, _ = self._cli(
            ["retention", "--remote", REMOTE, "--backup-id", "failed-a",
             "--confirm", "--json"])
        mcp_transport = FakeTransport()
        mcp_transport.add(fake_facts("failed-a", remote=REMOTE, state="failed",
                                     reason="dump_failed"))
        mcp_server_capture = ServerCaptureService(
            self.service.catalog, mcp_transport,
            environment={"SANDBOX_RECOVERY_DB_PASSWORD": "synthetic-db-password"},
            config={}, clock=lambda: 20_000.0,
            state_root=Path(self._temporary.name) / "mcp-retirement",
        )
        mcp_service = RecoveryService(self.service.catalog, server_capture=mcp_server_capture)
        self.mcp_recovery._service = lambda destination=None: mcp_service
        mcp_retire = _wire(self.mcp_recovery.recovery_retention(REMOTE, "failed-a", True))
        self.assertEqual(cli_retire, mcp_retire)
        self.assertEqual(cli_retire["status"], "retired")
        self.assertEqual(self.transport.calls.count("retire"), 1)
        self.assertEqual(mcp_transport.calls.count("retire"), 1)


if __name__ == "__main__":
    unittest.main()
