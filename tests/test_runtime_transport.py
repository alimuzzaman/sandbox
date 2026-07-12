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
                        "login_url": "https://fixture.tst/?sandbox_autologin=sensitive",
                        "autologin_token": "sensitive",
                    },
                )

        args = types.SimpleNamespace(project_dir="/tmp/project", label="default",
                                     create=False, json=True)
        output = io.StringIO()
        with mock.patch.object(commands, "wordpress_runtime_service",
                               return_value=SuccessfulService()), \
                contextlib.redirect_stdout(output):
            commands.cmd_ensure({}, args)
        self.assertNotIn("sensitive", output.getvalue())
        self.assertNotIn("login_url", output.getvalue())
        self.assertIn("https://fixture.tst", output.getvalue())

if __name__ == "__main__":
    unittest.main()
