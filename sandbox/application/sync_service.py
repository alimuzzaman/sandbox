"""Composition boundary for the synchronization service."""

from __future__ import annotations

from sandbox.core._paths import RUNTIME_DIR
from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService
from sandbox.transports.remote_sync import default_remote_sync_transport


def build_sync_service(*, repository=None, transport_factory=None,
                       pin_reconciler=None) -> SyncService:
    """Build one CLI/MCP-equivalent service without touching a runtime stack."""
    sync_repository = repository or SyncRepository(
        RUNTIME_DIR / "sync" / "journal.json")
    if pin_reconciler is None:
        def pin_reconciler():
            from sandbox.jobs.registry import JobRepository
            from sandbox.sync.projection import SyncJobGateway

            jobs = JobRepository(RUNTIME_DIR / "jobs" / "registry.sqlite3")
            try:
                return SyncJobGateway(
                    sync_repository, materialize=lambda *_args: None,
                ).release_terminal_jobs(jobs)
            finally:
                jobs.close()
    return SyncService(
        repository=sync_repository,
        transport_factory=transport_factory or default_remote_sync_transport,
        pin_reconciler=pin_reconciler,
    )


__all__ = ["build_sync_service"]
