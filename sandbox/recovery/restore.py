from __future__ import annotations

import json

from .errors import RecoveryError
from .models import RestorePlan


def build_restore_plan(drive, set_id: str, profiles: tuple[str, ...] = ()) -> RestorePlan:
    try:
        manifest = json.loads(drive.get(f"sets/{set_id}/manifest.json"))
    except (ValueError, RecoveryError) as exc:
        raise RecoveryError("recovery manifest is unavailable or invalid", "invalid_manifest") from exc
    if manifest.get("schema_version") != 1 or manifest.get("status") != "complete":
        raise RecoveryError("recovery set is not restorable", "invalid_manifest")
    available = tuple(manifest.get("profiles") or profiles)
    selected = profiles or available
    if set(selected) - set(available):
        raise RecoveryError("restore profile is not in the recovery set", "unknown_profile")
    actions = tuple(f"restore:{profile}" for profile in selected)
    return RestorePlan(set_id, tuple(selected), actions, ("verify-ciphertext",),
                       tuple(f"checkpoint:{profile}" for profile in selected),
                       tuple(f"rollback:{profile}" for profile in reversed(selected)), ())
