"""Normalize the per-project instance power policy.

The policy is deliberately separate from the selected runtime backend.  It
describes whether an already-provisioned instance may be suspended while idle;
it never authorizes provisioning, destruction, or arbitrary Docker commands.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy


DEFAULTS = {
    "mode": "always_on",
    "idleAfterSeconds": 900,
    "wakeTimeoutSeconds": 60,
    "stopGraceSeconds": 30,
    "maxPendingRequests": 32,
}

_KEYS = frozenset(DEFAULTS)
_MODES = frozenset({"always_on", "idle_stop"})


class InstanceLifecycleConfigError(ValueError):
    """A lifecycle declaration is invalid before any runtime contact."""

    def __init__(self, message: str, code: str = "invalid_instance_lifecycle") -> None:
        self.code = code
        super().__init__(message)


def _mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InstanceLifecycleConfigError("instanceLifecycle must be an object")
    unknown = sorted(set(value) - _KEYS, key=repr)
    if unknown:
        raise InstanceLifecycleConfigError(
            f"instanceLifecycle has unknown key: {unknown[0]!r}", "unknown_key"
        )
    return value


def _bounded_integer(name: str, value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InstanceLifecycleConfigError(
            f"instanceLifecycle {name} must be an integer between {minimum} and {maximum}",
            "invalid_duration",
        )
    return value


def normalize_instance_lifecycle(raw: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return a detached, strict lifecycle policy.

    Omission is intentionally represented as ``always_on`` so every runtime
    can make the safe default explicit without adding a new background actor.
    """
    source = _mapping(raw)
    mode = source.get("mode", DEFAULTS["mode"])
    if mode not in _MODES:
        raise InstanceLifecycleConfigError(
            "instanceLifecycle mode must be always_on or idle_stop", "invalid_mode"
        )
    result = copy.deepcopy(DEFAULTS)
    result.update({
        "mode": mode,
        "idleAfterSeconds": _bounded_integer(
            "idleAfterSeconds", source.get("idleAfterSeconds", DEFAULTS["idleAfterSeconds"]),
            60, 604800,
        ),
        "wakeTimeoutSeconds": _bounded_integer(
            "wakeTimeoutSeconds", source.get("wakeTimeoutSeconds", DEFAULTS["wakeTimeoutSeconds"]),
            5, 600,
        ),
        "stopGraceSeconds": _bounded_integer(
            "stopGraceSeconds", source.get("stopGraceSeconds", DEFAULTS["stopGraceSeconds"]),
            1, 120,
        ),
        "maxPendingRequests": _bounded_integer(
            "maxPendingRequests", source.get("maxPendingRequests", DEFAULTS["maxPendingRequests"]),
            1, 256,
        ),
    })
    return result


__all__ = ["DEFAULTS", "InstanceLifecycleConfigError", "normalize_instance_lifecycle"]
