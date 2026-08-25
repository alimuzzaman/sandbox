"""CLI and MCP contracts for explicit workspace lifecycle and matrices."""

from __future__ import annotations

import sys
import unittest
from argparse import ArgumentParser
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.application.context import _remote_workspace_control
from sandbox.application.context import resolve_project_identity
from sandbox.application.target_service import TargetService, TargetResolutionError
from sandbox.jobs.models import TargetRequest
from sandbox.commands.workspaces import cmd_workspace, configure_parser


MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


class _WorkspaceService:
    def __init__(self) -> None:
        self.actions = []

    def __getattr__(self, action):
        def run(request):
            self.actions.append((action, request.workspace, request.remote, request.local))
            return {"ok": True, "action": action, "label": request.workspace}
        return run


class WorkspaceContractTests(unittest.TestCase):
    @staticmethod
    def _cli_args(action: str, *, confirm: bool = False, json: bool = True):
        return SimpleNamespace(action=action, project_dir="/project", local=True,
                               remote=None, workspace="unit", confirm=confirm, json=json)

    def test_remote_mutation_uses_workspace_identity_without_checkout_path(self):
        commands = []
        target = SimpleNamespace(
            remote_name="vps", project_root="/project",
            workspace_label="reuse", sources={"identity": "project-id"})

        def run(_remote, command, timeout):
            commands.append((command, timeout))
            return SimpleNamespace(
                returncode=0,
                stdout='{"ok":true,"destroyed":true}\n', stderr="")

        with patch("sandbox.core._remote.get_remote",
                   return_value={"provisioned": True}), \
                patch("sandbox.core._remote.remote_sb_path",
                      return_value="/remote/sb"), \
                patch("sandbox.core._remote.ssh_run", side_effect=run):
            result = _remote_workspace_control(
                target, "destroy", SimpleNamespace(
                    project_identity="project-id", workspace_id="ws-test",
                    migration_plan_id=None, confirm=True,
                ))

        self.assertTrue(result["destroyed"])
        self.assertIn("--confirm", commands[-1][0])
        self.assertIn("--workspace-id ws-test", commands[-1][0])
        self.assertNotIn("--project-dir", commands[-1][0])

    def test_remote_create_deploys_exact_tree_but_control_command_is_path_free(self):
        commands = []
        target = SimpleNamespace(
            remote_name="vps", project_root="/project",
            workspace_label="reuse", sources={"identity": "project-id"},
        )

        def run(_remote, command, timeout):
            commands.append(command)
            return SimpleNamespace(
                returncode=0,
                stdout='{"ok":true,"workspace":{"workspace_id":"ws-one"}}',
                stderr="",
            )

        with patch("sandbox.core._remote.get_remote",
                   return_value={"provisioned": True}), \
             patch("sandbox.core._remote.deploy_exact_working_tree",
                   return_value={"target_path": "/remote/private/path",
                                 "commit": "abc", "dirty": False,
                                 "identity": "sha256:source",
                                 "dirty_digest": "0" * 64}), \
             patch("sandbox.core._remote.prepare_remote_workspace",
                   return_value="/remote/private/workspace"), \
             patch("sandbox.core._remote.register_workspace_deployment_receipt",
                   return_value="wdr_" + "a" * 64), \
             patch("sandbox.core._remote.remote_sb_path", return_value="/remote/sb"), \
             patch("sandbox.core._remote.ssh_run", side_effect=run):
            result = _remote_workspace_control(
                target, "create", SimpleNamespace(
                    project_identity="project-id", workspace_id=None,
                    migration_plan_id=None, confirm=False,
                ))
        self.assertTrue(result["ok"])
        self.assertEqual(len(commands), 1)
        self.assertNotIn("/project", commands[0])
        self.assertNotIn("/remote/private/path", commands[0])
        self.assertNotIn("/remote/private/workspace", commands[0])
        self.assertIn("--deployment-receipt", commands[0])
        self.assertNotIn("--project-dir", commands[0])
        self.assertNotIn("--checkout-locator", commands[0])

    def test_remote_label_ambiguity_returns_bounded_workspace_ids(self):
        target = SimpleNamespace(
            remote_name="vps", project_root="/project",
            workspace_label="resource-scan", sources={"identity": "project-id"},
        )
        listing = {
            "ok": True,
            "workspaces": [
                {"label": "resource-scan", "workspace_id": "ws-one"},
                {"label": "resource-scan", "workspace_id": "ws-two"},
            ],
        }
        with patch("sandbox.core._remote.get_remote",
                   return_value={"provisioned": True}), \
             patch("sandbox.transports.remote_workspaces.RemoteWorkspaceTransport.list",
                   return_value=listing):
            result = _remote_workspace_control(
                target, "status", SimpleNamespace(
                    project_identity="project-id", workspace_id=None,
                    migration_plan_id=None, confirm=False,
                ))

        self.assertEqual(result["code"], "workspace_identity_ambiguous")
        self.assertEqual(result["workspace_ids"], ["ws-one", "ws-two"])

    def test_cli_lifecycle_forwards_one_explicit_target_request(self):
        service = _WorkspaceService()
        output = StringIO()
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": service}), patch("sys.stdout", output):
            for action in ("create", "list", "status"):
                cmd_workspace(None, self._cli_args(action))
        self.assertEqual(service.actions, [
            ("create", "unit", None, True), ("list", "unit", None, True),
            ("status", "unit", None, True),
        ])
        self.assertEqual(output.getvalue().count('"ok": true'), 3)

    def test_documented_confirmation_and_idempotent_create_aliases_parse(self):
        parser = ArgumentParser()
        configure_parser(parser)
        self.assertTrue(parser.parse_args(["create", "--ensure"]).ensure)
        self.assertTrue(parser.parse_args(["reset", "--yes"]).confirm)

    def test_cli_reset_and_destroy_require_explicit_confirmation(self):
        service = _WorkspaceService()
        for action in ("reset", "destroy"):
            with self.subTest(action=action), \
                 patch("sandbox.core.die", side_effect=RuntimeError("confirmation required")):
                with self.assertRaisesRegex(RuntimeError, "confirmation required"):
                    cmd_workspace(None, self._cli_args(action))
        self.assertEqual(service.actions, [])
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": service}), patch("sys.stdout", StringIO()):
            cmd_workspace(None, self._cli_args("reset", confirm=True))
            cmd_workspace(None, self._cli_args("destroy", confirm=True))
        self.assertEqual([action for action, *_ in service.actions], ["reset", "destroy"])

    def test_cli_json_failure_preserves_stable_workspace_code(self):
        class FailedService:
            def list(self, _request):
                error = RuntimeError("legacy inventory is incomplete")
                error.code = "workspace_index_incomplete"
                raise error

        output = StringIO()
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": FailedService()}), \
             patch("sys.stdout", output), self.assertRaises(SystemExit):
            cmd_workspace(None, self._cli_args("list"))
        payload = __import__("json").loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "workspace_index_incomplete")

    def test_cli_json_remote_preflight_failure_preserves_safe_observation_and_recovery(self):
        class FailedService:
            def list(self, _request):
                from sandbox.workspaces.repository import WorkspaceIndexError
                raise WorkspaceIndexError(
                    "workspace_remote_revision_mismatch",
                    "remote MCP service runtime revision is not verified",
                    observed={"ownership": "proven", "runtime_revision_state": "mismatch"},
                    recovery_command="./sb remote service migrate <name> --confirm --json",
                    secret="must-not-render",
                )

        output = StringIO()
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": FailedService()}), \
             patch("sys.stdout", output), self.assertRaises(SystemExit):
            cmd_workspace(None, self._cli_args("list"))
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "workspace_remote_revision_mismatch")
        self.assertEqual(
            payload["error"]["observed"],
            {"ownership": "proven", "runtime_revision_state": "mismatch"},
        )
        self.assertEqual(
            payload["error"]["recovery_command"],
            "./sb remote service migrate <name> --confirm --json",
        )
        self.assertNotIn("secret", output.getvalue())

    def test_cli_json_local_workspace_recovery_hint_is_preserved(self):
        class FailedService:
            def list(self, _request):
                from sandbox.workspaces.repository import WorkspaceIndexError
                raise WorkspaceIndexError(
                    "workspace_recovery_required",
                    "workspace metadata is incomplete",
                    recovery_command="./sb workspace migrate --local --json",
                )

        output = StringIO()
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": FailedService()}), \
             patch("sys.stdout", output), self.assertRaises(SystemExit):
            cmd_workspace(None, self._cli_args("list"))
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(payload["error"]["recovery_command"],
                         "./sb workspace migrate --local --json")

    def test_cli_returned_failure_is_nonzero_and_preserves_top_level_code(self):
        class FailedService:
            def list(self, _request):
                return {"ok": False, "code": "workspace_index_incomplete",
                        "workspaces": []}

        output = StringIO()
        with patch("sandbox.commands.workspaces.durable_job_dependencies",
                   return_value={"workspace_service": FailedService()}), \
             patch("sys.stdout", output), self.assertRaises(SystemExit) as caught:
            cmd_workspace(None, self._cli_args("list"))
        self.assertEqual(caught.exception.code, 1)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(payload["code"], "workspace_index_incomplete")

    def test_mcp_lifecycle_requires_confirmation_and_matrix_isolated_cells(self):
        from tools import jobs

        workspace = _WorkspaceService()
        target = SimpleNamespace(kind="local", project_root="/project", remote_name=None,
                                 workspace_label="default", namespace="local:project",
                                 sources={"identity": "project:test"},
                                 project_identity="project:test")
        captured = []
        job_service = SimpleNamespace(submit_matrix=lambda submissions: captured.extend(submissions) or {
            "ok": True, "parent_job_id": "a" * 32, "children": []})
        with patch.object(jobs, "_workspace_service", workspace), \
             patch.object(jobs, "_target_service", SimpleNamespace(resolve=lambda _request: target)), \
             patch.object(jobs, "_job_service", job_service):
            self.assertEqual(jobs.workspace_reset("/project", local=True)["code"],
                             "confirmation_required")
            self.assertEqual(jobs.workspace_destroy("/project", local=True)["code"],
                             "confirmation_required")
            self.assertTrue(jobs.workspace_reset("/project", local=True, confirm=True)["ok"])
            self.assertTrue(jobs.workspace_destroy("/project", local=True, confirm=True)["ok"])
            result = jobs.job_matrix(["python", "-m", "unittest"], ["unit", "integration"],
                                     "/project", local=True)
        self.assertTrue(result["ok"])
        self.assertEqual([submission.workspace_label for submission in captured], ["unit", "integration"])
        self.assertTrue(all(submission.workspace_mode == "isolated" for submission in captured))

    def test_mcp_matrix_resolves_each_workspace_policy_independently(self):
        from tools import jobs

        runtime = {
            "executionProfiles": {
                "fast": {"timeoutSeconds": 30, "stallSeconds": 3,
                         "cancelGraceSeconds": 4, "cleanup": "retain"},
                "slow": {"timeoutSeconds": 90, "stallSeconds": 9,
                         "cancelGraceSeconds": 10, "cleanup": "ephemeral"},
            },
            "workspaces": {
                "unit": {"executionProfile": "fast"},
                "integration": {"executionProfile": "slow"},
            },
        }
        captured = []

        def resolve(request):
            return SimpleNamespace(
                kind="local", project_root="/project", remote_name=None,
                workspace_label=request.workspace, namespace="local:project",
                runtime_policy=runtime, sources={"identity": "project:test"},
            )

        with patch.object(jobs, "_target_service", SimpleNamespace(resolve=resolve)), \
                patch.object(jobs, "_job_service", SimpleNamespace(
                    submit_matrix=lambda submissions: captured.extend(submissions) or {"ok": True})):
            result = jobs.job_matrix(["echo", "ok"], ["unit", "integration"], "/project", local=True)

        self.assertTrue(result["ok"])
        self.assertEqual([(item.workspace_label, item.execution_profile, item.deadline_seconds,
                           item.cancel_grace_seconds, item.cleanup_policy) for item in captured],
                         [("unit", "fast", 30, 4, "retain"),
                          ("integration", "slow", 90, 10, "ephemeral")])

    def test_shared_project_identity_is_kind_neutral_and_stable(self):
        from sandbox.config.facade import project_identity

        descriptor = {
            "root": "/tmp/example.site",
            "kind": "compose",
            "display_name": "Example.Site",
        }
        first = project_identity(descriptor, label="qa", remote="myvps")
        second = project_identity(descriptor, label="qa", remote="myvps")
        self.assertEqual(first, second)
        self.assertEqual(first["canonical_root"], first["root"])
        self.assertEqual(first["display_name"], "Example.Site")
        self.assertEqual(first["kind"], "compose")
        self.assertNotIn("plugin", first["runtime_id"])

    def test_target_selection_surfaces_explicit_precedence_and_ambiguity(self):
        config = lambda _path: {
            "root": "/tmp/project", "kind": "compose",
            "runtime": {"default": "local", "remote": None, "workspace": "default"},
        }
        remotes = {
            "alpha": {"provisioned": True},
            "beta": {"provisioned": True},
        }
        service = TargetService(config_loader=config, remote_lookup=lambda name: remotes.get(name),
                                remote_list=lambda: remotes)
        with self.assertRaisesRegex(TargetResolutionError, "multiple configured remotes"):
            service.resolve(TargetRequest("/tmp/project"))
        selected = service.resolve(TargetRequest("/tmp/project", remote="beta"))
        self.assertEqual(selected.remote_name, "beta")
        self.assertEqual(selected.sources["remote_selection"], "explicit")
        self.assertEqual(selected.sources["canonical_root"], str(Path("/tmp/project").resolve()))
