"""Owner-only SQLite workspace metadata repository.

The repository is deliberately independent from the legacy filesystem.  It
uses legacy bytes only to compute a digest while planning/applying metadata;
the bytes and directory layout are never changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .migration import (
    LegacyScan,
    correlate,
    items_from_scan,
    normalize_evidence,
    plan_digest,
    scan_legacy,
)
from .maintenance import BaseMaintenanceBusy, base_maintenance_lock as _base_maintenance_lock
from .models import (
    LegacyWorkspace,
    MigrationItem,
    MigrationPlan,
    ResourceBinding,
    WorkspaceRecord,
)


SCHEMA_VERSION = 1
DEFAULT_PLAN_TTL_SECONDS = 900
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}$")
_LOCATOR_DIGESTS = (
    ("checkout_locator", "checkout_locator_digest"),
    ("source_checkout_locator", "source_checkout_locator_digest"),
)
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_WRITE_LOCK = threading.RLock()
_LIFECYCLES = frozenset({
    "provisioning", "ready", "resetting", "destroying", "destroyed",
    "indeterminate",
})
_LIFECYCLE_TRANSITIONS = {
    "provisioning": frozenset({"ready", "indeterminate"}),
    "ready": frozenset({"resetting", "destroying", "indeterminate"}),
    "resetting": frozenset({"ready", "indeterminate"}),
    "destroying": frozenset({"destroyed", "indeterminate"}),
    "destroyed": frozenset(),
    "indeterminate": frozenset({"ready", "destroying", "destroyed"}),
}


class _MaintenanceConnection(sqlite3.Connection):
    """Release the repository's shared home lock when SQLite closes."""

    _maintenance_exit: Callable[[], None] | None = None

    def close(self) -> None:
        try:
            super().close()
        finally:
            release = self._maintenance_exit
            self._maintenance_exit = None
            if release is not None:
                release()


class WorkspaceIndexError(RuntimeError):
    """Stable, machine-readable repository failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class WorkspaceNotFoundError(WorkspaceIndexError):
    def __init__(self, workspace_id: str) -> None:
        super().__init__("workspace_not_found", f"workspace {workspace_id!r} was not found",
                         workspace_id=workspace_id)


class AliasCollisionError(WorkspaceIndexError):
    def __init__(self, alias: str) -> None:
        super().__init__("workspace_alias_collision", f"workspace alias {alias!r} is already owned",
                         alias=alias)


class MigrationStaleError(WorkspaceIndexError):
    def __init__(self, reason: str = "migration plan is stale") -> None:
        super().__init__("workspace_migration_plan_stale", reason)


def _locator_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_path(value: str | Path, label: str) -> Path:
    """Resolve a migration base without following a caller-owned locator."""
    if not isinstance(value, (str, Path)):
        raise WorkspaceIndexError("workspace_locator_invalid", f"{label} is invalid")
    try:
        result = Path(value).expanduser()
    except (TypeError, ValueError, OSError) as exc:
        raise WorkspaceIndexError("workspace_locator_invalid", f"{label} is invalid") from exc
    if not result.is_absolute() or "\x00" in str(result):
        raise WorkspaceIndexError("workspace_locator_invalid", f"{label} is invalid")
    # Keep the lexical absolute spelling.  macOS exposes ``/var`` as a
    # symlink to ``/private/var``; resolving the migration base here would
    # make an index row recorded with the former spelling look external even
    # though both paths identify the same managed tree.  Callers that inspect
    # destination safety still resolve the candidate target independently.
    return result.absolute()


def _relative_locator(value: str, base: Path, label: str) -> tuple[Path, Path] | None:
    """Return ``(relative, lexical)`` when *value* is under *base*.

    The lexical form is intentional.  The old base may already have been
    removed when finalization runs, and resolving an old path would make a
    stale locator look external.  ``..`` is rejected before it can escape the
    managed base.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorkspaceIndexError("workspace_locator_invalid", f"{label} is invalid")
    try:
        raw = Path(value).expanduser()
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        raise WorkspaceIndexError(
            "workspace_locator_invalid", f"{label} is invalid") from exc
    if not raw.is_absolute():
        return None
    try:
        relative = raw.relative_to(base)
    except ValueError:
        # A managed locator may have been written before a platform alias was
        # canonicalized (notably ``/var`` versus ``/private/var`` on macOS).
        # Compare canonical forms only as a fallback; retain the relative
        # spelling for the destination so the index remains deterministic.
        try:
            relative = raw.resolve(strict=False).relative_to(base.resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            return None
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise WorkspaceIndexError(
            "workspace_locator_escape",
            f"{label} escapes the managed migration base",
        )
    return relative, raw


def _destination_target(base: Path, relative: Path, label: str) -> Path:
    """Validate a relocated target before any SQLite row is changed."""
    target = base / relative
    # A destination target is managed state, not an arbitrary path supplied by
    # a workspace record.  Check every component so a symlinked parent cannot
    # redirect a supposedly safe relative locator outside the new base.
    cursor = base
    try:
        if cursor.is_symlink():
            raise WorkspaceIndexError(
                "workspace_locator_symlink",
                f"{label} destination base is a symlink",
            )
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise WorkspaceIndexError(
                    "workspace_locator_symlink",
                    f"{label} destination is a symlink",
                )
    except OSError as exc:
        raise WorkspaceIndexError(
            "workspace_locator_unavailable",
            f"{label} destination could not be inspected",
        ) from exc
    try:
        resolved_base = base.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
        resolved_target.relative_to(resolved_base)
    except (OSError, RuntimeError, ValueError) as exc:
        raise WorkspaceIndexError(
            "workspace_locator_symlink",
            f"{label} destination escapes the managed base",
        ) from exc
    if not target.exists():
        raise WorkspaceIndexError(
            "workspace_locator_missing",
            f"{label} destination is missing",
        )
    return target


def _rebase_locator(
    value: str,
    source_base: Path,
    destination_base: Path,
    label: str,
) -> tuple[str, bool, bool]:
    """Rebase one absolute managed locator, returning value/changed/managed."""
    source = _relative_locator(value, source_base, label)
    if source is not None:
        relative, _raw = source
        target = _destination_target(destination_base, relative, label)
        return str(target), True, True
    destination = _relative_locator(value, destination_base, label)
    if destination is not None:
        relative, _raw = destination
        target = _destination_target(destination_base, relative, label)
        return str(target), False, True
    # Locators outside SANDBOX_HOME are deployment-owned or otherwise external
    # state.  Relocation must leave them byte-for-byte unchanged.
    return value, False, False


def _utc(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise TypeError("clock must return datetime or ISO timestamp")


def _timestamp(clock: Callable[[], Any] | None) -> str:
    now = _utc(clock() if callable(clock) else None)
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"))


def _migration_source_key(item: MigrationItem) -> str:
    """Stable per-leaf key; content digests alone collide across workspaces."""
    return hashlib.sha256(f"{item.path}\0{item.digest}".encode("utf-8")).hexdigest()


def _safe(value: Any, label: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value) or len(value.encode()) > 512:
        raise ValueError(f"{label} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _name(value: str, label: str) -> str:
    _safe(value, label)
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _legacy_alias(item: MigrationItem) -> str:
    """Return the stable alias retained for an adopted legacy leaf."""
    alias = f"legacy:{item.namespace}:{item.label}"
    try:
        _safe(alias, "legacy workspace alias")
        if not _SAFE_ALIAS.fullmatch(alias):
            raise ValueError("legacy alias is invalid")
        return alias
    except ValueError as exc:
        raise WorkspaceIndexError(
            "workspace_alias_collision",
            "legacy workspace alias cannot be represented safely",
            alias=alias[:128],
        ) from exc


def _default_index_path() -> Path:
    home = Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()
    return home / "runtime" / "workspaces" / "index.sqlite3"


def _default_legacy_root(index_path: Path) -> Path:
    # index: runtime/workspaces/index.sqlite3; legacy: runtime/jobs/workspaces
    return index_path.parent.parent / "jobs" / "workspaces"


class WorkspaceRepository:
    """SQLite-backed durable workspace metadata and migration boundary."""

    def __init__(self, index_path: str | Path | None = None,
                 legacy_root: str | Path | None = None,
                 job_index_reader: Callable[..., Any] | Any | None = None,
                 clock: Callable[[], Any] | None = None,
                 *, plan_ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS) -> None:
        self.index_path = Path(index_path).expanduser().absolute() if index_path is not None else _default_index_path()
        self.legacy_root = (Path(legacy_root).expanduser().absolute() if legacy_root is not None
                            else _default_legacy_root(self.index_path))
        self.job_index_reader = job_index_reader
        self.clock = clock
        if isinstance(plan_ttl_seconds, bool) or not isinstance(plan_ttl_seconds, int) or plan_ttl_seconds <= 0:
            raise ValueError("plan_ttl_seconds must be a positive integer")
        self.plan_ttl_seconds = plan_ttl_seconds
        self.initialize()

    # ---- SQLite lifecycle -------------------------------------------------
    def _maintenance_base(self) -> Path:
        # index: <base>/runtime/workspaces/index.sqlite3
        return self.index_path.parent.parent.parent

    @contextmanager
    def _shared_maintenance_lock(self):
        try:
            with _base_maintenance_lock(self._maintenance_base(), exclusive=False):
                yield
        except (BaseMaintenanceBusy, OSError) as exc:
            raise WorkspaceIndexError(
                "workspace_busy",
                "workspace repository is unavailable while home maintenance is active",
            ) from exc

    def _prepare_parent(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.index_path.parent.chmod(0o700)
        except OSError:
            pass
        if self.index_path.is_symlink():
            raise WorkspaceIndexError("index_symlink", "workspace index must not be a symlink")

    def _connect(self) -> sqlite3.Connection:
        guard = self._shared_maintenance_lock()
        guard.__enter__()
        connection: _MaintenanceConnection | None = None
        try:
            self._prepare_parent()
            connection = sqlite3.connect(
                self.index_path, timeout=5.0, isolation_level=None,
                factory=_MaintenanceConnection,
            )
            connection._maintenance_exit = lambda: guard.__exit__(None, None, None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                self.index_path.chmod(0o600)
            except OSError:
                pass
        except Exception:
            if connection is not None:
                connection.close()
            else:
                guard.__exit__(*sys.exc_info())
            raise
        return connection

    def _connect_read_only(self) -> sqlite3.Connection | None:
        """Open an existing index in SQLite read-only mode without creating it."""
        guard = self._shared_maintenance_lock()
        guard.__enter__()
        connection: _MaintenanceConnection | None = None
        try:
            if not self.index_path.is_file() or self.index_path.is_symlink():
                guard.__exit__(None, None, None)
                return None
            connection = sqlite3.connect(
                f"{self.index_path.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
                isolation_level=None,
                factory=_MaintenanceConnection,
            )
            connection._maintenance_exit = lambda: guard.__exit__(None, None, None)
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            else:
                guard.__exit__(*sys.exc_info())
            raise WorkspaceIndexError(
                "projection_unavailable",
                "workspace ownership index could not be opened read-only",
            ) from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _migration_guard(self):
        """Serialize rescan-and-commit across independent controller processes."""
        with self.operation_lock("workspace-migration"):
            yield

    @contextmanager
    def operation_lock(
        self,
        operation: str = "workspace-migration",
        *,
        timeout_seconds: float = 5.0,
    ):
        """Acquire a per-operation owner lock across controller processes."""
        _name(operation, "workspace operation")
        if (isinstance(timeout_seconds, bool) or
                not isinstance(timeout_seconds, (int, float)) or
                timeout_seconds < 0):
            raise ValueError("timeout_seconds must be a non-negative number")
        import fcntl

        self._prepare_parent()
        lock_digest = hashlib.sha256(operation.encode("utf-8")).hexdigest()[:24]
        lock_path = self.index_path.parent / f".operation-{lock_digest}.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            deadline = time.monotonic() + float(timeout_seconds)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise WorkspaceIndexError(
                            "workspace_busy",
                            "workspace operation lock is already held",
                        ) from exc
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    migration_lock = operation_lock

    @classmethod
    def read_only_projection(
        cls,
        index_path: str | Path | None = None,
        legacy_root: str | Path | None = None,
        job_index_reader: Callable[..., Any] | Any | None = None,
    ) -> dict[str, Any]:
        """Project ownership without running the mutating constructor.

        This classmethod is intentionally a separate composition seam for
        status/diagnostic callers.  It never calls ``initialize`` and does not
        create the index or its parent when they are absent.
        """
        repository = cls.__new__(cls)
        repository.index_path = (
            Path(index_path).expanduser().absolute()
            if index_path is not None else _default_index_path()
        )
        repository.legacy_root = (
            Path(legacy_root).expanduser().absolute()
            if legacy_root is not None else _default_legacy_root(repository.index_path)
        )
        repository.job_index_reader = job_index_reader
        repository.clock = None
        repository.plan_ttl_seconds = DEFAULT_PLAN_TTL_SECONDS
        return repository.ownership_projection()

    @classmethod
    def rebase_home_locators(
        cls,
        index_path: str | Path,
        source_base: str | Path,
        destination_base: str | Path,
    ) -> dict[str, Any]:
        """Serialize an existing-index rebase with repository users."""
        index = Path(index_path).expanduser().absolute()
        # Preserve the no-state behavior for a clean clone: no lock file or
        # index parent is created when there is nothing to rebase.
        if not index.exists() and not index.is_symlink():
            return cls._rebase_home_locators_locked(
                index, source_base, destination_base)
        destination = _base_path(destination_base, "destination base")
        try:
            with _base_maintenance_lock(destination, exclusive=False):
                return cls._rebase_home_locators_locked(
                    index, source_base, destination)
        except (BaseMaintenanceBusy, OSError) as exc:
            raise WorkspaceIndexError(
                "workspace_busy",
                "workspace repository is unavailable while home maintenance is active",
            ) from exc

    @classmethod
    def _rebase_home_locators_locked(
        cls,
        index_path: str | Path,
        source_base: str | Path,
        destination_base: str | Path,
    ) -> dict[str, Any]:
        """Atomically rebase managed workspace locators after a home move.

        This is deliberately an existing-index-only operation.  It never
        constructs a repository (which would create an absent index), never
        touches legacy ``workspace.json`` bytes, and never changes ownership,
        aliases, bindings, migration receipts, or index generation.  The
        caller supplies the source base from the verified migration journal;
        an index row is changed only when its locator is under that exact base.
        """
        source = _base_path(source_base, "source base")
        destination = _base_path(destination_base, "destination base")
        index = Path(index_path).expanduser().absolute()
        if source == destination:
            return {
                "ok": True,
                "metadata_only": True,
                "index_present": index.is_file() and not index.is_symlink(),
                "rows_rebased": 0,
                "locators_rebased": 0,
                "already_rebased": True,
                "index_generation": None,
            }
        if index.is_symlink():
            raise WorkspaceIndexError(
                "index_symlink", "workspace index must not be a symlink")
        if not index.exists():
            # No parent directory or SQLite file is created for a clean clone
            # or an index-less legacy migration.
            return {
                "ok": True,
                "metadata_only": True,
                "index_present": False,
                "rows_rebased": 0,
                "locators_rebased": 0,
                "already_rebased": True,
                "index_generation": None,
            }
        if not index.is_file():
            raise WorkspaceIndexError(
                "workspace_index_invalid", "workspace index is not a regular file")

        try:
            # ``mode=rw`` is important: a race after ``is_file`` must not turn
            # this metadata-only finalizer into an index creator.
            connection = sqlite3.connect(
                f"{index.as_uri()}?mode=rw", uri=True,
                timeout=5.0, isolation_level=None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise WorkspaceIndexError(
                "workspace_index_unavailable",
                "workspace ownership index could not be opened",
            ) from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            with _WRITE_LOCK:
                connection.execute("BEGIN IMMEDIATE")
                table = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name='workspaces'"
                ).fetchone()
                if table is None or table[0] != "table":
                    raise WorkspaceIndexError(
                        "workspace_index_invalid",
                        "workspace index has no workspaces table",
                    )
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(workspaces)")
                }
                if not {"workspace_id", "path", "metadata_json"}.issubset(columns):
                    raise WorkspaceIndexError(
                        "workspace_index_invalid",
                        "workspace index is missing locator columns",
                    )
                generation_row = connection.execute(
                    "SELECT value FROM workspace_meta WHERE key='generation'"
                ).fetchone()
                if generation_row is None:
                    raise WorkspaceIndexError(
                        "workspace_index_invalid",
                        "workspace index generation is unavailable",
                    )
                try:
                    generation = int(generation_row[0])
                except (TypeError, ValueError) as exc:
                    raise WorkspaceIndexError(
                        "workspace_index_invalid",
                        "workspace index generation is invalid",
                    ) from exc

                rows = connection.execute(
                    "SELECT workspace_id,path,metadata_json FROM workspaces "
                    "ORDER BY workspace_id"
                ).fetchall()
                planned: list[tuple[str, str | None, str, int]] = []
                target_owners: dict[str, str] = {}
                for row in rows:
                    workspace_id = row["workspace_id"]
                    old_path = row["path"]
                    new_path = old_path
                    path_changed = False
                    if old_path is not None:
                        if not isinstance(old_path, str):
                            raise WorkspaceIndexError(
                                "workspace_locator_invalid",
                                "workspace path is invalid",
                            )
                        new_path, path_changed, _managed = _rebase_locator(
                            old_path, source, destination, "workspace path")
                    try:
                        metadata = json.loads(row["metadata_json"] or "{}")
                    except (TypeError, ValueError) as exc:
                        raise WorkspaceIndexError(
                            "workspace_metadata_invalid",
                            "workspace metadata is not valid JSON",
                        ) from exc
                    if not isinstance(metadata, dict):
                        raise WorkspaceIndexError(
                            "workspace_metadata_invalid",
                            "workspace metadata must be an object",
                        )
                    rebased_metadata = dict(metadata)
                    locator_changes = int(path_changed)
                    for locator_key, digest_key in _LOCATOR_DIGESTS:
                        locator_present = locator_key in metadata
                        digest_present = digest_key in metadata
                        if digest_present and not locator_present:
                            raise WorkspaceIndexError(
                                "workspace_metadata_inconsistent",
                                f"{digest_key} has no matching locator",
                            )
                        if not locator_present:
                            continue
                        locator = metadata[locator_key]
                        if locator is None and not digest_present:
                            continue
                        if not isinstance(locator, str) or not locator:
                            raise WorkspaceIndexError(
                                "workspace_locator_invalid",
                                f"{locator_key} is invalid",
                            )
                        digest = metadata.get(digest_key)
                        if digest_present:
                            if not isinstance(digest, str) or not _SHA256_DIGEST.fullmatch(digest):
                                raise WorkspaceIndexError(
                                    "workspace_metadata_inconsistent",
                                    f"{digest_key} is invalid",
                                )
                            if digest != _locator_digest(locator):
                                raise WorkspaceIndexError(
                                    "workspace_metadata_inconsistent",
                                    f"{digest_key} does not match {locator_key}",
                                )
                        rebased, changed, _managed = _rebase_locator(
                            locator, source, destination, locator_key)
                        if changed:
                            rebased_metadata[locator_key] = rebased
                            if digest_present:
                                rebased_metadata[digest_key] = _locator_digest(rebased)
                            locator_changes += 1
                    if new_path is not None:
                        # The UNIQUE index normally catches this, but checking
                        # all rows before the first UPDATE gives a stable
                        # conflict and guarantees all-or-nothing behavior.
                        owner = target_owners.get(new_path)
                        if owner is not None and owner != workspace_id:
                            raise WorkspaceIndexError(
                                "workspace_locator_conflict",
                                "relocated workspace paths conflict",
                            )
                        target_owners[new_path] = workspace_id
                    metadata_changed = rebased_metadata != metadata
                    metadata_json = (
                        _json(rebased_metadata)
                        if metadata_changed else row["metadata_json"]
                    )
                    if path_changed or metadata_json != row["metadata_json"]:
                        planned.append((workspace_id, new_path, metadata_json, locator_changes))

                # No mutation occurs until every row, destination target, and
                # related digest has passed validation above.
                locator_count = 0
                for workspace_id, new_path, metadata_json, locator_changes in planned:
                    connection.execute(
                        "UPDATE workspaces SET path=?,metadata_json=? WHERE workspace_id=?",
                        (new_path, metadata_json, workspace_id),
                    )
                    locator_count += locator_changes
                connection.execute("COMMIT")
                return {
                    "ok": True,
                    "metadata_only": True,
                    "index_present": True,
                    "rows_rebased": len(planned),
                    "locators_rebased": locator_count,
                    "already_rebased": not planned,
                    "index_generation": generation,
                }
        except WorkspaceIndexError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise WorkspaceIndexError(
                "workspace_index_invalid",
                "workspace index locator rebase failed",
            ) from exc
        finally:
            connection.close()

    @classmethod
    def checkpoint_for_relocation(cls, index_path: str | Path) -> None:
        """Flush a real WAL index before its containing runtime tree is copied.

        Non-SQLite bytes remain ordinary runtime data for backwards-compatible
        relocation tests. A file bearing SQLite's header, however, must be
        checkpointed under the same shared base lock used by all repository
        connections; a busy or malformed index stops before its source moves.
        """
        index = Path(index_path).expanduser().absolute()
        if not index.exists():
            return
        if index.is_symlink() or not index.is_file():
            raise WorkspaceIndexError(
                "workspace_index_invalid", "workspace index is not a regular file")
        try:
            with index.open("rb") as handle:
                is_sqlite = handle.read(16) == b"SQLite format 3\x00"
        except OSError as exc:
            raise WorkspaceIndexError(
                "workspace_index_unavailable", "workspace index could not be read") from exc
        if not is_sqlite:
            return
        base = index.parent.parent.parent
        try:
            with _base_maintenance_lock(base, exclusive=False):
                connection = sqlite3.connect(
                    f"{index.as_uri()}?mode=rw", uri=True,
                    timeout=0.0, isolation_level=None,
                )
                try:
                    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if row is None or int(row[0]) != 0:
                        raise WorkspaceIndexError(
                            "workspace_index_unavailable",
                            "workspace index WAL checkpoint is busy",
                        )
                finally:
                    connection.close()
        except WorkspaceIndexError:
            raise
        except (BaseMaintenanceBusy, OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise WorkspaceIndexError(
                "workspace_index_unavailable",
                "workspace index could not be checkpointed",
            ) from exc

    def initialize(self) -> int:
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Upgrade the v0 workspaces table before creating any indexes
                # which refer to v1 columns.  Every DDL statement is executed
                # individually so SQLite keeps the whole upgrade in one
                # rollback-able transaction (``executescript`` would commit
                # before running its script).
                table = connection.execute(
                    "SELECT type FROM sqlite_master WHERE name='workspaces'"
                ).fetchone()
                if table is not None and table[0] != "table":
                    raise WorkspaceIndexError("schema_invalid", "workspaces must be a table")
                if table is None:
                    connection.execute(
                        "CREATE TABLE workspaces ("
                        "workspace_id TEXT PRIMARY KEY, project_identity TEXT, label TEXT NOT NULL, "
                        "namespace TEXT, path TEXT, lifecycle TEXT NOT NULL DEFAULT 'ready', "
                        "status TEXT NOT NULL DEFAULT 'ready', source TEXT NOT NULL DEFAULT 'index', "
                        "metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL)"
                    )
                else:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(workspaces)")}
                    if not {"workspace_id", "label"}.issubset(columns):
                        raise WorkspaceIndexError(
                            "schema_invalid",
                            "v0 workspaces table is missing workspace_id or label",
                        )
                    for name, definition in (
                        ("project_identity", "TEXT"), ("namespace", "TEXT"), ("path", "TEXT"),
                        ("lifecycle", "TEXT NOT NULL DEFAULT 'ready'"),
                        ("status", "TEXT NOT NULL DEFAULT 'ready'"),
                        ("source", "TEXT NOT NULL DEFAULT 'index'"),
                        ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                        ("created_at", "TEXT NOT NULL DEFAULT ''"),
                        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
                    ):
                        if name not in columns:
                            connection.execute(f"ALTER TABLE workspaces ADD COLUMN {name} {definition}")

                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_meta ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_aliases ("
                    "alias TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE, "
                    "created_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_bindings ("
                    "binding_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE, "
                    "resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'owned', "
                    "metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, "
                    "UNIQUE(resource_type, resource_id))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_audit ("
                    "event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, workspace_id TEXT, "
                    "payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_migrations ("
                    "source_digest TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT, workspace_id TEXT, "
                    "project_identity TEXT, namespace_digest TEXT NOT NULL, label TEXT NOT NULL, "
                    "first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_plans ("
                    "plan_id TEXT PRIMARY KEY, digest TEXT NOT NULL, generation INTEGER NOT NULL, "
                    "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS workspace_plan_applications ("
                    "plan_id TEXT PRIMARY KEY REFERENCES workspace_plans(plan_id), applied_at TEXT NOT NULL, "
                    "generation INTEGER NOT NULL, result_json TEXT NOT NULL)"
                )
                # Index creation is intentionally after the v0 column upgrade;
                # duplicate legacy paths fail the transaction rather than
                # leaving a partially upgraded index.
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS workspace_project_label "
                    "ON workspaces(project_identity, label) WHERE project_identity IS NOT NULL"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS workspace_path_unique ON workspaces(path) WHERE path IS NOT NULL"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS workspace_project ON workspaces(project_identity, updated_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS workspace_namespace ON workspaces(namespace, label)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS workspace_alias_owner ON workspace_aliases(workspace_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS workspace_binding_owner ON workspace_bindings(workspace_id)"
                )
                connection.execute("INSERT OR IGNORE INTO workspace_meta(key,value) VALUES('schema_version',?)",
                                   (str(SCHEMA_VERSION),))
                connection.execute("INSERT OR IGNORE INTO workspace_meta(key,value) VALUES('generation','0')")
                connection.execute("UPDATE workspace_meta SET value=? WHERE key='schema_version'",
                                   (str(SCHEMA_VERSION),))
                # Normalize pre-release index rows into the public lifecycle
                # enum. Migration decisions such as ``adoptable`` remain in
                # workspace_migrations/plans and never act as readiness.
                connection.execute(
                    "UPDATE workspaces SET lifecycle='ready',status='ready' "
                    "WHERE lifecycle='active' AND status IN ('adoptable','ready')"
                )
                connection.execute(
                    "UPDATE workspaces SET lifecycle='destroyed',status='destroyed' "
                    "WHERE lifecycle='tombstoned' OR status='tombstoned'"
                )
                lifecycle_placeholders = ",".join("?" for _ in _LIFECYCLES)
                connection.execute(
                    f"UPDATE workspaces SET lifecycle='indeterminate',status='indeterminate' "
                    f"WHERE lifecycle NOT IN ({lifecycle_placeholders})",
                    tuple(sorted(_LIFECYCLES)),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return SCHEMA_VERSION

    def schema_generation(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("SELECT value FROM workspace_meta WHERE key='generation'").fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def migration_summary(self, workspace_id: str, *, limit: int = 8) -> dict[str, Any]:
        """Return a bounded, path-free decision summary for public responses."""
        _name(workspace_id, "workspace id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 32:
            raise ValueError("migration summary limit must be between 1 and 32")
        connection = self._connect_read_only()
        if connection is None:
            return {
                "decision": None, "source_digest": None, "reason": None,
                "observed_at": None, "counts": {}, "total": 0,
                "records": [], "truncated": False,
            }
        try:
            rows = connection.execute(
                "SELECT source_digest,decision,reason,last_observed_at "
                "FROM workspace_migrations WHERE workspace_id=? "
                "ORDER BY last_observed_at DESC,source_digest LIMIT ?",
                (workspace_id, limit + 1),
            ).fetchall()
            counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT decision,COUNT(*) FROM workspace_migrations "
                    "WHERE workspace_id=? GROUP BY decision ORDER BY decision",
                    (workspace_id,),
                ).fetchall()
            }
        finally:
            connection.close()
        visible = rows[:limit]
        records = [{
            "decision": row[1],
            "source_digest": "sha256:" + row[0],
            "reason": row[2],
            "observed_at": row[3],
        } for row in visible]
        latest = records[0] if records else {}
        return {
            "decision": latest.get("decision"),
            "source_digest": latest.get("source_digest"),
            "reason": latest.get("reason"),
            "observed_at": latest.get("observed_at"),
            "counts": counts,
            "total": sum(counts.values()),
            "records": records,
            "truncated": len(rows) > limit,
        }

    def _bump_generation(self, connection: sqlite3.Connection) -> int:
        current = int(connection.execute("SELECT value FROM workspace_meta WHERE key='generation'").fetchone()[0])
        current += 1
        connection.execute("UPDATE workspace_meta SET value=? WHERE key='generation'", (str(current),))
        return current

    def _ensure_legacy_alias(
        self,
        connection: sqlite3.Connection,
        workspace_id: str,
        item: MigrationItem,
        created_at: str,
    ) -> tuple[str, bool]:
        """Insert the legacy alias or prove it already belongs to this row."""
        alias = _legacy_alias(item)
        owner = connection.execute(
            "SELECT workspace_id FROM workspace_aliases WHERE alias=?", (alias,)
        ).fetchone()
        if owner is not None and owner[0] != workspace_id:
            raise AliasCollisionError(alias)
        added = False
        if owner is None:
            try:
                connection.execute(
                    "INSERT INTO workspace_aliases(alias,workspace_id,created_at) VALUES(?,?,?)",
                    (alias, workspace_id, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise AliasCollisionError(alias) from exc
            added = True
        return alias, added

    # ---- Row conversion and lookups --------------------------------------
    def _record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> WorkspaceRecord:
        aliases = [item[0] for item in connection.execute(
            "SELECT alias FROM workspace_aliases WHERE workspace_id=? ORDER BY alias", (row["workspace_id"],))]
        bindings = [dict(item) for item in connection.execute(
            "SELECT resource_type,resource_id,status,metadata_json FROM workspace_bindings "
            "WHERE workspace_id=? ORDER BY resource_type,resource_id", (row["workspace_id"],))]
        for binding in bindings:
            try:
                metadata = json.loads(binding.pop("metadata_json") or "{}")
            except (ValueError, TypeError):
                metadata = {}
            binding["metadata"] = metadata if isinstance(metadata, dict) else {}
            binding["workspace_id"] = row["workspace_id"]
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        return WorkspaceRecord(
            workspace_id=row["workspace_id"], label=row["label"],
            project_identity=row["project_identity"], namespace=row["namespace"],
            path=row["path"], lifecycle=row["lifecycle"], status=row["status"],
            source=row["source"], aliases=tuple(aliases), bindings=tuple(bindings),
            metadata=metadata if isinstance(metadata, dict) else {},
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def _row_for(self, connection: sqlite3.Connection, workspace_id: str) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM workspaces WHERE workspace_id=?", (workspace_id,)).fetchone()

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        connection = self._connect()
        try:
            row = self._row_for(connection, workspace_id)
            return self._record(connection, row) if row else None
        finally:
            connection.close()

    def get_record(self, workspace_id: str | None = None, *, project_identity: str | None = None,
                   label: str | None = None) -> WorkspaceRecord | None:
        if workspace_id is not None:
            return self.get(workspace_id)
        if project_identity is None or label is None:
            raise ValueError("workspace_id or project_identity+label is required")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE project_identity=? AND label=?",
                (project_identity, label)).fetchone()
            return self._record(connection, row) if row else None
        finally:
            connection.close()

    def find(self, project_identity: str, label: str) -> WorkspaceRecord | None:
        return self.get_record(project_identity=project_identity, label=label)

    # ---- New metadata writes ---------------------------------------------
    def register(self, project_identity: str | None, label: str, *, namespace: str | None = None,
                 path: str | None = None, workspace_id: str | None = None,
                 lifecycle: str = "ready", status: str = "ready", source: str = "index",
                 aliases: Iterable[str] = (), metadata: Mapping[str, Any] | None = None) -> WorkspaceRecord:
        if project_identity is not None:
            _safe(project_identity, "project_identity")
        _name(label, "workspace label")
        if namespace is not None:
            _safe(namespace, "namespace")
        if path is not None:
            _safe(path, "workspace path")
        if lifecycle not in _LIFECYCLES:
            raise WorkspaceIndexError(
                "workspace_lifecycle_invalid", "workspace lifecycle is invalid")
        if status not in _LIFECYCLES or status != lifecycle:
            raise WorkspaceIndexError(
                "workspace_lifecycle_invalid",
                "workspace status must match its lifecycle")
        workspace_id = workspace_id or "ws_" + uuid.uuid4().hex
        _name(workspace_id, "workspace id")
        now = _timestamp(self.clock)
        alias_values = tuple(dict.fromkeys(aliases))
        for alias in alias_values:
            _name(alias, "workspace alias")
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO workspaces(workspace_id,project_identity,label,namespace,path,lifecycle,status,source,metadata_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (workspace_id, project_identity, label, namespace, path, lifecycle, status, source, _json(metadata), now, now))
                for alias in alias_values:
                    connection.execute("INSERT INTO workspace_aliases(alias,workspace_id,created_at) VALUES(?,?,?)",
                                       (alias, workspace_id, now))
                self._bump_generation(connection)
                connection.execute("INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                   ("workspace_registered", workspace_id, _json({"status": status}), now))
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                message = str(exc)
                if "workspace_project_label" in message or "project_identity" in message:
                    raise WorkspaceIndexError("workspace_collision", "project identity and label already exist") from exc
                if "alias" in message:
                    raise AliasCollisionError(alias_values[0] if alias_values else "") from exc
                raise WorkspaceIndexError("workspace_collision", "workspace identity already exists") from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return self.get(workspace_id)  # type: ignore[return-value]

    def create_ready(self, project_identity: str, label: str, **kwargs: Any) -> WorkspaceRecord:
        kwargs.setdefault("status", "ready")
        return self.register(project_identity, label, **kwargs)

    def register_workspace(self, *args: Any, **kwargs: Any) -> WorkspaceRecord:
        return self.register(*args, **kwargs)

    def mark_lifecycle(
        self,
        workspace_id: str,
        lifecycle: str,
        *,
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkspaceRecord:
        if lifecycle not in _LIFECYCLES:
            raise WorkspaceIndexError(
                "workspace_lifecycle_invalid", "workspace lifecycle is invalid")
        if status is not None and (status not in _LIFECYCLES or status != lifecycle):
            raise WorkspaceIndexError(
                "workspace_lifecycle_invalid",
                "workspace status must match its lifecycle")
        now = _timestamp(self.clock)
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_row = self._row_for(connection, workspace_id)
                if not current_row:
                    raise WorkspaceNotFoundError(workspace_id)
                current_lifecycle = current_row["lifecycle"]
                if (lifecycle != current_lifecycle and
                        lifecycle not in _LIFECYCLE_TRANSITIONS.get(
                            current_lifecycle, frozenset())):
                    raise WorkspaceIndexError(
                        "workspace_lifecycle_invalid",
                        f"invalid workspace lifecycle transition: "
                        f"{current_lifecycle} -> {lifecycle}")
                if metadata:
                    current = current_row
                    try:
                        merged = json.loads(current["metadata_json"] or "{}")
                    except (TypeError, ValueError):
                        merged = {}
                    if not isinstance(merged, dict):
                        merged = {}
                    merged.update(dict(metadata))
                    if status is None:
                        connection.execute(
                            "UPDATE workspaces SET lifecycle=?,metadata_json=?,updated_at=? WHERE workspace_id=?",
                            (lifecycle, _json(merged), now, workspace_id),
                        )
                    else:
                        connection.execute(
                            "UPDATE workspaces SET lifecycle=?,status=?,metadata_json=?,updated_at=? WHERE workspace_id=?",
                            (lifecycle, status, _json(merged), now, workspace_id),
                        )
                elif status is None:
                    connection.execute("UPDATE workspaces SET lifecycle=?,updated_at=? WHERE workspace_id=?",
                                       (lifecycle, now, workspace_id))
                else:
                    connection.execute("UPDATE workspaces SET lifecycle=?,status=?,updated_at=? WHERE workspace_id=?",
                                       (lifecycle, status, now, workspace_id))
                self._bump_generation(connection)
                connection.execute("INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                   ("lifecycle_changed", workspace_id, _json({
                                       "lifecycle": lifecycle, "status": status,
                                       "metadata": dict(metadata or {}),
                                   }), now))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return self.get(workspace_id)  # type: ignore[return-value]

    def revive_destroyed(self, workspace_id: str, *,
                         metadata: Mapping[str, Any]) -> WorkspaceRecord:
        """Publish a freshly materialized generation for one destroyed identity."""
        now = _timestamp(self.clock)
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._row_for(connection, workspace_id)
                if current is None or current["lifecycle"] != "destroyed":
                    raise WorkspaceIndexError(
                        "workspace_recovery_required",
                        "only a destroyed workspace can be rematerialized")
                connection.execute(
                    "UPDATE workspaces SET lifecycle='ready',status='ready',"
                    "metadata_json=?,updated_at=? WHERE workspace_id=?",
                    (_json(metadata), now, workspace_id),
                )
                self._bump_generation(connection)
                connection.execute(
                    "INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) "
                    "VALUES(?,?,?,?)",
                    ("workspace_rematerialized", workspace_id,
                     _json({"status": "ready"}), now),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return self.get(workspace_id)  # type: ignore[return-value]

    def reconcile_startup(self) -> list[str]:
        """Mark interrupted operations indeterminate; never retry mutation."""
        changed: list[str] = []
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT workspace_id FROM workspaces WHERE lifecycle IN "
                "('provisioning','resetting','destroying')",
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            workspace_id = row[0]
            try:
                with self.operation_lock(workspace_id, timeout_seconds=0):
                    with _WRITE_LOCK:
                        connection = self._connect()
                        try:
                            connection.execute("BEGIN IMMEDIATE")
                            current = connection.execute(
                                "SELECT lifecycle FROM workspaces WHERE workspace_id=?",
                                (workspace_id,),
                            ).fetchone()
                            if current is None or current[0] not in {
                                    "provisioning", "resetting", "destroying"}:
                                connection.execute("ROLLBACK")
                                continue
                            now = _timestamp(self.clock)
                            connection.execute(
                                "UPDATE workspaces SET lifecycle='indeterminate',status='indeterminate',updated_at=? "
                                "WHERE workspace_id=?", (now, workspace_id))
                            connection.execute(
                                "INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) "
                                "VALUES(?,?,?,?)",
                                ("workspace_operation_interrupted", workspace_id, "{}", now),
                            )
                            self._bump_generation(connection)
                            connection.execute("COMMIT")
                            changed.append(workspace_id)
                        except Exception:
                            if connection.in_transaction:
                                connection.execute("ROLLBACK")
                            raise
                        finally:
                            connection.close()
            except WorkspaceIndexError as exc:
                if exc.code == "workspace_busy":
                    continue
                raise
        return changed

    def tombstone(self, workspace_id: str, *, reason: str | None = None) -> WorkspaceRecord:
        metadata = {"tombstone_reason": reason} if reason else None
        current = self.get(workspace_id)
        if current is None:
            raise WorkspaceNotFoundError(workspace_id)
        if current.lifecycle == "ready":
            self.mark_lifecycle(
                workspace_id, "destroying", status="destroying")
        return self.mark_lifecycle(
            workspace_id, "destroyed", status="destroyed", metadata=metadata
        )

    def register_alias(self, workspace_id: str, alias: str) -> WorkspaceRecord:
        _name(alias, "workspace alias")
        now = _timestamp(self.clock)
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if not self._row_for(connection, workspace_id):
                    raise WorkspaceNotFoundError(workspace_id)
                try:
                    connection.execute("INSERT INTO workspace_aliases(alias,workspace_id,created_at) VALUES(?,?,?)",
                                       (alias, workspace_id, now))
                except sqlite3.IntegrityError as exc:
                    owner = connection.execute("SELECT workspace_id FROM workspace_aliases WHERE alias=?", (alias,)).fetchone()
                    if owner and owner[0] == workspace_id:
                        connection.execute("COMMIT")
                        return self.get(workspace_id)  # type: ignore[return-value]
                    raise AliasCollisionError(alias) from exc
                self._bump_generation(connection)
                connection.execute("INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                   ("alias_registered", workspace_id, _json({"alias": alias}), now))
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                connection.close()
        return self.get(workspace_id)  # type: ignore[return-value]

    def bind_resource(self, workspace_id: str, resource_type: str, resource_id: str,
                      *, status: str = "owned", metadata: Mapping[str, Any] | None = None) -> ResourceBinding:
        _name(resource_type, "resource type")
        _name(resource_id, "resource id")
        binding_id = "binding_" + hashlib.sha256(f"{resource_type}\0{resource_id}".encode()).hexdigest()[:32]
        now = _timestamp(self.clock)
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if not self._row_for(connection, workspace_id):
                    raise WorkspaceNotFoundError(workspace_id)
                owner = connection.execute(
                    "SELECT workspace_id FROM workspace_bindings "
                    "WHERE resource_type=? AND resource_id=?",
                    (resource_type, resource_id),
                ).fetchone()
                if owner is not None and owner[0] != workspace_id:
                    raise WorkspaceIndexError(
                        "workspace_alias_collision",
                        "resource binding is already owned by another workspace",
                    )
                connection.execute(
                    "INSERT INTO workspace_bindings(binding_id,workspace_id,resource_type,resource_id,status,metadata_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(resource_type,resource_id) DO UPDATE SET "
                    "status=excluded.status,metadata_json=excluded.metadata_json",
                    (binding_id, workspace_id, resource_type, resource_id,
                     status, _json(metadata), now))
                self._bump_generation(connection)
                connection.execute("INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                   ("resource_bound", workspace_id, _json({"resource_type": resource_type, "resource_id": resource_id}), now))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
        return ResourceBinding(workspace_id, resource_type, resource_id, status, dict(metadata or {}))

    # ---- Listing / ownership projection ----------------------------------
    def _indexed(self, *, read_only: bool = False) -> list[WorkspaceRecord]:
        connection = self._connect_read_only() if read_only else self._connect()
        if connection is None:
            return []
        try:
            rows = connection.execute("SELECT * FROM workspaces ORDER BY label,workspace_id").fetchall()
            return [self._record(connection, row) for row in rows]
        finally:
            connection.close()

    def list(self, project_identity: str | None = None, *, include_legacy: bool = True) -> list[WorkspaceRecord]:
        indexed = self._indexed()
        if project_identity is not None:
            indexed = [item for item in indexed if item.project_identity == project_identity or item.status in {"unresolved", "conflict", "incomplete", "invalid"}]
        if not include_legacy:
            return indexed
        evidence = self._read_evidence()
        scan = scan_legacy(self.legacy_root)
        correlated = correlate(self._records_with_findings(scan), evidence,
                               project_identity=project_identity)
        known_keys = {(item.namespace, item.label) for item in indexed}
        synthetic = []
        observed_at = _timestamp(self.clock)
        for item in correlated:
            if item.status == "excluded":
                continue
            if (item.namespace, item.label) in known_keys:
                continue
            synthetic.append(WorkspaceRecord(
                workspace_id="ws_" + hashlib.sha256(item.path.encode()).hexdigest()[:32],
                label=item.label, project_identity=item.project_identity, namespace=item.namespace,
                path=item.path, lifecycle="ready", status=item.status, source="legacy",
                metadata={**dict(item.payload), "legacy_source_digest": "sha256:" + item.digest},
                updated_at=observed_at, created_at=None,
            ))
        return sorted(indexed + synthetic, key=lambda item: (item.label, item.workspace_id))

    @staticmethod
    def _records_with_findings(scan: LegacyScan) -> tuple[LegacyWorkspace, ...]:
        records = list(scan.records)
        represented = {item.path for item in records}
        for finding in scan.findings:
            path = str(finding.get("path") or "")
            if path in represented:
                continue
            digest = hashlib.sha256(path.encode()).hexdigest()
            records.append(LegacyWorkspace(
                namespace="invalid-" + digest[:12],
                label="invalid-" + digest[12:24], path=path,
                digest=digest, status="invalid",
                reason=str(finding.get("code") or "invalid_legacy_record"),
            ))
        return tuple(records)

    @staticmethod
    def _scoped_migration_records(
        scan: LegacyScan,
        evidence: Iterable[Any] | None,
        project_identity: str | None,
    ) -> tuple[LegacyWorkspace, ...]:
        """Correlate one scope and omit only records proven unrelated.

        A conflict is deliberately retained: two exact identities for the
        same namespace/label are actionable ambiguity, not an exclusion.  An
        ``excluded`` result, on the other hand, is outside the requested
        project scope and must be omitted from both the plan and apply rescan.
        """
        correlated = correlate(
            WorkspaceRepository._records_with_findings(scan),
            evidence,
            project_identity=project_identity,
        )
        return tuple(item for item in correlated if item.status != "excluded")

    def list_records(self, project_identity: str | None = None, **kwargs: Any) -> list[WorkspaceRecord]:
        return self.list(project_identity, **kwargs)

    def submission_legacy_records(
        self, *, project_identity: str, namespace: str, label: str,
        evidence: Iterable[Any] | Mapping[str, Any],
    ) -> tuple[LegacyWorkspace, ...]:
        """Read the exact compatibility leaf before accepting a new job."""
        _safe(project_identity, "project_identity")
        _name(namespace, "legacy namespace")
        _name(label, "workspace label")
        scan = scan_legacy(self.legacy_root, expected_namespace=namespace)
        correlated = correlate(
            self._records_with_findings(scan), normalize_evidence(evidence),
            project_identity=project_identity,
        )
        return tuple(item for item in correlated if item.label == label)

    def ownership_projection(self) -> dict[str, Any]:
        # Do not call ``list`` here: a projection may be requested during
        # startup/diagnosis and must not create an index, parent directory, or
        # migration plan.  Both SQLite and legacy reads are read-only.
        indexed = self._indexed(read_only=True)
        evidence = self._read_evidence()
        projection_jobs = self._read_projection_jobs()
        scan = scan_legacy(self.legacy_root)
        correlated = correlate(self._records_with_findings(scan), evidence)
        known_keys = {(item.namespace, item.label) for item in indexed}
        synthetic = []
        observed_at = _timestamp(self.clock)
        for item in correlated:
            if item.status == "excluded" or (item.namespace, item.label) in known_keys:
                continue
            synthetic.append(WorkspaceRecord(
                workspace_id="ws_" + hashlib.sha256(item.path.encode()).hexdigest()[:32],
                label=item.label, project_identity=item.project_identity,
                namespace=item.namespace, path=item.path, lifecycle="ready",
                status=item.status, source="legacy",
                metadata={**dict(item.payload), "legacy_source_digest": "sha256:" + item.digest},
                updated_at=observed_at,
            ))
        records = sorted(indexed + synthetic, key=lambda item: (item.label, item.workspace_id))
        generation = 0
        connection = self._connect_read_only()
        if connection is not None:
            try:
                row = connection.execute(
                    "SELECT value FROM workspace_meta WHERE key='generation'"
                ).fetchone()
                generation = int(row[0]) if row else 0
            finally:
                connection.close()
        def project(item: WorkspaceRecord) -> dict[str, Any]:
            active_jobs = sum(
                (row.get("workspace_id") == item.workspace_id
                 if row.get("workspace_id") is not None
                 else (row.get("project_identity") == item.project_identity
                       and row.get("workspace_label") == item.label))
                and row.get("lifecycle") in {
                    "accepted", "queued", "running", "cancelling"}
                for row in projection_jobs
            )
            active_leases = sum(
                (row.get("workspace_id") == item.workspace_id
                 if row.get("workspace_id") is not None
                 else (row.get("project_identity") == item.project_identity
                       and row.get("workspace_label") == item.label))
                and row.get("lifecycle") in {"running", "cancelling"}
                for row in projection_jobs
            )
            binding_rows = [dict(binding) for binding in item.bindings]
            alias_evidence = []
            for alias in item.aliases:
                alias_evidence.append({
                    "kind": "workspace-alias",
                    "digest": "sha256:" + hashlib.sha256(alias.encode()).hexdigest(),
                    "quality": "high",
                })
            for binding in binding_rows:
                canonical = _json({
                    "resource_type": binding.get("resource_type"),
                    "resource_id": binding.get("resource_id"),
                    "status": binding.get("status"),
                })
                alias_evidence.append({
                    "kind": str(binding.get("resource_type") or "resource-binding"),
                    "digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
                    "quality": "high",
                })
            complete = (item.lifecycle in _LIFECYCLES and
                        item.lifecycle != "indeterminate" and
                        item.status not in {"unresolved", "conflict", "incomplete", "invalid", "indeterminate"})
            evidence_payload = _json({
                "workspace_id": item.workspace_id,
                "project_identity": item.project_identity,
                "lifecycle": item.lifecycle,
                "status": item.status,
                "aliases": list(item.aliases),
                "bindings": binding_rows,
            })
            return {
                "workspace_id": item.workspace_id,
                "owner_kind": "workspace",
                "owner_id": item.workspace_id,
                "project_identity": item.project_identity,
                "workspace_label": item.label,
                "label": item.label,
                "lifecycle": item.lifecycle,
                "status": item.status,
                "index_generation": generation,
                "locator_digest": ("sha256:" + hashlib.sha256(item.path.encode()).hexdigest()
                                   if item.path else None),
                "evidence_digest": "sha256:" + hashlib.sha256(evidence_payload.encode()).hexdigest(),
                "alias_evidence": alias_evidence,
                "active_references": {
                    "jobs": active_jobs,
                    "leases": active_leases,
                    "containers": None,
                    "mounts": None,
                },
                "complete": complete,
                "error": None if complete else "workspace_index_incomplete",
                "observed_at": item.updated_at,
                "aliases": list(item.aliases),
                "bindings": binding_rows,
            }

        projected = [project(item) for item in records]
        return {
            "records": projected,
            "workspaces": projected,
            "counts": {
                "total": len(records),
                "ready": sum(item.status == "ready" for item in records),
                "adoptable": sum(item.status == "adoptable" for item in records),
                "unresolved": sum(item.status == "unresolved" for item in records),
                "conflict": sum(item.status == "conflict" for item in records),
                "incomplete": sum(item.status in {"incomplete", "invalid"} for item in records),
            },
            "generation": generation,
            "index_generation": generation,
        }

    # ---- Migration -------------------------------------------------------
    def _read_evidence(self, supplied: Iterable[Any] | Mapping[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
        if supplied is not None:
            return normalize_evidence(supplied)
        reader = self.job_index_reader
        if reader is None:
            return ()
        try:
            if callable(reader):
                try:
                    value = reader()
                except TypeError:
                    value = reader(self.legacy_root)
            elif hasattr(reader, "list"):
                value = reader.list()
            elif hasattr(reader, "read"):
                value = reader.read()
            else:
                value = reader
        except Exception as exc:
            raise WorkspaceIndexError("evidence_unavailable", "typed job/project evidence could not be read") from exc
        return normalize_evidence(value)

    def _read_projection_jobs(self) -> tuple[dict[str, Any], ...]:
        """Read bounded job rows without discarding lifecycle evidence."""
        reader = self.job_index_reader
        if reader is None:
            return ()
        try:
            if callable(reader):
                try:
                    value = reader()
                except TypeError:
                    value = reader(self.legacy_root)
            elif hasattr(reader, "list"):
                value = {"jobs": reader.list(limit=10000)}
            elif hasattr(reader, "read"):
                value = reader.read()
            else:
                value = reader
        except Exception as exc:
            raise WorkspaceIndexError(
                "evidence_unavailable",
                "typed job evidence could not be read for ownership projection",
            ) from exc
        rows = value.get("jobs", ()) if isinstance(value, Mapping) else value
        if isinstance(rows, (str, bytes, Mapping)) or not isinstance(rows, Iterable):
            return ()
        return tuple(dict(row) for row in rows if isinstance(row, Mapping))

    def _migration_inputs(self, *, evidence: Iterable[Any] | Mapping[str, Any] | None = None,
                          job_inputs: Iterable[Any] | Mapping[str, Any] | None = None,
                          project_inputs: Iterable[Any] | Mapping[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
        values: list[Any] = []

        def extend(value: Iterable[Any] | Mapping[str, Any] | None, *keys: str) -> None:
            if value is None:
                return
            if isinstance(value, Mapping):
                found = False
                for key in keys:
                    if key in value:
                        found = True
                        nested = value.get(key) or ()
                        values.extend(nested if not isinstance(nested, Mapping) else (nested,))
                if not found:
                    values.append(value)
            else:
                values.extend(value)

        extend(evidence, "jobs", "projects")
        extend(job_inputs, "jobs")
        extend(project_inputs, "projects")
        return self._read_evidence(values if values else None)

    def migration_plan(self, project_identity: str | None = None, *, expected_legacy_namespace: str | None = None,
                       evidence: Iterable[Any] | Mapping[str, Any] | None = None,
                       job_inputs: Iterable[Any] | Mapping[str, Any] | None = None,
                       project_inputs: Iterable[Any] | Mapping[str, Any] | None = None,
                       ttl_seconds: int | None = None,
                       expected_inventory_digest: str | None = None,
                       expected_generation: int | None = None) -> MigrationPlan:
        if project_identity is not None:
            _safe(project_identity, "project_identity")
        if expected_legacy_namespace is not None:
            _safe(expected_legacy_namespace, "expected legacy namespace")
        evidence_rows = self._migration_inputs(evidence=evidence, job_inputs=job_inputs, project_inputs=project_inputs)
        inventory_scan = scan_legacy(self.legacy_root)
        scan = (inventory_scan if expected_legacy_namespace is None else
                scan_legacy(self.legacy_root, expected_namespace=expected_legacy_namespace))
        correlated = self._scoped_migration_records(scan, evidence_rows, project_identity)
        items = items_from_scan(correlated)
        inventory_items = items_from_scan(self._records_with_findings(inventory_scan))
        inventory_digest = plan_digest(inventory_items)
        generation = self.schema_generation()
        if (expected_inventory_digest is not None and
                expected_inventory_digest != inventory_digest):
            raise MigrationStaleError("workspace inventory digest assertion failed")
        if expected_generation is not None and expected_generation != generation:
            raise MigrationStaleError("workspace index generation assertion failed")
        digest = plan_digest(
            items, evidence_rows, inventory_digest=inventory_digest)
        now = _timestamp(self.clock)
        ttl = self.plan_ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        expires = (_utc(now) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        summary: dict[str, int] = {}
        for item in items:
            summary[item.status] = summary.get(item.status, 0) + 1
        plan_id = "wm_" + uuid.uuid4().hex
        plan = MigrationPlan(
            plan_id=plan_id, digest=digest, generation=generation,
            created_at=now, expires_at=expires, items=items, summary=summary,
            project_identity=project_identity,
            expected_namespace=expected_legacy_namespace,
            evidence=evidence_rows, inventory_digest=inventory_digest,
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO workspace_plans(plan_id,digest,generation,created_at,expires_at,payload_json) VALUES(?,?,?,?,?,?)",
                               (plan.plan_id, plan.digest, plan.generation, plan.created_at, plan.expires_at, _json(plan.to_dict())))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return plan

    plan_migration = migration_plan

    def _stored_plan(self, plan_id: str) -> MigrationPlan:
        connection = self._connect()
        try:
            row = connection.execute("SELECT payload_json FROM workspace_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if not row:
                raise WorkspaceIndexError("plan_not_found", f"migration plan {plan_id!r} was not found")
            payload = json.loads(row[0])
        finally:
            connection.close()
        items = tuple(MigrationItem(**item) for item in payload.get("items", ()))
        return MigrationPlan(
            plan_id=payload["plan_id"], digest=payload["digest"],
            generation=int(payload["generation"]),
            created_at=payload["created_at"], expires_at=payload["expires_at"],
            items=items, summary=payload.get("summary", {}),
            project_identity=payload.get("project_identity"),
            expected_namespace=payload.get("expected_namespace"),
            evidence=tuple(payload.get("evidence", ())),
            inventory_digest=payload.get("inventory_digest"),
        )

    def migration_apply(self, plan: MigrationPlan | str, *, confirm: bool = False,
                        project_identity: str | None = None,
                        expected_legacy_namespace: str | None = None,
                        evidence: Iterable[Any] | Mapping[str, Any] | None = None,
                        job_inputs: Iterable[Any] | Mapping[str, Any] | None = None,
                        project_inputs: Iterable[Any] | Mapping[str, Any] | None = None) -> dict[str, Any]:
        with _WRITE_LOCK:
            with self._migration_guard():
                stored = self._stored_plan(
                    plan.plan_id if isinstance(plan, MigrationPlan) else plan)
                workspace_ids = {
                    item.workspace_id for item in stored.items
                    if isinstance(item.workspace_id, str) and item.workspace_id
                }
                connection = self._connect()
                try:
                    for item in stored.items:
                        existing = connection.execute(
                            "SELECT workspace_id FROM workspaces WHERE path=?",
                            (item.path,),
                        ).fetchone()
                        if existing is None and item.project_identity is not None:
                            existing = connection.execute(
                                "SELECT workspace_id FROM workspaces "
                                "WHERE project_identity=? AND label=?",
                                (item.project_identity, item.label),
                            ).fetchone()
                        if existing is not None:
                            workspace_ids.add(existing[0])
                finally:
                    connection.close()
                with ExitStack() as locks:
                    for workspace_id in sorted(workspace_ids):
                        locks.enter_context(self.operation_lock(workspace_id))
                    return self._migration_apply_locked(
                        plan, confirm=confirm, project_identity=project_identity,
                        expected_legacy_namespace=expected_legacy_namespace,
                        evidence=evidence, job_inputs=job_inputs,
                        project_inputs=project_inputs,
                    )

    def _migration_apply_locked(self, plan: MigrationPlan | str, *, confirm: bool = False,
                        project_identity: str | None = None,
                        expected_legacy_namespace: str | None = None,
                        evidence: Iterable[Any] | Mapping[str, Any] | None = None,
                        job_inputs: Iterable[Any] | Mapping[str, Any] | None = None,
                        project_inputs: Iterable[Any] | Mapping[str, Any] | None = None) -> dict[str, Any]:
        if confirm is not True:
            raise WorkspaceIndexError("confirmation_required", "migration apply requires confirm=True")
        stored = self._stored_plan(plan.plan_id if isinstance(plan, MigrationPlan) else plan)
        if isinstance(plan, MigrationPlan) and (
            plan.digest != stored.digest
            or plan.generation != stored.generation
            or plan.expires_at != stored.expires_at
        ):
            raise MigrationStaleError("supplied migration plan does not match its stored receipt")
        connection = self._connect()
        try:
            applied = connection.execute("SELECT result_json FROM workspace_plan_applications WHERE plan_id=?",
                                         (stored.plan_id,)).fetchone()
            if applied:
                return {**json.loads(applied[0]), "already_applied": True}
        finally:
            connection.close()
        now = _utc(_timestamp(self.clock))
        if now >= _utc(stored.expires_at):
            raise MigrationStaleError("migration plan has expired")
        current_generation = self.schema_generation()
        if current_generation != stored.generation:
            raise MigrationStaleError("workspace index generation changed")
        if (project_identity is not None and project_identity != stored.project_identity) or (
            expected_legacy_namespace is not None
            and expected_legacy_namespace != stored.expected_namespace
        ):
            raise MigrationStaleError("migration scope changed since planning")
        identity = stored.project_identity if project_identity is None else project_identity
        namespace = stored.expected_namespace if expected_legacy_namespace is None else expected_legacy_namespace
        # A plan created with an explicit evidence set is self-contained.  When
        # a live job reader is configured, read it again so changed evidence
        # invalidates the digest before any write.
        supplied = evidence if evidence is not None else job_inputs if job_inputs is not None else project_inputs
        evidence_rows = self._migration_inputs(evidence=supplied) if supplied is not None else (
            self._read_evidence() if self.job_index_reader is not None else stored.evidence)
        inventory_scan = scan_legacy(self.legacy_root)
        scan = (inventory_scan if namespace is None else
                scan_legacy(self.legacy_root, expected_namespace=namespace))
        current_items = items_from_scan(
            self._scoped_migration_records(scan, evidence_rows, identity)
        )
        current_inventory_digest = plan_digest(items_from_scan(
            self._records_with_findings(inventory_scan)))
        if (stored.inventory_digest != current_inventory_digest or
                plan_digest(
                    current_items, evidence_rows,
                    inventory_digest=current_inventory_digest,
                ) != stored.digest):
            raise MigrationStaleError("legacy workspace evidence changed since planning")
        now_text = _timestamp(self.clock)
        inserted = 0
        relocated = 0
        aliases_added = 0
        unresolved = 0
        with _WRITE_LOCK:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                generation = int(connection.execute("SELECT value FROM workspace_meta WHERE key='generation'").fetchone()[0])
                if generation != stored.generation:
                    raise MigrationStaleError("workspace index generation changed during apply")
                for item in current_items:
                    if item.status != "adoptable":
                        unresolved += 1
                        connection.execute(
                            "INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) "
                            "VALUES(?,?,?,?)",
                            ("legacy_workspace_observed", None, _json({
                                "namespace_digest": hashlib.sha256(
                                    item.namespace.encode()).hexdigest(),
                                "label": item.label, "status": item.status,
                                "reason": item.reason, "legacy_digest": item.digest,
                            }), now_text),
                        )
                        connection.execute(
                            "INSERT INTO workspace_migrations(source_digest,decision,reason,workspace_id,project_identity,namespace_digest,label,first_observed_at,last_observed_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_digest) DO UPDATE SET "
                            "decision=excluded.decision,reason=excluded.reason,project_identity=excluded.project_identity,last_observed_at=excluded.last_observed_at",
                            (_migration_source_key(item), item.status, item.reason, None,
                             item.project_identity,
                             hashlib.sha256(item.namespace.encode()).hexdigest(),
                             item.label, now_text, now_text),
                        )
                        continue
                    workspace_id = item.workspace_id or "ws_" + hashlib.sha256(item.path.encode()).hexdigest()[:32]
                    existing = connection.execute("SELECT * FROM workspaces WHERE path=?", (item.path,)).fetchone()
                    if existing is None and item.project_identity is not None:
                        existing = connection.execute(
                            "SELECT * FROM workspaces WHERE project_identity=? AND label=?",
                            (item.project_identity, item.label),
                        ).fetchone()
                        if existing is not None:
                            try:
                                existing_metadata = json.loads(existing["metadata_json"] or "{}")
                            except (TypeError, ValueError):
                                existing_metadata = {}
                            # A moved SANDBOX_HOME changes the metadata file's
                            # absolute path but not its bytes.  Repoint the
                            # index only when the prior legacy digest proves
                            # this is the same leaf; never merge by label alone.
                            if (
                                existing["status"] in {"adoptable", "ready"}
                                and isinstance(existing_metadata, dict)
                                and existing_metadata.get("legacy_digest") == item.digest
                            ):
                                connection.execute(
                                    "UPDATE workspaces SET namespace=?,path=?,lifecycle='ready',"
                                    "status='ready',source='legacy',updated_at=? WHERE workspace_id=?",
                                    (item.namespace, item.path, now_text, existing["workspace_id"]),
                                )
                                _alias, alias_added = self._ensure_legacy_alias(
                                    connection, existing["workspace_id"], item, now_text
                                )
                                aliases_added += int(alias_added)
                                relocated += 1
                                connection.execute(
                                    "INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                    ("legacy_workspace_relocated", existing["workspace_id"], _json(item.to_dict()), now_text),
                                )
                                continue
                            raise WorkspaceIndexError(
                                "migration_collision",
                                "legacy workspace identity is already indexed differently",
                                path=item.path,
                            )
                    if existing:
                        # Replaying the same adoption is a no-op. Migration
                        # decision states never become durable lifecycle
                        # states; accept the pre-release ``adoptable`` value
                        # only to normalize it to ``ready`` transactionally.
                        if (existing["project_identity"] != item.project_identity or
                                existing["status"] not in {"ready", "adoptable"}):
                            raise WorkspaceIndexError("migration_collision", "legacy path is already indexed differently", path=item.path)
                        if existing["status"] != "ready" or existing["lifecycle"] != "ready":
                            connection.execute(
                                "UPDATE workspaces SET lifecycle='ready',status='ready',updated_at=? "
                                "WHERE workspace_id=?",
                                (now_text, existing["workspace_id"]),
                            )
                        _alias, alias_added = self._ensure_legacy_alias(
                            connection, existing["workspace_id"], item, now_text
                        )
                        aliases_added += int(alias_added)
                        continue
                    try:
                        connection.execute(
                            "INSERT INTO workspaces(workspace_id,project_identity,label,namespace,path,lifecycle,status,source,metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (workspace_id, item.project_identity, item.label, item.namespace, item.path,
                             "ready", "ready", "legacy", _json({"legacy_digest": item.digest, "reason": item.reason}), now_text, now_text))
                    except sqlite3.IntegrityError as exc:
                        raise WorkspaceIndexError("migration_collision", "legacy workspace identity collides", path=item.path) from exc
                    _alias, alias_added = self._ensure_legacy_alias(
                        connection, workspace_id, item, now_text
                    )
                    aliases_added += int(alias_added)
                    inserted += 1
                    connection.execute(
                        "INSERT INTO workspace_migrations(source_digest,decision,reason,workspace_id,project_identity,namespace_digest,label,first_observed_at,last_observed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source_digest) DO UPDATE SET "
                        "decision='adopted',reason=NULL,workspace_id=excluded.workspace_id,project_identity=excluded.project_identity,last_observed_at=excluded.last_observed_at",
                        (_migration_source_key(item), "adopted", None, workspace_id,
                         item.project_identity,
                         hashlib.sha256(item.namespace.encode()).hexdigest(),
                         item.label, now_text, now_text),
                    )
                    connection.execute("INSERT INTO workspace_audit(event_type,workspace_id,payload_json,created_at) VALUES(?,?,?,?)",
                                       ("legacy_workspace_migrated", workspace_id, _json(item.to_dict()), now_text))
                # Relocation only regenerates protected path-bearing locators;
                # it preserves the semantic index generation. New ownership
                # or alias evidence still advances the generation.
                new_generation = self._bump_generation(connection) if (
                    inserted or aliases_added
                ) else generation
                result = {
                    "ok": unresolved == 0, "plan_id": stored.plan_id, "inserted": inserted,
                    "relocated": relocated,
                    "aliases_added": aliases_added,
                    "unresolved": unresolved, "generation": new_generation,
                    "metadata_only": True, "legacy_unchanged": True,
                }
                if unresolved:
                    result["code"] = "workspace_index_incomplete"
                connection.execute("INSERT INTO workspace_plan_applications(plan_id,applied_at,generation,result_json) VALUES(?,?,?,?)",
                                   (stored.plan_id, now_text, new_generation, _json(result)))
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                connection.close()
        return result

    apply_migration = migration_apply

    def scan_legacy(self, **kwargs: Any) -> LegacyScan:
        return scan_legacy(self.legacy_root, **kwargs)


def read_only_projection(
    index_path: str | Path | None = None,
    legacy_root: str | Path | None = None,
    job_index_reader: Callable[..., Any] | Any | None = None,
) -> dict[str, Any]:
    """Module-level convenience wrapper for the constructor-free projection."""
    return WorkspaceRepository.read_only_projection(
        index_path=index_path,
        legacy_root=legacy_root,
        job_index_reader=job_index_reader,
    )
