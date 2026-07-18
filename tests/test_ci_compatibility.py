import unittest

from sandbox.ci.compatibility import detect


class CompatibilityTests(unittest.TestCase):
    def test_catalogued_job_differences_have_exact_locations(self):
        result = detect({"concurrency": "one", "jobs": {"win": {"runs-on": "windows-latest", "timeout-minutes": 3, "permissions": {"contents": "read"}}}})
        self.assertEqual({item["id"] for item in result}, {"act.concurrency-ignored", "act.non-linux-runner", "act.job-timeout-ignored", "act.job-permissions-ignored"})
