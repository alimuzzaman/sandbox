"""Offline cleanup verifier for the Credential Vault proof run.

The verifier consumes injected observations rather than touching a host. Its
one hard rule: it never removes anything. A resource that is foreign, drifted,
or simply unreadable produces `cleanup_incomplete` and a retained item, because
deleting something whose ownership we cannot prove is worse than leaving it.
"""

from __future__ import annotations

import re
from typing import Any


RESOURCE_KINDS = (
    "unit", "process", "socket", "cgroup", "interface", "route",
    "nftables_object", "path", "descriptor", "epoch_state",
)
OBSERVED_STATES = frozenset({"absent", "present", "foreign", "unavailable"})

_IDENTITY = re.compile(r"^[A-Za-z0-9@/][A-Za-z0-9._:@/-]{0,255}$")
_OBSERVATION_FIELDS = frozenset({"kind", "identity", "state", "owned"})


class CleanupError(ValueError):
    def __init__(self, code: str, location: str = "cleanup") -> None:
        super().__init__(code)
        self.code = code
        self.location = location[:256]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "location": self.location}


def _refuse(code: str, location: str = "cleanup") -> CleanupError:
    return CleanupError(code, location)


def expected_resources(manifest: dict[str, Any]) -> tuple[dict[str, str], ...]:
    """Everything the manifest says a finished run must have removed."""
    cleanup = manifest["cleanup"]
    service = manifest["service"]
    transport = manifest["transport"]
    items: list[dict[str, str]] = []
    items.extend({"kind": "unit", "identity": value} for value in cleanup["units"])
    items.extend({"kind": "socket", "identity": value} for value in cleanup["sockets"])
    items.extend({"kind": "interface", "identity": value}
                 for value in cleanup["interfaces"])
    items.extend({"kind": "cgroup", "identity": value} for value in cleanup["cgroups"])
    items.extend({"kind": "nftables_object", "identity": value}
                 for value in cleanup["nftables_objects"])
    items.extend({"kind": "path", "identity": value} for value in cleanup["paths"])
    items.append({"kind": "route", "identity": transport["guest_address"]})
    items.append({"kind": "process", "identity": service["executable"]})
    items.append({"kind": "descriptor", "identity": transport["lease_socket"]})
    items.append({"kind": "epoch_state", "identity": manifest["target"]["broker_epoch"]})
    return tuple(items)


def _validate_observation(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != _OBSERVATION_FIELDS:
        raise _refuse("observation_schema_invalid", location)
    if value["kind"] not in RESOURCE_KINDS:
        raise _refuse("observation_kind_invalid", location)
    if not isinstance(value["identity"], str) \
            or not _IDENTITY.fullmatch(value["identity"]):
        raise _refuse("observation_identity_invalid", location)
    if value["state"] not in OBSERVED_STATES:
        raise _refuse("observation_state_invalid", location)
    if not isinstance(value["owned"], bool):
        raise _refuse("observation_ownership_invalid", location)
    if value["state"] == "foreign" and value["owned"]:
        raise _refuse("observation_contradiction", location)
    return value


def verify(manifest: dict[str, Any], observations: Any) -> dict[str, Any]:
    """Return `complete` only when every expected resource was proven absent."""
    expected = expected_resources(manifest)
    if not isinstance(observations, (list, tuple)) or len(observations) > 512:
        raise _refuse("observations_invalid")
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    for index, item in enumerate(observations):
        value = _validate_observation(item, f"observations[{index}]")
        key = (value["kind"], value["identity"])
        if key in observed and observed[key] != value:
            raise _refuse("observation_contradiction", f"observations[{index}]")
        observed[key] = value
    retained: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    for item in expected:
        key = (item["kind"], item["identity"])
        value = observed.get(key)
        if value is None:
            retained.append({**item, "reason_code": "observation_missing"})
            continue
        if value["state"] == "absent":
            removed.append(item)
            continue
        if value["state"] == "unavailable":
            # A read that could not be made is never absence.
            retained.append({**item, "reason_code": "observation_unavailable"})
            continue
        if value["state"] == "foreign" or not value["owned"]:
            # Never remove what we cannot prove is ours. This is a residual for
            # a human, not a deletion for the harness.
            retained.append({**item, "reason_code": "foreign_resource"})
            continue
        retained.append({**item, "reason_code": "resource_present"})
    unexpected = tuple(sorted(
        f"{kind}:{identity}" for (kind, identity), value in observed.items()
        if (kind, identity) not in {(item["kind"], item["identity"]) for item in expected}
        and value["state"] != "absent"
    ))
    state = "complete" if not retained and not unexpected else "incomplete"
    return {
        "ok": state == "complete",
        "code": "cleanup_complete" if state == "complete" else "cleanup_incomplete",
        "state": state,
        "removed": tuple(f"{item['kind']}:{item['identity']}" for item in removed),
        "retained": tuple(retained),
        "unexpected": unexpected,
    }


__all__ = [
    "CleanupError", "OBSERVED_STATES", "RESOURCE_KINDS", "expected_resources",
    "verify",
]
