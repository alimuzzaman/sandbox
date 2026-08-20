from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


class RecordingService:
    def __init__(self):
        self.calls = []

    def status(
        self, *, thorough, budget_seconds, progress=None, deep=False,
        cancelled=False,
    ):
        self.calls.append(
            ("status", thorough, deep, budget_seconds)
            if not cancelled else
            ("status", thorough, deep, budget_seconds, cancelled)
        )
        deep_payload = {}
        if deep:
            deep_payload = {
                "deep_attribution": {
                    "status": "partial",
                    "filesystems": [{
                        "filesystem_id": "filesystem-root",
                        "display_name": "root filesystem",
                        "filesystem_type": "ext4",
                        "total_bytes": 10,
                        "used_bytes": 5,
                        "available_bytes": 5,
                        "writable": True,
                        "selected": True,
                        "selection_reason": "root",
                        "status": "complete",
                        "observed_allocated_bytes": 3,
                        "hardlink_deduplication": "confirmed",
                        "limitations": ["copy_on_write_unknown"],
                        "mount_id": "mount-root",
                        "parent_mount_id": "mount-parent",
                        "capacity_scope_id": "scope-root",
                        "mount_flags": ["read_write", "local", "root"],
                    }, {
                        "filesystem_id": "filesystem-data",
                        "display_name": "managed data filesystem",
                        "filesystem_type": "xfs",
                        "total_bytes": 20,
                        "used_bytes": 9,
                        "available_bytes": 11,
                        "writable": True,
                        "selected": True,
                        "selection_reason": "managed_root",
                        "status": "partial",
                        "observed_allocated_bytes": 2,
                        "hardlink_deduplication": "partial",
                        "limitations": ["nested_mount_excluded"],
                        "mount_id": "mount-data",
                        "parent_mount_id": "mount-root",
                        "capacity_scope_id": "scope-data",
                        "mount_flags": ["read_write", "local"],
                    }],
                    "findings": [{
                        "finding_id": "finding-a",
                        "kind": "deleted_open",
                        "display_name": "process 42",
                        "filesystem_id": "filesystem-root",
                        "owner": {"kind": "process", "id": "42"},
                        "observed_bytes": 3,
                        "capacity_accounted": True,
                        "overlap": "none",
                        "activity": "active",
                        "guidance": "manual",
                        "evidence": ["zero_link_count"],
                        "limitations": ["unprivileged_visibility"],
                        "unique_bytes": 3,
                        "shared_bytes": 0,
                        "potentially_reclaimable_bytes": 0,
                    }, {
                        "finding_id": "finding-b",
                        "kind": "directory",
                        "display_name": "managed data",
                        "filesystem_id": "filesystem-data",
                        "owner": {"kind": "host", "id": None},
                        "observed_bytes": 2,
                        "capacity_accounted": False,
                        "overlap": "directory_root",
                        "activity": "unknown",
                        "guidance": "manual",
                        "evidence": ["one_filesystem"],
                        "limitations": ["rank_truncated"],
                        "unique_bytes": 2,
                        "shared_bytes": 1,
                        "potentially_reclaimable_bytes": 0,
                    }],
                    "capabilities": [{
                        "category": "directory",
                        "name": "du",
                        "version": None,
                        "fallback": True,
                        "privilege": "unprivileged",
                        "status": "complete",
                        "limitations": ["standard_scanner"],
                    }],
                    "coverage": [{
                        "category": "directory",
                        "boundary_id": "filesystem-root",
                        "status": "complete",
                        "duration_ms": 4,
                        "confidence": "high",
                        "privilege_sufficient": True,
                        "reason": None,
                    }, {
                        "category": "deleted_open",
                        "boundary_id": None,
                        "status": "unavailable",
                        "duration_ms": 2,
                        "confidence": "low",
                        "privilege_sufficient": False,
                        "reason": "lsof_unavailable",
                    }],
                    "reconciliation": {
                        "used_bytes": 5,
                        "directory_allocated_bytes": 2,
                        "accounted_bytes": 3,
                        "residual_unexplained_bytes": 2,
                        "deleted_open_bytes": 3,
                        "observable_overhead_bytes": 0,
                        "overlapping_logical_bytes": 2,
                        "overage_bytes": 0,
                        "drift_bytes": 1,
                        "drift_material": False,
                        "capacity_drift_bytes": 1,
                        "attributed_drift_bytes": 0,
                        "capacity_drift_material": False,
                        "attributed_drift_material": False,
                    },
                },
            }
        return {
            "schema_version": 1, "ok": True, "action": "status",
            "status": "cancelled" if cancelled else "complete",
            "target": {"kind": "local", "name": "local"},
            "data": {
                "budget_seconds": budget_seconds,
                "capacity": {"total_bytes": 10, "used_bytes": 5, "available_bytes": 5},
                "summary": {"reclaimable_bytes": 0, "unknown_bytes": 0},
                "resources": [], "category_outcomes": [],
                **deep_payload,
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
        deep = self.parser().parse_args(["status", "--deep"])
        self.assertTrue(deep.deep)
        self.assertTrue(deep.action == "status")
        self.assertFalse(deep.cancelled)
        cancelled = self.parser().parse_args(["status", "--cancelled"])
        self.assertTrue(cancelled.cancelled)

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
        self.assertEqual(service.calls, [("status", False, False, 15.0)])

    def test_cli_boundary_subtracts_startup_and_skips_runtime_file_writes(self):
        from sandbox import cli
        from sandbox.commands import resources

        now = [106.0]

        class DelayedService(RecordingService):
            def status(self, **kwargs):
                payload = super().status(**kwargs)
                now[0] += kwargs["budget_seconds"]
                return payload

        service = DelayedService()
        output = io.StringIO()
        argv = ["sb", "resources", "status", "--deep", "--budget", "10", "--json"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(cli, "resolve_instances", return_value={}) as instance_resolver, \
             patch.object(cli, "_cwd_instance", return_value=None) as cwd_resolver, \
             patch.object(cli, "_core", return_value=SimpleNamespace(
                 registry_all=lambda: {},
             )) as core_factory, \
             patch.object(cli, "write_compose_files") as compose_writer, \
             patch.object(cli, "write_env_for_compose") as env_writer, \
             patch.object(resources, "resource_service", return_value=service), \
             patch.object(resources.time, "monotonic", side_effect=lambda: now[0]), \
             redirect_stdout(output):
            cli.main(invocation_started_monotonic=100.0)

        self.assertEqual(service.calls, [("status", True, True, 4.0)])
        self.assertEqual(now[0] - 100.0, 10.0)
        self.assertLessEqual(now[0] - 100.0, 15.0)
        self.assertEqual(json.loads(output.getvalue())["data"]["budget_seconds"], 10.0)
        compose_writer.assert_not_called()
        env_writer.assert_not_called()
        instance_resolver.assert_not_called()
        cwd_resolver.assert_not_called()
        core_factory.assert_not_called()

    def test_cli_boundary_does_not_dispatch_after_request_budget_expires(self):
        from sandbox import cli
        from sandbox.commands import resources

        output = io.StringIO()
        argv = ["sb", "resources", "status", "--deep", "--budget", "10", "--json"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(cli, "resolve_instances", return_value={}), \
             patch.object(cli, "_cwd_instance", return_value=None), \
             patch.object(cli, "_core", return_value=SimpleNamespace(
                 registry_all=lambda: {},
             )), \
             patch.object(cli, "write_compose_files") as compose_writer, \
             patch.object(cli, "write_env_for_compose") as env_writer, \
             patch.object(resources, "resource_service") as service_factory, \
             patch.object(resources.time, "monotonic", return_value=111.0), \
             redirect_stdout(output), self.assertRaises(SystemExit):
            cli.main(invocation_started_monotonic=100.0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "timed_out")
        self.assertEqual(payload["error"]["code"], "overall_budget_exhausted")
        self.assertEqual(payload["data"]["budget_seconds"], 10.0)
        service_factory.assert_not_called()
        compose_writer.assert_not_called()
        env_writer.assert_not_called()

    def test_resource_plan_and_cleanup_keep_runtime_file_writes(self):
        from sandbox import cli
        from sandbox.commands import resources

        cases = (
            (["sb", "resources", "plan", "--scope", "cache", "--json"], "plan"),
            (["sb", "resources", "cleanup", "--plan-id", "a" * 32,
              "--confirm", "--json"], "cleanup"),
        )
        for argv, expected_action in cases:
            with self.subTest(action=expected_action):
                service = RecordingService()
                with patch.object(sys, "argv", argv), \
                     patch.object(cli, "load_config", return_value={}), \
                     patch.object(cli, "resolve_instances") as instance_resolver, \
                     patch.object(cli, "_cwd_instance") as cwd_resolver, \
                     patch.object(cli, "_core") as core_factory, \
                     patch.object(cli, "write_compose_files") as compose_writer, \
                     patch.object(cli, "write_env_for_compose") as env_writer, \
                     patch.object(resources, "resource_service", return_value=service), \
                     redirect_stdout(io.StringIO()):
                    cli.main(invocation_started_monotonic=100.0)

                compose_writer.assert_called_once_with({})
                env_writer.assert_called_once_with({})
                instance_resolver.assert_not_called()
                cwd_resolver.assert_not_called()
                core_factory.assert_not_called()
                self.assertEqual(service.calls[0][0], expected_action)

    def test_cli_deep_implies_thorough_and_is_status_only(self):
        from sandbox.commands import resources
        service = RecordingService()
        args = self.parser().parse_args(["status", "--deep", "--json"])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output):
            resources.cmd_resources({}, args)
        self.assertEqual(service.calls, [("status", True, True, 15.0)])

        args = self.parser().parse_args([
            "plan", "--scope", "cache", "--deep", "--json",
        ])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output), self.assertRaises(SystemExit):
            resources.cmd_resources({}, args)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "invalid_mode",
        )

        args = self.parser().parse_args([
            "cleanup", "--plan-id", "a" * 32, "--cancelled", "--json",
        ])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output), self.assertRaises(SystemExit):
            resources.cmd_resources({}, args)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "invalid_mode",
        )

        args = self.parser().parse_args([
            "plan", "--scope", "cache", "--cancelled", "--json",
        ])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output), self.assertRaises(SystemExit):
            resources.cmd_resources({}, args)
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "invalid_mode",
        )

    def test_cli_deep_human_output_includes_reconciliation_coverage_and_guidance(self):
        from sandbox.commands import resources
        service = RecordingService()
        args = self.parser().parse_args(["status", "--deep"])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output):
            resources.cmd_resources({}, args)
        rendered = output.getvalue()
        self.assertIn("resources status: complete (local)", rendered)
        self.assertIn("target kind=local; name=local", rendered)
        self.assertIn("filesystem root filesystem (ext4):", rendered)
        self.assertIn("filesystem managed data filesystem (xfs):", rendered)
        self.assertIn("selected=True (root); status=complete", rendered)
        self.assertIn("selected=True (managed_root); status=partial", rendered)
        self.assertIn(
            "mount_id=mount-root; parent_mount_id=mount-parent; "
            "capacity_scope_id=scope-root; mount_flags=read_write,local,root",
            rendered,
        )
        self.assertIn(
            "mount_id=mount-data; parent_mount_id=mount-root; "
            "capacity_scope_id=scope-data; mount_flags=read_write,local",
            rendered,
        )
        self.assertIn("limitations: copy_on_write_unknown", rendered)
        self.assertIn("limitations: nested_mount_excluded", rendered)
        self.assertIn("deep used", rendered)
        self.assertIn("directory allocated", rendered)
        self.assertIn("logical overlap", rendered)
        self.assertIn("overage", rendered)
        self.assertIn("drift", rendered)
        self.assertIn("capacity drift", rendered)
        self.assertIn("attributed drift", rendered)
        self.assertIn("tool directory: du (complete)", rendered)
        self.assertIn("coverage: directory (complete:", rendered)
        self.assertIn(
            "deep partial: deleted_open "
            "(unavailable: lsof_unavailable)",
            rendered,
        )
        self.assertIn("rankings for root filesystem", rendered)
        self.assertIn("rankings for managed data filesystem", rendered)
        self.assertIn("owner=process:42", rendered)
        self.assertIn("unique=3.0 B", rendered)
        self.assertIn("shared=1.0 B", rendered)
        self.assertIn("potentially reclaimable=0.0 B", rendered)
        self.assertIn("[manual]", rendered)

    def test_cli_pre_cancelled_request_forwards_state_and_renders_returned_evidence(self):
        from sandbox.commands import resources
        service = RecordingService()
        args = self.parser().parse_args([
            "status", "--deep", "--cancelled",
        ])
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output):
            resources.cmd_resources({}, args)
        self.assertEqual(service.calls, [("status", True, True, 15.0, True)])
        self.assertIn("resources status: cancelled (local)", output.getvalue())
        self.assertIn("deep used", output.getvalue())

    def test_cli_redacts_every_new_human_deep_string_surface(self):
        from sandbox.commands import resources
        service = RecordingService()
        payload = service.status(
            thorough=True, budget_seconds=15, deep=True,
        )
        deep = payload["data"]["deep_attribution"]
        deep["filesystems"][0]["display_name"] = "token=filesystem-secret"
        deep["filesystems"][0]["mount_id"] = "token=mount-secret"
        deep["filesystems"][0]["parent_mount_id"] = "password=parent-secret"
        deep["filesystems"][0]["capacity_scope_id"] = "secret=scope-secret"
        deep["filesystems"][0]["mount_flags"] = ["cookie=flag-secret"]
        deep["capabilities"][0]["version"] = "password=capability-secret"
        deep["coverage"][0]["reason"] = "credential=coverage-secret"
        deep["findings"][0]["display_name"] = "secret=finding-secret"
        deep["findings"][0]["evidence"] = ["authorization=evidence-secret"]
        deep["findings"][0]["limitations"] = ["cookie=limitation-secret"]
        service.status = lambda **_kwargs: payload
        for args in (
            self.parser().parse_args(["status", "--deep", "--json"]),
            self.parser().parse_args(["status", "--deep"]),
        ):
            output = io.StringIO()
            with patch.object(resources, "resource_service", return_value=service), \
                 redirect_stdout(output):
                resources.cmd_resources({}, args)
            rendered = output.getvalue()
            for secret in (
                "filesystem-secret", "capability-secret", "coverage-secret",
                "finding-secret", "evidence-secret", "limitation-secret",
                "mount-secret", "parent-secret", "scope-secret", "flag-secret",
            ):
                self.assertNotIn(secret, rendered)
            self.assertIn("[redacted]", rendered)

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
            self.assertTrue(resources.resource_status(deep=True)["ok"])
            cancelled = resources.resource_status(deep=True, cancelled=True)
            self.assertTrue(cancelled["ok"])
            self.assertEqual(cancelled["status"], "cancelled")
            both = resources.resource_status(fast=True, refresh=True)
            self.assertFalse(both["ok"])
            self.assertEqual(both["error"]["code"], "invalid_mode")
            self.assertTrue(resources.resource_cleanup_plan("cache")["ok"])
            self.assertTrue(
                resources.resource_cleanup_apply("a" * 32, confirm=True)["ok"],
            )
            self.assertEqual(service.calls, [
                ("status", False, False, 15),
                ("status", True, True, 15),
                ("status", True, True, 15, True),
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


class CacheAwareRecordingService(RecordingService):
    def __init__(self, *, unknown_bytes=0, used_bytes=5, index=None):
        super().__init__()
        self.modes = []
        self.unknown_bytes = unknown_bytes
        self.used_bytes = used_bytes
        self.index = index

    def status(self, **kwargs):
        self.modes.append(kwargs.get("directory_cache"))
        payload = super().status(**{
            key: value for key, value in kwargs.items()
            if key != "directory_cache"
        })
        payload["data"]["capacity"]["used_bytes"] = self.used_bytes
        payload["data"]["summary"]["unknown_bytes"] = self.unknown_bytes
        if self.index is not None:
            payload["data"].setdefault("deep_attribution", {})
            payload["data"]["deep_attribution"]["directory_index"] = self.index
        return payload


class TestAlwaysAvailableAttribution(unittest.TestCase):
    def parser(self):
        from sandbox.commands.resources import configure_parser
        parser = argparse.ArgumentParser()
        configure_parser(parser)
        return parser

    def _run(self, argv, service):
        from sandbox.commands import resources
        args = self.parser().parse_args(argv)
        output = io.StringIO()
        with patch.object(resources, "resource_service", return_value=service), \
             redirect_stdout(output):
            resources.cmd_resources({}, args)
        return output.getvalue()

    def test_fast_reads_the_cached_index_and_never_walks(self):
        service = CacheAwareRecordingService()
        self._run(["status", "--fast", "--json"], service)
        self.assertEqual(service.modes, ["cache_only"])
        # --fast is deep attribution from cache, never a thorough sweep.
        self.assertEqual(service.calls[0][1:3], (False, True))
        self.assertEqual(service.calls[0][3], 10.0)

    def test_refresh_rebuilds_the_index_with_a_long_budget(self):
        service = CacheAwareRecordingService()
        self._run(["status", "--refresh", "--json"], service)
        self.assertEqual(service.modes, ["refresh"])
        self.assertEqual(service.calls[0][3], 900.0)

    def test_fast_and_refresh_are_mutually_exclusive(self):
        service = CacheAwareRecordingService()
        with self.assertRaises(SystemExit):
            self._run(["status", "--fast", "--refresh", "--json"], service)
        self.assertEqual(service.modes, [])

    def test_default_status_stays_compatible_with_older_providers(self):
        service = CacheAwareRecordingService()
        self._run(["status", "--json"], service)
        self.assertEqual(service.modes, [None])

    def test_large_unattributed_share_is_announced_first(self):
        service = CacheAwareRecordingService(
            unknown_bytes=178_000_000_000, used_bytes=187_000_000_000,
        )
        text = self._run(["status"], service)
        lines = [line for line in text.splitlines() if "UNATTRIBUTED" in line]
        self.assertTrue(lines, text)
        self.assertLess(
            text.index("UNATTRIBUTED"), text.index("reclaimable"),
        )
        self.assertIn("95.2%", lines[0])
        self.assertIn("--refresh", text)

    def test_missing_index_tells_the_operator_how_to_build_one(self):
        service = CacheAwareRecordingService(index={
            "source": "cache_missing", "complete": False, "stale": True,
            "age_seconds": None, "depth": 6, "minimum_row_bytes": 33554432,
        })
        text = self._run(["status", "--fast"], service)
        self.assertIn("directory index: cache_missing", text)
        self.assertIn("--deep --refresh", text)


if __name__ == "__main__":
    unittest.main()
