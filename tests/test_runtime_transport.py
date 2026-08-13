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

    def test_cli_ensure_json_never_prints_autologin_secret(self):
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
                    },
                )

        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True)
        output = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service",
                              return_value=SuccessfulService()), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)
        self.assertNotIn("raw-token", output.getvalue())
        self.assertNotIn("autologin_token", output.getvalue())
        self.assertIn("login_url", output.getvalue())
        self.assertIn("https://fixture.tst", output.getvalue())

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


class TestStatusJsonRedaction(unittest.TestCase):
    def _status_args(self):
        return types.SimpleNamespace(json=True, resolved_instance="fixture")

    def _capture_status(self, commands, cfg, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            commands.cmd_status(cfg, args)
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
            "open https://fixture.test/?sandbox_autologin=[REDACTED]&view=health",
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
            ["https://compose.fixture.test/?sandbox_autologin=[REDACTED]#details"],
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
            "https://remote.fixture.test/?sandbox_autologin=[REDACTED]&next=status",
        )

if __name__ == "__main__":
    unittest.main()
