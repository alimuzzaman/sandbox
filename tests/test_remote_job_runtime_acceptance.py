"""Disposable host-local acceptance coverage for durable runtime primitives.

Remote transport uses the same co-located `sb job-start` service, so these tests
exercise the execution-host behavior without requiring a credentialed VPS.
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class DurableRuntimeAcceptanceTests(unittest.TestCase):
    def _run(self, argv):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        repo = JobRepository(Path(temp.name) / "registry.sqlite"); self.addCleanup(repo.close)
        service = JobService(repo, JobStorage(temp.name, free_disk_reserve=0), None)
        accepted = service.submit(JobSubmission("test", temp.name, "acceptance", "local", "acceptance",
            tuple(argv), 60, SourceIdentity("acceptance")))
        for _ in range(200):
            snapshot = service.get(accepted["job_id"])
            if snapshot["lifecycle"] in {"succeeded", "failed", "timed_out"}: break
            time.sleep(.03)
        self.assertEqual(snapshot["lifecycle"], "succeeded", snapshot)
        return service.read_output(accepted["job_id"])["data"]

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_node_unit_command_retains_output(self):
        self.assertEqual(self._run(["node", "-e", "console.log('node-unit-pass')"]), "node-unit-pass\n")

    @unittest.skipUnless(shutil.which("php"), "PHP is not installed")
    def test_php_unit_style_command_retains_output(self):
        self.assertEqual(self._run(["php", "-r", "echo 'php-unit-pass', PHP_EOL;"]), "php-unit-pass\n")
