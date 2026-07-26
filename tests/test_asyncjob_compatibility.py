import unittest

from sandbox.transports.jobs import AsyncJobCompatibilityRouter, LegacyAsyncJobAdapter


class AsyncJobCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.legacy_calls = []
        self.durable_calls = []
        self.legacy_id = "a" * 16
        self.durable_id = "b" * 32
        self.router = AsyncJobCompatibilityRouter(
            legacy=LegacyAsyncJobAdapter(
                lambda value: value == self.legacy_id,
                lambda job_id, **kwargs: self.legacy_calls.append(("status", job_id, kwargs)) or {
                    "job_id": job_id, "status": "running", "stdout": "legacy",
                    "bytes_read": 6, "truncated": False,
                },
                lambda job_id: self.legacy_calls.append(("cancel", job_id, {})) or {
                    "job_id": job_id, "status": "completed", "killed": True,
                },
            ),
            durable_status=lambda job_id: self.durable_calls.append(("status", job_id, {})) or {
                "job_id": job_id, "lifecycle": "succeeded", "exit_code": 0,
            },
            durable_output=lambda job_id, **kwargs: self.durable_calls.append(("output", job_id, kwargs)) or {
                "data": "durable", "bytes_read": 7, "has_more": False,
            },
            durable_cancel=lambda job_id: self.durable_calls.append(("cancel", job_id, {})) or {
                "job_id": job_id, "lifecycle": "cancelling",
            },
        )

    def test_legacy_sixteen_hex_ids_keep_the_existing_status_and_cancel_contract(self):
        self.assertEqual(self.router.status(self.legacy_id, offset=3, limit=9)["stdout"], "legacy")
        self.assertEqual(self.router.cancel(self.legacy_id)["status"], "completed")
        self.assertEqual(self.legacy_calls, [
            ("status", self.legacy_id, {"offset": 3, "limit": 9}),
            ("cancel", self.legacy_id, {}),
        ])
        self.assertEqual(self.durable_calls, [])

    def test_durable_thirty_two_hex_ids_are_exposed_through_the_legacy_read_shape(self):
        result = self.router.status(self.durable_id, offset=2, limit=11)
        self.assertEqual(result, {
            "job_id": self.durable_id, "status": "completed", "exit_code": 0,
            "stdout": "durable", "bytes_read": 7, "truncated": False,
        })
        self.assertEqual(self.router.cancel(self.durable_id), {
            "job_id": self.durable_id, "status": "running", "killed": True,
            "lifecycle": "cancelling",
        })
        self.assertEqual(self.durable_calls, [
            ("status", self.durable_id, {}),
            ("output", self.durable_id, {"offset": 2, "limit": 11}),
            ("cancel", self.durable_id, {}),
        ])

    def test_invalid_ids_fail_before_any_legacy_or_durable_operation(self):
        with self.assertRaisesRegex(ValueError, "invalid async job id"):
            self.router.status("not-a-job")
        with self.assertRaisesRegex(ValueError, "invalid async job id"):
            self.router.cancel("c" * 15)
        self.assertEqual(self.legacy_calls, [])
        self.assertEqual(self.durable_calls, [])

