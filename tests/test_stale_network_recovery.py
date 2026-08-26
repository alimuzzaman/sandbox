import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import lifecycle


class TestStaleNetworkRecovery(unittest.TestCase):
    def test_missing_network_has_scoped_typed_recovery(self):
        output = io.StringIO()
        result = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error response from daemon: network sandbox-demo_default not found",
        )
        with patch.object(lifecycle, "compose", return_value=result) as compose, \
             contextlib.redirect_stderr(output), \
             self.assertRaises(SystemExit) as raised:
            lifecycle._compose_up("demo", ("wp", "db", "mailpit"))

        self.assertEqual(raised.exception.code, 1)
        message = output.getvalue()
        self.assertIn("stale_container_network", message)
        self.assertIn("./sb down --instance demo && ./sb up --instance demo", message)
        self.assertIn("no containers or volumes were removed", message)
        compose.assert_called_once_with(
            "up", "-d", "--remove-orphans", "wp", "db", "mailpit",
            instance="demo", check=False, capture=True,
        )

    def test_success_preserves_compose_output(self):
        output = io.StringIO()
        result = SimpleNamespace(returncode=0, stdout="db Started\n", stderr="")
        with patch.object(lifecycle, "compose", return_value=result), \
             contextlib.redirect_stdout(output):
            returned = lifecycle._compose_up("demo", ("wp",))

        self.assertIs(returned, result)
        self.assertEqual(output.getvalue(), "db Started\n")

    def test_unrelated_failure_is_bounded(self):
        output = io.StringIO()
        result = SimpleNamespace(
            returncode=17,
            stdout="",
            stderr="service failed " + ("x" * 1000),
        )
        with patch.object(lifecycle, "compose", return_value=result), \
             contextlib.redirect_stderr(output), \
             self.assertRaises(SystemExit) as raised:
            lifecycle._compose_up("demo", ("wp",))

        self.assertEqual(raised.exception.code, 17)
        self.assertLessEqual(len(output.getvalue()), 560)
        self.assertIn("docker compose up failed with exit code 17", output.getvalue())


if __name__ == "__main__":
    unittest.main()
