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
from typing import Any, Iterable

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

    def __post_init__(self) -> None:
        for name in (
            "used_bytes", "directory_allocated_bytes", "deleted_open_bytes",
            "observable_overhead_bytes", "overlapping_logical_bytes",
            "accounted_bytes", "residual_unexplained_bytes", "overage_bytes",
            "drift_bytes",
        ):
            _non_negative(getattr(self, name), name)
        if self.accounted_bytes > self.used_bytes:
            raise ValueError("accounted bytes cannot exceed used bytes")
        if self.accounted_bytes + self.residual_unexplained_bytes != self.used_bytes:
            raise ValueError("deep reconciliation must equal used bytes")
        if not isinstance(self.drift_material, bool):
            raise ValueError("drift_material must be boolean")

    def to_dict(self) -> dict:
        return {
            name: getattr(self, name)
            for name in (
                "used_bytes", "directory_allocated_bytes",
                "deleted_open_bytes", "observable_overhead_bytes",
                "overlapping_logical_bytes", "accounted_bytes",
                "residual_unexplained_bytes", "overage_bytes", "drift_bytes",
                "drift_material",
            )
        }

    @classmethod
    def from_dict(cls, value: dict) -> "AttributionReconciliation":
        return cls(**{
            name: value.get(name)
            for name in (
                "used_bytes", "directory_allocated_bytes",
                "deleted_open_bytes", "observable_overhead_bytes",
                "overlapping_logical_bytes", "accounted_bytes",
                "residual_unexplained_bytes", "overage_bytes", "drift_bytes",
                "drift_material",
            )
        })


def reconcile_attribution(
    *,
    used_bytes: int,
    directory_allocated_bytes: int,
    deleted_open_bytes: int = 0,
    observable_overhead_bytes: int = 0,
    overlapping_logical_bytes: int = 0,
    drift_bytes: int = 0,
) -> AttributionReconciliation:
    raw = (
        int(directory_allocated_bytes)
        + int(deleted_open_bytes)
        + int(observable_overhead_bytes)
    )
    used = max(int(used_bytes), 0)
    accounted = min(max(raw, 0), used)
    threshold = max(int(used * 0.01), 64 * 1024 * 1024)
    return AttributionReconciliation(
        used_bytes=used,
        directory_allocated_bytes=max(int(directory_allocated_bytes), 0),
        deleted_open_bytes=max(int(deleted_open_bytes), 0),
        observable_overhead_bytes=max(int(observable_overhead_bytes), 0),
        overlapping_logical_bytes=max(int(overlapping_logical_bytes), 0),
        accounted_bytes=accounted,
        residual_unexplained_bytes=used - accounted,
        overage_bytes=max(raw - used, 0),
        drift_bytes=max(int(drift_bytes), 0),
        drift_material=max(int(drift_bytes), 0) > threshold,
    )


@dataclass(frozen=True)
class DeepAttribution:
    status: str
    filesystems: tuple[FilesystemObservation, ...]
    findings: tuple[AttributionFinding, ...]
    capabilities: tuple[CapabilityObservation, ...]
    coverage: tuple[CoverageObservation, ...]
    reconciliation: AttributionReconciliation

    def __post_init__(self) -> None:
        if self.status not in DEEP_STATES:
            raise ValueError("invalid deep attribution status")

    def to_dict(self) -> dict:
        return redact({
            "status": self.status,
            "filesystems": [item.to_dict() for item in self.filesystems],
            "findings": [item.to_dict() for item in self.findings],
            "capabilities": [item.to_dict() for item in self.capabilities],
            "coverage": [item.to_dict() for item in self.coverage],
            "reconciliation": self.reconciliation.to_dict(),
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
        matches = [
            item for item in resource_rows
            if resource_kind
            and item.kind == resource_kind
            and item.display_name == finding.display_name
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
    by_process: dict[tuple[str, str], int] = {}
    for proc, record in records:
        if record.get("t") not in {"REG", "VREG"}:
            continue
        if require_deleted_marker and "(deleted)" not in record.get("n", ""):
            continue
        try:
            size = int(record.get("s", ""))
        except ValueError:
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
        by_process[(pid, safe_command)] = by_process.get((pid, safe_command), 0) + size

    findings = tuple(
        AttributionFinding(
            finding_id=_identifier("deleted-open", filesystem_id or "", pid),
            kind="deleted_open",
            display_name=f"process {pid}",
            filesystem_id=filesystem_id,
            owner_kind="process",
            owner_id=pid,
            observed_bytes=size,
            capacity_accounted=True,
            overlap="none",
            activity="active",
            guidance="manual",
            evidence=("zero_link_count", "regular_file", f"command:{command}"),
            limitations=(),
        )
        for (pid, command), size in sorted(
            by_process.items(), key=lambda item: (item[1], item[0]), reverse=True,
        )
    )
    return findings, sum(item.observed_bytes for item in findings)


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
    ) -> None:
        measured = parse_byte_size(size)
        if measured is None:
            return
        rows.append(AttributionFinding(
            finding_id=_identifier(kind, identity),
            kind=kind,
            display_name=display[:120] or kind.replace("_", " "),
            filesystem_id=None,
            owner_kind="container_engine",
            owner_id=None,
            observed_bytes=measured,
            capacity_accounted=False,
            overlap=overlap,
            activity=activity,
            guidance=guidance,
            evidence=evidence,
            limitations=("logical_engine_accounting",),
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
        add(
            "container_image", identity, display,
            row.get("UniqueSize", row.get("Size")),
            overlap="shared_layers",
            activity="active" if str(row.get("Containers") or "0") != "0" else "inactive",
            guidance="monitoring_only",
            evidence=("docker_system_df", "unique_size", "shared_size_reported"),
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
        timeout = max(min(deadline - self.monotonic(), maximum), 0.01)
        return self.runner.run(tuple(str(item) for item in argv), timeout=timeout)

    @staticmethod
    def _state(returncode: int) -> str:
        return "timed_out" if returncode == 124 else "unavailable"

    def _inventory(self, deadline: float, capacity: dict) -> tuple[list[dict], str]:
        result = self._run(("df", "-Pk"), deadline, 5)
        rows = parse_df_output(result.stdout) if result.returncode == 0 else []
        if rows:
            return rows, "complete"
        mount = str(self.host_root)
        return [{
            "source": "target",
            "mount_point": mount,
            "total_bytes": int(capacity.get("total_bytes") or 0),
            "used_bytes": int(capacity.get("used_bytes") or 0),
            "available_bytes": int(capacity.get("available_bytes") or 0),
        }], self._state(result.returncode)

    def collect(
        self,
        *,
        capacity: dict,
        budget_seconds: float,
        progress=None,
    ) -> DeepAttribution:
        started = self.monotonic()
        deadline = started + max(float(budget_seconds), 0.1)
        if progress:
            progress("deep_mounts")
        rows, mount_state = self._inventory(deadline, capacity)
        host_mount = _mount_for(self.host_root, rows) or rows[0]["mount_point"]
        sandbox_mount = _mount_for(self.sandbox_home, rows)

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
        docker_mount = _mount_for(docker_root, rows) if docker_root else None

        selected_reasons = {host_mount: "root"}
        if sandbox_mount and sandbox_mount not in selected_reasons:
            selected_reasons[sandbox_mount] = "sandbox_home"
        if docker_mount and docker_mount not in selected_reasons:
            selected_reasons[docker_mount] = "container_data"

        gdu_path = self.which("gdu")
        scanner_name = "gdu" if gdu_path else "du"
        scanner_version = None
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
            reason=None if mount_state == "complete" else "mount_inventory_unavailable",
        )]
        findings: list[AttributionFinding] = []
        filesystems = []
        root_allocated = 0

        for index, row in enumerate(rows):
            mount = row["mount_point"]
            selected = mount in selected_reasons
            filesystem_id = _identifier(
                "filesystem", str(row.get("source") or ""), mount,
            )
            status = "not_selected"
            observed = None
            hardlinks = "unavailable"
            limitations = ["filesystem_capabilities_unverified"]
            category_started = self.monotonic()
            reason = None
            if selected and self.monotonic() < deadline:
                if progress:
                    progress("deep_directory")
                scan_root = (
                    str(self.host_root)
                    if mount == host_mount and self.host_root != Path("/")
                    else mount
                )
                if gdu_path:
                    argv = (
                        *prefix, gdu_path, "-n", "-p", "-c", "--no-prefix",
                        "--depth", "4", "-x", "--no-delete", "--no-spawn-shell",
                        "--no-view-file", scan_root,
                    )
                    result = self._run(argv, deadline, 120)
                    parser = parse_gdu_output
                    hardlinks = "confirmed"
                else:
                    argv = (
                        *prefix, "du", "-x", "-k", "-d", "4", scan_root,
                    )
                    result = self._run(argv, deadline, 120)
                    parser = parse_du_output
                    hardlinks = "confirmed"
                if result.returncode == 0 or (
                    result.returncode == 124 and result.stdout.strip()
                ):
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
                    findings.extend(parsed)
                    status = (
                        "complete" if result.returncode == 0 else "partial"
                    )
                    if status == "partial":
                        reason = "directory_measurement_timed_out_with_partial"
                        hardlinks = "partial"
                    if mount == host_mount:
                        root_allocated = observed
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
                filesystem_type="unknown",
                total_bytes=max(int(row.get("total_bytes") or 0), 0),
                used_bytes=min(
                    max(int(row.get("used_bytes") or 0), 0),
                    max(int(row.get("total_bytes") or 0), 0),
                ),
                available_bytes=max(int(row.get("available_bytes") or 0), 0),
                writable=os.access(mount, os.W_OK),
                selected=selected,
                selection_reason=selection_reason,
                status=status,
                observed_allocated_bytes=observed,
                hardlink_deduplication=hardlinks,
                limitations=tuple(limitations),
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
            fallback=not bool(gdu_path),
            privilege="elevated" if elevated else "unprivileged",
            status=(
                "complete"
                if directory_statuses and directory_statuses == {"complete"}
                else "partial" if "partial" in directory_statuses
                else "timed_out" if "timed_out" in directory_statuses
                else "partial"
            ),
            limitations=("allocated_blocks_not_exact_physical_ownership",),
        ))

        if progress:
            progress("deep_deleted_open")
        deleted_started = self.monotonic()
        deleted_bytes = 0
        lsof_path = self.which("lsof")
        if lsof_path and self.monotonic() < deadline:
            result = self._run((
                *prefix, lsof_path, "-nP", "-FpcfDitsn", "+L1",
            ), deadline, 20)
            if result.returncode in {0, 1}:
                deleted, deleted_bytes = parse_lsof_fields(
                    result.stdout,
                    filesystem_id=next((
                        item.filesystem_id for item in filesystems
                        if item.selection_reason == "root"
                    ), None),
                    require_deleted_marker=self.system() == "Darwin",
                )
                findings.extend(deleted)
                deleted_status = "complete"
                deleted_reason = None
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
            privilege_sufficient=elevated or deleted_status == "complete",
            reason=deleted_reason,
        ))

        if progress:
            progress("deep_docker")
        docker_started = self.monotonic()
        docker_result = self._run(
            ("docker", "system", "df", "-v", "--format", "json"),
            deadline,
            30,
        )
        if docker_result.returncode == 0:
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
        try:
            current = shutil.disk_usage(self.host_root)
            drift = abs(int(current.used) - int(capacity.get("used_bytes") or 0))
        except OSError:
            drift = 0
        reconciliation = reconcile_attribution(
            used_bytes=int(capacity.get("used_bytes") or 0),
            directory_allocated_bytes=root_allocated,
            deleted_open_bytes=deleted_bytes,
            observable_overhead_bytes=0,
            overlapping_logical_bytes=logical_bytes,
            drift_bytes=drift,
        )
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
        )
