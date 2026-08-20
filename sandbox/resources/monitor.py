"""Storage-monitor policy resolution and last-run record storage.

Policy resolution is deliberately a configuration-only operation.  It reads
the machine configuration through the owning config helpers, validates a
named remote through the owning remote registry, and only then asks the
machine-config manifest to normalize one merged policy.  The record store is
the only persistence owned by this module; it keeps one private, atomically
replaced JSON document per target.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

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


__all__ = [
    "read_record",
    "record_age_seconds",
    "record_path",
    "resolve_policy",
    "write_record",
]
