import argparse
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import lifecycle


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
        result = SimpleNamespace(data={"url": "http://demo.local"})
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


if __name__ == "__main__":
    unittest.main()
