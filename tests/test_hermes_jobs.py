import unittest

from sandbox.hermes.jobs import HermesJobService
from tests.fakes.hermes import RecordingJobBackend


class TestHermesJobs(unittest.TestCase):
    def test_status_cancel_and_cleanup_delegate_to_an_injected_backend(self):
        backend = RecordingJobBackend(statuses={"job-1": {"job_id": "job-1", "status": "running"}})
        service = HermesJobService(backend)

        self.assertEqual(service.status("remote", "job-1", offset=3)["status"], "running")
        self.assertEqual(service.cancel("remote", "job-1")["status"], "cancelled")
        self.assertEqual(service.cleanup("remote", confirm=False, dry_run=True)["status"], "planned")
        self.assertEqual([name for name, _, _ in backend.calls], ["status", "cancel", "cleanup"])

    def test_run_creates_a_job_with_explicit_target_and_worktree(self):
        """US7 seam: job execution cannot be hidden inside the legacy remote module."""
        backend = RecordingJobBackend()
        service = HermesJobService(backend)
        run = getattr(service, "run", None)
        self.assertTrue(callable(run), "jobs must expose run(target, prompt, worktree=...) on the bounded service")

        job = run("remote", "bounded prompt", worktree="/tmp/worktree") if callable(run) else None

        self.assertEqual(job["status"], "running")
        self.assertEqual(job["worktree"], "/tmp/worktree")
        self.assertEqual([name for name, _, _ in backend.calls], ["run"])

    def test_duplicate_run_race_returns_the_existing_job_without_second_start(self):
        backend = RecordingJobBackend()
        service = HermesJobService(backend)
        run = getattr(service, "run", None)
        self.assertTrue(callable(run), "jobs must expose a race-safe run seam")

        first = run("remote", "same", worktree="/tmp/worktree", idempotency_key="fixture") if callable(run) else None
        second = run("remote", "same", worktree="/tmp/worktree", idempotency_key="fixture") if callable(run) else None

        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual([name for name, _, _ in backend.calls].count("run"), 1)


if __name__ == "__main__": unittest.main()
