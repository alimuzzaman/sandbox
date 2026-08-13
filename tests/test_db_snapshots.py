"""Focused unit coverage for Spec 008 snapshot/reset convergence fixes."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import redirect_stdout


class TestSnapshotCapture(unittest.TestCase):
    def test_db_only_overwrite_removes_stale_uploads_archive(self):
        from sandbox.commands import data

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "fixture"
            target.mkdir()
            (target / "db.sql").write_text("old db")
            (target / "uploads.tgz").write_text("stale uploads")

            def export_db(*args, **_kwargs):
                destination = next(value for value in args if str(value).startswith("/snapshots/"))
                (root / str(destination).removeprefix("/snapshots/")).write_text("new db")

            with mock.patch.object(data, "compose", side_effect=export_db), \
                    mock.patch.object(data, "_active_project_name", return_value="project"), \
                    mock.patch.object(data, "run") as archive:
                data._capture_snapshot("inst", root, "fixture", db_only=True)

            self.assertEqual((target / "db.sql").read_text(), "new db")
            self.assertFalse((target / "uploads.tgz").exists())
            self.assertIn("mode=db-only", (target / "META").read_text())
            archive.assert_not_called()

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
            compose.assert_any_call("run", "--rm", "-v", f"{root}:/snapshots",
                                    "wpcli", "db", "import", "/snapshots/fixture/db.sql",
                                    instance="inst")
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
            compose.assert_any_call("run", "--rm", "-v", f"{root}:/snapshots",
                                    "wpcli", "db", "import", "/snapshots/fixture/db.sql",
                                    instance="inst")
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


class TestDashboardResetDispatch(unittest.TestCase):
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
