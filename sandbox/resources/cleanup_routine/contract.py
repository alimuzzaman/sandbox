"""Fixed request/response contract for the cleanup routine control actions.

The three actions travel over the authenticated ``/resources`` control route.
Everything here is pure: the host handler and the CLI client both validate
with these functions, and the host re-validates whatever the client sent.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sandbox.config.storage_monitor import (
    StorageMonitorConfigError,
    normalize_storage_monitor,
)

ACTIONS = frozenset({
    "cleanup_routine_enable",
    "cleanup_routine_disable",
    "cleanup_routine_status",
})
ERROR_CODES = frozenset({
    "invalid_request",
    "invalid_cadence",
    "invalid_exclusion",
    "invalid_timeout",
    "runtime_revision_mismatch",
    "systemd_unavailable",
    "linger_disabled",
    "routine_install_failed",
    "routine_remove_failed",
})
OUTCOMES = frozenset({
    "running", "reclaimed", "nothing_to_do", "refused", "timed_out",
    "skipped_busy",
})

DEFAULT_CADENCE = "daily"
DEFAULT_TIMEOUT = "30min"
DEFAULT_RANDOMIZED_DELAY = "5min"
MAX_EXCLUSIONS = 32
MAX_EXCLUSION_CHARS = 128
MAX_CADENCE_CHARS = 128
MAX_HISTORY = 30
# A run must have room to plan and remove something, and must not outlive a
# daily cadence by much; the jitter must stay below one day.
MIN_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 6 * 3600
MAX_DELAY_SECONDS = 24 * 3600

_REVISION = re.compile(r"^[0-9a-f]{24}$")
_FIELDS = {
    "cleanup_routine_enable": {
        "action", "expected_runtime_revision", "cadence", "timeout",
        "randomized_delay", "exclusions",
    },
    "cleanup_routine_disable": {"action"},
    "cleanup_routine_status": {"action", "history"},
}
# systemd time-span units, in seconds.  ``m`` is minutes for systemd.
_UNITS = {
    "us": 1e-6, "ms": 1e-3, "s": 1, "min": 60, "m": 60, "h": 3600,
    "d": 86400, "w": 604800,
}
_SPAN_PART = re.compile(r"([0-9]+)(us|ms|s|min|h|d|w|m)")
_ROUTINE_FIELDS = (
    "enabled", "cadence", "timeout", "randomized_delay", "exclusions",
    "enabled_revision", "enabled_at", "disabled_at",
)
_RUN_FIELDS = (
    "schema", "run_id", "started_at", "ended_at", "outcome", "reason",
    "bytes_reclaimed", "removed", "skipped", "skipped_reasons",
    "runtime_revision", "manifest",
)


class RoutineError(ValueError):
    """A routine request or host transition was refused with a typed code."""

    def __init__(self, message: str, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code if code in ERROR_CODES else "invalid_request"


def _control_free(value: str) -> bool:
    return not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _brackets_balanced(value: str) -> bool:
    """Every ``[`` opens a set that closes; a stray ``]`` is refused.

    ``fnmatch`` never raises, so an unclosed set would silently match only a
    literal ``[`` and the exclusion would protect nothing.
    """
    index = 0
    while index < len(value):
        char = value[index]
        if char == "]":
            return False
        if char == "[":
            start = index + 1
            if start < len(value) and value[start] == "!":
                start += 1
            # A ``]`` directly after the opener is a literal member.
            close = value.find("]", start + 1)
            if close < 0:
                return False
            index = close + 1
            continue
        index += 1
    return True


def validate_exclusion(value: Any) -> str:
    if (not isinstance(value, str) or not value
            or len(value) > MAX_EXCLUSION_CHARS or "/" in value
            or not _control_free(value) or not _brackets_balanced(value)):
        raise RoutineError(
            "exclusions must be workspace name globs of at most "
            f"{MAX_EXCLUSION_CHARS} characters with no '/' or control characters "
            "and balanced brackets",
            "invalid_exclusion",
        )
    return value


def validate_exclusions(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise RoutineError("exclusions must be a list", "invalid_exclusion")
    if len(values) > MAX_EXCLUSIONS:
        raise RoutineError(
            f"at most {MAX_EXCLUSIONS} exclusions are allowed", "invalid_exclusion",
        )
    return list(dict.fromkeys(validate_exclusion(item) for item in values))


def validate_cadence_text(value: Any) -> str:
    """Syntactic check only; the host validates meaning with systemd-analyze."""
    if (not isinstance(value, str) or not value.strip()
            or len(value) > MAX_CADENCE_CHARS or not _control_free(value)):
        raise RoutineError("cadence must be a systemd calendar expression",
                           "invalid_cadence")
    return value.strip()


def span_seconds(value: str) -> float:
    """Convert one already-validated systemd time span to seconds."""
    total = 0.0
    for amount, unit in _SPAN_PART.findall(value):
        total += int(amount) * _UNITS[unit]
    return int(total) if float(total).is_integer() else total


def validate_spans(timeout: Any, randomized_delay: Any) -> tuple[str, str]:
    """Apply the storage-monitor schedule-field rules plus routine bounds."""
    try:
        normalized = normalize_storage_monitor({
            "schedule_timeout": timeout,
            "schedule_randomized_delay": randomized_delay,
        })
    except StorageMonitorConfigError as exc:
        raise RoutineError(str(exc), "invalid_timeout") from None
    timeout_value = normalized["schedule_timeout"]
    delay_value = normalized["schedule_randomized_delay"]
    if not MIN_TIMEOUT_SECONDS <= span_seconds(timeout_value) <= MAX_TIMEOUT_SECONDS:
        raise RoutineError(
            "routine timeout must be between 1min and 6h", "invalid_timeout",
        )
    if span_seconds(delay_value) > MAX_DELAY_SECONDS:
        raise RoutineError(
            "routine randomized delay must be at most 1d", "invalid_timeout",
        )
    return timeout_value, delay_value


def validate_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("action") not in ACTIONS:
        raise RoutineError("unsupported cleanup routine action")
    action = payload["action"]
    unknown = set(payload) - _FIELDS[action]
    if unknown:
        raise RoutineError(f"unknown cleanup routine field: {sorted(unknown)[0]!r}")
    if action == "cleanup_routine_disable":
        return {"action": action}
    if action == "cleanup_routine_status":
        history = payload.get("history", MAX_HISTORY)
        if (isinstance(history, bool) or not isinstance(history, int)
                or not 1 <= history <= MAX_HISTORY):
            raise RoutineError(f"history must be 1 through {MAX_HISTORY}")
        return {"action": action, "history": history}
    revision = payload.get("expected_runtime_revision")
    if not isinstance(revision, str) or _REVISION.fullmatch(revision) is None:
        raise RoutineError("expected_runtime_revision is required")
    cadence = validate_cadence_text(payload.get("cadence", DEFAULT_CADENCE))
    timeout, delay = validate_spans(
        payload.get("timeout", DEFAULT_TIMEOUT),
        payload.get("randomized_delay", DEFAULT_RANDOMIZED_DELAY),
    )
    return {
        "action": action,
        "expected_runtime_revision": revision,
        "cadence": cadence,
        "timeout": timeout,
        "randomized_delay": delay,
        "exclusions": validate_exclusions(payload.get("exclusions", [])),
    }


def public_run(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project one stored run record onto the contract fields only."""
    return {key: record.get(key) for key in _RUN_FIELDS}


def routine_result(config: Mapping[str, Any] | None, *,
                   effective_exclusions, runs, next_run) -> dict[str, Any]:
    """Build the ``ok`` result shared by enable, disable and status."""
    stored = dict(config or {})
    routine = {key: stored.get(key) for key in _ROUTINE_FIELDS}
    routine["enabled"] = stored.get("enabled") is True
    routine["exclusions"] = list(stored.get("exclusions") or [])
    routine["effective_exclusions"] = list(effective_exclusions or [])
    routine["next_run"] = next_run if routine["enabled"] else None
    public_runs = [public_run(item) for item in runs or ()]
    last = next(
        (item.get("runtime_revision") for item in public_runs
         if item.get("runtime_revision")), None,
    )
    return {
        "ok": True,
        "routine": routine,
        "last_run_revision": last,
        "runs": public_runs,
    }


def error_result(code: str, message: str) -> dict[str, Any]:
    error = RoutineError(message, code)
    text = str(message).replace("\r", " ").replace("\n", " ").strip()[:240]
    return {"ok": False, "error": {"code": error.code, "message": text}}


def envelope(result: Mapping[str, Any], *, runtime_revision: str) -> dict[str, Any]:
    return {
        "resource_schema": 1,
        "transport": "control",
        "service": {"runtime_revision": str(runtime_revision)},
        "result": dict(result),
    }


__all__ = [
    "ACTIONS", "DEFAULT_CADENCE", "DEFAULT_RANDOMIZED_DELAY", "DEFAULT_TIMEOUT",
    "ERROR_CODES", "MAX_HISTORY", "OUTCOMES", "RoutineError", "envelope",
    "error_result", "public_run", "routine_result", "span_seconds",
    "validate_cadence_text", "validate_exclusion", "validate_exclusions",
    "validate_request", "validate_spans",
]
