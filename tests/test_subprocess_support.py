import unittest
from unittest.mock import patch

from tests.subprocess_support import run_test_process, synthetic_environment


class TestSubprocessSupport(unittest.TestCase):
    def test_synthetic_environment_reads_only_compatibility_keys(self):
        parent = {"PATH": "/synthetic/bin", "UNRELATED_SYNTHETIC": "absent"}
        with patch("sandbox.services.environment.os.environ", parent):
            child = synthetic_environment({"EXPLICIT_SYNTHETIC": "present"})
        self.assertEqual(child["PATH"], "/synthetic/bin")
        self.assertEqual(child["EXPLICIT_SYNTHETIC"], "present")
        self.assertNotIn("UNRELATED_SYNTHETIC", child)
        self.assertEqual(repr(child), "<explicit child environment: 2 variables>")
        self.assertNotIn("EXPLICIT_SYNTHETIC", repr(child))
        self.assertNotIn("present", repr(child))

    def test_parent_environment_cannot_be_supplied_as_overrides(self):
        parent = {"PATH": "/synthetic/bin"}
        with patch("sandbox.services.environment.os.environ", parent), \
                self.assertRaisesRegex(ValueError, "parent environment"):
            synthetic_environment(parent)

    @patch("tests.subprocess_support.subprocess.run")
    def test_runner_owns_explicit_environment_timeout_and_shell(self, run):
        run_test_process(("fixture",), env={"ONLY_SYNTHETIC": "yes"}, timeout=3)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["env"]["ONLY_SYNTHETIC"], "yes")
        self.assertNotIn("UNRELATED_SYNTHETIC", kwargs["env"])
        self.assertEqual(kwargs["timeout"], 3)
        self.assertIs(kwargs["shell"], False)
