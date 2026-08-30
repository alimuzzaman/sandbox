"""Application boundary for durable, checkout-independent workspace lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from sandbox.workspaces import WorkspaceIndexError, WorkspaceRepository
from sandbox.jobs.process import ProcessIdentity, capture_process_identity, verify_process_identity


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
MAX_CI_MATERIALIZATION_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_CI_MATERIALIZATION_ENTRIES = 100_000
MIN_CI_MATERIALIZATION_FREE_RESERVE_BYTES = 1024 * 1024 * 1024


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


def _digest_payload(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _filesystem_identity(path: Path) -> dict[str, int]:
    observed = os.stat(path, follow_symlinks=False)
    return {"device": int(observed.st_dev), "inode": int(observed.st_ino)}


def _path_is_within(candidate: str, root: Path) -> bool:
    try:
        Path(candidate).resolve(strict=False).relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _decode_mountinfo_path(value: str) -> str:
    return (value.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def _mountinfo_reference_count(text: str, checkout: Path, *,
                               device: tuple[int, int] | None = None) -> int:
    """Count mountpoints in checkout and same-device bind roots from it."""
    checkout = checkout.resolve(strict=False)
    if device is None:
        observed = os.stat(checkout, follow_symlinks=False)
        device = (os.major(observed.st_dev), os.minor(observed.st_dev))
    rows: list[tuple[tuple[int, int], Path, Path]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        try:
            major, minor = (int(part) for part in fields[2].split(":", 1))
        except (TypeError, ValueError):
            continue
        rows.append(((major, minor),
                     Path(_decode_mountinfo_path(fields[3])).resolve(strict=False),
                     Path(_decode_mountinfo_path(fields[4])).resolve(strict=False)))
    covering = [row for row in rows if row[0] == device and
                (row[2] == checkout or _path_is_within(str(checkout), row[2]))]
    if not covering:
        return 0
    base = max(covering, key=lambda row: len(row[2].parts))
    relative = checkout.relative_to(base[2])
    checkout_root = (base[1] / relative).resolve(strict=False)
    count = 0
    for row_device, root, mountpoint in rows:
        if row_device == device and root == base[1] and mountpoint == base[2]:
            continue
        mounted_inside = (mountpoint == checkout or
                          _path_is_within(str(mountpoint), checkout))
        sourced_inside = (row_device == device and
                           (root == checkout_root or
                            _path_is_within(str(root), checkout_root) or
                            _path_is_within(str(checkout_root), root)))
        if mounted_inside or sourced_inside:
            count += 1
    return count


def _observe_mount_references(checkout: Path) -> int | None:
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.is_file():
        return 0 if not sys.platform.startswith("linux") else None
    try:
        return _mountinfo_reference_count(
            mountinfo.read_text(errors="replace"), checkout)
    except (OSError, ValueError):
        return None


def _process_group_empty(pgid: int) -> bool:
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return True
    except (OSError, ValueError):
        return False
    return False


def _owned_cgroup_empty(cgroup_path: str) -> bool:
    if (not isinstance(cgroup_path, str) or not cgroup_path.startswith("/") or
            cgroup_path == "/" or ".." in Path(cgroup_path).parts):
        return False
    root = Path("/sys/fs/cgroup").resolve(strict=False)
    target = (root / cgroup_path.lstrip("/")).resolve(strict=False)
    try:
        target.relative_to(root)
        payload = (target / "cgroup.procs").read_text(encoding="ascii")
    except FileNotFoundError:
        return True
    except (OSError, UnicodeError, ValueError):
        return False
    return not any(line.strip() for line in payload.splitlines())


def _remove_tree_fd(directory_fd: int) -> None:
    """Delete one already-open directory without following path replacements."""
    for name in os.listdir(directory_fd):
        observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(observed.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev != observed.st_dev or
                        opened.st_ino != observed.st_ino):
                    raise WorkspaceIndexError(
                        "workspace_ownership_drift",
                        "cleanup child directory identity changed")
                _remove_tree_fd(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _observe_cleanup_references(checkout: Path) -> dict[str, int | None]:
    """Return positive host/container mount absence or unknown on probe failure."""
    mounts = _observe_mount_references(checkout)
    containers: int | None = None
    try:
        from sandbox.services.environment import compatible_subprocess_environment
        listed = subprocess.run(
            ["docker", "ps", "-aq"], capture_output=True, text=True,
            timeout=10, check=False, env=compatible_subprocess_environment(),
        )
        if listed.returncode == 0 and len(listed.stdout) <= 131_072:
            all_identifiers = tuple(
                line.strip() for line in listed.stdout.splitlines() if line.strip())
            identifiers = all_identifiers if len(all_identifiers) <= 1000 else ()
            if not identifiers:
                containers = 0 if not all_identifiers else None
            else:
                inspected = subprocess.run(
                    ["docker", "inspect", *identifiers], capture_output=True,
                    text=True, timeout=20, check=False,
                    env=compatible_subprocess_environment(),
                )
                if inspected.returncode == 0 and len(inspected.stdout) <= 16_777_216:
                    rows = json.loads(inspected.stdout)
                    if isinstance(rows, list):
                        containers = sum(
                            any(isinstance(mount, dict) and
                                isinstance(mount.get("Source"), str) and
                                (_path_is_within(mount["Source"], checkout) or
                                 _path_is_within(str(checkout), Path(mount["Source"])))
                                for mount in (row.get("Mounts") or ()))
                            for row in rows if isinstance(row, dict)
                        )
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        containers = None
    return {"containers": containers, "mounts": mounts}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _archive_checkout(checkout: Path, artifact: Path) -> tuple[str, int]:
    measured, reason = _measure_tree(
        checkout, entry_budget=MAX_CI_MATERIALIZATION_ENTRIES,
        deadline=time.monotonic() + 30.0)
    if measured is None or measured > MAX_CI_MATERIALIZATION_ARCHIVE_BYTES:
        raise WorkspaceIndexError(
            "workspace_materialization_too_large",
            f"materialization archive exceeds its bounded input ({reason})")
    artifact.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    usage = shutil.disk_usage(artifact.parent)
    if (usage.free - MAX_CI_MATERIALIZATION_ARCHIVE_BYTES <
            MIN_CI_MATERIALIZATION_FREE_RESERVE_BYTES):
        raise WorkspaceIndexError(
            "workspace_materialization_reserve",
            "materialization archive cannot preserve the disk reserve")
    descriptor, temporary = tempfile.mkstemp(
        prefix=".ci-materialization-", suffix=".tar.gz", dir=artifact.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        archive_entries = 0
        archive_bytes = 0

        def bounded_member(member: tarfile.TarInfo) -> tarfile.TarInfo:
            nonlocal archive_entries, archive_bytes
            archive_entries += 1
            archive_bytes += member.size if member.isfile() else 0
            if (archive_entries > MAX_CI_MATERIALIZATION_ENTRIES or
                    archive_bytes > MAX_CI_MATERIALIZATION_ARCHIVE_BYTES):
                raise WorkspaceIndexError(
                    "workspace_materialization_too_large",
                    "materialization archive changed beyond its bounded input")
            return member

        class BoundedWriter:
            def __init__(self, handle):
                self.handle = handle
                self.written = 0

            def write(self, payload):
                if self.written + len(payload) > MAX_CI_MATERIALIZATION_ARCHIVE_BYTES:
                    raise WorkspaceIndexError(
                        "workspace_materialization_too_large",
                        "materialization archive exceeds its bounded output")
                written = self.handle.write(payload)
                self.written += written
                return written

            def __getattr__(self, name):
                return getattr(self.handle, name)

        with temporary_path.open("wb") as raw_archive:
            writer = BoundedWriter(raw_archive)
            with tarfile.open(fileobj=writer, mode="w:gz") as archive:
                archive.add(
                    checkout, arcname="workspace", recursive=True,
                    filter=bounded_member)
        size = temporary_path.stat().st_size
        if size > MAX_CI_MATERIALIZATION_ARCHIVE_BYTES:
            raise WorkspaceIndexError(
                "workspace_materialization_too_large",
                "materialization archive exceeds its bounded output")
        if (shutil.disk_usage(artifact.parent).free <
                MIN_CI_MATERIALIZATION_FREE_RESERVE_BYTES):
            raise WorkspaceIndexError(
                "workspace_materialization_reserve",
                "materialization archive crossed the disk reserve")
        os.chmod(temporary_path, 0o600)
        digest = _file_sha256(temporary_path)
        try:
            os.link(temporary_path, artifact, follow_symlinks=False)
        except FileExistsError as exc:
            raise WorkspaceIndexError(
                "workspace_materialization_failed",
                "materialization archive generation already exists") from exc
        return digest, size
    finally:
        temporary_path.unlink(missing_ok=True)


def _restore_checkout(artifact: Path, expected_digest: str,
                      checkout: Path) -> None:
    if (not artifact.is_file() or artifact.is_symlink() or
            _file_sha256(artifact) != expected_digest or checkout.exists()):
        raise WorkspaceIndexError(
            "workspace_materialization_unavailable",
            "retained CI materialization artifact is unavailable")
    staging = checkout.parent / f".{checkout.name}.restore-{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        with tarfile.open(artifact, "r:gz") as archive:
            archive.extractall(staging, filter="data")
        restored = staging / "workspace"
        if not restored.is_dir() or restored.is_symlink():
            raise WorkspaceIndexError(
                "workspace_materialization_unavailable",
                "retained CI materialization artifact is invalid")
        os.rename(restored, checkout)
    except (OSError, tarfile.TarError) as exc:
        raise WorkspaceIndexError(
            "workspace_materialization_failed",
            "retained CI materialization restore failed") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


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
    cleanup_reference_observer: Any | None = None
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
                  deployment_proof: dict[str, Any] | None = None,
                  mode: str = "persistent"):
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
                "mode": mode,
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

    def _materialize_ci_checkout(self, submission, *,
                                 restore_authority: dict[str, Any] | None = None,
                                 ) -> dict[str, Any] | None:
        source_value = getattr(submission, "materialization_source_root", None)
        if submission.kind != "ci" or not isinstance(source_value, str):
            return None
        if self.deployment_root is None:
            raise WorkspaceIndexError(
                "workspace_materialization_unavailable",
                "CI cleanup requires a controller deployment root")
        source = Path(source_value).resolve(strict=False)
        checkout = Path(submission.project_root).resolve(strict=False)
        deployment_root = Path(self.deployment_root).resolve(strict=False)
        try:
            source.relative_to(deployment_root)
            checkout.relative_to(deployment_root)
        except ValueError as exc:
            raise WorkspaceIndexError(
                "workspace_path_escape",
                "CI materialization must stay inside deploy storage") from exc
        if source == checkout or source.parent != checkout.parent:
            raise WorkspaceIndexError(
                "workspace_materialization_unavailable",
                "CI source and disposable checkout must be distinct siblings")
        if restore_authority is not None:
            artifact = Path(str(restore_authority.get("artifact_locator")))
            artifact_digest = restore_authority.get("artifact_digest")
            artifact_root = self._repo().index_path.parent / "ci-materializations"
            try:
                artifact.resolve(strict=False).relative_to(
                    artifact_root.resolve(strict=False))
            except ValueError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_escape",
                    "retained CI materialization artifact escapes its owner root") from exc
            if (not isinstance(artifact_digest, str) or
                    not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest)):
                raise WorkspaceIndexError(
                    "workspace_materialization_unavailable",
                    "retained CI materialization artifact proof is incomplete")
            _restore_checkout(artifact, artifact_digest, checkout)
            artifact_size = restore_authority.get("artifact_size_bytes")
            if (isinstance(artifact_size, bool) or
                    not isinstance(artifact_size, int) or artifact_size < 0 or
                    artifact.stat().st_size != artifact_size):
                raise WorkspaceIndexError(
                    "workspace_materialization_unavailable",
                    "retained CI materialization size proof is incomplete")
            receipt_payload = {
                "schema": 1, "workspace_path": str(checkout),
                "source_identity": submission.source.identity,
                "history_mode": "retained-artifact", "hardlinked_files": 0,
                "copied_git_entries": 0, "fallback_reason": None,
                "source_mutation_check": "artifact-digest-verified",
                "lock": {"key": "retained-artifact", "acquired": True,
                         "released": True},
            }
        else:
            from sandbox.workspaces.checkout import (
                WorkspaceMaterializationError, materialize, plan_materialization,
            )
            source_identity = (submission.source.identity
                               if re.fullmatch(r"sha256:[0-9a-f]{64}",
                                               submission.source.identity)
                               else None)
            try:
                receipt = materialize(plan_materialization(
                    source, checkout, source_identity=source_identity,
                    workspace_label=submission.workspace_label,
                ))
            except WorkspaceMaterializationError as exc:
                raise WorkspaceIndexError(
                    "workspace_materialization_failed",
                    "controller CI materialization failed") from exc
            receipt_payload = receipt.to_dict()
        generation = uuid.uuid4().hex
        if restore_authority is None:
            artifact = self._repo().index_path.parent / "ci-materializations" / (
                generation + ".tar.gz")
            artifact_digest, artifact_size = _archive_checkout(checkout, artifact)
        authority = {
            "schema": 1,
            "owner": "controller-ci-materialization",
            "job_kind": "ci",
            "checkout_locator": str(checkout),
            "source_checkout_locator": str(source),
            "source_identity": submission.source.identity,
            "workspace_label": submission.workspace_label,
            "checkout_identity": _filesystem_identity(checkout),
            "receipt": receipt_payload,
            "generation": generation,
            "artifact_locator": str(artifact),
            "artifact_digest": artifact_digest,
            "artifact_size_bytes": artifact_size,
        }
        authority["digest"] = _digest_payload(authority)
        return authority

    def ensure_submission(self, submission, *,
                          expected_previous_authority_digest: str | None = None):
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
        checkout = str(Path(submission.project_root).resolve(strict=False))
        if existing is not None:
            # Supported reusable/index/legacy workspaces remain authoritative.
            # A job may reference them, but never upgrades them into disposable
            # cleanup authority.
            if existing.source != "ci-materialization":
                return existing
            if existing.lifecycle != "destroyed":
                return existing
            previous_authority = existing.metadata.get("ci_cleanup_authority")
            if (not isinstance(previous_authority, dict) or
                    expected_previous_authority_digest is None or
                    previous_authority.get("digest") !=
                    expected_previous_authority_digest or
                    previous_authority.get("digest") != _digest_payload({
                        key: value for key, value in previous_authority.items()
                        if key != "digest"
                    })):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "retry materialization authority changed")
            authority = self._materialize_ci_checkout(
                submission,
                restore_authority=(previous_authority
                                   if isinstance(previous_authority, dict)
                                   else None),
            )
            if authority is None:
                return existing
            metadata_path = Path(str(existing.path))
            metadata_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._write_json_atomic(metadata_path, {
                "label": existing.label, "target": "local",
                "namespace": existing.namespace,
                "mode": submission.workspace_mode,
                "path": str(metadata_path.parent),
                "project_identity": submission.project_identity,
                "workspace_id": existing.workspace_id,
            })
            return repo.revive_destroyed(existing.workspace_id, metadata={
                "checkout_locator": checkout,
                "checkout_locator_digest": "sha256:" + hashlib.sha256(
                    checkout.encode()).hexdigest(),
                "source_checkout_locator": authority["source_checkout_locator"],
                "source_checkout_locator_digest": "sha256:" + hashlib.sha256(
                    authority["source_checkout_locator"].encode()).hexdigest(),
                "ci_cleanup_authority": authority,
            })
        namespace = _durable_namespace(submission.project_identity)
        authority = self._materialize_ci_checkout(submission)
        proof = {
            "checkout_locator": checkout,
            "checkout_locator_digest": "sha256:" + hashlib.sha256(
                checkout.encode()).hexdigest(),
            **({
                "source_checkout_locator": authority["source_checkout_locator"],
                "source_checkout_locator_digest": "sha256:" + hashlib.sha256(
                    authority["source_checkout_locator"].encode()).hexdigest(),
                "ci_cleanup_authority": authority,
            } if authority is not None else {}),
        }
        try:
            record, _created = self._register(
                project_identity=submission.project_identity,
                label=submission.workspace_label, namespace=namespace,
                checkout_locator=checkout,
                source="ci-materialization" if authority is not None else "job-reference",
                deployment_proof=proof, mode=submission.workspace_mode,
            )
        except Exception as exc:
            if authority is not None:
                artifact = Path(authority["artifact_locator"])
                artifact_root = repo.index_path.parent / "ci-materializations"
                retired = False
                try:
                    artifact.resolve(strict=False).relative_to(
                        artifact_root.resolve(strict=False))
                    if (not artifact.is_symlink() and artifact.is_file() and
                            artifact.stat().st_size == authority["artifact_size_bytes"] and
                            _file_sha256(artifact) == authority["artifact_digest"]):
                        artifact.unlink()
                        retired = True
                except (OSError, ValueError):
                    raise WorkspaceIndexError(
                        "workspace_materialization_failed",
                        "unpublished materialization artifact could not be retired") from exc
                if not retired:
                    raise WorkspaceIndexError(
                        "workspace_materialization_failed",
                        "unpublished materialization artifact proof changed") from exc
            raise
        if self.resource_binding_resolver is not None:
            bindings = self.resource_binding_resolver(submission) or ()
            for binding in bindings:
                if not isinstance(binding, (list, tuple)) or len(binding) != 2:
                    raise WorkspaceIndexError(
                        "workspace_ownership_drift",
                        "workspace resource binding is invalid")
                repo.bind_resource(
                    record.workspace_id, str(binding[0]), str(binding[1]))
        return record

    def terminal_cleanup_context(self) -> dict[str, str] | None:
        """Return the owner-only paths needed by a detached supervisor."""
        if self.repository is None or self.deployment_root is None:
            return None
        return {
            "index_path": str(self.repository.index_path),
            "legacy_root": str(self.repository.legacy_root),
            "deployment_root": str(Path(self.deployment_root)),
        }

    def has_retained_materialization(self, job: dict) -> bool:
        workspace_id = job.get("workspace_id")
        if not isinstance(workspace_id, str):
            return False
        record = self._repo().get(workspace_id)
        if record is None or record.source != "ci-materialization":
            return False
        authority = record.metadata.get("ci_cleanup_authority")
        return (isinstance(authority, dict) and
                authority.get("digest") == job.get("workspace_authority_digest") and
                not record.metadata.get("ci_materialization_retired", False))

    def retire_terminal_materialization(self, job: dict) -> bool:
        """Retire the exact retained artifact, never a guessed generation."""
        if not self.has_retained_materialization(job):
            return False
        workspace_id = job["workspace_id"]
        repo = self._repo()
        with repo.operation_lock(workspace_id):
            record = repo.get(workspace_id)
            if record is None:
                raise WorkspaceIndexError(
                    "workspace_identity_ambiguous", "workspace artifact owner is unavailable")
            authority = record.metadata.get("ci_cleanup_authority")
            artifact = Path(str(authority.get("artifact_locator")))
            artifact_root = repo.index_path.parent / "ci-materializations"
            try:
                artifact.resolve(strict=False).relative_to(
                    artifact_root.resolve(strict=False))
            except ValueError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_escape", "workspace artifact escapes retention root") from exc
            expected_size = authority.get("artifact_size_bytes")
            if (artifact.is_symlink() or not artifact.is_file() or
                    isinstance(expected_size, bool) or
                    not isinstance(expected_size, int) or
                    artifact.stat().st_size != expected_size or
                    _file_sha256(artifact) != authority.get("artifact_digest")):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift", "workspace artifact proof changed")
            artifact.unlink()
            repo.mark_lifecycle(
                workspace_id, record.lifecycle, status=record.status,
                metadata={"ci_materialization_retired": True},
            )
            return True

    def release_terminal_job(self, job: dict, job_repository) -> dict[str, Any]:
        """Release one exact job-owned disposable checkout after terminal proof."""
        terminal = {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}
        if job.get("lifecycle") not in terminal:
            raise WorkspaceIndexError(
                "active_job_protected", "workspace cleanup requires a terminal job")
        policy = job.get("cleanup_policy")
        mode = job.get("workspace_mode")
        if job.get("kind") != "ci":
            job_repository.set_cleanup_state(job["job_id"], "retained")
            return {"ok": True, "status": "retained"}
        workspace_id = job.get("workspace_id")
        if not isinstance(workspace_id, str) or not re.fullmatch(
                r"ws_[0-9a-f]{32}", workspace_id):
            raise WorkspaceIndexError(
                "workspace_identity_ambiguous",
                "terminal job has no exact workspace identity")
        repo = self._repo()
        guard = repo.operation_lock(workspace_id)
        with guard:
            record = repo.get(workspace_id)
            if record is None:
                raise WorkspaceIndexError(
                    "workspace_identity_ambiguous",
                    "terminal workspace identity is unavailable")
            if record.lifecycle == "destroyed" and record.status == "destroyed":
                job_repository.set_cleanup_state(job["job_id"], "completed")
                return {"ok": True, "status": "already_released"}
            checkout = str(Path(str(job.get("project_root"))).resolve(strict=False))
            expected_digest = "sha256:" + hashlib.sha256(checkout.encode()).hexdigest()
            authority = record.metadata.get("ci_cleanup_authority")
            authority_digest = (authority.get("digest")
                                if isinstance(authority, dict) else None)
            authorized_policy = (
                mode in {"isolated", "ephemeral"}
                and (policy in {"always", "ephemeral"}
                     or policy == "on-success" and
                     job.get("lifecycle") == "succeeded")
            )
            if not authorized_policy or job.get("workspace_authority_digest") is None:
                job_repository.set_cleanup_state(job["job_id"], "retained")
                return {"ok": True, "status": "retained"}
            if (record.project_identity != job.get("project_identity") or
                    record.label != job.get("workspace_label") or
                    record.source != "ci-materialization" or
                    record.lifecycle != "ready" or record.status != "ready" or
                    record.metadata.get("checkout_locator") != checkout or
                    record.metadata.get("checkout_locator_digest") != expected_digest or
                    authority.get("owner") != "controller-ci-materialization" or
                    authority.get("job_kind") != "ci" or
                    authority.get("checkout_locator") != checkout or
                    authority.get("workspace_label") != record.label or
                    authority_digest != job.get("workspace_authority_digest") or
                    authority_digest != _digest_payload({
                        key: value for key, value in authority.items()
                        if key != "digest"
                    })):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "terminal job does not exactly own the indexed workspace")
            active = job_repository.connection.execute(
                "SELECT job_id FROM jobs WHERE workspace_id=? AND job_id<>? "
                "AND lifecycle IN ('accepted','queued','running','cancelling') LIMIT 1",
                (workspace_id, job["job_id"]),
            ).fetchone()
            if active is not None:
                raise WorkspaceIndexError(
                    "workspace_busy",
                    "another active job still owns the disposable workspace")
            lease = job_repository.connection.execute(
                "SELECT lease_id FROM workspace_leases WHERE project_identity=? "
                "AND workspace_label=? LIMIT 1",
                (job.get("project_identity"), job.get("workspace_label")),
            ).fetchone()
            if lease is not None:
                raise WorkspaceIndexError(
                    "workspace_busy", "workspace lease is still live")
            process = job_repository.snapshot(job["job_id"]).get("process") or {}
            for role, pid_key, identity_key in (
                    ("supervisor", "supervisor_pid", "supervisor_start_identity"),
                    ("child", "child_pid", "child_start_identity")):
                pid = process.get(pid_key)
                identity = process.get(identity_key)
                if not pid or not identity:
                    continue
                observed = capture_process_identity(int(pid))
                if observed is not None:
                    observed = ProcessIdentity(
                        observed.host_boot_id, observed.pid, observed.start_identity,
                        process.get("supervisor_nonce_hash") or "",
                        observed.process_group_id,
                    )
                expected = ProcessIdentity(
                    process.get("host_boot_id") or "", int(pid), identity,
                    process.get("supervisor_nonce_hash") or "",
                    process.get("child_pgid") if role == "child" else None,
                )
                if verify_process_identity(expected, observed):
                    if role == "supervisor" and int(pid) == os.getpid():
                        continue
                    raise WorkspaceIndexError(
                        "workspace_busy", f"recorded {role} process is still live")
            child_pgid = process.get("child_pgid")
            if child_pgid is not None and not _process_group_empty(int(child_pgid)):
                raise WorkspaceIndexError(
                    "workspace_busy",
                    "recorded child process group is not proven empty")
            child_cgroup = process.get("child_cgroup_path")
            if child_cgroup is not None and not _owned_cgroup_empty(child_cgroup):
                raise WorkspaceIndexError(
                    "workspace_busy", "recorded child cgroup is not proven empty")
            if self.deployment_root is None:
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "deployment root is unavailable for terminal cleanup")
            deployment_root = Path(self.deployment_root).resolve(strict=False)
            checkout_path = Path(checkout).resolve(strict=False)
            try:
                checkout_path.relative_to(deployment_root)
            except ValueError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_escape",
                    "terminal workspace escapes deploy storage") from exc
            if checkout_path == deployment_root or checkout_path.is_symlink():
                raise WorkspaceIndexError(
                    "workspace_path_unsafe",
                    "terminal workspace locator is unsafe")
            references = (
                self.cleanup_reference_observer(checkout_path, record)
                if self.cleanup_reference_observer is not None
                else _observe_cleanup_references(checkout_path)
            )
            if (not isinstance(references, dict) or
                    references.get("containers") != 0 or
                    references.get("mounts") != 0 or record.bindings):
                raise WorkspaceIndexError(
                    "workspace_busy",
                    "live container, mount, or binding absence is not proven")
            metadata_path = Path(record.path) if isinstance(record.path, str) else None
            legacy_root = repo.legacy_root.resolve(strict=False)
            if (metadata_path is None or metadata_path.name != "workspace.json" or
                    metadata_path.is_symlink() or metadata_path.parent.is_symlink()):
                raise WorkspaceIndexError(
                    "workspace_path_unsafe", "workspace metadata is unsafe")
            try:
                metadata_path.parent.resolve(strict=False).relative_to(legacy_root)
            except ValueError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_escape", "workspace metadata escapes its owner root") from exc
            try:
                metadata_payload = json.loads(
                    metadata_path.read_text(encoding="utf-8"))
                metadata_entries = tuple(metadata_path.parent.iterdir())
            except (OSError, UnicodeError, ValueError) as exc:
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "workspace metadata cannot prove exact ownership") from exc
            if (not isinstance(metadata_payload, dict) or
                    metadata_payload.get("workspace_id") != workspace_id or
                    metadata_payload.get("project_identity") != job.get("project_identity") or
                    metadata_payload.get("label") != job.get("workspace_label") or
                    metadata_payload.get("mode") != mode or
                    metadata_payload.get("path") != str(metadata_path.parent) or
                    metadata_entries != (metadata_path,)):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "workspace metadata directory is not an exact owned leaf")
            expected_identity = authority.get("checkout_identity")
            if (not isinstance(expected_identity, dict) or
                    _filesystem_identity(checkout_path) != expected_identity):
                raise WorkspaceIndexError(
                    "workspace_ownership_drift",
                    "terminal checkout filesystem identity changed")
            cleanup_root = deployment_root / ".sandbox-ci-cleanup"
            try:
                cleanup_root.mkdir(mode=0o700, exist_ok=True)
                cleanup_identity = os.stat(cleanup_root, follow_symlinks=False)
            except OSError as exc:
                raise WorkspaceIndexError(
                    "workspace_path_unsafe", "private cleanup root is unavailable") from exc
            if (not stat.S_ISDIR(cleanup_identity.st_mode) or
                    cleanup_identity.st_uid != os.getuid() or
                    stat.S_IMODE(cleanup_identity.st_mode) & 0o077):
                raise WorkspaceIndexError(
                    "workspace_path_unsafe", "private cleanup root is not owner-only")
            repo.mark_lifecycle(workspace_id, "destroying", status="destroying")
            try:
                if checkout_path.exists():
                    if not checkout_path.is_dir():
                        raise WorkspaceIndexError(
                            "workspace_ownership_drift",
                            "terminal workspace is not a directory")
                    directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                                       getattr(os, "O_NOFOLLOW", 0))
                    parent_fd = os.open(checkout_path.parent, directory_flags)
                    cleanup_fd = os.open(cleanup_root, directory_flags)
                    operation_name = uuid.uuid4().hex
                    os.mkdir(operation_name, mode=0o700, dir_fd=cleanup_fd)
                    operation_fd = os.open(operation_name, directory_flags,
                                           dir_fd=cleanup_fd)
                    owned_fd = None
                    try:
                        os.rename(
                            checkout_path.name, "owned",
                            src_dir_fd=parent_fd, dst_dir_fd=operation_fd,
                        )
                        owned_fd = os.open("owned", directory_flags,
                                           dir_fd=operation_fd)
                        observed = os.fstat(owned_fd)
                        if {"device": int(observed.st_dev),
                            "inode": int(observed.st_ino)} != expected_identity:
                            os.rename(
                                "owned", checkout_path.name,
                                src_dir_fd=operation_fd, dst_dir_fd=parent_fd,
                            )
                            raise WorkspaceIndexError(
                                "workspace_ownership_drift",
                                "quarantined checkout identity changed")
                        _remove_tree_fd(owned_fd)
                        os.rmdir("owned", dir_fd=operation_fd)
                    finally:
                        if owned_fd is not None:
                            os.close(owned_fd)
                        os.close(operation_fd)
                        os.rmdir(operation_name, dir_fd=cleanup_fd)
                        os.close(cleanup_fd)
                        os.close(parent_fd)
                metadata_path.unlink()
                metadata_path.parent.rmdir()
            except Exception:
                repo.mark_lifecycle(
                    workspace_id, "indeterminate", status="indeterminate")
                raise
            repo.mark_lifecycle(workspace_id, "destroyed", status="destroyed")
            job_repository.set_cleanup_state(job["job_id"], "completed")
            return {"ok": True, "status": "released"}

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


def finalize_terminal_workspace(job_repository, job_id: str,
                                context: dict[str, Any] | None, *,
                                workspace_service: WorkspaceService | None = None,
                                ) -> dict[str, Any]:
    """Run the shared fail-closed cleanup seam without changing job result truth."""
    job = job_repository.get(job_id)
    if job.get("cleanup_state") in {"completed", "retained"}:
        return {"ok": True, "status": job["cleanup_state"]}
    if not isinstance(context, dict) or set(context) != {
            "index_path", "legacy_root", "deployment_root"}:
        # Compatibility compositions without a workspace index cannot infer a
        # deletion target. Retention is the only safe terminal outcome.
        job_repository.set_cleanup_state(job_id, "retained")
        return {"ok": True, "status": "retained"}
    try:
        registry_path = Path(job_repository.path).resolve(strict=False)
        runtime_root = registry_path.parent.parent
        expected = {
            "index_path": runtime_root / "workspaces" / "index.sqlite3",
            "legacy_root": registry_path.parent / "workspaces",
            "deployment_root": runtime_root.parent / "deploy-src",
        }
        if any(Path(context[key]).resolve(strict=False) != value.resolve(strict=False)
               for key, value in expected.items()):
            raise WorkspaceIndexError(
                "workspace_ownership_drift",
                "terminal cleanup context does not match the job registry owner")
        if workspace_service is None:
            repository = WorkspaceRepository(
                context["index_path"], context["legacy_root"],
                job_index_reader=lambda: __import__(
                    "sandbox.jobs.registry", fromlist=["read_resource_index"]
                ).read_resource_index(job_repository.path),
            )
            service = WorkspaceService(
                None, repository=repository,
                deployment_root=Path(context["deployment_root"]),
            )
        else:
            service = workspace_service
        result = service.release_terminal_job(job, job_repository)
        job_repository.append_event(
            job_id, "workspace_cleanup", {"status": result["status"]})
        return result
    except Exception as exc:
        job_repository.set_cleanup_state(job_id, "failed")
        code = getattr(exc, "code", "workspace_cleanup_failed")
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code):
            code = "workspace_cleanup_failed"
        job_repository.append_event(
            job_id, "workspace_cleanup", {"status": "failed", "code": code})
        return {"ok": False, "status": "failed", "code": code}
