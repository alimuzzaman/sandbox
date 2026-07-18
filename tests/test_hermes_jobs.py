import unittest

from sandbox.hermes.jobs import DurableHermesJobBackend, HermesJobService
from tests.fakes.hermes import RecordingJobBackend


class TestHermesJobs(unittest.TestCase):
    def test_durable_backend_preserves_status_and_retained_output_shape(self):
        calls = []
        backend = DurableHermesJobBackend(
            submitter=lambda target, prompt, worktree=None: {"job_id": "d" * 32, "status": "running"},
            status_reader=lambda remote, job_id: {"job_id": job_id, "lifecycle": "running"},
            canceler=lambda remote, job_id: {"job_id": job_id, "lifecycle": "cancelling"},
            cleaner=lambda remote, confirm=False, dry_run=True: {"status": "planned"},
            output_reader=lambda remote, job_id, **kwargs: {"data": "retained\n", "bytes_read": 9,
                                                              "has_more": False},
        )
        result = backend.status("remote", "d" * 32, offset=3)
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["stdout"], "retained\n")
        self.assertEqual(result["bytes_read"], 9)

    def test_legacy_async_adapter_rejects_invalid_ids_before_storage_calls(self):
        called = []
        adapter = __import__("sandbox.transports.jobs", fromlist=["LegacyAsyncJobAdapter"]).LegacyAsyncJobAdapter(
            lambda value: value == "a" * 16,
            lambda *args, **kwargs: called.append((args, kwargs)) or {"status": "running"},
            lambda value: {"job_id": value, "killed": True},
        )
        with self.assertRaises(ValueError):
            adapter.status("bad")
        self.assertFalse(called)
        self.assertEqual(adapter.status("a" * 16)["status"], "running")

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
