import tempfile
import threading
import unittest
from pathlib import Path


def submission(request_id="request-1"):
    from sandbox.jobs.models import JobSubmission, SourceIdentity

    return JobSubmission(
        kind="test", project_root="/tmp/project", project_identity="project-1",
        target_kind="local", workspace_label="default", argv=("python", "-V"),
        deadline_seconds=60, request_id=request_id,
        source=SourceIdentity("sha256:source"),
    )


class JobRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "registry.sqlite3"
        self.repositories = []

    def tearDown(self):
        for repository in self.repositories:
            repository.close()
        self.temp.cleanup()

    def repository(self):
        from sandbox.jobs.registry import JobRepository
        repository = JobRepository(self.path)
        self.repositories.append(repository)
        return repository

    def test_schema_uses_wal_foreign_keys_and_version(self):
        repo = self.repository()
        self.assertEqual(repo.schema_version(), 2)
        self.assertEqual(repo.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(repo.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        names = {row[0] for row in repo.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue({"jobs", "process_identities", "heartbeats", "workspace_leases",
                         "output_streams", "job_events", "metrics_index", "artifacts",
                         "compatibility_differences"}.issubset(names))

    def test_accept_is_durable_and_idempotent(self):
        repo = self.repository()
        first, replay = repo.accept(submission())
        second, second_replay = repo.accept(submission())
        self.assertFalse(replay)
        self.assertTrue(second_replay)
        self.assertEqual(first["job_id"], second["job_id"])
        reopened = self.repository()
        self.assertEqual(reopened.get(first["job_id"])["lifecycle"], "accepted")

    def test_request_id_conflict_fails_atomically(self):
        from sandbox.jobs.registry import RequestIdConflict
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        repo.accept(submission())
        changed = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="default", argv=("python", "-c", "pass"),
            deadline_seconds=60, request_id="request-1", source=SourceIdentity("sha256:source"),
        )
        with self.assertRaises(RequestIdConflict):
            repo.accept(changed)
        self.assertEqual(len(repo.list(limit=10)), 1)

    def test_concurrent_same_request_creates_one_job(self):
        job_ids = []
        errors = []

        def worker():
            try:
                row, _ = self.repository().accept(submission())
                job_ids.append(row["job_id"])
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(job_ids)), 1)

    def test_transitions_process_heartbeat_events_and_indexes_round_trip(self):
        repo = self.repository()
        row, _ = repo.accept(submission())
        job_id = row["job_id"]
        repo.transition(job_id, "queued")
        repo.transition(job_id, "running")
        repo.put_process_identity(job_id, host_boot_id="boot", supervisor_pid=10,
                                  supervisor_start_identity="100", supervisor_nonce_hash="hash")
        repo.put_heartbeat(job_id, supervisor_at="2026-07-18T00:00:00Z",
                           health_evidence={"reason": "alive"})
        repo.append_event(job_id, "progress", {"current": 1})
        repo.upsert_output_stream(job_id, "stdout", bytes_stored=4, events_stored=1,
                                  next_sequence=1)
        repo.upsert_metrics_index(job_id, samples=1)
        repo.add_artifact(job_id, artifact_id="a1", display_name="report.txt",
                          stored_relative_path="artifacts/a1/report.txt", size_bytes=4,
                          sha256="0" * 64)
        snapshot = repo.snapshot(job_id)
        self.assertEqual(snapshot["lifecycle"], "running")
        self.assertEqual(snapshot["process"]["supervisor_pid"], 10)
        self.assertEqual(snapshot["heartbeat"]["health_evidence"]["reason"], "alive")
        self.assertEqual(snapshot["output"][0]["bytes_stored"], 4)
        self.assertEqual(snapshot["metrics"]["samples"], 1)
        self.assertEqual(snapshot["artifacts"][0]["artifact_id"], "a1")

    def test_compatibility_differences_are_durable_with_job_snapshot(self):
        repo = self.repository()
        row, _ = repo.accept(submission())
        repo.record_compatibility_differences(row["job_id"], [{
            "id": "act.safe-mode", "workflow": ".github/workflows/ci.yml",
            "location": "jobs.release.steps[0]", "severity": "accepted",
            "accepted": True, "detail": "publish step skipped", "catalog_version": "v1",
        }])
        snapshot = repo.snapshot(row["job_id"])
        self.assertEqual(snapshot["compatibility_differences"][0]["difference_id"], "act.safe-mode")
        self.assertEqual(snapshot["compatibility_differences"][0]["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
