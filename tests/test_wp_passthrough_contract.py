import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import wp


class TestWpPassthroughContract(unittest.TestCase):
    def test_redundant_wp_token_is_rejected_before_runtime(self):
        output = io.StringIO()
        args = SimpleNamespace(
            resolved_instance="demo",
            passthrough=["--", "wp", "--require=fixture.php", "eval-file", "run.php"],
            run_async=False,
            timeout=60,
            project_dir="/tmp/project",
        )
        with patch.object(wp, "preflight_instance_capability", return_value=None), \
             patch.object(wp, "wpcli") as wpcli, \
             contextlib.redirect_stderr(output), \
             self.assertRaises(SystemExit) as raised:
            wp.cmd_wp({}, args)

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("do not repeat", output.getvalue())
        self.assertIn("No command was executed", output.getvalue())
        wpcli.assert_not_called()

    def test_normal_passthrough_is_not_rejected(self):
        wp._reject_redundant_wp_token(["option", "get", "missing_key"])


if __name__ == "__main__":
    unittest.main()
