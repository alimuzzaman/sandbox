from __future__ import annotations

from pathlib import Path

from .catalog import load_catalog
from .service import RecoveryService
from .inventory import SandboxRemoteInventory


def recovery_service(root: str | Path) -> RecoveryService:
    root = Path(root)
    return RecoveryService(
        load_catalog(root / "config" / "recovery-profiles.json"),
        inventory=SandboxRemoteInventory(),
    )
