from __future__ import annotations

from datetime import datetime, timezone

from sandbox.resources.attribution import (
    CapabilityObservation,
    CoverageObservation,
    DeepAttribution,
    FilesystemObservation,
    reconcile_attribution,
)
from sandbox.resources.models import ResourceObservation, StorageTarget


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def target(name: str = "local", identity: str = "host-fixture") -> StorageTarget:
    return StorageTarget("local" if name == "local" else "remote", name, identity)


def observation(
    resource_id: str = "resource-1",
    *,
    kind: str = "download_cache",
    classification: str = "disposable_cache",
    size_bytes: int | None = 1024,
    owner_kind: str = "sandbox",
    owner_id: str | None = "sandbox",
    locator: str = "/managed/cache/resource-1",
    references: tuple[str, ...] = (),
    evidence: tuple[str, ...] = ("managed_root", "unused"),
    capacity_accounted: bool = True,
) -> ResourceObservation:
    return ResourceObservation(
        resource_id=resource_id,
        kind=kind,
        locator=locator,
        display_name=resource_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        classification=classification,
        size_state="measured" if size_bytes is not None else "unavailable",
        size_bytes=size_bytes,
        reclaimable_bytes=size_bytes or 0 if classification in {
            "disposable_cache", "stale_candidate",
        } else 0,
        capacity_accounted=capacity_accounted,
        references=references,
        evidence=evidence,
    )


def deep_attribution(
    *,
    used_bytes: int = 80,
    directory_allocated_bytes: int = 70,
    deleted_open_bytes: int = 10,
) -> DeepAttribution:
    return DeepAttribution(
        status="complete",
        filesystems=(FilesystemObservation(
            filesystem_id="filesystem-root",
            display_name="root filesystem",
            filesystem_type="unknown",
            total_bytes=100,
            used_bytes=used_bytes,
            available_bytes=max(100 - used_bytes, 0),
            writable=True,
            selected=True,
            selection_reason="root",
            status="complete",
            observed_allocated_bytes=directory_allocated_bytes,
            hardlink_deduplication="confirmed",
        ),),
        findings=(),
        capabilities=(CapabilityObservation(
            category="directory",
            name="du",
            version=None,
            fallback=True,
            privilege="unprivileged",
            status="complete",
        ),),
        coverage=(CoverageObservation(
            category="directory",
            boundary_id="filesystem-root",
            status="complete",
            duration_ms=1,
            confidence="high",
            privilege_sufficient=True,
        ),),
        reconciliation=reconcile_attribution(
            used_bytes=used_bytes,
            directory_allocated_bytes=directory_allocated_bytes,
            deleted_open_bytes=deleted_open_bytes,
        ),
    )
