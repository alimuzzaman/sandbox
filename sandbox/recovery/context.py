from __future__ import annotations

import os
from pathlib import Path

from .catalog import load_catalog
from .drive import RcloneDrive
from .service import RecoveryService
from .inventory import SandboxRemoteInventory
from sandbox.services.process import BoundedProcessRunner


def recovery_service(root: str | Path) -> RecoveryService:
    root = Path(root)
    destination = os.environ.get("RECOVERY_RCLONE_DESTINATION")
    drive = None
    if destination:
        drive = RcloneDrive(
            BoundedProcessRunner(secret_values=(os.environ.get("RECOVERY_PASSPHRASE", ""),)),
            destination,
        )
    return RecoveryService(
        load_catalog(root / "config" / "recovery-profiles.json"),
        inventory=SandboxRemoteInventory(),
        drive=drive,
    )
