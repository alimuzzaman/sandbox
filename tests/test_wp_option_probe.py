import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import wp


class TestWpOptionProbe(unittest.TestCase):
    def _args(self, passthrough, *, allow_missing=True):
        return SimpleNamespace(
            resolved_instance="demo", passthrough=passthrough,
            run_async=False, timeout=60, project_dir="/tmp/project",
            allow_missing=allow_missing,
        )

    def test_missing_option_emits_explicit_json_marker(self):
        result = SimpleNamespace(
            returncode=1, stdout="",
            stderr="Error: Could not get 'xspeed_cache' option. Does it exist?\n",
        )
        output = io.StringIO()
        with patch.object(wp, "preflight_instance_capability", return_value=None), \
             patch.object(wp, "wpcli", return_value=result) as wpcli, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            wp.cmd_wp({}, self._args(["option", "get", "xspeed_cache"]))

        self.assertEqual(json.loads(output.getvalue()), {"present": False, "value": None})
        wpcli.assert_called_once()

    def test_transport_failure_is_not_misclassified_as_missing(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="Error: connection refused\n")
        with patch.object(wp, "preflight_instance_capability", return_value=None), \
             patch.object(wp, "wpcli", return_value=result), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error, \
             self.assertRaises(SystemExit) as raised:
            wp.cmd_wp({}, self._args(["option", "get", "xspeed_cache"]))
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("wp command failed", error.getvalue())

    def test_allow_missing_rejects_other_commands_before_runtime(self):
        error = io.StringIO()
        with patch.object(wp, "preflight_instance_capability", return_value=None), \
             patch.object(wp, "wpcli") as wpcli, \
             contextlib.redirect_stderr(error), \
             self.assertRaises(SystemExit):
            wp.cmd_wp({}, self._args(["plugin", "list"]))
        self.assertIn("only valid with `option get KEY`", error.getvalue())
        wpcli.assert_not_called()


if __name__ == "__main__":
    unittest.main()
