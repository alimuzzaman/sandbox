import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class JobServiceTests(unittest.TestCase):
    def test_synchronized_submission_requires_authoritative_gateway_before_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(
                repository, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: self.fail("submission must not launch"),
            )
            item = JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("source"), sync_relationship_id="relationship",
                sync_generation_id="generation", source_access="managed_read_only",
                parallel_safe=True,
            )
            with self.assertRaisesRegex(
                    RuntimeError, "synchronized_job_authority_unavailable"):
                service.submit(item)
            self.assertEqual(repository.list(), [])
            repository.close()

    def test_default_launcher_uses_package_root_when_cli_was_called_by_absolute_path(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("sandbox.application.job_service.subprocess.Popen", return_value=MagicMock(poll=lambda: None)) as launch:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None)
            service._launch(Path(temp) / "descriptor.json")
            package_root = Path(__file__).resolve().parents[1]
            self.assertEqual(Path(launch.call_args.kwargs["cwd"]).resolve(), package_root)
            repository.close()

    def test_acceptance_precedes_launcher_and_idempotency_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            launched = []
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None, launcher=launched.append)
            submission = JobSubmission("test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("source"), request_id="once")
            first = service.submit(submission); second = service.submit(submission)
            self.assertFalse(first["idempotent_replay"]); self.assertTrue(second["idempotent_replay"])
            self.assertTrue(launched[0].exists())
            self.assertEqual(first["deadline"], {"seconds": 60, "source": "explicit", "reminder": None})
            repository.close()

    def test_workspace_registration_precedes_durable_job_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            observed = []

            class Registry:
                def ensure_submission(self, submission):
                    observed.append((submission.project_identity, repository.list()))
                    return type("Workspace", (), {"workspace_id": "ws_" + "a" * 32})()

            service = JobService(
                repository, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: None, workspace_registry=Registry(),
            )
            result = service.submit(JobSubmission(
                "test", temp, "project-identity", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source"),
            ))
            self.assertTrue(result["ok"])
            self.assertEqual(observed, [("project-identity", [])])
            self.assertEqual(len(repository.list()), 1)
            repository.close()

    def test_resolved_policy_persists_to_descriptor_acceptance_and_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            launched = []
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=launched.append)
            submission = JobSubmission(
                "test", temp, "p", "local", "qa", ("echo", "ok"), 120,
                SourceIdentity("source"), execution_profile="custom", deadline_source="profile:custom",
                deadline_reminder="deadline supplied by profile:custom; pass an explicit timeout to override it",
                stall_seconds=12, cancel_grace_seconds=13, cancel_on_stall=False,
                cleanup_policy="ephemeral", execution_policy_provenance={
                    "execution_profile": "workspace", "deadline": "profile:workspace",
                    "stall": "profile:workspace", "cancel_grace": "profile:workspace",
                    "cancel_on_stall": "profile:workspace", "cleanup": "profile:workspace",
                },
            )
            accepted = service.submit(submission)
            descriptor = json.loads(launched[0].read_text())
            self.assertEqual(descriptor["cancel_grace_seconds"], 13)
            self.assertEqual(accepted["deadline"]["reminder"], submission.deadline_reminder)
            self.assertEqual(accepted["execution_policy"]["provenance"],
                             dict(submission.execution_policy_provenance))
            repository.transition(accepted["job_id"], "running")
            repository.transition(accepted["job_id"], "succeeded")
            retry = service.retry(accepted["job_id"], request_id="policy-retry")
            retried = repository.get(retry["job_id"])
            self.assertEqual((retried["cancel_grace_seconds"], retried["deadline_reminder"]),
                             (13, submission.deadline_reminder))
            self.assertEqual(json.loads(retried["execution_policy_provenance_json"]),
                             dict(submission.execution_policy_provenance))
            repository.close()

    def test_workspace_registration_failure_cannot_accept_a_job(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")

            class Registry:
                def ensure_submission(self, _submission):
                    raise RuntimeError("workspace_index_unavailable")

            service = JobService(
                repository, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: None, workspace_registry=Registry(),
            )
            with self.assertRaisesRegex(RuntimeError, "workspace_index_unavailable"):
                service.submit(JobSubmission(
                    "test", temp, "project-identity", "local", "default",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                ))
            self.assertEqual(repository.list(), [])
            repository.close()

    def test_workspace_resource_binding_failure_precedes_job_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")

            class Registry:
                def ensure_submission(self, _submission):
                    raise RuntimeError("workspace_ownership_drift")

            service = JobService(
                repository, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: None, workspace_registry=Registry(),
            )
            with self.assertRaisesRegex(RuntimeError, "workspace_ownership_drift"):
                service.submit(JobSubmission(
                    "test", temp, "project-identity", "local", "default",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                ))
            self.assertEqual(repository.list(), [])
            repository.close()

    def test_launch_failure_is_durably_failed_never_running_or_successful(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=lambda _descriptor: (_ for _ in ()).throw(OSError("launch failed")))
            with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
                service.submit(JobSubmission("test", temp, "p", "local", "default",
                    ("echo", "ok"), 60, SourceIdentity("source")))
            row = repository.list(limit=1)[0]
            self.assertEqual(row["lifecycle"], "failed")
            self.assertEqual(row["termination_reason"], "supervisor_launch_failed")
            repository.close()

    def test_reconcile_marks_lost_supervisor_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=99999999,
                supervisor_start_identity="start", supervisor_nonce_hash="nonce")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["lifecycle"], "interrupted")
            repository.close()

    def test_reconcile_marks_running_job_without_supervisor_identity_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["termination_reason"], "missing_supervisor_identity")
            repository.close()

    def test_reconcile_marks_missing_child_identity_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=99999999,
                supervisor_start_identity="start", supervisor_nonce_hash="nonce", child_pid=99999998,
                child_pgid=99999998, child_start_identity="child-start")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["termination_reason"], "supervisor_lost")
            repository.close()

    def test_read_reconciliation_interrupts_stale_supervisor_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source"), stall_seconds=1))
            repository.transition(row["job_id"], "running")
            old = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
            repository.put_heartbeat(row["job_id"], supervisor_at=old, health_evidence={})
            result = service.get(row["job_id"])
            self.assertEqual(result["lifecycle"], "interrupted")
            self.assertEqual(result["termination_reason"], "supervisor_heartbeat_stale")
            repository.close()

    def test_retention_sweep_removes_terminal_outputs_and_marks_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            for name in ("output", "artifacts", "metrics"):
                (job_dir / name).mkdir()
                (job_dir / name / "data").write_text("retained")
            result = service.retention_sweep(retention_days=0)
            self.assertEqual(len(result["cleaned"]), 1)
            self.assertEqual(repository.get(row["job_id"])["cleanup_state"], "completed")
            self.assertFalse((job_dir / "output").exists())
            repository.close()

    def test_cleanup_marks_retained_metadata_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.upsert_output_stream(row["job_id"], "stdout", bytes_stored=3,
                events_stored=1, next_sequence=1, complete=True)
            repository.upsert_metrics_index(row["job_id"], samples=1, complete=True)
            repository.add_artifact(row["job_id"], artifact_id="report", display_name="report.txt",
                stored_relative_path="artifacts/report", size_bytes=3, sha256="0" * 64)
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            for name in ("output", "artifacts", "metrics"):
                (job_dir / name).mkdir()
                (job_dir / name / "data").write_text("retained")
            service.cleanup(row["job_id"])
            snapshot = repository.snapshot(row["job_id"])
            self.assertFalse(snapshot["output"][0]["available"])
            self.assertFalse(snapshot["metrics"]["available"])
            self.assertEqual(snapshot["artifacts"][0]["status"], "expired")
            self.assertEqual(snapshot["artifacts"][0]["reason"], "cleanup_removed")
            repository.close()

    def test_scoped_cleanup_remains_retained_until_retention_removes_remaining_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.upsert_output_stream(row["job_id"], "stdout", bytes_stored=3,
                events_stored=1, next_sequence=1, complete=True)
            repository.upsert_metrics_index(row["job_id"], samples=1, complete=True)
            repository.add_artifact(row["job_id"], artifact_id="report", display_name="report.txt",
                stored_relative_path="artifacts/report", size_bytes=3, sha256="0" * 64)
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            (job_dir / "output").mkdir(); (job_dir / "output" / "data").write_text("log")
            (job_dir / "artifacts").mkdir(); (job_dir / "artifacts" / "report").write_text("art")
            (job_dir / "metrics.jsonl").write_text('{"timestamp":1}\n')
            first = service.cleanup(row["job_id"], logs=True, artifacts=False, metrics=False)
            self.assertEqual(first["cleanup_state"], "retained")
            self.assertTrue((job_dir / "artifacts").exists())
            self.assertTrue((job_dir / "metrics.jsonl").exists())
            retained = service.retention_sweep(retention_days=0)
            self.assertEqual(len(retained["cleaned"]), 1)
            self.assertEqual(repository.get(row["job_id"])["cleanup_state"], "completed")
            self.assertFalse((job_dir / "artifacts").exists())
            self.assertFalse((job_dir / "metrics.jsonl").exists())
            repository.close()

    def test_storage_pressure_retention_reclaims_oldest_terminal_job(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            (job_dir / "output").mkdir()
            (job_dir / "output" / "data").write_text("retained")
            storage.is_under_pressure = lambda: True
            result = service.retention_sweep(retention_days=7, storage_pressure=True)
            self.assertTrue(result["storage_pressure"])
            self.assertEqual(len(result["cleaned"]), 1)
            repository.close()
