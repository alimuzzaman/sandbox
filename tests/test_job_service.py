import json
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.process import ProcessIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import WorkspaceBusy
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.supervisor import run_descriptor
from sandbox.sync.models import SynchronizationRelationship
from sandbox.sync.projection import SyncJobGateway
from sandbox.sync.repository import SyncRepository


BOOT = "11111111-2222-3333-4444-555555555555"


class _CiReleaseRegistry:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0

    def release_terminal_job(self, job, repo):
        self.calls += 1
        if self.mode == "raise":
            raise RuntimeError("stack_teardown_incomplete")
        if self.mode == "failed":
            return {"ok": False, "status": "failed"}
        if self.mode == "retained":
            return {"ok": True, "status": "retained"}
        if self.mode == "released":
            return {"ok": True, "status": "released"}
        raise AssertionError(self.mode)


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

    def test_synchronized_submission_pins_accepted_generation_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            generation, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="request",
                request_digest="b" * 64, manifest_digest="a" * 64,
                file_count=1, byte_count=1, commit="1" * 40,
                created_at="2026-08-26T00:00:00Z",
            )
            sync.claim_generation_transfer(generation.generation_id)
            sync.transition_generation(
                generation.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:01Z",
            )
            projected = Path(temp) / "generation"
            projected.mkdir()
            gateway = SyncJobGateway(sync, materialize=lambda decision, _submission: {
                "project_root": str(projected),
                "source_identity": "sha256:" + "a" * 64,
            })
            launched = []
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=launched.append, sync_gateway=gateway,
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=generation.generation_id,
                source_access="managed_read_only",
                parallel_safe=True,
            ))
            row = jobs.get(accepted["job_id"])
            self.assertEqual(row["project_root"], str(projected))
            self.assertEqual(accepted["generation"], {
                "relationship_id": "relationship",
                "generation_id": generation.generation_id,
                "source_access": "managed_read_only",
            })
            self.assertEqual(service.get(accepted["job_id"], reconcile=False)["generation"],
                             accepted["generation"])
            self.assertEqual(len(launched), 1)
            self.assertEqual(len(sync.active_pins("relationship")), 1)
            jobs.transition(accepted["job_id"], "running")
            jobs.transition(accepted["job_id"], "succeeded")
            service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(sync.active_pins("relationship"), ())
            jobs.close()

    def test_newest_pending_generation_blocks_stale_job_before_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            generation, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="request",
                request_digest="b" * 64, manifest_digest="a" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:00Z",
            )
            sync.claim_generation_transfer(generation.generation_id)
            sync.transition_generation(
                generation.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:01Z",
            )
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda path: launched.append(path),
                sync_gateway=SyncJobGateway(
                    sync, materialize=lambda *_args: {
                        "project_root": str(Path(temp) / "pending-generation"),
                        "source_identity": "sha256:" + "d" * 64,
                    }),
            )
            launched = []
            submission = JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=generation.generation_id,
                request_id="job-pending-replay",
            )
            accepted = service.submit(submission)
            self.assertEqual(accepted["queue"]["reason"], "sync_generation_pending")
            self.assertEqual(jobs.get(accepted["job_id"])["sync_generation_id"],
                             pending.generation_id)
            self.assertEqual(service.get(
                accepted["job_id"], reconcile=False,
            )["queue"]["reason"], "sync_generation_pending")
            self.assertEqual(launched, [])
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            newest, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="newest",
                request_digest="e" * 64, manifest_digest="f" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:04Z",
            )
            queued = service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(queued["queue"]["reason"], "sync_generation_pending")
            self.assertEqual(jobs.get(accepted["job_id"])["sync_generation_id"],
                             newest.generation_id)
            sync.claim_generation_transfer(newest.generation_id)
            sync.transition_generation(
                newest.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:05Z",
            )
            (Path(temp) / "pending-generation").mkdir()
            service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(len(launched), 1)
            self.assertEqual(jobs.get(accepted["job_id"])["source_identity"],
                             "sha256:" + "d" * 64)
            replay = service.submit(submission)
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(replay["job_id"], accepted["job_id"])
            jobs.close()

    def test_failed_pending_generation_terminates_queued_job(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: self.fail("failed generation must not launch"),
                sync_gateway=SyncJobGateway(
                    sync, materialize=lambda *_args: self.fail("must not materialize")),
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
            ))
            sync.transition_generation(pending.generation_id, "failed")
            terminal = service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(terminal["lifecycle"], "failed")
            self.assertEqual(terminal["termination_reason"],
                             "sync_generation_failed")
            jobs.close()

    def test_promoted_pending_generation_launch_failure_releases_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            projected = Path(temp) / "generation"
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: (_ for _ in ()).throw(OSError("launch")),
                sync_gateway=SyncJobGateway(sync, materialize=lambda *_args: {
                    "project_root": str(projected),
                    "source_identity": "sha256:" + "d" * 64,
                }),
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
            ))
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            projected.mkdir()
            with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
                service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(jobs.get(accepted["job_id"])["lifecycle"], "failed")
            self.assertEqual(sync.active_pins("relationship"), ())
            jobs.close()

    def test_concurrent_pending_promotion_has_one_launch_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            projected = Path(temp) / "generation"
            launched = []
            started = threading.Event()
            release = threading.Event()
            def launcher(path):
                launched.append(path)
                started.set()
                release.wait(5)
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=launcher,
                sync_gateway=SyncJobGateway(sync, materialize=lambda *_args: {
                    "project_root": str(projected),
                    "source_identity": "sha256:" + "d" * 64,
                }),
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
                parallel_safe=True,
            ))
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            projected.mkdir()
            errors = []
            def promote():
                try:
                    service.get(accepted["job_id"], reconcile=False)
                except Exception as exc:
                    errors.append(exc)
            first = threading.Thread(
                target=promote,
            )
            first.start()
            self.assertTrue(started.wait(5))
            try:
                second = service.get(accepted["job_id"], reconcile=False)
                self.assertEqual(second["queue"]["reason"],
                                 "sync_generation_launch_committed")
                observer_jobs = JobRepository(Path(temp) / "registry.sqlite")
                observer = JobService(
                    observer_jobs, JobStorage(temp, free_disk_reserve=0), None,
                    launcher=lambda _path: self.fail("observer must not launch"),
                    sync_gateway=service.sync_gateway,
                )
                startup = observer.reconcile_startup()
                self.assertEqual(startup["interrupted"], [])
                self.assertEqual(jobs.get(accepted["job_id"])["lifecycle"],
                                 "queued")
                observer_jobs.close()
            except Exception as exc:
                errors.append(exc)
            finally:
                release.set()
                first.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(len(launched), 1)
            self.assertEqual(len(sync.active_pins("relationship")), 1)
            jobs.close()

    def test_cancel_wins_before_pending_launch_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            projected = Path(temp) / "generation"
            admission_started = threading.Event()
            release_admission = threading.Event()
            class Scheduler:
                def acquire(self, *_args, **_kwargs):
                    admission_started.set()
                    release_admission.wait(5)
                def release(self, _job_id):
                    pass
                def queue_details(self, _row):
                    return {"reason": "busy", "position": 1,
                            "blocking_jobs": []}
                def reconcile_stale(self):
                    return []
            launched = []
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=launched.append, scheduler=Scheduler(),
                sync_gateway=SyncJobGateway(sync, materialize=lambda *_args: {
                    "project_root": str(projected),
                    "source_identity": "sha256:" + "d" * 64,
                }),
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
            ))
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            projected.mkdir()
            errors = []
            def promote():
                try:
                    service.get(accepted["job_id"], reconcile=False)
                except Exception as exc:
                    errors.append(exc)
            promoter = threading.Thread(target=promote)
            promoter.start()
            self.assertTrue(admission_started.wait(5))
            service.cancel(accepted["job_id"])
            release_admission.set()
            promoter.join(5)
            self.assertEqual(errors, [])
            self.assertEqual(launched, [])
            self.assertEqual(jobs.get(accepted["job_id"])["lifecycle"],
                             "cancelled")
            self.assertEqual(sync.active_pins("relationship"), ())
            jobs.close()

    def test_pending_launch_claim_requeues_after_scheduler_contention(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            projected = Path(temp) / "generation"
            class Scheduler:
                attempts = 0
                def acquire(self, *_args, **_kwargs):
                    self.attempts += 1
                    if self.attempts == 1:
                        raise WorkspaceBusy("busy")
                def queue_details(self, _row):
                    return {"reason": "workspace_or_capacity_busy",
                            "position": 1, "blocking_jobs": []}
                def release(self, _job_id):
                    pass
            scheduler = Scheduler()
            launched = []
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=launched.append, scheduler=scheduler,
                sync_gateway=SyncJobGateway(sync, materialize=lambda *_args: {
                    "project_root": str(projected),
                    "source_identity": "sha256:" + "d" * 64,
                }),
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
            ))
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            projected.mkdir()
            blocked = service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(blocked["queue_reason"], "workspace_or_capacity_busy")
            self.assertEqual(jobs.get(accepted["job_id"])["queue_reason"],
                             "sync_generation_pending")
            service.get(accepted["job_id"], reconcile=False)
            self.assertEqual(len(launched), 1)
            jobs.close()

    def test_synchronized_request_race_replays_original_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            generation, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="generation",
                request_digest="b" * 64, manifest_digest="a" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:00Z",
            )
            sync.claim_generation_transfer(generation.generation_id)
            sync.transition_generation(
                generation.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:01Z",
            )
            projected = Path(temp) / "generation"
            projected.mkdir()
            submission = JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), request_id="job-race",
                sync_relationship_id="relationship",
                sync_generation_id=generation.generation_id,
            )
            original_replay = jobs.replay
            inserted = []
            def racing_replay(value):
                result = original_replay(value)
                if not inserted and result is None:
                    jobs.accept(submission)
                    inserted.append(True)
                return result
            jobs.replay = racing_replay
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: self.fail("replay must not launch"),
                sync_gateway=SyncJobGateway(sync, materialize=lambda *_args: {
                    "project_root": str(projected),
                    "source_identity": "sha256:" + "a" * 64,
                }),
            )
            replay = service.submit(submission)
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(len(jobs.list()), 1)
            jobs.close()

    def test_startup_reconciliation_releases_terminal_generation_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            generation, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="request",
                request_digest="b" * 64, manifest_digest="a" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:00Z",
            )
            sync.claim_generation_transfer(generation.generation_id)
            sync.transition_generation(
                generation.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:01Z",
            )
            projected = Path(temp) / "generation"
            projected.mkdir()
            gateway = SyncJobGateway(sync, materialize=lambda *_args: {
                "project_root": str(projected),
                "source_identity": "sha256:" + "a" * 64,
            })
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: None, sync_gateway=gateway,
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=generation.generation_id,
            ))
            jobs.transition(accepted["job_id"], "running")
            jobs.transition(accepted["job_id"], "succeeded")
            self.assertEqual(len(sync.active_pins("relationship")), 1)
            service.reconcile_startup()
            self.assertEqual(sync.active_pins("relationship"), ())
            jobs.close()

    def test_startup_gives_committed_launch_bounded_supervisor_handoff_grace(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            submission = JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id="generation",
            )
            row, _ = jobs.accept(submission)
            jobs.transition(
                row["job_id"], "queued",
                queue_reason="sync_generation_pending",
            )
            jobs.claim_pending_sync_launch(
                row["job_id"], owner_boot_id=BOOT,
                owner_pid=99999999, owner_start_identity="dead-start",
            )
            jobs.commit_pending_sync_launch(
                row["job_id"], owner_boot_id=BOOT,
                owner_pid=99999999, owner_start_identity="dead-start",
            )
            gateway = MagicMock()
            gateway.release_terminal_jobs.return_value = ()
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: None, sync_gateway=gateway,
            )
            with patch("sandbox.application.job_service.capture_process_identity", return_value=None), \
                    patch("sandbox.application.job_service.process_absence_proven", return_value=True):
                fresh = service.reconcile_startup()
                self.assertEqual(fresh["interrupted"], [])
                self.assertEqual(jobs.get(row["job_id"])["lifecycle"], "queued")
                future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
                jobs.connection.execute(
                    "UPDATE jobs SET updated_at=? WHERE job_id=?",
                    (future, row["job_id"]),
                )
                stale = service.reconcile_startup()
            self.assertEqual(stale["interrupted"], [row["job_id"]])
            self.assertEqual(jobs.get(row["job_id"])["lifecycle"], "interrupted")
            jobs.close()

    def test_delayed_real_supervisor_survives_dead_claimant_handoff_grace(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = JobRepository(Path(temp) / "registry.sqlite")
            sync = SyncRepository(Path(temp) / "sync.json")
            sync.put_relationship(SynchronizationRelationship(
                "relationship", "p", "remote", "workspace",
                mode="live", lifecycle="active",
                updated_at="2026-08-26T00:00:00Z",
            ))
            pending, _ = sync.reserve_generation(
                relationship_id="relationship", request_id="pending",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
                created_at="2026-08-26T00:00:02Z",
            )
            projected = Path(temp) / "generation"
            start_supervisor = threading.Event()
            supervisor_results = []
            supervisor_threads = []
            def delayed_launcher(path):
                def supervise():
                    start_supervisor.wait(5)
                    supervisor_results.append(run_descriptor(path))
                thread = threading.Thread(target=supervise)
                thread.start()
                supervisor_threads.append(thread)
            gateway = SyncJobGateway(sync, materialize=lambda *_args: {
                "project_root": str(projected),
                "source_identity": "sha256:" + "d" * 64,
            })
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=delayed_launcher, sync_gateway=gateway,
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("caller"), sync_relationship_id="relationship",
                sync_generation_id=pending.generation_id,
            ))
            sync.claim_generation_transfer(pending.generation_id)
            sync.transition_generation(
                pending.generation_id, "accepted",
                accepted_at="2026-08-26T00:00:03Z",
            )
            projected.mkdir()
            service.get(accepted["job_id"], reconcile=False)
            jobs.connection.execute(
                "UPDATE jobs SET launch_owner_boot_id='dead-boot', "
                "launch_owner_pid=99999999, launch_owner_start_identity='dead-start' "
                "WHERE job_id=?", (accepted["job_id"],),
            )
            observer_jobs = JobRepository(Path(temp) / "registry.sqlite")
            observer = JobService(
                observer_jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: self.fail("observer must not launch"),
                sync_gateway=gateway,
            )
            recovery = observer.reconcile_startup()
            self.assertEqual(recovery["interrupted"], [])
            self.assertEqual(observer_jobs.get(accepted["job_id"])["queue_reason"],
                             "sync_generation_launch_committed")
            start_supervisor.set()
            supervisor_threads[0].join(10)
            self.assertEqual(supervisor_results, [0])
            self.assertEqual(observer_jobs.get(accepted["job_id"])["lifecycle"],
                             "succeeded")
            observer_jobs.close()
            jobs.close()

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
            self.assertEqual(descriptor["authoritative_context"], {
                "job_id": accepted["job_id"], "request_id": None,
                "project_identity": "p",
                "project_root_digest": "sha256:" + __import__("hashlib").sha256(
                    temp.encode()).hexdigest(),
                "source_identity": "source", "source_commit": None,
                "source_dirty_digest": None,
            })
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
            detail = json.loads(repository.get(row["job_id"])["result_json"])["launch_failure"]
            self.assertEqual(detail, {"stage": "supervisor_spawn", "error_class": "OSError",
                                      "errno": None})
            repository.close()

    def test_reconcile_marks_lost_supervisor_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.put_process_identity(row["job_id"], host_boot_id=BOOT, supervisor_pid=99999999,
                supervisor_start_identity="start", supervisor_nonce_hash="nonce")
            with patch("sandbox.application.job_service.capture_process_identity", return_value=None), \
                    patch("sandbox.application.job_service.process_absence_proven", return_value=True):
                result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["lifecycle"], "interrupted")
            repository.close()

    def test_reconcile_keeps_running_job_without_supervisor_identity_unresolved(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [])
            self.assertEqual(repository.get(row["job_id"])["lifecycle"], "running")
            self.assertIsNone(repository.get(row["job_id"])["termination_reason"])
            repository.close()

    def test_reconcile_marks_child_start_identity_mismatch_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.put_process_identity(row["job_id"], host_boot_id=BOOT, supervisor_pid=99999999,
                supervisor_start_identity="start", supervisor_nonce_hash="nonce", child_pid=99999998,
                child_pgid=99999998, child_start_identity="child-start")
            with patch("sandbox.application.job_service.capture_process_identity", side_effect=[
                    ProcessIdentity(BOOT, 99999999, "start", "ignored"),
                    ProcessIdentity(BOOT, 99999998, "different-child-start", "ignored"),
            ]):
                result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["termination_reason"],
                             "child_process_identity_mismatch")
            repository.close()

    def test_read_reconciliation_preserves_stale_heartbeat_without_owner_identity(self):
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
            self.assertEqual(result["lifecycle"], "running")
            self.assertIsNone(result["termination_reason"])
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

    def test_retention_sweep_progresses_to_older_eligible_jobs_across_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            # 60 eligible terminal jobs older than 7 days (> 1 batch page of 50)
            eligible_ids = []
            for i in range(60):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"eligible-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-el-{i}",
                ))
                jid = row["job_id"]
                eligible_ids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=10, seconds=60 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, jid))
                job_dir = storage.job_dir(jid, create=True)
                (job_dir / "output").mkdir()
                (job_dir / "output" / "log").write_text("data")

            # 10 newer running jobs (active, protected)
            running_ids = []
            for i in range(10):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"running-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-run-{i}",
                ))
                jid = row["job_id"]
                running_ids.append(jid)
                repository.transition(jid, "running")
                job_dir = storage.job_dir(jid, create=True)
                (job_dir / "output").mkdir()
                (job_dir / "output" / "log").write_text("live")

            # 10 newer completed jobs (cleanup_state='completed')
            for i in range(10):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"completed-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-comp-{i}",
                ))
                jid = row["job_id"]
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
                repository.connection.execute(
                    "UPDATE jobs SET finished_at=?, cleanup_state='completed' WHERE job_id=?",
                    (fin_time, jid),
                )

            sweep = service.retention_sweep(retention_days=7, limit=60)
            self.assertEqual(len(sweep["cleaned"]), 60)
            cleaned_set = {c["job_id"] for c in sweep["cleaned"]}
            self.assertEqual(cleaned_set, set(eligible_ids))

            # Active jobs remain protected and untouched
            for jid in running_ids:
                self.assertEqual(repository.get(jid)["lifecycle"], "running")
                self.assertTrue((storage.job_dir(jid) / "output").exists())

            repository.close()

    def test_retention_sweep_under_pressure_reclaims_oldest_until_cleared(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            job_ids = []
            for i in range(4):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"pressure-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-p-{i}",
                ))
                jid = row["job_id"]
                job_ids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin = (now - timedelta(days=4 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin, jid))
                (storage.job_dir(jid, create=True) / "output").mkdir()
                (storage.job_dir(jid) / "output" / "data").write_text("log")

            cleaned_count = 0
            real_cleanup = service.cleanup

            def dynamic_cleanup(job_id):
                nonlocal cleaned_count
                cleaned_count += 1
                if cleaned_count >= 2:
                    storage.is_under_pressure = lambda: False
                return real_cleanup(job_id)

            storage.is_under_pressure = lambda: True
            service.cleanup = dynamic_cleanup

            sweep = service.retention_sweep(retention_days=30, storage_pressure=True)
            self.assertEqual(len(sweep["cleaned"]), 2)
            self.assertEqual(sweep["cleaned"][0]["job_id"], job_ids[0])
            self.assertEqual(sweep["cleaned"][1]["job_id"], job_ids[1])
            self.assertEqual(repository.get(job_ids[2])["cleanup_state"], "not_requested")
            self.assertEqual(repository.get(job_ids[3])["cleanup_state"], "not_requested")
            repository.close()

    def test_retention_sweep_preserves_active_retry_and_cleans_original(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            orig_row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "orig",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-orig",
            ))
            orig_id = orig_row["job_id"]
            repository.transition(orig_id, "running")
            repository.transition(orig_id, "failed", exit_code=1)
            fin_orig = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_orig, orig_id))
            (storage.job_dir(orig_id, create=True) / "output").mkdir()
            (storage.job_dir(orig_id) / "output" / "data").write_text("orig")

            retry_row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "retry",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-retry", retry_of_job_id=orig_id,
            ))
            retry_id = retry_row["job_id"]
            repository.transition(retry_id, "running")
            (storage.job_dir(retry_id, create=True) / "output").mkdir()
            (storage.job_dir(retry_id) / "output" / "data").write_text("retry")

            sweep = service.retention_sweep(retention_days=7)
            cleaned_ids = {c["job_id"] for c in sweep["cleaned"]}
            self.assertIn(orig_id, cleaned_ids)
            self.assertNotIn(retry_id, cleaned_ids)
            self.assertEqual(repository.get(retry_id)["lifecycle"], "running")
            self.assertTrue((storage.job_dir(retry_id) / "output").exists())
            repository.close()

    def test_retention_sweep_repeated_calls_progress_past_page_of_retained_and_noop_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            # 55 terminal jobs already in 'retained' state with no remaining data (page of no-ops)
            noop_ids = []
            for i in range(55):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"noop-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-noop-{i}",
                ))
                jid = row["job_id"]
                noop_ids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=20, seconds=100 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute(
                    "UPDATE jobs SET finished_at=?, cleanup_state='retained' WHERE job_id=?",
                    (fin_time, jid),
                )

            # 10 eligible jobs older than 7 days
            batch1_ids = []
            for i in range(10):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"b1-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-b1-{i}",
                ))
                jid = row["job_id"]
                batch1_ids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=15, seconds=100 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, jid))
                (storage.job_dir(jid, create=True) / "output").mkdir()
                (storage.job_dir(jid) / "output" / "data").write_text("b1")

            # 10 more eligible jobs older than 7 days
            batch2_ids = []
            for i in range(10):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"b2-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-b2-{i}",
                ))
                jid = row["job_id"]
                batch2_ids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=10, seconds=100 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, jid))
                (storage.job_dir(jid, create=True) / "output").mkdir()
                (storage.job_dir(jid) / "output" / "data").write_text("b2")

            # First sweep: should skip no-ops and clean first 10 eligible
            sweep1 = service.retention_sweep(retention_days=7, limit=10)
            self.assertEqual(len(sweep1["cleaned"]), 10)
            cleaned1 = [c["job_id"] for c in sweep1["cleaned"]]
            self.assertEqual(cleaned1, batch1_ids)

            # Second sweep: should progress to next 10 eligible
            sweep2 = service.retention_sweep(retention_days=7, limit=10)
            self.assertEqual(len(sweep2["cleaned"]), 10)
            cleaned2 = [c["job_id"] for c in sweep2["cleaned"]]
            self.assertEqual(cleaned2, batch2_ids)

            # Third sweep: no more eligible jobs, returns 0 cleaned
            sweep3 = service.retention_sweep(retention_days=7, limit=10)
            self.assertEqual(len(sweep3["cleaned"]), 0)

            repository.close()

    def test_retention_sweep_truthfully_propagates_materialization_broker_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            class MockWorkspaceRegistry:
                def has_retained_materialization(self, job):
                    return True
                def retire_terminal_materialization(self, job):
                    raise RuntimeError("simulated broker failure")
            service = JobService(repository, storage, None, launcher=lambda _desc: None, workspace_registry=MockWorkspaceRegistry())
            now = datetime.now(timezone.utc)

            row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "failing-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-failing",
            ))
            jid = row["job_id"]
            repository.transition(jid, "running")
            repository.transition(jid, "succeeded", exit_code=0)
            fin_time = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute(
                "UPDATE jobs SET finished_at=?, kind='ci', workspace_authority_digest='sha256:' || hex(randomblob(32)) WHERE job_id=?",
                (fin_time, jid),
            )
            # A refused retirement must leave the job's files in place, so the
            # reported `removed: []` stays true.
            job_dir = storage.job_dir(jid, create=True)
            for name in ("output", "artifacts"):
                (job_dir / name).mkdir(parents=True, exist_ok=True)
                (job_dir / name / "kept.txt").write_text("synthetic")

            sweep = service.retention_sweep(retention_days=7)
            self.assertFalse(sweep["ok"])
            self.assertEqual(len(sweep["cleaned"]), 1)
            self.assertEqual(sweep["cleaned"][0]["job_id"], jid)
            self.assertFalse(sweep["cleaned"][0]["ok"])
            self.assertEqual(sweep["cleaned"][0]["cleanup_state"], "retained")
            self.assertEqual(sweep["cleaned"][0]["removed"], [])
            for name in ("output", "artifacts"):
                self.assertTrue((job_dir / name / "kept.txt").exists(), name)

            # Direct cleanup maintains fail-closed retirement exception contract
            with self.assertRaisesRegex(RuntimeError, "simulated broker failure"):
                service.cleanup(jid)
            for name in ("output", "artifacts"):
                self.assertTrue((job_dir / name / "kept.txt").exists(), name)

            repository.close()

    def test_retention_sweep_explicit_cursor_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            subs = []
            for i in range(5):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"cur-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-cur-{i}",
                ))
                jid = row["job_id"]
                subs.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=10, seconds=5 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, jid))
                (storage.job_dir(jid, create=True) / "output").mkdir()
                (storage.job_dir(jid) / "output" / "data").write_text("ok")

            with self.assertRaisesRegex(ValueError, "limit must be a positive whole number"):
                service.retention_sweep(limit=0)
            with self.assertRaisesRegex(ValueError, "retention_days must be a non-negative whole number"):
                service.retention_sweep(retention_days=-1)

            # Pass cursor explicitly to skip subs[0]
            sweep = service.retention_sweep(retention_days=7, limit=2, cursor_job_id=subs[0])
            self.assertEqual(len(sweep["cleaned"]), 2)
            self.assertEqual(sweep["cleaned"][0]["job_id"], subs[1])
            self.assertEqual(sweep["cleaned"][1]["job_id"], subs[2])

            repository.close()

    def test_retention_sweep_completed_ci_rows_do_not_starve_scan_budget(self):
        """200 completed CI rows do not starve scan budget for eligible jobs."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            # Insert 200 completed CI jobs with older timestamps and authority digest
            for i in range(200):
                row, _ = repository.accept(JobSubmission(
                    "ci", temp, "p", "local", f"ci-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-ci-{i}",
                ))
                jid = row["job_id"]
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=20, seconds=i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute(
                    "UPDATE jobs SET finished_at=?, cleanup_state='completed', "
                    "workspace_authority_digest='digest-auth' WHERE job_id=?",
                    (fin_time, jid),
                )

            # Insert 1 newer eligible job
            newer_row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "eligible-target",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-eligible-target",
            ))
            newer_jid = newer_row["job_id"]
            repository.transition(newer_jid, "running")
            repository.transition(newer_jid, "succeeded", exit_code=0)
            fin_time = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, newer_jid))
            (storage.job_dir(newer_jid, create=True) / "output").mkdir()
            (storage.job_dir(newer_jid) / "output" / "data").write_text("ok")

            # First sweep: scans up to scan budget over no-op prefix, returns truncated and cursor
            sweep1 = service.retention_sweep(retention_days=7, limit=10)
            self.assertEqual(len(sweep1["cleaned"]), 0)
            self.assertTrue(sweep1["truncated"])
            self.assertIsNotNone(sweep1["cursor"])

            # Second sweep: resumes from cursor past no-op prefix and cleans the eligible job
            sweep2 = service.retention_sweep(retention_days=7, limit=10, cursor_job_id=sweep1["cursor"])
            self.assertEqual(len(sweep2["cleaned"]), 1)
            self.assertEqual(sweep2["cleaned"][0]["job_id"], newer_jid)
            self.assertFalse(sweep2["truncated"])
            self.assertIsNone(sweep2["cursor"])

            repository.close()

    def test_retention_sweep_cross_process_keyset_continuation(self):
        """Cross-process keyset pagination resumes deterministically via returned cursor."""
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "registry.sqlite"
            repository = JobRepository(db_path)
            storage = JobStorage(temp, free_disk_reserve=0)
            now = datetime.now(timezone.utc)

            jids = []
            for i in range(5):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"page-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-page-{i}",
                ))
                jid = row["job_id"]
                jids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                fin_time = (now - timedelta(days=10, seconds=100 - i * 10)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, jid))
                (storage.job_dir(jid, create=True) / "output").mkdir()
                (storage.job_dir(jid) / "output" / "data").write_text("ok")
            repository.close()

            # Process 1: Page 1 with limit=2
            repo1 = JobRepository(db_path)
            svc1 = JobService(repo1, storage, None, launcher=lambda _desc: None)
            sweep1 = svc1.retention_sweep(retention_days=7, limit=2)
            self.assertEqual(len(sweep1["cleaned"]), 2)
            self.assertEqual([c["job_id"] for c in sweep1["cleaned"]], jids[0:2])
            self.assertTrue(sweep1["truncated"])
            cursor1 = sweep1["cursor"]
            self.assertIsNotNone(cursor1)
            repo1.close()

            # Process 2: Page 2 using cursor from Process 1
            repo2 = JobRepository(db_path)
            svc2 = JobService(repo2, storage, None, launcher=lambda _desc: None)
            sweep2 = svc2.retention_sweep(retention_days=7, limit=2, cursor_job_id=cursor1)
            self.assertEqual(len(sweep2["cleaned"]), 2)
            self.assertEqual([c["job_id"] for c in sweep2["cleaned"]], jids[2:4])
            self.assertTrue(sweep2["truncated"])
            cursor2 = sweep2["cursor"]
            self.assertIsNotNone(cursor2)
            repo2.close()

            # Process 3: Page 3 using cursor from Process 2, reaching exhaustion
            repo3 = JobRepository(db_path)
            svc3 = JobService(repo3, storage, None, launcher=lambda _desc: None)
            sweep3 = svc3.retention_sweep(retention_days=7, limit=2, cursor_job_id=cursor2)
            self.assertEqual(len(sweep3["cleaned"]), 1)
            self.assertEqual([c["job_id"] for c in sweep3["cleaned"]], [jids[4]])
            self.assertFalse(sweep3["truncated"])
            self.assertIsNone(sweep3["cursor"])
            repo3.close()

    def test_retention_sweep_safely_handles_malformed_finished_at_timestamp(self):
        """Malformed or corrupt finished_at timestamps do not crash the sweep."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            # Job 1: malformed finished_at timestamp
            row1, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "bad-ts",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-bad-ts",
            ))
            j1 = row1["job_id"]
            repository.transition(j1, "running")
            repository.transition(j1, "succeeded", exit_code=0)
            repository.connection.execute("UPDATE jobs SET finished_at='corrupt-timestamp-not-iso' WHERE job_id=?", (j1,))

            # Job 2: valid finished_at timestamp older than 7 days
            row2, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "good-ts",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-good-ts",
            ))
            j2 = row2["job_id"]
            repository.transition(j2, "running")
            repository.transition(j2, "succeeded", exit_code=0)
            fin_time = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (fin_time, j2))
            (storage.job_dir(j2, create=True) / "output").mkdir()
            (storage.job_dir(j2) / "output" / "data").write_text("ok")

            # Sweep should not raise ValueError and should clean job 2
            sweep = service.retention_sweep(retention_days=7, limit=10)
            self.assertEqual(len(sweep["cleaned"]), 1)
            self.assertEqual(sweep["cleaned"][0]["job_id"], j2)

            repository.close()

    def test_ci_release_failure_reports_truthful_failure_and_exposes_in_sweep_envelope(self):
        """CI release failure (raise or ok:False) with no files is reported truthfully and retryable."""
        for mode in ("raise", "failed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                db_path = Path(temp) / "registry.sqlite"
                repo = JobRepository(db_path)
                storage = JobStorage(temp, free_disk_reserve=0)
                reg = _CiReleaseRegistry(mode)
                sub = JobSubmission(
                    "ci", temp, "proj-ci", "local", "ci-fail",
                    ("true",), 60, SourceIdentity("source"),
                    workspace_mode="isolated", cleanup_policy="always",
                    request_id=f"req-ci-fail-{mode}",
                )
                row, _ = repo.accept(
                    sub,
                    workspace_id="ws_" + "1" * 32,
                    workspace_authority_digest="sha256:" + "1" * 64,
                )
                jid = row["job_id"]
                repo.transition(jid, "running")
                repo.transition(jid, "succeeded", exit_code=0)
                fin_time = "2020-01-01T00:00:00Z"
                repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_time, fin_time, jid))
                repo.set_cleanup_state(jid, "retained")

                service = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg)

                # 1. Direct cleanup
                cleanup_res = service.cleanup(jid)
                self.assertFalse(cleanup_res["ok"])
                self.assertEqual(cleanup_res["removed"], [])
                self.assertEqual(cleanup_res["cleanup_state"], "retained")
                self.assertEqual(repo.get(jid)["cleanup_state"], "retained")

                # 2. Retention sweep exposes failure in envelope and cleaned list
                sweep = service.retention_sweep(retention_days=0)
                self.assertFalse(sweep["ok"])
                hits = [x for x in sweep["cleaned"] if x["job_id"] == jid]
                self.assertEqual(len(hits), 1)
                self.assertFalse(hits[0]["ok"])
                self.assertEqual(hits[0]["removed"], [])
                self.assertEqual(hits[0]["cleanup_state"], "retained")
                self.assertEqual(repo.get(jid)["cleanup_state"], "retained")

                # 3. Fresh second sweep still retries release and still reports failure
                sweep2 = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg).retention_sweep(retention_days=0)
                self.assertFalse(sweep2["ok"])
                hits2 = [x for x in sweep2["cleaned"] if x["job_id"] == jid]
                self.assertEqual(len(hits2), 1)
                self.assertFalse(hits2[0]["ok"])
                self.assertEqual(reg.calls, 3)

                repo.close()

    def test_cleanup_final_materialization_check_failure_reports_removed_truthfully(self):
        """A check that fails after removal reports what was removed, retained and ok:False."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)

            class FlakyRegistry:
                calls = 0
                def has_retained_materialization(self, job):
                    self.calls += 1
                    if self.calls > 1:
                        raise RuntimeError("index unavailable")
                    return True
                def retire_terminal_materialization(self, job):
                    return True

            registry = FlakyRegistry()
            service = JobService(repository, storage, None, launcher=lambda _desc: None,
                                 workspace_registry=registry)
            row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "late-check-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-late-check",
            ))
            jid = row["job_id"]
            repository.transition(jid, "running")
            repository.transition(jid, "succeeded", exit_code=0)
            output = storage.job_dir(jid, create=True) / "output"
            output.mkdir(parents=True, exist_ok=True)
            (output / "log.txt").write_text("synthetic")

            result = service.cleanup(jid)
            self.assertFalse(result["ok"])
            self.assertEqual(result["cleanup_state"], "retained")
            self.assertEqual(result["removed"], ["logs", "workspace_materialization"])
            self.assertFalse(output.exists())
            self.assertEqual(registry.calls, 2)

            repository.close()

    def test_ci_prior_log_removal_with_subsequent_release_failure_retains_truthful_removed_and_failure(self):
        """Physical logs removed before workspace release failure preserves truthful removed and ok:False."""
        for mode in ("raise", "failed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                db_path = Path(temp) / "registry.sqlite"
                repo = JobRepository(db_path)
                storage = JobStorage(temp, free_disk_reserve=0)
                reg = _CiReleaseRegistry(mode)
                sub = JobSubmission(
                    "ci", temp, "proj-ci", "local", "ci-partial",
                    ("true",), 60, SourceIdentity("source"),
                    workspace_mode="isolated", cleanup_policy="always",
                    request_id=f"req-ci-partial-{mode}",
                )
                row, _ = repo.accept(
                    sub,
                    workspace_id="ws_" + "2" * 32,
                    workspace_authority_digest="sha256:" + "2" * 64,
                )
                jid = row["job_id"]
                repo.transition(jid, "running")
                repo.transition(jid, "succeeded", exit_code=0)
                fin_time = "2020-01-01T00:00:00Z"
                repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_time, fin_time, jid))
                repo.set_cleanup_state(jid, "retained")

                # Put physical output logs
                out_dir = storage.job_dir(jid, create=True) / "output"
                out_dir.mkdir(parents=True)
                (out_dir / "data.bin").write_bytes(b"test-logs")

                service = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg)
                res = service.cleanup(jid)
                self.assertFalse(res["ok"])
                self.assertEqual(res["removed"], ["logs"])
                self.assertEqual(res["cleanup_state"], "retained")
                self.assertFalse(out_dir.exists())
                self.assertEqual(repo.get(jid)["cleanup_state"], "retained")

                sweep = service.retention_sweep(retention_days=0)
                self.assertFalse(sweep["ok"])
                hits = [x for x in sweep["cleaned"] if x["job_id"] == jid]
                self.assertEqual(len(hits), 1)
                self.assertFalse(hits[0]["ok"])
                self.assertEqual(hits[0]["removed"], [])

                repo.close()

    def test_ci_legitimate_policy_retention_and_completed_positive_control(self):
        """Policy retention returns ok:True without workspace_cleanup; released positive control claims workspace_cleanup."""
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "registry.sqlite"
            repo = JobRepository(db_path)
            storage = JobStorage(temp, free_disk_reserve=0)

            # Policy retain
            reg_retain = _CiReleaseRegistry("retained")
            sub_retain = JobSubmission(
                "ci", temp, "proj-ci", "local", "ci-retained",
                ("true",), 60, SourceIdentity("source"),
                workspace_mode="isolated", cleanup_policy="retain",
                request_id="req-ci-retained",
            )
            row_ret, _ = repo.accept(
                sub_retain,
                workspace_id="ws_" + "3" * 32,
                workspace_authority_digest="sha256:" + "3" * 64,
            )
            j_ret = row_ret["job_id"]
            repo.transition(j_ret, "running")
            repo.transition(j_ret, "succeeded", exit_code=0)
            fin_time = "2020-01-01T00:00:00Z"
            repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_time, fin_time, j_ret))
            repo.set_cleanup_state(j_ret, "retained")

            svc_retain = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg_retain)
            res_ret = svc_retain.cleanup(j_ret)
            self.assertTrue(res_ret["ok"])
            self.assertEqual(res_ret["removed"], [])
            self.assertNotIn("workspace_cleanup", res_ret["removed"])
            self.assertEqual(res_ret["cleanup_state"], "retained")
            self.assertEqual(repo.get(j_ret)["cleanup_state"], "retained")

            sw_ret = svc_retain.retention_sweep(retention_days=0)
            self.assertTrue(sw_ret["ok"])
            self.assertEqual(sw_ret["cleaned"], [])
            self.assertEqual(reg_retain.calls, 2)

            # Released positive control
            reg_release = _CiReleaseRegistry("released")
            sub_rel = JobSubmission(
                "ci", temp, "proj-ci", "local", "ci-released",
                ("true",), 60, SourceIdentity("source"),
                workspace_mode="isolated", cleanup_policy="always",
                request_id="req-ci-released",
            )
            row_rel, _ = repo.accept(
                sub_rel,
                workspace_id="ws_" + "4" * 32,
                workspace_authority_digest="sha256:" + "4" * 64,
            )
            j_rel = row_rel["job_id"]
            repo.transition(j_rel, "running")
            repo.transition(j_rel, "succeeded", exit_code=0)
            repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_time, fin_time, j_rel))
            repo.set_cleanup_state(j_rel, "retained")

            svc_rel = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg_release)
            res_rel = svc_rel.cleanup(j_rel)
            self.assertTrue(res_rel["ok"])
            self.assertEqual(res_rel["removed"], ["workspace_cleanup"])
            self.assertEqual(res_rel["cleanup_state"], "completed")
            self.assertEqual(repo.get(j_rel)["cleanup_state"], "completed")

            repo.close()

    def test_retained_unindexed_non_ci_output_reclaimed_under_age_and_pressure_sweeps(self):
        """Retained non-CI physical output with no stream index is swept by age and pressure while active is protected."""
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "registry.sqlite"
            repo = JobRepository(db_path)
            storage = JobStorage(temp, free_disk_reserve=0)
            now = datetime.now(timezone.utc)
            reg = _CiReleaseRegistry("retained")

            # 1. Old unindexed non-CI job
            sub_old = JobSubmission(
                "test", temp, "proj-test", "local", "lbl-old",
                ("true",), 60, SourceIdentity("source"),
                request_id="req-old-unindexed",
            )
            row_old, _ = repo.accept(sub_old)
            j_old = row_old["job_id"]
            repo.transition(j_old, "running")
            repo.transition(j_old, "succeeded", exit_code=0)
            fin_old = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_old, fin_old, j_old))
            repo.set_cleanup_state(j_old, "retained")
            out_old = storage.job_dir(j_old, create=True) / "output"
            out_old.mkdir(parents=True)
            (out_old / "data.bin").write_bytes(b"old-output")

            # 2. Active running job with physical output
            sub_act = JobSubmission(
                "test", temp, "proj-test", "local", "lbl-act",
                ("true",), 60, SourceIdentity("source"),
                request_id="req-act-unindexed",
            )
            row_act, _ = repo.accept(sub_act)
            j_act = row_act["job_id"]
            repo.transition(j_act, "running")
            out_act = storage.job_dir(j_act, create=True) / "output"
            out_act.mkdir(parents=True)
            (out_act / "data.bin").write_bytes(b"active-output")

            # 3. Recent non-CI job with physical output
            sub_rec = JobSubmission(
                "test", temp, "proj-test", "local", "lbl-rec",
                ("true",), 60, SourceIdentity("source"),
                request_id="req-rec-unindexed",
            )
            row_rec, _ = repo.accept(sub_rec)
            j_rec = row_rec["job_id"]
            repo.transition(j_rec, "running")
            repo.transition(j_rec, "succeeded", exit_code=0)
            fin_rec = now.isoformat().replace("+00:00", "Z")
            repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_rec, fin_rec, j_rec))
            repo.set_cleanup_state(j_rec, "retained")
            out_rec = storage.job_dir(j_rec, create=True) / "output"
            out_rec.mkdir(parents=True)
            (out_rec / "data.bin").write_bytes(b"recent-output")

            svc = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg)

            # Active job cleanup refused
            with self.assertRaisesRegex(RuntimeError, "active_job_protected"):
                svc.cleanup(j_act)

            # Age sweep cleans only old job
            age_sweep = svc.retention_sweep(retention_days=7)
            self.assertTrue(age_sweep["ok"])
            self.assertEqual([x["job_id"] for x in age_sweep["cleaned"]], [j_old])
            self.assertEqual(age_sweep["cleaned"][0]["removed"], ["logs"])
            self.assertFalse(out_old.exists())
            self.assertEqual(repo.get(j_old)["cleanup_state"], "completed")
            self.assertTrue(out_rec.exists())
            self.assertTrue(out_act.exists())

            # Storage pressure sweep cleans recent job, still spares active job
            with patch.object(storage, "is_under_pressure", return_value=True):
                pres_sweep = svc.retention_sweep(retention_days=30, storage_pressure=True)
            self.assertTrue(pres_sweep["ok"])
            self.assertTrue(pres_sweep["storage_pressure"])
            self.assertEqual([x["job_id"] for x in pres_sweep["cleaned"]], [j_rec])
            self.assertEqual(pres_sweep["cleaned"][0]["removed"], ["logs"])
            self.assertFalse(out_rec.exists())
            self.assertEqual(repo.get(j_rec)["cleanup_state"], "completed")
            self.assertTrue(out_act.exists())
            self.assertEqual(repo.get(j_act)["lifecycle"], "running")

            # Non-CI jobs never call release_terminal_job
            self.assertEqual(reg.calls, 0)

            repo.close()

    def test_keyset_bounded_progress_with_no_ops_and_continuation(self):
        """Keyset sweep skips no-ops without count pollution, returns exact output jobs, and ends on continuation."""
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "registry.sqlite"
            repo = JobRepository(db_path)
            storage = JobStorage(temp, free_disk_reserve=0)
            reg = _CiReleaseRegistry("retained")

            noops = []
            outs = []
            for i in range(65):
                sub = JobSubmission(
                    "test", temp, "proj-test", "local", f"lbl-{i}",
                    ("true",), 60, SourceIdentity("source"),
                    request_id=f"req-progress-{i}",
                )
                row, _ = repo.accept(sub)
                jid = row["job_id"]
                repo.transition(jid, "running")
                repo.transition(jid, "succeeded", exit_code=0)
                fin_time = f"2020-01-01T{i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z"
                repo.connection.execute("UPDATE jobs SET finished_at=?, accepted_at=? WHERE job_id=?", (fin_time, fin_time, jid))
                repo.set_cleanup_state(jid, "retained")
                if i < 55:
                    noops.append(jid)
                else:
                    out = storage.job_dir(jid, create=True) / "output"
                    out.mkdir(parents=True)
                    (out / "data.bin").write_bytes(b"data")
                    outs.append(jid)

            svc = JobService(repo, storage, None, launcher=lambda _desc: None, workspace_registry=reg)

            # Page 1: limit 10 cleans exactly the 10 jobs with output
            p1 = svc.retention_sweep(retention_days=7, limit=10)
            self.assertTrue(p1["ok"])
            cleaned_ids = [x["job_id"] for x in p1["cleaned"]]
            self.assertEqual(cleaned_ids, outs)
            self.assertTrue(all(x["removed"] == ["logs"] and x["ok"] is True for x in p1["cleaned"]))
            self.assertFalse(any(j in cleaned_ids for j in noops))
            self.assertTrue(p1["truncated"])
            self.assertIsNotNone(p1["cursor"])

            # Page 2: continuation with cursor cleans nothing more
            p2 = svc.retention_sweep(retention_days=7, limit=10, cursor=p1["cursor"])
            self.assertTrue(p2["ok"])
            self.assertEqual(p2["cleaned"], [])

            # All 55 no-ops completed, all 10 outputs removed, 0 release calls
            self.assertEqual(sum(repo.get(j)["cleanup_state"] == "completed" for j in noops), 55)
            self.assertFalse(any((storage.job_dir(j) / "output").exists() for j in outs))
            self.assertEqual(reg.calls, 0)

            repo.close()

    def test_retention_sweep_progresses_past_failing_materialization_to_newer_job(self):
        """Old failing CI materialization does not abort sweep; newer eligible output is cleaned."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            class MockWorkspaceRegistry:
                def has_retained_materialization(self, job):
                    return job.get("job_id") == j1
                def retire_terminal_materialization(self, job):
                    if job.get("job_id") == j1:
                        raise RuntimeError("persistent retirement failure")
                    return True
            service = JobService(repository, storage, None, launcher=lambda _desc: None,
                               workspace_registry=MockWorkspaceRegistry())
            now = datetime.now(timezone.utc)

            # Job 1: older, persistently failing CI materialization
            row1, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "failing-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-failing-1",
            ))
            j1 = row1["job_id"]
            repository.transition(j1, "running")
            repository.transition(j1, "succeeded", exit_code=0)
            t1 = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute(
                "UPDATE jobs SET finished_at=?, kind='ci', workspace_authority_digest='sha256:fail' WHERE job_id=?",
                (t1, j1),
            )
            repository.set_cleanup_state(j1, "retained")

            # Job 2: newer, eligible job with physical output
            row2, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "eligible-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-eligible-2",
            ))
            j2 = row2["job_id"]
            repository.transition(j2, "running")
            repository.transition(j2, "succeeded", exit_code=0)
            t2 = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (t2, j2))
            out_dir = storage.job_dir(j2, create=True) / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "log.txt").write_text("eligible output")

            sweep = service.retention_sweep(retention_days=7)
            self.assertFalse(sweep["ok"])
            self.assertFalse(sweep["truncated"])
            self.assertEqual(len(sweep["cleaned"]), 2)
            # Job 1 reported ok: False, cleanup_state: retained, removed: []
            self.assertEqual(sweep["cleaned"][0]["job_id"], j1)
            self.assertFalse(sweep["cleaned"][0]["ok"])
            self.assertEqual(sweep["cleaned"][0]["cleanup_state"], "retained")
            self.assertEqual(sweep["cleaned"][0]["removed"], [])
            # Job 2 reached and cleaned
            self.assertEqual(sweep["cleaned"][1]["job_id"], j2)
            self.assertTrue(sweep["cleaned"][1]["ok"])
            self.assertIn("logs", sweep["cleaned"][1]["removed"])
            self.assertFalse(out_dir.exists())

            repository.close()

    def test_retention_sweep_limit_one_exposes_failure_with_cursor_and_continuation_reaches_newer_row(self):
        """Limit=1 stops after failing row, returns cursor; next call with cursor reaches newer row."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            class MockWorkspaceRegistry:
                def has_retained_materialization(self, job):
                    return job.get("job_id") == j1
                def retire_terminal_materialization(self, job):
                    if job.get("job_id") == j1:
                        raise RuntimeError("persistent retirement failure")
                    return True
            service = JobService(repository, storage, None, launcher=lambda _desc: None,
                               workspace_registry=MockWorkspaceRegistry())
            now = datetime.now(timezone.utc)

            row1, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "failing-job-cur",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-failing-cur",
            ))
            j1 = row1["job_id"]
            repository.transition(j1, "running")
            repository.transition(j1, "succeeded", exit_code=0)
            t1 = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            repository.connection.execute(
                "UPDATE jobs SET finished_at=?, kind='ci', workspace_authority_digest='sha256:failcur' WHERE job_id=?",
                (t1, j1),
            )
            repository.set_cleanup_state(j1, "retained")

            row2, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "eligible-job-cur",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-eligible-cur",
            ))
            j2 = row2["job_id"]
            repository.transition(j2, "running")
            repository.transition(j2, "succeeded", exit_code=0)
            t2 = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (t2, j2))
            out_dir = storage.job_dir(j2, create=True) / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "log.txt").write_text("output")

            # Page 1: limit=1 returns failing row with truncated=True and cursor
            sweep1 = service.retention_sweep(retention_days=7, limit=1)
            self.assertFalse(sweep1["ok"])
            self.assertTrue(sweep1["truncated"])
            self.assertEqual(sweep1["cursor"], j1)
            self.assertEqual(len(sweep1["cleaned"]), 1)
            self.assertEqual(sweep1["cleaned"][0]["job_id"], j1)
            self.assertFalse(sweep1["cleaned"][0]["ok"])

            # Page 2: continue with cursor; reaches Job 2
            sweep2 = service.retention_sweep(retention_days=7, limit=1, cursor=sweep1["cursor"])
            self.assertTrue(sweep2["ok"])
            self.assertEqual(len(sweep2["cleaned"]), 1)
            self.assertEqual(sweep2["cleaned"][0]["job_id"], j2)
            self.assertTrue(sweep2["cleaned"][0]["ok"])
            self.assertFalse(out_dir.exists())
            if sweep2["truncated"]:
                self.assertEqual(sweep2["cursor"], j2)
                exhausted = service.retention_sweep(
                    retention_days=7, limit=1, cursor=sweep2["cursor"])
                self.assertTrue(exhausted["ok"])
                self.assertFalse(exhausted["truncated"])
                self.assertEqual(exhausted["cleaned"], [])
                self.assertIsNone(exhausted["cursor"])

            repository.close()

    def test_retention_sweep_repeat_refusal_preserves_artifact_and_authority(self):
        """Repeated sweeps against failing retirement preserve artifact file and authority digest."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            artifact_file = Path(temp) / "retained-artifact.tar.gz"
            artifact_file.write_text("artifact payload")

            class MockWorkspaceRegistry:
                def has_retained_materialization(self, job):
                    return True
                def retire_terminal_materialization(self, job):
                    raise RuntimeError("broker refusal: checksum drift")

            service = JobService(repository, storage, None, launcher=lambda _desc: None,
                               workspace_registry=MockWorkspaceRegistry())
            now = datetime.now(timezone.utc)

            row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "protected-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-protected",
            ))
            jid = row["job_id"]
            repository.transition(jid, "running")
            repository.transition(jid, "succeeded", exit_code=0)
            t = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            authority_digest = "sha256:preservedauthority1234567890"
            repository.connection.execute(
                "UPDATE jobs SET finished_at=?, kind='ci', workspace_authority_digest=? WHERE job_id=?",
                (t, authority_digest, jid),
            )
            repository.set_cleanup_state(jid, "retained")

            # Sweep 1
            s1 = service.retention_sweep(retention_days=7)
            self.assertFalse(s1["ok"])
            self.assertTrue(artifact_file.exists())
            r1 = repository.get(jid)
            self.assertEqual(r1["workspace_authority_digest"], authority_digest)
            self.assertEqual(r1["cleanup_state"], "retained")

            # Sweep 2
            s2 = service.retention_sweep(retention_days=7)
            self.assertFalse(s2["ok"])
            self.assertTrue(artifact_file.exists())
            r2 = repository.get(jid)
            self.assertEqual(r2["workspace_authority_digest"], authority_digest)
            self.assertEqual(r2["cleanup_state"], "retained")

            repository.close()

    def test_retention_sweep_storage_pressure_with_cursor_pagination(self):
        """Storage pressure sweep paginates correctly with cursor across terminal jobs."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            class PressureStorage:
                def __init__(self, root):
                    self.root = Path(root)
                    self.pressure = True
                def is_under_pressure(self):
                    return self.pressure
                def job_dir(self, job_id, create=False):
                    d = self.root / job_id
                    if create:
                        d.mkdir(parents=True, exist_ok=True)
                    return d

            storage = PressureStorage(temp)
            service = JobService(repository, storage, None, launcher=lambda _desc: None)
            now = datetime.now(timezone.utc)

            jids = []
            for i in range(3):
                row, _ = repository.accept(JobSubmission(
                    "test", temp, "p", "local", f"press-{i}",
                    ("echo", "ok"), 60, SourceIdentity("source"),
                    request_id=f"req-press-{i}",
                ))
                jid = row["job_id"]
                jids.append(jid)
                repository.transition(jid, "running")
                repository.transition(jid, "succeeded", exit_code=0)
                t = (now - timedelta(days=20 - i)).isoformat().replace("+00:00", "Z")
                repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (t, jid))
                out = storage.job_dir(jid, create=True) / "output"
                out.mkdir(parents=True, exist_ok=True)
                (out / "log.txt").write_text("data")

            # Page 1 under pressure: limit=1
            p1 = service.retention_sweep(retention_days=30, limit=1, storage_pressure=True)
            self.assertTrue(p1["ok"])
            self.assertTrue(p1["truncated"])
            self.assertEqual(p1["cursor"], jids[0])
            self.assertEqual(len(p1["cleaned"]), 1)
            self.assertEqual(p1["cleaned"][0]["job_id"], jids[0])

            # Pressure clears before page 2
            storage.pressure = False
            p2 = service.retention_sweep(retention_days=30, limit=10, storage_pressure=True, cursor=p1["cursor"])
            # Pressure cleared: storage_pressure becomes False, jobs finished 18-19 days ago not older than 30 days
            self.assertTrue(p2["ok"])
            self.assertFalse(p2["storage_pressure"])
            self.assertEqual(len(p2["cleaned"]), 0)

            repository.close()

    def test_retention_sweep_has_retained_materialization_raising_does_not_abort_page(self):
        """has_retained_materialization error is caught per-row and does not abort page."""
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            class MockWorkspaceRegistry:
                def has_retained_materialization(self, job):
                    if job.get("job_id") == j1:
                        raise RuntimeError("corrupt workspace projection")
                    return False
            service = JobService(repository, storage, None, launcher=lambda _desc: None,
                               workspace_registry=MockWorkspaceRegistry())
            now = datetime.now(timezone.utc)

            row1, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "bad-obs-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-bad-obs",
            ))
            j1 = row1["job_id"]
            repository.transition(j1, "running")
            repository.transition(j1, "succeeded", exit_code=0)
            t1 = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
            # A completed CI row with authority stays a candidate; a failed
            # check must still report it retained, not its stored state.
            repository.connection.execute(
                "UPDATE jobs SET finished_at=?, kind='ci', cleanup_state='completed', "
                "workspace_authority_digest='sha256:' || hex(randomblob(32)) WHERE job_id=?",
                (t1, j1))

            row2, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "good-job",
                ("echo", "ok"), 60, SourceIdentity("source"),
                request_id="req-good",
            ))
            j2 = row2["job_id"]
            repository.transition(j2, "running")
            repository.transition(j2, "succeeded", exit_code=0)
            t2 = (now - timedelta(days=8)).isoformat().replace("+00:00", "Z")
            repository.connection.execute("UPDATE jobs SET finished_at=? WHERE job_id=?", (t2, j2))
            out_dir = storage.job_dir(j2, create=True) / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "log.txt").write_text("output")

            sweep = service.retention_sweep(retention_days=7)
            self.assertFalse(sweep["ok"])
            self.assertEqual(len(sweep["cleaned"]), 2)
            self.assertEqual(sweep["cleaned"][0]["job_id"], j1)
            self.assertFalse(sweep["cleaned"][0]["ok"])
            self.assertEqual(sweep["cleaned"][0]["cleanup_state"], "retained")
            self.assertEqual(sweep["cleaned"][0]["removed"], [])
            self.assertEqual(sweep["cleaned"][1]["job_id"], j2)
            self.assertTrue(sweep["cleaned"][1]["ok"])
            self.assertFalse(out_dir.exists())

            repository.close()
