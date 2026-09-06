"""Composition boundary for the synchronization service."""

from __future__ import annotations

from typing import Any, Callable, Optional

from sandbox.core._paths import RUNTIME_DIR
from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService
from sandbox.transports.remote_sync import default_remote_sync_transport


class PolicyAwareSyncTransport:
    """Routes sync transfer to owned storage authority when future policy is active."""

    def __init__(
        self,
        fallback_transport: Any,
        *,
        storage_repository: Optional[Any] = None,
        owned_storage_transport: Optional[Any] = None,
    ):
        self.fallback_transport = fallback_transport
        self.storage_repository = storage_repository
        self.owned_storage_transport = owned_storage_transport

    def _get_policy(self, remote_identity: str, project_identity: str) -> Optional[Any]:
        if self.storage_repository is not None:
            return self.storage_repository.get_policy(remote_identity, project_identity)
        db_path = RUNTIME_DIR / "storage_authority" / "authority.db"
        if db_path.exists():
            from sandbox.owned_storage.repository import StorageAuthorityRepository

            repo = StorageAuthorityRepository(db_path)
            return repo.get_policy(remote_identity, project_identity)
        return None

    def transfer(self, project_dir: Any, manifest: Any, relationship: Any, generation: Any) -> Any:
        policy = self._get_policy(relationship.remote_name, relationship.project_identity)
        mode = getattr(policy, "mode", None)
        mode_val = mode.value if hasattr(mode, "value") else str(mode)
        if mode_val == "future":
            from sandbox.transports.remote_owned_storage import RemoteOwnedStorageTransport

            transport = self.owned_storage_transport or RemoteOwnedStorageTransport()
            return transport.transfer(project_dir, manifest, relationship, generation)
        return self.fallback_transport.transfer(project_dir, manifest, relationship, generation)

    def reconcile(self, relationship: Any, generation: Any) -> Any:
        policy = self._get_policy(relationship.remote_name, relationship.project_identity)
        mode = getattr(policy, "mode", None)
        mode_val = mode.value if hasattr(mode, "value") else str(mode)
        if mode_val == "future":
            from sandbox.transports.remote_owned_storage import RemoteOwnedStorageTransport

            transport = self.owned_storage_transport or RemoteOwnedStorageTransport()
            return transport.reconcile(relationship, generation)
        if hasattr(self.fallback_transport, "reconcile"):
            return self.fallback_transport.reconcile(relationship, generation)
        return {"status": "unknown"}


def build_sync_service(
    *,
    repository=None,
    transport_factory: Optional[Callable[[], Any]] = None,
    pin_reconciler: Optional[Callable[[], Any]] = None,
    storage_repository: Optional[Any] = None,
) -> SyncService:
    """Build one CLI/MCP-equivalent service without touching a runtime stack."""
    sync_repository = repository or SyncRepository(RUNTIME_DIR / "sync" / "journal.json")
    if pin_reconciler is None:

        def pin_reconciler():
            from sandbox.jobs.registry import JobRepository
            from sandbox.sync.projection import SyncJobGateway

            jobs = JobRepository(RUNTIME_DIR / "jobs" / "registry.sqlite3")
            try:
                return SyncJobGateway(
                    sync_repository,
                    materialize=lambda *_args: None,
                ).release_terminal_jobs(jobs)
            finally:
                jobs.close()

    base_factory = transport_factory or default_remote_sync_transport

    def policy_routing_factory():
        transport = base_factory()
        return PolicyAwareSyncTransport(
            transport,
            storage_repository=storage_repository,
        )

    return SyncService(
        repository=sync_repository,
        transport_factory=policy_routing_factory,
        pin_reconciler=pin_reconciler,
    )


__all__ = ["build_sync_service", "PolicyAwareSyncTransport"]
