import contextlib
import io
import json
import secrets
import types
import unittest
from unittest import mock

from sandbox.commands import lifecycle
from sandbox.runtimes.base import OperationResult


class StatusJsonDisclosureTests(unittest.TestCase):
    def test_local_compose_dynamic_names_and_values_are_sanitized(self):
        marker = secrets.token_urlsafe(24)
        name = "?sandbox_autologin=" + marker + "&view=health"
        owner = {"kind": "compose", "root": "/fixture", "label": "default"}
        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            True, "status", owner["root"], "compose",
            {"status": "healthy", "diagnostics": [{name: "ready", "message": name,
                                                     "sandbox_autologin": marker,
                                                     "api_key": marker}],
             "url": "https://fixture.test/health"},
        )
        args = types.SimpleNamespace(json=True, resolved_instance="fixture")
        output = io.StringIO()
        with mock.patch.object(lifecycle, "_remote_lifecycle", return_value=None), \
                mock.patch.object(lifecycle, "_core", return_value=types.SimpleNamespace(
                    registry_find_instance=lambda _name: owner)), \
                mock.patch.object(lifecycle, "runtime_service", return_value=service), \
                contextlib.redirect_stdout(output):
            lifecycle.cmd_status({}, args)

        rendered = output.getvalue()
        self.assertFalse(marker in rendered, "autologin material reached status JSON")
        payload = json.loads(rendered)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["url"], "https://fixture.test/health")
        diagnostic = payload["diagnostics"][0]
        self.assertNotIn("sandbox_autologin", diagnostic)
        self.assertNotIn("api_key", diagnostic)
        self.assertEqual(diagnostic["?sandbox_autologin=[REDACTED]&view=health"], "ready")
        self.assertEqual(diagnostic["message"], "?sandbox_autologin=[REDACTED]&view=health")

    def test_sensitive_field_variants_keep_unrelated_status_metadata(self):
        fields = ("private_key", "private-key", "PRIVATE_KEY", "Private-Key",
                  "basic_auth", "basic-auth", "BASIC_AUTH", "Basic-Auth",
                  "access_key", "access-key", "ACCESS_KEY", "Access-Key")
        marker = secrets.token_urlsafe(24)
        result = {"ok": True, "status": "healthy", "details": {
            **{field: marker for field in fields},
            "display_name": "fixture", "url": "https://fixture.test/health"}}
        output = io.StringIO()
        args = types.SimpleNamespace(json=True, resolved_instance="fixture")
        with mock.patch.object(lifecycle, "_remote_lifecycle", return_value=result), \
                contextlib.redirect_stdout(output):
            lifecycle.cmd_status({}, args)
        self.assertFalse(marker in output.getvalue(), "sensitive field reached status JSON")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["details"], {"display_name": "fixture",
                                               "url": "https://fixture.test/health"})

    def test_remote_failure_dynamic_name_is_sanitized(self):
        marker = secrets.token_urlsafe(24)
        name = "?sandbox_autologin=" + marker + "&view=detail"
        result = {"ok": False, "error": {"code": "unavailable", "details": {name: "blocked"}}}
        args = types.SimpleNamespace(json=True, resolved_instance="fixture")
        output = io.StringIO()
        with mock.patch.object(lifecycle, "_remote_lifecycle", return_value=result), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            lifecycle.cmd_status({}, args)

        self.assertEqual(raised.exception.code, 1)
        rendered = output.getvalue()
        self.assertFalse(marker in rendered, "autologin material reached status JSON")
        payload = json.loads(rendered)
        self.assertEqual(payload["error"]["code"], "unavailable")
        self.assertEqual(payload["error"]["details"]["?sandbox_autologin=[REDACTED]&view=detail"],
                         "blocked")


if __name__ == "__main__":
    unittest.main()
