"""Access-only caller contracts; all authority/process ports are synthetic.

The actual selector, CLI handoff, alias selection and physical scope code run.
No provider, Docker, credential store, SSH or live durable job is invoked.
"""
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

from sandbox.application import server_cli as sdk
from sandbox.commands import host_stack
from sandbox.hosting import server_cli_dispatch as dispatch
from sandbox.hosting import server_cli_target as targets
from sandbox.hosting import server_target_selection as selection
from sandbox.hosting.target_resolution import TargetRefusal


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.template = self.root / "sandbox.hosting.yml"
        self.template.write_text("version: 1\n")
        self.args = Namespace(action="stack", image_action="up", confirm=True, plan=False,
            remote="box", project_dir=str(self.root), template=str(self.template), request_id="original",
            timeout=None, json=True, _server_cli_argv=("host", "stack", "up", "--remote", "box",
                "--project-dir", str(self.root), "--template", str(self.template), "--confirm"))
        self.enterContext(patch.object(sdk, "server_cli_context_if_selected", return_value=None))
        self.lookup = self.enterContext(patch("sandbox.core._remote.get_remote",
            return_value={"joined": True, "control_transport": "tailscale"}))
        self.submit = self.enterContext(patch.object(sdk, "dispatch_server_cli",
            return_value={"ok": True, "status": "accepted", "job_id": "d" * 32, "request_id": "original"}))

    def test_confirmed_stack_uses_original_receipt_and_relocates_only_source_paths(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("sandbox.commands.hosting._secret_status",
                side_effect=AssertionError("controller secret access")):
            self.assertTrue(dispatch.dispatch_host_if_selected(self.args))
        args, kwargs = self.submit.call_args
        self.assertEqual(args[0], "box")
        self.assertEqual(args[1][args[1].index("--template") + 1], "sandbox.hosting.yml")
        self.assertEqual(args[1][args[1].index("--project-dir") + 1], ".")
        self.assertEqual(kwargs["request_id"], "original")
        self.assertEqual(json.loads(output.getvalue()), self.submit.return_value)

    def test_observation_validation_plan_and_exec_do_not_stage_cwd_or_consume_stdin(self):
        for action, subaction in (("stack", "list"), ("logs", None), ("status", None),
                ("diagnose", None), ("validate", None), ("plan", None), ("exec", None)):
            with self.subTest(action=action):
                self.args.action, self.args.image_action = action, subaction
                self.args.project_dir = "/nonexistent/not-a-checkout"
                self.args._server_cli_argv = ("host", action, "--remote", "box", "--", "cat")
                self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.submit.assert_not_called()
        self.lookup.assert_not_called()

    def test_personal_local_and_explicit_profile_keep_their_existing_contract(self):
        for fields in ({"credential_scope": "personal-local"},
                       {"credential_source": "registered-source"}, {"plan": True}, {"confirm": False}):
            with self.subTest(fields=fields), patch.dict(self.args.__dict__, fields):
                self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.submit.assert_not_called()

    def test_delimiter_is_refused_before_acceptance_for_automatic_apply(self):
        self.args._server_cli_argv += ("--", "cat")
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(caught.exception.code, 1)
        self.submit.assert_not_called()

    def test_invalid_selected_job_never_falls_back_or_replays(self):
        with patch.object(sdk, "server_cli_context_if_selected", side_effect=sdk.ServerCLIUnavailable("revoked")):
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                dispatch.dispatch_host_if_selected(self.args)
        self.lookup.assert_not_called()
        self.submit.assert_not_called()

    def test_old_capability_and_unknown_effect_have_bounded_json_without_error_body(self):
        marker = "untrusted-provider-or-transport-body"
        for error, acceptance in ((sdk.ServerCLIUnavailable(marker), "not_accepted"),
                                  (sdk.ServerCLIEffectUnknown(marker), "unknown")):
            with self.subTest(acceptance=acceptance), patch.object(sdk, "dispatch_server_cli", side_effect=error) as submit:
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                    dispatch.dispatch_host_if_selected(self.args)
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["acceptance"], acceptance)
                self.assertEqual(payload["request_id"], "original")
                self.assertNotIn(marker, output.getvalue())
                submit.assert_called_once()

    def test_unknown_receipt_retains_only_original_handle_and_refuses_replay(self):
        self.submit.return_value = {"status": "acceptance_unknown", "job_id": "d" * 32,
            "request_id": "original", "error": "untrusted raw response"}
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["job_id"], "d" * 32)
        self.assertEqual(payload["request_id"], "original")
        self.assertEqual(payload["acceptance"], "unknown")
        self.assertNotIn("untrusted raw response", output.getvalue())
        self.submit.assert_called_once()


class OwnerTargetTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.owner = sdk.ServerCliOwner(os.geteuid(), str(self.home), str(self.home),
            str(self.home / "sb"), "a" * 24, "b" * 24, "c" * 24)
        self.context = sdk.ServerCLIContext(1, "d" * 32, "original", str(self.home), "project",
            "source", "e" * 40, None, "a" * 24, "c" * 24,
            (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
            ("host", "exec"), "box", MappingProxyType({"front_door": "nginx"}), self.owner)
        self.current = self.enterContext(patch.object(sdk, "validate_owner_current", return_value=self.context))
        self.enterContext(patch.object(sdk, "server_cli_context_if_selected", return_value=self.context))
        self.entry = targets.selected_owner_target("box")

    def test_only_original_alias_selects_owner_and_no_dict_can_grant_it(self):
        self.assertIsNone(targets.selected_owner_target("different-alias"))
        self.assertFalse(targets.is_owner_target({"owner": self.owner, "local": True}))
        with self.assertRaises(targets.OwnerTargetUnavailable):
            targets.require_owner_target({"local": True})
        self.assertNotIn("ssh", self.entry)
        self.assertNotIn("provisioned", self.entry)

    def test_revocation_after_process_prevents_result_acknowledgement(self):
        def revoke(*args, **kwargs):
            self.current.side_effect = sdk.ServerCLIUnavailable("revoked")
            return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with patch.object(sdk, "owner_process", side_effect=revoke) as run:
            with self.assertRaises(sdk.ServerCLIUnavailable):
                self.entry.process("fixed-command")
        run.assert_called_once()

    def test_home_and_delivery_digest_use_current_owner_without_self_ssh(self):
        from sandbox.delivery.context import registration_digest
        from sandbox.core import _remote
        with patch.object(_remote, "remote_ssh_parts", side_effect=AssertionError("self SSH")):
            self.assertEqual(_remote.resolve_sandbox_home(self.entry), str(self.home))
            self.assertEqual(registration_digest(self.entry, "box"), self.entry.identity_digest())
        with self.assertRaises(ValueError):
            registration_digest(self.entry, "different-alias")

    def test_missing_front_door_does_not_default_to_caddy(self):
        with patch.object(sdk, "server_cli_context_if_selected", return_value=replace(self.context,
                target_metadata=MappingProxyType({}))), patch.object(sdk, "validate_owner_current",
                return_value=replace(self.context, target_metadata=MappingProxyType({}))):
            with self.assertRaises(targets.OwnerTargetUnavailable):
                targets.selected_owner_target("box")

    def test_stack_apply_guidance_retains_real_parent_and_original_alias(self):
        from sandbox.commands.hosting import _host_delivery_guidance
        with patch.object(sdk, "local_server_cli_submission_argv", return_value=["fixed-child"]) as child:
            result = _host_delivery_guidance({"project_root": str(self.home),
                "manifest_root": str(self.home / "manifest"), "environment": "dev"},
                "box", request_id="original-apply", source_commit="e" * 40)
        self.assertEqual(result, ["fixed-child"])
        self.assertEqual(child.call_args.kwargs["parent_job_id"], "d" * 32)
        self.assertEqual(child.call_args.kwargs["request_id"], "original-apply")
        self.assertIn("box", child.call_args.args[1])


class ServerSelectionTests(OwnerTargetTests):
    def setUp(self):
        super().setUp()
        self.source = self.home / "deploy-src/hosts/project"
        self.source.mkdir(parents=True)
        self.compose = self.source / "compose.yml"
        self.compose.write_text("services: {web: {image: synthetic}}\n")
        self.override = self.home / "runtime/hosts/project/dev/compose.override.yml"
        self.override.parent.mkdir(parents=True)
        self.override.write_text("services: {}\n")
        self.lifetime = {"project": "project", "environment": "dev", "kind": "stack",
            "slug": "named", "copy_id": "1234abcd", "commit": "e" * 40,
            "compose_project": "sandbox-host-project-dev", "state": "active", "ports": {}}
        self.summary = {"project": "project", "environment": "dev", "compose_project":
            "sandbox-host-project-dev", "commit": "e" * 40,
            "last_apply": {"status": "succeeded"}, "stack": {"slug": "named", "commit": "e" * 40, "ports": {}}}
        self.state = {"hosts": {"old-alias/project/dev": {}}}
        self.enterContext(patch("sandbox.hosting.lifetime.hoststore.Store.peek",
            side_effect=lambda *a, **k: {"records": [self.lifetime], "invalid": [], "truncated": False}))
        self.enterContext(patch("sandbox.hosting.panel_records.read_server_records",
            side_effect=lambda *a, **k: {"targets": [self.summary], "partial_reasons": []}))
        self.enterContext(patch("sandbox.hosting.recovery.repository.RecoveryRepository.load", side_effect=lambda: self.state))
        self.labels = ["sandbox-host-project-dev", f"{self.compose},{self.override}", str(self.source)]
        def output(owner, command, **kwargs):
            argv = shlex.split(command)
            if "rev-parse" in argv:
                value = "e" * 40
            elif argv[:2] == ["docker", "ps"]:
                value = "f" * 12
            elif argv[:2] == ["docker", "inspect"]:
                value = json.dumps(self.labels)
            else:
                value = ""
            return subprocess.CompletedProcess(argv, 0, stdout=value, stderr="")
        self.process = self.enterContext(patch.object(sdk, "owner_process", side_effect=output))

    def selected(self):
        return selection.read_owner_exec_selection(self.entry, name="named")

    def test_server_inventory_resolves_without_local_history_and_uses_actual_alias_keys(self):
        with patch("sandbox.hosting.stack_registry.read", side_effect=AssertionError("local registry")), \
                patch("sandbox.core._hosting.load_host_state", side_effect=AssertionError("local history")):
            result = self.selected()
        self.assertEqual(result.target.label, "project/dev")
        self.assertEqual(result.copy_id, "1234abcd")
        self.assertEqual(result.native_keys, ("box/project/dev", "old-alias/project/dev"))
        self.assertFalse(any(".Config.Env" in call.args[1] for call in self.process.call_args_list))

    def test_owner_exec_prepare_uses_selected_server_manifest_without_laptop_history(self):
        from sandbox.commands import host_exec
        args = Namespace(remote="box", name="named", target=None, environment=None,
            project_dir="/unrelated/controller", exec_argv=["id", "-un"], service="web", timeout=30, json=True)
        with patch("sandbox.hosting.stack_registry.read", side_effect=AssertionError("local registry")), \
                patch("sandbox.core._hosting.load_host_state", side_effect=AssertionError("local history")), \
                patch.object(host_exec, "_deployed_services", return_value=["web"]):
            request, entry, prefix = host_exec._prepare(args)
        self.assertIsNotNone(request.owner_selection)
        self.assertEqual(request.target.project, "project")
        self.assertIn(str(self.compose), prefix)
        self.assertIs(type(entry), targets.OwnerHostTarget)

    def test_unknown_apply_or_active_native_owner_refuses_without_execution(self):
        self.summary["last_apply"]["status"] = "unverified"
        with self.assertRaises(TargetRefusal):
            self.selected()
        self.process.assert_not_called()
        self.summary["last_apply"]["status"] = "succeeded"
        self.state["hosts"]["old-alias/project/dev"]["recovery_uncertainty"] = {"unknown": True}
        with self.assertRaises(TargetRefusal):
            self.selected()
        self.process.assert_not_called()

    def test_changed_copy_or_runtime_manifest_cannot_reuse_selector(self):
        result = self.selected()
        self.lifetime["copy_id"] = "abcd1234"
        with self.assertRaises(TargetRefusal):
            result.require_current()
        self.lifetime["copy_id"] = "1234abcd"
        self.labels[1] = str(self.home / "other-compose.yml") + "," + str(self.override)
        with self.assertRaises(TargetRefusal):
            self.selected()


class SubmissionBoundaryTests(unittest.TestCase):
    def test_missing_job_outcome_fences_original_request_and_no_new_stack_effects(self):
        with patch.object(host_stack, "job_view", return_value=None):
            for last in ({"job_id": "d" * 32}, {"status": "acceptance_unknown"},
                         {"status": "submission_failed"}):
                with self.subTest(last=last), self.assertRaises(host_stack.StackCommandError) as caught:
                    host_stack._refuse_if_active({"slug": "old", "last_apply": {"request_id": "original", **last}})
                self.assertEqual(caught.exception.code, host_stack.SUBMISSION_UNKNOWN)

    def test_malformed_acceptance_is_unknown_and_never_returns_output(self):
        marker = "synthetic-untrusted-stdout-marker"
        with patch.object(host_stack.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout=marker, stderr=marker)) as run:
            with self.assertRaises(host_stack.StackCommandError) as caught:
                host_stack._submit(["synthetic-job-start"])
        self.assertEqual(caught.exception.code, host_stack.SUBMISSION_UNKNOWN)
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn("XSPEED_JEV_API_KEY", run.call_args.kwargs["env"])
