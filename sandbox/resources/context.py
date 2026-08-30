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


def _workspace_projection():
    """Return the typed application-owned workspace projection."""
    try:
        from sandbox.application.context import workspace_ownership_projection
        return workspace_ownership_projection()
    except Exception:
        return {"records": [], "counts": {"total": 0, "incomplete": 1}}


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
            workspace_projection=_workspace_projection,
        )
    return ResourceService(
        adapter,
        PlanStore(Path(RUNTIME_DIR) / "resource-plans"),
    )


def reclaim_service(remote: str | None = None):
    """Build the tiered reclamation service for a local or remote target."""
    import hashlib
    import platform

    from sandbox.core._paths import BASE, RUNTIME_DIR

    from .models import StorageTarget
    from .reclaim_service import (
        ReclaimService, _LocalReclaimProvider, _RemoteReclaimProvider,
    )
    from .remote import LocalProbeAdapter, RemoteResourceAdapter

    store = PlanStore(Path(RUNTIME_DIR) / "resource-plans")
    if remote:
        return ReclaimService(
            _RemoteReclaimProvider(RemoteResourceAdapter(remote)), store,
        )
    seed = f"{platform.node()}:{BASE}"
    target = StorageTarget(
        "local", "local", hashlib.sha256(seed.encode()).hexdigest()[:24],
    )
    return ReclaimService(
        _LocalReclaimProvider(LocalProbeAdapter(), target), store,
        target=target,
    )


def node_store_service(remote: str | None = None):
    """Build the exact-name node-store plan/apply service."""
    from sandbox.core._paths import RUNTIME_DIR
    from .node_store import NodeStoreReclaimService

    service = resource_service(remote)
    return NodeStoreReclaimService(
        service.adapter, Path(RUNTIME_DIR) / "resource-plans",
    )


def _build_host_memory_service(remote: str | None = None):
    """Build the remote-only Feature 046 controller service."""
    if not remote:
        raise ValueError("remote_required")
    from sandbox.core import _remote
    from .host_memory.remote import HostMemoryRemote, RemoteProtocolError
    from .host_memory.service import HostMemoryService

    record = _remote.get_remote(remote)
    if not isinstance(record, dict):
        raise ValueError("unknown_remote")
    service = record.get("mcp_service") or {}
    local_revision = _remote._remote_mcp_runtime_revision()
    if service.get("runtime_revision") != local_revision:
        raise RemoteProtocolError(
            "remote_runtime_revision_mismatch",
            "installed remote runtime does not match this controller",
        )

    def request(selected, payload):
        return _remote.remote_host_memory_request(selected, payload)

    adapter = HostMemoryRemote(remote, record, request)
    return HostMemoryService(adapter)


def host_memory_status(remote: str, *, budget_seconds: float = 15):
    """Return the bounded status envelope without exposing service authority."""
    return _build_host_memory_service(remote).status(budget_seconds)


def host_memory_status_projection(remote: str, *, budget_seconds: int = 15):
    """Return Feature 046's immutable read-only governance projection only."""
    service = _build_host_memory_service(remote)
    result = service.status(budget_seconds)
    if not result.get("ok"):
        error = result.get("error") or {}
        raise ValueError(error.get("code", "response_invalid"))
    return service.projection(result["data"])
