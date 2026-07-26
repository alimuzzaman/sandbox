import unittest

from sandbox.hermes.jobs import DurableHermesJobBackend, HermesJobService


class HermesJobCompatibilityTests(unittest.TestCase):
    def test_durable_terminal_lifecycle_and_retained_output_keep_legacy_fields(self):
        backend = DurableHermesJobBackend(
            submitter=lambda *_args, **_kwargs: {"job_id": "d" * 32, "status": "running"},
            status_reader=lambda _remote, job_id: {"job_id": job_id, "lifecycle": "cancelled"},
            canceler=lambda _remote, job_id: {"job_id": job_id, "lifecycle": "cancelling"},
            cleaner=lambda _remote, **_kwargs: {"status": "planned"},
            output_reader=lambda _remote, _job_id, **_kwargs: {
                "data": "retained output", "bytes_read": 15, "has_more": True,
            },
        )
        result = HermesJobService(backend).status("remote", "d" * 32, offset=17)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stdout"], "retained output")
        self.assertEqual(result["bytes_read"], 15)
        self.assertTrue(result["truncated"])

    def test_cancel_and_cleanup_do_not_change_the_injected_backend_contract(self):
        calls = []
        backend = DurableHermesJobBackend(
            submitter=lambda *_args, **_kwargs: {},
            status_reader=lambda *_args: {},
            canceler=lambda remote, job_id: calls.append(("cancel", remote, job_id)) or {
                "job_id": job_id, "status": "cancelled",
            },
            cleaner=lambda remote, confirm, dry_run: calls.append(
                ("cleanup", remote, confirm, dry_run)) or {"status": "planned"},
        )
        service = HermesJobService(backend)
        self.assertEqual(service.cancel("remote", "d" * 32)["status"], "cancelled")
        self.assertEqual(service.cleanup("remote", confirm=False, dry_run=True)["status"], "planned")
        self.assertEqual(calls, [
            ("cancel", "remote", "d" * 32),
            ("cleanup", "remote", False, True),
        ])

