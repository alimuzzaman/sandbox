"""Focused unit coverage for Spec 008 snapshot/reset convergence fixes."""
from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import redirect_stdout


class TestSnapshotCapture(unittest.TestCase):
    def test_explicit_stdout_sink_bypasses_web_streaming(self):
        from sandbox.core import _ui

        sink = io.BytesIO()
        with mock.patch.object(_ui.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0,
                                                            stdout="",
                                                            stderr="")) as run, \
                mock.patch.object(_ui, "_WEB_STREAM", [True]):
            _ui.run(["docker", "compose", "db"], stdout=sink)

        self.assertIs(run.call_args.kwargs["stdout"], sink)
        self.assertNotIn("capture_output", run.call_args.kwargs)

    def test_stdin_only_keeps_web_streaming(self):
        from sandbox.core import _ui

        source = io.StringIO("input")
        proc = SimpleNamespace(stdout=iter(["output\n"]), returncode=0,
                               wait=mock.Mock())
        with mock.patch.object(_ui.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.object(_ui.subprocess, "run") as run, \
                mock.patch.object(_ui, "_WEB_STREAM", [True]):
            result = _ui.run(["docker", "compose", "db"], stdin=source, timeout=10)

        self.assertEqual(result.stdout, "output\n")
        self.assertIs(popen.call_args.kwargs["stdin"], source)
        self.assertIs(popen.call_args.kwargs["stdout"], _ui.subprocess.PIPE)
        self.assertIs(popen.call_args.kwargs["stderr"], _ui.subprocess.STDOUT)
        self.assertNotIn("timeout", popen.call_args.kwargs)
        run.assert_not_called()

    def test_compose_forwards_explicit_stream_sinks(self):
        from sandbox.core import _docker

        source = io.BytesIO(b"dump")
        sink = io.BytesIO()
        with mock.patch.object(_docker, "run") as run:
            _docker.compose("run", "--rm", instance="inst",
                            stdin=source, stdout=sink)

        self.assertIs(run.call_args.kwargs["stdin"], source)
        self.assertIs(run.call_args.kwargs["stdout"], sink)

    def test_db_only_overwrite_removes_stale_uploads_archive(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "fixture"
            target.mkdir()
            (target / "db.sql").write_text("old db")
            (target / "uploads.tgz").write_text("stale uploads")
            observed = {}

            def export_db(*args, **_kwargs):
                sink = _kwargs["stdout"]
                observed["sink_mode"] = stat.S_IMODE(os.fstat(sink.fileno()).st_mode)
                sink.write(b"new db")

            with mock.patch.object(data, "compose", side_effect=export_db) as compose, \
                    mock.patch.object(data, "_active_project_name", return_value="project"), \
                    mock.patch.object(data, "run") as archive:
                data._capture_snapshot("inst", root, "fixture", db_only=True)

            self.assertEqual((target / "db.sql").read_text(), "new db")
            self.assertFalse((target / "uploads.tgz").exists())
            self.assertIn("mode=db-only", (target / "META").read_text())
            archive.assert_not_called()
            export_args = compose.call_args.args
            self.assertEqual(export_args[:2], ("run", "--rm"))
            self.assertNotIn("--user", export_args,
                             "export must retain the generated wpcli service UID")
            self.assertNotIn("-v", export_args)
            self.assertNotIn("/snapshots", " ".join(map(str, export_args)))
            self.assertEqual(export_args[-5:],
                             ("db", "export", "-", "--quiet", "--add-drop-table"))
            self.assertEqual(observed["sink_mode"], 0o600)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target / "db.sql").stat().st_mode), 0o600)

    def test_cmd_snapshot_db_only_force_replaces_full_snapshot(self):
        """The public CLI path must pass both overwrite flags to the shared capture."""
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "fixture"
            target.mkdir()
            (target / "db.sql").write_text("old db")
            (target / "uploads.tgz").write_text("stale uploads")

            def export_db(*_args, **kwargs):
                kwargs["stdout"].write(b"new db")

            args = SimpleNamespace(
                resolved_instance="inst", name="fixture", force=True, db_only=True,
            )
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "compose", side_effect=export_db), \
                    mock.patch.object(data, "_active_project_name", return_value="project"), \
                    mock.patch.object(data, "run") as archive:
                data.cmd_snapshot({}, args)

            self.assertEqual((target / "db.sql").read_text(), "new db")
            self.assertFalse((target / "uploads.tgz").exists())
            self.assertIn("mode=db-only", (target / "META").read_text())
            archive.assert_not_called()

    def test_large_export_streams_to_sink_without_capture_buffer(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"x" * (2 * 1024 * 1024)

            def export_db(*args, **kwargs):
                sink = kwargs["stdout"]
                for offset in range(0, len(payload), 64 * 1024):
                    sink.write(payload[offset:offset + 64 * 1024])

            with mock.patch.object(data, "compose", side_effect=export_db) as compose, \
                    mock.patch.object(data, "_active_project_name", return_value="project"):
                data._capture_snapshot("inst", root, "large", db_only=True)

            self.assertEqual((root / "large" / "db.sql").stat().st_size, len(payload))
            call = compose.call_args
            self.assertIn("stdout", call.kwargs)
            self.assertFalse(call.kwargs.get("capture", False))

    def test_empty_export_is_rejected_and_cleaned(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(data, "compose"):
                with self.assertRaisesRegex(RuntimeError, "no db.sql"):
                    data._capture_snapshot("inst", root, "empty", db_only=True)

            self.assertEqual(list(root.iterdir()), [],
                             "empty export must not leave a usable-looking snapshot")

    def test_snapshot_dump_mode_is_0600_even_with_restrictive_umask(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "db.sql"
            previous = os.umask(0o777)
            try:
                with data._open_snapshot_dump(path) as dump:
                    dump.write(b"dump")
            finally:
                os.umask(previous)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_capture_and_restore_stream_the_same_dump_bytes(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observed = {}
            opened = []

            def compose_stream(*args, **kwargs):
                if "export" in args:
                    kwargs["stdout"].write(b"captured dump")
                elif "import" in args:
                    self.assertTrue(opened and not opened[0].closed,
                                    "restore must retain the host FD through import")
                    observed["imported"] = kwargs["stdin"].read()
                elif "reset" in args:
                    self.assertTrue(opened and not opened[0].closed,
                                    "restore must open db.sql before reset")

            with mock.patch.object(data, "compose", side_effect=compose_stream), \
                    mock.patch.object(data, "_active_project_name", return_value="project"):
                data._capture_snapshot("inst", root, "fixture", db_only=True)
                sql = root / "fixture" / "db.sql"
                path_type = type(sql)
                real_open = path_type.open

                def track_open(*args, **kwargs):
                    file_obj = real_open(sql, *args, **kwargs)
                    opened.append(file_obj)
                    return file_obj

                with mock.patch.object(path_type, "open", side_effect=track_open):
                    data._restore_snapshot("inst", root, "fixture")

            self.assertEqual(observed["imported"], b"captured dump")

    def test_system_exit_from_export_removes_staging_directory(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observed = {}

            def fail_export(*args, **_kwargs):
                sink = _kwargs["stdout"]
                observed["sink_mode"] = stat.S_IMODE(os.fstat(sink.fileno()).st_mode)
                sink.write(b"partial")
                raise SystemExit(13)

            real_rmtree = data.shutil.rmtree

            def observe_cleanup(path, *args, **kwargs):
                observed["cleanup_mode"] = stat.S_IMODE(Path(path).stat().st_mode)
                observed["cleanup_file_mode"] = stat.S_IMODE(
                    (Path(path) / "db.sql").stat().st_mode
                )
                return real_rmtree(path, *args, **kwargs)

            with mock.patch.object(data, "compose", side_effect=fail_export), \
                    mock.patch.object(data.shutil, "rmtree", side_effect=observe_cleanup):
                with self.assertRaises(SystemExit):
                    data._capture_snapshot("inst", root, "fixture", db_only=True)

            self.assertEqual(observed["sink_mode"], 0o600)
            self.assertEqual(observed["cleanup_mode"], 0o700)
            self.assertEqual(observed["cleanup_file_mode"], 0o600)
            self.assertEqual(list(root.iterdir()), [],
                             "failed export must not leave a .tmp snapshot behind")

    def test_install_snapshot_failures_log_and_do_not_abort_provisioning(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "_capture_snapshot",
                                      side_effect=SystemExit(13)) as capture, \
                    mock.patch.object(data, "info") as info:
                data.capture_install_snapshots("inst")

            self.assertEqual(capture.call_count, 2,
                             "baseline and full restore points are independent")
            self.assertEqual(info.call_count, 2)
            self.assertIn("@install baseline capture failed", info.call_args_list[0].args[0])
            self.assertIn("full install snapshot", info.call_args_list[1].args[0])
            self.assertEqual(list(root.iterdir()), [])

    def test_list_shows_protected_baseline_separately(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / data._BASELINE_DIR
            baseline.mkdir()
            (baseline / "db.sql").write_text("baseline")
            (baseline / "META").write_text("mode=db-only\n")
            regular = root / "named"
            regular.mkdir()
            (regular / "db.sql").write_text("named")
            (regular / "META").write_text("mode=full\n")
            output = io.StringIO()
            with mock.patch.object(data, "snapshots_dir", return_value=root), redirect_stdout(output):
                data.cmd_snapshots({}, SimpleNamespace(resolved_instance="inst"))

            rendered = output.getvalue()
            self.assertIn("@install (baseline)", rendered)
            self.assertIn("[protected; reset target]", rendered)
            self.assertIn("named", rendered)


class TestRestoreConfirmation(unittest.TestCase):
    @staticmethod
    def _args(**kwargs):
        return SimpleNamespace(resolved_instance="inst", name="fixture", **kwargs)

    @staticmethod
    def _snapshot(root):
        target = root / "fixture"
        target.mkdir()
        (target / "db.sql").write_text("fixture db")

    def _assert_invalid_snapshot_does_not_reset(self, configure, *, patch_open=None):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "fixture"
            target.mkdir()
            configure(target)
            with mock.patch.object(data, "compose") as compose:
                if patch_open is None:
                    with self.assertRaises(SystemExit):
                        data._restore_snapshot("inst", root, "fixture")
                else:
                    path_type = type(target / "db.sql")
                    with mock.patch.object(path_type, "open", side_effect=patch_open):
                        with self.assertRaises(SystemExit):
                            data._restore_snapshot("inst", root, "fixture")
            compose.assert_not_called()

    def test_restore_rejects_empty_db_before_reset(self):
        self._assert_invalid_snapshot_does_not_reset(
            lambda target: (target / "db.sql").write_bytes(b""))

    def test_restore_rejects_directory_db_before_reset(self):
        self._assert_invalid_snapshot_does_not_reset(
            lambda target: (target / "db.sql").mkdir())

    def test_restore_rejects_symlink_db_before_reset(self):
        def configure(target):
            (target / "real.sql").write_text("fixture db")
            (target / "db.sql").symlink_to("real.sql")

        self._assert_invalid_snapshot_does_not_reset(configure)

    def test_restore_rejects_unreadable_db_before_reset(self):
        self._assert_invalid_snapshot_does_not_reset(
            lambda target: (target / "db.sql").write_text("fixture db"),
            patch_open=PermissionError("permission denied"))

    def test_noninteractive_restore_requires_confirmation_before_db_reset(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._snapshot(root)
            args = self._args()
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "compose") as compose, \
                    mock.patch.object(data, "run") as run, \
                    mock.patch.object(data, "die", side_effect=RuntimeError(
                        "restore requires --yes when stdin is not interactive")), \
                    mock.patch.object(data.sys.stdin, "isatty", return_value=False), \
                    mock.patch("builtins.input") as prompt:
                with self.assertRaisesRegex(RuntimeError, "restore requires --yes"):
                    data.cmd_restore({}, args)

            prompt.assert_not_called()
            compose.assert_not_called()
            run.assert_not_called()
            self.assertEqual((root / "fixture" / "db.sql").read_text(), "fixture db")

    def test_interactive_restore_cancel_preserves_db_and_uploads(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._snapshot(root)
            args = self._args()
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "compose") as compose, \
                    mock.patch.object(data, "run") as run, \
                    mock.patch.object(data.sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", return_value="n"):
                data.cmd_restore({}, args)

            compose.assert_not_called()
            run.assert_not_called()
            self.assertEqual((root / "fixture" / "db.sql").read_text(), "fixture db")

    def test_interactive_restore_confirmation_accepts_and_dispatches(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._snapshot(root)
            args = self._args()
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "compose") as compose, \
                    mock.patch.object(data, "run") as run, \
                    mock.patch.object(data.sys.stdin, "isatty", return_value=True), \
                    mock.patch("builtins.input", return_value="yes") as prompt:
                data.cmd_restore({}, args)

            prompt.assert_called_once()
            compose.assert_any_call("run", "--rm", "wpcli", "db", "reset", "--yes",
                                    instance="inst")
            imports = [call for call in compose.call_args_list
                       if call.args[:4] == ("run", "--rm", "-T", "wpcli")]
            self.assertEqual(len(imports), 1)
            self.assertEqual(imports[0].args[4:], ("db", "import", "-"))
            self.assertNotIn("-v", imports[0].args)
            self.assertNotIn("/snapshots", " ".join(map(str, imports[0].args)))
            self.assertEqual(imports[0].kwargs["stdin"].name,
                             str(root / "fixture" / "db.sql"))
            run.assert_not_called()

    def test_restore_yes_bypasses_prompt_and_dispatches_reset_then_import(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._snapshot(root)
            args = self._args(yes=True)
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "compose") as compose, \
                    mock.patch.object(data, "run") as run, \
                    mock.patch.object(data.sys.stdin, "isatty", return_value=False), \
                    mock.patch("builtins.input") as prompt:
                data.cmd_restore({}, args)

            prompt.assert_not_called()
            compose.assert_any_call("run", "--rm", "wpcli", "db", "reset", "--yes",
                                    instance="inst")
            imports = [call for call in compose.call_args_list
                       if call.args[:4] == ("run", "--rm", "-T", "wpcli")]
            self.assertEqual(len(imports), 1)
            self.assertEqual(imports[0].args[4:], ("db", "import", "-"))
            self.assertNotIn("-v", imports[0].args)
            self.assertNotIn("/snapshots", " ".join(map(str, imports[0].args)))
            self.assertEqual(imports[0].kwargs["stdin"].name,
                             str(root / "fixture" / "db.sql"))
            run.assert_not_called()

    def test_restore_accepts_existing_confirm_attribute_as_alias(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._snapshot(root)
            args = self._args(confirm=True)
            with mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "snapshots_dir", return_value=root), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "compose") as compose, \
                    mock.patch.object(data, "run"), \
                    mock.patch.object(data.sys.stdin, "isatty", return_value=False), \
                    mock.patch("builtins.input") as prompt:
                data.cmd_restore({}, args)

            prompt.assert_not_called()
            self.assertTrue(compose.called)


class TestBaselineProtection(unittest.TestCase):
    def test_cli_snapshot_and_restore_reject_reserved_baseline_labels(self):
        from sandbox.commands import data

        for operation, name in ((data.cmd_snapshot, "@install"),
                                (data.cmd_snapshot, "__install__"),
                                (data.cmd_restore, "@install"),
                                (data.cmd_restore, "__install__")):
            with self.subTest(operation=operation.__name__, name=name), \
                    mock.patch.object(data, "preflight_instance_capability", return_value=None), \
                    mock.patch.object(data, "_is_herd_instance", return_value=False), \
                    mock.patch.object(data, "_capture_snapshot") as capture, \
                    mock.patch.object(data, "_restore_snapshot") as restore, \
                    mock.patch.object(data, "die", side_effect=RuntimeError) as die:
                args = SimpleNamespace(resolved_instance="inst", name=name,
                                       force=True, db_only=True, yes=True,
                                       confirm=True)
                with self.assertRaises(RuntimeError):
                    operation({}, args)
                capture.assert_not_called()
                restore.assert_not_called()
                self.assertIn("baseline", str(die.call_args).lower() +
                              str(die.call_args_list).lower())


class TestDashboardResetDispatch(unittest.TestCase):
    def test_wp_admin_template_routes_reset_with_explicit_confirmation(self):
        from sandbox.core import _paths

        template = _paths._SNAPSHOT_MU_TEMPLATE
        self.assertIn("} elseif ( 'reset' === $op ) {", template)
        self.assertIn("'1' === sanitize_text_field", template)
        self.assertIn("'POST', '/reset', array( 'confirm' => $confirm )", template)
        self.assertIn("call('reset',{confirm:1})", template)

    def test_wp_admin_template_routes_restore_with_explicit_confirmation(self):
        from sandbox.core import _paths

        template = _paths._SNAPSHOT_MU_TEMPLATE
        self.assertIn("} elseif ( 'restore' === $op ) {", template)
        self.assertIn("'POST', '/restore', array( 'name' => $name, 'confirm' => $confirm )", template)
        self.assertIn("call('restore',{name:r,confirm:1})", template)

    def test_reset_dispatches_confirmed_reset_command(self):
        import sandbox.core._dash as dashboard

        called = []

        def inline_job(_label, operation):
            operation()
            return "job-1"

        with mock.patch.object(dashboard, "_start_job", side_effect=inline_job), \
                mock.patch.object(dashboard, "load_config", return_value={}), \
                mock.patch("sandbox.commands.data.cmd_reset",
                           side_effect=lambda cfg, args: called.append((cfg, args))):
            result = dashboard._web_do_action({
                "action": "reset", "instance": "demo", "confirm": True,
            })

        self.assertEqual(result, {"ok": True, "job_id": "job-1"})
        self.assertEqual(len(called), 1)
        self.assertTrue(called[0][1].yes)
        self.assertFalse(called[0][1].rebaseline)
        self.assertEqual(called[0][1].resolved_instance, "demo")

    def test_reset_requires_dashboard_confirmation(self):
        import sandbox.core._dash as dashboard

        outcome = []

        def inline_job(_label, operation):
            outcome.append(operation())
            return "job-1"

        with mock.patch.object(dashboard, "_start_job", side_effect=inline_job), \
                mock.patch.object(dashboard, "load_config", return_value={}), \
                mock.patch("sandbox.commands.data.cmd_reset") as reset:
            dashboard._web_do_action({"action": "reset", "instance": "demo"})

        self.assertEqual(outcome, [False])
        reset.assert_not_called()

    def test_dashboard_snapshot_rejects_reserved_baseline_labels(self):
        import sandbox.core._dash as dashboard

        with mock.patch.object(dashboard, "_start_job") as start, \
                mock.patch("sandbox.commands.data.cmd_snapshot") as snapshot:
            for name in ("@install", " __install__ ", " @INSTALL "):
                with self.subTest(name=name):
                    result = dashboard._web_do_action({
                        "action": "snapshot", "instance": "demo", "name": name,
                    })
                    self.assertEqual(result["ok"], False)
                    self.assertNotIn("job_id", result)
                    self.assertIn("protected", result["output"])

        start.assert_not_called()
        snapshot.assert_not_called()

    def test_dashboard_restore_rejects_reserved_baseline_before_job(self):
        import sandbox.core._dash as dashboard

        with mock.patch.object(dashboard, "_start_job") as start, \
                mock.patch("sandbox.commands.data.cmd_restore") as restore:
            for name in ("@install", " __install__ ", " @INSTALL "):
                with self.subTest(name=name):
                    result = dashboard._web_do_action({
                        "action": "restore", "instance": "demo",
                        "name": name, "confirm": True,
                    })
                    self.assertEqual(result["ok"], False)
                    self.assertNotIn("job_id", result)
                    self.assertIn("protected", result["output"])

        start.assert_not_called()
        restore.assert_not_called()
