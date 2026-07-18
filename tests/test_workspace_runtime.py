import tempfile
import unittest
from pathlib import Path

from sandbox.application.workspace_service import WorkspaceService
from sandbox.jobs.models import TargetRequest
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.scheduler import JobScheduler
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.models import JobSubmission, SourceIdentity


class _Target:
    def resolve(self, request):
        from sandbox.jobs.models import ResolvedTarget
        return ResolvedTarget("/p", "local", None, request.workspace or "default", "local:test", {})


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_create_list_reset_destroy_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0))
            request = TargetRequest("/p", local=True, workspace="node-unit")
            self.assertTrue(service.create(request)["created"])
            self.assertEqual(len(service.list(request)["workspaces"]), 1)
            self.assertTrue(service.reset(request)["reset"])
            self.assertTrue(service.destroy(request)["destroyed"])

    def test_reset_refuses_active_workspace_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = JobRepository(root / "registry.sqlite")
            scheduler = JobScheduler(repo)
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0), scheduler=scheduler)
            request = TargetRequest("/p", local=True, workspace="node-unit")
            service.create(request)
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "node-unit", ("echo", "x"), 60, SourceIdentity("s")))
            scheduler.acquire(job)
            with self.assertRaises(RuntimeError): service.reset(request)
            repo.close()
