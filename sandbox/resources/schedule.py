"""Render and confirmation-gate the host storage-monitor schedule.

The schedule is deliberately a local-controller concern.  A plan is pure and
installs nothing; activation is a separate, explicit operation that accepts
only a plan produced by this module and only the fixed monitor argv.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Any

from sandbox.config.storage_monitor import normalize_storage_monitor


class ScheduleError(ValueError):
    """A schedule plan or protected lifecycle operation was rejected."""

    def __init__(self, message: str, code: str = "invalid_schedule_plan", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PLATFORMS = frozenset({"systemd", "launchd"})
_POLICY_FIELDS = (
    "schedule_calendar",
    "schedule_randomized_delay",
    "schedule_timeout",
)
_UNIT_MODE = {"systemd": 0o644, "launchd": 0o600}
_MAX_SCHEDULE_FILE_BYTES = 256 * 1024
_LAUNCHD_CALENDARS = {
    "hourly": {"Minute": 0},
    "daily": {"Hour": 0, "Minute": 0},
    "weekly": {"Weekday": 0, "Hour": 0, "Minute": 0},
    "monthly": {"Day": 1, "Hour": 0, "Minute": 0},
}
_COMMAND_PREFIX = ("sb", "resources", "monitor", "--scheduled", "--json")


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    # Recovery's immutable SchedulePolicy is useful in tests and for callers
    # that already have a typed policy.  Do not accept arbitrary objects.
    if all(hasattr(value, item) for item in _POLICY_FIELDS):
        return {item: getattr(value, item) for item in _POLICY_FIELDS}
    # ``sandbox.recovery.models.SchedulePolicy`` predates storage-monitor
    # naming and calls these fields calendar/randomized_delay/timeout.
    if all(hasattr(value, item) for item in ("calendar", "randomized_delay", "timeout")):
        return {
            "schedule_calendar": getattr(value, "calendar"),
            "schedule_randomized_delay": getattr(value, "randomized_delay"),
            "schedule_timeout": getattr(value, "timeout"),
        }
    raise ScheduleError(f"schedule {field} must be an object", "invalid_schedule_plan")


def _target(target: Any) -> dict[str, str]:
    if isinstance(target, str):
        value = {"kind": "local", "name": target.strip()}
    elif isinstance(target, Mapping):
        value = {"kind": target.get("kind"), "name": target.get("name")}
    elif all(hasattr(target, item) for item in ("kind", "name")):
        value = {"kind": getattr(target, "kind"), "name": getattr(target, "name")}
    else:
        raise ScheduleError("schedule target is invalid", "invalid_target")
    kind, name = value.get("kind"), value.get("name")
    if kind not in {"local", "remote"} or not isinstance(name, str):
        raise ScheduleError("schedule target is invalid", "invalid_target")
    name = name.strip()
    if kind == "local" and name != "local":
        raise ScheduleError("local schedule target must be local", "invalid_target")
    if not _NAME.fullmatch(name):
        raise ScheduleError("schedule target name is invalid", "invalid_target")
    return {"kind": kind, "name": name}


def normalize_platform(platform: str | None = None) -> str:
    """Map an OS name to the supported user scheduler."""
    value = sys.platform if platform is None else str(platform).strip().lower()
    if value in {"linux", "linux2", "systemd"} or value.startswith("linux"):
        return "systemd"
    if value in {"darwin", "macos", "osx", "launchd"}:
        return "launchd"
    raise ScheduleError("storage-monitor scheduling is unsupported on this platform", "unsupported_platform")


def _digest(target: Mapping[str, str]) -> str:
    return hashlib.sha256(f"{target['kind']}:{target['name']}".encode("utf-8")).hexdigest()[:24]


def _command(target: Mapping[str, str], command: Sequence[str] | None = None) -> list[str]:
    expected = list(_COMMAND_PREFIX)
    if target["kind"] == "remote":
        expected.extend(("--remote", target["name"]))
    if command is None:
        return expected
    if isinstance(command, (str, bytes)):
        raise ScheduleError("schedule command must use the fixed monitor argv", "invalid_schedule_command")
    try:
        supplied = list(command)
    except TypeError:
        raise ScheduleError("schedule command must use the fixed monitor argv", "invalid_schedule_command") from None
    if supplied != expected or any(not isinstance(item, str) for item in supplied):
        raise ScheduleError("schedule command must use the fixed monitor argv", "invalid_schedule_command")
    return expected


def _paths(target: Mapping[str, str], platform: str, digest: str) -> tuple[dict[str, str], dict[str, str]]:
    if platform == "systemd":
        directory = Path.home() / ".config" / "systemd" / "user"
        names = {
            "service": f"sandbox-storage-monitor-{digest}.service",
            "timer": f"sandbox-storage-monitor-{digest}.timer",
        }
    else:
        directory = Path.home() / "Library" / "LaunchAgents"
        names = {"plist": f"com.wpdeveloper.sandbox.storage-monitor.{digest}.plist"}
    return names, {key: str(directory / name) for key, name in names.items()}


def _systemd_units(policy: Mapping[str, Any], target: Mapping[str, str], digest: str, command: list[str]) -> dict[str, str]:
    names, _ = _paths(target, "systemd", digest)
    shell_command = " ".join(
        # Values are slugs or validated schedule fields.  shlex.join is
        # intentionally used instead of hand-built quoting for future-safe
        # calendar/target values.
        shlex.quote(item) for item in command
    )
    service = "\n".join((
        "[Unit]",
        f"Description=Sandbox storage pressure monitor ({target['name']})",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "UMask=0077",
        f"TimeoutStartSec={policy['schedule_timeout']}",
        f"ExecStart=/usr/bin/env {shell_command}",
        "",
    ))
    timer = "\n".join((
        "[Unit]",
        f"Description=Sandbox storage pressure monitor timer ({target['name']})",
        "",
        "[Timer]",
        f"OnCalendar={policy['schedule_calendar']}",
        f"RandomizedDelaySec={policy['schedule_randomized_delay']}",
        "Persistent=true",
        f"Unit={names['service']}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ))
    return names | {"service": service, "timer": timer}


def _launchd_unit(policy: Mapping[str, Any], target: Mapping[str, str], command: list[str]) -> dict[str, str]:
    calendar = str(policy["schedule_calendar"]).strip().lower()
    interval = _LAUNCHD_CALENDARS.get(calendar)
    if interval is None:
        raise ScheduleError(
            "launchd cannot represent this storage-monitor calendar safely",
            "unsupported_calendar",
        )
    digest = _digest(target)
    names, _ = _paths(target, "launchd", digest)
    label = names["plist"][:-6]
    payload: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": command,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "StartCalendarInterval": interval,
        "Comment": (
            f"Sandbox storage monitor; requested randomized delay "
            f"{policy['schedule_randomized_delay']} (launchd has no native jitter)"
        ),
    }
    content = plistlib.dumps(payload, sort_keys=True).decode("utf-8")
    return names | {"plist": content}


def build_schedule_plan(
    policy: Mapping[str, Any] | Any,
    target: Mapping[str, str] | Any,
    platform: str | None = None,
    *,
    command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a disabled, install-free schedule plan.

    ``command`` exists as a test/extension seam, but any value other than the
    exact monitor argv is rejected.  The returned unit contents are strings;
    activation performs all filesystem writes separately.
    """
    target_value = _target(target)
    platform_value = normalize_platform(platform)
    raw_policy = _mapping(policy, "policy")
    try:
        normalized = normalize_storage_monitor(raw_policy)
    except Exception as exc:
        code = getattr(exc, "code", "invalid_schedule_field")
        raise ScheduleError(str(exc), code) from exc
    argv = _command(target_value, command)
    digest = _digest(target_value)
    names, paths = _paths(target_value, platform_value, digest)
    if platform_value == "systemd":
        rendered = _systemd_units(normalized, target_value, digest, argv)
    else:
        rendered = _launchd_unit(normalized, target_value, argv)
    # The public contract keys both maps by the actual filename.  Keeping the
    # role-to-filename mapping local lets lifecycle commands remain clear while
    # avoiding a second, potentially divergent path spelling in the receipt.
    units = {names[key]: rendered[key] for key in names}
    file_paths = {names[key]: paths[key] for key in names}
    activate_command, deactivate_command = _lifecycle_commands(
        platform_value, names, paths,
    )
    plan: dict[str, Any] = {
        "schema_version": 1,
        "action": "schedule",
        "status": "planned",
        "target": target_value,
        "platform": platform_value,
        "enabled": False,
        "calendar": normalized["schedule_calendar"],
        "randomized_delay": normalized["schedule_randomized_delay"],
        "timeout": normalized["schedule_timeout"],
        "command": argv,
        "units": units,
        "paths": file_paths,
        "activate_command": activate_command,
        "deactivate_command": deactivate_command,
        "activation_supported": platform_value == "systemd",
        "timeout_enforced": platform_value == "systemd",
    }
    if platform_value == "launchd":
        plan["limitations"] = [
            "launchd has no native randomized-delay primitive",
            "activation is refused because launchd cannot enforce schedule_timeout",
        ]
    return plan


def _lifecycle_commands(platform: str, names: Mapping[str, str], paths: Mapping[str, str]) -> tuple[list[str], list[str]]:
    if platform == "systemd":
        return (
            ["systemctl", "--user", "enable", "--now", names["timer"]],
            ["systemctl", "--user", "disable", "--now", names["timer"]],
        )
    domain = f"gui/{os.getuid()}"
    return (
        ["launchctl", "bootstrap", domain, paths["plist"]],
        ["launchctl", "bootout", domain, paths["plist"]],
    )


def _canonical_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ScheduleError("schedule plan is invalid", "invalid_schedule_plan")
    if plan.get("schema_version") != 1 or plan.get("action") != "schedule":
        raise ScheduleError("schedule plan is invalid", "invalid_schedule_plan")
    if not isinstance(plan.get("target"), Mapping):
        raise ScheduleError("schedule plan is invalid", "invalid_schedule_plan")
    if plan.get("platform") not in _PLATFORMS:
        raise ScheduleError("schedule plan platform is invalid", "invalid_schedule_plan")
    target = _target(plan.get("target"))
    platform = normalize_platform(plan.get("platform"))
    policy = {
        "schedule_calendar": plan.get("calendar"),
        "schedule_randomized_delay": plan.get("randomized_delay"),
        "schedule_timeout": plan.get("timeout"),
    }
    canonical = build_schedule_plan(policy, target, platform, command=plan.get("command"))
    for field in ("units", "paths", "activate_command", "deactivate_command"):
        if plan.get(field) != canonical[field]:
            raise ScheduleError("schedule plan does not match its fixed renderer", "invalid_schedule_plan")
    return canonical


def _result(plan: Mapping[str, Any], *, ok: bool, status: str, error: ScheduleError | None = None, **data: Any) -> dict[str, Any]:
    base = dict(plan) if isinstance(plan, Mapping) else {}
    return {
        "schema_version": 1,
        "ok": bool(ok),
        "action": "schedule",
        "status": status,
        "target": base.get("target"),
        "data": {**base, **data},
        "error": None if error is None else {
            "code": error.code,
            "message": str(error),
            "retryable": bool(error.retryable),
        },
    }


def _protected(plan: Mapping[str, Any], operation: str) -> dict[str, Any]:
    return _result(
        plan,
        ok=False,
        status="refused",
        error=ScheduleError(
            f"{operation} a storage-monitor timer is a protected operation; re-run with --confirm",
            "protected_operation",
        ),
    )


def _scheduler_root(platform: str, *, create: bool) -> Path:
    home = Path.home()
    parts = (
        (".config", "systemd", "user")
        if platform == "systemd" else ("Library", "LaunchAgents")
    )
    try:
        home_state = home.lstat()
    except OSError as exc:
        raise ScheduleError("scheduler home is unavailable", "unsafe_schedule_path") from exc
    if (not stat.S_ISDIR(home_state.st_mode) or stat.S_ISLNK(home_state.st_mode)
            or home_state.st_uid != os.getuid()
            or stat.S_IMODE(home_state.st_mode) & 0o022):
        raise ScheduleError("scheduler home ownership or mode is unsafe", "unsafe_schedule_path")

    current = home
    for part in parts:
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            if not create:
                raise ScheduleError(
                    "installed schedule identity evidence is unavailable",
                    "schedule_evidence_unknown",
                ) from None
            try:
                current.mkdir(mode=0o700)
                state = current.lstat()
            except OSError as exc:
                raise ScheduleError(
                    "scheduler directory could not be created",
                    "schedule_write_failed",
                    retryable=True,
                ) from exc
        except OSError as exc:
            raise ScheduleError("scheduler directory is unsafe", "unsafe_schedule_path") from exc
        if (not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode)
                or state.st_uid != os.getuid()
                or stat.S_IMODE(state.st_mode) != 0o700):
            raise ScheduleError(
                "scheduler directory ownership or mode is unsafe",
                "unsafe_schedule_path",
            )
    return current


def _safe_unit_path(path: str, platform: str, *, create_root: bool = False) -> Path:
    candidate = Path(path)
    digest_root = _scheduler_root(platform, create=create_root)
    if candidate.parent != digest_root or candidate.name in {"", ".", ".."}:
        raise ScheduleError("schedule unit path is outside the user scheduler directory", "unsafe_schedule_path")
    return candidate


def _receipt_path(target: Mapping[str, str], platform: str) -> Path:
    digest = _digest(target)
    root = (
        Path.home() / ".config" / "systemd" / "user"
        if platform == "systemd" else Path.home() / "Library" / "LaunchAgents"
    )
    return root / f"sandbox-storage-monitor-{digest}.installed.json"


def _safe_receipt_path(
    target: Mapping[str, str], platform: str, *, create_root: bool = False,
) -> Path:
    return _safe_unit_path(
        str(_receipt_path(target, platform)), platform, create_root=create_root,
    )


def _existing_file_state(path: Path, expected_mode: int) -> os.stat_result | None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ScheduleError("schedule path could not be inspected", "unsafe_schedule_path") from exc
    if (not stat.S_ISREG(state.st_mode) or stat.S_ISLNK(state.st_mode)
            or state.st_uid != os.getuid()
            or stat.S_IMODE(state.st_mode) != expected_mode):
        raise ScheduleError(
            "schedule file ownership, type, or mode is unsafe",
            "unsafe_schedule_path",
        )
    return state


def _bounded_file_bytes(path: Path, expected_mode: int) -> bytes | None:
    """Read one scheduler file without an unbounded allocation or decode leak."""
    state = _existing_file_state(path, expected_mode)
    if state is None:
        return None
    if state.st_size > _MAX_SCHEDULE_FILE_BYTES:
        raise ScheduleError(
            "schedule file content is invalid",
            "schedule_content_mismatch",
        )
    try:
        with path.open("rb") as handle:
            content = handle.read(_MAX_SCHEDULE_FILE_BYTES + 1)
    except OSError as exc:
        raise ScheduleError("schedule file could not be read", "unsafe_schedule_path") from exc
    if len(content) > _MAX_SCHEDULE_FILE_BYTES:
        raise ScheduleError(
            "schedule file content is invalid",
            "schedule_content_mismatch",
        )
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise ScheduleError(
            "schedule file content is invalid",
            "schedule_content_mismatch",
        ) from None
    return content


def _receipt_content(plan: Mapping[str, Any]) -> str:
    return json.dumps(dict(plan), sort_keys=True, separators=(",", ":")) + "\n"


def _load_receipt(target: Mapping[str, str], platform: str) -> tuple[dict[str, Any], Path]:
    path = _safe_receipt_path(target, platform)
    try:
        content = _bounded_file_bytes(path, 0o600)
        if content is None:
            raise OSError
        raw = json.loads(content.decode("utf-8"))
        canonical = _canonical_plan(raw)
    except Exception:
        raise ScheduleError(
            "installed schedule identity evidence is unavailable or invalid",
            "schedule_evidence_unknown",
        ) from None
    if canonical["target"] != dict(target) or canonical["platform"] != platform:
        raise ScheduleError(
            "installed schedule identity does not match the requested target",
            "schedule_evidence_unknown",
        )
    return canonical, path


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    platform = "systemd" if path.parent == Path.home() / ".config" / "systemd" / "user" else "launchd"
    root = _scheduler_root(platform, create=True)
    if path.parent != root or path.name in {"", ".", ".."}:
        raise ScheduleError("schedule unit path is outside the user scheduler directory", "unsafe_schedule_path")
    _existing_file_state(path, mode)
    descriptor = -1
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, mode)
    except OSError as exc:
        raise ScheduleError("schedule unit could not be written", "schedule_write_failed", retryable=True) from exc
    finally:
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


def _write_unit(path: Path, content: str, mode: int) -> None:
    _atomic_write(path, content.encode("utf-8"), mode)


def _snapshot_installation(
    paths: Mapping[Path, int],
) -> dict[Path, tuple[bytes, int] | None]:
    """Capture the complete pre-write state for transactional restoration."""
    snapshot: dict[Path, tuple[bytes, int] | None] = {}
    for path, expected_mode in paths.items():
        try:
            content = _bounded_file_bytes(path, expected_mode)
            if content is None:
                snapshot[path] = None
                continue
            snapshot[path] = (content, expected_mode)
        except ScheduleError:
            raise
        except OSError as exc:
            raise ScheduleError(
                "schedule installation could not be captured",
                "schedule_write_failed",
                retryable=True,
            ) from exc
    return snapshot


def _restore_installation(snapshot: Mapping[Path, tuple[bytes, int] | None]) -> None:
    """Restore all paths after a failed pre-transition write."""
    try:
        for path, prior in snapshot.items():
            if prior is None:
                platform = "systemd" if path.parent == Path.home() / ".config" / "systemd" / "user" else "launchd"
                root = _scheduler_root(platform, create=False)
                if path.parent != root:
                    raise OSError
                _existing_file_state(path, _UNIT_MODE[platform] if path.suffix != ".json" else 0o600)
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                content, mode = prior
                _atomic_write(path, content, mode)
    except (OSError, ScheduleError) as exc:
        raise ScheduleError(
            "schedule installation rollback did not complete",
            "schedule_rollback_failed",
            retryable=True,
        ) from exc


def _run_bounded(command: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError("scheduler transition did not complete", "schedule_transition_failed", retryable=True) from exc
    if completed.returncode != 0:
        raise ScheduleError("scheduler transition was refused", "schedule_transition_failed", retryable=True)


def activate(plan: Mapping[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Install and enable a schedule only after explicit confirmation."""
    if not confirm:
        return _protected(plan, "activating")
    try:
        canonical = _canonical_plan(plan)
        if not canonical["activation_supported"]:
            raise ScheduleError(
                "schedule activation cannot enforce the configured timeout on this platform",
                "schedule_timeout_unenforced",
            )
        paths = {
            key: _safe_unit_path(value, canonical["platform"], create_root=True)
            for key, value in canonical["paths"].items()
        }
        receipt_path = _safe_receipt_path(
            canonical["target"], canonical["platform"], create_root=True,
        )
        mode = _UNIT_MODE[canonical["platform"]]
        all_match = True
        for key, path in paths.items():
            content = _bounded_file_bytes(path, mode)
            all_match = (
                all_match and content is not None
                and content == canonical["units"][key].encode("utf-8")
            )
        receipt = _receipt_content(canonical)
        receipt_bytes = _bounded_file_bytes(receipt_path, 0o600)
        all_match = (
            all_match and receipt_bytes is not None
            and receipt_bytes == receipt.encode("utf-8")
        )
        paths_written = []
        if not all_match:
            prior = _snapshot_installation({
                **{path: mode for path in paths.values()},
                receipt_path: 0o600,
            })
            try:
                for key, path in paths.items():
                    _write_unit(path, canonical["units"][key], mode)
                    paths_written.append(str(path))
                _write_unit(receipt_path, receipt, 0o600)
                paths_written.append(str(receipt_path))
            except Exception as exc:
                _restore_installation(prior)
                if isinstance(exc, ScheduleError):
                    raise
                raise ScheduleError(
                    "schedule unit could not be written",
                    "schedule_write_failed",
                    retryable=True,
                ) from None
        # Matching files and a receipt prove only intended bytes, not active
        # scheduler state.  Re-run the idempotent transition every time.
        _run_bounded(canonical["activate_command"])
        return _result(
            canonical, ok=True,
            status="unchanged" if all_match else "activated",
            enabled=True, paths_written=paths_written,
        )
    except ScheduleError as exc:
        fallback = plan if isinstance(plan, Mapping) else {}
        return _result(fallback, ok=False, status="failed", error=exc)


def deactivate(plan: Mapping[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Disable from persisted installed identity, not current policy."""
    if not confirm:
        return _protected(plan, "deactivating")
    try:
        canonical = _canonical_plan(plan)
        installed, _receipt = _load_receipt(
            canonical["target"], canonical["platform"])
        if installed != canonical:
            raise ScheduleError(
                "installed schedule differs from the supplied plan",
                "schedule_evidence_unknown",
            )
        return _deactivate_canonical(installed)
    except ScheduleError as exc:
        fallback = plan if isinstance(plan, Mapping) else {}
        return _result(fallback, ok=False, status="failed", error=exc)


def _deactivate_canonical(canonical: Mapping[str, Any]) -> dict[str, Any]:
    paths = {
        key: _safe_unit_path(value, canonical["platform"])
        for key, value in canonical["paths"].items()
    }
    receipt_path = _safe_receipt_path(canonical["target"], canonical["platform"])
    present = []
    for key, path in paths.items():
        content = _bounded_file_bytes(path, _UNIT_MODE[canonical["platform"]])
        if content is not None:
            if content != canonical["units"].get(key, "").encode("utf-8"):
                raise ScheduleError(
                    "schedule unit content does not match installed evidence",
                    "schedule_content_mismatch",
                )
            present.append(path)
    _run_bounded(canonical["deactivate_command"])
    removed = []
    removals = [
        *((path, _UNIT_MODE[canonical["platform"]]) for path in present),
        (receipt_path, 0o600),
    ]
    for path, expected_mode in removals:
        try:
            _scheduler_root(canonical["platform"], create=False)
            if _existing_file_state(path, expected_mode) is None:
                continue
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ScheduleError(
                "schedule unit could not be removed",
                "schedule_remove_failed", retryable=True,
            ) from exc
    return _result(
        canonical, ok=True, status="deactivated", enabled=False,
        paths_removed=removed,
    )


def deactivate_installed(
    target: Mapping[str, str] | Any,
    platform: str | None = None,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Disable using canonical installed evidence even after policy removal."""
    if not confirm:
        return _protected({}, "deactivating")
    display: dict[str, Any] = {}
    try:
        try:
            target_value = _target(target)
        except ScheduleError:
            raise
        except Exception:
            raise ScheduleError("schedule target is invalid", "invalid_target") from None
        try:
            platform_value = normalize_platform(platform)
        except ScheduleError:
            raise
        except Exception:
            raise ScheduleError(
                "storage-monitor scheduling is unsupported on this platform",
                "unsupported_platform",
            ) from None
        display = {"target": target_value, "platform": platform_value}
        canonical, _receipt = _load_receipt(target_value, platform_value)
        return _deactivate_canonical(canonical)
    except ScheduleError as exc:
        return _result(display, ok=False, status="failed", error=exc)


__all__ = [
    "ScheduleError",
    "activate",
    "build_schedule_plan",
    "deactivate",
    "deactivate_installed",
    "normalize_platform",
]
