import unittest
from dataclasses import FrozenInstanceError

from sandbox.recovery.models import ArtifactRecord, RecoveryProfile, RecoverySet, RestorePlan, RetentionPlan, SchedulePolicy


class TestRecoveryModels(unittest.TestCase):
    def test_profile_is_immutable(self):
        profile = RecoveryProfile("fixture", "test", "filesystem", ("root",), ("path",),
                                  "partial", "stable", (), "encrypted", "target", "hash", "test")
        with self.assertRaises(FrozenInstanceError):
            profile.scope = "changed"

    def test_profile_metadata_is_not_mutable(self):
        profile = RecoveryProfile("fixture", "test", "filesystem", ("root",), ("path",),
                                  "partial", "stable", (), "encrypted", "target", "hash", "test",
                                  metadata={"owner": "fixture"})
        with self.assertRaises(TypeError):
            profile.metadata["owner"] = "changed"

    def test_artifact_state_machine_is_forward_only(self):
        record = ArtifactRecord("fixture", "artifact", "filesystem")
        self.assertEqual(record.transition("validated").state, "validated")
        with self.assertRaises(ValueError):
            record.transition("capturing")

    def test_recovery_set_only_becomes_restorable_when_complete(self):
        staged = RecoverySet("set-1")
        self.assertFalse(staged.restorable)
        complete = staged.transition("captured").transition("encrypted").transition("remotely_verified").transition("complete")
        self.assertTrue(complete.restorable)

    def test_restore_plan_requires_confirmation(self):
        plan = RestorePlan("set-1", ("fixture",), (), (), (), (), ())
        self.assertTrue(plan.requires_confirmation)

    def test_schedule_is_disabled_and_retention_is_protected_by_default(self):
        self.assertFalse(SchedulePolicy("daily", ("fixture",), "daily").enabled)
        self.assertTrue(RetentionPlan("recovery/", ("latest",), ()).requires_confirmation)


if __name__ == "__main__": unittest.main()
