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


def _revision_mismatch_message(installed: object, controller: object,
                               remote: str | None = None, state: str | None = None) -> str:
    """Name both runtime revisions (short, hex-only), the verdict and the remedies."""
    import re
    from sandbox.remote_runtime.refusal import (
        migrate_plan_remedy, remote_name_or_none, status_remedy,
    )

    def short(value: object) -> str:
        text = str(value or "")
        return text[:12] if re.fullmatch(r"[0-9a-f]{7,64}", text) else "unknown"
    name = remote_name_or_none(remote)
    verdict = f" (compatibility: {state})" if state else ""
    if name:
        remedy = f"inspect with `{status_remedy(name)}`, then `{migrate_plan_remedy(name)}`"
    else:
        remedy = "inspect with `./sb remote service status <remote>`"
    return (f"installed remote runtime {short(installed)} is not compatible with this "
            f"controller {short(controller)}{verdict}; {remedy}, or use a controller "
            f"checkout at the installed revision")


def _require_compatible_runtime(remote: str, record: dict, *, timeout_seconds=None) -> None:
    """Spec 061 FR-003: admit the registered runtime when it is this exact
    revision, or when the live status verdict says it serves this
    controller's protocol. The live probe runs only on a revision difference.
    """
    from sandbox.core import _remote
    from sandbox.remote_runtime.verdict import UNKNOWN, admitted, strict_requested
    from .host_memory.remote import RemoteProtocolError

    installed = (record.get("mcp_service") or {}).get("runtime_revision")
    controller = _remote._remote_mcp_runtime_revision()
    if installed == controller and not strict_requested():
        return
    try:
        status = (_remote.remote_runtime_status_for_operation(record) if timeout_seconds is None else
                  _remote.remote_runtime_status_for_operation(record, timeout_seconds=timeout_seconds))
    except Exception:  # noqa: BLE001 - remote detail is never forwarded
        status = None
    ok, state = admitted(status)
    if ok:
        return
    raise RemoteProtocolError(
        "remote_runtime_revision_mismatch",
        _revision_mismatch_message(installed, controller, remote,
                                   state if status is not None else UNKNOWN),
    )


def _build_host_memory_service(remote: str | None = None, *, budget_seconds=15):
    """Build the remote-only Feature 046 controller service."""
    if not remote:
        raise ValueError("remote_required")
    from sandbox.core import _remote
    import time
    from .host_memory.remote import HostMemoryRemote, RemoteProtocolError, validate_request
    from .host_memory.service import HostMemoryService

    validate_request({'action': 'host_memory_status', 'remote_name': remote, 'budget_seconds': budget_seconds})
    deadline = time.monotonic() + budget_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RemoteProtocolError('remote_budget_exhausted', 'host-memory observation exhausted its operation budget')
        return value
    record = _remote.get_remote(remote, budget_seconds=remaining())
    if not isinstance(record, dict):
        raise ValueError("unknown_remote")
    _require_compatible_runtime(remote, record, timeout_seconds=remaining())

    def request(selected, payload):
        available = remaining()
        payload = {**payload, 'budget_seconds': min(payload['budget_seconds'], available)}
        return _remote.remote_host_memory_request(selected, payload, timeout_seconds=available)

    adapter = HostMemoryRemote(remote, record, request)
    from pathlib import Path
    from .host_memory.repository import HostMemoryRepository
    repo_root = Path.home() / ".sandbox" / "host-memory"
    try:
        repo_root.mkdir(parents=True, exist_ok=True)
        repo = HostMemoryRepository(repo_root)
    except OSError:
        repo = None
    return HostMemoryService(adapter, repo=repo)


def host_memory_status(remote: str, *, budget_seconds: float = 15):
    """Return the bounded status envelope without exposing service authority."""
    return _build_host_memory_service(remote, budget_seconds=budget_seconds).status(budget_seconds)


def authenticated_target_identity(remote: str, *, budget_seconds: float = 15) -> dict:
    """Read authenticated identity without creating a telemetry repository.

    Partial telemetry is not an identity failure or a resource-policy approval.
    The adapter validates the service marker, revision and entire status schema.
    """
    import re
    import time
    from sandbox.core import _remote
    from .host_memory.remote import HostMemoryRemote, RemoteProtocolError, validate_request

    validate_request({'action': 'host_memory_status', 'remote_name': remote, 'budget_seconds': budget_seconds})
    deadline = time.monotonic() + budget_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise RemoteProtocolError('remote_budget_exhausted', 'authenticated identity observation exhausted its operation budget')
        return value
    record = _remote.get_remote(remote, budget_seconds=remaining())
    if not isinstance(record, dict):
        raise ValueError("recovery_target_identity_unavailable")
    _require_compatible_runtime(remote, record, timeout_seconds=remaining())
    adapter = HostMemoryRemote(remote, record, lambda selected, payload:
        _remote.remote_host_memory_request(selected, payload, timeout_seconds=remaining()))
    try:
        value = adapter.call("host_memory_status", budget_seconds=remaining())
    except RemoteProtocolError as exc:
        from sandbox.remote_runtime.join_adoption import joined
        if exc.code != 'remote_runtime_revision_mismatch' or not joined(record):
            raise
        # This is a fresh read, before any operation binding or effect. Keep
        # previously constructed adapters and persisted plans revision-bound.
        from sandbox.remote_runtime.join_revision import reconnect_join_record
        record = reconnect_join_record(remote, record, budget_seconds=remaining())
        adapter = HostMemoryRemote(remote, record, lambda selected, payload:
            _remote.remote_host_memory_request(selected, payload, timeout_seconds=remaining()))
        value = adapter.call("host_memory_status", budget_seconds=remaining())
    identity = value.get("target_identity")
    if (value.get("evidence_state") not in {"known", "partial", "unmanaged"}
            or not isinstance(identity, str)
            or re.fullmatch(r"[0-9a-f]{24}", identity) is None):
        raise ValueError("recovery_target_identity_unavailable")
    return {"schema_version": 1, "target_identity": identity,
            "evidence_state": value["evidence_state"], "observed_at": value["observed_at"]}


def host_memory_status_projection(remote: str, *, budget_seconds: int = 15):
    """Return Feature 046's immutable read-only governance projection only."""
    service = _build_host_memory_service(remote, budget_seconds=budget_seconds)
    result = service.status(budget_seconds)
    if not result.get("ok"):
        error = result.get("error") or {}
        raise ValueError(error.get("code", "response_invalid"))
    return service.projection(result["data"])


def host_memory_plan(remote: str, *, size_gib: int = 4, budget_seconds: float = 15):
    """Return one deterministic controller-owned enable plan without mutation."""
    return _build_host_memory_service(remote, budget_seconds=budget_seconds).plan(size_gib, budget_seconds)


def host_memory_apply(remote: str, *, plan_id=None, confirm: bool = False, budget_seconds: float = 300):
    """Execute confirmed host-memory apply with normative outcome envelope."""
    return _build_host_memory_service(remote, budget_seconds=budget_seconds).apply(plan_id, confirmed=confirm, budget_seconds=budget_seconds)

def host_memory_history(remote: str, *, since=None, until=None, limit: int = 288, budget_seconds: float = 15):
    """Retrieve bounded aggregate history window for the remote host."""
    return _build_host_memory_service(remote, budget_seconds=budget_seconds).history(
        since=since, until=until, limit=limit, budget_seconds=budget_seconds,
    )


def host_memory_disable(remote: str, *, plan_id=None, confirm: bool = False, budget_seconds: float = 300):
    """Execute confirmed host-memory disable with normative outcome envelope."""
    service = _build_host_memory_service(remote, budget_seconds=budget_seconds)
    if not plan_id:
        plan_res = service.disable_plan(budget_seconds=min(budget_seconds, 15))
        if not plan_res.get("ok"):
            return plan_res
        plan_id = plan_res["data"]["plan_id"]
    return service.apply(plan_id, confirmed=confirm, budget_seconds=budget_seconds)
