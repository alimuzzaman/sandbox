"""Host-local acceptance fixtures for the durable remote-job contract.

The execution host uses these same durable services when it is reached through
the remote control plane. Credentialed WordPress and VPS cases are deliberately
gated, so normal CI never mistakes a local fixture for remote acceptance.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.application.workspace_service import WorkspaceService
from sandbox.jobs.artifacts import collect
from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class _LocalTarget:
    def resolve(self, request):
        from sandbox.jobs.models import ResolvedTarget
        return ResolvedTarget("/acceptance", "local", None, request.workspace or "default",
                              "local:acceptance", {})


class DurableRuntimeAcceptanceFixtures(unittest.TestCase):
    def _service(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        repository = JobRepository(Path(temp.name) / "registry.sqlite3"); self.addCleanup(repository.close)
        return temp.name, JobService(repository, JobStorage(temp.name, free_disk_reserve=0), None)

    def _wait(self, service, job_id, *, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = service.get(job_id)
            if state["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                return state
            time.sleep(0.03)
        self.fail(f"job {job_id} did not become terminal")

    def _submit(self, service, root, argv, *, workspace="acceptance", deadline=30):
        return service.submit(JobSubmission("test", root, "acceptance", "local", workspace,
            tuple(argv), deadline, SourceIdentity("acceptance")))

    def test_disconnect_resume_retains_complete_output(self):
        root, service = self._service()
        accepted = self._submit(service, root, [sys.executable, "-c",
            "import time; print('first', flush=True); time.sleep(.15); print('second', flush=True)"])
        terminal = self._wait(service, accepted["job_id"])
        self.assertEqual(terminal["lifecycle"], "succeeded")
        first = service.read_output(accepted["job_id"], __import__("sandbox.jobs.models", fromlist=["OutputQuery"]).OutputQuery(max_events=1))
        resumed = service.read_output(accepted["job_id"], __import__("sandbox.jobs.models", fromlist=["OutputQuery"]).OutputQuery(cursor=first["cursor"]))
        self.assertIn("first", first["data"])
        self.assertIn("second", resumed["data"])
        self.assertEqual(resumed["events_read"], 1)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_node_unit_fixture(self):
        root, service = self._service()
        accepted = self._submit(service, root, ["node", "-e", "console.log('node-acceptance')"])
        self.assertEqual(self._wait(service, accepted["job_id"])["lifecycle"], "succeeded")
        self.assertIn("node-acceptance", service.read_output(accepted["job_id"])["data"])

    @unittest.skipUnless(shutil.which("php"), "PHP is not installed")
    def test_php_unit_fixture(self):
        root, service = self._service()
        accepted = self._submit(service, root, ["php", "-r", "echo 'php-acceptance', PHP_EOL;"])
        self.assertEqual(self._wait(service, accepted["job_id"])["lifecycle"], "succeeded")
        self.assertIn("php-acceptance", service.read_output(accepted["job_id"])["data"])

    def test_simultaneous_isolated_labels_and_workspace_lifecycle_fixture(self):
        root, service = self._service()
        source = SourceIdentity("acceptance")
        matrix = service.submit_matrix([
            JobSubmission("test", root, "acceptance", "local", label, (sys.executable, "-c", "print('ok')"),
                30, source, workspace_mode="isolated") for label in ("cell-a", "cell-b")
        ])
        self.assertEqual({child["workspace"] for child in matrix["children"]}, {"cell-a", "cell-b"})
        workspaces = WorkspaceService(_LocalTarget(), JobStorage(root, free_disk_reserve=0))
        request = TargetRequest(root, local=True, workspace="reuse", confirm=True)
        self.assertTrue(workspaces.create(request)["created"])
        self.assertFalse(workspaces.create(request)["created"])
        self.assertTrue(workspaces.reset(request)["reset"])
        self.assertTrue(workspaces.destroy(request)["destroyed"])

    def test_artifact_and_deadline_fixtures(self):
        root, service = self._service()
        report = Path(root) / "report.txt"; report.write_text("artifact")
        accepted = self._submit(service, root, [sys.executable, "-c", "print('artifact')"])
        self.assertEqual(self._wait(service, accepted["job_id"])["lifecycle"], "succeeded")
        items = collect(service.storage, service.repository, accepted["job_id"],
                        project_root=Path(root), declared_paths=("report.txt",))
        self.assertEqual(items[0]["display_name"], "report.txt")
        delayed = self._submit(service, root, [sys.executable, "-c", "import time; time.sleep(2)"],
                               workspace="deadline", deadline=1)
        self.assertEqual(self._wait(service, delayed["job_id"], timeout=5)["lifecycle"], "timed_out")

    @unittest.skipUnless(os.environ.get("SANDBOX_RUN_WP_ACCEPTANCE") == "1",
                         "set SANDBOX_RUN_WP_ACCEPTANCE=1 with a disposable configured project")
    def test_wordpress_integration_fixture_requires_disposable_project(self):
        project = os.environ.get("SANDBOX_ACCEPTANCE_PROJECT")
        self.assertTrue(project, "SANDBOX_ACCEPTANCE_PROJECT must name the disposable WordPress project")
        self.assertTrue(Path(project, "sandbox.config.json").is_file())


if __name__ == "__main__":
    unittest.main()
