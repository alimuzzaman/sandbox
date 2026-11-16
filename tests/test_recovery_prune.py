"""`sb recovery retention --prune`: plan-bound deletion of verified sets beyond N."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.recovery.drive import MemoryDrive, RcloneDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.service import RecoveryService


def put_set(drive, set_id, day, *, profile="infisical", manifest=True):
    ciphertext = f"ciphertext-{set_id}".encode()
    drive.put(f"sets/{set_id}/archive.bin", ciphertext)
    if manifest:
        drive.put(f"sets/{set_id}/manifest.json", json.dumps({
            "schema_version": 1, "id": set_id, "status": "complete",
            "created_at": f"2026-11-{day:02d}T03:00:00Z", "profiles": [profile],
            "ciphertext_object": f"sets/{set_id}/archive.bin",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_size": len(ciphertext),
        }).encode())


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(os.path.realpath(self.temp.name)) / "recovery"
        self.drive = MemoryDrive()
        self.drive.destination = "gdrive:fixture"
        for day in range(1, 6):
            put_set(self.drive, f"infisical-{day:02d}", day)
        put_set(self.drive, "hermes-01", 1, profile="hermes")
        put_set(self.drive, "infisical-00", 1, manifest=False)  # incomplete
        self.service = RecoveryService(None, drive=self.drive, capture=SimpleNamespace())
        patcher = patch.object(RecoveryService, "_current_passphrase_verifies", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def prune(self, **kwargs):
        kwargs.setdefault("keep_count", 2)
        return self.service.retention_prune("infisical", state_root=self.state, **kwargs)

    def sets(self):
        return sorted({key.split("/")[1] for key in self.drive.objects})

    def test_plan_then_confirm_deletes_only_older_verified_sets_of_the_profile(self):
        planned = self.prune()
        self.assertTrue(planned["ok"], planned)
        data = planned["data"]
        self.assertEqual(data["protected_sets"], ("infisical-04", "infisical-05"))
        self.assertEqual(data["candidates"], ("infisical-01", "infisical-02", "infisical-03"))
        self.assertTrue(data["requires_confirmation"])
        self.assertEqual(len(self.sets()), 7, "planning deletes nothing")
        applied = self.prune(plan_id=data["plan_id"], confirm=True)
        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["data"]["deleted"], ["infisical-01", "infisical-02", "infisical-03"])
        self.assertEqual(self.sets(), ["hermes-01", "infisical-00", "infisical-04", "infisical-05"])
        journal = (self.state / "retention/deletions" / f"{data['plan_id']}.jsonl").read_text()
        phases = [(event["phase"], event["set_id"]) for event in map(json.loads, journal.splitlines())]
        self.assertEqual(phases[:2], [("intent", "infisical-01"), ("outcome", "infisical-01")])
        for path in (self.state / "retention/plans", self.state / "retention/deletions"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_confirmation_and_plan_id_are_both_required(self):
        self.assertEqual(self.prune(confirm=True)["error"]["code"], "confirmation_required")
        plan_id = self.prune()["data"]["plan_id"]
        self.assertEqual(self.prune(plan_id=plan_id)["error"]["code"], "confirmation_required")
        self.assertEqual(self.prune(plan_id="rp_" + "0" * 32, confirm=True)["error"]["code"],
                         "retention_plan_unknown")
        self.assertEqual(self.prune(plan_id=plan_id, confirm=True, keep_count=3)["error"]["code"],
                         "retention_plan_mismatch")
        self.assertEqual(len(self.sets()), 7)

    def test_a_new_set_after_planning_makes_the_plan_stale(self):
        plan_id = self.prune()["data"]["plan_id"]
        put_set(self.drive, "infisical-06", 6)
        outcome = self.prune(plan_id=plan_id, confirm=True)
        self.assertEqual(outcome["error"]["code"], "stale_retention_plan")
        self.assertEqual(len(self.sets()), 8)

    def test_a_changed_candidate_makes_the_plan_stale(self):
        plan_id = self.prune()["data"]["plan_id"]
        del self.drive.objects["sets/infisical-01/manifest.json"]
        outcome = self.prune(plan_id=plan_id, confirm=True)
        self.assertEqual(outcome["error"]["code"], "stale_retention_plan")
        self.assertIn("sets/infisical-01/archive.bin", self.drive.objects)

    def test_interrupted_prune_resumes_under_the_same_plan(self):
        plan_id = self.prune()["data"]["plan_id"]
        original = self.drive.delete
        calls = []

        def failing(key):
            calls.append(key)
            if len(calls) == 2:
                raise RecoveryError("Drive operation failed", "drive_delete_failed")
            original(key)

        with patch.object(self.drive, "delete", side_effect=failing):
            self.assertEqual(self.prune(plan_id=plan_id, confirm=True)["error"]["code"],
                             "drive_delete_failed")
        self.assertEqual(calls[0], "sets/infisical-01/manifest.json", "manifest is deleted first")
        resumed = self.prune(plan_id=plan_id, confirm=True)
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(self.sets(), ["hermes-01", "infisical-00", "infisical-04", "infisical-05"])

    def test_newest_verified_set_is_never_a_candidate_and_expired_plans_refuse(self):
        planned = self.prune(keep_count=1)["data"]
        self.assertEqual(planned["protected_sets"], ("infisical-05",))
        self.assertNotIn("infisical-05", planned["candidates"])
        with patch("sandbox.recovery.prune.time.time", return_value=4_000_000_000):
            outcome = self.prune(keep_count=1, plan_id=planned["plan_id"], confirm=True)
        self.assertEqual(outcome["error"]["code"], "retention_plan_expired")

    def test_unconfigured_and_invalid_policy_refuse(self):
        self.assertEqual(RecoveryService(None).retention_prune(
            "infisical", keep_count=2, state_root=self.state)["error"]["code"],
            "recovery_not_configured")
        self.assertEqual(RecoveryService(None, drive=self.drive).retention_prune(
            "infisical", keep_count=2, state_root=self.state)["error"]["code"], "missing_passphrase")
        self.assertEqual(self.prune(keep_count=0)["error"]["code"], "invalid_retention_policy")


class RcloneDeleteTests(unittest.TestCase):
    def test_delete_is_confined_to_one_set(self):
        calls = []
        runner = SimpleNamespace(run=lambda argv, timeout: calls.append(argv) or SimpleNamespace(returncode=0))
        drive = RcloneDrive(runner, "gdrive:hermes-full-recovery")
        drive.delete("sets/infisical-01/manifest.json")
        drive.remove_empty_dir("sets/infisical-01")
        self.assertEqual(calls, [
            ("rclone", "deletefile", "gdrive:hermes-full-recovery/sets/infisical-01/manifest.json"),
            ("rclone", "rmdir", "gdrive:hermes-full-recovery/sets/infisical-01"),
        ])
        for key in ("sets/infisical-01", "sets/../x/y", "other/a/b", "sets/a/../b", ""):
            with self.subTest(key=key), self.assertRaises(RecoveryError):
                drive.delete(key)
        with self.assertRaises(RecoveryError):
            drive.remove_empty_dir("sets")


if __name__ == "__main__":
    unittest.main()
