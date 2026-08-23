"""Validated, read-only deep storage attribution evidence and parsers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import time
from collections import Counter
from typing import Any, Iterable

from sandbox.services.process import ProcessResult

from .models import redact


DEEP_STATES = frozenset({"complete", "partial"})
COVERAGE_STATES = frozenset({
    "complete", "partial", "not_selected", "timed_out", "cancelled",
    "disconnected", "unavailable",
})
CONFIDENCE_STATES = frozenset({"high", "medium", "low"})
PRIVILEGE_STATES = frozenset({"elevated", "unprivileged", "unavailable"})
SELECTION_REASONS = frozenset({
    "root", "sandbox_home", "container_data", "managed_root", "unrelated",
    "virtual", "unavailable",
})
HARDLINK_STATES = frozenset({"confirmed", "partial", "unavailable"})
FINDING_KINDS = frozenset({
    "directory", "deleted_open", "container_image", "container", "volume",
    "build_cache", "filesystem_overhead",
})
OVERLAP_STATES = frozenset({
    "none", "directory_root", "shared_layers", "logical_cache", "unknown",
})
ACTIVITY_STATES = frozenset({"active", "inactive", "mixed", "unknown"})
GUIDANCE_STATES = frozenset({
    "existing_cache_scope", "existing_stale_scope", "manual",
    "monitoring_only", "non_cleanable",
})
NETWORK_PRESSURE_THRESHOLD = 28
_BYTE_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b)?$", re.I)


def _non_negative(value: int | None, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _strings(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(values)
    if not all(isinstance(item, str) for item in result):
        raise ValueError(f"{name} must contain strings")
    return result


def _identifier(kind: str, *parts: str) -> str:
    seed = "\0".join(parts)
    return f"{kind}-{hashlib.sha256(seed.encode()).hexdigest()[:20]}"


def network_capacity_pressure(
    resources: Iterable[Any],
    *,
    inventory_status: str = "complete",
) -> dict | None:
    """Summarize observed Sandbox-managed user-defined network pressure.

    This is deliberately a diagnostic signal.  It never turns an inactive
    network into a cleanup candidate and it does not infer liveness from job
    termination alone.  A complete Docker network inventory gives high
    confidence; partial inventories retain the bounded observation while
    clearly marking confidence as low.
    """
    managed = []
    summary = Counter({
        "active": 0,
        "retained": 0,
        "unverified": 0,
        "disposable_cache": 0,
        "stale_candidate": 0,
        "foreign": 0,
        "unattributed": 0,
    })
    for item in resources or ():
        if isinstance(item, dict):
            resource_type = item.get("kind")
            owner = item.get("owner") or {}
            ownership = owner.get("kind")
            classification = str(item.get("classification", "unverified"))
        else:
            resource_type = getattr(item, "kind", None)
            ownership = getattr(item, "owner_kind", None)
            classification = str(getattr(item, "classification", "unverified"))
        if resource_type != "network":
            continue
        if ownership == "project":
            managed.append(item)
            if classification in summary:
                summary[classification] += 1
            else:
                summary["unverified"] += 1
        elif ownership == "foreign":
            summary["foreign"] += 1
        else:
            summary["unattributed"] += 1

    if inventory_status not in {"complete", "observed", "partial"}:
        # An unavailable inventory cannot establish either pressure or safe
        # ownership.  Do not fabricate a low-pressure result from no rows.
        return None if not managed else {
            "level": "high" if len(managed) >= NETWORK_PRESSURE_THRESHOLD else
            "medium" if len(managed) >= NETWORK_PRESSURE_THRESHOLD - 4 else "low",
            "managed_user_defined_network_count": len(managed),
            "threshold": NETWORK_PRESSURE_THRESHOLD,
            "classification_summary": dict(summary),
            "confidence": "low",
            "status": "partial",
            "recovery": {
                "code": "network_pool_exhausted"
                if len(managed) >= NETWORK_PRESSURE_THRESHOLD
                else "network_capacity_pressure"
                if len(managed) >= NETWORK_PRESSURE_THRESHOLD - 4 else None,
                "guidance": (
                    "Network inventory is incomplete; do not delete networks. "
                    "Review the specific Sandbox workspace and job/container "
                    "references, then rescan before any confirmed lifecycle action."
                ),
                "automatic_cleanup": False,
            },
        }

    count = len(managed)
    level = (
        "high" if count >= NETWORK_PRESSURE_THRESHOLD else
        "medium" if count >= NETWORK_PRESSURE_THRESHOLD - 4 else "low"
    )
    code = (
        "network_pool_exhausted" if level == "high" else
        "network_capacity_pressure" if level == "medium" else None
    )
    if level == "high":
        guidance = (
            "Docker user-defined network capacity is under severe pressure. "
            "Review Sandbox-managed workspace ownership and active job, lease, "
            "and container references; if a specific workspace is no longer "
            "needed and has no active jobs or leases, use the existing "
            "confirmation-gated `sb workspace destroy --remote NAME "
            "--workspace LABEL --yes` lifecycle, then rescan. Do not delete "
            "active, foreign, "
            "or unattributed Docker networks directly."
        )
    elif level == "medium":
        guidance = (
            "Docker user-defined network capacity is approaching the pressure "
            "threshold. Review Sandbox workspace/job/container references and "
            "rescan before any explicitly confirmed workspace lifecycle action; "
            "do not delete networks directly."
        )
    else:
        guidance = (
            "No immediate Sandbox network-capacity recovery action is indicated; "
            "continue bounded monitoring."
        )
    return {
        "level": level,
        "managed_user_defined_network_count": count,
        "threshold": NETWORK_PRESSURE_THRESHOLD,
        "classification_summary": dict(summary),
        "confidence": "high" if inventory_status in {"complete", "observed"} else "low",
        "status": "complete" if inventory_status in {"complete", "observed"} else "partial",
        "recovery": {
            "code": code,
            "guidance": guidance,
            "automatic_cleanup": False,
        },
    }


def parse_byte_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    match = _BYTE_SIZE.fullmatch(value.strip())
    if not match:
        return None
    unit = (match.group(2) or "b").lower()
    power = {
        "b": 0,
        "kb": 1, "kib": 1,
        "mb": 2, "mib": 2,
        "gb": 3, "gib": 3,
        "tb": 4, "tib": 4,
        "pb": 5, "pib": 5,
        "eb": 6, "eib": 6,
    }.get(unit)
    return None if power is None else int(float(match.group(1)) * (1024 ** power))


@dataclass(frozen=True)
class FilesystemObservation:
    filesystem_id: str
    display_name: str
    filesystem_type: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    writable: bool
    selected: bool
    selection_reason: str
    status: str
    observed_allocated_bytes: int | None
    hardlink_deduplication: str
    limitations: tuple[str, ...] = ()
    mount_id: str | None = None
    parent_mount_id: str | None = None
    capacity_scope_id: str | None = None
    mount_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("filesystem_id", "display_name", "filesystem_type"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} is required")
        for name in ("total_bytes", "used_bytes", "available_bytes"):
            _non_negative(getattr(self, name), name)
        if self.used_bytes > self.total_bytes:
            raise ValueError("filesystem used bytes cannot exceed total")
        if not isinstance(self.writable, bool) or not isinstance(self.selected, bool):
            raise ValueError("filesystem flags must be boolean")
        if self.selection_reason not in SELECTION_REASONS:
            raise ValueError("invalid filesystem selection reason")
        if self.status not in COVERAGE_STATES:
            raise ValueError("invalid filesystem status")
        _non_negative(
            self.observed_allocated_bytes,
            "observed_allocated_bytes",
            optional=True,
        )
        if self.status == "complete" and self.observed_allocated_bytes is None:
            raise ValueError("complete filesystem requires observed allocation")
        if self.hardlink_deduplication not in HARDLINK_STATES:
            raise ValueError("invalid hardlink deduplication state")
        object.__setattr__(self, "limitations", _strings(
            self.limitations, "limitations",
        ))
        for name in ("mount_id", "parent_mount_id", "capacity_scope_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string or null")
        object.__setattr__(self, "mount_flags", _strings(
            self.mount_flags, "mount_flags",
        ))

    def to_dict(self) -> dict:
        return redact({
            "filesystem_id": self.filesystem_id,
            "display_name": self.display_name,
            "filesystem_type": self.filesystem_type,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "available_bytes": self.available_bytes,
            "writable": self.writable,
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "status": self.status,
            "observed_allocated_bytes": self.observed_allocated_bytes,
            "hardlink_deduplication": self.hardlink_deduplication,
            "limitations": list(self.limitations),
            "mount_id": self.mount_id,
            "parent_mount_id": self.parent_mount_id,
            "capacity_scope_id": self.capacity_scope_id,
            "mount_flags": list(self.mount_flags),
        })

    @classmethod
    def from_dict(cls, value: dict) -> "FilesystemObservation":
        return cls(
            filesystem_id=value.get("filesystem_id"),
            display_name=value.get("display_name"),
            filesystem_type=value.get("filesystem_type"),
            total_bytes=value.get("total_bytes"),
            used_bytes=value.get("used_bytes"),
            available_bytes=value.get("available_bytes"),
            writable=value.get("writable"),
            selected=value.get("selected"),
            selection_reason=value.get("selection_reason"),
            status=value.get("status"),
            observed_allocated_bytes=value.get("observed_allocated_bytes"),
            hardlink_deduplication=value.get(
                "hardlink_deduplication", "unavailable",
            ),
            limitations=tuple(value.get("limitations") or ()),
            mount_id=value.get("mount_id"),
            parent_mount_id=value.get("parent_mount_id"),
            capacity_scope_id=value.get("capacity_scope_id"),
            mount_flags=tuple(value.get("mount_flags") or ()),
        )


@dataclass(frozen=True)
class AttributionFinding:
    finding_id: str
    kind: str
    display_name: str
    filesystem_id: str | None
    owner_kind: str | None
    owner_id: str | None
    observed_bytes: int
    capacity_accounted: bool
    overlap: str
    activity: str
    guidance: str
    evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    unique_bytes: int | None = None
    shared_bytes: int | None = None
    potentially_reclaimable_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id, str) or not self.finding_id:
            raise ValueError("finding_id is required")
        value = getattr(self, "kind")
        if value not in FINDING_KINDS:
            raise ValueError("invalid finding kind")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("display_name is required")
        for name in ("filesystem_id", "owner_kind", "owner_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string or null")
        _non_negative(self.observed_bytes, "observed_bytes")
        if not isinstance(self.capacity_accounted, bool):
            raise ValueError("capacity_accounted must be boolean")
        if self.capacity_accounted and self.overlap != "none":
            raise ValueError("overlapping findings cannot be capacity accounted")
        if self.overlap not in OVERLAP_STATES:
            raise ValueError("invalid overlap state")
        if self.activity not in ACTIVITY_STATES:
            raise ValueError("invalid activity state")
        if self.guidance not in GUIDANCE_STATES:
            raise ValueError("invalid guidance")
        object.__setattr__(self, "evidence", _strings(self.evidence, "evidence"))
        object.__setattr__(
            self, "limitations", _strings(self.limitations, "limitations"),
        )
        for name in (
            "unique_bytes", "shared_bytes", "potentially_reclaimable_bytes",
        ):
            _non_negative(getattr(self, name), name, optional=True)

    def to_dict(self) -> dict:
        return redact({
            "finding_id": self.finding_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "filesystem_id": self.filesystem_id,
            "owner": {"kind": self.owner_kind, "id": self.owner_id},
            "observed_bytes": self.observed_bytes,
            "capacity_accounted": self.capacity_accounted,
            "overlap": self.overlap,
            "activity": self.activity,
            "guidance": self.guidance,
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
            "unique_bytes": self.unique_bytes,
            "shared_bytes": self.shared_bytes,
            "potentially_reclaimable_bytes": self.potentially_reclaimable_bytes,
        })

    @classmethod
    def from_dict(cls, value: dict) -> "AttributionFinding":
        owner = value.get("owner") or {}
        return cls(
            finding_id=value.get("finding_id"),
            kind=value.get("kind"),
            display_name=value.get("display_name"),
            filesystem_id=value.get("filesystem_id"),
            owner_kind=owner.get("kind"),
            owner_id=owner.get("id"),
            observed_bytes=value.get("observed_bytes"),
            capacity_accounted=value.get("capacity_accounted", False),
            overlap=value.get("overlap"),
            activity=value.get("activity"),
            guidance=value.get("guidance"),
            evidence=tuple(value.get("evidence") or ()),
            limitations=tuple(value.get("limitations") or ()),
            unique_bytes=value.get("unique_bytes"),
            shared_bytes=value.get("shared_bytes"),
            potentially_reclaimable_bytes=value.get(
                "potentially_reclaimable_bytes",
            ),
        )


@dataclass(frozen=True)
class CapabilityObservation:
    category: str
    name: str
    version: str | None
    fallback: bool
    privilege: str
    status: str
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.category, str) or not self.category:
            raise ValueError("capability category is required")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("capability name is required")
        if self.version is not None and not isinstance(self.version, str):
            raise ValueError("capability version must be a string or null")
        if not isinstance(self.fallback, bool):
            raise ValueError("fallback must be boolean")
        if self.privilege not in PRIVILEGE_STATES:
            raise ValueError("invalid privilege")
        if self.status not in {"complete", "partial", "timed_out", "unavailable"}:
            raise ValueError("invalid capability status")
        object.__setattr__(
            self, "limitations", _strings(self.limitations, "limitations"),
        )

    def to_dict(self) -> dict:
        return redact({
            "category": self.category,
            "name": self.name,
            "version": self.version,
            "fallback": self.fallback,
            "privilege": self.privilege,
            "status": self.status,
            "limitations": list(self.limitations),
        })

    @classmethod
    def from_dict(cls, value: dict) -> "CapabilityObservation":
        return cls(
            category=value.get("category"),
            name=value.get("name"),
            version=value.get("version"),
            fallback=value.get("fallback", False),
            privilege=value.get("privilege"),
            status=value.get("status"),
            limitations=tuple(value.get("limitations") or ()),
        )


@dataclass(frozen=True)
class CoverageObservation:
    category: str
    boundary_id: str | None
    status: str
    duration_ms: int
    confidence: str
    privilege_sufficient: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, str) or not self.category:
            raise ValueError("coverage category is required")
        if self.boundary_id is not None and (
            not isinstance(self.boundary_id, str) or not self.boundary_id
        ):
            raise ValueError("boundary_id must be a non-empty string or null")
        if self.status not in COVERAGE_STATES:
            raise ValueError("invalid coverage status")
        _non_negative(self.duration_ms, "duration_ms")
        if self.confidence not in CONFIDENCE_STATES:
            raise ValueError("invalid coverage confidence")
        if not isinstance(self.privilege_sufficient, bool):
            raise ValueError("privilege_sufficient must be boolean")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("coverage reason must be a string or null")

    def to_dict(self) -> dict:
        return redact({
            "category": self.category,
            "boundary_id": self.boundary_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "confidence": self.confidence,
            "privilege_sufficient": self.privilege_sufficient,
            "reason": self.reason,
        })

    @classmethod
    def from_dict(cls, value: dict) -> "CoverageObservation":
        return cls(
            category=value.get("category"),
            boundary_id=value.get("boundary_id"),
            status=value.get("status"),
            duration_ms=value.get("duration_ms"),
            confidence=value.get("confidence"),
            privilege_sufficient=value.get("privilege_sufficient"),
            reason=value.get("reason"),
        )


@dataclass(frozen=True)
class AttributionReconciliation:
    used_bytes: int
    directory_allocated_bytes: int
    deleted_open_bytes: int
    observable_overhead_bytes: int
    overlapping_logical_bytes: int
    accounted_bytes: int
    residual_unexplained_bytes: int
    overage_bytes: int
    drift_bytes: int
    drift_material: bool
    capacity_drift_bytes: int = 0
    attributed_drift_bytes: int = 0
    capacity_drift_material: bool = False
    attributed_drift_material: bool = False

    def __post_init__(self) -> None:
        for name in (
            "used_bytes", "directory_allocated_bytes", "deleted_open_bytes",
            "observable_overhead_bytes", "overlapping_logical_bytes",
            "accounted_bytes", "residual_unexplained_bytes", "overage_bytes",
            "drift_bytes",
            "capacity_drift_bytes", "attributed_drift_bytes",
        ):
            _non_negative(getattr(self, name), name)
        if self.accounted_bytes > self.used_bytes:
            raise ValueError("accounted bytes cannot exceed used bytes")
        if self.accounted_bytes + self.residual_unexplained_bytes != self.used_bytes:
            raise ValueError("deep reconciliation must equal used bytes")
        if not isinstance(self.drift_material, bool):
            raise ValueError("drift_material must be boolean")
        if not isinstance(self.capacity_drift_material, bool):
            raise ValueError("capacity_drift_material must be boolean")
        if not isinstance(self.attributed_drift_material, bool):
            raise ValueError("attributed_drift_material must be boolean")

    def to_dict(self) -> dict:
        return {
            name: getattr(self, name)
            for name in (
                "used_bytes", "directory_allocated_bytes",
                "deleted_open_bytes", "observable_overhead_bytes",
                "overlapping_logical_bytes", "accounted_bytes",
                "residual_unexplained_bytes", "overage_bytes", "drift_bytes",
                "drift_material",
                "capacity_drift_bytes", "attributed_drift_bytes",
                "capacity_drift_material", "attributed_drift_material",
            )
        }

    @classmethod
    def from_dict(cls, value: dict) -> "AttributionReconciliation":
        kwargs = {
            name: value.get(name)
            for name in (
                "used_bytes", "directory_allocated_bytes",
                "deleted_open_bytes", "observable_overhead_bytes",
                "overlapping_logical_bytes", "accounted_bytes",
                "residual_unexplained_bytes", "overage_bytes", "drift_bytes",
                "drift_material",
            )
        }
        for name, default in (
            ("capacity_drift_bytes", kwargs["drift_bytes"]),
            ("attributed_drift_bytes", 0),
            ("capacity_drift_material", kwargs["drift_material"]),
            ("attributed_drift_material", False),
        ):
            kwargs[name] = value.get(name, default)
        return cls(**kwargs)


def reconcile_attribution(
    *,
    used_bytes: int,
    directory_allocated_bytes: int,
    deleted_open_bytes: int = 0,
    observable_overhead_bytes: int = 0,
    overlapping_logical_bytes: int = 0,
    drift_bytes: int = 0,
    capacity_drift_bytes: int | None = None,
    attributed_drift_bytes: int = 0,
) -> AttributionReconciliation:
    raw = (
        int(directory_allocated_bytes)
        + int(deleted_open_bytes)
        + int(observable_overhead_bytes)
    )
    used = max(int(used_bytes), 0)
    accounted = min(max(raw, 0), used)
    capacity_drift = max(int(
        drift_bytes if capacity_drift_bytes is None else capacity_drift_bytes
    ), 0)
    attributed_drift = max(int(attributed_drift_bytes), 0)
    threshold = max(int(used * 0.01), 64 * 1024 * 1024)
    capacity_material = capacity_drift > threshold
    attributed_material = attributed_drift > threshold
    return AttributionReconciliation(
        used_bytes=used,
        directory_allocated_bytes=max(int(directory_allocated_bytes), 0),
        deleted_open_bytes=max(int(deleted_open_bytes), 0),
        observable_overhead_bytes=max(int(observable_overhead_bytes), 0),
        overlapping_logical_bytes=max(int(overlapping_logical_bytes), 0),
        accounted_bytes=accounted,
        residual_unexplained_bytes=used - accounted,
        overage_bytes=max(raw - used, 0),
        drift_bytes=max(capacity_drift, attributed_drift),
        drift_material=capacity_material or attributed_material,
        capacity_drift_bytes=capacity_drift,
        attributed_drift_bytes=attributed_drift,
        capacity_drift_material=capacity_material,
        attributed_drift_material=attributed_material,
    )


@dataclass(frozen=True)
class DeepAttribution:
    status: str
    filesystems: tuple[FilesystemObservation, ...]
    findings: tuple[AttributionFinding, ...]
    capabilities: tuple[CapabilityObservation, ...]
    coverage: tuple[CoverageObservation, ...]
    reconciliation: AttributionReconciliation
    capacity_scope_id: str | None = None
    directory_index: dict | None = None
    managed_root_measurements: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in DEEP_STATES:
            raise ValueError("invalid deep attribution status")
        if self.directory_index is not None and not isinstance(
            self.directory_index, dict,
        ):
            raise ValueError("directory_index must be an object or null")
        if self.capacity_scope_id is not None and (
            not isinstance(self.capacity_scope_id, str) or not self.capacity_scope_id
        ):
            raise ValueError("capacity_scope_id must be a non-empty string or null")
        for item in self.managed_root_measurements:
            if not isinstance(item, dict):
                raise ValueError("managed_root_measurements must contain objects")
            if not isinstance(item.get("owner_id"), str) or not item["owner_id"]:
                raise ValueError("managed root measurements require owner_id")
            if item.get("size_state") not in {
                "measured", "not_measured", "timed_out", "unavailable",
            }:
                raise ValueError("invalid managed root measurement state")
            size = item.get("size_bytes")
            if size is not None and (
                isinstance(size, bool) or not isinstance(size, int) or size < 0
            ):
                raise ValueError("managed root size must be a non-negative integer")
            if item.get("size_state") == "measured" and size is None:
                raise ValueError("measured managed roots require size_bytes")
            if item.get("size_state") != "measured" and size is not None:
                raise ValueError("unmeasured managed roots cannot have size_bytes")

    def to_dict(self) -> dict:
        return redact({
            "status": self.status,
            "filesystems": [item.to_dict() for item in self.filesystems],
            "findings": [item.to_dict() for item in self.findings],
            "capabilities": [item.to_dict() for item in self.capabilities],
            "coverage": [item.to_dict() for item in self.coverage],
            "reconciliation": self.reconciliation.to_dict(),
            "capacity_scope_id": self.capacity_scope_id,
            "directory_index": self.directory_index,
            "managed_root_measurements": list(self.managed_root_measurements),
        })

    @classmethod
    def from_dict(cls, value: dict | None) -> "DeepAttribution | None":
        if value is None:
            return None
        return cls(
            status=value.get("status"),
            filesystems=tuple(
                FilesystemObservation.from_dict(item)
                for item in value.get("filesystems") or ()
            ),
            findings=tuple(
                AttributionFinding.from_dict(item)
                for item in value.get("findings") or ()
            ),
            capabilities=tuple(
                CapabilityObservation.from_dict(item)
                for item in value.get("capabilities") or ()
            ),
            coverage=tuple(
                CoverageObservation.from_dict(item)
                for item in value.get("coverage") or ()
            ),
            reconciliation=AttributionReconciliation.from_dict(
                value.get("reconciliation") or {},
            ),
            capacity_scope_id=value.get("capacity_scope_id"),
            directory_index=(
                value.get("directory_index")
                if isinstance(value.get("directory_index"), dict) else None
            ),
            managed_root_measurements=tuple(
                item for item in value.get("managed_root_measurements") or ()
                if isinstance(item, dict)
            ),
        )


def apply_cleanup_guidance(
    deep: DeepAttribution,
    resources: Iterable[Any],
) -> DeepAttribution:
    """Reference only an already-established exact cleanup classification."""
    resource_rows = tuple(resources)
    kind_map = {
        "container_image": "image",
        "container": "container",
        "volume": "volume",
        "build_cache": "build_cache",
    }
    findings = []
    for finding in deep.findings:
        static_guidance = {
            "filesystem_overhead": "non_cleanable",
            "directory": "manual",
            "deleted_open": "manual",
        }.get(finding.kind)
        resource_kind = kind_map.get(finding.kind)
        locator = finding.owner_id
        matches = [
            item for item in resource_rows
            if resource_kind
            and isinstance(locator, str) and locator
            and item.kind == resource_kind
            and item.locator == locator
            and item.resource_id == _identifier(resource_kind, locator)
        ]
        existing_scope = (
            {
                "disposable_cache": "existing_cache_scope",
                "stale_candidate": "existing_stale_scope",
            }.get(matches[0].classification)
            if len(matches) == 1 else None
        )
        guidance = static_guidance or existing_scope or "monitoring_only"
        findings.append(replace(finding, guidance=guidance))
    return replace(deep, findings=tuple(findings))


def parse_df_output(output: str) -> list[dict]:
    rows = []
    for index, line in enumerate(str(output or "").splitlines()):
        if index == 0 and "filesystem" in line.lower():
            continue
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        source, total, used, available, _capacity, mount_point = parts
        try:
            values = [int(total) * 1024, int(used) * 1024, int(available) * 1024]
        except ValueError:
            continue
        if not mount_point.startswith("/"):
            continue
        rows.append({
            "source": source,
            "mount_point": mount_point,
            "total_bytes": values[0],
            "used_bytes": values[1],
            "available_bytes": values[2],
        })
    return sorted(rows, key=lambda item: (len(item["mount_point"]), item["mount_point"]))


_VIRTUAL_FILESYSTEMS = frozenset({
    "autofs", "cgroup", "cgroup2", "configfs", "debugfs", "devfs",
    "devpts", "devtmpfs", "fusectl", "hugetlbfs", "mqueue", "proc",
    "pstore", "securityfs", "sysfs", "tmpfs", "tracefs",
})
_REMOTE_FILESYSTEMS = frozenset({
    "9p", "afpfs", "cifs", "fuse.sshfs", "nfs", "nfs4", "smbfs", "sshfs",
})
_SAFE_MOUNT_FLAGS = frozenset({"nodev", "noexec", "nosuid"})
_DIRECTORY_CACHE_TTL = 6 * 60 * 60
_DIRECTORY_CACHE_MIN_BYTES = 32 * 1024 * 1024
_DIRECTORY_CACHE_MAX_ROWS = 20_000


def _capacity_scope_identity(source: str, filesystem_type: str) -> str:
    normalized_source = source
    if filesystem_type == "apfs":
        match = re.match(r"^/dev/(disk\d+)s\d+", source)
        if match:
            normalized_source = f"/dev/{match.group(1)}"
    return _identifier("capacity-scope", normalized_source, filesystem_type)


def parse_mount_output(
    output: str,
    *,
    capacity_rows: Iterable[dict] = (),
) -> list[dict]:
    """Merge safe mount topology into capacity rows without exposing sources."""
    capacities = {
        os.path.normpath(str(row.get("mount_point") or "")): dict(row)
        for row in capacity_rows
        if str(row.get("mount_point") or "").startswith("/")
    }
    parsed: dict[str, dict] = {}
    pattern = re.compile(
        r"^(?P<source>.+?) on (?P<mount>/\S*|/) "
        r"(?:type )?(?P<type>[A-Za-z0-9_.+-]+) "
        r"\((?P<options>[^)]*)\)$"
    )
    darwin_pattern = re.compile(
        r"^(?P<source>.+?) on (?P<mount>/\S*|/) "
        r"\((?P<type>[A-Za-z0-9_.+-]+)(?:, (?P<options>.*))?\)$"
    )
    for line in str(output or "").splitlines():
        match = pattern.match(line.strip()) or darwin_pattern.match(line.strip())
        if not match:
            continue
        mount = os.path.normpath(match.group("mount"))
        filesystem_type = match.group("type").lower()[:40]
        options = {
            item.strip().lower()
            for item in (match.group("options") or "").split(",")
            if item.strip()
        }
        writable = not ({"ro", "read-only"} & options)
        source = match.group("source")
        remote = filesystem_type in _REMOTE_FILESYSTEMS
        scope_id = _capacity_scope_identity(source, filesystem_type)
        mount_id = _identifier("mount", source, mount)
        parsed[mount] = {
            **capacities.get(mount, {}),
            "source": source,
            "mount_point": mount,
            "filesystem_type": filesystem_type,
            "writable": writable,
            "mount_id": mount_id,
            "capacity_scope_id": scope_id,
            "mount_flags": tuple(sorted({
                "read_write" if writable else "read_only",
                "virtual" if filesystem_type in _VIRTUAL_FILESYSTEMS else (
                    "remote" if remote else "local"
                ),
                *(_SAFE_MOUNT_FLAGS & options),
            })),
            "virtual": filesystem_type in _VIRTUAL_FILESYSTEMS,
            "remote": remote,
        }
    for mount, capacity in capacities.items():
        if mount in parsed:
            continue
        source = str(capacity.get("source") or "unknown")
        parsed[mount] = {
            **capacity,
            "filesystem_type": "unknown",
            "writable": None,
            "mount_id": _identifier("mount", source, mount),
            "capacity_scope_id": _capacity_scope_identity(source, "unknown"),
            "mount_flags": ("local",),
            "virtual": False,
            "remote": False,
        }
    rows = sorted(parsed.values(), key=lambda row: (
        len(row["mount_point"]), row["mount_point"],
    ))
    for row in rows:
        mount = row["mount_point"]
        parents = [
            candidate for candidate in rows
            if candidate is not row
            and (
                mount == candidate["mount_point"]
                or mount.startswith(candidate["mount_point"].rstrip("/") + os.sep)
            )
        ]
        parent = max(parents, key=lambda item: len(item["mount_point"]), default=None)
        row["parent_mount_id"] = parent["mount_id"] if parent else None
        flags = set(row["mount_flags"])
        flags.add("nested" if parent else "root")
        row["mount_flags"] = tuple(sorted(flags))
    return rows


def _managed_path(value: Any) -> Path | None:
    raw = value.get("path") if isinstance(value, dict) else getattr(value, "path", None)
    if not isinstance(raw, (str, os.PathLike)):
        return None
    try:
        return Path(raw).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _mount_for_system(
    path: Path,
    rows: list[dict],
    *,
    system_name: str,
) -> str | None:
    local_rows = [
        row for row in rows if not row.get("virtual") and not row.get("remote")
    ]
    mount = _mount_for(path, local_rows)
    if system_name != "Darwin":
        return mount
    target = str(path.resolve(strict=False))
    if mount is None and target.startswith("/private/"):
        alias = target[len("/private"):]
        matches = [
            str(row.get("mount_point") or "") for row in local_rows
            if alias == str(row.get("mount_point") or "")
            or alias.startswith(
                str(row.get("mount_point") or "").rstrip("/") + os.sep
            )
        ]
        mount = max(matches, key=len) if matches else None
    data_prefixes = ("/Applications", "/Library", "/Users", "/private")
    if not any(target == prefix or target.startswith(prefix + os.sep) for prefix in data_prefixes):
        return mount
    data_mount = next((
        row["mount_point"] for row in local_rows
        if row.get("mount_point") == "/System/Volumes/Data"
    ), None)
    return data_mount or mount


def select_filesystem_mounts(
    rows: list[dict],
    *,
    host_root: Path,
    sandbox_home: Path,
    docker_root: Path | None = None,
    managed_roots: Iterable[Any] = (),
    system_name: str | None = None,
) -> dict[str, str]:
    """Return one selection reason per mount, preferring the strongest reason."""
    system_name = system_name or platform.system()
    priorities = {"root": 4, "sandbox_home": 3, "container_data": 2, "managed_root": 1}
    selected: dict[str, str] = {}

    def add(path: Path | None, reason: str) -> None:
        if path is None:
            return
        mount = _mount_for_system(path, rows, system_name=system_name)
        if mount and priorities[reason] > priorities.get(selected.get(mount, ""), 0):
            selected[mount] = reason

    add(host_root, "root")
    add(sandbox_home, "sandbox_home")
    add(docker_root, "container_data")
    for record in managed_roots:
        add(_managed_path(record), "managed_root")
    return selected


def parse_gdu_output(
    output: str,
    *,
    filesystem_id: str,
    root: str,
    limit: int = 100,
    safe_labels: dict[str, str] | None = None,
) -> tuple[tuple[AttributionFinding, ...], int]:
    safe_roots = {
        "/var": "host variable data",
        "/home": "user home data",
        "/root": "root user data",
        "/usr": "system software",
        "/opt": "optional software",
        "/srv": "service data",
        "/tmp": "temporary data",
        "/boot": "boot data",
        "/etc": "host configuration",
        "/var/lib": "host state data",
        "/var/log": "host logs",
        "/var/cache": "host package cache",
    }
    safe_roots.update(safe_labels or {})
    rows = []
    root_total = None
    for line in str(output or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            size = int(parts[0])
        except ValueError:
            continue
        path = parts[1].strip()
        if size < 0 or not path:
            continue
        if path.rstrip("/") == str(root).rstrip("/"):
            root_total = size
            continue
        rows.append((size, path))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    findings = tuple(
        AttributionFinding(
            finding_id=_identifier("directory", filesystem_id, path),
            kind="directory",
            display_name=safe_roots.get(path, f"entry {index + 1}"),
            filesystem_id=filesystem_id,
            owner_kind="host",
            owner_id=None,
            observed_bytes=size,
            capacity_accounted=False,
            overlap="directory_root",
            activity="unknown",
            guidance="monitoring_only",
            evidence=("allocated_blocks", "one_filesystem"),
            limitations=(),
        )
        for index, (size, path) in enumerate(rows[:max(int(limit), 0)])
    )
    paths = {os.path.normpath(path) for _size, path in rows}
    frontier = (
        (size, path) for size, path in rows
        if not any(
            parent != os.path.normpath(path)
            and os.path.normpath(path).startswith(parent.rstrip("/") + os.sep)
            for parent in paths
        )
    )
    total = (
        root_total if root_total is not None
        else sum(size for size, _path in frontier)
    )
    return findings, total


def parse_du_output(
    output: str,
    *,
    filesystem_id: str,
    root: str,
    limit: int = 100,
    safe_labels: dict[str, str] | None = None,
) -> tuple[tuple[AttributionFinding, ...], int]:
    converted = []
    for line in str(output or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            size = int(parts[0]) * 1024
        except ValueError:
            continue
        converted.append(f"{size} {parts[1]}")
    return parse_gdu_output(
        "\n".join(converted),
        filesystem_id=filesystem_id,
        root=root,
        limit=limit,
        safe_labels=safe_labels,
    )


def parse_lsof_fields(
    output: str,
    *,
    filesystem_id: str | None,
    filesystem_by_device: dict[str, str] | None = None,
    block_size: int = 512,
    require_deleted_marker: bool = False,
) -> tuple[tuple[AttributionFinding, ...], int]:
    process: dict[str, str] = {}
    current: dict[str, str] | None = None
    records: list[tuple[dict[str, str], dict[str, str]]] = []

    def flush() -> None:
        nonlocal current
        if current:
            records.append((dict(process), current))
        current = None

    for line in str(output or "").splitlines():
        if not line:
            continue
        key, value = line[0], line[1:]
        if key == "p":
            flush()
            process = {"p": value}
        elif key == "c":
            process["c"] = value
        elif key == "f":
            flush()
            current = {"f": value}
        elif current is not None:
            current[key] = value
    flush()

    seen = set()
    by_process: dict[tuple[str | None, str, str], tuple[int, bool]] = {}
    for proc, record in records:
        if record.get("t") not in {"REG", "VREG"}:
            continue
        if require_deleted_marker and "(deleted)" not in record.get("n", ""):
            continue
        allocated = record.get("B", record.get("b"))
        try:
            size = (
                int(allocated) * max(int(block_size), 1)
                if allocated is not None else int(record.get("s", ""))
            )
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        device, inode = record.get("D"), record.get("i")
        if device and inode:
            identity = (device, inode)
        else:
            identity = (
                proc.get("p", ""), record.get("f", ""),
                record.get("n", ""), str(size),
            )
        if identity in seen:
            continue
        seen.add(identity)
        pid = proc.get("p") or "unknown"
        command = proc.get("c") or "unknown"
        safe_command = re.sub(
            r"(?i)(token|password|secret|credential).*$",
            "[redacted]",
            command,
        ).strip() or "unknown"
        mapped_filesystem = next((
            (filesystem_by_device or {}).get(alias)
            for alias in _device_aliases(device)
            if (filesystem_by_device or {}).get(alias)
        ), None) or filesystem_id
        key = (mapped_filesystem, pid, safe_command)
        previous, all_allocated = by_process.get(key, (0, True))
        by_process[key] = (previous + size, all_allocated and allocated is not None)

    findings = tuple(
        AttributionFinding(
            finding_id=_identifier("deleted-open", mapped_filesystem or "", pid),
            kind="deleted_open",
            display_name=f"process {pid}",
            filesystem_id=mapped_filesystem,
            owner_kind="process",
            owner_id=pid,
            observed_bytes=size,
            # Apparent size is useful diagnostic evidence but is not physical
            # allocation. Only allocated-block evidence may reduce residual.
            capacity_accounted=(
                mapped_filesystem is not None and all_allocated
            ),
            overlap="none",
            activity="active",
            guidance="manual",
            evidence=(
                "zero_link_count", "regular_file",
                "allocated_blocks" if all_allocated else "apparent_size_fallback",
                f"command:{command}",
            ),
            limitations=() if all_allocated else ("allocated_blocks_unavailable",),
        )
        for (mapped_filesystem, pid, command), (size, all_allocated) in sorted(
            by_process.items(),
            key=lambda item: (
                item[1][0], item[0][0] or "", item[0][1], item[0][2],
            ),
            reverse=True,
        )
    )
    return findings, sum(item.observed_bytes for item in findings)


def _device_aliases(value: str | int | None) -> tuple[str, ...]:
    if value is None:
        return ()
    text = str(value).strip().lower()
    aliases = {text}
    if "," in text:
        major_text, minor_text = text.split(",", 1)
        for base in (10, 16):
            try:
                device = os.makedev(int(major_text, base), int(minor_text, base))
            except ValueError:
                continue
            aliases.update({str(device), hex(device), f"{device:x}"})
    else:
        for base in (0, 16):
            try:
                device = int(text, base)
            except ValueError:
                continue
            aliases.update({str(device), hex(device), f"{device:x}"})
    return tuple(sorted(aliases))


def add_proc_allocated_blocks(output: str) -> str:
    """Add safe allocated-block fields from Linux proc descriptors when visible."""
    pid = None
    rendered: list[str] = []
    for line in str(output or "").splitlines():
        rendered.append(line)
        if line.startswith("p"):
            pid = line[1:] if line[1:].isdigit() else None
        elif line.startswith("f") and pid:
            match = re.match(r"(\d+)", line[1:])
            if not match:
                continue
            try:
                stat = os.stat(f"/proc/{pid}/fd/{match.group(1)}")
            except OSError:
                continue
            rendered.append(f"B{max(int(stat.st_blocks), 0)}")
    return "\n".join(rendered)


def parse_docker_disk_usage(
    output: str,
    *,
    limit: int = 100,
) -> tuple[tuple[AttributionFinding, ...], int]:
    try:
        payload = json.loads(str(output or ""))
    except (TypeError, json.JSONDecodeError):
        return (), 0
    if not isinstance(payload, dict):
        return (), 0
    rows: list[AttributionFinding] = []

    def add(
        kind: str,
        identity: str,
        display: str,
        size: Any,
        *,
        overlap: str,
        activity: str,
        guidance: str,
        evidence: tuple[str, ...],
        unique: Any = None,
        shared: Any = None,
        reclaimable: Any = None,
    ) -> None:
        measured = parse_byte_size(size)
        if measured is None:
            return
        unique_bytes = parse_byte_size(unique)
        shared_bytes = parse_byte_size(shared)
        reclaimable_bytes = parse_byte_size(reclaimable)
        rows.append(AttributionFinding(
            finding_id=_identifier(kind, identity),
            kind=kind,
            display_name=display[:120] or kind.replace("_", " "),
            filesystem_id=None,
            owner_kind="container_engine",
            owner_id=identity,
            observed_bytes=measured,
            capacity_accounted=False,
            overlap=overlap,
            activity=activity,
            guidance=guidance,
            evidence=evidence,
            limitations=("logical_engine_accounting",),
            unique_bytes=measured if unique_bytes is None else unique_bytes,
            shared_bytes=0 if shared_bytes is None else shared_bytes,
            potentially_reclaimable_bytes=(
                0 if reclaimable_bytes is None else reclaimable_bytes
            ),
        ))

    for row in payload.get("Images") or ():
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or row.get("Repository") or "")
        if not identity:
            continue
        display = str(row.get("Repository") or "image")
        if row.get("Tag") not in {None, "", "<none>"}:
            display += ":" + str(row.get("Tag"))
        unique_value = row.get("UniqueSize")
        if unique_value is None:
            total_value = parse_byte_size(row.get("Size"))
            shared_value = parse_byte_size(row.get("SharedSize"))
            unique_value = (
                max(total_value - shared_value, 0)
                if total_value is not None and shared_value is not None
                else row.get("Size")
            )
        add(
            "container_image", identity, display,
            unique_value,
            overlap="shared_layers",
            activity="active" if str(row.get("Containers") or "0") != "0" else "inactive",
            guidance="monitoring_only",
            evidence=("docker_system_df", "unique_size", "shared_size_reported"),
            unique=unique_value,
            shared=row.get("SharedSize", 0),
            reclaimable=(
                unique_value
                if str(row.get("Containers") or "0") == "0" else 0
            ),
        )
    for row in payload.get("Containers") or ():
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or row.get("Names") or "")
        if not identity:
            continue
        state = str(row.get("State") or "").lower()
        add(
            "container", identity, str(row.get("Names") or "container"),
            row.get("Size"),
            overlap="directory_root",
            activity="active" if state == "running" else "inactive",
            guidance="monitoring_only",
            evidence=("docker_system_df", "writable_layer"),
            reclaimable=row.get("Size") if state != "running" else 0,
        )
    for row in payload.get("LocalVolumes") or ():
        if not isinstance(row, dict):
            continue
        identity = str(row.get("Name") or "")
        if not identity:
            continue
        active = str(row.get("Links") or "0") != "0"
        add(
            "volume", identity, identity,
            row.get("Size"),
            overlap="directory_root",
            activity="active" if active else "inactive",
            guidance="monitoring_only",
            evidence=("docker_system_df", "volume_detail"),
            reclaimable=row.get("Size") if not active else 0,
        )
    for row in payload.get("BuildCache") or ():
        if not isinstance(row, dict):
            continue
        identity = str(row.get("ID") or "")
        if not identity:
            continue
        active = str(row.get("InUse") or "").lower() == "true"
        add(
            "build_cache", identity, f"build cache {identity[:12]}",
            row.get("Size"),
            overlap="logical_cache",
            activity="active" if active else "inactive",
            guidance="monitoring_only",
            evidence=("docker_system_df", "build_cache_detail"),
            reclaimable=(
                row.get("Size")
                if str(row.get("Reclaimable") or "").lower() in {"true", "yes"}
                or not active else 0
            ),
        )
    rows.sort(key=lambda item: (
        item.observed_bytes, item.kind, item.finding_id,
    ), reverse=True)
    logical = sum(item.observed_bytes for item in rows)
    return tuple(rows[:max(int(limit), 0)]), logical


def _mount_for(path: Path, rows: list[dict]) -> str | None:
    target = str(path.resolve(strict=False))
    matches = []
    for row in rows:
        mount = str(row.get("mount_point") or "")
        if target == mount or target.startswith(mount.rstrip("/") + os.sep):
            matches.append(mount)
    return max(matches, key=len) if matches else None


def _directory_cache_rows(
    output: str,
    *,
    multiplier: int,
    keep_prefixes: Iterable[str] = (),
) -> list[list[object]]:
    """Keep a bounded, path-ranked directory index from du/gdu output.

    The cache is an observation aid, not cleanup authority.  Retaining only
    material rows plus managed prefixes keeps the host-local durable result
    small while preserving enough evidence to answer the next status request
    without another full inode walk.
    """
    prefixes = tuple(
        str(item).rstrip(os.sep) for item in keep_prefixes if str(item)
    )
    rows: list[list[object]] = []
    for line in str(output or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            measured = int(parts[0]) * int(multiplier)
        except (TypeError, ValueError):
            continue
        path = parts[1].strip()
        if measured < _DIRECTORY_CACHE_MIN_BYTES and not any(
            path == prefix or path.startswith(prefix + os.sep)
            for prefix in prefixes
        ):
            continue
        rows.append([max(measured, 0), path])
        if len(rows) >= _DIRECTORY_CACHE_MAX_ROWS:
            break
    rows.sort(key=lambda item: (int(item[0]), str(item[1])), reverse=True)
    return rows


def _managed_root_sizes(
    output: str,
    *,
    multiplier: int,
    managed_roots: Iterable[Any],
) -> dict[str, int]:
    """Extract exact root rows from one bounded multi-path ``du`` call."""
    paths: dict[str, str] = {}
    for record in managed_roots:
        path = _managed_path(record)
        owner_id = record.get("owner_id") if isinstance(record, dict) else None
        if path is None or not isinstance(owner_id, str) or not owner_id:
            continue
        paths[os.path.normpath(str(path))] = owner_id
    sizes: dict[str, int] = {}
    for line in str(output or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            size = int(parts[0]) * int(multiplier)
        except (TypeError, ValueError):
            continue
        path = os.path.normpath(parts[1].strip())
        owner_id = paths.get(path)
        if owner_id is not None and size >= 0:
            sizes[owner_id] = size
    return sizes


def _directory_cache_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and isinstance(
        payload.get("mounts"), dict,
    ) else None


def _write_directory_cache(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(path.name + ".staging")
        staging.write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8",
        )
        os.replace(staging, path)
        return True
    except (OSError, UnicodeError, ValueError, AttributeError, RuntimeError):
        return False


class DeepAttributionCollector:
    """Bounded host collector; command selection is read-only and explicit."""

    def __init__(
        self,
        runner,
        *,
        host_root: Path = Path("/"),
        sandbox_home: Path,
        which=shutil.which,
        system=platform.system,
        monotonic=time.monotonic,
    ) -> None:
        self.runner = runner
        self.host_root = Path(host_root).resolve(strict=False)
        self.sandbox_home = Path(sandbox_home).resolve(strict=False)
        self.which = which
        self.system = system
        self.monotonic = monotonic

    def _run(self, argv, deadline: float, maximum: float):
        command = tuple(str(item) for item in argv)
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            return ProcessResult(command, 124, "", "overall deadline exhausted")
        timeout = min(remaining, maximum)
        return self.runner.run(command, timeout=timeout)

    @staticmethod
    def _state(returncode: int) -> str:
        return "timed_out" if returncode == 124 else "unavailable"

    def _directory_cache_path(self) -> Path:
        return self.sandbox_home / "runtime" / "resources" / "directory-index.json"

    def _directory_cache_read(self) -> dict | None:
        return _directory_cache_payload(self._directory_cache_path())

    def _directory_cache_lookup(
        self, mount: str, *, mode: str, now: float,
    ) -> dict | None:
        if mode == "refresh":
            return None
        payload = self._directory_cache_read()
        entry = (payload or {}).get("mounts", {}).get(mount)
        if not isinstance(entry, dict) or not isinstance(entry.get("rows"), list):
            return {
                "source": "cache_missing", "complete": False, "stale": True,
                "age_seconds": None, "rows": [],
            } if mode == "cache_only" else None
        try:
            created = float(entry.get("created_at") or 0)
        except (TypeError, ValueError):
            created = 0
        age = max(now - created, 0)
        fresh = age <= _DIRECTORY_CACHE_TTL
        if mode == "cache_only" or fresh:
            rows = [
                [int(item[0]), str(item[1])]
                for item in entry["rows"]
                if isinstance(item, (list, tuple)) and len(item) == 2
                and isinstance(item[0], int) and item[0] >= 0
                and isinstance(item[1], str) and item[1]
            ]
            return {
                "source": "cache", "complete": bool(entry.get("complete")),
                "stale": not fresh, "age_seconds": int(age), "rows": rows,
            }
        return None

    def _directory_cache_store(
        self, mount: str, *, rows: list[list[object]], complete: bool,
        now: float,
    ) -> bool:
        path = self._directory_cache_path()
        payload = self._directory_cache_read() or {
            "schema_version": 1, "mounts": {},
        }
        mounts = payload.setdefault("mounts", {})
        previous = mounts.get(mount)
        if (
            isinstance(previous, dict) and previous.get("complete")
            and not complete
        ):
            return False
        mounts[mount] = {
            "created_at": now, "complete": bool(complete), "rows": rows,
        }
        payload["schema_version"] = 1
        return _write_directory_cache(path, payload)

    @staticmethod
    def _cached_directory_output(rows: Iterable[list[object]]) -> str:
        # Cached rows are normalized to allocated bytes, which is the gdu
        # parser's unit. This keeps cache and fresh-scan reconciliation paths
        # identical without exposing paths outside the existing finding model.
        return "\n".join(
            f"{int(item[0])} {item[1]}"
            for item in rows
            if isinstance(item, (list, tuple)) and len(item) == 2
        )

    def _inventory(self, deadline: float, capacity: dict) -> tuple[list[dict], str]:
        result = self._run(("df", "-Pk"), deadline, 5)
        rows = parse_df_output(result.stdout) if result.returncode == 0 else []
        if rows:
            mount_result = self._run(("mount",), deadline, 3)
            return parse_mount_output(
                mount_result.stdout if mount_result.returncode == 0 else "",
                capacity_rows=rows,
            ), "complete" if mount_result.returncode == 0 else "partial"
        mount = str(self.host_root)
        fallback_rows = [{
            "source": "target",
            "mount_point": mount,
            "total_bytes": int(capacity.get("total_bytes") or 0),
            "used_bytes": int(capacity.get("used_bytes") or 0),
            "available_bytes": int(capacity.get("available_bytes") or 0),
        }]
        return (
            parse_mount_output("", capacity_rows=fallback_rows),
            self._state(result.returncode),
        )

    def collect(
        self,
        *,
        capacity: dict,
        budget_seconds: float,
        progress=None,
        managed_roots: Iterable[Any] = (),
        capacity_snapshots: dict[str, dict] | None = None,
        cancelled=False,
        directory_cache: str | None = None,
    ) -> DeepAttribution:
        started = self.monotonic()
        deadline = started + max(float(budget_seconds), 0.0)
        managed_roots = tuple(
            item for item in managed_roots
            if isinstance(item, dict) and _managed_path(item) is not None
        )
        # Interactive deep scans historically capped one directory command at
        # 120 seconds. That cap made a detached scan with a larger explicit
        # budget time out its primary host walk early, even though the durable
        # supervisor was still healthy. Reserve ten percent for deleted-open,
        # engine, and reconciliation phases while allowing the directory walk
        # to consume the rest of the caller's finite budget.
        directory_max = max(float(budget_seconds) * 0.9, 120.0)
        if progress:
            progress("deep_mounts")
        rows, mount_state = self._inventory(deadline, capacity)
        host_mount = _mount_for_system(
            self.host_root, rows, system_name=self.system(),
        ) or rows[0]["mount_point"]

        docker_root = None
        docker_info = self._run(
            ("docker", "info", "--format", "{{json .DockerRootDir}}"),
            deadline,
            4,
        )
        if docker_info.returncode == 0:
            try:
                docker_root = Path(json.loads(docker_info.stdout.strip()))
            except (TypeError, ValueError, json.JSONDecodeError):
                docker_root = None
        selected_reasons = select_filesystem_mounts(
            rows,
            host_root=self.host_root,
            sandbox_home=self.sandbox_home,
            docker_root=docker_root,
            managed_roots=managed_roots,
            system_name=self.system(),
        )

        gdu_path = self.which("gdu")
        scanner_name = "gdu" if gdu_path else "du"
        scanner_version = None
        preferred_scanner_failed = False
        if gdu_path:
            version = self._run((gdu_path, "--version"), deadline, 2)
            if version.returncode == 0:
                scanner_version = (version.stdout or version.stderr).splitlines()[0][:120]
        elevated = False
        if self.which("sudo"):
            sudo_check = self._run(("sudo", "-n", "true"), deadline, 2)
            elevated = sudo_check.returncode == 0
        prefix = ("sudo", "-n") if elevated else ()
        capabilities = []
        coverage = [CoverageObservation(
            category="mount_inventory",
            boundary_id=None,
            status=mount_state,
            duration_ms=max(int((self.monotonic() - started) * 1000), 0),
            confidence="high" if mount_state == "complete" else "low",
            privilege_sufficient=True,
            reason=(
                None if mount_state == "complete" else
                "mount_topology_unavailable" if mount_state == "partial" else
                "mount_inventory_unavailable"
            ),
        )]
        findings: list[AttributionFinding] = []
        filesystems = []
        directory_allocated = 0
        selected_used = 0
        accounted_filesystems: set[str] = set()
        used_scopes: set[str] = set()
        scanned_filesystems: set[str] = set()
        attributed_baseline = 0
        has_attributed_baseline = False
        attribution_rechecks: list[tuple[str, Any, int, tuple[str, ...]]] = []
        directory_indexes: dict[str, dict] = {}
        managed_root_measurements: list[dict] = []
        cache_mode = str(directory_cache or "auto")
        cache_now = time.time()

        def is_cancelled() -> bool:
            return bool(cancelled() if callable(cancelled) else cancelled)

        for index, row in enumerate(rows):
            mount = row["mount_point"]
            selected = mount in selected_reasons
            filesystem_id = _identifier(
                "filesystem", str(row.get("source") or ""), mount,
            )
            capacity_scope_id = str(
                row.get("capacity_scope_id")
                or _identifier("capacity-scope", str(row.get("source") or ""))
            )
            filesystem_scope_id = _identifier(
                "filesystem-scope",
                str(row.get("source") or ""),
                str(row.get("filesystem_type") or "unknown"),
            )
            snapshot = (capacity_snapshots or {}).get(mount) or (
                (capacity_snapshots or {}).get(filesystem_id)
            ) or row
            if selected and capacity_scope_id not in used_scopes:
                snapshot_total = max(int(snapshot.get("total_bytes") or 0), 0)
                snapshot_available = min(
                    max(int(snapshot.get("available_bytes") or 0), 0),
                    snapshot_total,
                )
                scope_used = (
                    snapshot_total - snapshot_available
                    if str(row.get("filesystem_type") or "").lower() == "apfs"
                    else min(
                        max(int(snapshot.get("used_bytes") or 0), 0),
                        snapshot_total,
                    )
                )
                selected_used += scope_used
                if snapshot.get("observed_allocated_bytes") is not None:
                    attributed_baseline += max(
                        int(snapshot.get("observed_allocated_bytes") or 0), 0,
                    )
                    has_attributed_baseline = True
                used_scopes.add(capacity_scope_id)
            status = "not_selected"
            observed = None
            hardlinks = "unavailable"
            filesystem_type = str(row.get("filesystem_type") or "unknown").lower()
            limitations = ["allocated_blocks_not_exact_physical_ownership"]
            if filesystem_type == "unknown":
                limitations.append("filesystem_capabilities_unverified")
            if filesystem_type in {"apfs", "btrfs", "overlay", "zfs"}:
                limitations.append("copy_on_write_or_shared_allocation")
            category_started = self.monotonic()
            reason = None
            nested_mounts: tuple[str, ...] = ()
            cache_entry = self._directory_cache_lookup(
                mount, mode=cache_mode, now=cache_now,
            ) if selected else None
            if selected and is_cancelled():
                status, reason = "cancelled", "request_cancelled"
            elif selected and filesystem_scope_id in scanned_filesystems:
                status = "not_selected"
                reason = "duplicate_filesystem_mount"
            elif selected and cache_entry is not None:
                scanned_filesystems.add(filesystem_scope_id)
                directory_indexes[mount] = cache_entry
                if cache_entry["source"] == "cache":
                    if progress:
                        progress("deep_directory_cache")
                    try:
                        parsed, observed = parse_gdu_output(
                            self._cached_directory_output(cache_entry["rows"]),
                            filesystem_id=filesystem_id,
                            root=mount,
                            limit=100,
                            safe_labels={
                                str(self.sandbox_home): "Sandbox home",
                                str(self.sandbox_home.parent):
                                    "Sandbox host account",
                                str(self.sandbox_home / "runtime"):
                                    "Sandbox runtime data",
                                str(self.sandbox_home / "deploy-src"):
                                    "Sandbox deployment sources",
                                str(self.sandbox_home / "sb-src"):
                                    "Sandbox tool source",
                            },
                        )
                    except Exception:
                        parsed, observed = (), None
                    if observed is None:
                        status, reason = "unavailable", "directory_index_parse_failed"
                    else:
                        findings.extend(parsed)
                        status = "complete" if (
                            cache_entry["complete"] and not cache_entry["stale"]
                        ) else "partial"
                        reason = (
                            None if status == "complete" else
                            "directory_index_cache_stale"
                            if cache_entry["stale"] else
                            "directory_index_cache_partial"
                        )
                        hardlinks = "confirmed" if status == "complete" else "partial"
                        if filesystem_scope_id not in accounted_filesystems:
                            directory_allocated += observed
                            accounted_filesystems.add(filesystem_scope_id)
            elif selected and self.monotonic() < deadline:
                scanned_filesystems.add(filesystem_scope_id)
                if progress:
                    progress("deep_directory")
                scan_root = (
                    str(self.host_root)
                    if mount == host_mount and self.host_root != Path("/")
                    else mount
                )
                nested_mounts = tuple(sorted(
                    str(candidate.get("mount_point") or "")
                    for candidate in rows
                    if str(candidate.get("mount_point") or "").rstrip("/")
                    != scan_root.rstrip("/")
                    and str(candidate.get("mount_point") or "").startswith(
                        scan_root.rstrip("/") + os.sep,
                    )
                ))
                if nested_mounts:
                    limitations.append("nested_mount_excluded")
                gdu_exclusions = tuple(
                    token
                    for nested in nested_mounts
                    for token in ("--exclude", nested)
                )
                du_exclusions = tuple(
                    token
                    for nested in nested_mounts
                    for token in (
                        ("-I", nested)
                        if self.system() == "Darwin"
                        else (f"--exclude={nested}",)
                    )
                )
                if gdu_path:
                    argv = (
                        *prefix, gdu_path, "-n", "-p", "-c", "--no-prefix",
                        "--depth", "4", "-x", "--no-delete", "--no-spawn-shell",
                        "--no-view-file", *gdu_exclusions, scan_root,
                    )
                    result = self._run(argv, deadline, directory_max)
                    parser = parse_gdu_output
                    hardlinks = "confirmed"
                    if (
                        result.returncode != 0
                        and not (result.returncode == 124 and result.stdout.strip())
                        and self.monotonic() < deadline
                    ):
                        preferred_scanner_failed = True
                        scanner_name = "du"
                        argv = (
                            *prefix, "du", "-x", "-k", "-d", "4",
                            *du_exclusions, scan_root,
                        )
                        result = self._run(argv, deadline, directory_max)
                        parser = parse_du_output
                else:
                    argv = (
                        *prefix, "du", "-x", "-k", "-d", "4",
                        *du_exclusions, scan_root,
                    )
                    result = self._run(argv, deadline, directory_max)
                    parser = parse_du_output
                    hardlinks = "confirmed"
                parsed, observed = parser(
                    result.stdout,
                    filesystem_id=filesystem_id,
                    root=scan_root,
                    limit=100,
                    safe_labels={
                        str(self.sandbox_home): "Sandbox home",
                        str(self.sandbox_home.parent):
                            "Sandbox host account",
                        str(self.sandbox_home / "runtime"):
                            "Sandbox runtime data",
                        str(self.sandbox_home / "deploy-src"):
                            "Sandbox deployment sources",
                        str(self.sandbox_home / "sb-src"):
                            "Sandbox tool source",
                        **({
                            str(docker_root): "Docker data root",
                            str(docker_root / "overlay2"):
                                "Docker image and container layers",
                            str(docker_root / "volumes"):
                                "Docker volume data",
                            str(docker_root / "buildkit"):
                                "Docker build cache data",
                            str(docker_root / "containers"):
                                "Docker container metadata and logs",
                            str(docker_root / "image"):
                                "Docker image metadata",
                        } if docker_root else {}),
                    },
                )
                # ``du`` can finish with a non-zero status after reporting
                # useful rows (for example, one unreadable pseudo-tree or a
                # transient inode disappearing during the walk).  Throwing
                # that output away was the reason a multi-hundred-gigabyte
                # host appeared wholly UNKNOWN.  Preserve parseable output as
                # partial evidence; only an empty/unparseable stream remains
                # unavailable.
                usable_output = bool(result.stdout.strip()) and (
                    observed > 0 or bool(parsed)
                )
                if result.returncode == 0 or usable_output:
                    findings.extend(parsed)
                    status = (
                        "complete" if result.returncode == 0 else "partial"
                    )
                    if status == "partial":
                        reason = (
                            "directory_measurement_timed_out_with_partial"
                            if result.returncode == 124
                            else "directory_measurement_failed_with_partial"
                        )
                        hardlinks = "partial"
                    if filesystem_scope_id not in accounted_filesystems:
                        directory_allocated += observed
                        accounted_filesystems.add(filesystem_scope_id)
                        if status == "complete":
                            attribution_rechecks.append((
                                scan_root, parser, observed, nested_mounts,
                            ))
                    cache_rows = _directory_cache_rows(
                        result.stdout,
                        multiplier=1 if parser is parse_gdu_output else 1024,
                        keep_prefixes=(
                            str(self.sandbox_home),
                            str(self.sandbox_home.parent),
                            str(self.sandbox_home / "runtime"),
                            str(self.sandbox_home / "deploy-src"),
                            str(self.sandbox_home / "sb-src"),
                            *tuple(
                                str(_managed_path(item))
                                for item in managed_roots
                                if _managed_path(item) is not None
                            ),
                        ),
                    )
                    directory_indexes[mount] = {
                        "source": "scan", "complete": status == "complete",
                        "stale": False, "age_seconds": 0, "rows": cache_rows,
                    }
                    self._directory_cache_store(
                        mount, rows=cache_rows, complete=status == "complete",
                        now=cache_now,
                    )
                else:
                    status = self._state(result.returncode)
                    reason = (
                        "directory_measurement_timed_out"
                        if status == "timed_out"
                        else "directory_measurement_unavailable"
                    )
                    hardlinks = "unavailable"
            elif selected:
                status, reason = "timed_out", "overall_budget_exhausted"
            selection_reason = selected_reasons.get(mount, "unrelated")
            filesystems.append(FilesystemObservation(
                filesystem_id=filesystem_id,
                display_name=(
                    "root filesystem"
                    if mount == host_mount
                    else f"filesystem {index + 1}"
                ),
                filesystem_type=str(row.get("filesystem_type") or "unknown")[:40],
                total_bytes=max(int(snapshot.get("total_bytes") or 0), 0),
                used_bytes=min(
                    max(int(snapshot.get("used_bytes") or 0), 0),
                    max(int(snapshot.get("total_bytes") or 0), 0),
                ),
                available_bytes=max(int(snapshot.get("available_bytes") or 0), 0),
                writable=(
                    bool(row["writable"])
                    if row.get("writable") is not None
                    else os.access(mount, os.W_OK)
                ),
                selected=selected,
                selection_reason=selection_reason,
                status=status,
                observed_allocated_bytes=observed,
                hardlink_deduplication=hardlinks,
                limitations=tuple(limitations),
                mount_id=row.get("mount_id"),
                parent_mount_id=row.get("parent_mount_id"),
                capacity_scope_id=capacity_scope_id,
                mount_flags=tuple(row.get("mount_flags") or ()),
            ))
            coverage.append(CoverageObservation(
                category="directory",
                boundary_id=filesystem_id,
                status=status,
                duration_ms=max(int((self.monotonic() - category_started) * 1000), 0),
                confidence="high" if status == "complete" else (
                    "medium"
                    if status in {"partial", "not_selected"} else "low"
                ),
                privilege_sufficient=(
                    elevated
                    or status in {"complete", "partial", "not_selected"}
                ),
                reason=reason if selected else "unrelated_filesystem",
            ))

        directory_statuses = {
            item.status for item in filesystems if item.selected
        }
        capabilities.append(CapabilityObservation(
            category="directory",
            name=scanner_name,
            version=scanner_version,
            fallback=not bool(gdu_path) or preferred_scanner_failed,
            privilege="elevated" if elevated else "unprivileged",
            status=(
                "complete"
                if directory_statuses
                and directory_statuses <= {"complete", "not_selected"}
                and "complete" in directory_statuses
                else "partial" if "partial" in directory_statuses
                else "timed_out" if "timed_out" in directory_statuses
                else "partial"
            ),
            limitations=(
                "allocated_blocks_not_exact_physical_ownership",
                *(("preferred_scanner_failed",) if preferred_scanner_failed else ()),
            ),
        ))

        # The bounded root walk proves capacity ownership, but its depth is
        # intentionally limited.  Measure every known managed root in one
        # multi-path open-source ``du`` call so worktrees, runtime entries,
        # and Docker volume mountpoints can be reconciled to their logical
        # resource records without issuing one probe per record.
        managed_sizes: dict[str, int] = {}
        managed_source = "cache"
        managed_stale = False
        for entry in directory_indexes.values():
            cached_rows = entry.get("rows") or ()
            cached_sizes = _managed_root_sizes(
                self._cached_directory_output(cached_rows),
                multiplier=1,
                managed_roots=managed_roots,
            )
            managed_sizes.update(cached_sizes)
            managed_stale = managed_stale or bool(entry.get("stale"))
        managed_state = "complete" if managed_sizes else "not_measured"
        if managed_roots and cache_mode != "cache_only" and self.monotonic() < deadline:
            paths = tuple(dict.fromkeys(
                str(_managed_path(item))
                for item in managed_roots
                if _managed_path(item) is not None
            ))
            if paths:
                remaining = deadline - self.monotonic()
                result = self._run(
                    (*prefix, "du", "-k", "-s", *paths),
                    deadline,
                    min(60.0, max(1.0, remaining)),
                )
                measured = _managed_root_sizes(
                    result.stdout,
                    multiplier=1024,
                    managed_roots=managed_roots,
                )
                managed_sizes.update(measured)
                managed_source = "scan"
                managed_state = (
                    "complete" if result.returncode == 0
                    else "partial" if measured else self._state(result.returncode)
                )
        if managed_source == "scan" and managed_sizes:
            for item in managed_roots:
                owner_id = item.get("owner_id")
                path = _managed_path(item)
                size = managed_sizes.get(owner_id)
                if not isinstance(owner_id, str) or path is None or size is None:
                    continue
                mount = _mount_for(path, rows)
                if mount is None:
                    normalized_path = os.path.normpath(os.path.realpath(str(path)))
                    matches = [
                        candidate for candidate in directory_indexes
                        if normalized_path == os.path.normpath(os.path.realpath(candidate))
                        or normalized_path.startswith(
                            os.path.normpath(os.path.realpath(candidate)).rstrip(os.sep)
                            + os.sep
                        )
                    ]
                    mount = max(matches, key=len, default=None)
                entry = directory_indexes.get(mount)
                if entry is None or not entry.get("complete"):
                    continue
                cached_rows = [
                    [int(row[0]), str(row[1])]
                    for row in entry.get("rows") or ()
                    if isinstance(row, (list, tuple)) and len(row) == 2
                ]
                normalized = os.path.normpath(str(path))
                cached_rows = [
                    row for row in cached_rows
                    if os.path.normpath(str(row[1])) != normalized
                ]
                cached_rows.append([int(size), str(path)])
                cached_rows.sort(key=lambda row: (int(row[0]), str(row[1])), reverse=True)
                entry["rows"] = cached_rows[:_DIRECTORY_CACHE_MAX_ROWS]
                self._directory_cache_store(
                    mount, rows=entry["rows"], complete=True, now=cache_now,
                )
        for item in managed_roots:
            owner_id = item.get("owner_id")
            if not isinstance(owner_id, str) or not owner_id:
                continue
            size = managed_sizes.get(owner_id)
            managed_root_measurements.append({
                "owner_id": owner_id,
                "kind": str(item.get("kind") or "managed_root"),
                "size_state": "measured" if size is not None else (
                    "timed_out" if managed_state == "timed_out" else
                    "unavailable"
                ),
                "size_bytes": size,
                "source": managed_source,
                "stale": managed_stale,
                "status": managed_state,
            })

        if progress:
            progress("deep_deleted_open")
        deleted_started = self.monotonic()
        deleted_bytes = 0
        lsof_path = self.which("lsof")
        filesystem_by_device: dict[str, str] = {}
        selected_ids = {
            item.filesystem_id for item in filesystems if item.selected
        }
        for row, filesystem in zip(rows, filesystems):
            try:
                device = os.stat(row["mount_point"]).st_dev
            except OSError:
                continue
            values = {
                str(device), hex(device), f"{device:x}",
                f"{os.major(device)},{os.minor(device)}",
                f"{os.major(device):x},{os.minor(device):x}",
            }
            for value in values:
                filesystem_by_device[value] = filesystem.filesystem_id
        if is_cancelled():
            deleted_status, deleted_reason = "cancelled", "request_cancelled"
        elif lsof_path and self.monotonic() < deadline:
            result = self._run((
                *prefix, lsof_path, "-nP", "-FpcfDitsn", "+L1",
            ), deadline, 20)
            if result.returncode in {0, 1}:
                try:
                    deleted, _all_deleted_bytes = parse_lsof_fields(
                        add_proc_allocated_blocks(result.stdout)
                        if self.system() == "Linux" else result.stdout,
                        filesystem_id=None,
                        filesystem_by_device=filesystem_by_device,
                        require_deleted_marker=self.system() == "Darwin",
                    )
                except Exception:
                    deleted_status = "unavailable"
                    deleted_reason = "deleted_open_parse_failed"
                else:
                    findings.extend(deleted)
                    deleted_bytes = sum(
                        item.observed_bytes for item in deleted
                        if item.capacity_accounted
                        and item.filesystem_id in selected_ids
                    )
                    deleted_status = "complete" if elevated else "partial"
                    deleted_reason = (
                        None if elevated else "elevated_visibility_unavailable"
                    )
            else:
                deleted_status = self._state(result.returncode)
                deleted_reason = "deleted_open_measurement_unavailable"
        else:
            deleted_status = (
                "timed_out" if self.monotonic() >= deadline else "unavailable"
            )
            deleted_reason = (
                "overall_budget_exhausted"
                if deleted_status == "timed_out"
                else "lsof_unavailable"
            )
        capabilities.append(CapabilityObservation(
            category="deleted_open",
            name="lsof" if lsof_path else "unavailable",
            version=None,
            fallback=False,
            privilege=(
                "elevated" if elevated else
                "unprivileged" if lsof_path else "unavailable"
            ),
            status=deleted_status,
            limitations=(
                ("darwin_link_count_requires_deleted_marker",)
                if self.system() == "Darwin" else ()
            ),
        ))
        coverage.append(CoverageObservation(
            category="deleted_open",
            boundary_id=None,
            status=deleted_status,
            duration_ms=max(int((self.monotonic() - deleted_started) * 1000), 0),
            confidence="high" if deleted_status == "complete" else "low",
            privilege_sufficient=elevated,
            reason=deleted_reason,
        ))

        if progress:
            progress("deep_docker")
        docker_started = self.monotonic()
        # Engine accounting is a diagnostic phase, not a reason to discard
        # the directory evidence.  Give the open-source Docker CLI a bounded
        # share of a large explicit budget (capped for predictable latency),
        # while retaining the historical 30-second floor for interactive
        # requests.
        docker_max = min(
            max(30.0, float(budget_seconds) * 0.2),
            300.0,
        )
        docker_result = None if is_cancelled() else self._run(
            ("docker", "system", "df", "-v", "--format", "json"),
            deadline, docker_max,
        )
        if docker_result is None:
            logical_bytes = 0
            docker_status, docker_reason = "cancelled", "request_cancelled"
        elif docker_result.returncode == 0:
            docker_findings, logical_bytes = parse_docker_disk_usage(
                docker_result.stdout,
            )
            findings.extend(docker_findings)
            docker_status, docker_reason = "complete", None
        else:
            logical_bytes = 0
            docker_status = self._state(docker_result.returncode)
            docker_reason = "docker_accounting_unavailable"
        capabilities.append(CapabilityObservation(
            category="container_storage",
            name="docker_system_df",
            version=None,
            fallback=False,
            privilege="unprivileged",
            status=docker_status,
            limitations=("logical_engine_accounting",),
        ))
        coverage.append(CoverageObservation(
            category="container_storage",
            boundary_id=None,
            status=docker_status,
            duration_ms=max(int((self.monotonic() - docker_started) * 1000), 0),
            confidence="high" if docker_status == "complete" else "low",
            privilege_sufficient=docker_status == "complete",
            reason=docker_reason,
        ))

        findings.sort(key=lambda item: (
            item.observed_bytes, item.kind, item.finding_id,
        ), reverse=True)
        recheck_before = 0
        recheck_after = 0
        recheck_count = 0
        for scan_root, parser, previous_observed, nested_mounts in attribution_rechecks:
            if is_cancelled() or self.monotonic() >= deadline:
                break
            if parser is parse_gdu_output and gdu_path:
                exclusions = tuple(
                    token
                    for nested in nested_mounts
                    for token in ("--exclude", nested)
                )
                argv = (
                    *prefix, gdu_path, "-n", "-p", "-c", "--no-prefix",
                    "--depth", "0", "-x", "--no-delete", "--no-spawn-shell",
                    "--no-view-file", *exclusions, scan_root,
                )
            else:
                exclusions = tuple(
                    token
                    for nested in nested_mounts
                    for token in (
                        ("-I", nested)
                        if self.system() == "Darwin"
                        else (f"--exclude={nested}",)
                    )
                )
                argv = (
                    *prefix, "du", "-x", "-k", "-s", *exclusions, scan_root,
                )
            result = self._run(argv, deadline, 30)
            if result.returncode != 0:
                continue
            _ignored, current_observed = parser(
                result.stdout,
                filesystem_id="attribution-drift",
                root=scan_root,
                limit=0,
            )
            recheck_before += previous_observed
            recheck_after += current_observed
            recheck_count += 1
        current_used = 0
        measured_current_scopes: set[str] = set()
        for row, filesystem in zip(rows, filesystems):
            scope = filesystem.capacity_scope_id or filesystem.filesystem_id
            if not filesystem.selected or scope in measured_current_scopes:
                continue
            try:
                current_used += int(shutil.disk_usage(row["mount_point"]).used)
                measured_current_scopes.add(scope)
            except OSError:
                continue
        capacity_drift = (
            abs(current_used - selected_used) if measured_current_scopes else 0
        )
        if recheck_count:
            attributed_drift = abs(recheck_after - recheck_before)
        elif has_attributed_baseline:
            attributed_drift = abs(directory_allocated - attributed_baseline)
        else:
            attributed_drift = 0
        reconciliation = reconcile_attribution(
            used_bytes=selected_used,
            directory_allocated_bytes=directory_allocated,
            deleted_open_bytes=deleted_bytes,
            observable_overhead_bytes=0,
            overlapping_logical_bytes=logical_bytes,
            capacity_drift_bytes=capacity_drift,
            attributed_drift_bytes=attributed_drift,
        )
        index_entries = tuple(directory_indexes.values())
        if not index_entries:
            directory_index = {
                "source": "not_measured", "complete": False, "stale": True,
                "age_seconds": None, "row_count": 0,
            }
        else:
            sources = {str(item.get("source")) for item in index_entries}
            source = next(iter(sources)) if len(sources) == 1 else "mixed"
            ages = [
                int(item["age_seconds"])
                for item in index_entries
                if isinstance(item.get("age_seconds"), int)
            ]
            directory_index = {
                "source": source,
                "complete": all(bool(item.get("complete")) for item in index_entries),
                "stale": any(bool(item.get("stale")) for item in index_entries),
                "age_seconds": max(ages) if ages else None,
                "row_count": sum(len(item.get("rows") or ()) for item in index_entries),
            }
        directory_index.update({
            "depth": 4,
            "minimum_row_bytes": _DIRECTORY_CACHE_MIN_BYTES,
            "ttl_seconds": _DIRECTORY_CACHE_TTL,
            "mode": cache_mode,
        })
        incomplete = any(
            item.status not in {"complete", "not_selected"}
            for item in coverage
        )
        return DeepAttribution(
            status="partial" if incomplete else "complete",
            filesystems=tuple(filesystems),
            findings=tuple(findings[:300]),
            capabilities=tuple(capabilities),
            coverage=tuple(coverage),
            reconciliation=reconciliation,
            capacity_scope_id=_identifier(
                "capacity-scope-set",
                *sorted(item.filesystem_id for item in filesystems if item.selected),
            ),
            directory_index=directory_index,
            managed_root_measurements=tuple(managed_root_measurements),
        )
