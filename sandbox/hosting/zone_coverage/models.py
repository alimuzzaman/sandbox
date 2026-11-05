"""Models and validation for zone coverage (spec 070)."""
from __future__ import annotations

from enum import Enum
import re
from typing import Any

from sandbox.hosting.zone_coverage import (
    COVERAGE_SCHEMA_UNSUPPORTED,
    CoverageError,
)

CURRENT_SCHEMA_VERSION = 1
MAX_DEPENDENTS = 512
MAX_HISTORY = 64

_ZONE_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class CoverageState(str, Enum):
    ABSENT = "absent"
    RETIRED = "retired"
    ENABLING = "enabling"
    FALLBACK_ONLY = "fallback_only"
    ACTIVE = "active"
    DISABLING = "disabling"
    DISABLED = "disabled"
    INCOMPLETE = "incomplete"


class DependentState(str, Enum):
    PENDING = "pending"
    BOUND = "bound"
    RECOVERED = "recovered"


ALL_STATES = frozenset(s.value for s in CoverageState)
ALL_DEPENDENT_STATES = frozenset(s.value for s in DependentState)


def normalize_zone(zone: str) -> str:
    """Normalize and validate a zone name (IDNA, lower, stripped)."""
    if not isinstance(zone, str):
        raise ValueError("zone must be a string")
    clean = zone.strip().rstrip(".").lower()
    if not clean:
        raise ValueError("zone name cannot be empty")
    try:
        encoded = clean.encode("idna").decode("ascii")
    except Exception as exc:
        raise ValueError(f"invalid zone name {zone!r}: {exc}") from exc
    labels = encoded.split(".")
    if len(labels) < 2 or not all(_ZONE_LABEL_RE.match(lbl) for lbl in labels):
        raise ValueError(f"invalid zone name format {zone!r}")
    return encoded


def normalize_hostname(hostname: str) -> str:
    """Normalize a hostname (lower, stripped, no trailing dot)."""
    if not isinstance(hostname, str):
        raise ValueError("hostname must be a string")
    clean = hostname.strip().rstrip(".").lower()
    if not clean:
        raise ValueError("hostname cannot be empty")
    try:
        return clean.encode("idna").decode("ascii")
    except Exception as exc:
        raise ValueError(f"invalid hostname {hostname!r}: {exc}") from exc


def can_transition(
    from_state: str,
    to_state: str,
    *,
    kind: str | None = None,
    interrupted_kind: str | None = None,
) -> bool:
    """Check if state transition is allowed per spec 070 data-model.md."""
    if from_state not in ALL_STATES or to_state not in ALL_STATES:
        return False

    if from_state in (CoverageState.ABSENT.value, CoverageState.RETIRED.value):
        return to_state == CoverageState.ENABLING.value and kind == "enable"

    if from_state == CoverageState.ENABLING.value:
        return to_state in (
            CoverageState.FALLBACK_ONLY.value,
            CoverageState.ACTIVE.value,
            CoverageState.INCOMPLETE.value,
            CoverageState.ENABLING.value,  # resume
        )

    if from_state == CoverageState.FALLBACK_ONLY.value:
        if to_state == CoverageState.ENABLING.value and kind == "enable":
            return True
        if to_state == CoverageState.RETIRED.value and kind == "remove_fallback":
            return True
        return False

    if from_state == CoverageState.ACTIVE.value:
        if to_state == CoverageState.DISABLING.value and kind == "disable":
            return True
        if to_state in (CoverageState.ACTIVE.value, CoverageState.INCOMPLETE.value) and kind == "repair":
            return True
        return False

    if from_state == CoverageState.DISABLING.value:
        return to_state in (
            CoverageState.DISABLED.value,
            CoverageState.INCOMPLETE.value,
            CoverageState.DISABLING.value,  # resume
        )

    if from_state == CoverageState.DISABLED.value:
        if to_state == CoverageState.ENABLING.value and kind == "enable":
            return True
        if to_state == CoverageState.RETIRED.value and kind == "remove_fallback":
            return True
        return False

    if from_state == CoverageState.INCOMPLETE.value:
        # Incomplete retries only its interrupted_kind (or repair for enable per data-model.md)
        if interrupted_kind == "enable":
            if to_state == CoverageState.ENABLING.value and kind == "enable":
                return True
            if to_state == CoverageState.FALLBACK_ONLY.value:  # rollback ok
                return True
            if to_state in (CoverageState.ACTIVE.value, CoverageState.INCOMPLETE.value) and kind == "repair":
                return True
        elif interrupted_kind == "disable":
            if to_state == CoverageState.DISABLING.value and kind == "disable":
                return True
        return False

    return False


def validate_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Validate a remote ZoneCoverage document."""
    if not isinstance(doc, dict):
        raise ValueError("document must be a dict")

    schema_version = doc.get("schema_version")
    if not isinstance(schema_version, int):
        raise ValueError("schema_version must be an integer")
    if schema_version > CURRENT_SCHEMA_VERSION:
        raise CoverageError(
            COVERAGE_SCHEMA_UNSUPPORTED,
            f"document schema_version {schema_version} is unsupported (maximum supported is {CURRENT_SCHEMA_VERSION})",
        )

    zone = normalize_zone(doc.get("zone", ""))

    state = doc.get("state")
    if state not in ALL_STATES:
        raise ValueError(f"invalid state {state!r}")

    gen = doc.get("generation")
    if not isinstance(gen, int) or gen < 0:
        raise ValueError("generation must be a non-negative integer")

    dep_rev = doc.get("dependents_revision", 0)
    if not isinstance(dep_rev, int) or dep_rev < 0:
        raise ValueError("dependents_revision must be a non-negative integer")

    dependents = doc.get("dependents") or {}
    if not isinstance(dependents, dict):
        raise ValueError("dependents must be a dict")
    if len(dependents) > MAX_DEPENDENTS:
        raise ValueError(f"dependents count {len(dependents)} exceeds maximum {MAX_DEPENDENTS}")

    for k, v in dependents.items():
        if not isinstance(v, dict):
            raise ValueError(f"dependent {k!r} must be a dict")
        dep_state = v.get("state")
        if dep_state not in ALL_DEPENDENT_STATES:
            raise ValueError(f"dependent {k!r} has invalid state {dep_state!r}")

    history = doc.get("history") or []
    if not isinstance(history, list):
        raise ValueError("history must be a list")
    if len(history) > MAX_HISTORY:
        raise ValueError(f"history count exceeds {MAX_HISTORY}")

    return doc


def eligible(hostname: str, zone: str, zone_records: list[dict[str, Any]] | None = None) -> bool:
    """Return True if hostname is eligible for shared wildcard coverage under zone.

    Per data-model.md:
    eligible(hostname, zone, zone_records) is true only if hostname is exactly
    one label above zone, the label is not '*' and does not begin with '_',
    and no record in zone_records is named hostname or ends with '.hostname'.
    """
    try:
        host_clean = normalize_hostname(hostname)
        zone_clean = normalize_zone(zone)
    except ValueError:
        return False

    suffix = "." + zone_clean
    if not host_clean.endswith(suffix):
        return False

    prefix = host_clean[:-len(suffix)]
    if not prefix or "." in prefix:
        # Not exactly one label above zone (apex or multi-level)
        return False

    if prefix == "*" or prefix.startswith("_"):
        return False

    if zone_records:
        sub_suffix = "." + host_clean
        for rec in zone_records:
            if not isinstance(rec, dict):
                continue
            rname = rec.get("name")
            if not isinstance(rname, str):
                continue
            try:
                rname_clean = normalize_hostname(rname)
            except ValueError:
                rname_clean = rname.strip().rstrip(".").lower()

            if rname_clean == host_clean or rname_clean.endswith(sub_suffix):
                return False

    return True
