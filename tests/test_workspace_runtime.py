import tempfile
import unittest
from pathlib import Path

from sandbox.application.workspace_service import WorkspaceService
from sandbox.jobs.models import TargetRequest
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.scheduler import JobScheduler
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.workspaces import WorkspaceRepository


class _Target:
    def resolve(self, request):
        from sandbox.jobs.models import ResolvedTarget
        return ResolvedTarget("/p", "local", None, request.workspace or "default", "local:test",
                              {"identity": "project:test"})


class _RemoteTarget:
    def resolve(self, request):
        from sandbox.jobs.models import ResolvedTarget
        return ResolvedTarget("/p", "remote", "vps", request.workspace or "default", "remote:vps:test",
                              {"identity": "project:test"})


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_create_rejects_namespace_traversal_and_symlink_without_residue(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = JobStorage(temp, free_disk_reserve=0)
            service = WorkspaceService(_Target(), storage)
            with self.assertRaisesRegex(Exception, "valid only for migration"):
                service.create(TargetRequest(
                    "/p", local=True, workspace="unit",
                    project_identity="project:test",
                    expected_legacy_namespace="../../escape",
                ))
            self.assertFalse(Path(temp).joinpath("escape").exists())

            legacy_root = service._repo().legacy_root
            outside = Path(temp) / "outside"
            outside.mkdir()
            legacy_root.mkdir(parents=True, exist_ok=True)
            (legacy_root / "local-test").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(Exception, "escapes|symlink"):
                service.create(TargetRequest("/p", local=True, workspace="unit"))
            self.assertEqual(list(outside.iterdir()), [])

    def test_failed_repository_registration_removes_new_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = JobStorage(temp, free_disk_reserve=0)
            service = WorkspaceService(_Target(), storage)
            alias = "legacy:local-test:unit"
            service._repo().register("project:other", "other", aliases=(alias,))
            with self.assertRaises(Exception):
                service.create(TargetRequest("/p", local=True, workspace="unit"))
            self.assertFalse(
                service._repo().legacy_root.joinpath(
                    "local-test", "unit", "workspace.json").exists())

    def test_job_workspace_registration_persists_exact_resource_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = JobStorage(temp, free_disk_reserve=0)
            repository = WorkspaceRepository(
                Path(temp) / "workspaces" / "index.sqlite3",
                storage.root / "workspaces",
            )
            service = WorkspaceService(
                _Target(), storage, repository=repository,
                resource_binding_resolver=lambda _submission: (
                    ("runtime_instance", "unit-instance"),
                    ("compose_project", "sandbox-unit-instance"),
                ),
            )
            submission = JobSubmission(
                "test", "/p", "project:test", "local", "node-unit",
                ("echo", "x"), 60, SourceIdentity("source"),
            )
            service.ensure_submission(submission)
            record = repository.find("project:test", "node-unit")
            self.assertEqual(
                {(item["resource_type"], item["resource_id"])
                 for item in record.bindings},
                {("runtime_instance", "unit-instance"),
                 ("compose_project", "sandbox-unit-instance")},
            )

    def test_job_acceptance_refuses_an_unmigrated_exact_legacy_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = JobStorage(temp, free_disk_reserve=0)
            repository = WorkspaceRepository(
                Path(temp) / "workspaces" / "index.sqlite3",
                storage.root / "workspaces",
            )
            project_root = "/p"
            import hashlib
            namespace = "local-" + hashlib.sha256(project_root.encode()).hexdigest()[:12]
            metadata = repository.legacy_root / namespace / "unit" / "workspace.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{"label":"unit"}\n')
            service = WorkspaceService(_Target(), repository=repository)
            submission = JobSubmission(
                "test", project_root, "project:test", "local", "unit",
                ("echo", "x"), 60, SourceIdentity("source"),
            )
            with self.assertRaises(Exception) as incomplete:
                service.ensure_submission(submission)
            self.assertEqual(incomplete.exception.code, "workspace_index_incomplete")
            self.assertIsNone(repository.find("project:test", "unit"))

    def test_deployment_receipt_proof_is_persisted_and_refreshed(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp) / "checkout"
            checkout.mkdir()
            source_checkout = Path(temp) / "source-checkout"
            source_checkout.mkdir()
            proof = {
                "checkout_locator": str(checkout),
                "source_checkout_locator": str(source_checkout),
                "source_identity": "sha256:" + "1" * 64,
                "commit": "a" * 40,
                "dirty_digest": "2" * 64,
            }
            service = WorkspaceService(
                _Target(), JobStorage(temp, free_disk_reserve=0),
                deployment_receipt_resolver=lambda _receipt, _identity: dict(proof),
            )
            request = TargetRequest(
                project_identity="project:test", workspace="unit",
                deployment_receipt="receipt-one",
            )
            first = service.create(request)
            self.assertEqual(first["deployment_proof"]["source_commit"], "a" * 40)
            proof["source_identity"] = "sha256:" + "3" * 64
            proof["commit"] = "b" * 40
            second = service.create(request)
            self.assertFalse(second["created"])
            self.assertEqual(second["deployment_proof"]["source_commit"], "b" * 40)
            stored = service._repo().get(second["workspace_id"])
            self.assertEqual(stored.metadata["source_identity"], "sha256:" + "3" * 64)

    def test_receipt_backed_lifecycle_operates_on_prepared_tree_not_source(self):
        with tempfile.TemporaryDirectory() as temp:
            deploy_root = Path(temp) / "deploy-src"
            source = deploy_root / "source"
            checkout = deploy_root / "workspace"
            source.mkdir(parents=True)
            checkout.mkdir()
            (source / "tracked.txt").write_text("source")
            (checkout / "scratch.txt").write_text("scratch")
            proof = {
                "checkout_locator": str(checkout),
                "source_checkout_locator": str(source),
                "source_identity": "sha256:" + "1" * 64,
                "commit": "a" * 40,
                "dirty_digest": "2" * 64,
            }
            service = WorkspaceService(
                _Target(), JobStorage(temp, free_disk_reserve=0),
                deployment_receipt_resolver=lambda _receipt, _identity: dict(proof),
                deployment_root=deploy_root,
            )
            request = TargetRequest(
                project_identity="project:test", workspace="unit",
                deployment_receipt="receipt-one",
            )
            created = service.create(request)
            identity = TargetRequest(
                project_identity="project:test", workspace_id=created["workspace_id"],
                workspace="unit", confirm=True,
            )
            self.assertTrue(service.reset(identity)["source_restored"])
            self.assertEqual((checkout / "tracked.txt").read_text(), "source")
            self.assertTrue(source.is_dir())
            self.assertTrue(service.destroy(identity)["source_removed"])
            self.assertFalse(checkout.exists())
            self.assertTrue(source.is_dir())

    def test_migration_is_metadata_only_and_status_survives_missing_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            legacy = runtime / "jobs" / "workspaces" / "local-abc" / "unit"
            legacy.mkdir(parents=True)
            metadata = legacy / "workspace.json"
            original = b'{"label":"unit","namespace":"local:abc"}\n'
            metadata.write_bytes(original)
            repository = WorkspaceRepository(
                runtime / "workspaces" / "index.sqlite3", legacy.parents[1],
                job_index_reader=lambda: {"jobs": [{
                    "project_identity": "project:test", "namespace": "local-abc",
                    "workspace_label": "unit", "job_id": "job-one",
                }]},
            )
            service = WorkspaceService(_Target(), repository=repository)
            request = TargetRequest(
                project_identity="project:test", workspace="unit",
                expected_legacy_namespace="local-abc",
            )
            plan = service.migration_plan(request)
            self.assertEqual(plan["summary"], {"adoptable": 1})
            self.assertNotIn("path", plan["records"][0])
            applied = service.migration_apply(TargetRequest(
                project_identity="project:test", workspace="unit",
                expected_legacy_namespace="local-abc",
                migration_plan_id=plan["plan_id"], confirm=True,
            ))
            self.assertTrue(applied["metadata_only"])
            self.assertEqual(metadata.read_bytes(), original)
            listed = service.list(request)
            self.assertTrue(listed["ok"])
            public = listed["workspaces"][0]
            workspace_id = public["workspace_id"]
            self.assertEqual(public["migration"]["decision"], "adopted")
            self.assertTrue(public["migration"]["source_digest"].startswith("sha256:"))
            self.assertEqual(public["index_generation"], listed["generation"])
            self.assertEqual(public["index"]["generation"], listed["generation"])
            self.assertTrue(public["locator_digests"]["metadata"].startswith("sha256:"))
            self.assertNotIn("path", public)
            legacy.rename(legacy.with_name("checkout-gone"))
            status = service.status(TargetRequest(workspace_id=workspace_id))
            self.assertTrue(status["ok"])
            self.assertEqual(status["workspace_id"], workspace_id)
            self.assertEqual(status["workspace_label"], "unit")

    def test_create_list_and_scoped_lifecycle_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0))
            request = TargetRequest("/p", local=True, workspace="node-unit")
            created = service.create(request)
            self.assertTrue(created["created"])
            self.assertEqual(created["state"], "ready")
            self.assertEqual(created["migration"]["total"], 0)
            self.assertIn("metadata", created["locator_digests"])
            self.assertFalse(service.create(request)["created"])
            self.assertEqual(len(service.list(request)["workspaces"]), 1)
            self.assertEqual(service.status(request)["namespace"], "local-test")
            confirmed = TargetRequest("/p", local=True, workspace="node-unit", confirm=True)
            scratch = service._repo().get(service.status(request)["workspace_id"]).path
            Path(scratch).parent.joinpath("scratch.txt").write_text("temporary")
            self.assertTrue(service.reset(confirmed)["reset"])
            self.assertFalse(Path(scratch).parent.joinpath("scratch.txt").exists())
            self.assertTrue(service.destroy(confirmed)["destroyed"])
            self.assertEqual(service.status(request)["status"], "destroyed")

    def test_workspace_id_is_filtered_and_cannot_cross_project_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = WorkspaceRepository(
                Path(temp) / "workspaces" / "index.sqlite3",
                Path(temp) / "legacy",
            )
            owner_a = repository.register("project:a", "unit")
            owner_b = repository.register("project:b", "unit")
            lifecycle_calls = []
            service = WorkspaceService(
                _Target(), repository=repository,
                lifecycle_gateway=lambda action, record: lifecycle_calls.append(
                    (action, record.workspace_id)) or {"ok": True, "destroyed": True},
            )
            listed = service.list(TargetRequest(
                project_identity="project:a", workspace_id=owner_a.workspace_id))
            self.assertEqual(
                [item["workspace_id"] for item in listed["workspaces"]],
                [owner_a.workspace_id],
            )
            with self.assertRaises(Exception) as status_error:
                service.status(TargetRequest(
                    project_identity="project:a", workspace_id=owner_b.workspace_id))
            self.assertEqual(status_error.exception.code, "workspace_ownership_drift")
            with self.assertRaises(Exception) as destroy_error:
                service.destroy(TargetRequest(
                    project_identity="project:a", workspace_id=owner_b.workspace_id,
                    confirm=True,
                ))
            self.assertEqual(destroy_error.exception.code, "workspace_ownership_drift")
            self.assertEqual(lifecycle_calls, [])

    def test_reset_refuses_active_workspace_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = JobRepository(root / "registry.sqlite")
            scheduler = JobScheduler(repo)
            service = WorkspaceService(_Target(), JobStorage(temp, free_disk_reserve=0), scheduler=scheduler)
            request = TargetRequest("/p", local=True, workspace="node-unit")
            service.create(request)
            job, _ = repo.accept(JobSubmission("test", "/p", "project:test", "local", "node-unit", ("echo", "x"), 60, SourceIdentity("s")))
            scheduler.acquire(job)
            confirmed = TargetRequest("/p", local=True, workspace="node-unit", confirm=True)
            with self.assertRaises(RuntimeError): service.reset(confirmed)
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
            self.assertTrue(service.status(request)["ok"])
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
            confirmed = TargetRequest("/p", remote="vps", workspace="e2e", confirm=True)
            self.assertEqual(service.reset(confirmed)["action"], "reset")
            self.assertEqual(service.destroy(confirmed)["action"], "destroy")
            self.assertEqual(calls, [("remote:vps:test", action)
                                     for action in ("create", "status", "reset", "destroy")])
