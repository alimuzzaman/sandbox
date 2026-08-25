import types
import unittest
import contextlib
import io
import json
from unittest import mock

from sandbox.runtimes.base import OperationError, OperationResult


class RejectingService:
    def __init__(self):
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return OperationError(
            code="unsupported_capability",
            message="blocked before side effects",
            project_kind="test",
            requested_capability=request.operation,
        )


class TestRuntimeTransportPreflight(unittest.TestCase):
    def test_cli_ensure_preserves_typed_mount_refusal_json_and_human_guidance(self):
        import sandbox.commands.instances_cmd as commands

        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            False, "ensure", "/tmp/project", "wordpress", {
                "ok": False, "mutated": False,
                "error": {
                    "code": "instance_mount_drift",
                    "message": "live source bind drift; run `sb apply --project-dir <project-dir>`",
                },
            },
        )
        for as_json in (True, False):
            args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                         create=False, json=as_json, local=True)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with self.subTest(json=as_json), \
                    mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                    self.assertRaises(SystemExit) as raised:
                commands.cmd_ensure({}, args)
            self.assertEqual(raised.exception.code, 1)
            if as_json:
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["error"]["code"], "instance_mount_drift")
                self.assertFalse(payload["mutated"])
            else:
                self.assertIn("instance_mount_drift", stderr.getvalue())
                self.assertIn("sb apply --project-dir <project-dir>", stderr.getvalue())

    def test_cli_ensure_rejects_before_legacy_ensure(self):
        import sandbox.commands.instances_cmd as commands

        service = RejectingService()
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=False)
        with mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                mock.patch.object(commands, "ensure_instance") as legacy, \
                self.assertRaises(SystemExit):
            commands.cmd_ensure({}, args)
        legacy.assert_not_called()
        self.assertEqual(service.requests[0].operation, "ensure")

    def test_cli_ensure_json_redacts_typed_operation_error(self):
        import sandbox.commands.instances_cmd as commands

        service = RejectingService()
        service.invoke = mock.Mock(return_value=OperationError(
            code="unsupported_capability",
            message="password=operation-error-password",
            project_kind="test",
            requested_capability="ensure",
        ))
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=True)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as raised:
            commands.cmd_ensure({}, args)

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {
            "ok": False,
            "error": {
                "code": "unsupported_capability",
                "message": "password=[REDACTED]",
            },
        })
        self.assertNotIn("operation-error-password", stdout.getvalue())

    def test_cli_ensure_json_redacts_config_error(self):
        import sandbox.commands.instances_cmd as commands

        class FixtureConfigError(Exception):
            pass

        service = mock.Mock()
        service.invoke.side_effect = FixtureConfigError(
            "authorization=Bearer config-error-authorization")
        core = types.SimpleNamespace(ConfigError=FixtureConfigError)
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=True)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(commands, "_core", return_value=core), \
                mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as raised:
            commands.cmd_ensure({}, args)

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {
            "ok": False,
            "error": {
                "code": "config_error",
                "message": "authorization=[REDACTED]",
            },
        })
        self.assertNotIn("config-error-authorization", stdout.getvalue())

    def test_cli_ensure_human_errors_are_redacted(self):
        import sandbox.commands.instances_cmd as commands

        operation_service = mock.Mock()
        operation_service.invoke.return_value = OperationError(
            code="unsupported_capability",
            message="password=human-operation-password",
        )
        config_service = mock.Mock()
        config_service.invoke.side_effect = RuntimeError(
            "authorization=Bearer human-config-authorization")
        cases = (
            (operation_service, "human-operation-password"),
            (config_service, "human-config-authorization"),
        )
        for service, secret in cases:
            args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                         create=False, json=False, local=True)
            stderr = io.StringIO()
            with self.subTest(secret=secret), \
                    mock.patch.object(commands, "_core", return_value=types.SimpleNamespace(
                        ConfigError=RuntimeError)), \
                    mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                    contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                commands.cmd_ensure({}, args)
            self.assertNotIn(secret, stderr.getvalue())
            self.assertIn("[REDACTED]", stderr.getvalue())

    def test_cli_apply_rejects_before_legacy_apply(self):
        import sandbox.commands.config_setup as commands

        service = RejectingService()
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default", json=False)
        with mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                mock.patch.object(commands, "apply_config") as legacy, \
                self.assertRaises(SystemExit):
            commands.cmd_apply_config({}, args)
        legacy.assert_not_called()
        self.assertEqual(service.requests[0].operation, "apply")

    def test_cli_ensure_json_redacts_local_instance_credentials(self):
        import sandbox.commands.instances_cmd as commands
        from sandbox.runtimes.base import OperationResult

        class SuccessfulService:
            def invoke(self, request):
                return OperationResult(
                    True, "ensure", request.project_root, "wordpress",
                    {
                        "instance": "fixture",
                        "url": "https://fixture.tst",
                        "login_url": "https://fixture.tst/?sandbox_autologin=login-token",
                        "autologin_token": "raw-token",
                        "password": "raw-password",
                        "authorization": "Bearer raw-authorization",
                    },
                )

        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True)
        output = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service",
                              return_value=SuccessfulService()), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)
        serialized = output.getvalue()
        self.assertEqual(len(serialized.splitlines()), 1)
        payload = json.loads(serialized)
        self.assertEqual(payload["url"], "https://fixture.tst")
        self.assertEqual(
            payload["login_url"],
            "https://fixture.tst/?sandbox_autologin=%5BREDACTED%5D",
        )
        self.assertTrue(payload["login_url_redacted"])
        self.assertEqual(payload["autologin_token"], "[REDACTED]")
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["authorization"], "[REDACTED]")
        for secret in ("login-token", "raw-token", "raw-password", "raw-authorization"):
            self.assertNotIn(secret, serialized)

    def _ensure_json_payload(self, login_url, **overrides):
        """Run `cmd_ensure --json` over one fixture record and parse its line."""
        import sandbox.commands.instances_cmd as commands
        from sandbox.runtimes.base import OperationResult

        class SuccessfulService:
            def invoke(self, request):
                return OperationResult(
                    True, "ensure", request.project_root, "wordpress",
                    {
                        "instance": "fixture",
                        "url": "https://fixture.tst",
                        "login_url": login_url,
                        "autologin_token": "raw-token",
                        "password": "raw-password",
                    },
                )

        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, **overrides)
        output = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service",
                               return_value=SuccessfulService()), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)
        return output.getvalue(), json.loads(output.getvalue())

    def test_cli_ensure_json_reveal_login_returns_local_autologin_url(self):
        """--reveal-login is the documented opt-in for a loopback instance."""
        login_url = "https://fixture.tst/?sandbox_autologin=login-token"
        with mock.patch("socket.gethostbyname", return_value="127.0.0.42"):
            serialized, payload = self._ensure_json_payload(login_url, reveal_login=True)
        self.assertEqual(payload["login_url"], login_url)
        self.assertFalse(payload["login_url_redacted"])
        # The opt-in covers login_url alone; every other credential stays redacted.
        self.assertEqual(payload["autologin_token"], "[REDACTED]")
        self.assertEqual(payload["password"], "[REDACTED]")
        for secret in ("raw-token", "raw-password"):
            self.assertNotIn(secret, serialized)

    def test_cli_ensure_json_reveal_login_returns_remote_autologin_url(self):
        """A remote ensure record qualifies too: E2E runners need a usable login.

        The remote redacts its own JSON, so the flag has to reach the VPS and
        the revealed field has to survive the local parse.
        """
        import sandbox.commands.instances_cmd as commands

        login_url = "https://remote.fixture.tst/?sandbox_autologin=remote-login"
        remote_result = {
            "ok": True, "instance": "fixture-master",
            "url": "https://remote.fixture.tst",
            "login_url": login_url,
            "autologin_token": "remote-token",
            "target": {"remote": "fixture-remote", "workspace": "default"},
        }
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=False,
                                     reveal_login=True)
        output = io.StringIO()
        with mock.patch("sandbox.commands.lifecycle._remote_lifecycle",
                        return_value=remote_result), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)
        serialized = output.getvalue()
        payload = json.loads(serialized)
        self.assertEqual(payload["login_url"], login_url)
        self.assertFalse(payload["login_url_redacted"])
        self.assertEqual(payload["autologin_token"], "[REDACTED]")
        self.assertNotIn("remote-token", serialized)

    def test_remote_ensure_asks_the_vps_to_reveal_its_login_url(self):
        import sandbox.commands.lifecycle as commands
        from sandbox.core import _remote as remote

        login_url = "https://remote.fixture.tst/?sandbox_autologin=remote-login"
        record = {"ok": True, "instance": "fixture-master",
                  "url": "https://remote.fixture.tst", "login_url": login_url,
                  "autologin_token": "remote-token"}
        target = types.SimpleNamespace(
            kind="remote", remote={"ssh": "fixture.invalid"}, remote_name="fixture-remote",
            project_root="/tmp/project", workspace_label="default",
        )
        service = types.SimpleNamespace(resolve=lambda _request: target)
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(record), stderr="")
        args = types.SimpleNamespace(remote="fixture-remote", local=False,
                                     project_dir="/tmp/project", workspace="default",
                                     reveal_login=True)
        with mock.patch("sandbox.application.context.durable_job_dependencies",
                        return_value={"target_service": service}), \
                mock.patch.object(remote, "deploy_exact_working_tree",
                                  return_value={"target_path": "/srv/project"}), \
                mock.patch.object(remote, "prepare_remote_workspace",
                                  return_value="/srv/project-workspace"), \
                mock.patch.object(remote, "remote_sb_path", return_value="/srv/sandbox/sb"), \
                mock.patch.object(remote, "ssh_run", return_value=result) as ssh_run:
            ensured = commands._remote_lifecycle({}, args, "ensure")
        self.assertIn("--reveal-login", ssh_run.call_args.args[1])
        self.assertEqual(ensured["login_url"], login_url)
        # Only login_url is lifted out of the unredacted document.
        self.assertEqual(ensured["autologin_token"], "[REDACTED]")

    def test_remote_ensure_retries_without_reveal_login_on_older_runtime(self):
        """A remote staged from an older runtime must still boot the instance."""
        import sandbox.commands.lifecycle as commands
        from sandbox.core import _remote as remote

        record = {"ok": True, "instance": "fixture-master", "url": "http://localhost:8201"}
        rejected = types.SimpleNamespace(
            returncode=2, stdout="",
            stderr="sandbox: error: unrecognized arguments: --reveal-login")
        accepted = types.SimpleNamespace(returncode=0, stdout=json.dumps(record), stderr="")
        target = types.SimpleNamespace(
            kind="remote", remote={"ssh": "fixture.invalid"}, remote_name="fixture-remote",
            project_root="/tmp/project", workspace_label="default",
        )
        service = types.SimpleNamespace(resolve=lambda _request: target)
        args = types.SimpleNamespace(remote="fixture-remote", local=False,
                                     project_dir="/tmp/project", workspace="default",
                                     reveal_login=True)
        with mock.patch("sandbox.application.context.durable_job_dependencies",
                        return_value={"target_service": service}), \
                mock.patch.object(remote, "deploy_exact_working_tree",
                                  return_value={"target_path": "/srv/project"}), \
                mock.patch.object(remote, "prepare_remote_workspace",
                                  return_value="/srv/project-workspace"), \
                mock.patch.object(remote, "remote_sb_path", return_value="/srv/sandbox/sb"), \
                mock.patch.object(remote, "ssh_run",
                                  side_effect=[rejected, accepted]) as ssh_run, \
                contextlib.redirect_stdout(io.StringIO()):
            ensured = commands._remote_lifecycle({}, args, "ensure")
        self.assertEqual(ssh_run.call_count, 2)
        self.assertNotIn("--reveal-login", ssh_run.call_args.args[1])
        self.assertEqual(ensured["url"], "http://localhost:8201")

    def test_cli_ensure_json_reveal_login_refuses_non_loopback_host(self):
        """A deployed or rewritten URL never qualifies, flag or not."""
        with mock.patch("socket.gethostbyname", return_value="203.0.113.9"):
            serialized, payload = self._ensure_json_payload(
                "https://public.example/?sandbox_autologin=login-token",
                reveal_login=True,
            )
        self.assertEqual(
            payload["login_url"],
            "https://public.example/?sandbox_autologin=%5BREDACTED%5D",
        )
        self.assertTrue(payload["login_url_redacted"])
        self.assertNotIn("login-token", serialized)

    def test_cli_ensure_json_reveal_login_refuses_unresolvable_host(self):
        """Fail closed when the host cannot be proven loopback."""
        with mock.patch("socket.gethostbyname", side_effect=OSError("no such host")):
            serialized, payload = self._ensure_json_payload(
                "https://fixture.tst/?sandbox_autologin=login-token",
                reveal_login=True,
            )
        self.assertNotIn("login-token", serialized)
        self.assertIn("sandbox_autologin=%5BREDACTED%5D", payload["login_url"])
        self.assertTrue(payload["login_url_redacted"])

    def test_cli_ensure_json_reveal_login_keeps_already_redacted_input_safe(self):
        """A placeholder supplied by an older remote stays redacted."""
        serialized, payload = self._ensure_json_payload(
            "https://fixture.tst/?sandbox_autologin=%5BREDACTED%5D",
            reveal_login=True,
        )
        self.assertIn("sandbox_autologin=%5BREDACTED%5D", payload["login_url"])
        self.assertTrue(payload["login_url_redacted"])
        self.assertNotIn('"login_url_redacted":false', serialized)

    def test_cli_ensure_json_marks_malformed_autologin_url_redacted(self):
        import sandbox.commands.instances_cmd as commands

        malformed = "https://[broken/?sandbox_autologin=malformed-token"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            commands._print_ensure_json({
                "login_url": malformed,
                "login_url_redacted": False,
            })
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["login_url"], "[REDACTION_FAILED]")
        self.assertTrue(payload["login_url_redacted"])
        self.assertNotIn("malformed-token", output.getvalue())

    def test_cli_ensure_json_derives_redaction_status_instead_of_trusting_input(self):
        import sandbox.commands.instances_cmd as commands

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            commands._print_ensure_json({
                "login_url": "https://fixture.tst/?sandbox_autologin=secret",
                "login_url_redacted": False,
            })
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["login_url_redacted"])
        self.assertNotIn("secret", output.getvalue())

    def test_cli_ensure_json_omits_redaction_status_without_autologin(self):
        import sandbox.commands.instances_cmd as commands

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            commands._print_ensure_json({
                "login_url": "https://fixture.tst/wp-admin/",
                "login_url_redacted": False,
            })
        payload = json.loads(output.getvalue())
        self.assertNotIn("login_url_redacted", payload)

    def test_cli_ensure_json_redacts_remote_credentials(self):
        import sandbox.commands.instances_cmd as commands

        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=False)
        remote_result = {
            "ok": True,
            "url": "https://remote.fixture.tst",
            "login_url": "https://remote.fixture.tst/?sandbox_autologin=remote-login",
            "autologin_token": "remote-token",
            "nested": {"password": "remote-password", "state": "ready"},
        }
        output = io.StringIO()
        with mock.patch("sandbox.commands.lifecycle._remote_lifecycle",
                        return_value=remote_result), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)

        serialized = output.getvalue()
        self.assertEqual(len(serialized.splitlines()), 1)
        payload = json.loads(serialized)
        self.assertEqual(payload["url"], "https://remote.fixture.tst")
        self.assertEqual(payload["nested"]["state"], "ready")
        self.assertEqual(payload["autologin_token"], "[REDACTED]")
        self.assertEqual(payload["nested"]["password"], "[REDACTED]")
        self.assertIn("sandbox_autologin=%5BREDACTED%5D", payload["login_url"])
        self.assertTrue(payload["login_url_redacted"])
        for secret in ("remote-login", "remote-token", "remote-password"):
            self.assertNotIn(secret, serialized)

    def test_cli_ensure_json_redacts_managed_native_success_credentials(self):
        import sandbox.commands.instances_cmd as commands

        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            True, "ensure", "/tmp/project", "wordpress", {
                "ok": True,
                "state": "ready",
                "backend": {"address": "127.0.0.1", "port": 3306,
                            "password": "native-password"},
                "login_url": "https://native.fixture.tst/?sandbox_autologin=native-login",
                "autologin_token": "native-token",
            },
        )
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=True)
        output = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)

        serialized = output.getvalue()
        self.assertEqual(len(serialized.splitlines()), 1)
        payload = json.loads(serialized)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "ready")
        self.assertEqual(payload["backend"]["address"], "127.0.0.1")
        self.assertEqual(payload["backend"]["password"], "[REDACTED]")
        self.assertEqual(payload["autologin_token"], "[REDACTED]")
        self.assertIn("sandbox_autologin=%5BREDACTED%5D", payload["login_url"])
        self.assertTrue(payload["login_url_redacted"])
        for secret in ("native-password", "native-login", "native-token"):
            self.assertNotIn(secret, serialized)

    def test_cli_ensure_json_redacts_structured_failure_and_stderr(self):
        import sandbox.commands.instances_cmd as commands

        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            False, "ensure", "/tmp/project", "wordpress", {
                "ok": False,
                "reason": {
                    "code": "provision_failed",
                    "message": "password=failure-password",
                    "failed_after": ["database"],
                },
                "login_url": "https://failure.fixture.tst/?sandbox_autologin=failure-login",
                "authorization": "Bearer failure-authorization",
            },
        )
        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True, local=True)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service", return_value=service), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                self.assertRaises(SystemExit):
            commands.cmd_ensure({}, args)

        serialized = stdout.getvalue()
        self.assertEqual(len(serialized.splitlines()), 1)
        payload = json.loads(serialized)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"]["code"], "provision_failed")
        self.assertEqual(payload["reason"]["message"], "password=[REDACTED]")
        self.assertEqual(payload["authorization"], "[REDACTED]")
        self.assertIn("sandbox_autologin=%5BREDACTED%5D", payload["login_url"])
        combined = serialized + stderr.getvalue()
        for secret in ("failure-password", "failure-login", "failure-authorization"):
            self.assertNotIn(secret, combined)

    def test_registered_generic_instance_can_use_generated_long_runtime_id(self):
        import sandbox.commands.instances_cmd as commands
        from sandbox.runtimes.base import OperationResult

        runtime_id = "generic-workspace-" + "a" * 30
        owner = {
            "root": "/tmp/generic-project",
            "kind": "compose",
            "label": "default",
        }
        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            True, "destroy", owner["root"], "compose", {})
        args = types.SimpleNamespace(
            action="delete", name=runtime_id, yes=True)

        with mock.patch.object(
                commands, "_core",
                return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: owner)), \
                mock.patch.object(
                    commands, "runtime_service", return_value=service), \
                contextlib.redirect_stdout(io.StringIO()):
            commands.cmd_instance({}, args)

        request = service.invoke.call_args.args[0]
        self.assertEqual(request.operation, "destroy")
        self.assertEqual(request.project_root, owner["root"])

    def test_instance_inventory_json_omits_autologin_urls(self):
        import sandbox.commands.instances_cmd as commands

        args = types.SimpleNamespace(project_dir=None, json=True)
        rows = [{
            "name": "fixture",
            "url": "http://localhost:8188",
            "login_url": (
                "http://localhost:8188/?sandbox_autologin=secret-token"),
        }]
        output = io.StringIO()
        with mock.patch.object(
                commands, "collect_instance_rows", return_value=rows), \
                contextlib.redirect_stdout(output):
            commands.cmd_instances({}, args)

        payload = json.loads(output.getvalue())
        self.assertNotIn("login_url", payload["instances"][0])
        self.assertNotIn("secret-token", output.getvalue())


class TestRemoteInstanceControl(unittest.TestCase):
    def test_remote_inventory_uses_selected_remote_without_local_rows(self):
        import sandbox.commands.instances_cmd as commands

        args = types.SimpleNamespace(
            remote="remote-a", local=False, project_dir=None, json=True,
        )
        entry = {"provisioned": True}
        output = io.StringIO()
        with mock.patch("sandbox.core._remote.get_remote", return_value=entry), \
                mock.patch("sandbox.core._remote.list_remote_instances",
                           return_value=[{"name": "preview-a", "label": "qa"}]), \
                mock.patch.object(commands, "collect_instance_rows") as local_rows, \
                contextlib.redirect_stdout(output):
            commands.cmd_instances({}, args)
        local_rows.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["remote"], "remote-a")

    def test_remote_inventory_omits_autologin_and_bearer_equivalent_fields(self):
        import sandbox.core._remote as remote

        response = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "instances": [{
                "name": "preview-a", "label": "qa", "status": "ready",
                "url": "https://preview.example.test",
                "login_url": (
                    "https://preview.example.test/?sandbox_autologin=secret-token"
                ),
                "autologin_token": "secret-token",
                "bearer_token": "bearer-secret",
            }]}) + "\n",
            stderr="",
        )
        with mock.patch.object(remote, "remote_sb_path", return_value="/srv/sb"), \
                mock.patch.object(remote, "ssh_run", return_value=response):
            rows = remote.list_remote_instances({"ssh": "host"})

        self.assertEqual(rows, [{
            "name": "preview-a", "label": "qa", "status": "ready",
            "url": "https://preview.example.test",
        }])
        self.assertNotIn("secret-token", json.dumps(rows))
        self.assertNotIn("bearer-secret", json.dumps(rows))

    def test_remote_delete_requires_exact_name_and_yes(self):
        import sandbox.commands.instances_cmd as commands

        args = types.SimpleNamespace(
            action="delete", name="preview-a", yes=True,
            remote="remote-a", local=False,
        )
        entry = {"provisioned": True}
        with mock.patch("sandbox.core._remote.get_remote", return_value=entry), \
                mock.patch("sandbox.core._remote.delete_remote_instance") as delete, \
                contextlib.redirect_stdout(io.StringIO()):
            commands.cmd_instance({}, args)
        delete.assert_called_once_with(entry, "preview-a")


class TestStatusJsonRedaction(unittest.TestCase):
    def _status_args(self):
        return types.SimpleNamespace(json=True, resolved_instance="fixture")

    def _capture_status(self, commands, cfg, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            commands.cmd_status(cfg, args)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        return json.loads(output.getvalue())

    def _capture_failed_status(self, commands, cfg, args, expected=1):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as raised:
            commands.cmd_status(cfg, args)
        self.assertEqual(raised.exception.code, expected)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        return json.loads(output.getvalue())

    def test_status_json_sanitizer_removes_sensitive_keys_and_redacts_assignment(self):
        from sandbox.commands.lifecycle import _public_status_json

        payload = _public_status_json({
            "url": "https://fixture.test/health",
            "login_url": "https://fixture.test/?sandbox_autologin=fixture-secret",
            "nested": {
                "accessToken": "fixture-token",
                "detail": "open https://fixture.test/?sandbox_autologin=fixture-secret&view=health",
                "status": "ready",
            },
        })

        self.assertEqual(payload["url"], "https://fixture.test/health")
        self.assertEqual(payload["nested"]["status"], "ready")
        self.assertNotIn("login_url", payload)
        self.assertNotIn("accessToken", payload["nested"])
        self.assertEqual(
            payload["nested"]["detail"],
            "open https://fixture.test/?sandbox_autologin=%5BREDACTED%5D&view=health",
        )

    def test_status_json_redacts_nested_wordpress_runtime_data(self):
        import sandbox.commands.lifecycle as commands

        owner = {"root": "/tmp/wordpress-project", "label": "default", "url": "https://fixture.test"}
        service = mock.Mock()
        service.invoke.return_value = OperationResult(True, "status", owner["root"], "wordpress", {
            "status": "ready",
            "nested": {
                "login_url": "https://fixture.test/?sandbox_autologin=fixture-secret",
                "public_url": "https://fixture.test/wp-json/",
                "message": "resume at ?sandbox_autologin=fixture-secret&view=diagnostics",
                "mapping_diagnostics": [types.MappingProxyType({
                    "access_token": "fixture-token",
                    "url": "https://fixture.test/status",
                    "status": "ready",
                })],
            },
        })

        with mock.patch.object(commands, "_remote_lifecycle", return_value=None), \
                mock.patch.object(commands, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: owner)), \
                mock.patch.object(commands, "runtime_service", return_value=service), \
                mock.patch.object(commands, "resolve_instances", return_value={"fixture": {}}), \
                mock.patch.object(commands, "_is_herd_instance", return_value=False), \
                mock.patch.object(commands, "_instance_reachable", return_value=True), \
                mock.patch.object(commands, "php_extension_status", return_value=None):
            payload = self._capture_status(commands, {}, self._status_args())

        self.assertEqual(payload["url"], "https://fixture.test")
        self.assertEqual(payload["runtime"]["status"], "ready")
        self.assertEqual(payload["runtime"]["nested"]["public_url"], "https://fixture.test/wp-json/")
        self.assertNotIn("login_url", payload["runtime"]["nested"])
        mapping_diagnostic = payload["runtime"]["nested"]["mapping_diagnostics"][0]
        self.assertEqual(mapping_diagnostic["url"], "https://fixture.test/status")
        self.assertEqual(mapping_diagnostic["status"], "ready")
        self.assertNotIn("access_token", mapping_diagnostic)
        self.assertEqual(
            payload["runtime"]["nested"]["message"],
            "resume at ?sandbox_autologin=[REDACTED]&view=diagnostics",
        )
        self.assertNotIn("php_extensions", payload)
        self.assertNotIn("exit_code", payload)

    def test_status_json_redacts_generic_compose_runtime_data(self):
        import sandbox.commands.lifecycle as commands

        owner = {"root": "/tmp/compose-project", "kind": "compose", "label": "default"}
        service = mock.Mock()
        service.invoke.return_value = OperationResult(True, "status", owner["root"], "compose", {
            "url": "https://compose.fixture.test/health",
            "status": "healthy",
            "session_cookie": "fixture-cookie",
            "diagnostics": ["https://compose.fixture.test/?sandbox_autologin=fixture-secret#details"],
        })

        with mock.patch.object(commands, "_remote_lifecycle", return_value=None), \
                mock.patch.object(commands, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: owner)), \
                mock.patch.object(commands, "runtime_service", return_value=service):
            payload = self._capture_status(commands, {}, self._status_args())

        self.assertEqual(payload["url"], "https://compose.fixture.test/health")
        self.assertEqual(payload["status"], "healthy")
        self.assertNotIn("session_cookie", payload)
        self.assertEqual(
            payload["diagnostics"],
            ["https://compose.fixture.test/?sandbox_autologin=%5BREDACTED%5D"],
        )

    def test_status_json_redacts_remote_result(self):
        import sandbox.commands.lifecycle as commands

        remote_result = {
            "ok": True,
            "status": "ready",
            "url": "https://remote.fixture.test/status",
            "authorization": "Bearer fixture-secret",
            "output": "https://remote.fixture.test/?sandbox_autologin=fixture-secret&next=status",
        }
        with mock.patch.object(commands, "_remote_lifecycle", return_value=remote_result):
            payload = self._capture_status(commands, {}, self._status_args())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["url"], "https://remote.fixture.test/status")
        self.assertNotIn("authorization", payload)
        self.assertEqual(
            payload["output"],
            "https://remote.fixture.test/?sandbox_autologin=%5BREDACTED%5D&next=status",
        )

    def test_status_text_redacts_remote_extension_report(self):
        import sandbox.commands.lifecycle as commands

        marker = "github_pat_" + "fixturecredentialvalue1234567890"
        remote_result = {
            "ok": True,
            "status": "ready",
            "php_extensions": {
                "ok": True,
                "desired": {"profile": "default", "catalog": {}},
                "observed": {"web": {"state": "ready", "php_version": marker}},
                "issues": [{"code": "plane_drift", "message": marker}],
            },
        }
        args = types.SimpleNamespace(json=False, resolved_instance="fixture", workspace="default")
        output = io.StringIO()
        with mock.patch.object(commands, "_remote_lifecycle", return_value=remote_result), \
                contextlib.redirect_stdout(output):
            commands.cmd_status({}, args)

        self.assertNotIn(marker, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_status_json_emits_one_document_before_matching_extension_exit(self):
        import sandbox.commands.lifecycle as commands

        remote_result = {
            "ok": False,
            "exit_code": 1,
            "php_extensions": {"ok": False, "exit_code": 1,
                               "issues": [{"code": "missing"}]},
        }
        with mock.patch.object(commands, "_remote_lifecycle", return_value=remote_result):
            payload = self._capture_failed_status(commands, {}, self._status_args())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["php_extensions"]["issues"][0]["code"], "missing")

    def test_status_json_fails_closed_on_inconsistent_remote_zero_exit(self):
        import sandbox.commands.lifecycle as commands

        remote_result = {
            "ok": False,
            "exit_code": 0,
            "php_extensions": {"ok": False, "exit_code": 0,
                               "issues": [{"code": "plane_drift"}]},
        }
        with mock.patch.object(commands, "_remote_lifecycle", return_value=remote_result):
            payload = self._capture_failed_status(commands, {}, self._status_args())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], 1)

    def test_remote_nonzero_valid_status_json_is_forwarded(self):
        import sandbox.commands.lifecycle as commands
        import sandbox.core._remote as remote

        document = {"ok": False, "exit_code": 1,
                    "php_extensions": {"ok": False, "exit_code": 1,
                                       "issues": [{"code": "plane_drift"}]}}
        target = types.SimpleNamespace(
            kind="remote", remote={"ssh": "fixture.invalid"}, remote_name="fixture-remote",
            project_root="/tmp/project", workspace_label="default",
        )
        service = types.SimpleNamespace(resolve=lambda _request: target)
        result = types.SimpleNamespace(returncode=1, stdout=json.dumps(document), stderr="")
        args = types.SimpleNamespace(remote="fixture-remote", local=False,
                                     project_dir="/tmp/project", workspace="default")
        with mock.patch("sandbox.application.context.durable_job_dependencies",
                        return_value={"target_service": service}), \
                mock.patch.object(remote, "remote_workspace_path", return_value="/srv/project"), \
                mock.patch.object(remote, "remote_sb_path", return_value="/srv/sandbox/sb"), \
                mock.patch.object(remote, "ssh_run", return_value=result):
            forwarded = commands._remote_lifecycle({}, args, "status")
        self.assertFalse(forwarded["ok"])
        self.assertEqual(forwarded["exit_code"], 1)
        self.assertEqual(forwarded["php_extensions"], document["php_extensions"])

    def test_direct_remote_instance_status_uses_explicit_inner_selector(self):
        import sandbox.commands.lifecycle as commands
        import sandbox.core._remote as remote

        result = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "instance": "remote-instance",
                               "status": "ready"}),
            stderr="",
        )
        args = types.SimpleNamespace(
            remote="fixture-remote", local=False, instance="remote-instance",
            project_dir=None, workspace=None, refresh=True,
        )
        with mock.patch.object(remote, "get_remote", return_value={
                    "provisioned": True, "capabilities": ["job.exec"],
                }), \
                mock.patch.object(remote, "remote_sb_path",
                                  return_value="/srv/sandbox/sb"), \
                mock.patch.object(remote, "ssh_run", return_value=result) as ssh_run:
            observed = commands._remote_lifecycle({}, args, "status")

        command = ssh_run.call_args.args[1]
        self.assertIn("/srv/sandbox/sb status --local --instance remote-instance", command)
        self.assertIn("--json", command)
        self.assertIn("--refresh", command)
        self.assertEqual(observed["instance"], "remote-instance")
        self.assertEqual(observed["target"], {
            "remote": "fixture-remote", "instance": "remote-instance",
        })

    def test_remote_observation_child_uses_staged_root_and_not_outer_label(self):
        """Outer workspace labels must not select an inner instance."""
        import sandbox.commands.lifecycle as commands
        import sandbox.core._remote as remote

        target = types.SimpleNamespace(
            kind="remote", remote={"ssh": "fixture.invalid"}, remote_name="fixture-remote",
            project_root="/tmp/project", workspace_label="outer-workspace",
        )
        service = types.SimpleNamespace(resolve=lambda _request: target)
        for action, stdout in (
            ("status", json.dumps({"ok": True, "status": "ready"})),
            ("logs", "wp: ready\n"),
        ):
            args = types.SimpleNamespace(
                remote="fixture-remote", local=False, project_dir="/tmp/project",
                workspace="outer-workspace",
            )
            result = types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
            with self.subTest(action=action), \
                    mock.patch("sandbox.application.context.durable_job_dependencies",
                               return_value={"target_service": service}), \
                    mock.patch.object(remote, "remote_workspace_path",
                                      return_value="/srv/staged-project"), \
                    mock.patch.object(remote, "remote_sb_path",
                                      return_value="/srv/sandbox/sb"), \
                    mock.patch.object(remote, "ssh_run", return_value=result) as ssh_run:
                observed = commands._remote_lifecycle({}, args, action)

            command = ssh_run.call_args.args[1]
            self.assertEqual(
                command.split()[:5],
                ["/srv/sandbox/sb", action, "--local", "--project-dir", "/srv/staged-project"],
            )
            self.assertNotIn("--workspace", command)
            self.assertNotIn("--label", command)
            if action == "status":
                self.assertIn("--json", command)
                self.assertEqual(observed["status"], "ready")
            else:
                self.assertEqual(observed["output"], stdout)

    def test_remote_ensure_requests_json_and_returns_instance_record(self):
        """Remote ensure must carry the instance record, not a bare ok flag.

        Without `--json` on the remote command the VPS prints human text, the
        parser finds no document, and every caller (the MCP server, project
        `sb ensure --json` consumers) loses the URL.
        """
        import sandbox.commands.lifecycle as commands
        from sandbox.core import _remote as remote

        record = {"ok": True, "instance": "fixture-master", "url": "http://localhost:8201",
                  "wordpress_port": 8201}
        target = types.SimpleNamespace(
            kind="remote", remote={"ssh": "fixture.invalid"}, remote_name="fixture-remote",
            project_root="/tmp/project", workspace_label="default",
        )
        service = types.SimpleNamespace(resolve=lambda _request: target)
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(record), stderr="")
        args = types.SimpleNamespace(remote="fixture-remote", local=False,
                                     project_dir="/tmp/project", workspace="default")
        with mock.patch("sandbox.application.context.durable_job_dependencies",
                        return_value={"target_service": service}), \
                mock.patch.object(remote, "deploy_exact_working_tree",
                                  return_value={"target_path": "/srv/project"}), \
                mock.patch.object(remote, "prepare_remote_workspace",
                                  return_value="/srv/project-workspace"), \
                mock.patch.object(remote, "remote_sb_path", return_value="/srv/sandbox/sb"), \
                mock.patch.object(remote, "ssh_run", return_value=result) as ssh_run:
            ensured = commands._remote_lifecycle({}, args, "ensure")
        command = ssh_run.call_args.args[1]
        self.assertIn("--json", command)
        self.assertIn("--label default", command)
        self.assertIn("--create", command)
        self.assertEqual(ensured["url"], "http://localhost:8201")
        self.assertEqual(ensured["instance"], "fixture-master")
        self.assertEqual(ensured["target"]["remote"], "fixture-remote")

if __name__ == "__main__":
    unittest.main()
