from __future__ import annotations

import os
from pathlib import Path

from .catalog import load_catalog
from .capture import StagingCaptureCoordinator
from .crypto import GpgCrypto
from .drive import RcloneDrive
from .service import RecoveryService
from .inventory import SandboxRemoteInventory
from sandbox.services.process import BoundedProcessRunner


def recovery_service(root: str | Path) -> RecoveryService:
    root = Path(root)
    destination = os.environ.get("RECOVERY_RCLONE_DESTINATION")
    passphrase = os.environ.get("RECOVERY_PASSPHRASE")
    drive = None
    capture = None
    if destination:
        drive = RcloneDrive(
            BoundedProcessRunner(secret_values=(passphrase or "",)),
            destination,
        )
        if passphrase:
            capture = StagingCaptureCoordinator(
                GpgCrypto(passphrase),
                drive,
                staging_root=os.environ.get("RECOVERY_STAGING_ROOT") or None,
                pending_root=os.environ.get("RECOVERY_PENDING_ROOT") or None,
            )
    return RecoveryService(
        load_catalog(root / "config" / "recovery-profiles.json"),
        inventory=SandboxRemoteInventory(),
        drive=drive,
        capture=capture,
        pending_root=os.environ.get("RECOVERY_PENDING_ROOT") or None,
    )
