import contextlib
import errno
import io
import os
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sandbox.commands import wp
from sandbox.core import _docker, _ui
from sandbox.wp_argv import is_eval_file_stdin, requires_stdin
from tests.subprocess_support import synthetic_environment


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


class TestWpStdinRequirement(unittest.TestCase):
    def test_every_refused_form(self):
        refused = [
            ["eval-file", "-"],
            ["--path=/x", "eval-file", "-"],
            ["--url", "x", "db", "query"],
            ["db", "query"],
            ["db", "import", "-"],
            ["post", "create", "-"],
            ["post", "update", "1", "-"],
            ["option", "add", "key"],
            ["option", "update", "key"],
            ["post", "meta", "add", "1", "key"],
            ["post", "meta", "update", "1", "key"],
            ["user", "meta", "add", "1", "key"],
            ["user", "meta", "update", "1", "key"],
            ["term", "meta", "add", "1", "key"],
            ["term", "meta", "update", "1", "key"],
            ["comment", "meta", "add", "1", "key"],
            ["comment", "meta", "update", "1", "key"],
        ]
        for cmd in refused:
            with self.subTest(cmd=cmd):
                self.assertTrue(requires_stdin(cmd), f"{cmd} should require stdin")

    def test_every_passing_form(self):
        passing = [
            ["option", "update", "sep", "-"],
            ["search-replace", "old", "-"],
            ["eval-file", "script.php", "-"],
            ["db", "query", "SELECT 1"],
            ["option", "update", "k", "v"],
            ["post", "meta", "update", "1", "k", "v"],
            ["--format=-"],
            ["post", "create", "file.txt"],
            ["post", "update", "1", "file.txt"],
            ["db", "import", "dump.sql"],
            ["option", "add", "k", "v"],
            ["user", "meta", "update", "1", "k", "v"],
        ]
        for cmd in passing:
            with self.subTest(cmd=cmd):
                self.assertFalse(requires_stdin(cmd), f"{cmd} should not require stdin")


class TestWpCliProbeAndExecStdin(unittest.TestCase):
    def test_probe_gets_stdin_devnull_and_real_exec_does_not(self):
        recorded_calls = []

        def fake_compose(*args, **kwargs):
            recorded_calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        _docker._WP_CLI_BUILTIN.pop("test-inst", None)
        with patch.object(_docker, "compose", side_effect=fake_compose), \
             patch.object(_docker, "_managed_execution_gate", return_value=None), \
             patch.object(_docker, "_is_herd_instance", return_value=False):
            _docker._wpcli_unleased(["option", "get", "siteurl"], instance="test-inst")

        self.assertEqual(len(recorded_calls), 2)
        probe_args, probe_kwargs = recorded_calls[0]
        exec_args, exec_kwargs = recorded_calls[1]

        # Verify probe received stdin=subprocess.DEVNULL
        self.assertEqual(probe_args, ("exec", "-T", "wp", "test", "-f", "/usr/local/bin/wp"))
        self.assertEqual(probe_kwargs.get("stdin"), subprocess.DEVNULL)

        # Verify real exec did NOT pass stdin (stays inheriting)
        self.assertEqual(
            exec_args,
            ("exec", "-e", "PAGER=cat", "-u", "www-data", "-T", "wp", "wp", "option", "get", "siteurl"),
        )
        self.assertIsNone(exec_kwargs.get("stdin"))


class TestStdinOperandCount(unittest.TestCase):
    def test_a_negative_value_is_an_operand(self):
        from sandbox.wp_argv import requires_stdin
        for argv in (["option", "update", "offset", "-1"],
                     ["post", "meta", "update", "1", "key", "-1"],
                     ["option", "update", "sep", "-"]):
            with self.subTest(argv=argv):
                self.assertFalse(requires_stdin(argv))
        for argv in (["option", "update", "offset"],
                     ["option", "update", "offset", "--format=json"],
                     ["post", "meta", "update", "1", "key"]):
            with self.subTest(argv=argv):
                self.assertTrue(requires_stdin(argv))


class TestUiRunCaptureStdin(unittest.TestCase):
    def test_run_with_capture_and_stdin_no_longer_raises(self):
        res = _ui.run(["true"], check=False, capture=True, stdin=subprocess.DEVNULL,
                      env=synthetic_environment())
        self.assertEqual(res.returncode, 0)

    def test_run_with_capture_and_stdout_still_raises(self):
        with self.assertRaisesRegex(ValueError, "capture cannot be combined with explicit stdout"):
            _ui.run(["true"], check=False, capture=True, stdout=subprocess.PIPE)


class TestWpEvalFileFd0(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "resolved_instance": "demo",
            "passthrough": ["--", "eval-file", "-"],
            "run_async": False,
            "timeout": 60,
            "project_dir": None,
            "remote": None,
            "local": True,
            "allow_missing": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    def test_eval_file_refuses_when_fd0_is_character_device(self, _preflight):
        mock_stat = MagicMock(st_mode=stat.S_IFCHR | 0o666)
        err = io.StringIO()
        with patch("os.fstat", return_value=mock_stat), \
             patch.object(wp, "wpcli") as wpcli, \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as raised:
            wp.cmd_wp({}, self._args())

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("eval-file - requires a piped script", err.getvalue())
        self.assertIn("character device or closed", err.getvalue())
        wpcli.assert_not_called()

    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    def test_eval_file_refuses_when_fd0_is_closed(self, _preflight):
        err = io.StringIO()
        with patch("os.fstat", side_effect=OSError(errno.EBADF, "Bad file descriptor")), \
             patch.object(wp, "wpcli") as wpcli, \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as raised:
            wp.cmd_wp({}, self._args())

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("eval-file - requires a piped script", err.getvalue())
        self.assertIn("character device or closed", err.getvalue())
        wpcli.assert_not_called()

    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    @patch("sandbox.commands.wp._stage_host_package_paths", return_value=(["eval-file", "-"], []))
    def test_eval_file_passes_fd0_check_when_piped(self, _stage, _preflight):
        mock_stat = MagicMock(st_mode=stat.S_IFIFO | 0o666)
        out = io.StringIO()
        with patch("os.fstat", return_value=mock_stat), \
             patch.object(wp, "wpcli", return_value=SimpleNamespace(returncode=0, stdout="42\n", stderr="")) as wpcli, \
             contextlib.redirect_stdout(out):
            wp.cmd_wp({}, self._args())

        wpcli.assert_called_once()
        self.assertEqual(out.getvalue(), "42\n")


class TestWpAsyncAndManagedNativeRefusal(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "resolved_instance": "demo",
            "passthrough": ["--", "db", "query"],
            "run_async": True,
            "timeout": 60,
            "project_dir": None,
            "remote": None,
            "local": True,
            "allow_missing": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    def test_async_refuses_command_requiring_stdin_before_launch(self, _preflight):
        for cmd in (["--", "db", "query"], ["--", "eval-file", "-"]):
            for char_dev in (True, False):
                with self.subTest(cmd=cmd, char_dev=char_dev):
                    err = io.StringIO()
                    with patch("sandbox.commands.wp._stdin_is_char_device_or_closed", return_value=char_dev),                          patch("sandbox.commands.jobs.launch_job") as launch_mock,                          contextlib.redirect_stderr(err),                          self.assertRaises(SystemExit) as raised:
                        wp.cmd_wp({}, self._args(passthrough=cmd))

                    self.assertEqual(raised.exception.code, 1)
                    self.assertIn("async wp does not support stdin", err.getvalue())
                    launch_mock.assert_not_called()

    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    @patch("sandbox.commands.wp.managed_native_instance_selected", return_value=("/project", "default"))
    def test_managed_native_refuses_command_requiring_stdin_before_wpcli(self, _mn, _preflight):
        for char_dev in (True, False):
            with self.subTest(char_dev=char_dev):
                err = io.StringIO()
                args = self._args(run_async=False, passthrough=["--", "eval-file", "-"])
                with patch("sandbox.commands.wp._stdin_is_char_device_or_closed", return_value=char_dev),                      patch.object(wp, "wpcli") as wpcli,                      contextlib.redirect_stderr(err),                      self.assertRaises(SystemExit) as raised:
                    wp.cmd_wp({}, args)

                self.assertEqual(raised.exception.code, 1)
                self.assertIn("managed-native wp does not support stdin", err.getvalue())
                wpcli.assert_not_called()

if __name__ == "__main__":
    unittest.main()
