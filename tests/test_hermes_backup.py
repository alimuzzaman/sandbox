import unittest

import sandbox.hermes.backup as backup
from sandbox.hermes.backup import plan_restore
from tests.fakes.hermes import RecordingArtifactStore


class TestHermesBackupPlanning(unittest.TestCase):
    def test_restore_plan_is_non_mutating_and_requires_confirmation(self):
        manifest = {"schema_version": 1, "id": "fixture", "artifacts": [
            {"id": "config", "archive": "config.tgz", "sha256": "a" * 64, "restore_target": "/safe"}
        ]}
        before = repr(manifest)
        plan = plan_restore(manifest)
        self.assertEqual(repr(manifest), before)
        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.actions[0]["artifact_id"], "config")

    def test_restore_plan_rejects_missing_or_invalid_integrity_digest(self):
        with self.assertRaisesRegex(ValueError, "sha256"):
            plan_restore({"schema_version": 1, "id": "fixture", "artifacts": [{"id": "config"}]})
        with self.assertRaisesRegex(ValueError, "sha256"):
            plan_restore({"schema_version": 1, "id": "fixture", "artifacts": [
                {"id": "config", "sha256": "not-a-digest"}
            ]})

    def test_artifact_store_create_list_and_integrity_are_injected(self):
        """US7 seam: artifact I/O is injectable and never depends on remote state."""
        service_type = getattr(backup, "HermesBackupService", None)
        self.assertIsNotNone(service_type, "backup must expose HermesBackupService")
        store = RecordingArtifactStore()
        service = service_type(store) if service_type else None
        artifact = {
            "id": "fixture", "archive": "fixture.tgz", "sha256": "b" * 64,
            "created_at": "2026-07-14T00:00:00Z", "scope": "hermes",
        }

        created = service.create(artifact) if service else None
        listed = service.list() if service else ()

        self.assertEqual(created["id"], "fixture")
        self.assertEqual([item["id"] for item in listed], ["fixture"])
        self.assertTrue(service.verify(created))
        self.assertEqual([call[0] for call in store.calls], ["put", "list", "read"])

    def test_retention_hook_returns_candidates_without_deleting_artifacts(self):
        selector = getattr(backup, "retention_candidates", None)
        self.assertTrue(callable(selector), "backup must expose a non-mutating retention_candidates hook")
        artifacts = [
            {"id": "old", "created_at": "2026-07-01T00:00:00Z"},
            {"id": "new", "created_at": "2026-07-14T00:00:00Z"},
        ]
        before = repr(artifacts)

        candidates = selector(artifacts, keep=1) if callable(selector) else ()

        self.assertEqual(repr(artifacts), before)
        self.assertEqual([artifact["id"] for artifact in candidates], ["old"])


if __name__ == "__main__": unittest.main()
