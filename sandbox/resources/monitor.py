"""Storage-monitor policy resolution and last-run record storage.

Policy resolution is deliberately a configuration-only operation.  It reads
the machine configuration through the owning config helpers, validates a
named remote through the owning remote registry, and only then asks the
machine-config manifest to normalize one merged policy.  The record store and
the persistent monitor guard are the only persistence owned by this module;
records are atomically replaced while guard state is updated through its
retained descriptor.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import errno
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import Any

try:  # POSIX is the supported controller runtime; keep import-safe elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts.
    fcntl = None

from sandbox.config.manifest import apply_machine_config
from sandbox.config.storage_monitor import StorageMonitorConfigError
from sandbox.core._config import load_config
from sandbox.core._paths import RUNTIME_DIR
from sandbox.core._remote import get_remote, list_remotes


_LOCAL_TARGET = {"kind": "local", "name": "local"}

# Keep this contract aligned with specs/043-storage-pressure-scheduler's
# MonitorRunRecord.  The runner is intentionally not implemented in this
# module yet, so all documented runner fields remain admissible here.
_RECORD_FIELDS = frozenset({
    "schema", "target", "at", "trigger", "level", "free_bytes",
    "total_bytes", "free_ratio", "warn_ratio", "critical_ratio",
    "auto_ratio", "threshold_crossed", "guidance", "auto", "reap",
    "inventory_status", "errors",
})
_AUTO_FIELDS = frozenset({
    "enabled", "eligible", "tier", "ran", "reclaimed_bytes", "run_id", "reason",
})
_REAP_FIELDS = frozenset({
    "enabled", "dry_run", "candidates", "reclaimed_bytes", "reason",
})
_ERROR_FIELDS = frozenset({"code", "message"})
_LEVELS = frozenset({"normal", "warning", "critical", "unknown"})
_TRIGGERS = frozenset({"manual", "scheduled"})
_THRESHOLDS = frozenset({"warn_ratio", "critical_ratio"})
_REMOTE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# These are the bounded guidance strings produced by the capacity classifier.
# Doctor deliberately does not echo arbitrary text from a persisted record:
# records are local evidence, not an authority to print paths, connection
# details, or secret-like values.
_SAFE_GUIDANCE = frozenset({
    "no action required",
    "capacity is unmeasured; rerun with --refresh",
    "free space is below the warning threshold; run `sb resources plan --tier safe` and review the candidates",
    "free space is critically low; run `sb resources cleanup --tier safe --confirm` after reviewing the plan",
})


class StorageMonitorLockError(RuntimeError):
    """A monitor lock could not be acquired or its grace was invalid.

    The public message is deliberately constant.  Lock failures must not expose
    target names, filesystem paths, or operating-system diagnostics.
    """

    _MESSAGES = {
        "invalid_lock_grace": "storage monitor lock grace is invalid",
        "lock_unavailable": "storage monitor lock is unavailable",
    }

    def __init__(self, code: str = "lock_unavailable") -> None:
        if code not in self._MESSAGES:
            code = "lock_unavailable"
        self.code = code
        super().__init__(self._MESSAGES[code])


_LOCK_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_LOCK_MAX_BYTES = 4096
_LOCK_MODE = 0o600
_LOCK_PARENT_MODE = 0o700


def _lock_error(code: str = "lock_unavailable") -> StorageMonitorLockError:
    return StorageMonitorLockError(code)


def _unknown_target(name: str) -> StorageMonitorConfigError:
    return StorageMonitorConfigError(
        f"storage monitor target {name!r} is not configured",
        "unknown_target",
    )


def _remote_name(remote: Any) -> str | None:
    """Return the name for a named remote, or ``None`` for the local target."""
    if remote is None:
        return None
    if isinstance(remote, str):
        name = remote.strip()
    elif isinstance(remote, Mapping):
        # A target descriptor is accepted for callers that already resolved a
        # typed target.  A remote descriptor must still be looked up by name;
        # its fields are never treated as configuration authority.
        kind = remote.get("kind")
        if kind == "local":
            # A local descriptor is canonical only as {kind: local, name:
            # local}.  Treat contradictory descriptors as unknown targets so
            # they cannot silently fall back to loading the local policy.
            if remote.get("name") == "local":
                return None
            name = remote.get("name")
            raise _unknown_target(name.strip() if isinstance(name, str) else "")
        name = remote.get("name")
        if not isinstance(name, str):
            name = ""
        name = name.strip()
    else:
        kind = getattr(remote, "kind", None)
        if kind == "local":
            if getattr(remote, "name", None) == "local":
                return None
            name = getattr(remote, "name", "")
            raise _unknown_target(name.strip() if isinstance(name, str) else "")
        name = getattr(remote, "name", "")
        name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise _unknown_target(name)
    return name


def _remote_entry(name: str) -> Mapping[str, Any]:
    """Resolve one registry entry without exposing its sensitive fields."""
    # ``get_remote`` is the owning lookup and is enough for the normal path.
    # If an adapter returns ``None`` for a registered entry with no fields,
    # consult ``list_remotes`` only to distinguish that from an unknown name.
    entry = get_remote(name)
    if entry is None:
        registered = list_remotes()
        if not isinstance(registered, Mapping) or name not in registered:
            raise _unknown_target(name)
        listed = registered.get(name)
        return listed if isinstance(listed, Mapping) else {}
    if not isinstance(entry, Mapping):
        # A registry key can exist with a null/non-object value.  It is a
        # configured target, but has no monitor override to merge.
        return {}
    return entry


def _merged_config(
    remote_name: str | None,
    remote_entry: Mapping[str, Any] | None = None,
) -> dict:
    """Load raw config and add one sparse per-target override."""
    loaded = load_config()
    if not isinstance(loaded, Mapping):
        raise StorageMonitorConfigError(
            "machine configuration must be an object",
            "invalid_schedule_field",
        )

    # ``load_config`` owns the checked-in + machine-local merge.  Copy only
    # the containers we modify so no caller-owned config object is changed and
    # no remote entry is accidentally carried into the policy result.
    result = copy.deepcopy(dict(loaded))
    if remote_name is None:
        return result

    entry = remote_entry if remote_entry is not None else _remote_entry(remote_name)
    override = entry.get("storage_monitor")
    if override is None:
        return result

    resources = result.get("resources")
    if "resources" not in result:
        result["resources"] = {"monitor": override}
    elif isinstance(resources, Mapping):
        resources_copy = dict(resources)
        base_monitor = resources_copy.get("monitor")
        if isinstance(base_monitor, Mapping) and isinstance(override, Mapping):
            merged_monitor = dict(base_monitor)
            merged_monitor.update(dict(override))
            resources_copy["monitor"] = merged_monitor
        else:
            # Leave malformed layers for the manifest provider to reject with
            # its normal, pre-contact configuration error.
            resources_copy["monitor"] = override
        result["resources"] = resources_copy
    else:
        # Preserve a malformed resources layer; ``apply_machine_config`` owns
        # the corresponding validation and error code.
        result["resources"] = resources
    return result


def resolve_policy(remote: Any = None) -> dict[str, Any]:
    """Resolve and normalize the storage-monitor policy for ``remote``.

    The local policy is selected with ``None``.  A named remote must already
    exist in the registered remote set; unknown names fail before the machine
    config is loaded and before any host/network operation could be attempted.
    ``load_config`` performs the checked-in ``sandbox.yml`` plus
    ``sandbox.local.yml`` merge.  The per-target sparse override is merged into
    that raw block, and ``apply_machine_config`` performs the one normalization
    pass (including built-in defaults and all validation).
    """
    name = _remote_name(remote)
    # Validate target registration before loading configuration.  This keeps
    # an unknown target a purely local refusal even when config bootstrap is
    # unavailable and guarantees no process/network work is reached.
    entry = _remote_entry(name) if name is not None else None
    config = _merged_config(name, entry)
    resolved = apply_machine_config(config)
    try:
        resources = resolved["resources"]
        policy = resources["monitor"]
    except (KeyError, TypeError):
        raise StorageMonitorConfigError(
            "resolved storage monitor policy is missing",
            "invalid_schedule_field",
        ) from None
    if not isinstance(policy, Mapping):
        raise StorageMonitorConfigError(
            "resolved storage monitor policy is invalid",
            "invalid_schedule_field",
        )
    return dict(policy)


def _target_descriptor(target: Any) -> tuple[str, str, dict[str, str]]:
    """Return canonical target identity and a safe record target object."""
    if target is None:
        return "local", "local", dict(_LOCAL_TARGET)

    if isinstance(target, str):
        name = target.strip()
        if not name:
            raise ValueError("target is invalid")
        if name == "local":
            return "local", "local", dict(_LOCAL_TARGET)
        return "remote", name, {"kind": "remote", "name": name}

    if isinstance(target, Mapping):
        kind = target.get("kind")
        name = target.get("name")
    else:
        kind = getattr(target, "kind", None)
        name = getattr(target, "name", None)

    if kind == "local":
        if name == "local":
            return "local", "local", dict(_LOCAL_TARGET)
        raise ValueError("target is invalid")
    if kind == "remote" and isinstance(name, str):
        name = name.strip()
        if name and not any(ord(char) < 32 or ord(char) == 127 for char in name):
            return "remote", name, {"kind": "remote", "name": name}
    # A name-only target object is useful to record callers that pass the same
    # remote argument used by ``resolve_policy``.
    if kind is None and isinstance(name, str) and name:
        name = name.strip()
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ValueError("target is invalid")
        if name == "local":
            return "local", "local", dict(_LOCAL_TARGET)
        return "remote", name, {"kind": "remote", "name": name}
    raise ValueError("target is invalid")


def record_path(target: Any) -> Path:
    """Return the opaque last-run record path for ``target``.

    Only a digest appears in the basename.  In particular, no remote name,
    SSH host, or other registry field is used as a path component.
    """
    kind, name, _safe_target = _target_descriptor(target)
    identity = "local" if kind == "local" else f"remote:{name}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return Path(RUNTIME_DIR) / "resources" / "monitor" / f"{digest}.json"


def lock_path(target: Any) -> Path:
    """Return the draft compatibility lock path beside a target's record.

    New callers must use :func:`guard_path`.  The ``.lock`` spelling remains
    readable for one-way compatibility with the unreleased draft, but it is
    never created, replaced, truncated, or removed by the lease lifecycle.
    """
    return record_path(target).with_suffix(".lock")


def guard_path(target: Any) -> Path:
    """Return the private per-target guard path used for lock arbitration."""
    return record_path(target).with_suffix(".guard")


_GUARD_FIELDS = frozenset({
    "schema", "state", "pid", "created_at", "released_at", "owner_token",
})
_LEGACY_LOCK_FIELDS = frozenset({"schema", "pid", "created_at", "owner_token"})
_GUARD_STATES = frozenset({"active", "released"})
_GUARD_SCHEMA = 2
_LEGACY_SCHEMA = 1
_EMPTY = object()
_INVALID = object()


def _optional_flag(name: str) -> int:
    return int(getattr(os, name, 0) or 0)


def _safe_mode(mode: int, expected: int = _LOCK_MODE) -> bool:
    return (mode & 0o7777) == expected


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _close_fd(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _prepare_lock_parent(path: Path) -> int:
    """Create and validate the private monitor directory, retaining its fd."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=_LOCK_PARENT_MODE)
    flags = (
        os.O_RDONLY
        | _optional_flag("O_DIRECTORY")
        | _optional_flag("O_NOFOLLOW")
        | _optional_flag("O_CLOEXEC")
    )
    descriptor = os.open(parent, flags)
    try:
        parent_stat = os.fstat(descriptor)
        getuid = getattr(os, "getuid", None)
        if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_nlink < 1:
            raise OSError(errno.ENOTDIR, "monitor parent is not a directory")
        if callable(getuid) and parent_stat.st_uid != getuid():
            raise OSError(errno.EACCES, "monitor parent owner is unsafe")
        # fchmod is descriptor-relative and closes the mode race that a
        # path-based chmod would introduce.  Revalidate after the change.
        if not _safe_mode(parent_stat.st_mode, _LOCK_PARENT_MODE):
            os.fchmod(descriptor, _LOCK_PARENT_MODE)
            parent_stat = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_nlink < 1
            or not _safe_mode(parent_stat.st_mode, _LOCK_PARENT_MODE)
            or (callable(getuid) and parent_stat.st_uid != getuid())
        ):
            raise OSError(errno.EACCES, "monitor parent is unsafe")
        return descriptor
    except Exception:
        _close_fd(descriptor)
        raise


def _open_child(
    path: Path,
    flags: int,
    mode: int = 0,
    parent_descriptor: int | None = None,
) -> int:
    """Open one monitor child by name below the retained parent descriptor."""
    if parent_descriptor is None:
        return os.open(path, flags, mode) if mode else os.open(path, flags)
    if mode:
        return os.open(path.name, flags, mode, dir_fd=parent_descriptor)
    return os.open(path.name, flags, dir_fd=parent_descriptor)


def _lstat_child(path: Path, parent_descriptor: int | None) -> os.stat_result:
    return os.lstat(path.name, dir_fd=parent_descriptor)


def _guard_identity_matches(
    path: Path,
    expected: os.stat_result,
    parent_descriptor: int,
) -> bool:
    """Check that the pathname still names our originally opened guard.

    This is an identity check only.  A mismatch means the fd is detached and
    the caller must not write it or attempt to repair the successor pathname.
    """
    try:
        current = _lstat_child(path, parent_descriptor)
    except OSError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_nlink == 1
        and _safe_mode(current.st_mode)
        and _same_inode(current, expected)
    )


def _open_guard(
    path: Path,
    parent_descriptor: int,
) -> tuple[int, os.stat_result] | None:
    """Open and validate one persistent guard without repairing old evidence.

    ``O_EXCL`` lets us distinguish the one guard this acquisition created from
    an existing artifact.  Only the former may be normalized with descriptor
    ``fchmod``; an existing wrong-mode artifact is unsafe evidence and remains
    byte-for-byte untouched.
    """
    common_flags = _optional_flag("O_NOFOLLOW") | _optional_flag("O_CLOEXEC")
    created = False
    try:
        try:
            descriptor = _open_child(
                path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | common_flags,
                _LOCK_MODE,
                parent_descriptor,
            )
            created = True
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            descriptor = _open_child(
                path,
                os.O_RDWR | common_flags,
                parent_descriptor=parent_descriptor,
            )
    except OSError as exc:
        # Unsafe paths and inaccessible artifacts are indistinguishable from a
        # held advisory lock to callers.  No path cleanup is attempted.
        if exc.errno in {
            errno.EACCES, errno.EAGAIN, errno.ELOOP, errno.EPERM, errno.ENOENT,
        }:
            return None
        raise
    try:
        opened = os.fstat(descriptor)
        current = _lstat_child(path, parent_descriptor)
        getuid = getattr(os, "getuid", None)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or not _same_inode(opened, current)
            or (callable(getuid) and opened.st_uid != getuid())
        ):
            _close_fd(descriptor)
            return None
        if created:
            os.fchmod(descriptor, _LOCK_MODE)
            opened = os.fstat(descriptor)
            current = _lstat_child(path, parent_descriptor)
        if (
            not _safe_mode(opened.st_mode)
            or not _safe_mode(current.st_mode)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or not _same_inode(opened, current)
            or (callable(getuid) and opened.st_uid != getuid())
        ):
            _close_fd(descriptor)
            return None
        return descriptor, opened
    except Exception:
        _close_fd(descriptor)
        raise


def _release_guard(descriptor: int | None) -> None:
    if descriptor is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
    _close_fd(descriptor)


def _acquire_guard(path: Path, parent_descriptor: int) -> tuple[int, os.stat_result] | None:
    if fcntl is None:
        raise _lock_error()
    opened = _open_guard(path, parent_descriptor)
    if opened is None:
        return None
    descriptor, identity = opened
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        _release_guard(descriptor)
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
            return None
        raise
    # A replacement between the initial identity checks and flock must detach
    # without touching the successor file.
    if not _guard_identity_matches(path, identity, parent_descriptor):
        _release_guard(descriptor)
        return None
    return descriptor, identity


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or value.strip() != value:
        return None
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value,
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate guard field")
        result[key] = value
    return result


def _read_fd(descriptor: int) -> bytes | None:
    """Read one bounded payload from a retained fd, never through its path."""
    try:
        size = os.fstat(descriptor).st_size
        if size < 0 or size > _LOCK_MAX_BYTES:
            return None
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while total <= _LOCK_MAX_BYTES:
            chunk = os.read(descriptor, min(1024, _LOCK_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _LOCK_MAX_BYTES:
                return None
        return b"".join(chunks)
    except OSError:
        return None


def _encode_guard(payload: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if len(encoded) > _LOCK_MAX_BYTES:
        raise ValueError("guard payload is too large")
    return encoded


def _decode_guard(raw: bytes) -> dict[str, Any] | object:
    if raw == b"":
        return _EMPTY
    try:
        # The canonical contract is ASCII.  This also rejects an actual UTF-8
        # character even though json.loads could otherwise accept it.
        text = raw.decode("ascii")
        value = json.loads(text, object_pairs_hook=_json_no_duplicates)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return _INVALID
    if not isinstance(value, dict) or set(value) != _GUARD_FIELDS:
        return _INVALID
    if type(value.get("schema")) is not int or value["schema"] != _GUARD_SCHEMA:
        return _INVALID
    if value.get("state") not in _GUARD_STATES:
        return _INVALID
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return _INVALID
    created = _parse_utc(value.get("created_at"))
    if created is None:
        return _INVALID
    released_value = value.get("released_at")
    released = None if released_value is None else _parse_utc(released_value)
    if value["state"] == "active":
        if released_value is not None:
            return _INVALID
    elif released is None or released < created:
        return _INVALID
    token = value.get("owner_token")
    if not isinstance(token, str) or _LOCK_TOKEN_RE.fullmatch(token) is None:
        return _INVALID
    # New guard writes are canonical compact objects.  Reject whitespace and
    # alternate ordering as malformed evidence rather than normalizing it.
    try:
        if _encode_guard(value) != raw:
            return _INVALID
    except (TypeError, ValueError, OverflowError):
        return _INVALID
    value["_parsed_created_at"] = created
    value["_parsed_released_at"] = released
    return value


def _read_guard(descriptor: int) -> dict[str, Any] | object:
    raw = _read_fd(descriptor)
    if raw is None:
        return _INVALID
    return _decode_guard(raw)


def _decode_legacy(raw: bytes) -> dict[str, Any] | object:
    if raw == b"" or len(raw) > _LOCK_MAX_BYTES:
        return _INVALID
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_json_no_duplicates)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return _INVALID
    if not isinstance(value, dict) or set(value) != _LEGACY_LOCK_FIELDS:
        return _INVALID
    if type(value.get("schema")) is not int or value["schema"] != _LEGACY_SCHEMA:
        return _INVALID
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return _INVALID
    created = _parse_utc(value.get("created_at"))
    token = value.get("owner_token")
    if created is None or not isinstance(token, str) or _LOCK_TOKEN_RE.fullmatch(token) is None:
        return _INVALID
    value["_parsed_created_at"] = created
    return value


def _inspect_legacy(path: Path, parent_descriptor: int) -> dict[str, Any] | object:
    """Read one draft ``.lock`` artifact by fd; never remove or rewrite it."""
    flags = os.O_RDONLY | _optional_flag("O_NOFOLLOW") | _optional_flag("O_CLOEXEC")
    descriptor: int | None = None
    try:
        try:
            descriptor = _open_child(path, flags, parent_descriptor=parent_descriptor)
        except FileNotFoundError:
            return _EMPTY
        opened = os.fstat(descriptor)
        current = _lstat_child(path, parent_descriptor)
        getuid = getattr(os, "getuid", None)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or not _safe_mode(opened.st_mode)
            or not _safe_mode(current.st_mode)
            or not _same_inode(opened, current)
            or (callable(getuid) and opened.st_uid != getuid())
        ):
            return _INVALID
        raw = _read_fd(descriptor)
        if raw is None:
            return _INVALID
        return _decode_legacy(raw)
    except (OSError, TypeError, ValueError, OverflowError):
        return _INVALID
    finally:
        _close_fd(descriptor)


def _pid_liveness(pid: int) -> str:
    """Return ``alive``, ``dead``, or ``ambiguous`` without spawning."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "alive"
    except OverflowError:
        return "ambiguous"
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return "dead"
        if exc.errno == errno.EPERM:
            return "alive"
        return "ambiguous"
    except Exception:
        return "ambiguous"
    return "alive"


def _is_stale(payload: Mapping[str, Any], stale_after_seconds: int) -> bool:
    parsed = payload.get("_parsed_created_at")
    if not isinstance(parsed, datetime):
        return False
    now = datetime.now(timezone.utc)
    age = (now - parsed).total_seconds()
    if parsed > now or not math.isfinite(age) or age <= stale_after_seconds:
        return False
    return _pid_liveness(payload["pid"]) == "dead"


def _active_payload() -> dict[str, Any]:
    return {
        "schema": _GUARD_SCHEMA,
        "state": "active",
        "pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "released_at": None,
        "owner_token": secrets.token_hex(16),
    }


def _released_payload(active: Mapping[str, Any]) -> dict[str, Any]:
    created = active.get("created_at")
    released = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": _GUARD_SCHEMA,
        "state": "released",
        "pid": active["pid"],
        "created_at": created,
        "released_at": released,
        "owner_token": active["owner_token"],
    }


def _write_fd_payload(
    descriptor: int,
    payload: Mapping[str, Any],
    previous: bytes | None = None,
) -> None:
    """Write and fsync one payload through a retained fd only.

    A best-effort rollback keeps active evidence when a write/fsync fails.  No
    pathname operation is used, so a replaced successor cannot be touched.
    """
    encoded = _encode_guard(payload)
    if previous is None:
        previous = _read_fd(descriptor)
    if previous is None:
        previous = b""
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if not written:
                raise OSError(errno.EIO, "guard write failed")
            offset += written
        os.fsync(descriptor)
    except Exception:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            offset = 0
            while offset < len(previous):
                written = os.write(descriptor, previous[offset:])
                if not written:
                    break
                offset += written
            os.fsync(descriptor)
        except Exception:
            pass
        raise


class _MonitorLock:
    """Context-manager lease backed by one retained guard fd."""

    __slots__ = (
        "acquired", "reason", "_path", "_guard_descriptor", "_guard_identity",
        "_parent_descriptor", "_payload", "_released",
    )

    def __init__(
        self,
        *,
        acquired: bool,
        reason: str,
        path: Path,
        guard_descriptor: int | None = None,
        guard_identity: os.stat_result | None = None,
        parent_descriptor: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.acquired = acquired
        self.reason = reason
        self._path = path
        self._guard_descriptor = guard_descriptor
        self._guard_identity = guard_identity
        self._parent_descriptor = parent_descriptor
        self._payload = dict(payload) if payload is not None else None
        self._released = False

    def __enter__(self) -> "_MonitorLock":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        # Release is deliberately best-effort; returning False preserves the
        # body exception even if durable release evidence cannot be written.
        self.release()
        return False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        descriptor = self._guard_descriptor
        parent_descriptor = self._parent_descriptor
        try:
            if (
                self.acquired
                and descriptor is not None
                and self._guard_identity is not None
                and parent_descriptor is not None
                and self._payload is not None
                and self._payload.get("state") == "active"
                and _guard_identity_matches(self._path, self._guard_identity, parent_descriptor)
            ):
                current = _read_guard(descriptor)
                if (
                    isinstance(current, dict)
                    and current.get("state") == "active"
                    and current.get("owner_token") == self._payload.get("owner_token")
                    and current.get("pid") == self._payload.get("pid")
                ):
                    try:
                        _write_fd_payload(descriptor, _released_payload(current))
                    except Exception:
                        # Keep the active payload (rollback is best effort),
                        # then always unlock/close below.
                        pass
        finally:
            _release_guard(descriptor)
            self._guard_descriptor = None
            _close_fd(parent_descriptor)
            self._parent_descriptor = None


def monitor_lock(target: Any, *, stale_after_seconds: int = 1800) -> _MonitorLock:
    """Acquire one nonblocking persistent monitor lease for ``target``.

    The v2 ``.guard`` file is both the advisory ``flock`` liveness lock and
    durable state evidence.  A released marker remains on disk.  The draft
    v1 ``.lock`` is inspected once only while an empty guard is held, and is
    never deleted or replaced.
    """
    grace_valid = (
        not isinstance(stale_after_seconds, bool)
        and isinstance(stale_after_seconds, int)
        and 1 <= stale_after_seconds <= 86400
    )
    if not grace_valid:
        raise _lock_error("invalid_lock_grace")

    try:
        path = guard_path(target)
        legacy_path = lock_path(target)
        parent_descriptor = _prepare_lock_parent(path)
    except StorageMonitorLockError:
        raise
    except Exception:
        raise _lock_error() from None

    opened: tuple[int, os.stat_result] | None = None
    try:
        opened = _acquire_guard(path, parent_descriptor)
    except Exception:
        _close_fd(parent_descriptor)
        raise _lock_error() from None
    if opened is None:
        _close_fd(parent_descriptor)
        return _MonitorLock(acquired=False, reason="lock_held", path=path)

    descriptor, identity = opened
    transferred = False
    try:
        if not _guard_identity_matches(path, identity, parent_descriptor):
            return _MonitorLock(acquired=False, reason="lock_held", path=path)
        state = _read_guard(descriptor)
        if state is _INVALID:
            return _MonitorLock(acquired=False, reason="lock_held", path=path)

        reason = "acquired"
        if state is _EMPTY:
            # Legacy compatibility is intentionally one-way.  Once this guard
            # has any v2 state, the .lock artifact is not consulted again.
            legacy = _inspect_legacy(legacy_path, parent_descriptor)
            if legacy is _INVALID:
                return _MonitorLock(acquired=False, reason="lock_held", path=path)
            if legacy is not _EMPTY:
                if not _is_stale(legacy, stale_after_seconds):
                    return _MonitorLock(acquired=False, reason="lock_held", path=path)
                reason = "stale_lock_recovered"
        elif not isinstance(state, dict):
            return _MonitorLock(acquired=False, reason="lock_held", path=path)
        elif state.get("state") == "active":
            if not _is_stale(state, stale_after_seconds):
                return _MonitorLock(acquired=False, reason="lock_held", path=path)
            reason = "stale_lock_recovered"
        elif state.get("state") != "released":
            return _MonitorLock(acquired=False, reason="lock_held", path=path)

        if not _guard_identity_matches(path, identity, parent_descriptor):
            return _MonitorLock(acquired=False, reason="lock_held", path=path)
        payload = _active_payload()
        try:
            previous = _read_fd(descriptor)
            if previous is None:
                raise OSError(errno.EIO, "guard read failed")
            _write_fd_payload(descriptor, payload, previous)
        except Exception:
            raise _lock_error() from None
        # If the pathname changed while we were writing, detach the old fd and
        # do not write/recover the successor.  The caller observes contention.
        if not _guard_identity_matches(path, identity, parent_descriptor):
            return _MonitorLock(acquired=False, reason="lock_held", path=path)
        result = _MonitorLock(
            acquired=True,
            reason=reason,
            path=path,
            guard_descriptor=descriptor,
            guard_identity=identity,
            parent_descriptor=parent_descriptor,
            payload=payload,
        )
        transferred = True
        return result
    finally:
        if not transferred:
            _release_guard(descriptor)
            _close_fd(parent_descriptor)


def _assert_json_value(value: Any, seen: set[int] | None = None) -> None:
    """Reject Python values that are not finite, native JSON values."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("record is invalid")
        return
    if not isinstance(value, (dict, list)):
        raise ValueError("record is invalid")

    active = set() if seen is None else seen
    identity = id(value)
    if identity in active:
        raise ValueError("record is invalid")
    active.add(identity)
    try:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("record is invalid")
            for item in value.values():
                _assert_json_value(item, active)
        else:
            for item in value:
                _assert_json_value(item, active)
    finally:
        active.remove(identity)


def _record_number(value: Any, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("record is invalid")
    if not math.isfinite(float(value)):
        raise ValueError("record is invalid")


def _record_integer(value: Any, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("record is invalid")


def _record_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("record is invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("record is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("record is invalid")


def _validate_record_shape(value: Any) -> dict[str, Any]:
    """Validate one persisted/candidate record without contacting a target."""
    if not isinstance(value, dict):
        raise ValueError("record is invalid")
    if set(value) - _RECORD_FIELDS:
        raise ValueError("record is invalid")
    if value.get("schema") != 1 or type(value.get("schema")) is not int:
        raise ValueError("record is invalid")
    if "target" not in value:
        raise ValueError("record is invalid")

    _assert_json_value(value)
    target = value["target"]
    if not isinstance(target, dict) or set(target) != {"kind", "name"}:
        raise ValueError("record is invalid")
    try:
        _kind, _name, safe_target = _target_descriptor(target)
    except (TypeError, ValueError):
        raise ValueError("record is invalid") from None
    if target != safe_target:
        raise ValueError("record is invalid")

    if "at" in value:
        _record_timestamp(value["at"])
    if "trigger" in value and value["trigger"] not in _TRIGGERS:
        raise ValueError("record is invalid")
    if "level" in value and value["level"] not in _LEVELS:
        raise ValueError("record is invalid")
    for field in ("free_bytes", "total_bytes"):
        if field in value:
            _record_integer(value[field], nullable=True)
    if "free_ratio" in value:
        _record_number(value["free_ratio"], nullable=True)
        if value["free_ratio"] is not None and not 0 <= value["free_ratio"] <= 1:
            raise ValueError("record is invalid")
    for field in ("warn_ratio", "critical_ratio", "auto_ratio"):
        if field in value:
            _record_number(value[field])
            if not 0 < value[field] < 1:
                raise ValueError("record is invalid")
    if "threshold_crossed" in value:
        crossed = value["threshold_crossed"]
        if crossed is not None and crossed not in _THRESHOLDS:
            raise ValueError("record is invalid")
    for field in ("guidance", "inventory_status"):
        if field in value and not isinstance(value[field], str):
            raise ValueError("record is invalid")

    if "auto" in value:
        auto = value["auto"]
        if not isinstance(auto, dict) or set(auto) - _AUTO_FIELDS:
            raise ValueError("record is invalid")
    if "reap" in value:
        reap = value["reap"]
        if not isinstance(reap, dict) or set(reap) - _REAP_FIELDS:
            raise ValueError("record is invalid")
    if "errors" in value:
        errors = value["errors"]
        if not isinstance(errors, list):
            raise ValueError("record is invalid")
        for error in errors:
            if not isinstance(error, dict) or set(error) - _ERROR_FIELDS:
                raise ValueError("record is invalid")
            if any(not isinstance(error[field], str) for field in error):
                raise ValueError("record is invalid")
    return value


def _safe_record(record: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    if not isinstance(record, Mapping):
        raise ValueError("record is invalid")
    payload = dict(record)
    if "target" not in payload or any(
        not isinstance(key, str) or key not in _RECORD_FIELDS for key in payload
    ):
        raise ValueError("record is invalid")
    try:
        _kind, _name, safe_target = _target_descriptor(payload.get("target"))
    except (TypeError, ValueError):
        raise ValueError("record is invalid") from None
    # The record contract contains only kind/name for a target.  Rebuild this
    # field so a caller cannot persist an SSH connection or another sensitive
    # registry field by accident.
    payload["target"] = safe_target
    _validate_record_shape(payload)
    try:
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("record is invalid") from None
    return payload, record_path(safe_target)


def write_record(record: Mapping[str, Any]) -> Path:
    """Atomically replace the one last-run record for ``record['target']``."""
    payload, path = _safe_record(record)
    root = path.parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=root,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
        # Sync the directory entry where the platform supports directory fsync.
        try:
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            pass
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise
    return path


def read_record(target: Any) -> dict[str, Any] | None:
    """Read one record, returning ``None`` for missing/corrupt evidence."""
    try:
        _kind, _name, safe_target = _target_descriptor(target)
        path = record_path(safe_target)
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        _validate_record_shape(value)
        # A record at a valid digest path is still not evidence if its
        # embedded identity names another target (or is non-canonical).
        if value.get("target") != safe_target:
            return None
        return value
    except (OSError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
        # A monitor/doctor surface must fail closed without echoing corrupt
        # payload or filesystem details to an operator.
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        # Timestamps in the record contract are UTC ISO-8601.  Treat a naive
        # ``now`` supplied by a test/caller as UTC, but reject naive evidence.
        return parsed.replace(tzinfo=timezone.utc) if isinstance(value, datetime) else None
    return parsed.astimezone(timezone.utc)


def record_age_seconds(record: Mapping[str, Any] | None, now: Any = None) -> float | None:
    """Return non-negative age in seconds, or ``None`` for invalid evidence."""
    if not isinstance(record, Mapping):
        return None
    try:
        stamped = _as_datetime(record.get("at"))
    except (TypeError, ValueError, OverflowError):
        return None
    if stamped is None:
        return None
    current = datetime.now(timezone.utc) if now is None else _as_datetime(now)
    if current is None:
        return None
    return max(0.0, (current - stamped).total_seconds())


# ---------------------------------------------------------------------------
# Offline doctor evidence
# ---------------------------------------------------------------------------

_MISSING = object()


def _doctor_refresh_command(kind: str, name: str) -> str:
    """Return the fixed, replayable monitor command for one target."""
    if kind == "local":
        return "sb resources monitor --json"
    # Remote names have already passed the strict local registry grammar.
    return f"sb resources monitor --remote {name} --json"


def _doctor_age_text(seconds: float | int) -> str:
    """Render a bounded age without exposing timestamps or filesystem data."""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError, OverflowError):
        return "unknown age"
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value / 60:.1f}m"
    if value < 86400:
        return f"{value / 3600:.1f}h"
    return f"{value / 86400:.1f}d"


def _doctor_human_bytes(value: int | None) -> str:
    """Format only validated public byte counts for a doctor hint."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "unknown"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "unknown"


def _doctor_valid_remote_name(value: Any) -> bool:
    """Validate a configured key without consulting the remote registry."""
    return (
        isinstance(value, str)
        and value != "local"
        and _REMOTE_NAME_RE.fullmatch(value) is not None
    )


def _doctor_record(record: Any, target: Mapping[str, str]) -> dict[str, Any] | None:
    """Validate the evidence fields doctor needs, without exposing payloads."""
    if not isinstance(record, Mapping):
        return None
    try:
        candidate = dict(record)
        _validate_record_shape(candidate)
    except (TypeError, ValueError, RecursionError):
        return None

    # ``read_record`` checks this too, but retaining the identity check here is
    # important for injected/read-only test seams and for future stores.
    if candidate.get("target") != dict(target):
        return None
    if "at" not in candidate or "level" not in candidate:
        return None
    if candidate.get("level") not in _LEVELS:
        return None

    level = candidate["level"]
    if level == "unknown":
        # Unknown capacity may legitimately carry null measurements.  If a
        # value is present, however, it must have the same safe integer shape
        # as a measured record.
        for field in ("free_bytes", "total_bytes"):
            if field in candidate:
                try:
                    _record_integer(candidate[field], nullable=True)
                except ValueError:
                    return None
        return candidate

    # A normal/warning/critical result must carry measured, internally sane
    # capacity numbers.  ``free_ratio`` is optional for backwards-compatible
    # records; doctor derives it from these two public counters.
    free = candidate.get("free_bytes")
    total = candidate.get("total_bytes")
    if (
        isinstance(free, bool)
        or not isinstance(free, int)
        or free < 0
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or free > total
    ):
        return None
    return candidate


def _doctor_capacity(record: Mapping[str, Any]) -> tuple[int, int, float] | None:
    """Return validated public capacity values, or ``None`` if unmeasured."""
    free = record.get("free_bytes")
    total = record.get("total_bytes")
    if (
        isinstance(free, bool)
        or not isinstance(free, int)
        or free < 0
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or free > total
    ):
        return None
    return free, total, free / total


def _doctor_guidance(record: Mapping[str, Any], level: str) -> str:
    """Use classifier-owned guidance only; otherwise use a safe generic line."""
    guidance = record.get("guidance")
    if isinstance(guidance, str) and guidance in _SAFE_GUIDANCE:
        return guidance
    if level == "warning":
        return "review the safe-tier plan before reclaiming"
    if level == "critical":
        return "review the safe-tier plan before reclaiming"
    if level == "unknown":
        return "capacity is unmeasured; refresh the monitor"
    return "no action required"


def _doctor_pressure_hint(
    record: Mapping[str, Any], *, refresh: str,
) -> str:
    """Build a secret/path-free warning from validated public fields."""
    level = record.get("level")
    capacity = _doctor_capacity(record)
    if capacity is None:
        summary = f"{str(level).upper()}: capacity is unmeasured"
    else:
        free, total, ratio = capacity
        summary = (
            f"{str(level).upper()}: {_doctor_human_bytes(free)} free of "
            f"{_doctor_human_bytes(total)} ({ratio * 100:.1f}%)"
        )
        threshold = record.get("threshold_crossed")
        if threshold in _THRESHOLDS:
            summary += f"; threshold {threshold}"
    guidance = _doctor_guidance(record, str(level))
    return f"{summary}; {guidance}; refresh with {refresh}"


def _doctor_policy(base_config: Any, remote_entry: Any = None) -> dict[str, Any]:
    """Resolve one policy from already-loaded local config and registry data.

    This intentionally does not call :func:`resolve_policy` or ``get_remote``:
    doctor must remain an offline check.  Only the ``resources.monitor`` block
    is copied into the manifest provider, so unrelated remote fields (including
    credentials) never become diagnostic output or error context.
    """
    if not isinstance(base_config, Mapping):
        raise StorageMonitorConfigError(
            "machine configuration must be an object", "invalid_schedule_field",
        )

    resources = base_config.get("resources", _MISSING)
    if resources is _MISSING:
        raw_resources: Any = {}
        base_monitor: Any = _MISSING
    elif isinstance(resources, Mapping):
        raw_resources = {}
        base_monitor = resources.get("monitor", _MISSING)
        if base_monitor is not _MISSING:
            raw_resources["monitor"] = copy.deepcopy(base_monitor)
    else:
        # Preserve the provider's normal refusal for a malformed resources
        # layer, but do not pass unrelated config fields through.
        raw_resources = resources
        base_monitor = _MISSING

    if remote_entry is not None:
        if not isinstance(remote_entry, Mapping):
            raise StorageMonitorConfigError(
                "remote storage monitor configuration is invalid",
                "invalid_schedule_field",
            )
        override = remote_entry.get("storage_monitor", _MISSING)
        if override is not _MISSING and override is not None:
            if isinstance(raw_resources, Mapping):
                if isinstance(base_monitor, Mapping) and isinstance(override, Mapping):
                    merged = dict(base_monitor)
                    merged.update(dict(override))
                    raw_resources["monitor"] = merged
                else:
                    raw_resources["monitor"] = copy.deepcopy(override)
            else:
                # Let apply_machine_config report malformed machine resources;
                # no target is considered healthy on this path.
                raw_resources = resources

    if isinstance(raw_resources, Mapping):
        config = {"resources": raw_resources}
    elif raw_resources is not _MISSING:
        config = {"resources": raw_resources}
    else:
        config = {}
    resolved = apply_machine_config(config)
    try:
        policy = resolved["resources"]["monitor"]
    except (KeyError, TypeError):
        raise StorageMonitorConfigError(
            "resolved storage monitor policy is missing", "invalid_schedule_field",
        ) from None
    if not isinstance(policy, Mapping):
        raise StorageMonitorConfigError(
            "resolved storage monitor policy is invalid", "invalid_schedule_field",
        )
    return dict(policy)


def _doctor_target_row(
    *, label: str, kind: str, name: str, base_config: Any,
    remote_entry: Any = None, malformed: bool = False,
) -> dict[str, Any]:
    """Build one deterministic doctor row while fail-closing all evidence errors."""
    refresh = _doctor_refresh_command(kind, name)
    target = _LOCAL_TARGET if kind == "local" else {
        "kind": "remote", "name": name,
    }
    if malformed:
        return {
            "label": label,
            "ok": False,
            "hint": "invalid configured remote name; repair local configuration before refreshing",
        }
    try:
        policy = _doctor_policy(base_config, remote_entry)
    except Exception:
        # Configuration errors are intentionally bounded and secret-free.  A
        # bad policy is never converted into healthy evidence.
        return {
            "label": label,
            "ok": False,
            "hint": f"storage-monitor policy is invalid; refresh with {refresh} after fixing local configuration",
        }

    try:
        record = read_record(target)
    except Exception:
        record = None
    record = _doctor_record(record, target)
    if record is None:
        return {
            "label": label,
            "ok": False,
            "hint": f"no valid monitor run recorded; refresh with {refresh}",
        }

    try:
        age = record_age_seconds(record)
    except Exception:
        age = None
    if (
        isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not math.isfinite(float(age))
        or age < 0
    ):
        age = None
    max_age = policy.get("record_max_age_seconds")
    if age is None or isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        return {
            "label": label,
            "ok": False,
            "hint": f"monitor record age is unknown; refresh with {refresh}",
        }
    if age > max_age:
        stale_hint = (
            f"monitor record is stale (age {_doctor_age_text(age)}; "
            f"maximum {_doctor_age_text(max_age)});"
        )
        if record.get("level") in {"warning", "critical", "unknown"}:
            stale_hint += f" {_doctor_pressure_hint(record, refresh=refresh)}"
        else:
            stale_hint += f" refresh with {refresh}"
        return {
            "label": label,
            "ok": False,
            "hint": stale_hint,
        }

    level = record.get("level")
    if level in {"warning", "critical", "unknown"}:
        return {
            "label": label,
            "ok": False,
            "hint": _doctor_pressure_hint(record, refresh=refresh),
        }
    if level != "normal":
        return {
            "label": label,
            "ok": False,
            "hint": f"monitor record has an unknown level; refresh with {refresh}",
        }
    return {"label": label, "ok": True, "hint": ""}


def storage_doctor_checks() -> list[dict[str, Any]]:
    """Return offline storage-pressure checks for local and configured targets.

    ``list_remotes`` and ``load_config`` read only the operator's local
    configuration.  Every health decision then comes from the matching local
    record; no remote lookup, SSH, subprocess, or host probe is performed.
    """
    remote_config_error = False
    try:
        configured = list_remotes()
    except Exception:
        configured = {}
        remote_config_error = True
    if not isinstance(configured, Mapping):
        configured = {}
        remote_config_error = True
    try:
        base_config = load_config()
    except Exception:
        base_config = None

    rows = [
        _doctor_target_row(
            label="local", kind="local", name="local", base_config=base_config,
        )
    ]
    if remote_config_error:
        rows.append({
            "label": "remote configuration",
            "ok": False,
            "hint": "configured remotes could not be read; repair local configuration before refreshing",
        })

    valid: list[tuple[str, Any]] = []
    invalid: list[tuple[Any, Any]] = []
    try:
        items = list(configured.items())
    except Exception:
        items = []
        if not remote_config_error:
            rows.append({
                "label": "remote configuration",
                "ok": False,
                "hint": "configured remotes could not be read; repair local configuration before refreshing",
            })
    for name, entry in items:
        if _doctor_valid_remote_name(name):
            valid.append((name, entry))
        else:
            invalid.append((name, entry))
    valid.sort(key=lambda item: item[0])
    # Sorting malformed keys by a bounded type/name token keeps output stable
    # without ever echoing an invalid key (which could contain a path/secret).
    invalid.sort(key=lambda item: (
        type(item[0]).__name__,
        item[0] if isinstance(item[0], str) else type(item[0]).__name__,
    ))

    for name, entry in valid:
        rows.append(
            _doctor_target_row(
                label=name, kind="remote", name=name, base_config=base_config,
                remote_entry=entry,
            )
        )
    for index, (_name, _entry) in enumerate(invalid, start=1):
        suffix = f" #{index}" if len(invalid) > 1 else ""
        rows.append(
            _doctor_target_row(
                label=f"remote (invalid name){suffix}",
                kind="remote", name="invalid", base_config=base_config,
                malformed=True,
            )
        )
    return rows


__all__ = [
    "guard_path",
    "StorageMonitorLockError",
    "lock_path",
    "monitor_lock",
    "read_record",
    "record_age_seconds",
    "record_path",
    "resolve_policy",
    "storage_doctor_checks",
    "write_record",
]
