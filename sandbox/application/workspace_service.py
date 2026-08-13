"""Application boundary for durable, checkout-independent workspace lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from sandbox.workspaces import WorkspaceIndexError, WorkspaceRepository


class WorkspaceServiceProtocol(Protocol):
    def create(self, request): ...
    def list(self, request): ...
    def status(self, request): ...
    def migration_plan(self, request): ...
    def migration_apply(self, request): ...
    def reset(self, request): ...
    def destroy(self, request): ...


_INCOMPLETE = {"unresolved", "conflict", "incomplete", "invalid", "indeterminate"}
_SAFE_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _legacy_namespace(project_root: str, target_scope: str,
                      remote_name: str | None = None) -> str:
    digest = hashlib.sha256(project_root.encode()).hexdigest()[:12]
    is_remote = target_scope == "remote"
    raw = (f"remote:{remote_name}:{digest}" if is_remote
           else f"local:{digest}")
    return raw.replace(":", "-")


def _durable_namespace(project_identity: str) -> str:
    return "project-" + hashlib.sha256(project_identity.encode()).hexdigest()[:24]


def _public_record(record, repository: WorkspaceRepository, *,
                   index_generation: int | None = None) -> dict[str, Any]:
    """Return lifecycle metadata without protected filesystem locators."""
    generation = (repository.schema_generation() if index_generation is None
                  else index_generation)
    digest_pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
    metadata_digest = ("sha256:" + hashlib.sha256(record.path.encode()).hexdigest()
                       if isinstance(record.path, str) else None)
    checkout_digest = record.metadata.get("checkout_locator_digest")
    source_checkout_digest = record.metadata.get("source_checkout_locator_digest")
    if not isinstance(checkout_digest, str) or not digest_pattern.fullmatch(checkout_digest):
        checkout_digest = None
    if (not isinstance(source_checkout_digest, str) or
            not digest_pattern.fullmatch(source_checkout_digest)):
        source_checkout_digest = None
    deployment = {
        key: record.metadata.get(key)
        for key in (
            "checkout_locator_digest", "source_identity", "source_commit",
            "source_dirty_digest",
        )
        if isinstance(record.metadata.get(key), str)
    }
    migration = repository.migration_summary(record.workspace_id)
    legacy_digest = record.metadata.get("legacy_source_digest")
    if (migration["total"] == 0 and record.source == "legacy" and
            isinstance(legacy_digest, str) and digest_pattern.fullmatch(legacy_digest)):
        migration = {
            **migration,
            "decision": record.status,
            "source_digest": legacy_digest,
            "observed_at": record.updated_at,
        }
    complete = record.status not in _INCOMPLETE
    return {
        "workspace_id": record.workspace_id,
        "label": record.label,
        "workspace_label": record.label,
        "project_identity": record.project_identity,
        "namespace": record.namespace,
        "lifecycle": record.lifecycle,
        "state": record.lifecycle,
        "status": record.status,
        "source": record.source,
        "aliases": list(record.aliases),
        "bindings": [dict(item) for item in record.bindings],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "locator_digests": {
            "metadata": metadata_digest,
            "checkout": checkout_digest,
            "source_checkout": source_checkout_digest,
        },
        "checkout": {"present": checkout_digest is not None,
                     "identity": checkout_digest},
        "index_generation": generation,
        "index": {"generation": generation, "complete": complete},
        "migration": migration,
        "deployment_proof": deployment or None,
        "error": None if complete else "workspace_index_incomplete",
    }


def _is_remote_target(target) -> bool:
    return getattr(target, "kind", None) == "remote"


@dataclass
class WorkspaceService:
    """Own workspace metadata; runtime mutation stays behind a capability gateway."""

    target_service: Any
    storage: Any | None = None
    remote_control: Any | None = None
    scheduler: Any | None = None
    repository: WorkspaceRepository | None = None
    lifecycle_gateway: Any | None = None
    resource_binding_resolver: Any | None = None
    deployment_receipt_resolver: Any | None = None
    deployment_root: Path | None = None

    def __post_init__(self) -> None:
        if self.repository is None and self.storage is not None:
            job_reader = None
            if self.scheduler is not None and getattr(self.scheduler, "repository", None) is not None:
                job_reader = lambda: {"jobs": self.scheduler.repository.list(limit=200)}
            self.repository = WorkspaceRepository(
                self.storage.root.parent / "workspaces" / "index.sqlite3",
                self.storage.root / "workspaces",
                job_index_reader=job_reader,
            )
        if self.lifecycle_gateway is None and self.repository is not None:
            self.lifecycle_gateway = self._local_lifecycle

    def _local_lifecycle(self, action: str, record) -> dict[str, Any]:
        """Preserve the legacy scoped reset/destroy behind index containment."""
        metadata_path = Path(record.path) if isinstance(record.path, str) else None
        if metadata_path is None or metadata_path.name != "workspace.json":
            raise WorkspaceIndexError(
                "workspace_metadata_unavailable", "workspace metadata path is unavailable")
        legacy_root = self._repo().legacy_root.resolve(strict=False)
        workspace_root = metadata_path.parent.resolve(strict=False)
        try:
            workspace_root.relative_to(legacy_root)
        except ValueError as exc:
            raise WorkspaceIndexError(
                "workspace_path_escape", "workspace path escapes the lifecycle root") from exc
        if (not isinstance(record.namespace, str) or
                not isinstance(record.label, str) or
                workspace_root != (
                    legacy_root / record.namespace / record.label
                ).resolve(strict=False)):
            raise WorkspaceIndexError(
                "workspace_ownership_drift",
                "workspace locator no longer matches its indexed identity")
        if workspace_root.is_symlink() or metadata_path.is_symlink():
            raise WorkspaceIndexError(
                "workspace_path_unsafe", "workspace lifecycle refuses symlinked metadata")
        checkout_locator = record.metadata.get("checkout_locator")
        source_locator = record.metadata.get("source_checkout_locator")
        source_identity = record.metadata.get("source_identity")
        if any(value is not None for value in (
                checkout_locator, source_locator, source_identity)):
            if (self.deployment_root is None or
                    not all(isinstance(value, str) and value for value in (
                        checkout_locator, source_locator, source_identity))):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "receipt-backed workspace lifecycle proof is incomplete")
            deployment_root = self.deployment_root.resolve(strict=False)
            checkout = Path(checkout_locator).resolve(strict=False)
            source_checkout = Path(source_locator).resolve(strict=False)
            try:
                checkout.relative_to(deployment_root)
                source_checkout.relative_to(deployment_root)
            except ValueError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_escape",
                    "receipt-backed workspace escapes deploy storage") from exc
            if (checkout == deployment_root or source_checkout == deployment_root or
                    checkout == source_checkout or checkout.is_symlink() or
                    source_checkout.is_symlink() or not source_checkout.is_dir()):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "receipt-backed workspace locator is unavailable")
            expected_digest = record.metadata.get("checkout_locator_digest")
            observed_digest = "sha256:" + hashlib.sha256(
                str(checkout).encode()).hexdigest()
            if expected_digest != observed_digest:
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "receipt-backed workspace locator digest changed")
            if action == "reset":
                if checkout.exists():
                    if not checkout.is_dir():
                        raise WorkspaceIndexError(
                            "workspace_ownership_drift",
                            "receipt-backed workspace is not a directory")
                    shutil.rmtree(checkout)
                shutil.copytree(source_checkout, checkout, symlinks=True)
                return {"ok": True, "reset": True, "source_restored": True}
            if action == "destroy":
                if checkout.exists():
                    if not checkout.is_dir():
                        raise WorkspaceIndexError(
                            "workspace_ownership_drift",
                            "receipt-backed workspace is not a directory")
                    shutil.rmtree(checkout)
                shutil.rmtree(workspace_root)
                return {"ok": True, "destroyed": True, "source_removed": True}
        if action == "reset":
            for child in workspace_root.iterdir():
                if child.name == "workspace.json":
                    continue
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
            return {"ok": True, "reset": True}
        if action == "destroy":
            shutil.rmtree(workspace_root)
            return {"ok": True, "destroyed": True}
        raise WorkspaceIndexError(
            "workspace_operation_unsupported", "unsupported workspace lifecycle action")

    def _repo(self) -> WorkspaceRepository:
        if self.repository is None:
            raise WorkspaceIndexError(
                "workspace_index_unavailable", "workspace index is unavailable")
        return self.repository

    def _target(self, request):
        direct_identity = getattr(request, "project_identity", None)
        direct_id = getattr(request, "workspace_id", None)
        direct_plan = getattr(request, "migration_plan_id", None)
        if direct_identity or direct_id or direct_plan:
            remote = getattr(request, "remote", None)
            return SimpleNamespace(
                project_root=getattr(request, "project_dir", "."),
                kind="remote" if remote else "local", remote_name=remote,
                workspace_label=getattr(request, "workspace", None) or "default",
                namespace=getattr(request, "expected_legacy_namespace", None),
                sources={"identity": direct_identity} if direct_identity else {},
            )
        return self.target_service.resolve(request)

    @staticmethod
    def _identity(target, request) -> str | None:
        return (getattr(request, "project_identity", None)
                or getattr(target, "sources", {}).get("identity")
                or getattr(target, "namespace", None))

    def _assert_not_busy(self, identity: str | None, label: str) -> None:
        if self.scheduler is None:
            return
        active = self.scheduler.active()
        if any(item.get("project_identity") == identity and
               item.get("workspace_label") == label for item in active):
            raise WorkspaceIndexError(
                "workspace_busy", f"workspace {label!r} is busy with an active job")

    def _remote(self, target, action: str, request) -> dict | None:
        if not _is_remote_target(target):
            return None
        if self.remote_control is None:
            raise WorkspaceIndexError(
                "workspace_remote_unavailable", "remote workspace control is unavailable")
        import inspect
        parameters = inspect.signature(self.remote_control).parameters
        return (self.remote_control(target, action, request)
                if len(parameters) >= 3 else self.remote_control(target, action))

    def _legacy_root(self, namespace: str) -> Path:
        if not isinstance(namespace, str) or not _SAFE_NAMESPACE.fullmatch(namespace):
            raise WorkspaceIndexError(
                "workspace_namespace_invalid", "workspace namespace is invalid")
        legacy_root = self._repo().legacy_root
        if legacy_root.exists() and legacy_root.is_symlink():
            raise WorkspaceIndexError(
                "workspace_namespace_invalid", "workspace root must not be a symlink")
        root = legacy_root / namespace
        try:
            root.resolve(strict=False).relative_to(legacy_root.resolve(strict=False))
        except (OSError, ValueError) as exc:
            raise WorkspaceIndexError(
                "workspace_namespace_invalid", "workspace namespace escapes its owner root") from exc
        if root.exists() and root.is_symlink():
            raise WorkspaceIndexError(
                "workspace_namespace_invalid", "workspace namespace must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _register(self, *, project_identity: str, label: str, namespace: str,
                  checkout_locator: str | None = None, source: str = "index",
                  deployment_proof: dict[str, str] | None = None):
        if not isinstance(label, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", label):
            raise WorkspaceIndexError(
                "workspace_identity_invalid", "workspace label is invalid")
        repo = self._repo()
        existing = repo.find(project_identity, label)
        if existing is not None:
            if deployment_proof:
                if existing.lifecycle != "ready" or existing.status != "ready":
                    raise WorkspaceIndexError(
                        "workspace_recovery_required",
                        "workspace is not ready for deployment registration")
                current = {
                    key: existing.metadata.get(key) for key in deployment_proof
                }
                if current != deployment_proof:
                    existing = repo.mark_lifecycle(
                        existing.workspace_id, "ready", status="ready",
                        metadata=deployment_proof,
                    )
            return existing, False
        workspace_id = "ws_" + uuid.uuid4().hex
        directory = self._legacy_root(namespace) / label
        metadata_path = directory / "workspace.json"
        if directory.exists() and directory.is_symlink():
            raise WorkspaceIndexError(
                "workspace_namespace_invalid", "workspace directory must not be a symlink")
        existed = metadata_path.exists()
        wrote_metadata = False
        if existed:
            if metadata_path.is_symlink():
                raise WorkspaceIndexError(
                    "workspace_path_unsafe", "workspace metadata must not be a symlink")
            try:
                existing_payload = json.loads(
                    metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise WorkspaceIndexError(
                    "workspace_index_incomplete",
                    "existing workspace metadata requires reviewed migration") from exc
            stored_id = (existing_payload.get("workspace_id")
                         if isinstance(existing_payload, dict) else None)
            if (not isinstance(existing_payload, dict) or
                    existing_payload.get("project_identity") != project_identity or
                    existing_payload.get("label") != label or
                    not isinstance(stored_id, str) or
                    not re.fullmatch(r"ws_[0-9a-f]{32}", stored_id)):
                raise WorkspaceIndexError(
                    "workspace_index_incomplete",
                    "existing legacy workspace requires reviewed migration")
            workspace_id = stored_id
        else:
            self._write_json_atomic(metadata_path, {
                "label": label,
                "target": "remote" if namespace.startswith("remote-") else "local",
                "namespace": namespace.replace("-", ":", 2),
                "mode": "persistent",
                "path": str(directory),
                "project_identity": project_identity,
                "workspace_id": workspace_id,
            })
            wrote_metadata = True
        try:
            record = repo.register(
                project_identity, label, namespace=namespace,
                path=str(metadata_path), workspace_id=workspace_id,
                lifecycle="ready", status="ready",
                source="legacy" if existed else source,
                aliases=(f"legacy:{namespace}:{label}",),
                metadata={
                    **({"checkout_locator": checkout_locator} if checkout_locator else {}),
                    **(deployment_proof or {}),
                },
            )
        except Exception:
            if wrote_metadata:
                try:
                    metadata_path.unlink()
                    directory.rmdir()
                    directory.parent.rmdir()
                except OSError:
                    pass
            raise
        return record, True

    def ensure_submission(self, submission) -> None:
        repo = self._repo()
        existing = repo.find(
            submission.project_identity, submission.workspace_label)
        if existing is None:
            legacy_namespace = _legacy_namespace(
                submission.project_root, submission.target_kind,
                submission.remote_name)
            legacy = repo.submission_legacy_records(
                project_identity=submission.project_identity,
                namespace=legacy_namespace,
                label=submission.workspace_label,
                evidence=(submission.as_dict(),),
            )
            if legacy:
                raise WorkspaceIndexError(
                    "workspace_index_incomplete",
                    "legacy workspace metadata must be migrated before accepting a job")
        namespace = _durable_namespace(submission.project_identity)
        record, _created = self._register(
            project_identity=submission.project_identity,
            label=submission.workspace_label, namespace=namespace,
            checkout_locator=submission.project_root, source="job",
        )
        if self.resource_binding_resolver is not None:
            bindings = self.resource_binding_resolver(submission) or ()
            for binding in bindings:
                if not isinstance(binding, (list, tuple)) or len(binding) != 2:
                    raise WorkspaceIndexError(
                        "workspace_ownership_drift",
                        "workspace resource binding is invalid")
                repo.bind_resource(
                    record.workspace_id, str(binding[0]), str(binding[1]))

    def create(self, request):
        target = self._target(request)
        remote = self._remote(target, "create", request)
        if remote is not None:
            return remote
        identity = self._identity(target, request)
        if not identity:
            raise WorkspaceIndexError(
                "workspace_identity_ambiguous", "project identity is required")
        if getattr(request, "expected_legacy_namespace", None) is not None:
            raise WorkspaceIndexError(
                "workspace_request_invalid",
                "expected legacy namespace is valid only for migration planning")
        namespace = None
        if getattr(request, "project_identity", None):
            namespace = _durable_namespace(identity)
        if not namespace:
            namespace = (target.namespace or "").replace(":", "-")
        if not namespace:
            namespace = _legacy_namespace(target.project_root, target.kind, target.remote_name)
        checkout_locator = getattr(request, "checkout_locator", None)
        receipt = getattr(request, "deployment_receipt", None)
        deployment_proof = None
        if receipt is not None:
            if self.deployment_receipt_resolver is None:
                raise WorkspaceIndexError(
                    "workspace_deployment_receipt_unavailable",
                    "deployment receipt resolution is unavailable")
            resolved = self.deployment_receipt_resolver(receipt, identity)
            if not isinstance(resolved, dict) or not isinstance(
                    resolved.get("checkout_locator"), str):
                raise WorkspaceIndexError(
                    "workspace_deployment_receipt_invalid",
                    "deployment receipt does not identify an exact prepared tree")
            checkout_locator = resolved["checkout_locator"]
            source_identity = resolved.get("source_identity")
            source_commit = resolved.get("commit")
            dirty_digest = resolved.get("dirty_digest")
            if (not isinstance(source_identity, str) or
                    not re.fullmatch(r"sha256:[0-9a-f]{64}", source_identity) or
                    not isinstance(source_commit, str) or
                    not re.fullmatch(r"[0-9a-f]{40,64}", source_commit) or
                    dirty_digest is not None and (
                        not isinstance(dirty_digest, str) or
                        not re.fullmatch(r"[0-9a-f]{64}", dirty_digest))):
                raise WorkspaceIndexError(
                    "workspace_deployment_receipt_invalid",
                    "deployment receipt has incomplete exact-tree provenance")
            checkout_locator = str(Path(checkout_locator).resolve(strict=False))
            source_checkout_locator = str(Path(
                resolved["source_checkout_locator"]).resolve(strict=False))
            deployment_proof = {
                "checkout_locator_digest": "sha256:" + hashlib.sha256(
                    checkout_locator.encode()).hexdigest(),
                "source_checkout_locator": source_checkout_locator,
                "source_checkout_locator_digest": "sha256:" + hashlib.sha256(
                    source_checkout_locator.encode()).hexdigest(),
                "checkout_locator": checkout_locator,
                "source_identity": source_identity,
                "source_commit": source_commit,
                **({"source_dirty_digest": dirty_digest} if dirty_digest else {}),
            }
        record, created = self._register(
            project_identity=identity, label=target.workspace_label,
            namespace=namespace,
            checkout_locator=checkout_locator,
            deployment_proof=deployment_proof,
        )
        return {"ok": True, "created": created,
                **_public_record(record, self._repo())}

    def list(self, request):
        target = self._target(request)
        remote = self._remote(target, "list", request)
        if remote is not None:
            return remote
        identity = self._identity(target, request)
        records = self._repo().list(identity)
        workspace_id = getattr(request, "workspace_id", None)
        if workspace_id:
            records = [item for item in records if item.workspace_id == workspace_id]
        if getattr(request, "active_only", False):
            records = [item for item in records if item.lifecycle not in {
                "destroyed", "tombstoned"}]
        records = records[:getattr(request, "limit", 50)]
        generation = self._repo().schema_generation()
        public = [_public_record(
            item, self._repo(), index_generation=generation) for item in records]
        incomplete = [item for item in public if item["status"] in _INCOMPLETE]
        if incomplete:
            return {
                "ok": False, "code": "workspace_index_incomplete",
                "project_identity": identity, "workspaces": public,
                "counts": {status: sum(item["status"] == status for item in public)
                           for status in sorted(_INCOMPLETE)},
            }
        return {"ok": True, "workspaces": public,
                "project_identity": identity,
                "generation": self._repo().schema_generation()}

    def status(self, request):
        target = self._target(request)
        remote = self._remote(target, "status", request)
        if remote is not None:
            return remote
        workspace_id = getattr(request, "workspace_id", None)
        identity = self._identity(target, request)
        record = (self._repo().get(workspace_id) if workspace_id else
                  self._repo().find(identity, target.workspace_label) if identity else None)
        if (record is not None and identity is not None and
                record.project_identity != identity):
            raise WorkspaceIndexError(
                "workspace_ownership_drift",
                "workspace ID is not owned by the selected project identity")
        if record is None:
            # A legacy record that cannot be attributed is not a safe
            # workspace_not_found result.
            listing = self.list(request)
            if listing.get("code") == "workspace_index_incomplete":
                return listing
            return {"ok": False, "code": "workspace_not_found",
                    "workspace_id": workspace_id, "label": target.workspace_label}
        if record.status in _INCOMPLETE:
            return {"ok": False, "code": "workspace_index_incomplete",
                    "workspace": _public_record(record, self._repo())}
        return {"ok": True, **_public_record(record, self._repo())}

    def migration_plan(self, request):
        target = self._target(request)
        remote = self._remote(target, "migration_plan", request)
        if remote is not None:
            return remote
        plan = self._repo().migration_plan(
            self._identity(target, request),
            expected_legacy_namespace=getattr(
                request, "expected_legacy_namespace", None),
            expected_inventory_digest=getattr(request, "inventory_digest", None),
            expected_generation=getattr(request, "index_generation", None),
        )
        # Protected paths stay repository-internal. The plan remains bound by
        # its persisted digest, generation, expiry, and opaque plan ID.
        return {
            "ok": True, "plan_id": plan.plan_id, "digest": plan.digest,
            "generation": plan.generation, "created_at": plan.created_at,
            "expires_at": plan.expires_at, "summary": dict(plan.summary),
            "project_identity": plan.project_identity,
            "inventory_digest": plan.inventory_digest,
            "records": [{
                "workspace_id": item.workspace_id, "namespace": item.namespace,
                "label": item.label, "status": item.status,
                "project_identity": item.project_identity, "reason": item.reason,
            } for item in plan.items],
            "metadata_only": True,
        }

    def migration_apply(self, request):
        if getattr(request, "confirm", False) is not True:
            raise WorkspaceIndexError(
                "confirmation_required", "workspace migration apply requires confirmation")
        target = self._target(request)
        remote = self._remote(target, "migration_apply", request)
        if remote is not None:
            return remote
        plan_id = getattr(request, "migration_plan_id", None)
        if not plan_id:
            raise WorkspaceIndexError(
                "workspace_migration_plan_required", "migration plan ID is required")
        return self._repo().migration_apply(
            plan_id, confirm=True,
            project_identity=self._identity(target, request),
            expected_legacy_namespace=getattr(
                request, "expected_legacy_namespace", None),
        )

    def _mutate(self, request, action: str):
        if getattr(request, "confirm", False) is not True:
            raise WorkspaceIndexError(
                "confirmation_required", f"workspace {action} requires confirmation")
        target = self._target(request)
        remote = self._remote(target, action, request)
        if remote is not None:
            return remote
        workspace_id = getattr(request, "workspace_id", None)
        identity = self._identity(target, request)
        record = (self._repo().get(workspace_id) if workspace_id else
                  self._repo().find(identity, target.workspace_label) if identity else None)
        if (record is not None and identity is not None and
                record.project_identity != identity):
            raise WorkspaceIndexError(
                "workspace_ownership_drift",
                "workspace ID is not owned by the selected project identity")
        if record is None:
            return {"ok": False, "code": "workspace_not_found"}
        repo = self._repo()
        guard = (repo.operation_lock(record.workspace_id)
                 if hasattr(repo, "operation_lock") else nullcontext())
        with guard:
            current = repo.get(record.workspace_id)
            if current is None:
                return {"ok": False, "code": "workspace_not_found"}
            if (identity is not None and
                    current.project_identity != identity):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "workspace ownership changed before lifecycle mutation")
            if current.lifecycle in {"destroyed", "tombstoned"}:
                return {"ok": False, "code": "workspace_not_found",
                        "workspace_id": current.workspace_id}
            if current.lifecycle != "ready" or current.status != "ready":
                raise WorkspaceIndexError(
                    "workspace_recovery_required",
                    "workspace lifecycle is not ready for mutation")
            self._assert_not_busy(current.project_identity, current.label)
            if self.lifecycle_gateway is None:
                raise WorkspaceIndexError(
                    f"workspace_{action}_unsupported",
                    f"workspace {action} requires a registered runtime lifecycle adapter")
            transient = "resetting" if action == "reset" else "destroying"
            repo.mark_lifecycle(current.workspace_id, transient, status=transient)
            try:
                result = self.lifecycle_gateway(action, current)
                if not isinstance(result, dict) or result.get("ok") is not True:
                    raise WorkspaceIndexError(
                        f"workspace_{action}_failed",
                        f"workspace {action} adapter did not return a successful receipt")
            except Exception:
                repo.mark_lifecycle(
                    current.workspace_id, "indeterminate", status="indeterminate")
                raise
            if action == "destroy":
                repo.mark_lifecycle(current.workspace_id, "destroyed", status="destroyed")
            else:
                repo.mark_lifecycle(current.workspace_id, "ready", status="ready")
            return {**result, **_public_record(
                repo.get(current.workspace_id), repo)}

    def reset(self, request):
        return self._mutate(request, "reset")

    def destroy(self, request):
        return self._mutate(request, "destroy")
