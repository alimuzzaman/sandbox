"""Pure storage-monitor configuration normalization.

The storage monitor is machine-scoped configuration.  This module deliberately
does not load YAML, inspect the host, or resolve a remote: callers hand it one
raw ``resources.monitor`` mapping and receive a detached, validated policy.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from types import MappingProxyType
from numbers import Real
from typing import Any

from sandbox.resources import reclaim


class StorageMonitorConfigError(ValueError):
    """A storage-monitor setting was rejected before any host contact."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


# Keep this mapping immutable so a caller cannot accidentally change the
# built-in safety posture for subsequent normalizations.  The values are all
# scalars, so a shallow ``dict(DEFAULTS)`` is sufficient for detached results.
DEFAULTS = MappingProxyType({
    "warn_ratio": 0.15,
    "critical_ratio": 0.05,
    "auto_enabled": False,
    "auto_tier": "safe",
    "auto_ratio": None,
    "reap_enabled": False,
    "reap_ttl": None,
    "schedule_calendar": "hourly",
    "schedule_randomized_delay": "5min",
    "schedule_timeout": "30min",
    "record_max_age_seconds": 21600,
})

_KEYS = frozenset(DEFAULTS)

# This is the same deliberately bounded systemd time-span grammar used by the
# recovery scheduler.  Keep it local: importing the scheduler would couple a
# pure config provider to schedule implementation details.
_SYSTEMD_TIME_SPAN = re.compile(
    r"^[0-9]+(?:us|ms|s|min|h|d|w|m)(?:[ \t]+[0-9]+(?:us|ms|s|min|h|d|w|m))*$"
)


def _error(message: str, code: str) -> StorageMonitorConfigError:
    return StorageMonitorConfigError(message, code)


def _mapping(raw: Any) -> Mapping[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _error("storage monitor configuration must be an object", "invalid_schedule_field")
    unknown = sorted(set(raw) - _KEYS, key=repr)
    if unknown:
        raise _error(
            f"storage monitor has unknown key: {unknown[0]!r}",
            "unknown_key",
        )
    return raw


def _ratio(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _error(f"storage monitor {name} must be a number between 0 and 1", "invalid_threshold")
    number = float(value)
    if not math.isfinite(number) or not 0 < number < 1:
        raise _error(f"storage monitor {name} must be between 0 and 1", "invalid_threshold")
    return number


def _flag(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise _error(f"storage monitor {name} must be a boolean", "invalid_flag")
    return value


def _schedule_text(name: str, value: Any) -> str:
    if (not isinstance(value, str) or not value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _error(f"storage monitor {name} is invalid", "invalid_schedule_field")
    return value


def _duration(value: Any) -> str | None:
    if value is None:
        return None
    try:
        reclaim.parse_duration(value)
    except reclaim.ReclaimPolicyError as exc:
        raise _error(str(exc), "invalid_duration") from None
    # ``parse_duration`` accepts surrounding whitespace and upper-case units;
    # store one stable spelling while retaining the documented string shape.
    return value.strip().lower()


def _record_age(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        # The config contract intentionally has no separate record-age code;
        # this is a schedule/record field validation refusal.
        raise _error(
            "storage monitor record_max_age_seconds must be a positive integer",
            "invalid_schedule_field",
        )
    return value


def normalize_storage_monitor(raw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a detached, strictly validated storage-monitor policy.

    ``None`` and an omitted block resolve to :data:`DEFAULTS`.  The function
    performs no file, network, process, or host operations; duration parsing is
    delegated only to the existing pure reclaim policy helper.
    """

    source = _mapping(raw)
    result = dict(DEFAULTS)

    warn_ratio = _ratio("warn_ratio", source.get("warn_ratio", DEFAULTS["warn_ratio"]))
    critical_ratio = _ratio(
        "critical_ratio", source.get("critical_ratio", DEFAULTS["critical_ratio"])
    )
    if critical_ratio > warn_ratio:
        raise _error(
            "storage monitor critical_ratio must not exceed warn_ratio",
            "invalid_threshold_order",
        )

    auto_ratio_raw = source.get("auto_ratio", DEFAULTS["auto_ratio"])
    auto_ratio = critical_ratio if auto_ratio_raw is None else _ratio("auto_ratio", auto_ratio_raw)
    if auto_ratio > warn_ratio:
        raise _error(
            "storage monitor auto_ratio must not exceed warn_ratio",
            "invalid_threshold_order",
        )

    auto_tier = source.get("auto_tier", DEFAULTS["auto_tier"])
    if auto_tier != "safe":
        raise _error(
            "storage monitor auto_tier must be safe",
            "invalid_auto_tier",
        )

    result.update({
        "warn_ratio": warn_ratio,
        "critical_ratio": critical_ratio,
        "auto_enabled": _flag(
            "auto_enabled", source.get("auto_enabled", DEFAULTS["auto_enabled"])
        ),
        "auto_tier": auto_tier,
        "auto_ratio": auto_ratio,
        "reap_enabled": _flag(
            "reap_enabled", source.get("reap_enabled", DEFAULTS["reap_enabled"])
        ),
        "reap_ttl": _duration(source.get("reap_ttl", DEFAULTS["reap_ttl"])),
        "schedule_calendar": _schedule_text(
            "schedule_calendar", source.get("schedule_calendar", DEFAULTS["schedule_calendar"])
        ),
        "schedule_randomized_delay": _schedule_text(
            "schedule_randomized_delay",
            source.get("schedule_randomized_delay", DEFAULTS["schedule_randomized_delay"]),
        ),
        "schedule_timeout": _schedule_text(
            "schedule_timeout", source.get("schedule_timeout", DEFAULTS["schedule_timeout"])
        ),
        "record_max_age_seconds": _record_age(
            source.get("record_max_age_seconds", DEFAULTS["record_max_age_seconds"])
        ),
    })

    for field in ("schedule_randomized_delay", "schedule_timeout"):
        if _SYSTEMD_TIME_SPAN.fullmatch(result[field]) is None:
            raise _error(
                f"storage monitor {field} is not a valid systemd time span",
                "invalid_schedule_field",
            )
    return result


__all__ = ["DEFAULTS", "StorageMonitorConfigError", "normalize_storage_monitor"]
