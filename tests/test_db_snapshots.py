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
