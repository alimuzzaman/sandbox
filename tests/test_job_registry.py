import hashlib
import json
import sqlite3
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
        self.assertEqual(repo.schema_version(), 6)
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

    def test_synchronized_generation_and_source_policy_are_durable(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        item = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="remote", remote_name="remote", workspace_label="default",
            argv=("python", "-V"), deadline_seconds=60,
            source=SourceIdentity("sha256:source"),
            sync_relationship_id="rel_fixture", sync_generation_id="gen_fixture",
            source_access="managed_read_only", parallel_safe=True,
        )
        row, _ = repo.accept(item)
        self.assertEqual(row["sync_relationship_id"], "rel_fixture")
        self.assertEqual(row["sync_generation_id"], "gen_fixture")
        self.assertEqual(row["source_access"], "managed_read_only")
        snapshot = repo.submission_snapshot(row["job_id"])
        self.assertEqual(snapshot["sync_generation_id"], "gen_fixture")

    def test_credential_like_argv_is_refused_before_any_submission_persistence(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        item = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="default",
            argv=("tool", "--api-key", "synthetic-candidate-value"),
            deadline_seconds=60, request_id="unsafe-request",
            source=SourceIdentity("sha256:source"),
        )

        with self.assertRaisesRegex(ValueError, "credential-like material"):
            repo.accept(item)
        self.assertEqual(repo.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_safe_url_argv_is_persisted_verbatim_while_credential_urls_are_refused(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        safe_url = "https://example.test/path?next=a%2Fb#safe-section"
        safe = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="default", argv=("tool", safe_url),
            deadline_seconds=60, request_id="safe-url",
            source=SourceIdentity("sha256:source"),
        )
        row, _ = repo.accept(safe)
        self.assertEqual(repo.submission_snapshot(row["job_id"])["argv"], ["tool", safe_url])

        unsafe_urls = (
            "https://fixture-user:fixture-password@example.test/path",
            "https://example.test/path?token=fixture-value",
        )
        outcomes = []
        for index, unsafe_url in enumerate(unsafe_urls):
            item = JobSubmission(
                kind="test", project_root="/tmp/project", project_identity="project-1",
                target_kind="local", workspace_label="default", argv=("tool", unsafe_url),
                deadline_seconds=60, request_id=f"unsafe-url-{index}",
                source=SourceIdentity("sha256:source"),
            )
            try:
                repo.accept(item)
            except ValueError:
                outcomes.append(True)
            else:
                outcomes.append(False)
        self.assertTrue(all(outcomes))
        self.assertEqual(repo.connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_active_only_filters_before_the_bounded_page(self):
        repo = self.repository()
        active, _ = repo.accept(submission("active-old"))
        terminal, _ = repo.accept(submission("terminal-new"))
        repo.transition(terminal["job_id"], "running")
        repo.transition(terminal["job_id"], "succeeded", exit_code=0)
        rows = repo.list(limit=1, active_only=True)
        self.assertEqual([item["job_id"] for item in rows], [active["job_id"]])

    def test_resource_index_exposes_exact_workspace_ownership_evidence(self):
        from sandbox.jobs.registry import read_resource_index

        repo = self.repository()
        workspace_id = "ws_" + "a" * 32
        row, _ = repo.accept(submission(), workspace_id=workspace_id)
        indexed = read_resource_index(self.path)["jobs"]
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["job_id"], row["job_id"])
        self.assertEqual(indexed[0]["project_identity"], "project-1")
        self.assertEqual(indexed[0]["project_root"], "/tmp/project")
        self.assertEqual(indexed[0]["target_kind"], "local")
        self.assertIsNone(indexed[0]["remote_name"])
        self.assertEqual(indexed[0]["workspace_id"], workspace_id)

    def test_heartbeat_updates_preserve_prior_observation_timestamps(self):
        repo = self.repository()
        row, _ = repo.accept(submission())
        repo.put_heartbeat(row["job_id"], supervisor_at="2026-01-01T00:00:00Z",
                           last_output_at="2026-01-01T00:00:00Z", health_evidence={"output": True})
        repo.put_heartbeat(row["job_id"], supervisor_at="2026-01-01T00:00:01Z",
                           last_metric_at="2026-01-01T00:00:01Z", health_evidence={"metric": True})
        heartbeat = repo.snapshot(row["job_id"])["heartbeat"]
        self.assertEqual(heartbeat["last_output_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(heartbeat["last_metric_at"], "2026-01-01T00:00:01Z")

    def test_canonical_submission_snapshot_round_trips_retry_policy_without_values(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        item = JobSubmission(
            kind="ci", project_root="/tmp/project", project_identity="project-1",
            target_kind="remote", remote_name="remote-1", workspace_label="ci-cell",
            workspace_mode="isolated", argv=("python", "-V"), cwd_relative="build",
            deadline_seconds=120, deadline_source="workflow", execution_profile="ci",
            output_profile="errors", stall_seconds=30, cancel_on_stall=True,
            cleanup_policy="on-success", request_id="snapshot-1", attempt=2,
            environment_keys=("CI_TOKEN", "NODE_ENV"), artifact_paths=("reports", "coverage.xml"),
            depends_on=("build",), failure_policy="continue",
            compatibility_differences=({
                "id": "act.safe-mode", "workflow": ".github/workflows/ci.yml",
                "location": "jobs.release.steps[0]", "severity": "notice",
                "accepted": True, "detail": "release step neutralized", "catalog_version": "1",
            },),
            source=SourceIdentity("sha256:source", "commit", "dirty"),
        )
        row, _ = repo.accept(item)
        snapshot = repo.submission_snapshot(row["job_id"])
        self.assertEqual(snapshot["artifact_paths"], ["reports", "coverage.xml"])
        self.assertEqual(snapshot["environment_keys"], ["CI_TOKEN", "NODE_ENV"])
        self.assertNotIn("environment", snapshot)
        self.assertEqual(snapshot["compatibility_differences"][0]["id"], "act.safe-mode")
        self.assertEqual(snapshot["source"]["dirty_digest"], "dirty")

    def test_submission_snapshot_accepts_multiline_tabbed_shell_argument(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        item = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="default",
            argv=("sh", "-lc", "printf 'one\\ntwo\\tthree\\n'"),
            deadline_seconds=60, source=SourceIdentity("source"),
        )
        row, _ = repo.accept(item)
        self.assertEqual(repo.submission_snapshot(row["job_id"])["argv"][2], item.argv[2])

    def test_exact_legacy_request_replay_precedes_new_snapshot_validation(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        original, _ = repo.accept(submission("legacy-replay"))
        legacy = JobSubmission(
            kind="test", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="default", argv=("python", "-V"),
            deadline_seconds=60, request_id="legacy-replay", source=SourceIdentity("sha256:source"),
            compatibility_differences=({"id": "act.legacy", "detail": "x" * 2_049},),
        )
        repo.connection.execute("UPDATE jobs SET request_digest=?, submission_json=NULL WHERE job_id=?",
                                (legacy.canonical_digest(), original["job_id"]))
        replayed, replay = repo.accept(legacy)
        self.assertTrue(replay)
        self.assertEqual(replayed["job_id"], original["job_id"])
        self.assertIsNone(repo.submission_snapshot(original["job_id"]))

    def test_default_sync_metadata_preserves_legacy_request_digest_and_replay(self):
        item = submission("legacy-digest")
        legacy_payload = item.as_dict()
        for field in (
                "sync_relationship_id", "sync_generation_id", "source_access",
                "parallel_safe", "materialization_source_root"):
            legacy_payload.pop(field)
        legacy_json = json.dumps(
            legacy_payload, sort_keys=True, separators=(",", ":"),
        )
        legacy_digest = hashlib.sha256(legacy_json.encode()).hexdigest()
        self.assertEqual(item.canonical_digest(), legacy_digest)

        repo = self.repository()
        original, _ = repo.accept(item)
        repo.connection.execute(
            "UPDATE jobs SET request_digest=?, submission_json=NULL WHERE job_id=?",
            (legacy_digest, original["job_id"]),
        )
        replayed, replay = repo.accept(submission("legacy-digest"))
        self.assertTrue(replay)
        self.assertEqual(replayed["job_id"], original["job_id"])

    def test_unset_materialization_source_preserves_v5_request_digest_and_replay(self):
        item = submission("v5-materialization-replay")
        legacy_payload = item.as_dict()
        for field in (
                "sync_relationship_id", "sync_generation_id", "source_access",
                "parallel_safe", "materialization_source_root"):
            legacy_payload.pop(field)
        legacy_json = json.dumps(
            legacy_payload, sort_keys=True, separators=(",", ":"),
        )
        legacy_digest = hashlib.sha256(legacy_json.encode()).hexdigest()
        self.assertEqual(item.canonical_digest(), legacy_digest)

        repo = self.repository()
        original, _ = repo.accept(item)
        repo.connection.execute(
            "UPDATE jobs SET request_digest=?, submission_json=NULL WHERE job_id=?",
            (legacy_digest, original["job_id"]),
        )
        replayed, replay = repo.accept(submission("v5-materialization-replay"))
        self.assertTrue(replay)
        self.assertEqual(replayed["job_id"], original["job_id"])

    def test_submission_snapshot_rejects_unbounded_compatibility_detail(self):
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        repo = self.repository()
        item = JobSubmission(
            kind="ci", project_root="/tmp/project", project_identity="project-1",
            target_kind="local", workspace_label="ci", argv=("echo", "x"),
            deadline_seconds=60, source=SourceIdentity("source"),
            compatibility_differences=({"id": "act.demo", "detail": "x" * 2_049},),
        )
        with self.assertRaisesRegex(ValueError, "snapshot limit"):
            repo.accept(item)
        self.assertEqual(repo.list(), [])

    def test_version_two_registry_migrates_additively_and_legacy_rows_remain_readable(self):
        repo = self.repository()
        row, _ = repo.accept(submission())
        repo.close()
        self.repositories.remove(repo)
        with sqlite3.connect(self.path) as connection:
            columns = {item[1] for item in connection.execute("PRAGMA table_info(jobs)")}
            if "submission_json" in columns:
                connection.execute("ALTER TABLE jobs DROP COLUMN submission_json")
            connection.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
        reopened = self.repository()
        self.assertEqual(reopened.schema_version(), 6)
        self.assertIsNone(reopened.submission_snapshot(row["job_id"]))
        self.assertEqual(reopened.get(row["job_id"])["lifecycle"], "accepted")

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
                                  supervisor_start_identity="100", supervisor_nonce_hash="hash",
                                  child_cgroup_path="/sandbox/job-fixture")
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
        self.assertEqual(snapshot["process"]["child_cgroup_path"],
                         "/sandbox/job-fixture")
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
