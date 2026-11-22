"""A nested job runs on its waiting parent's host slot (no slot deadlock)."""
import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, Lifecycle, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.jobs.storage import JobStorage


class NestedSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.addCleanup(self.repo.close)
        self.scheduler = JobScheduler(self.repo, max_parallel=1, min_free_memory_mb=0,
                                      min_free_disk_mb=0)

    def accept(self, label, parent=None, request=None):
        return self.repo.accept(JobSubmission(
            "test", "/p", "p", "local", label, ("echo", "x"), 60, SourceIdentity("s"),
            parent_job_id=parent, request_id=request))[0]

    def running_parent(self, label="outer"):
        parent = self.accept(label)
        self.scheduler.acquire(parent)
        self.repo.transition(parent["job_id"], Lifecycle.RUNNING)
        return parent

    def test_the_child_of_a_running_slot_holder_runs_on_its_slot(self):
        parent = self.running_parent()
        child = self.accept("inner", parent["job_id"])
        self.scheduler.acquire(child)  # max_parallel=1 is already used by the parent
        self.assertTrue(self.scheduler.is_admitted(child["job_id"]))
        self.assertFalse(self.scheduler.holds_slot(child["job_id"]))
        self.assertEqual(self.scheduler.free_slots(), 0)

    def test_a_chain_of_nested_jobs_stays_on_one_slot(self):
        parent = self.running_parent()
        child = self.accept("inner", parent["job_id"])
        self.scheduler.acquire(child)
        self.repo.transition(child["job_id"], Lifecycle.RUNNING)
        grandchild = self.accept("deeper", child["job_id"])
        self.scheduler.acquire(grandchild)
        self.assertFalse(self.scheduler.holds_slot(grandchild["job_id"]))

    def test_a_second_parallel_child_needs_its_own_slot(self):
        parent = self.running_parent()
        first = self.accept("inner-1", parent["job_id"])
        second = self.accept("inner-2", parent["job_id"])
        self.scheduler.acquire(first)
        with self.assertRaises(WorkspaceBusy) as raised:
            self.scheduler.acquire(second)
        self.assertEqual(raised.exception.kind, "capacity")

    def test_unrelated_and_orphan_jobs_still_need_a_slot(self):
        self.running_parent()
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(self.accept("other"))
        queued_parent = self.accept("queued-parent")
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(self.accept("child-of-queued", queued_parent["job_id"]))

    def test_the_drain_admits_a_queued_child_past_older_slot_waiters(self):
        launched = []
        service = JobService(self.repo, JobStorage(self.temp.name, free_disk_reserve=0), None,
                             launcher=launched.append, scheduler=self.scheduler)
        parent = self.running_parent()
        waiting = service.submit(JobSubmission(
            "test", self.temp.name, "p", "local", "other", ("echo", "x"), 60, SourceIdentity("s")))
        self.assertEqual(self.repo.get(waiting["job_id"])["lifecycle"], "queued")
        child = service.submit(JobSubmission(
            "test", self.temp.name, "p", "local", "inner", ("echo", "x"), 60, SourceIdentity("s"),
            parent_job_id=parent["job_id"]))
        # Admitted at submission despite the full host and the older queued job.
        self.assertTrue(self.scheduler.is_admitted(child["job_id"]))
        self.assertEqual(self.repo.get(waiting["job_id"])["lifecycle"], "queued")

    def test_the_drain_skips_slot_waiters_to_start_a_borrowing_child(self):
        launched = []
        service = JobService(self.repo, JobStorage(self.temp.name, free_disk_reserve=0), None,
                             launcher=launched.append, scheduler=self.scheduler)
        parent = self.accept("outer")
        self.scheduler.acquire(parent)
        waiting = service.submit(JobSubmission(
            "test", self.temp.name, "p", "local", "other", ("echo", "x"), 60, SourceIdentity("s")))
        child = service.submit(JobSubmission(
            "test", self.temp.name, "p", "local", "inner", ("echo", "x"), 60, SourceIdentity("s"),
            parent_job_id=parent["job_id"]))
        self.assertEqual(self.repo.get(child["job_id"])["lifecycle"], "queued")  # parent not running yet
        self.repo.transition(parent["job_id"], Lifecycle.RUNNING)
        changed = service.drain_queue()
        self.assertIn(child["job_id"], changed)
        self.assertTrue(self.scheduler.is_admitted(child["job_id"]))
        self.assertEqual(self.repo.get(waiting["job_id"])["lifecycle"], "queued")

    def finish(self, job):
        self.repo.transition(job["job_id"], Lifecycle.SUCCEEDED)
        self.repo.release_leases(job["job_id"])

    def held(self):
        return {row[0] for row in self.repo.connection.execute(
            "SELECT job_id FROM host_capacity_leases").fetchall()}

    def test_a_parent_that_ends_first_hands_its_slot_to_the_child(self):
        parent = self.running_parent()
        child = self.accept("inner", parent["job_id"])
        self.scheduler.acquire(child)
        self.repo.transition(child["job_id"], Lifecycle.RUNNING)
        self.finish(parent)
        self.assertEqual(self.held(), {child["job_id"]})
        self.assertEqual(self.scheduler.free_slots(), 0)
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(self.accept("other"))
        self.finish(child)
        self.assertEqual(self.held(), set())

    def test_repeated_detached_parents_never_exceed_the_limit(self):
        for index in range(5):
            if self.held():
                with self.assertRaises(WorkspaceBusy):
                    self.scheduler.acquire(self.accept(f"outer-{index}"))
                continue
            parent = self.running_parent(f"outer-{index}")
            child = self.accept(f"inner-{index}", parent["job_id"])
            self.scheduler.acquire(child)
            self.repo.transition(child["job_id"], Lifecycle.RUNNING)
            self.finish(parent)
        running = self.repo.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE lifecycle='running'").fetchone()[0]
        self.assertEqual(running, 1)

    def test_a_grandchild_is_covered_after_its_parent_and_then_the_root_end(self):
        root = self.running_parent()
        child = self.accept("inner", root["job_id"])
        self.scheduler.acquire(child)
        self.repo.transition(child["job_id"], Lifecycle.RUNNING)
        grandchild = self.accept("deeper", child["job_id"])
        self.scheduler.acquire(grandchild)
        self.repo.transition(grandchild["job_id"], Lifecycle.RUNNING)
        self.finish(child)
        self.assertEqual(self.held(), {root["job_id"]})  # the root still covers it
        self.finish(root)
        self.assertEqual(self.held(), {grandchild["job_id"]})

    def test_a_cancelling_parent_lends_nothing(self):
        parent = self.running_parent()
        self.repo.transition(parent["job_id"], Lifecycle.CANCELLING)
        with self.assertRaises(WorkspaceBusy):
            self.scheduler.acquire(self.accept("inner", parent["job_id"]))

    def test_a_borrowing_child_counts_as_admitted_for_launch_recovery(self):
        from sandbox.jobs.launch_recovery import slot_held
        parent = self.running_parent()
        child = self.accept("inner", parent["job_id"])
        self.scheduler.acquire(child)
        self.assertTrue(slot_held(self.repo, child["job_id"]))


if __name__ == "__main__":
    unittest.main()
