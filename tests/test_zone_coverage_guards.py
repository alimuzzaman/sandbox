import tempfile
import unittest
from unittest import mock

from sandbox.hosting.zone_coverage import program
from tests.zone_coverage_test_support import store_transport, validate_store_command


class StoreGuardTests(unittest.TestCase):
    def test_canonical_command_runs_fixed_program(self):
        with tempfile.TemporaryDirectory() as home:
            result = store_transport(home)({}, program.remote_command({"action": "list"}))
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout or result.stderr)

    def test_rejects_before_launch(self):
        command = program.remote_command({"action": "invalid"})
        for bad in [command + " ", command + "; touch /tmp/bad", "python3 -c pass", "'unterminated", "echo zone-coverage"]:
            with self.subTest(command=bad[:30]), mock.patch("tests.zone_coverage_test_support.run_test_process") as run:
                with self.assertRaises(AssertionError):
                    store_transport("/unused")({}, bad)
                run.assert_not_called()
