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
    # Recovery plaintext is never staged in the checkout.  Keep all transient
    # material under the Sandbox-owned machine state directory instead.
    state_root = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox")) / "recovery"
    staging_root = state_root / "staging"
    pending_root = state_root / "pending"
    materialization_root = state_root / "materialized"
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
                staging_root=staging_root,
                pending_root=pending_root,
                materialization_root=materialization_root,
            )
    return RecoveryService(
        load_catalog(root / "config" / "recovery-profiles.json"),
        inventory=SandboxRemoteInventory(),
        drive=drive,
        capture=capture,
        pending_root=pending_root,
    )
