import sys
import unittest

from sandbox.services.process import BoundedProcessRunner


class TestBoundedProcessRunner(unittest.TestCase):
    def test_argument_list_cwd_environment_redaction_and_limit(self):
        secret = "recovery-secret-sentinel"
        runner = BoundedProcessRunner(max_output=20, secret_values=(secret,))
        result = runner.run([
            sys.executable, "-c",
            "import os; print(os.getcwd()); print(os.environ['FIXTURE_SECRET']); print('x'*100)",
        ], cwd="/tmp", env={"FIXTURE_SECRET": secret}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)
        self.assertLessEqual(len(result.stdout), 20)

    def test_shell_string_is_rejected(self):
        with self.assertRaises(ValueError):
            BoundedProcessRunner().run("echo unsafe")

    def test_timeout_returns_a_redacted_bounded_result(self):
        secret = "timeout-secret-sentinel"
        runner = BoundedProcessRunner(secret_values=(secret,))
        result = runner.run(
            [sys.executable, "-c", f"import time; print('{secret}'); time.sleep(1)"],
            timeout=0.01,
        )
        self.assertEqual(result.returncode, 124)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("timed out", result.stderr)

    def test_constructor_rejects_invalid_bounds_and_secret_types(self):
        with self.assertRaises(ValueError):
            BoundedProcessRunner(max_output=-1)
        with self.assertRaises(ValueError):
            BoundedProcessRunner(max_output=True)
        with self.assertRaises(ValueError):
            BoundedProcessRunner(secret_values=(123,))

    def test_run_rejects_unsafe_arguments_environment_and_timeout(self):
        runner = BoundedProcessRunner()
        invalid_calls = (
            (("echo", "bad\x00arg"), {}, None),
            (("echo",), {"BAD\x00KEY": "value"}, None),
            (("echo",), {"BAD": "bad\x00value"}, None),
            (("echo",), {}, float("nan")),
            (("echo",), {}, -1),
            (("echo",), {}, True),
        )
        for argv, env, timeout in invalid_calls:
            with self.subTest(argv=argv, env=env, timeout=timeout), self.assertRaises(ValueError):
                runner.run(argv, env=env, timeout=timeout)


if __name__ == "__main__":
    unittest.main()
