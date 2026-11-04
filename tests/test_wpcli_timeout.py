"""Timeout and routing contracts for the Docker WP-CLI helper."""
from __future__ import annotations

import unittest
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import re
from types import SimpleNamespace
import subprocess
import tempfile
from pathlib import Path

from sandbox.core import _docker


class _Result:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class TestWpCliTimeout(unittest.TestCase):
    def setUp(self):
        self.previous = dict(_docker._WP_CLI_BUILTIN)
        _docker._WP_CLI_BUILTIN.clear()
        # ``wpcli`` checks the managed-native gateway before selecting the
        # Compose route.  This test is about timeout/routing mechanics, so keep
        # that unrelated composition boundary inert under the unittest argv.
        self.gate = mock.patch.object(_docker, "_managed_execution_gate", return_value=None)
        self.gate.start()
        # Docker routing tests do not exercise Herd/config discovery.  The
        # latter may bootstrap the CLI venv (and re-exec) under a bare system
        # Python, turning unittest's module selector into Sandbox CLI argv.
        self.herd = mock.patch.object(_docker, "_is_herd_instance", return_value=False)
        self.herd.start()

    def tearDown(self):
        self.herd.stop()
        self.gate.stop()
        _docker._WP_CLI_BUILTIN.clear()
        _docker._WP_CLI_BUILTIN.update(self.previous)

    def test_builtin_preflight_forwards_timeout(self):
        with mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            self.assertTrue(_docker._wp_has_builtin_cli("fixture", timeout=2.5))
        self.assertEqual(compose.call_args.kwargs["timeout"], 2.5)

    def test_wpcli_forwards_timeout_to_builtin_preflight_and_exec(self):
        with mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            _docker.wpcli(["core", "is-installed"], instance="fixture",
                          check=False, capture=True, timeout=7)
        self.assertEqual(compose.call_count, 2)
        self.assertEqual(compose.call_args_list[0].kwargs["timeout"], 7)
        self.assertEqual(compose.call_args_list[1].kwargs["timeout"], 7)

    def test_wpcli_forwards_timeout_to_managed_execution_gate(self):
        gate_result = _Result()
        with mock.patch.object(_docker, "_managed_execution_gate",
                               return_value=gate_result) as gate:
            result = _docker.wpcli(["core", "is-installed"], instance="fixture",
                                   check=False, capture=True, timeout=17)
        self.assertIs(result, gate_result)
        self.assertEqual(gate.call_args.kwargs["timeout"], 17)

    def test_db_commands_skip_builtin_preflight(self):
        with mock.patch.object(_docker, "_wp_has_builtin_cli") as preflight, \
                mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            _docker.wpcli(["db", "query", "SELECT 1", "--skip-column-names"],
                          instance="fixture", check=False, capture=True, timeout=11)
        preflight.assert_not_called()
        compose.assert_called_once()
        self.assertEqual(
            compose.call_args.args[:5],
            ("run", "--rm", "-e", "PAGER=cat", "wpcli"),
        )
        self.assertEqual(compose.call_args.kwargs["timeout"], 11)

    def test_cmd_wp_uses_default_timeout_for_direct_namespace(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False,
        )
        result = SimpleNamespace(returncode=0, stdout="https://example.test\n", stderr="")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        self.assertEqual(wpcli.call_args.kwargs["timeout"], 60)

    def test_cmd_wp_stages_absolute_host_plugin_zip_for_docker_wpcli(self):
        import sandbox.commands.wp as command
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "xspeed.zip"
            package.write_bytes(b"zip-fixture")
            runtime = Path(directory) / "runtime"
            args = SimpleNamespace(
                resolved_instance="fixture",
                passthrough=["plugin", "install", str(package), "--activate"],
                run_async=False,
            )
            result = SimpleNamespace(returncode=0, stdout="installed\n", stderr="")
            with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(command, "_is_herd_instance", return_value=False), \
                    mock.patch.object(command, "RUNTIME_DIR", runtime), \
                    mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                command.cmd_wp({}, args)
        forwarded = wpcli.call_args.args[0]
        self.assertEqual(forwarded[:2], ["plugin", "install"])
        self.assertTrue(forwarded[2].startswith("/sandbox-dl-cache/.sandbox-host-package-"))
        self.assertEqual(forwarded[3], "--activate")
        self.assertFalse(any(runtime.rglob("*")))

    def test_staged_file_keeps_its_extension(self):
        import sandbox.commands.wp as command
        for filename, suffix, content in (
            ("foo.zip", ".zip", b"zip-content"),
            ("a.JPG", ".JPG", b"jpg-content"),
            ("script.php", ".php", b"php-content"),
        ):
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / filename
                    source.write_bytes(content)
                    runtime = Path(directory) / "runtime"
                    args = SimpleNamespace(
                        resolved_instance="fixture",
                        passthrough=["plugin", "install", str(source)],
                        run_async=False,
                    )
                    captured = {}
                    def fake_wpcli(argv, **kwargs):
                        captured["argv"] = argv
                        staged_files = list((runtime / "dl-cache" / "wp-http").glob(".sandbox-host-package-*"))
                        captured["staged_count"] = len(staged_files)
                        if staged_files:
                            captured["staged_bytes"] = staged_files[0].read_bytes()
                            captured["staged_name"] = staged_files[0].name
                        return SimpleNamespace(returncode=0, stdout="", stderr="")

                    with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                            mock.patch.object(command, "_is_herd_instance", return_value=False), \
                            mock.patch.object(command, "RUNTIME_DIR", runtime), \
                            mock.patch.object(command, "wpcli", side_effect=fake_wpcli), \
                            redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        command.cmd_wp({}, args)

                    forwarded = captured["argv"]
                    self.assertTrue(forwarded[2].endswith(suffix))
                    self.assertEqual(captured["staged_count"], 1)
                    self.assertTrue(captured["staged_name"].endswith(suffix))
                    self.assertEqual(captured["staged_bytes"], content)

    def test_staged_file_drops_an_unsafe_extension(self):
        import sandbox.commands.wp as command
        cases = (
            ("foo.z p", "foo.z p"),
            ("foo.$(x)", "foo.$(x)"),
            ("long_ext", "foo." + "a" * 17),
            ("noext", "noext"),
        )
        for label, filename in cases:
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / filename
                    source.write_bytes(b"content")
                    runtime = Path(directory) / "runtime"
                    args = SimpleNamespace(
                        resolved_instance="fixture",
                        passthrough=["plugin", "install", str(source)],
                        run_async=False,
                    )
                    result = SimpleNamespace(returncode=0, stdout="", stderr="")
                    with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                            mock.patch.object(command, "_is_herd_instance", return_value=False), \
                            mock.patch.object(command, "RUNTIME_DIR", runtime), \
                            mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                            redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                        command.cmd_wp({}, args)
                    forwarded = wpcli.call_args.args[0]
                    self.assertRegex(forwarded[2], r"^/sandbox-dl-cache/\.sandbox-host-package-\d+-\d+$")

    def test_staged_files_with_extensions_are_cleaned_up(self):
        import sandbox.commands.wp as command
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test-plugin.zip"
            source.write_bytes(b"zip-payload")
            runtime = Path(directory) / "runtime"
            args = SimpleNamespace(
                resolved_instance="fixture",
                passthrough=["plugin", "install", str(source)],
                run_async=False,
            )
            # Success case
            fake_result = SimpleNamespace(returncode=0, stdout="", stderr="")
            with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(command, "_is_herd_instance", return_value=False), \
                    mock.patch.object(command, "RUNTIME_DIR", runtime), \
                    mock.patch.object(command, "wpcli", return_value=fake_result), \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                command.cmd_wp({}, args)
            leftover = list((runtime / "dl-cache" / "wp-http").glob(".sandbox-host-pack*"))
            self.assertEqual(leftover, [])

            # Command failure case
            with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(command, "_is_herd_instance", return_value=False), \
                    mock.patch.object(command, "RUNTIME_DIR", runtime), \
                    mock.patch.object(command, "wpcli", side_effect=RuntimeError("wpcli crash")), \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                    self.assertRaises(RuntimeError):
                command.cmd_wp({}, args)
            leftover = list((runtime / "dl-cache" / "wp-http").glob(".sandbox-host-pack*"))
            self.assertEqual(leftover, [])

            # Staging failure case
            with mock.patch.object(command.shutil, "copyfile", side_effect=OSError("disk error")), \
                    mock.patch.object(command, "RUNTIME_DIR", runtime), \
                    mock.patch.object(command, "_is_herd_instance", return_value=False), \
                    redirect_stderr(StringIO()), \
                    self.assertRaises(SystemExit):
                command._stage_host_package_paths(["plugin", "install", str(source)], "fixture", directory)
            leftover = list((runtime / "dl-cache" / "wp-http").glob(".sandbox-host-pack*"))
            self.assertEqual(leftover, [])

    def test_cmd_wp_stages_absolute_host_media_and_eval_files(self):
        import sandbox.commands.wp as command
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "fixture.png"
            media.write_bytes(b"png-fixture")
            script = Path(directory) / "prepare.php"
            script.write_text("<?php echo 'ok';", encoding="utf-8")
            runtime = Path(directory) / "runtime"
            result = SimpleNamespace(returncode=0, stdout="", stderr="")
            for passthrough, expected_position in (
                (["media", "import", str(media), "--porcelain"], 2),
                (["eval-file", str(script), "--", "fixture"], 1),
            ):
                args = SimpleNamespace(
                    resolved_instance="fixture", passthrough=passthrough,
                    run_async=False,
                )
                with self.subTest(command=passthrough[0]), \
                        mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                        mock.patch.object(command, "_is_herd_instance", return_value=False), \
                        mock.patch.object(command, "RUNTIME_DIR", runtime), \
                        mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                        redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    command.cmd_wp({}, args)
                self.assertTrue(wpcli.call_args.args[0][expected_position].startswith(
                    "/sandbox-dl-cache/.sandbox-host-package-"))
            self.assertFalse(any(path.is_file() for path in runtime.rglob("*")))

    def test_cmd_wp_stages_project_relative_eval_file(self):
        import sandbox.commands.wp as command
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "support-floor-prepare.php"
            script.write_text("<?php echo 'ok';", encoding="utf-8")
            runtime = Path(directory) / "runtime"
            args = SimpleNamespace(
                resolved_instance="fixture", passthrough=["eval-file", script.name],
                run_async=False, project_dir=directory,
            )
            result = SimpleNamespace(returncode=0, stdout="", stderr="")
            with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(command, "_is_herd_instance", return_value=False), \
                    mock.patch.object(command, "RUNTIME_DIR", runtime), \
                    mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                    redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                command.cmd_wp({}, args)
            self.assertTrue(wpcli.call_args.args[0][1].startswith(
                "/sandbox-dl-cache/.sandbox-host-package-"))

    def test_cmd_wp_preserves_skip_plugins_for_replacement(self):
        import sandbox.commands.wp as command
        args = SimpleNamespace(
            resolved_instance="fixture",
            passthrough=["--skip-plugins=broken-plugin", "plugin", "install", "fixed.zip"],
            run_async=False,
        )
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        self.assertEqual(wpcli.call_args.args[0], args.passthrough)

    def test_cmd_wp_timeout_preserves_partial_streams_and_is_uncertain(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False, timeout=3,
        )
        out, err = StringIO(), StringIO()
        expired = subprocess.TimeoutExpired(
            ["docker", "compose"], 3, output=b"partial stdout\n", stderr=b"partial stderr\n"
        )
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", side_effect=expired) as wpcli, \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.cmd_wp({}, args)
        self.assertEqual(raised.exception.code, 124)
        self.assertEqual(out.getvalue(), "partial stdout\n")
        self.assertIn("partial stderr\n", err.getvalue())
        self.assertIn("wp command timed out after 3 seconds; completion is unknown—inspect state before retrying, or use --async for long work", err.getvalue())
        wpcli.assert_called_once_with(["option", "get", "siteurl"],
                                      instance="fixture", check=False, capture=True,
                                      timeout=3)

    def test_cmd_wp_child_exit_124_is_not_treated_as_timeout(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False, timeout=3,
        )
        out, err = StringIO(), StringIO()
        result = SimpleNamespace(returncode=124, stdout="child output\n", stderr="child error\n")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result), \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.cmd_wp({}, args)
        self.assertEqual(raised.exception.code, 124)
        self.assertIn("child output\n", out.getvalue())
        self.assertIn("wp command failed with exit code 124", err.getvalue())
        self.assertNotIn("completion is unknown", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
