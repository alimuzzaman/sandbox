import unittest
from sandbox.hermes.backup import plan_restore


class TestHermesBackupPlanning(unittest.TestCase):
    def test_restore_plan_is_non_mutating_and_requires_confirmation(self):
        manifest = {"schema_version": 1, "id": "fixture", "artifacts": [
            {"id": "config", "archive": "config.tgz", "sha256": "abc", "restore_target": "/safe"}
        ]}
        before = repr(manifest)
        plan = plan_restore(manifest)
        self.assertEqual(repr(manifest), before)
        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.actions[0]["artifact_id"], "config")


if __name__ == "__main__": unittest.main()
