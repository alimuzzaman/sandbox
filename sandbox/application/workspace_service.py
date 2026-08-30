"""Application boundary for durable, checkout-independent workspace lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from sandbox.workspaces import WorkspaceIndexError, WorkspaceRepository


class WorkspaceServiceProtocol(Protocol):
    def create(self, request): ...
    def list(self, request): ...
    def status(self, request): ...
    def migration_plan(self, request): ...
    def migration_apply(self, request): ...
    def reset(self, request): ...
    def destroy(self, request): ...
    def publish_sync(self, request): ...


_INCOMPLETE = {"unresolved", "conflict", "incomplete", "invalid", "indeterminate"}
_SAFE_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
# A degraded index must never hide occupied storage, so the on-disk report is
# bounded rather than optional. Sizes stay unmeasured by default: a default
# listing must not walk 85 GB of deployment trees.
_ON_DISK_ENTRY_LIMIT = 2000
_SIZE_ENTRY_BUDGET = 50_000
_SIZE_TIME_BUDGET_SECONDS = 5.0
_REMOTE_REVISION_STATES = {"match", "mismatch", "unavailable", "unknown"}
_REMOTE_OWNERSHIP_STATES = {"proven", "missing", "ambiguous", "unknown"}
_REMOTE_WORKSPACE_RECOVERY = "./sb remote service migrate <name> --confirm --json"
_LOCAL_WORKSPACE_RECOVERY = "./sb workspace migrate --local --json"
_SYNC_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SYNC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


@dataclass(frozen=True)
class SyncPublishRequest:
    """Opaque, path-free request for one controller-owned sync publication."""

    workspace_id: str
    project_identity: str
    generation_id: str
    manifest_digest: str
    archive_manifest_digest: str
    file_count: int
    byte_count: int
    expected_index_generation: int

    def __post_init__(self) -> None:
        for value, label in (
            (self.workspace_id, "workspace id"),
            (self.project_identity, "project identity"),
            (self.generation_id, "generation id"),
        ):
            if not isinstance(value, str) or _SYNC_ID.fullmatch(value) is None:
                raise ValueError(f"{label} is invalid")
        for value, label in (
            (self.manifest_digest, "manifest digest"),
            (self.archive_manifest_digest, "archive manifest digest"),
        ):
            if not isinstance(value, str) or _SYNC_DIGEST.fullmatch(value) is None:
                raise ValueError(f"{label} is invalid")
        for value, label in (
            (self.file_count, "file count"),
            (self.byte_count, "byte count"),
            (self.expected_index_generation, "expected index generation"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _measure_tree(root: Path, *, entry_budget: int,
                  deadline: float) -> tuple[int | None, str]:
    """Sum a tree's apparent size under explicit entry and time budgets.

    Returning ``None`` with a reason is always preferred over an unbounded walk
    or a hanging ``du``: a report that cannot be measured cheaply must say so.
    """
    total = 0
    seen = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > entry_budget:
                        return None, "size_budget_exhausted"
                    if time.monotonic() > deadline:
                        return None, "size_deadline_exceeded"
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        return None, "size_unreadable"
        except OSError:
            return None, "size_unreadable"
    return total, "measured"


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
    def live_directory(locator_key: str, digest: str | None) -> bool:
        locator = record.metadata.get(locator_key)
        if (
            not isinstance(locator, str)
            or not isinstance(digest, str)
            or "sha256:" + hashlib.sha256(locator.encode()).hexdigest() != digest
        ):
            return False
        try:
            details = Path(locator).lstat()
        except OSError:
            return False
        return stat.S_ISDIR(details.st_mode) and not stat.S_ISLNK(details.st_mode)

    checkout_present = live_directory("checkout_locator", checkout_digest)
    source_present = live_directory(
        "source_checkout_locator", source_checkout_digest,
    )
    source_binding = {
        "checkout_present": checkout_present,
        "source_present": source_present,
        "healthy": checkout_present and source_present,
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
        "source_binding": source_binding,
        "error": None if complete else "workspace_index_incomplete",
    }


def _assert_sync_ready(record, repository: WorkspaceRepository,
                       project_identity: str, expected_generation: int) -> None:
    """Re-attest the canonical workspace proof while its operation lock is held."""
    if record.project_identity != project_identity:
        raise WorkspaceIndexError(
            "workspace_ownership_drift",
            "workspace ownership changed before synchronization publication",
        )
    if record.lifecycle in {"destroyed", "tombstoned"}:
        raise WorkspaceIndexError("workspace_not_found", "workspace was destroyed")
    evidence = _public_record(record, repository)
    index = evidence.get("index")
    checkout = evidence.get("checkout")
    locator_digests = evidence.get("locator_digests")
    deployment = evidence.get("deployment_proof")
    binding = evidence.get("source_binding")
    ready = (
        evidence.get("lifecycle") == "ready"
        and evidence.get("state") == "ready"
        and evidence.get("status") == "ready"
        and evidence.get("error") is None
        and isinstance(index, Mapping)
        and index.get("complete") is True
        and index.get("generation") == expected_generation
        and isinstance(checkout, Mapping)
        and checkout.get("present") is True
        and isinstance(checkout.get("identity"), str)
        and isinstance(locator_digests, Mapping)
        and locator_digests.get("checkout") == checkout.get("identity")
        and isinstance(locator_digests.get("source_checkout"), str)
        and isinstance(deployment, Mapping)
        and deployment.get("checkout_locator_digest") == checkout.get("identity")
        and isinstance(deployment.get("source_identity"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", deployment["source_identity"])
        and isinstance(deployment.get("source_commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", deployment["source_commit"])
        and isinstance(binding, Mapping)
        and binding.get("checkout_present") is True
        and binding.get("source_present") is True
        and binding.get("healthy") is True
    )
    if not ready:
        code = (
            "workspace_ownership_drift"
            if isinstance(index, Mapping)
            and index.get("generation") != expected_generation
            else "workspace_recovery_required"
        )
        raise WorkspaceIndexError(
            code,
            "workspace ownership or live source binding changed before synchronization publication",
        )


def _sync_manifest(
    root_fd: int, request: SyncPublishRequest,
) -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
    """Read the manifest through a no-follow directory handle."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(".sandbox-sync-manifest.json", flags, dir_fd=root_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise WorkspaceIndexError("sync_manifest_invalid", "generation manifest is invalid")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        manifest_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        if manifest_identity != (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise WorkspaceIndexError(
                "sync_manifest_invalid", "generation manifest changed during validation")
        manifest_bytes = b"".join(chunks)
        document = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceIndexError(
            "sync_manifest_invalid", "generation manifest is invalid") from exc
    finally:
        os.close(descriptor)
    expected_keys = {
        "schema_version", "generation_id", "manifest_digest",
        "archive_manifest_digest", "file_count", "byte_count", "entries",
    }
    entries = document.get("entries") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != expected_keys
        or document.get("schema_version") != 1
        or document.get("generation_id") != request.generation_id
        or document.get("manifest_digest") != request.manifest_digest
        or document.get("archive_manifest_digest") != request.archive_manifest_digest
        or document.get("file_count") != request.file_count
        or document.get("byte_count") != request.byte_count
        or not isinstance(entries, list)
        or len(entries) != request.file_count
    ):
        raise WorkspaceIndexError(
            "sync_manifest_invalid", "generation manifest binding is invalid")
    canonical = json.dumps(
        entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    if hashlib.sha256(canonical).hexdigest() != request.archive_manifest_digest:
        raise WorkspaceIndexError(
            "sync_manifest_invalid", "generation manifest digest is invalid")
    return entries, (*manifest_identity, hashlib.sha256(manifest_bytes).hexdigest())


def _validate_sync_tree(directory: Path, request: SyncPublishRequest) -> tuple[Any, ...]:
    """Validate the exact tree through directory FDs, including every entry type."""
    root_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                  | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    root_fd = os.open(directory, root_flags)
    try:
        root_details = os.fstat(root_fd)
        if not stat.S_ISDIR(root_details.st_mode):
            raise WorkspaceIndexError("sync_staging_unsafe", "generation root is unsafe")
        entries, manifest_identity = _sync_manifest(root_fd, request)
        expected: dict[str, dict[str, Any]] = {}
        expected_directories: set[str] = set()
        for item in entries:
            if not isinstance(item, dict) or set(item) != {
                "path", "size", "sha256", "executable",
            }:
                raise WorkspaceIndexError("sync_manifest_invalid", "manifest entry is invalid")
            path = item["path"]
            parts = PurePosixPath(path).parts if isinstance(path, str) else ()
            if (not isinstance(path, str) or not path or path.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or path in expected):
                raise WorkspaceIndexError("sync_manifest_invalid", "manifest path is invalid")
            expected[path] = item
            for index in range(1, len(parts)):
                expected_directories.add(PurePosixPath(*parts[:index]).as_posix())

        observed: dict[str, tuple[Any, ...]] = {}
        total = 0

        def walk(directory_fd: int, prefix: str = "") -> None:
            nonlocal total
            with os.scandir(directory_fd) as iterator:
                names = sorted(entry.name for entry in iterator)
            for name in names:
                relative = f"{prefix}/{name}" if prefix else name
                details = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if relative == ".sandbox-sync-manifest.json" and not prefix:
                    if not stat.S_ISREG(details.st_mode):
                        raise WorkspaceIndexError(
                            "sync_manifest_invalid", "generation manifest is invalid")
                    continue
                if stat.S_ISLNK(details.st_mode):
                    raise WorkspaceIndexError(
                        "sync_manifest_invalid", "generation contains an unlisted entry")
                if stat.S_ISDIR(details.st_mode):
                    if relative not in expected_directories:
                        raise WorkspaceIndexError(
                            "sync_manifest_invalid", "generation contains an unlisted entry")
                    child_fd = os.open(name, root_flags, dir_fd=directory_fd)
                    try:
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(details.st_mode) or relative not in expected:
                    raise WorkspaceIndexError(
                        "sync_manifest_invalid", "generation contains an unlisted entry")
                item = expected[relative]
                descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    before = os.fstat(descriptor)
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                    after = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                identity = (
                    before.st_dev, before.st_ino, before.st_mode, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns,
                )
                if identity != (
                    after.st_dev, after.st_ino, after.st_mode, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns,
                ) or (
                    isinstance(item["size"], bool) or item["size"] != size
                    or not isinstance(item["sha256"], str)
                    or digest.hexdigest() != item["sha256"]
                    or not isinstance(item["executable"], bool)
                    or bool(before.st_mode & stat.S_IXUSR) != item["executable"]
                ):
                    raise WorkspaceIndexError(
                        "sync_manifest_invalid", "generation member mismatch")
                observed[relative] = (*identity, digest.hexdigest())
                total += size

        walk(root_fd)
        if set(observed) != set(expected) or total != request.byte_count:
            raise WorkspaceIndexError(
                "sync_manifest_invalid", "generation inventory mismatch")
        return (
            (".sandbox-sync-manifest.json", *manifest_identity),
            *((path, *observed[path]) for path in sorted(observed)),
        )
    finally:
        os.close(root_fd)


def _current_points_to(current: Path, published: Path) -> bool:
    if not current.is_symlink():
        return False
    try:
        return current.resolve(strict=True) == published.resolve(strict=True)
    except OSError:
        return False


def _publish_sync_generation_unchecked(root: Path, request: SyncPublishRequest) -> None:
    project_hash = hashlib.sha256(request.project_identity.encode()).hexdigest()[:32]
    base = root / "sync" / project_hash / request.workspace_id
    staging = base / "staging" / request.generation_id
    published = base / "generations" / request.generation_id
    current = base / "current"
    for directory in (root, root / "sync", root / "sync" / project_hash, base,
                      base / "staging", base / "generations"):
        details = directory.lstat()
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise WorkspaceIndexError("sync_staging_unsafe", "generation path is unsafe")
    staging_present = staging.exists() and not staging.is_symlink()
    published_present = published.exists() and not published.is_symlink()
    if staging_present and published_present:
        raise WorkspaceIndexError(
            "sync_generation_conflict", "staged and published generations are ambiguous")
    if not staging_present and not published_present:
        raise WorkspaceIndexError("sync_staging_unavailable", "staged generation is unavailable")
    source = published if published_present else staging
    fingerprint = _validate_sync_tree(source, request)
    if published_present and _current_points_to(current, published):
        return
    if os.path.lexists(current) and not current.is_symlink():
        raise WorkspaceIndexError("sync_pointer_unsafe", "current generation pointer is unsafe")
    old_target = os.readlink(current) if current.is_symlink() else None
    renamed = False
    committed = False
    temporary = current.with_name(".current-" + request.generation_id)
    rollback = current.with_name(".rollback-" + request.generation_id)
    try:
        if not published_present:
            os.replace(staging, published)
            renamed = True
        if _validate_sync_tree(published, request) != fingerprint:
            raise WorkspaceIndexError(
                "sync_manifest_invalid", "generation changed during publication")
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        os.symlink(str(published), str(temporary), target_is_directory=True)
        if _validate_sync_tree(published, request) != fingerprint:
            raise WorkspaceIndexError(
                "sync_manifest_invalid", "generation changed during publication")
        os.replace(temporary, current)
        committed = True
        if _validate_sync_tree(published, request) != fingerprint:
            raise WorkspaceIndexError(
                "sync_manifest_invalid", "generation changed during publication")
        os.chmod(published, 0o700)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        if committed:
            try:
                if old_target is None:
                    current.unlink()
                else:
                    try:
                        rollback.unlink()
                    except FileNotFoundError:
                        pass
                    os.symlink(old_target, rollback, target_is_directory=True)
                    os.replace(rollback, current)
            except OSError:
                pass
        if renamed:
            try:
                if not staging.exists() and published.exists():
                    os.replace(published, staging)
            except OSError:
                pass
        raise


def _publish_sync_generation(root: Path, request: SyncPublishRequest) -> None:
    """Publish with exact-tree replay recovery and bounded filesystem errors."""
    try:
        _publish_sync_generation_unchecked(root, request)
    except WorkspaceIndexError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise WorkspaceIndexError(
            "sync_publication_failed", "generation publication failed safely") from None


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
    # Remote workspace control is allowed only after the selected MCP service
    # has proved both ownership and parity with this runtime.  The resolver is
    # injected at the composition boundary so this application service never
    # reaches into the remote registry or reads its state files directly.
    remote_service_status: Any | None = None

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
                from sandbox.workspaces.checkout import (
                    WorkspaceMaterializationError, materialize,
                    plan_materialization,
                )
                try:
                    receipt = materialize(plan_materialization(
                        source_checkout, checkout,
                        source_identity=source_identity,
                        workspace_label=record.label,
                    ))
                except WorkspaceMaterializationError as exc:
                    raise WorkspaceIndexError(
                        "workspace_lifecycle_indeterminate",
                        "receipt-backed workspace reset is indeterminate",
                    ) from exc
                return {"ok": True, "reset": True, "source_restored": True,
                        "materialization": receipt.to_dict()}
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

    def _indexed_locators(self) -> dict[str, str]:
        """Map every indexed deployment locator to its owning workspace ID."""
        locators: dict[str, str] = {}
        try:
            records = self._repo().list(None)
        except Exception:
            return locators
        for record in records:
            for key in ("checkout_locator", "source_checkout_locator"):
                value = record.metadata.get(key)
                if isinstance(value, str) and value:
                    locators.setdefault(
                        str(Path(value).resolve(strict=False)), record.workspace_id)
        return locators

    def _on_disk_inventory(self, *, measure: bool = False,
                           limit: int = _ON_DISK_ENTRY_LIMIT) -> dict[str, Any]:
        """Report deployment storage that exists, indexed or not.

        This is deliberately read-only and cheap: one directory listing plus one
        ``stat`` per child. It exists so a degraded index can never make
        occupied storage invisible to reporting and reclaim decisions.
        """
        root = self.deployment_root
        if root is None:
            return {"available": False, "reason": "deployment_root_unset",
                    "root": None, "measured": False, "total": 0,
                    "unindexed": 0, "truncated": False, "entries": []}
        root = Path(root)
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            reason = ("deployment_root_missing"
                      if isinstance(exc, FileNotFoundError)
                      else "deployment_root_unreadable")
            return {"available": False, "reason": reason, "root": str(root),
                    "measured": False, "total": 0, "unindexed": 0,
                    "truncated": False, "entries": []}
        locators = self._indexed_locators()
        deadline = time.monotonic() + _SIZE_TIME_BUDGET_SECONDS
        entries: list[dict[str, Any]] = []
        unindexed = 0
        total = 0
        for child in children:
            try:
                is_symlink = child.is_symlink()
                if not child.is_dir():
                    continue
                stat = child.stat(follow_symlinks=False)
            except OSError:
                continue
            total += 1
            resolved = str(child.resolve(strict=False))
            workspace_id = locators.get(resolved) or locators.get(str(child))
            if workspace_id is None:
                unindexed += 1
            if len(entries) >= limit:
                continue
            size_bytes: int | None = None
            size_reason = "not_measured"
            if measure and not is_symlink:
                size_bytes, size_reason = _measure_tree(
                    child, entry_budget=_SIZE_ENTRY_BUDGET, deadline=deadline)
            elif measure:
                size_reason = "size_symlink_skipped"
            entries.append({
                "path": str(child),
                "name": child.name,
                "indexed": workspace_id is not None,
                "workspace_id": workspace_id,
                "symlink": is_symlink,
                "size_bytes": size_bytes,
                "size_reason": size_reason,
                "modified_at": _iso(stat.st_mtime),
                "age_seconds": max(0, int(time.time() - stat.st_mtime)),
            })
        return {"available": True, "reason": None, "root": str(root),
                "measured": bool(measure), "total": total,
                "unindexed": unindexed,
                "truncated": total > len(entries), "entries": entries}

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

        self._assert_remote_service_ready(target)
        import inspect
        parameters = inspect.signature(self.remote_control).parameters
        return (self.remote_control(target, action, request)
                if len(parameters) >= 3 else self.remote_control(target, action))

    @staticmethod
    def _safe_remote_observation(status: Any) -> dict[str, str]:
        """Keep only finite enum observations from the remote status probe.

        The status probe runs outside this process and is therefore treated as
        untrusted input.  In particular, never copy a remote error, unit
        content, endpoint, or credential-bearing field into a workspace error.
        """
        ownership = status.get("ownership") if isinstance(status, dict) else None
        revision = status.get("runtime_revision_state") if isinstance(status, dict) else None
        if not isinstance(ownership, str):
            ownership = None
        if not isinstance(revision, str):
            revision = None
        return {
            "ownership": ownership if ownership in _REMOTE_OWNERSHIP_STATES else "unknown",
            "runtime_revision_state": revision if revision in _REMOTE_REVISION_STATES else "unknown",
        }

    @staticmethod
    def _remote_failure_message(message: str, observed: dict[str, str]) -> str:
        """Render finite preflight evidence for adapters that flatten errors."""
        return (
            f"{message} (observed ownership={observed['ownership']}, "
            f"runtime_revision_state={observed['runtime_revision_state']}; "
            f"recovery: {_REMOTE_WORKSPACE_RECOVERY})"
        )

    def _assert_remote_service_ready(self, target) -> None:
        """Fail closed before dispatching any remote workspace operation.

        A configured service record is not evidence that the selected owned
        service is running the same Sandbox runtime.  The injected status
        resolver is the only authority for that live observation; absent or
        malformed evidence is refused just like an explicit mismatch.
        """
        status: Any = None
        probe_failed = False
        if not callable(self.remote_service_status):
            probe_failed = True
        else:
            try:
                status = self.remote_service_status(target)
            except Exception:
                # Remote diagnostics are intentionally not forwarded.  The
                # operator gets the supported refresh/migration command below.
                probe_failed = True

        observed = self._safe_remote_observation(status)
        if probe_failed and not isinstance(status, dict):
            observed = {"ownership": "unknown", "runtime_revision_state": "unavailable"}
        if probe_failed:
            raise WorkspaceIndexError(
                "workspace_remote_preflight_unavailable",
                self._remote_failure_message(
                    "remote MCP service revision evidence is unavailable; refresh the owned service before retrying",
                    observed,
                ),
                observed=observed, recovery_command=_REMOTE_WORKSPACE_RECOVERY,
            )

        if observed["ownership"] != "proven":
            raise WorkspaceIndexError(
                "workspace_remote_service_unproven",
                self._remote_failure_message(
                    "remote MCP service ownership could not be proven; refresh the owned service before retrying",
                    observed,
                ),
                observed=observed, recovery_command=_REMOTE_WORKSPACE_RECOVERY,
            )

        revision_state = observed["runtime_revision_state"]
        if revision_state != "match":
            code = f"workspace_remote_revision_{revision_state}"
            raise WorkspaceIndexError(
                code,
                self._remote_failure_message(
                    "remote MCP service runtime revision is not verified; refresh the owned service before retrying",
                    observed,
                ),
                observed=observed, recovery_command=_REMOTE_WORKSPACE_RECOVERY,
            )

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
        counts = {status: sum(item["status"] == status for item in public)
                  for status in sorted(_INCOMPLETE)}
        on_disk = self._on_disk_inventory(
            measure=bool(getattr(request, "measure_sizes", False)))
        # A degraded index is loud but not fatal for read-only reporting: an
        # unreadable inventory is exactly how occupied storage becomes
        # invisible. Mutation paths keep their own per-record readiness gate.
        index_block = {
            "generation": generation,
            "complete": not incomplete,
            "code": "workspace_index_incomplete" if incomplete else None,
            "counts": counts,
        }
        payload: dict[str, Any] = {
            "ok": True, "workspaces": public, "project_identity": identity,
            "generation": generation, "counts": counts,
            "index": index_block, "on_disk": on_disk,
        }
        if incomplete:
            payload["code"] = "workspace_index_incomplete"
            payload["recovery_command"] = _LOCAL_WORKSPACE_RECOVERY
            payload["warning"] = (
                "workspace index is incomplete; this listing is a read-only "
                f"report and workspace mutation stays refused; recovery: {_LOCAL_WORKSPACE_RECOVERY}")
        return payload

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
            if listing.get("index", {}).get("complete") is False:
                # Reporting degrades; single-workspace status does not. An
                # unattributable legacy record is still not a safe
                # workspace_not_found answer.
                return {"ok": False, "code": "workspace_index_incomplete",
                        "project_identity": listing.get("project_identity"),
                        "workspaces": listing.get("workspaces", []),
                        "counts": listing.get("counts"),
                        "index": listing.get("index"),
                        "on_disk": listing.get("on_disk"),
                        "recovery_command": _LOCAL_WORKSPACE_RECOVERY}
            return {"ok": False, "code": "workspace_not_found",
                    "workspace_id": workspace_id, "label": target.workspace_label}
        if record.status in _INCOMPLETE:
            return {"ok": False, "code": "workspace_index_incomplete",
                    "workspace": _public_record(record, self._repo()),
                    "recovery_command": _LOCAL_WORKSPACE_RECOVERY}
        return {"ok": True, **_public_record(record, self._repo())}

    def publish_sync(self, request: SyncPublishRequest) -> dict[str, Any]:
        """Authorize and publish staged sync bytes under one workspace lock."""
        if not isinstance(request, SyncPublishRequest):
            raise TypeError("sync publication request is invalid")
        if self.storage is None or not isinstance(getattr(self.storage, "root", None), Path):
            raise WorkspaceIndexError(
                "sync_publication_unsupported",
                "synchronization publication storage is unavailable",
            )
        repo = self._repo()
        with repo.operation_lock(request.workspace_id):
            current = repo.get(request.workspace_id)
            if current is None or current.lifecycle in {"destroyed", "tombstoned"}:
                raise WorkspaceIndexError("workspace_not_found", "workspace is unavailable")
            _assert_sync_ready(
                current, repo, request.project_identity,
                request.expected_index_generation,
            )
            _publish_sync_generation(self.storage.root, request)
            return {
                "ok": True,
                "status": "accepted",
                "accepted_generation": request.generation_id,
                "manifest_digest": request.manifest_digest,
                "file_count": request.file_count,
                "byte_count": request.byte_count,
                "workspace_id": request.workspace_id,
                "project_identity": request.project_identity,
            }

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
                if current.status in _INCOMPLETE:
                    raise WorkspaceIndexError(
                        "workspace_recovery_required",
                        "workspace metadata is incomplete; create and review a migration plan before mutation",
                        recovery_command=_LOCAL_WORKSPACE_RECOVERY,
                    )
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
