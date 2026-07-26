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


class _RemoteTarget:
    def resolve(self, request):
        from sandbox.jobs.models import ResolvedTarget
        return ResolvedTarget("/p", "remote", "vps", request.workspace or "default", "remote:vps:test", {})


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_create_list_reset_destroy_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0))
            request = TargetRequest("/p", local=True, workspace="node-unit")
            self.assertTrue(service.create(request)["created"])
            self.assertFalse(service.create(request)["created"])
            self.assertEqual(len(service.list(request)["workspaces"]), 1)
            self.assertEqual(service.status(request)["namespace"], "local:test")
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

    def test_failed_job_does_not_remove_persistent_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = JobRepository(root / "registry.sqlite")
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0))
            request = TargetRequest("/p", local=True, workspace="failure-retained")
            service.create(request)
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "failure-retained",
                ("false",), 60, SourceIdentity("s")))
            repo.transition(job["job_id"], "running")
            repo.transition(job["job_id"], "failed", exit_code=1)
            self.assertTrue(service.status(request)["ok"])
            self.assertTrue(service.reset(request)["reset"])
            repo.close()

    def test_remote_workspace_actions_use_remote_namespace_and_control_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            service = WorkspaceService(_RemoteTarget(), JobStorage(temp, free_disk_reserve=0),
                remote_control=lambda target, action: calls.append((target.namespace, action)) or {
                    "ok": True, "action": action, "namespace": target.namespace,
                })
            request = TargetRequest("/p", remote="vps", workspace="e2e")
            self.assertEqual(service.create(request)["namespace"], "remote:vps:test")
            self.assertEqual(service.status(request)["action"], "status")
            self.assertEqual(service.reset(request)["action"], "reset")
            self.assertEqual(service.destroy(request)["action"], "destroy")
            self.assertEqual(calls, [("remote:vps:test", action)
                                     for action in ("create", "status", "reset", "destroy")])
