"""Fault-injection and recovery tests for archive review cleanup journaling."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.plugin_check.journal import (  # noqa: E402
    PLANE_ORDER,
    ArchiveCleanupError,
    ArchiveCleanupService,
    ArchiveJournalError,
    ArchivePhaseError,
    ArchiveReviewJournal,
    CleanupPlane,
    recover_archive_cleanup,
)


class TestArchiveReviewJournal(unittest.TestCase):
    def _create(self, directory: str, name: str = "journal.json") -> ArchiveReviewJournal:
        root = Path(directory) / "run"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        return ArchiveReviewJournal.create(
            root / name,
            run_id="run-001",
            target={
                "archive_sha256": "a" * 64,
                "archive_slug": "demo-plugin",
                "caller_project_root": "/tmp/caller",
                "environment": {"SECRET": "must-not-land"},
            },
        )

    def test_create_is_owner_only_and_persists_complete_journal_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._create(directory)
            self.assertEqual(stat.S_IMODE(journal.path.stat().st_mode), 0o600)
            self.assertEqual(journal.path.stat().st_uid, os.getuid())
            self.assertEqual(journal.data["phase"], "journal:complete")
            self.assertEqual(set(journal.data["planes"]), set(PLANE_ORDER))
            self.assertNotIn("SECRET", journal.path.read_text())
            reopened = ArchiveReviewJournal.open(journal.path)
            self.assertEqual(reopened.receipt_id, journal.receipt_id)
            self.assertEqual(reopened.data["target"]["archive_slug"], "demo-plugin")

    def test_transition_and_phase_failure_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._create(directory)
            journal.transition("boot:in_progress")
            self.assertEqual(ArchiveReviewJournal.open(journal.path).data["phase"], "boot:in_progress")
            with self.assertRaises(ArchivePhaseError) as raised:
                journal.execute_phase("check", lambda: (_ for _ in ()).throw(RuntimeError("private detail")))
            self.assertEqual(raised.exception.phase, "check")
            data = ArchiveReviewJournal.open(journal.path).data
            self.assertEqual(data["phase"], "check:failed")
            self.assertEqual(data["last_error"], "RuntimeError")
            self.assertNotIn("private detail", json.dumps(data))

    def test_interrupt_marks_phase_before_propagating(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._create(directory)
            with self.assertRaises(KeyboardInterrupt):
                journal.execute_phase("report", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
            self.assertEqual(ArchiveReviewJournal.open(journal.path).data["phase"], "report:failed")

    def test_untrusted_or_symlink_journal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            root.mkdir(mode=0o700)
            path = root / "journal.json"
            path.write_text("{}")
            path.chmod(0o600)
            path.unlink()
            path.symlink_to(root / "missing")
            with self.assertRaises(ArchiveJournalError) as raised:
                ArchiveReviewJournal.open(path)
            self.assertEqual(raised.exception.code, "archive_journal_invalid")

    def test_creation_failure_is_typed_and_does_not_fall_back_to_global_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            root.mkdir(mode=0o700)
            with patch("sandbox.plugin_check.journal.os.open", side_effect=OSError("blocked")):
                with self.assertRaises(ArchiveJournalError) as raised:
                    ArchiveReviewJournal.create(
                        root / "journal.json",
                        run_id="run-002",
                        target={"archive_sha256": "b" * 64},
                    )
            self.assertEqual(raised.exception.code, "archive_journal_write_failed")
            self.assertFalse((root / "journal.json").exists())


class TestArchiveCleanupService(unittest.TestCase):
    def _journal(self, directory: str) -> ArchiveReviewJournal:
        root = Path(directory) / "run"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        return ArchiveReviewJournal.create(
            root / "journal.json",
            run_id="run-001",
            target={"archive_sha256": "a" * 64},
        )

    def _planes(self, state: dict[str, bool], *, fail: str | None = None, report: bool = True):
        calls: dict[str, int] = {name: 0 for name in PLANE_ORDER}
        planes = []
        for name in PLANE_ORDER:
            desired_present = name == "report"
            state.setdefault(name, True)
            if name == fail and name == "report":
                state[name] = False

            def cleanup(name=name):
                calls[name] += 1
                if name == fail:
                    raise RuntimeError(f"injected-{name}")
                if name != "report":
                    state[name] = False

            def verify(name=name):
                return state[name] is desired_present if name == "report" else state[name] is False

            planes.append(CleanupPlane(name, cleanup, verify))
        return planes, calls

    def test_all_planes_are_verified_and_complete_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(directory)
            state = {}
            planes, calls = self._planes(state)
            service = ArchiveCleanupService(journal, planes)
            first = service.cleanup()
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["planes"], {
                **{name: "absent" for name in PLANE_ORDER if name != "report"},
                "report": "complete",
            })
            before = dict(calls)
            second = service.cleanup()
            self.assertEqual(second["status"], "complete")
            self.assertEqual(calls, before)
            self.assertFalse(journal.data["recovery_required"])

    def test_one_failed_cleanup_plane_is_unknown_and_retry_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(directory)
            state = {}
            planes, _calls = self._planes(state, fail="network")
            first = ArchiveCleanupService(journal, planes).cleanup()
            self.assertEqual(first["status"], "unknown")
            self.assertEqual(first["planes"]["network"], "unknown")
            self.assertTrue(first["recovery_required"])
            self.assertTrue(journal.path.exists())

            def retry_planes():
                return self._planes(state)[0]

            second = recover_archive_cleanup(journal.path, retry_planes())
            self.assertEqual(second["status"], "complete")
            self.assertFalse(second["recovery_required"])

    def test_every_cleanup_boundary_is_independently_checked(self):
        for failed_plane in PLANE_ORDER:
            with self.subTest(failed_plane=failed_plane), tempfile.TemporaryDirectory() as directory:
                journal = self._journal(directory)
                state = {}
                planes, _calls = self._planes(state, fail=failed_plane)
                receipt = ArchiveCleanupService(journal, planes).cleanup()
                self.assertEqual(receipt["status"], "unknown")
                self.assertEqual(receipt["planes"][failed_plane], "unknown")
                self.assertEqual(set(receipt["planes"]), set(PLANE_ORDER))
                self.assertTrue(journal.data["recovery_required"])

    def test_callback_error_is_not_unknown_when_postcondition_proves_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(directory)
            state = {}
            planes, _calls = self._planes(state, fail="volume")
            original = planes[2]
            state["volume"] = True

            def cleanup_and_raise():
                state["volume"] = False
                raise RuntimeError("already removed")

            planes[2] = CleanupPlane("volume", cleanup_and_raise, original.verify)
            receipt = ArchiveCleanupService(journal, planes).cleanup()
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["planes"]["volume"], "absent")

    def test_interrupted_cleanup_retains_unknown_state_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(directory)
            state = {}
            planes, _calls = self._planes(state)
            original = planes[0]

            def interrupt():
                raise KeyboardInterrupt()

            planes[0] = CleanupPlane("container", interrupt, original.verify)
            with self.assertRaises(KeyboardInterrupt):
                ArchiveCleanupService(journal, planes).cleanup()
            interrupted = ArchiveReviewJournal.open(journal.path)
            self.assertEqual(interrupted.data["cleanup"]["status"], "unknown")
            self.assertTrue(interrupted.data["recovery_required"])

            receipt = recover_archive_cleanup(journal.path, self._planes(state)[0])
            self.assertEqual(receipt["status"], "complete")

    def test_journal_write_failure_never_returns_a_passing_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(directory)
            state = {}
            planes, _calls = self._planes(state)
            with patch("sandbox.plugin_check.journal._atomic_json_write", side_effect=OSError("blocked")):
                with self.assertRaises(ArchiveCleanupError) as raised:
                    ArchiveCleanupService(journal, planes).cleanup()
            self.assertEqual(raised.exception.code, "archive_journal_write_failed")


if __name__ == "__main__":
    unittest.main()
