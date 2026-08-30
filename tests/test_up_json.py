import argparse
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import lifecycle
from sandbox.runtimes.base import OperationError, OperationResult


class TestUpJson(unittest.TestCase):
    def test_parser_accepts_json(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        lifecycle.configure_parser(sub)

        args = parser.parse_args(["up", "--json"])

        self.assertTrue(args.json)

    def test_generic_compose_success_is_one_json_document(self):
        output = io.StringIO()
        owner = {"kind": "compose", "root": "/tmp/demo", "label": "default"}
        result = OperationResult(
            True, "start", "/tmp/demo", "compose", {"url": "http://demo.local"},
        )
        args = SimpleNamespace(resolved_instance="demo", json=True)
        with patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                registry_find_instance=lambda _name: owner)), \
             patch.object(lifecycle, "runtime_service") as runtime, \
             contextlib.redirect_stdout(output):
            runtime.return_value.invoke.return_value = result
            lifecycle.cmd_up({}, args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload, {
            "command": "up",
            "instance": "demo",
            "ok": True,
            "runtime": "compose",
            "url": "http://demo.local",
        })

    def test_generic_operation_error_is_one_redacted_json_failure(self):
        output = io.StringIO()
        errors = io.StringIO()
        owner = {"kind": "compose", "root": "/tmp/demo", "label": "default"}
        args = SimpleNamespace(resolved_instance="demo", json=True)
        result = OperationError(
            "runtime_unavailable",
            "start failed token=super-secret " + ("x" * 1000),
            project_kind="compose",
        )
        with patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                registry_find_instance=lambda _name: owner)), \
             patch.object(lifecycle, "runtime_service") as runtime, \
             contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(errors), \
             self.assertRaises(SystemExit) as raised:
            runtime.return_value.invoke.return_value = result
            lifecycle.cmd_up({}, args)

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "runtime_unavailable")
        self.assertIn("[REDACTED]", payload["error"]["message"])
        self.assertNotIn("super-secret", output.getvalue())
        self.assertLessEqual(len(payload["error"]["message"]), 500)

    def test_generic_failed_result_is_one_typed_json_failure(self):
        output = io.StringIO()
        errors = io.StringIO()
        owner = {"kind": "compose", "root": "/tmp/demo", "label": "default"}
        args = SimpleNamespace(resolved_instance="demo", json=True)
        result = OperationResult(False, "start", "/tmp/demo", "compose", {
            "mutated": False,
            "error": {"code": "stale_container_network", "message": "network is missing"},
            "recovery": {"command": "./sb down --instance demo && ./sb up --instance demo"},
        })
        with patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                registry_find_instance=lambda _name: owner)), \
             patch.object(lifecycle, "runtime_service") as runtime, \
             contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(errors), \
             self.assertRaises(SystemExit) as raised:
            runtime.return_value.invoke.return_value = result
            lifecycle.cmd_up({}, args)

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "stale_container_network")
        self.assertFalse(payload["mutated"])
        self.assertEqual(
            payload["recovery"]["command"],
            "./sb down --instance demo && ./sb up --instance demo",
        )


if __name__ == "__main__":
    unittest.main()
