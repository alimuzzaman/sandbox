from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from unittest.mock import patch


class RecordingService:
    def __init__(self):
        self.calls = []

    def status(self, *, thorough, budget_seconds, progress=None):
        self.calls.append(("status", thorough, budget_seconds))
        return {
            "schema_version": 1, "ok": True, "action": "status",
            "status": "complete", "target": {"kind": "local", "name": "local"},
            "data": {
                "capacity": {"total_bytes": 10, "used_bytes": 5, "available_bytes": 5},
                "summary": {"reclaimable_bytes": 0, "unknown_bytes": 0},
                "resources": [], "category_outcomes": [],
            },
            "error": None,
        }

    def plan(self, scope, *, thorough, budget_seconds, progress=None):
        self.calls.append(("plan", scope, thorough, budget_seconds))
        return {
            "schema_version": 1, "ok": True, "action": "plan",
            "status": "planned", "target": {"kind": "local", "name": "local"},
            "data": {
                "plan_id": "a" * 32, "scope": scope,
                "estimated_reclaimable_bytes": 0, "candidates": [],
                "exclusions": [], "requires_confirmation": True,
            },
            "error": None,
        }

    def cleanup(self, plan_id, *, confirm):
        self.calls.append(("cleanup", plan_id, confirm))
        if not confirm:
            return {
                "schema_version": 1, "ok": False, "action": "cleanup",
                "status": "refused", "target": None, "data": {},
                "error": {"code": "confirmation_required", "message": "required",
                          "retryable": False},
            }
        return {
            "schema_version": 1, "ok": True, "action": "cleanup",
            "status": "completed", "target": {"kind": "local", "name": "local"},
            "data": {"plan_id": plan_id, "outcomes": []}, "error": None,
        }


class TestResourceInterfaces(unittest.TestCase):
    def parser(self):
        from sandbox.commands.resources import configure_parser
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        return parser

    def test_parser_contract(self):
        status = self.parser().parse_args(
            ["status", "--remote", "remote-a", "--thorough", "--budget", "30", "--json"],
        )
        self.assertEqual(
            (status.action, status.remote, status.thorough, status.budget, status.json),
            ("status", "remote-a", True, 30.0, True),
        )
        plan = self.parser().parse_args(["plan", "--scope", "stale"])
        self.assertEqual(plan.scope, "stale")
        cleanup = self.parser().parse_args(
            ["cleanup", "--plan-id", "a" * 32, "--confirm"],
        )
        self.assertTrue(cleanup.confirm)

    def test_cli_json_uses_shared_service_and_global_scope(self):
        from sandbox.commands import resources
        service = RecordingService()
        args = self.parser().parse_args(["status", "--json"])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output):
            resources.cmd_resources({}, args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(service.calls, [("status", False, 15.0)])

    def test_cli_refusal_emits_json_then_exits_nonzero(self):
        from sandbox.commands import resources
        service = RecordingService()
        args = self.parser().parse_args(
            ["cleanup", "--plan-id", "a" * 32, "--json"],
        )
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output), self.assertRaises(SystemExit):
            resources.cmd_resources({}, args)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "confirmation_required")

    def test_feature_command_is_manifest_owned_without_central_parser(self):
        from sandbox.commands.manifest import BUILTIN_COMMAND_MODULES, load_builtin_commands
        from sandbox.registry import COMMAND_SPECS

        load_builtin_commands()
        spec = COMMAND_SPECS.get("resources")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.scope, "global")
        self.assertIn("sandbox.commands.resources", BUILTIN_COMMAND_MODULES)
        cli = (Path(__file__).parent.parent / "sandbox" / "cli.py").read_text()
        self.assertNotIn('add_parser("resources"', cli)

    def test_mcp_adapters_use_the_same_service_semantics(self):
        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from dependencies import ToolDependencies
            from tools import resources

            service = RecordingService()

            class Server:
                def __init__(self):
                    self.names = []

                def tool(self):
                    def decorate(function):
                        self.names.append(function.__name__)
                        return function
                    return decorate

            server = Server()
            resources.register(
                server,
                ToolDependencies({
                    "resource_service_factory": lambda _remote: service,
                }),
            )
            self.assertEqual(server.names, [
                "resource_status",
                "resource_cleanup_plan",
                "resource_cleanup_apply",
            ])
            self.assertTrue(resources.resource_status()["ok"])
            self.assertTrue(resources.resource_cleanup_plan("cache")["ok"])
            self.assertTrue(
                resources.resource_cleanup_apply("a" * 32, confirm=True)["ok"],
            )
            self.assertEqual(service.calls, [
                ("status", False, 15),
                ("plan", "cache", True, 60),
                ("cleanup", "a" * 32, True),
            ])
        finally:
            sys.path.remove(str(mcp_root))

    def test_mcp_missing_confirmation_refuses_before_service_factory(self):
        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from dependencies import ToolDependencies
            from tools import resources

            calls = []

            class Server:
                @staticmethod
                def tool():
                    return lambda function: function

            resources.register(
                Server(),
                ToolDependencies({
                    "resource_service_factory": lambda remote: calls.append(remote),
                }),
            )
            payload = resources.resource_cleanup_apply("a" * 32, confirm=False)
            self.assertEqual(payload["error"]["code"], "confirmation_required")
            self.assertEqual(calls, [])
        finally:
            sys.path.remove(str(mcp_root))


if __name__ == "__main__":
    unittest.main()
