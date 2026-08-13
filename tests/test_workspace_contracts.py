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

    def test_remote_mutation_forwards_confirm_after_busy_check(self):
        commands = []
        target = SimpleNamespace(
            remote_name="vps", project_root="/project",
            workspace_label="reuse")

        def run(_remote, command, timeout):
            commands.append((command, timeout))
            if "job-list" in command:
                return SimpleNamespace(
                    returncode=0, stdout='{"ok":true,"jobs":[]}\n',
                    stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout='{"ok":true,"destroyed":true}\n', stderr="")

        with patch("sandbox.core._remote.get_remote",
                   return_value={"provisioned": True}), \
                patch("sandbox.core._remote.remote_workspace_path",
                      return_value="/remote/workspace"), \
                patch("sandbox.core._remote.remote_sb_path",
                      return_value="/remote/sb"), \
                patch("sandbox.core._remote.ssh_run", side_effect=run):
            result = _remote_workspace_control(target, "destroy")

        self.assertTrue(result["destroyed"])
        self.assertIn("--confirm", commands[-1][0])

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

    def test_mcp_lifecycle_requires_confirmation_and_matrix_isolated_cells(self):
        from tools import jobs

        workspace = _WorkspaceService()
        target = SimpleNamespace(kind="local", project_root="/project", remote_name=None,
                                 workspace_label="default", namespace="local:project")
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
