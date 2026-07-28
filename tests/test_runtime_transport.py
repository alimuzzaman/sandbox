import types
import unittest
import contextlib
import io
from unittest import mock

from sandbox.runtimes.base import OperationError


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
        import json
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

if __name__ == "__main__":
    unittest.main()
