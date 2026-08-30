import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.debug import cmd_selftest


class TestSelftestEnvironment(unittest.TestCase):
    @patch("sandbox.commands.debug.ok")
    @patch("subprocess.run", return_value=SimpleNamespace(returncode=0))
    def test_selftest_child_excludes_unrelated_parent_keys(self, run, _ok):
        parent = {"PATH": "/synthetic/bin", "UNRELATED_SYNTHETIC": "absent"}
        with patch("sandbox.services.environment.os.environ", parent):
            cmd_selftest({}, SimpleNamespace())

        child_environment = run.call_args.kwargs["env"]
        self.assertEqual(child_environment["PATH"], "/synthetic/bin")
        self.assertEqual(child_environment["PYTHONUTF8"], "1")
        self.assertNotIn("UNRELATED_SYNTHETIC", child_environment)
        self.assertEqual(run.call_args.kwargs["timeout"], 1800)
        self.assertIs(run.call_args.kwargs["shell"], False)
