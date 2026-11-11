"""Oldest-first durable-job admission (spec 083)."""

import json
import multiprocessing
import os
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sandbox.application.job_service import JobService
from sandbox.config.job_scheduler import JobSchedulerConfigError, JobSchedulerPolicy
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.process import ProcessIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.jobs.storage import JobStorage

BOOT = "11111111-2222-3333-4444-555555555555"
OTHER_BOOT = "22222222-3333-4444-5555-666666666666"


class Host:
    """One host: a registry, a scheduler with floors off and a recording launcher."""

    def __init__(self, root, *, slots=1, registry=None, launch_log=None):
        self.root = str(root)
        self.repo = JobRepository(Path(registry or Path(root) / "jobs.sqlite"))
        self.scheduler = JobScheduler(self.repo, max_parallel=slots,
                                      min_free_memory_mb=0, min_free_disk_mb=0)
        self.launched = []
        self.launch_log = launch_log
        self.fail_launch = set()
        self.service = JobService(self.repo, JobStorage(self.root, free_disk_reserve=0), None,
                                  launcher=self._launch, scheduler=self.scheduler)

    def _launch(self, descriptor_path):
        job_id = json.loads(Path(descriptor_path).read_text())["job_id"]
        if job_id in self.fail_launch:
            raise OSError("launch failed")
        self.launched.append(job_id)
        if self.launch_log:
            with open(self.launch_log, "a") as handle:
                handle.write(job_id + "\n")

    def submit(self, label, *, project="p", **extra):
        row = self.service.submit(JobSubmission(
            "test", self.root, project, "local", label, ("echo", "x"), 60,
            SourceIdentity("s"), **extra))
        return row["job_id"]

    def free_without_drain(self, job_id):
        """Free a slot the way a crashed reader would: no drain runs."""
        self.repo.transition(job_id, "cancelled", termination_reason="cancelled_by_request")
        self.scheduler.release(job_id)

    def state(self, job_id):
        row = self.repo.get(job_id)
        return row["lifecycle"], row["queue_reason"]

    def held(self):
        return self.repo.connection.execute("SELECT COUNT(*) FROM host_capacity_leases").fetchone()[0]

    def set_accepted_at(self, job_id, stamp):
        self.repo.connection.execute("UPDATE jobs SET accepted_at=? WHERE job_id=?", (stamp, job_id))
        self.repo.connection.commit()

    def close(self):
        self.repo.close()


class HostCase(unittest.TestCase):
    slots = 1

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = Host(self.temp.name, slots=self.slots)

    def tearDown(self):
        self.host.close()
        self.temp.cleanup()


class OldestFirstTests(HostCase):
    def test_a_freed_slot_goes_to_the_oldest_queued_job(self):
        holder = self.host.submit("w0")
        older, younger = self.host.submit("wa"), self.host.submit("wb")
        self.host.service.cancel(holder)
        self.assertEqual(self.host.launched, [holder, older])
        self.assertEqual(self.host.state(younger)[0], "queued")

    def test_a_younger_poller_starts_the_older_job_not_itself(self):
        holder = self.host.submit("w0")
        older, younger = self.host.submit("wa"), self.host.submit("wb")
        self.host.free_without_drain(holder)
        snapshot = self.host.service.get(younger)
        self.assertEqual(self.host.launched, [holder, older])
        self.assertEqual(snapshot["lifecycle"], "queued")
        self.assertEqual(snapshot["queue"]["position"], 1)

    def test_the_order_gate_refuses_a_direct_admission_past_an_older_job(self):
        holder = self.host.submit("w0")
        self.host.submit("wa")
        younger = self.host.submit("wb")
        self.host.free_without_drain(holder)
        with self.assertRaises(WorkspaceBusy) as refused:
            self.host.scheduler.acquire(self.host.repo.get(younger))
        self.assertEqual(refused.exception.kind, "order")

    def test_equal_acceptance_times_order_by_job_id(self):
        holder = self.host.submit("w0")
        first, second = self.host.submit("wa"), self.host.submit("wb")
        self.host.set_accepted_at(first, "2026-11-11T00:00:00Z")
        self.host.set_accepted_at(second, "2026-11-11T00:00:00Z")
        self.host.service.cancel(holder)
        self.assertEqual(self.host.launched[-1], min(first, second))

    def test_replay_of_the_2026_11_10_pattern_has_no_out_of_order_start(self):
        self.host.scheduler.max_parallel = 2
        holders = [self.host.submit("h1"), self.host.submit("h2")]
        older = [self.host.submit(f"o{index}") for index in range(4)]
        younger = [self.host.submit(f"y{index}") for index in range(3)]
        running = list(holders)
        while any(self.host.state(job)[0] == "queued" for job in older + younger):
            self.host.free_without_drain(running.pop(0))
            # Only the younger jobs' owners poll, often.
            for job in younger:
                self.host.service.get(job)
            running = [job for job in self.host.launched if self.host.state(job)[0] != "cancelled"]
        self.assertEqual(self.host.launched[2:], older + younger)


class TwoSlotTests(HostCase):
    slots = 2

    def test_two_free_slots_start_the_two_oldest(self):
        holders = [self.host.submit("h1"), self.host.submit("h2")]
        queued = [self.host.submit(f"q{index}") for index in range(3)]
        for job in holders:
            self.host.free_without_drain(job)
        self.host.service.get(queued[2])
        self.assertEqual(self.host.launched[2:], queued[:2])
        self.assertEqual(self.host.state(queued[2])[0], "queued")


class DrainTriggerTests(HostCase):
    def test_reconcile_startup_drains(self):
        holder = self.host.submit("w0")
        waiting = self.host.submit("wa")
        self.host.free_without_drain(holder)
        self.host.service.reconcile_startup()
        self.assertEqual(self.host.launched, [holder, waiting])

    def test_a_terminal_status_read_drains(self):
        holder = self.host.submit("w0")
        waiting = self.host.submit("wa")
        self.host.repo.transition(holder, "cancelled", termination_reason="cancelled_by_request")
        self.host.service.get(holder)  # releases the terminal job's slot, then drains
        self.assertEqual(self.host.launched, [holder, waiting])

    def test_no_free_slot_touches_nothing(self):
        self.host.submit("w0")
        queued = [self.host.submit("wa"), self.host.submit("wb")]
        before = [self.host.repo.get(job) for job in queued]
        self.assertEqual(self.host.service.drain_queue(), [])
        self.assertEqual([self.host.repo.get(job) for job in queued], before)
        self.assertEqual(len(self.host.launched), 1)

    def test_a_drain_reentered_from_a_launch_is_a_no_op_and_the_outer_one_finishes(self):
        holder = self.host.submit("w0")
        first, second = self.host.submit("wa"), self.host.submit("wb")
        self.host.scheduler.max_parallel = 3
        self.host.free_without_drain(holder)
        nested = []
        launch = self.host._launch

        def reentering_launch(descriptor_path):
            launch(descriptor_path)
            nested.append(self.host.service.drain_queue())

        self.host.service.launcher = reentering_launch
        self.assertEqual(self.host.service.drain_queue(), [first, second])
        self.assertEqual(nested, [[], []])
        self.assertEqual(self.host.launched, [holder, first, second])

    def test_a_drain_pass_ends_at_the_newest_job_queued_when_it_started(self):
        self.host.submit("w0")
        self.host.scheduler.max_parallel = 10
        blocked = [self.host.submit("w0") for _ in range(3)]  # own workspace busy
        visits, arriving = [], []
        original = self.host.service.get

        def get_while_jobs_arrive(job_id, **kwargs):
            if not arriving:
                visits.append(job_id)
                if len(visits) < 30:
                    arriving.append(True)
                    try:
                        self.host.submit("w0")
                    finally:
                        arriving.clear()
            return original(job_id, **kwargs)

        with mock.patch.object(self.host.service, "get", get_while_jobs_arrive):
            self.host.service.drain_queue()
        self.assertEqual(visits, blocked)

    def test_a_capacity_refusal_does_not_stop_a_later_drain_at_a_dependency_wait(self):
        blocker = self.host.repo.accept(JobSubmission(
            "test", self.host.root, "p", "local", "dep", ("echo", "x"), 60, SourceIdentity("s")))[0]
        self.host.repo.transition(blocker["job_id"], "running")
        holder = self.host.submit("w0")
        dependent = self.host.submit("wd", depends_on=(blocker["job_id"],))
        ready = self.host.submit("wr")
        self.host.service.get(ready)  # this drain stops on capacity
        self.host.free_without_drain(holder)
        self.host.service.drain_queue()  # first visit returns at the dependency wait
        self.assertEqual(self.host.state(dependent)[0], "queued")
        self.assertEqual(self.host.launched, [holder, ready])

    def test_an_interrupted_drain_leaves_valid_state_and_the_next_one_finishes(self):
        holder = self.host.submit("w0")
        first, second = self.host.submit("wa"), self.host.submit("wb")
        self.host.scheduler.max_parallel = 2
        self.host.free_without_drain(holder)
        calls = []
        original = self.host.service.get

        def failing_get(job_id, **kwargs):
            calls.append(job_id)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return original(job_id, **kwargs)

        with mock.patch.object(self.host.service, "get", failing_get):
            with self.assertRaises(KeyboardInterrupt):
                self.host.service.drain_queue()
        self.assertFalse(getattr(self.host.service._drain_local, "active", False))
        self.host.service.drain_queue()
        self.assertEqual(self.host.launched, [holder, first, second])


class AdmittedGuardTests(HostCase):
    def test_a_second_admission_of_the_same_job_is_refused_and_keeps_its_leases(self):
        row = self.host.repo.accept(JobSubmission(
            "test", self.host.root, "p", "local", "w", ("echo", "x"), 60,
            SourceIdentity("s"), parallel_safe=True))[0]
        self.host.scheduler.acquire(row, parallel_safe=True)
        with self.assertRaises(WorkspaceBusy) as refused:
            self.host.scheduler.acquire(row, parallel_safe=True)
        self.assertEqual(refused.exception.kind, "admitted")
        self.assertEqual(self.host.held(), 1)

    def test_a_queued_job_already_admitted_is_left_alone_by_a_later_read(self):
        holder = self.host.submit("w0", parallel_safe=True)
        waiting = self.host.submit("w1", parallel_safe=True)
        self.host.free_without_drain(holder)
        self.host.service.get(waiting)        # admitted; lifecycle stays queued until its supervisor runs
        self.host.service.get(waiting)        # a second reader
        self.assertEqual(self.host.launched.count(waiting), 1)
        self.assertEqual(self.host.state(waiting)[0], "queued")
        self.assertEqual(self.host.held(), 1)


class CancelRaceTests(HostCase):
    def test_a_cancel_that_read_the_job_queued_never_cancels_it_once_it_runs(self):
        job = self.host.submit("w0")  # admitted; the fake launcher leaves it queued
        stale = self.host.repo.snapshot(job)
        self.host.repo.transition(job, "running")  # its supervisor won meanwhile
        reads = iter([stale])
        original = self.host.repo.snapshot

        def first_read_is_stale(job_id):
            return next(reads, None) or original(job_id)

        with mock.patch.object(self.host.repo, "snapshot", first_read_is_stale):
            with self.assertRaises(RuntimeError) as refused:
                self.host.service.cancel(job)
        self.assertEqual(str(refused.exception), "process_identity_mismatch")
        self.assertEqual(self.host.state(job)[0], "running")
        self.assertTrue(self.host.scheduler.holds_slot(job))


class BlockedOlderJobTests(HostCase):
    slots = 2

    def test_an_older_job_on_a_busy_workspace_does_not_block(self):
        self.host.submit("w")
        older = self.host.submit("w")
        younger = self.host.submit("x")
        self.assertIn(younger, self.host.launched)
        self.assertEqual(self.host.state(older), ("queued", "workspace_or_capacity_busy"))

    def test_a_same_workspace_pair_starts_the_older(self):
        self.host.scheduler.max_parallel = 1
        holder = self.host.submit("w")
        older, younger = self.host.submit("w"), self.host.submit("w")
        self.host.service.cancel(holder)
        self.assertEqual(self.host.launched, [holder, older])
        self.assertEqual(self.host.state(younger)[0], "queued")

    def test_an_older_parallel_safe_job_that_can_share_blocks_a_younger_one(self):
        self.host.submit("w", parallel_safe=True)
        other = self.host.submit("x")
        older = self.host.submit("w", parallel_safe=True)
        younger = self.host.submit("y")
        self.host.free_without_drain(other)
        self.host.service.get(younger)
        self.assertEqual(self.host.launched[-1], older)
        self.assertEqual(self.host.state(younger)[0], "queued")

    def test_an_older_job_waiting_on_a_dependency_does_not_block(self):
        blocker = self.host.repo.accept(JobSubmission(
            "test", self.host.root, "p", "local", "dep", ("echo", "x"), 60, SourceIdentity("s")))[0]
        self.host.repo.transition(blocker["job_id"], "running")
        self.host.submit("h1")
        holder = self.host.submit("h2")
        dependent = self.host.submit("d", depends_on=(blocker["job_id"],))
        younger = self.host.submit("y")
        self.host.free_without_drain(holder)
        self.host.service.get(younger)
        self.assertIn(younger, self.host.launched)
        self.assertEqual(self.host.state(dependent)[0], "queued")

    def test_an_aggregate_parent_never_blocks(self):
        self.host.submit("h1")
        holder = self.host.submit("h2")
        parent = self.host.repo.accept(JobSubmission(
            "matrix", self.host.root, "p", "local", "m", ("echo", "x"), 60, SourceIdentity("s")))[0]
        self.host.repo.transition(parent["job_id"], "queued", queue_reason="workspace_or_capacity_busy")
        younger = self.host.submit("y")
        self.host.free_without_drain(holder)
        self.host.scheduler.acquire(self.host.repo.get(younger))
        self.assertEqual(self.host.held(), 2)

    def test_many_blocked_older_jobs_do_not_hide_a_startable_one(self):
        self.host.submit("w")
        holder = self.host.submit("v")
        for _ in range(60):
            self.host.submit("w")
        startable = self.host.submit("x")
        self.host.free_without_drain(holder)
        with mock.patch("sandbox.application.job_service.DRAIN_PAGE_SIZE", 7):
            self.host.service.drain_queue()
        self.assertIn(startable, self.host.launched)


class PositionTests(HostCase):
    def test_positions_are_host_wide_in_acceptance_order(self):
        self.host.submit("h")
        jobs = [self.host.submit("a", project="p1"), self.host.submit("b", project="p2"),
                self.host.submit("c", project="p1")]
        positions = [self.host.service.get(job)["queue"]["position"] for job in jobs]
        self.assertEqual(positions, [1, 2, 3])

    def test_an_older_job_blocked_on_its_workspace_is_not_counted(self):
        self.host.scheduler.max_parallel = 2
        self.host.submit("w")
        self.host.submit("h")
        self.host.submit("w")                # older, waiting on its workspace
        younger = self.host.submit("x")      # waiting for a slot
        self.assertEqual(self.host.service.get(younger)["queue"]["position"], 1)


class ProvedGoneTests(HostCase):
    def _running_with_slot(self):
        row = self.host.repo.accept(JobSubmission(
            "test", self.host.root, "p", "local", "r", ("echo", "x"), 60, SourceIdentity("s")))[0]
        self.host.scheduler.acquire(row)
        self.host.repo.transition(row["job_id"], "running")
        self.host.repo.put_process_identity(row["job_id"], host_boot_id=BOOT, supervisor_pid=101,
            supervisor_start_identity="start", supervisor_nonce_hash="nonce")
        return row["job_id"]

    def test_a_proved_gone_supervisor_frees_its_slot_for_the_next_job(self):
        running = self._running_with_slot()
        waiting = self.host.submit("q")
        lost = ProcessIdentity(OTHER_BOOT, 101, "start", "nonce")
        with mock.patch("sandbox.application.job_service.capture_process_identity", return_value=lost):
            self.host.service.reconcile_startup()
        self.assertEqual(self.host.repo.get(running)["termination_reason"], "supervisor_lost")
        self.assertEqual(self.host.launched, [waiting])

    def test_a_verified_supervisor_keeps_its_slot(self):
        running = self._running_with_slot()
        waiting = self.host.submit("q")
        alive = ProcessIdentity(BOOT, 101, "start", "nonce")
        with mock.patch("sandbox.application.job_service.capture_process_identity", return_value=alive):
            self.host.service.reconcile_startup()
        self.assertEqual(self.host.repo.get(running)["lifecycle"], "running")
        self.assertEqual(self.host.launched, [])
        self.assertEqual(self.host.state(waiting)[0], "queued")


class EdgeCaseTests(HostCase):
    def test_a_launch_failure_fails_that_job_and_the_drain_continues(self):
        holder = self.host.submit("w0")
        broken, healthy = self.host.submit("wa"), self.host.submit("wb")
        self.host.fail_launch.add(broken)
        self.host.free_without_drain(holder)
        self.host.service.drain_queue()
        self.assertEqual(self.host.repo.get(broken)["termination_reason"], "supervisor_launch_failed")
        self.assertEqual(self.host.launched, [holder, healthy])

    def test_a_floor_refusal_admits_nothing(self):
        holder = self.host.submit("w0")
        waiting = [self.host.submit("wa"), self.host.submit("wb")]
        self.host.free_without_drain(holder)
        self.host.scheduler._resource_floor_available = lambda: False
        self.host.service.drain_queue()
        self.assertEqual(self.host.launched, [holder])
        self.assertEqual([self.host.state(job)[0] for job in waiting], ["queued", "queued"])

    def test_a_lowered_limit_admits_nothing_until_enough_jobs_end(self):
        self.host.scheduler.max_parallel = 2
        first, second = self.host.submit("w0"), self.host.submit("w1")
        waiting = self.host.submit("wa")
        self.host.scheduler.max_parallel = 1
        self.host.free_without_drain(first)
        self.host.service.drain_queue()
        self.assertEqual(self.host.state(waiting)[0], "queued")
        self.host.free_without_drain(second)
        self.host.service.drain_queue()
        self.assertIn(waiting, self.host.launched)

    def test_an_invalid_policy_records_its_code_as_the_reason(self):
        holder = self.host.submit("w0")
        waiting = self.host.submit("wa")
        self.host.free_without_drain(holder)

        def invalid():
            raise JobSchedulerConfigError("bad", "job_policy_invalid_floor")

        self.host.scheduler.policy_loader = invalid
        self.host.service.drain_queue()
        self.assertEqual(self.host.state(waiting), ("queued", "job_policy_invalid_floor"))
        self.host.scheduler.policy_loader = lambda: JobSchedulerPolicy(1, 512, 2048)
        self.host.service.drain_queue()
        self.assertIn(waiting, self.host.launched)

    def test_a_waiting_job_keeps_the_existing_reason_value(self):
        self.host.submit("w0")
        waiting = self.host.submit("wa")
        self.assertEqual(self.host.state(waiting), ("queued", "workspace_or_capacity_busy"))


class DrainAfterReleaseTests(unittest.TestCase):
    def test_a_temporary_registry_never_touches_the_host_registry(self):
        from sandbox.application import context
        with mock.patch.object(context, "durable_job_dependencies",
                               side_effect=AssertionError("must not build host services")):
            self.assertFalse(context.drain_after_release(Path(tempfile.gettempdir()) / "x.sqlite3"))

    def test_the_host_registry_drains_and_errors_are_suppressed(self):
        from sandbox.application import context
        from sandbox.core._paths import RUNTIME_DIR
        dependencies = {"job_service": mock.Mock()}
        with mock.patch.object(context, "durable_job_dependencies",
                               return_value=dependencies) as build:
            self.assertTrue(context.drain_after_release(RUNTIME_DIR / "jobs" / "registry.sqlite3"))
        build.assert_called_once()
        dependencies["job_service"].repository.close.assert_called_once()
        with mock.patch.object(context, "durable_job_dependencies", side_effect=RuntimeError("x")):
            self.assertFalse(context.drain_after_release(RUNTIME_DIR / "jobs" / "registry.sqlite3"))

    def test_an_admission_tick_reconciles_through_fresh_services_and_closes_them(self):
        from sandbox.application import context
        dependencies = {"job_service": mock.Mock()}
        with mock.patch.object(context, "durable_job_dependencies",
                               return_value=dependencies) as build:
            context.run_admission_tick()
        build.assert_called_once()
        dependencies["job_service"].repository.close.assert_called_once()


def _interleave_worker(registry, root, seed, operations, launch_log, violations, limit):
    """One OS process of the concurrency check; writes invariant breaks to a file."""
    host = Host(root, slots=limit, registry=registry, launch_log=launch_log)
    rng = random.Random(seed)
    try:
        for _ in range(operations):
            choice = rng.random()
            try:
                if choice < 0.35:
                    host.submit(rng.choice(("w1", "w2", "w3", "w4")), project=rng.choice(("p1", "p2")),
                                parallel_safe=rng.random() < 0.3)
                else:
                    rows = host.repo.connection.execute(
                        "SELECT job_id, lifecycle FROM jobs WHERE lifecycle IN ('accepted','queued')"
                    ).fetchall()
                    if rows:
                        job_id = rng.choice(rows)[0]
                        if choice < 0.6:
                            host.service.get(job_id)
                        elif choice < 0.8:
                            host.service.cancel(job_id)
                        else:
                            host.service.drain_queue()
            except (RuntimeError, ValueError, sqlite3.OperationalError):
                pass  # already terminal, or a busy database under contention
            held = host.held()
            if held > limit:
                with open(violations, "a") as handle:
                    handle.write(f"held {held} > {limit}\n")
    finally:
        host.close()


class ConcurrencyTests(unittest.TestCase):
    def test_two_processes_never_start_a_job_twice_or_exceed_the_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = os.path.join(temp, "jobs.sqlite")
            Host(temp, registry=registry).close()  # create the schema once
            launch_log = os.path.join(temp, "launched.log")
            violations = os.path.join(temp, "violations.log")
            context = multiprocessing.get_context("spawn")
            workers = [context.Process(target=_interleave_worker, args=(
                registry, temp, seed, 100, launch_log, violations, 2)) for seed in (1, 2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(240)
                self.assertEqual(worker.exitcode, 0)
            launched = Path(launch_log).read_text().split() if os.path.exists(launch_log) else []
            self.assertTrue(launched, "the interleaving admitted no job at all")
            self.assertEqual(len(launched), len(set(launched)), "a job was started twice")
            self.assertFalse(os.path.exists(violations), Path(violations).read_text()
                             if os.path.exists(violations) else "")


if __name__ == "__main__":
    unittest.main()
