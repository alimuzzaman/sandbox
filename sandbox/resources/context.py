from __future__ import annotations

from pathlib import Path

from .adapters import LocalResourceAdapter
from .plans import PlanStore
from .service import ResourceService


def _registry_records():
    """Read registry records through the typed repository contract."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.project_registry import JsonRegistryRepository

    return JsonRegistryRepository(RUNTIME_DIR / "registry.json").read_only_all()


def _job_resource_records():
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import read_resource_index

    return read_resource_index(RUNTIME_DIR / "jobs" / "registry.sqlite3")


def resource_service(remote: str | None = None) -> ResourceService:
    from sandbox.core._paths import BASE, RUNTIME_DIR

    if remote:
        from .remote import RemoteResourceAdapter
        adapter = RemoteResourceAdapter(remote)
    else:
        adapter = LocalResourceAdapter(
            BASE,
            registry_records=_registry_records,
            job_resource_records=_job_resource_records,
        )
    return ResourceService(
        adapter,
        PlanStore(Path(RUNTIME_DIR) / "resource-plans"),
    )
