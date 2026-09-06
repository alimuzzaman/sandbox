import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import WorkspaceBusy
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.supervisor import run_descriptor
from sandbox.sync.models import SynchronizationRelationship
from sandbox.sync.projection import SyncJobGateway
from sandbox.sync.repository import SyncRepository


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
                row["job_id"], owner_boot_id="dead-boot",
                owner_pid=99999999, owner_start_identity="dead-start",
            )
            jobs.commit_pending_sync_launch(
                row["job_id"], owner_boot_id="dead-boot",
                owner_pid=99999999, owner_start_identity="dead-start",
            )
            gateway = MagicMock()
            gateway.release_terminal_jobs.return_value = ()
            service = JobService(
                jobs, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _path: None, sync_gateway=gateway,
            )
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
