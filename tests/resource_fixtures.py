from __future__ import annotations

from datetime import datetime, timezone

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
