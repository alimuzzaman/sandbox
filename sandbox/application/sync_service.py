"""Composition boundary for the synchronization service."""

from __future__ import annotations

from sandbox.core._paths import RUNTIME_DIR
from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService
from sandbox.transports.remote_sync import default_remote_sync_transport


def build_sync_service(*, repository=None, transport_factory=None) -> SyncService:
    """Build one CLI/MCP-equivalent service without touching a runtime stack."""
    return SyncService(
        repository=repository or SyncRepository(RUNTIME_DIR / "sync" / "journal.json"),
        transport_factory=transport_factory or default_remote_sync_transport,
    )


__all__ = ["build_sync_service"]
