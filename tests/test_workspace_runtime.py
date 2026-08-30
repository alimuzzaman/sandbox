import tempfile
import unittest
from dataclasses import replace
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from unittest import mock

from sandbox.application.workspace_service import SyncPublishRequest, WorkspaceService
from sandbox.jobs.models import TargetRequest
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.scheduler import JobScheduler
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.workspaces import WorkspaceRepository
from sandbox.workspaces.models import JobEvidence


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
    def _sync_fixture(self, temp):
        storage = JobStorage(temp, free_disk_reserve=0)
        checkout = Path(temp) / "checkout"
        source = Path(temp) / "source"
        checkout.mkdir()
        source.mkdir()
        repository = WorkspaceRepository(
            Path(temp) / "workspaces" / "index.sqlite3",
            storage.root / "workspaces",
        )
        record = repository.register(
            "project:test", "unit", workspace_id="ws_sync",
            metadata={
                "checkout_locator": str(checkout),
                "checkout_locator_digest": "sha256:" + hashlib.sha256(
                    str(checkout).encode()).hexdigest(),
                "source_checkout_locator": str(source),
                "source_checkout_locator_digest": "sha256:" + hashlib.sha256(
                    str(source).encode()).hexdigest(),
                "source_identity": "sha256:" + "1" * 64,
                "source_commit": "a" * 40,
            },
        )
        generation = repository.schema_generation()
        base = storage.root / "sync" / hashlib.sha256(
            b"project:test").hexdigest()[:32] / record.workspace_id
        (base / "generations").mkdir(parents=True)
        staging = base / "staging" / "gen_sync"
        staging.mkdir(parents=True)
        content = b"safe\n"
        (staging / "source.txt").write_bytes(content)
        entries = [{
            "path": "source.txt", "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "executable": False,
        }]
        archive_digest = hashlib.sha256(json.dumps(
            entries, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        manifest_digest = "2" * 64
        (staging / ".sandbox-sync-manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "generation_id": "gen_sync",
            "manifest_digest": manifest_digest,
            "archive_manifest_digest": archive_digest,
            "file_count": 1,
            "byte_count": len(content),
            "entries": entries,
        }, sort_keys=True, separators=(",", ":")))
        service = WorkspaceService(
            _Target(), storage, repository=repository,
        )
        request = SyncPublishRequest(
            workspace_id=record.workspace_id,
            project_identity="project:test",
            generation_id="gen_sync",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=1,
            byte_count=len(content),
            expected_index_generation=generation,
        )
        return service, repository, request, base

    def test_sync_publication_holds_workspace_operation_lock_through_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            service, repository, request, base = self._sync_fixture(temp)
            original = repository.operation_lock
            events = []

            @contextmanager
            def recording(operation="workspace-migration", **kwargs):
                events.append(("enter", operation))
                with original(operation, **kwargs):
                    yield
                    self.assertTrue((base / "current").is_symlink())
                events.append(("exit", operation))

            with mock.patch.object(repository, "operation_lock", side_effect=recording):
                result = service.publish_sync(request)
            self.assertTrue(result["ok"])
            self.assertEqual(events, [("enter", "ws_sync"), ("exit", "ws_sync")])

    def test_sync_publication_refuses_destroy_between_preflight_and_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            service, repository, request, base = self._sync_fixture(temp)
            repository.tombstone(request.workspace_id, reason="race")
            with self.assertRaises(Exception) as refused:
                service.publish_sync(request)
            self.assertEqual(refused.exception.code, "workspace_not_found")
            self.assertFalse((base / "current").exists())

    def test_sync_publication_refuses_migration_or_adoption_after_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            service, repository, request, base = self._sync_fixture(temp)
            metadata = (
                repository.legacy_root / "local:race" / "adopted" / "workspace.json"
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{"label":"adopted"}\n')
            plan = repository.migration_plan(evidence=[
                JobEvidence("project:adopted", "local:race", "adopted"),
            ])
            repository.migration_apply(plan, confirm=True)
            with self.assertRaises(Exception) as refused:
                service.publish_sync(request)
            self.assertEqual(refused.exception.code, "workspace_ownership_drift")
            self.assertFalse((base / "current").exists())

    def test_sync_publication_refuses_ownership_change_after_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            service, repository, request, base = self._sync_fixture(temp)
            current = repository.get(request.workspace_id)
            changed = replace(current, project_identity="project:other")
            with mock.patch.object(repository, "get", return_value=changed):
                with self.assertRaises(Exception) as refused:
                    service.publish_sync(request)
            self.assertEqual(refused.exception.code, "workspace_ownership_drift")
            self.assertFalse((base / "current").exists())

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
            self.assertEqual(first["source_binding"], {
                "checkout_present": True,
                "source_present": True,
                "healthy": True,
            })
            identity = TargetRequest(
                project_identity="project:test", workspace="unit",
                workspace_id=first["workspace_id"],
            )
            checkout.rmdir()
            stale_checkout = service.status(identity)["source_binding"]
            self.assertEqual(stale_checkout, {
                "checkout_present": False,
                "source_present": True,
                "healthy": False,
            })
            checkout.mkdir()
            source_checkout.rmdir()
            stale_source = service.status(identity)["source_binding"]
            self.assertEqual(stale_source, {
                "checkout_present": True,
                "source_present": False,
                "healthy": False,
            })
            source_checkout.mkdir()
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
                remote_service_status=lambda _target: {
                    "ownership": "proven", "runtime_revision_state": "match",
                },
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

    def test_remote_workspace_revision_preflight_runs_before_every_control_action(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []

            def status(target):
                calls.append(("status", target.workspace_label))
                return {"ownership": "proven", "runtime_revision_state": "match"}

            def control(target, action):
                calls.append(("control", action))
                return {"ok": True, "action": action}

            service = WorkspaceService(
                _RemoteTarget(), JobStorage(temp, free_disk_reserve=0),
                remote_service_status=status, remote_control=control,
            )
            request = TargetRequest("/p", remote="vps", workspace="e2e")
            confirmed = TargetRequest("/p", remote="vps", workspace="e2e", confirm=True)

            service.create(request)
            service.list(request)
            service.status(request)
            service.migration_plan(request)
            service.migration_apply(TargetRequest(
                "/p", remote="vps", workspace="e2e", migration_plan_id="plan-1",
                confirm=True,
            ))
            service.reset(confirmed)
            service.destroy(confirmed)

            self.assertEqual(
                [kind for kind, _value in calls],
                ["status", "control", "status", "control", "status", "control",
                 "status", "control", "status", "control", "status", "control",
                 "status", "control"],
            )

    def test_remote_workspace_revision_and_ownership_failures_are_safe_and_pre_dispatch(self):
        states = (
            ("mismatch", "proven", "workspace_remote_revision_mismatch"),
            ("unavailable", "proven", "workspace_remote_revision_unavailable"),
            ("unknown", "proven", "workspace_remote_revision_unknown"),
            ("match", "ambiguous", "workspace_remote_service_unproven"),
            ("match", "missing", "workspace_remote_service_unproven"),
            ("match", "unknown", "workspace_remote_service_unproven"),
        )
        for revision, ownership, expected_code in states:
            with self.subTest(revision=revision, ownership=ownership), tempfile.TemporaryDirectory() as temp:
                control_calls = []
                service = WorkspaceService(
                    _RemoteTarget(), JobStorage(temp, free_disk_reserve=0),
                    remote_service_status=lambda _target, revision=revision, ownership=ownership: {
                        "ownership": ownership,
                        "runtime_revision_state": revision,
                        "installed_runtime_revision": "remote-secret-like-value",
                        "detail": "/Users/private/unit",
                    },
                    remote_control=lambda *_args: control_calls.append(True),
                )
                with self.assertRaises(Exception) as refused:
                    service.list(TargetRequest("/p", remote="vps", workspace="e2e"))
                self.assertEqual(refused.exception.code, expected_code)
                self.assertEqual(control_calls, [])
                self.assertEqual(
                    refused.exception.details["observed"],
                    {"ownership": ownership, "runtime_revision_state": revision},
                )
                self.assertEqual(
                    refused.exception.details["recovery_command"],
                    "./sb remote service migrate <name> --confirm --json",
                )
                self.assertNotIn("/Users/private", str(refused.exception))
                self.assertNotIn("remote-secret-like-value", str(refused.exception))

    def test_remote_workspace_missing_or_failed_preflight_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            control_calls = []
            service = WorkspaceService(
                _RemoteTarget(), JobStorage(temp, free_disk_reserve=0),
                remote_control=lambda *_args: control_calls.append(True),
            )
            with self.assertRaises(Exception) as missing:
                service.status(TargetRequest("/p", remote="vps", workspace="e2e"))
            self.assertEqual(missing.exception.code, "workspace_remote_preflight_unavailable")
            self.assertEqual(control_calls, [])
            self.assertEqual(
                missing.exception.details["observed"],
                {"ownership": "unknown", "runtime_revision_state": "unavailable"},
            )

            service.remote_service_status = lambda _target: (_ for _ in ()).throw(
                RuntimeError("ssh ubuntu@1.2.3.4 token=secret"))
            with self.assertRaises(Exception) as failed:
                service.status(TargetRequest("/p", remote="vps", workspace="e2e"))
            self.assertEqual(failed.exception.code, "workspace_remote_preflight_unavailable")
            self.assertNotIn("ubuntu@1.2.3.4", str(failed.exception))
            self.assertNotIn("secret", str(failed.exception))

    def test_local_workspace_operations_do_not_require_remote_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WorkspaceService(
                _Target(), JobStorage(temp, free_disk_reserve=0),
                remote_service_status=lambda _target: self.fail("local must not probe remote"),
            )
            request = TargetRequest("/p", local=True, workspace="local-unit")
            self.assertTrue(service.create(request)["ok"])
            self.assertTrue(service.list(request)["ok"])


class WorkspaceDegradedReportingTests(unittest.TestCase):
    """A degraded index must never hide occupied deployment storage."""

    def _service(self, temp, *, deploy_root):
        repository = WorkspaceRepository(
            Path(temp) / "workspaces" / "index.sqlite3",
            Path(temp) / "legacy",
        )
        service = WorkspaceService(
            _Target(), repository=repository, deployment_root=deploy_root,
            lifecycle_gateway=lambda action, record: {"ok": True, action: True},
        )
        return repository, service

    def test_degraded_index_still_reports_on_disk_workspaces(self):
        with tempfile.TemporaryDirectory() as temp:
            deploy_root = Path(temp) / "deploy-src"
            indexed = deploy_root / "indexed-checkout"
            orphan = deploy_root / "orphan-checkout"
            indexed.mkdir(parents=True)
            orphan.mkdir()
            (orphan / "payload.bin").write_bytes(b"x" * 64)
            repository, service = self._service(temp, deploy_root=deploy_root)
            record = repository.register(
                "project:test", "unit",
                metadata={"checkout_locator": str(indexed)})
            repository.mark_lifecycle(
                record.workspace_id, "indeterminate", status="indeterminate")

            listed = service.list(TargetRequest(project_identity="project:test"))

            self.assertTrue(listed["ok"])
            self.assertFalse(listed["index"]["complete"])
            self.assertEqual(listed["index"]["code"], "workspace_index_incomplete")
            self.assertEqual(listed["code"], "workspace_index_incomplete")
            self.assertIn("read-only report", listed["warning"])
            self.assertEqual(listed["recovery_command"], "./sb workspace migrate --local --json")
            self.assertEqual(listed["index"]["counts"]["indeterminate"], 1)
            entries = {item["path"]: item for item in listed["on_disk"]["entries"]}
            self.assertEqual(set(entries), {str(indexed), str(orphan)})
            self.assertTrue(entries[str(indexed)]["indexed"])
            self.assertEqual(
                entries[str(indexed)]["workspace_id"], record.workspace_id)
            for item in entries.values():
                self.assertIsNone(item["size_bytes"])
                self.assertEqual(item["size_reason"], "not_measured")
                self.assertIsInstance(item["age_seconds"], int)
                self.assertTrue(item["modified_at"].startswith("2"))

    def test_on_disk_only_workspace_is_reported_as_unindexed(self):
        with tempfile.TemporaryDirectory() as temp:
            deploy_root = Path(temp) / "deploy-src"
            orphan = deploy_root / "unindexed-85gb"
            orphan.mkdir(parents=True)
            (orphan / "blob").write_bytes(b"y" * 128)
            _repository, service = self._service(temp, deploy_root=deploy_root)

            listed = service.list(TargetRequest(project_identity="project:test"))

            self.assertTrue(listed["ok"])
            self.assertTrue(listed["index"]["complete"])
            self.assertEqual(listed["workspaces"], [])
            self.assertEqual(listed["on_disk"]["unindexed"], 1)
            self.assertEqual(listed["on_disk"]["total"], 1)
            self.assertFalse(listed["on_disk"]["truncated"])
            entry = listed["on_disk"]["entries"][0]
            self.assertEqual(entry["path"], str(orphan))
            self.assertFalse(entry["indexed"])
            self.assertIsNone(entry["workspace_id"])

            measured = service.list(TargetRequest(
                project_identity="project:test", measure_sizes=True))
            measured_entry = measured["on_disk"]["entries"][0]
            self.assertTrue(measured["on_disk"]["measured"])
            self.assertEqual(measured_entry["size_reason"], "measured")
            self.assertGreaterEqual(measured_entry["size_bytes"], 128)

    def test_missing_deployment_root_reports_reason_without_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            _repository, service = self._service(
                temp, deploy_root=Path(temp) / "absent")
            listed = service.list(TargetRequest(project_identity="project:test"))
            self.assertTrue(listed["ok"])
            self.assertFalse(listed["on_disk"]["available"])
            self.assertEqual(listed["on_disk"]["reason"], "deployment_root_missing")
            self.assertEqual(listed["on_disk"]["entries"], [])

    def test_degraded_index_still_refuses_mutation_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            deploy_root = Path(temp) / "deploy-src"
            deploy_root.mkdir()
            repository, service = self._service(temp, deploy_root=deploy_root)
            record = repository.register("project:test", "unit")
            repository.mark_lifecycle(
                record.workspace_id, "indeterminate", status="indeterminate")
            confirmed = TargetRequest(
                project_identity="project:test", workspace="unit",
                workspace_id=record.workspace_id, confirm=True)
            for action in ("reset", "destroy"):
                with self.assertRaises(Exception) as refusal:
                    getattr(service, action)(confirmed)
                self.assertEqual(
                    refusal.exception.code, "workspace_recovery_required")
            unconfirmed = TargetRequest(
                project_identity="project:test", workspace="unit",
                workspace_id=record.workspace_id)
            status = service.status(unconfirmed)
            self.assertFalse(status["ok"])
            self.assertEqual(status["code"], "workspace_index_incomplete")
            self.assertEqual(
                repository.get(record.workspace_id).lifecycle, "indeterminate")

    def test_healthy_index_listing_stays_successful_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            deploy_root = Path(temp) / "deploy-src"
            deploy_root.mkdir()
            repository, service = self._service(temp, deploy_root=deploy_root)
            repository.register("project:test", "unit")

            listed = service.list(TargetRequest(project_identity="project:test"))

            self.assertTrue(listed["ok"])
            self.assertNotIn("code", listed)
            self.assertNotIn("warning", listed)
            self.assertTrue(listed["index"]["complete"])
            self.assertIsNone(listed["index"]["code"])
            self.assertEqual([item["label"] for item in listed["workspaces"]], ["unit"])
            self.assertEqual(listed["generation"], listed["index"]["generation"])
            self.assertEqual(listed["on_disk"]["entries"], [])
