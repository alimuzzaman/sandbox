"""Access-only caller contracts; all authority/process ports are synthetic.

The actual selector, CLI handoff, alias selection and physical scope code run.
No provider, Docker, credential store, SSH or live durable job is invoked.
"""
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import hashlib
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
        self.enterContext(patch.object(host_stack, "resolve_ref",
            return_value=host_stack.StackRef("HEAD", "e" * 40, "")))
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

    def test_unselected_commands_do_not_stage_cwd_or_consume_stdin(self):
        for action, subaction in (("logs", None), ("diagnose", None), ("validate", None)):
            with self.subTest(action=action):
                self.args.action, self.args.image_action = action, subaction
                self.args.project_dir = "/nonexistent/not-a-checkout"
                self.args._server_cli_argv = ("host", action, "--remote", "box", "--", "cat")
                self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.submit.assert_not_called()
        self.lookup.assert_not_called()

    def test_joined_observations_use_owner_dispatch_without_confirmation(self):
        for action, subaction in (("stack", "list"), ("status", None), ("plan", None)):
            with self.subTest(action=action):
                self.submit.reset_mock()
                self.args.action, self.args.image_action = action, subaction
                self.args.confirm = False
                self.args._server_cli_argv = tuple(["host", action] +
                    ([subaction] if subaction else []) + ["--remote", "box",
                    "--project-dir", str(self.root), "--json"])
                with redirect_stdout(io.StringIO()), patch("sandbox.commands.hosting._secret_status",
                        side_effect=AssertionError("controller secret access")):
                    self.assertTrue(dispatch.dispatch_host_if_selected(self.args))
                self.submit.assert_called_once()
                argv = self.submit.call_args.args[1]
                self.assertNotIn("--confirm", argv)
                self.assertEqual(argv[argv.index("--project-dir") + 1], ".")
                self.assertNotIn("source_ref", self.submit.call_args.kwargs)

    def test_joined_credential_overrides_refuse_without_controller_fallback(self):
        for fields in ({"credential_scope": "personal-local"},
                       {"credential_source": "registered-source"}):
            with self.subTest(fields=fields), patch.dict(self.args.__dict__, fields):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                    dispatch.dispatch_host_if_selected(self.args)
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(json.loads(output.getvalue())["acceptance"], "not_accepted")
        self.submit.assert_not_called()

    def test_stack_plan_uses_owner_dispatch_and_preserves_plan_argv(self):
        self.args.plan, self.args.confirm = True, False
        self.args._server_cli_argv = tuple("--plan" if token == "--confirm" else token
                                           for token in self.args._server_cli_argv)
        with redirect_stdout(io.StringIO()):
            self.assertTrue(dispatch.dispatch_host_if_selected(self.args))
        self.submit.assert_called_once()
        self.assertIn("--plan", self.submit.call_args.args[1])
        self.assertNotIn("--confirm", self.submit.call_args.args[1])

    def test_nonjoined_credential_selection_keeps_controller_contract(self):
        self.lookup.return_value = {"joined": False}
        self.args.credential_scope = "personal-local"
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
        job_id = hashlib.sha256(self.id().encode()).hexdigest()[:32]
        self.context = sdk.ServerCLIContext(1, job_id, "original", str(self.home), "project",
            "source", "e" * 40, None, "a" * 24, "c" * 24,
            (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(),
            ("host", "exec"), "box", MappingProxyType({"front_door": "nginx"}), self.owner)
        self.current = self.enterContext(patch.object(sdk, "validate_owner_current", return_value=self.context))
        self.selected_context = self.enterContext(patch.object(sdk, "server_cli_context_if_selected", return_value=self.context))
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
            with self.assertRaises(sdk.ServerCLIEffectUnknown):
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
        self.assertEqual(child.call_args.kwargs["parent_job_id"], self.context.job_id)
        self.assertEqual(child.call_args.kwargs["request_id"], "original-apply")
        self.assertIn("box", child.call_args.args[1])

    def test_unknown_owner_process_retains_physical_lease_and_cannot_reissue_target(self):
        from sandbox.commands import hosting
        with patch.object(hosting, "_acquire_host_apply_lease", return_value={"token": "held"}), \
                patch.object(hosting, "_release_host_apply_lease") as release, \
                patch.object(sdk, "owner_process", side_effect=sdk.ServerCLIEffectUnknown("unknown")) as process, \
                patch.object(hosting, "_apply_host_unleased", side_effect=lambda *a, **k: self.entry.process("effect")):
            with self.assertRaises(sdk.ServerCLIEffectUnknown):
                hosting._apply_host({}, self.entry, "box", {})
        release.assert_not_called()
        process.assert_called_once()
        with self.assertRaises(sdk.ServerCLIEffectUnknown):
            targets.selected_owner_target("box")

    def test_intended_recipient_environment_uses_anonymous_stdin_not_encoded_argv(self):
        from sandbox.commands.hosting import _write_remote_text
        import base64
        marker = "synthetic-recipient-credential-canary"
        text = "APPLICATION_KEY=" + marker + "\n"
        with patch.object(sdk, "owner_process", return_value=subprocess.CompletedProcess(
                [], 0, stdout=b'{"ok":true,"status":"published"}', stderr=b"")) as process:
            _write_remote_text(self.entry, str(self.home / "runtime/hosts/project/dev/environment.env"), text)
        command = process.call_args.args[1]
        self.assertNotIn(marker, command)
        self.assertNotIn(base64.b64encode(text.encode()).decode(), command)
        frame = process.call_args.kwargs["input_data"]
        self.assertEqual(int.from_bytes(frame[:8], "big"), len(text.encode()))
        self.assertEqual(frame[8:], text.encode())
        self.assertNotIn(marker, repr(self.entry))

    def test_unusable_writer_results_retain_only_fixed_metadata_and_unknown_fence(self):
        from sandbox.commands.hosting import _write_remote_text
        from sandbox.core._hosting import HostingError
        marker = "synthetic-private-diagnostic-canary"
        cases = (
            (2, b"", "empty"),
            (0, marker.encode(), "invalid_json"),
            (1, json.dumps({"ok": False, "status": marker}).encode(), "invalid_shape"),
            (1, b'{"ok":false,"status":"publication_unknown"}', "publication_unknown"),
            (1, json.dumps({"ok": False, "status": "not_published", "extra": marker}).encode(), "invalid_shape"),
            (0, b'{"ok":true,"status":[]}', "invalid_shape"),
            (0, b'{"ok":true,"status":{}}', "invalid_shape"),
            (0, b'{"ok":true,"status":false}', "invalid_shape"),
            (0, b'{"ok":1,"status":"published"}', "invalid_shape"),
            (2, b'{"ok":true,"status":"published"}', "published"),
            (2, b'{"ok":true,"status":"already_current"}', "already_current"),
            (2, b'{"ok":false,"status":"not_published"}', "not_published"),
        )
        for code, stdout, hint in cases:
            with self.subTest(code=code, hint=hint), \
                    patch.object(sdk, "owner_process", return_value=subprocess.CompletedProcess(
                        [], code, stdout=stdout, stderr=marker.encode())), \
                    patch.object(targets, "withhold_owner_completion") as fence:
                with self.assertRaises(HostingError) as caught:
                    _write_remote_text(self.entry, str(self.home / "recipient.txt"), "synthetic-data")
                message = str(caught.exception)
                self.assertIn(f"writer_exit={code}; writer_receipt={hint};", message)
                self.assertNotIn(marker, message)
                self.assertNotIn(str(self.home), message)
                fence.assert_called_once_with(self.entry)

    def test_writer_exit_metadata_is_bounded_and_does_not_clear_unknown_fence(self):
        from sandbox.commands.hosting import _write_remote_text
        from sandbox.core._hosting import HostingError
        for code, hint in ((-255, "-255"), (255, "255"), (-256, "unavailable"),
                           (256, "unavailable"), (None, "unavailable"),
                           (True, "unavailable"), ("private-value", "unavailable")):
            with self.subTest(code=code), patch.object(sdk, "owner_process",
                    return_value=subprocess.CompletedProcess([], code, stdout=b"", stderr=b"")), \
                    patch.object(targets, "withhold_owner_completion") as fence:
                with self.assertRaises(HostingError) as caught:
                    _write_remote_text(self.entry, str(self.home / "recipient.txt"), "synthetic-data")
                self.assertIn(f"writer_exit={hint}; writer_receipt=empty;", str(caught.exception))
                self.assertNotIn("private-value", str(caught.exception))
                fence.assert_called_once_with(self.entry)

    def test_malformed_writer_status_retains_apply_lease(self):
        from sandbox.commands import hosting
        from sandbox.core._hosting import HostingError
        with patch.object(hosting, "_acquire_host_apply_lease", return_value={"token": "held"}), \
                patch.object(hosting, "_release_host_apply_lease") as release, \
                patch.object(sdk, "owner_process", return_value=subprocess.CompletedProcess(
                    [], 0, stdout=b'{"ok":true,"status":[]}', stderr=b"")), \
                patch.object(hosting, "_apply_host_unleased", side_effect=lambda *a, **k:
                    hosting._write_remote_text(self.entry, str(self.home / "recipient.txt"), "synthetic-data")):
            with self.assertRaises(HostingError):
                hosting._apply_host({}, self.entry, "box", {})
        self.assertTrue(targets.owner_effect_unknown(self.entry))
        release.assert_not_called()

    def test_fixed_recipient_writer_persists_only_private_intended_file_and_rejects_links(self):
        from sandbox.commands.hosting import _write_remote_text
        from sandbox.core._hosting import HostingError
        from tests.subprocess_support import run_test_process
        import stat
        def run(owner, command, *, timeout, input_data):
            return run_test_process(shlex.split(command), input=input_data,
                env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                capture_output=True, timeout=5)
        destination = self.home / "runtime/hosts/project/dev/recipient.txt"
        with patch.object(sdk, "owner_process", side_effect=run):
            _write_remote_text(self.entry, str(destination), "synthetic-private-recipient")
            self.assertEqual(destination.read_text(), "synthetic-private-recipient")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            linked = destination.with_name("linked.txt")
            linked.symlink_to(destination)
            with self.assertRaises(HostingError):
                _write_remote_text(self.entry, str(linked), "unintended-write")
            self.assertEqual(destination.read_text(), "synthetic-private-recipient")
            linked.unlink()
            os.link(destination, linked)
            with self.assertRaises(HostingError):
                _write_remote_text(self.entry, str(linked), "unintended-write")
            self.assertEqual(destination.read_text(), "synthetic-private-recipient")


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
        self.labels = ["sandbox-host-project-dev", f"{self.compose},{self.override}", str(self.source), "web", "1"]
        self.published_commit = "e" * 40
        def output(owner, command, **kwargs):
            argv = shlex.split(command)
            if "rev-parse" in argv:
                value = self.published_commit if argv[-1] == 'HEAD' else 'a' * 40
            elif 'cat-file' in argv:
                value = 'blob'
            elif 'hash-object' in argv:
                value = 'a' * 40
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

    def bind_original_selector(self):
        digest = "sha256:" + self.selected().fingerprint
        self.context = replace(self.context, target_selector_digest=digest)
        self.current.return_value = self.context
        self.selected_context.return_value = self.context
        self.entry = targets.selected_owner_target("box")
        return digest

    def test_metadata_git_never_executes_repository_fsmonitor_or_content_filters(self):
        from sandbox.services.process import ProcessResult
        from tests.subprocess_support import run_test_process, synthetic_environment
        def git(*args):
            result = run_test_process(['git', '-C', str(self.source), *args],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        git('init', '-q', '-b', 'fixture')
        (self.source / '.gitattributes').write_text('*.yml filter=fixture diff=fixture\n')
        git('add', 'compose.yml', '.gitattributes')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.published_commit = git('rev-parse', 'HEAD')
        self.summary['commit'] = self.published_commit
        sentinel = self.home / 'metadata-helper-ran'
        helper = self.home / 'fsmonitor-helper'
        helper.write_text('#!/bin/sh\nprintf ran > ' + shlex.quote(str(sentinel))
                          + '\nprintf "fixture-token\\0"\n')
        helper.chmod(0o700)
        git('config', 'core.fsmonitor', str(helper))
        git('config', 'core.fsmonitorHookVersion', '2')
        # Establish that this Git actually invokes the configured helper.
        git('ls-files', '--error-unmatch', '--', 'compose.yml')
        self.assertTrue(sentinel.exists(), 'fixture must reproduce Git helper execution')
        sentinel.unlink()
        git('config', 'filter.fixture.clean', shlex.quote(str(helper)))
        git('config', 'filter.fixture.required', 'true')
        git('config', 'diff.fixture.textconv', shlex.quote(str(helper)))
        def run(argv, **kwargs):
            if argv[0] == 'git':
                result = run_test_process(argv, cwd=kwargs.get('cwd'),
                    env=kwargs.get('env'), capture_output=True, text=True, timeout=5)
            else:
                result = self.process.side_effect(self.owner, shlex.join(argv))
            return ProcessResult(tuple(argv), result.returncode, result.stdout, result.stderr)
        with patch.object(sdk, 'read_current_server_owner', return_value=self.owner), \
             patch.object(sdk, 'owner_environment', return_value=synthetic_environment()), \
             patch('sandbox.services.process.BoundedProcessRunner.run', side_effect=run):
            selected = selection.inspect_server_exec_target(origin_remote_name='box', name='named')
            self.assertEqual(selected['source_commit'], self.published_commit)
            self.assertFalse(sentinel.exists(), 'metadata query executed repository configuration')
            self.compose.write_text('services: {changed: {image: synthetic}}\n')
            with self.assertRaises(TargetRefusal):
                selection.inspect_server_exec_target(origin_remote_name='box', name='named')
            self.assertFalse(sentinel.exists(), 'dirty-file refusal executed a content filter')

    def use_plain_target(self, commit="e" * 40):
        self.lifetime = {"project": "project", "environment": "dev", "kind": "target",
            "slug": None, "copy_id": None, "commit": commit,
            "compose_project": "sandbox-host-project-dev", "state": "active", "ports": {}}
        self.summary["stack"] = None

    def test_plain_target_without_lifetime_record_refuses_as_missing_evidence(self):
        self.use_plain_target()
        self.lifetime = {**self.lifetime, "project": "other"}
        with self.assertRaises(TargetRefusal) as refused:
            selection.read_owner_exec_selection(self.entry, target="project/dev")
        self.assertEqual(refused.exception.code, selection.UNAVAILABLE)
        self.process.assert_not_called()

    def test_classified_plain_target_selects_and_a_stale_commit_refuses(self):
        self.use_plain_target()
        selected = selection.read_owner_exec_selection(self.entry, target="project/dev")
        self.assertEqual(selected.target.label, "project/dev")
        self.assertEqual(selected.services, (("web", 1, "f" * 12),))
        self.assertIsNone(selected.copy_id)
        self.summary["commit"] = self.published_commit = "a" * 40
        with self.assertRaises(TargetRefusal) as refused:
            selection.read_owner_exec_selection(self.entry, target="project/dev")
        self.assertEqual(refused.exception.code, selection.UNAVAILABLE)

    def store_runner(self):
        from sandbox.hosting.lifetime import hoststore
        calls = []
        def run(command, payload):
            calls.append(json.loads(payload))
            return subprocess.CompletedProcess([], 0, stdout=json.dumps(
                hoststore.handle(json.loads(payload), str(self.home), free_paths=())), stderr="")
        return run, calls

    def write_server_summary(self, commit):
        (self.override.parent / "summary.json").write_text(json.dumps({**self.summary, "commit": commit}))
        self.summary["commit"] = self.published_commit = commit

    def stored_lifetime(self):
        from sandbox.hosting.lifetime import hoststore
        return hoststore.handle({"op": "list", "now": hoststore.to_rfc3339(1_800_000_000)},
            str(self.home), free_paths=())["result"]["records"]

    def test_succeeded_apply_refreshes_a_classified_plain_target_for_selection(self):
        from sandbox.commands import hosting
        from sandbox.hosting.lifetime import client as lifetime_client
        run, calls = self.store_runner()
        caller = {"label": "owner", "controller": "a" * 32}
        self.use_plain_target()
        self.write_server_summary("e" * 40)
        lifetime_client.StoreClient(run, caller).call(
            "classify", project="project", environment="dev", lifetime="hosted")
        self.lifetime = self.stored_lifetime()[0]
        first = selection.read_owner_exec_selection(self.entry, target="project/dev")
        # A later server-side apply at a new commit; exec refuses until the record moves.
        self.write_server_summary("a" * 40)
        with self.assertRaises(TargetRefusal):
            selection.read_owner_exec_selection(self.entry, target="project/dev")
        validated = {"project": "project", "environment": "dev"}
        with patch.object(lifetime_client, "ssh_runner", return_value=run):
            hosting._record_target_apply(self.entry, validated, {"commit": "a" * 40})
        self.assertEqual(calls[-1]["op"], "record_apply")
        self.lifetime = self.stored_lifetime()[0]
        self.assertEqual((self.lifetime["commit"], self.lifetime["lifetime"]), ("a" * 40, "hosted"))
        refreshed = selection.read_owner_exec_selection(self.entry, target="project/dev")
        self.assertEqual(refreshed.source_commit, "a" * 40)
        self.assertNotEqual(refreshed.fingerprint, first.fingerprint)

    def test_apply_of_an_unclassified_or_stack_target_creates_no_lifetime_record(self):
        from sandbox.commands import hosting
        from sandbox.hosting.lifetime import client as lifetime_client
        run, calls = self.store_runner()
        self.use_plain_target()
        self.write_server_summary("a" * 40)
        with patch.object(lifetime_client, "ssh_runner", return_value=run):
            hosting._record_target_apply(self.entry, {"project": "project", "environment": "dev"},
                                         {"commit": "a" * 40})
            hosting._record_target_apply(self.entry, {"project": "project", "environment": "dev",
                                         "stack": {"slug": "named"}}, {"commit": "a" * 40})
        self.assertEqual([call["op"] for call in calls], ["record_apply"])
        self.assertEqual(self.stored_lifetime(), [])

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
        self.bind_original_selector()
        args = Namespace(remote="box", name="named", target=None, environment=None,
            project_dir="/unrelated/controller", exec_argv=["id", "-un"], service="web", timeout=30, json=True)
        with patch("sandbox.hosting.stack_registry.read", side_effect=AssertionError("local registry")), \
                patch("sandbox.core._hosting.load_host_state", side_effect=AssertionError("local history")), \
                patch.object(host_exec, "_deployed_services", side_effect=AssertionError("runtime env rendering")):
            request, entry, prefix = host_exec._prepare(args)
        self.assertIsNotNone(request.owner_selection)
        self.assertEqual(request.target.project, "project")
        self.assertEqual(shlex.split(prefix), ["docker", "exec", "-i", "f" * 12])
        self.assertNotIn("environment.env", prefix)
        self.assertIs(type(entry), targets.OwnerHostTarget)

    def test_owner_exec_without_original_selector_refuses_before_metadata_or_effects(self):
        from sandbox.commands import host_exec
        args = Namespace(remote="box", name="named", target=None, environment=None,
            exec_argv=["id"], service="web", timeout=30, json=True)
        with self.assertRaises(host_exec.ExecRefusal) as refusal:
            host_exec._prepare(args)
        self.assertEqual(refusal.exception.code, "server_cli_target_binding_unavailable")
        self.process.assert_not_called()

    def test_held_exec_contends_with_actual_native_alias_locks(self):
        from sandbox.commands import host_exec
        from sandbox.hosting.recovery.repository import RecoveryRepository
        repository = RecoveryRepository(self.home / "hosts.json", self.home / "native-locks")
        self.bind_original_selector()
        selected = selection.read_owner_exec_selection(self.entry, name="named",
            expected_selector_digest=self.context.target_selector_digest)
        request = host_exec.ExecRequest(selected.target, "web", ("id",), owner_selection=selected)
        lease = {"path": str(self.home / "lease"), "token": "a" * 32, "owner": "b" * 32}
        with patch("sandbox.hosting.recovery.repository.RecoveryRepository", return_value=repository), \
                patch("sandbox.hosting.target_lease.acquire", return_value=lease) as acquire:
            with host_exec._execution_scope(request, self.entry):
                for key in selected.native_keys:
                    with self.assertRaises(TimeoutError), repository.effect_lock(key, timeout_seconds=0):
                        self.fail("native alias was not held")
            acquire.assert_called_once()
        # The same actual native locks become available after complete exit.
        for key in selected.native_keys:
            with repository.effect_lock(key, timeout_seconds=0):
                pass
        self.assertTrue(self.process.call_args.args[1].endswith(" release"))

    def test_native_exit_failure_does_not_retire_physical_fence(self):
        from sandbox.commands import host_exec
        from sandbox.hosting.recovery.repository import RecoveryRepository
        from contextlib import contextmanager
        self.bind_original_selector()
        selected = selection.read_owner_exec_selection(self.entry, name="named",
            expected_selector_digest=self.context.target_selector_digest)
        request = host_exec.ExecRequest(selected.target, "web", ("id",), owner_selection=selected)
        @contextmanager
        def broken_exit(*args, **kwargs):
            yield
            raise OSError("synthetic native unlock failure")
        repository = RecoveryRepository(self.home / "hosts.json", self.home / "native-locks")
        with patch.object(repository, "effect_lock", side_effect=broken_exit), \
                patch("sandbox.hosting.recovery.repository.RecoveryRepository", return_value=repository), \
                patch("sandbox.hosting.target_lease.acquire", return_value={
                    "path": "synthetic", "token": "a" * 32, "owner": "b" * 32}), \
                self.assertRaises(OSError):
            with host_exec._execution_scope(request, self.entry):
                pass
        self.assertFalse(any(call.args[1].endswith(" release") for call in self.process.call_args_list))

    def test_byte_emitter_publishes_only_metadata_after_entire_held_scope(self):
        from sandbox.commands import host_exec
        from sandbox.hosting.recovery.repository import RecoveryRepository
        from contextlib import contextmanager
        self.bind_original_selector()
        args = Namespace(remote="box", name="named", target=None, environment=None,
            exec_argv=["synthetic"], service="web", timeout=30, json=True, exec_capture_artifacts=True)
        outcome = host_exec.host_exec.ExecOutcome(0, "ok", 1, stdout=b"\x00\xff" + b"AIza" + b"A" * 40)
        metadata = {"ok": True, "code": "ok", "exit_code": 0,
                    "capture": {"contract": "job.server-cli.capture-artifact.v1"}}
        events = []
        custody = Mock()
        def prepare():
            events.append("prepare")
            return custody
        def publish(actual):
            self.assertIs(actual, outcome)
            self.assertEqual(events, ["prepare", "run", "scope_exited"])
            events.append("publish")
            return metadata
        custody.publish.side_effect = publish
        actual_scope = host_exec._execution_scope
        @contextmanager
        def scope(request, entry):
            with actual_scope(request, entry) as scoped:
                yield scoped
            events.append("scope_exited")
        def run(*args, **kwargs):
            events.append("run")
            return outcome
        repository = RecoveryRepository(self.home / "hosts.json", self.home / "native-locks")
        audit = Mock()
        audit.__enter__ = Mock(return_value=audit)
        audit.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with patch("sandbox.application.server_cli_capture.prepare_server_cli_capture", side_effect=prepare), \
                patch.object(host_exec, "_execution_scope", side_effect=scope), \
                patch.object(host_exec.host_exec, "run", side_effect=run), \
                patch.object(host_exec.host_exec, "AuditLog", return_value=audit), \
                patch.object(sdk, "owner_command_args", return_value=["synthetic"]), \
                patch.object(sdk, "owner_environment", return_value={"PATH": "/usr/bin:/bin"}), \
                patch("sandbox.hosting.recovery.repository.RecoveryRepository", return_value=repository), \
                patch("sandbox.hosting.target_lease.acquire", return_value={
                    "path": "synthetic", "token": "a" * 32, "owner": "b" * 32}), redirect_stdout(output):
            host_exec.cmd_host_exec(args)
        self.assertEqual(json.loads(output.getvalue()), metadata)
        self.assertNotIn("stdout", output.getvalue())
        self.assertNotIn("AIza", output.getvalue())
        self.assertEqual(events[-1], "publish")
        custody.close.assert_called_once()
        audit.write.assert_called_once()

    def test_byte_emitter_cleanup_failure_closes_custody_without_publishing(self):
        from sandbox.commands import host_exec
        from contextlib import contextmanager
        self.bind_original_selector()
        args = Namespace(remote="box", name="named", target=None, environment=None,
            exec_argv=["synthetic"], service="web", timeout=30, json=True, exec_capture_artifacts=True)
        @contextmanager
        def failed_cleanup(request, entry):
            yield request
            raise RuntimeError("synthetic cleanup uncertainty")
        custody = Mock()
        audit = Mock()
        audit.__enter__ = Mock(return_value=audit)
        audit.__exit__ = Mock(return_value=False)
        output = io.StringIO()
        with patch("sandbox.application.server_cli_capture.prepare_server_cli_capture", return_value=custody), \
                patch.object(host_exec, "_execution_scope", side_effect=failed_cleanup), \
                patch.object(host_exec.host_exec, "run", return_value=host_exec.host_exec.ExecOutcome(
                    0, "ok", 1, stdout=b"synthetic private output")), \
                patch.object(host_exec.host_exec, "AuditLog", return_value=audit), \
                patch.object(sdk, "owner_command_args", return_value=["synthetic"]), \
                patch.object(sdk, "owner_environment", return_value={"PATH": "/usr/bin:/bin"}), \
                redirect_stdout(output), self.assertRaises(SystemExit) as failure:
            host_exec.cmd_host_exec(args)
        self.assertEqual(failure.exception.code, host_exec.host_exec.EXIT_TRANSPORT)
        self.assertEqual(json.loads(output.getvalue())["code"], "host_exec_capture_unavailable")
        self.assertNotIn("synthetic private output", output.getvalue())
        custody.publish.assert_not_called()
        custody.close.assert_called_once()

    def run_owner_exec(self, *, seconds_left, timeout=300, capture_artifacts=False, acquire=None, run=None):
        """cmd_host_exec on the owner path; returns (exit, output, acquire mock, run mock, audit)."""
        from sandbox.commands import host_exec
        from sandbox.hosting import target_lease
        from sandbox.hosting.recovery.repository import RecoveryRepository
        self.bind_original_selector()
        self.context = replace(self.context, deadline_at=(
            datetime.now(timezone.utc) + timedelta(seconds=seconds_left)).isoformat())
        self.current.return_value = self.context
        self.selected_context.return_value = self.context
        self.entry = targets.selected_owner_target("box")
        args = Namespace(remote="box", name="named", target=None, environment=None,
            exec_argv=["true"], service="web", timeout=timeout, json=True,
            exec_capture_artifacts=capture_artifacts)
        audit = Mock()
        audit.__enter__ = Mock(return_value=audit)
        audit.__exit__ = Mock(return_value=False)
        repository = RecoveryRepository(self.home / "hosts.json", self.home / "native-locks")
        output = io.StringIO()
        code = 0
        with patch("sandbox.hosting.target_lease.acquire", side_effect=acquire,
                   wraps=None if acquire else target_lease.acquire) as acquired, \
                patch.object(host_exec.host_exec, "run", side_effect=run or (
                    lambda *a, **k: host_exec.host_exec.ExecOutcome(0, "ok", 1))) as ran, \
                patch.object(host_exec.host_exec, "AuditLog", return_value=audit), \
                patch.object(sdk, "owner_command_args", side_effect=lambda owner, command: ["sh", "-c", command]), \
                patch.object(sdk, "owner_environment", return_value={"PATH": "/usr/bin:/bin"}), \
                patch("sandbox.application.server_cli_capture.prepare_server_cli_capture", return_value=Mock()), \
                patch("sandbox.hosting.recovery.repository.RecoveryRepository", return_value=repository), \
                redirect_stdout(output):
            try:
                host_exec.cmd_host_exec(args)
            except SystemExit as exit_:
                code = exit_.code
        return code, output.getvalue(), acquired, ran, audit

    def test_owner_exec_without_time_to_release_refuses_before_taking_the_lease(self):
        from sandbox.hosting import target_lease
        code, output, acquired, ran, audit = self.run_owner_exec(seconds_left=20, timeout=30)
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertEqual(payload["code"], "host_exec_timeout")
        self.assertIn("nothing ran", payload["error"])
        acquired.assert_not_called()
        ran.assert_not_called()
        self.assertFalse(any(" acquire" in call.args[1] for call in self.process.call_args_list))
        self.assertFalse(Path(target_lease.lease_path(str(self.home), "project", "dev")).exists())
        audit.write.assert_called_once()

    def test_owner_exec_clamps_the_command_timeout_to_the_time_left(self):
        code, output, acquired, ran, _ = self.run_owner_exec(seconds_left=100, timeout=300)
        self.assertEqual(code, 0)
        acquired.assert_called_once()
        request = ran.call_args.args[1]
        argv = shlex.split(ran.call_args.args[0][-1])
        self.assertEqual(argv[:2], ["timeout", "--kill-after=10"])
        self.assertEqual(int(argv[2]), request.timeout_seconds)
        # 100 s left less the 10 s kill grace and 15 s lease release.
        self.assertLessEqual(request.timeout_seconds, 75)
        self.assertGreaterEqual(request.timeout_seconds, 72)
        self.assertEqual(json.loads(output)["code"], "ok")

    def test_owner_exec_keeps_a_timeout_that_already_fits(self):
        code, _, acquired, ran, _ = self.run_owner_exec(seconds_left=300, timeout=30)
        self.assertEqual(code, 0)
        self.assertEqual(ran.call_args.args[1].timeout_seconds, 30)
        self.assertIn("timeout --kill-after=10 30 env", ran.call_args.args[0][-1])

    def test_a_held_lease_is_target_busy_not_a_transport_failure(self):
        from sandbox.hosting import target_lease
        held = target_lease.LeaseHeld("another controller holds this target's lease (since unix time 1)")
        for capture in (False, True):
            with self.subTest(capture_artifacts=capture):
                code, output, _, ran, audit = self.run_owner_exec(
                    seconds_left=300, timeout=30, capture_artifacts=capture, acquire=held)
                self.assertEqual(code, 1)
                payload = json.loads(output)
                self.assertEqual(payload["code"], "host_target_busy")
                self.assertIn("nothing ran", payload["error"])
                ran.assert_not_called()
                self.assertEqual(audit.write.call_args.args[1].code, "host_target_busy")

    def test_a_deadline_expiry_inside_the_scope_is_a_timeout_not_a_transport_failure(self):
        expired = getattr(sdk, "ServerCLIDeadlineExpired", sdk.ServerCLIUnavailable)
        for capture in (False, True):
            with self.subTest(capture_artifacts=capture):
                code, output, _, _, audit = self.run_owner_exec(seconds_left=300, timeout=30,
                    capture_artifacts=capture, run=expired("retained server CLI job deadline expired"))
                self.assertEqual(code, 124)
                payload = json.loads(output)
                self.assertEqual(payload["code"], "host_exec_timeout")
                self.assertIn("before any replay", payload["error"])
                self.assertEqual(audit.write.call_args.args[1].code, "host_exec_timeout")

    def test_an_unknown_failure_inside_the_scope_stays_a_transport_failure(self):
        code, output, _, _, _ = self.run_owner_exec(seconds_left=300, timeout=30,
            run=RuntimeError("synthetic unknown failure"))
        self.assertEqual(code, 255)
        self.assertEqual(json.loads(output)["code"], "host_exec_transport_failed")

    def test_owner_exec_refuses_a_service_without_a_unique_first_container(self):
        selected = self.selected()
        with self.assertRaises(TargetRefusal):
            selected.exec_prefix("another-service")
        self.labels[4] = "2"
        selected = self.selected()
        with self.assertRaises(TargetRefusal):
            selected.exec_prefix("web")

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

    def test_corrupt_alias_partition_is_not_treated_as_absent_history(self):
        from sandbox.hosting.state_partition.layout import HostState
        state = HostState(self.state)
        state.unreadable = frozenset({"opaque-damaged-key"})
        self.state = state
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

    def test_original_and_published_subtree_revisions_are_distinct_bindings(self):
        self.summary["commit"] = self.published_commit = "a" * 40
        selected = self.selected()
        self.assertEqual(selected.source_commit, "a" * 40)
        self.assertEqual(selected.source_origin_commit, "e" * 40)
        self.summary["stack"]["commit"] = "b" * 40
        with self.assertRaises(TargetRefusal):
            self.selected()

    def test_expected_snapshot_digest_is_revalidated_at_held_consumption(self):
        observed = self.selected()
        expected = "sha256:" + observed.fingerprint
        current = selection.read_owner_exec_selection(self.entry, name="named",
            expected_selector_digest=expected)
        current.require_current()
        self.lifetime["copy_id"] = "abcd1234"
        with self.assertRaises(TargetRefusal) as refusal:
            current.require_current()
        self.assertEqual(refusal.exception.code, "host_server_target_changed")
        with self.assertRaises(TargetRefusal):
            selection.read_owner_exec_selection(self.entry, name="named", expected_selector_digest="bad")

    def test_prepare_consumes_selector_only_from_genuine_retained_context(self):
        from sandbox.commands import host_exec
        observed = self.selected()
        args = Namespace(remote="box", name="named", target=None, environment=None,
            project_dir="/unrelated/controller", exec_argv=["id", "-un"], service="web", timeout=30, json=True)
        expected = "sha256:" + observed.fingerprint
        self.context = replace(self.context, target_selector_digest=expected)
        self.current.return_value = self.context
        with patch.object(sdk, "server_cli_context_if_selected", return_value=self.context):
            self.entry = targets.selected_owner_target("box")
            request, _, _ = host_exec._prepare(args)
        self.assertEqual(request.owner_selection.expected_selector_digest, expected)
        self.lifetime["copy_id"] = "abcd1234"
        with patch.object(sdk, "server_cli_context_if_selected", return_value=self.context), \
                self.assertRaises(TargetRefusal) as refused:
            host_exec._prepare(args)
        self.assertEqual(refused.exception.code, "host_server_target_changed")

    def test_metadata_inspection_cannot_issue_execution_selection_or_serialize_owner_proof(self):
        reader = getattr(sdk, "read_current_server_owner", None)
        if reader is None:
            self.skipTest("public SDK owner metadata reader not assembled in this snapshot")
        from sandbox.services.process import ProcessResult
        def run(argv, **kwargs):
            result = self.process.side_effect(self.owner, shlex.join(argv))
            return ProcessResult(tuple(argv), result.returncode, result.stdout, result.stderr)
        with patch.object(sdk, "read_current_server_owner", return_value=self.owner), \
                patch.object(sdk, "owner_environment", return_value={"PATH": "/usr/bin:/bin"}), \
                patch("sandbox.services.process.BoundedProcessRunner.run", side_effect=run), \
                patch.object(targets, "require_owner_target", side_effect=AssertionError("metadata is not a job grant")):
            result = selection.inspect_server_exec_target(origin_remote_name="box", name="named")
        self.assertEqual(result["copy_id"], "1234abcd")
        self.assertEqual(result["target_identity"], self.owner.target_identity)
        self.assertEqual(result["source_origin_commit"], "e" * 40)
        self.assertNotIn("owner", result)
        self.assertIsInstance(json.loads(json.dumps(result)), dict)
        with self.assertRaises(targets.OwnerTargetUnavailable):
            targets.require_owner_target(result)


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
