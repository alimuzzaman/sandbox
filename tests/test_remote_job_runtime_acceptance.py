"""Disposable host-local acceptance coverage for durable runtime primitives.

Remote transport uses the same co-located `sb job-start` service, so these tests
exercise the execution-host behavior without requiring a credentialed VPS.
"""

import shutil
import tempfile
import time
import unittest
import json
from contextlib import chdir, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.jobs_runtime import cmd_job_list
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

    def test_3da039b4_local_acceptance_has_explicit_status_and_durable_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            service = JobService(
                repo, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: None,
            )
            accepted = service.submit(JobSubmission(
                "test", temp, "project:acceptance", "local", "acceptance",
                ("echo", "accepted"), 60,
                SourceIdentity("guide-resolved-source", "commit", "dirty"),
                cwd_relative=".",
            ))
            self.assertIs(accepted["ok"], True)
            self.assertEqual(accepted["status"], "accepted")
            self.assertTrue(accepted["job_id"])
            self.assertEqual(accepted["source"]["identity"], "guide-resolved-source")
            self.assertEqual(repo.submission_snapshot(accepted["job_id"])["source"]["identity"],
                             "guide-resolved-source")

    def test_b027d2ab_submission_snapshot_retains_checkout_and_cwd_after_caller_changes_directory(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            root = Path(temp)
            (root / "tests" / "fixtures").mkdir(parents=True)
            repo = JobRepository(root / "registry.sqlite")
            self.addCleanup(repo.close)
            service = JobService(
                repo, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _descriptor: None,
            )
            accepted = service.submit(JobSubmission(
                "test", str(root), "project:proof", "local", "proof",
                ("echo", "proof"), 60, SourceIdentity("proof-checkout"),
                cwd_relative="tests/fixtures",
            ))
            with chdir(other):
                snapshot = repo.submission_snapshot(accepted["job_id"])
            self.assertEqual(snapshot["project_root"], str(root))
            self.assertEqual(snapshot["cwd_relative"], "tests/fixtures")
            self.assertEqual(snapshot["source"]["identity"], "proof-checkout")

    def test_6bc4c6d5_job_list_keeps_the_top_level_jobs_envelope(self):
        rows = [{"job_id": "a" * 32, "lifecycle": "succeeded", "workspace_label": "proof"}]
        service = SimpleNamespace(list=lambda _query: rows)
        output = StringIO()
        args = SimpleNamespace(
            remote=None, project_dir=None, project_identity=None, workspace=None,
            active_only=False, limit=50, json=True,
        )
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies",
                   return_value={"job_service": service}), redirect_stdout(output):
            cmd_job_list(None, args)
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "jobs": rows})
