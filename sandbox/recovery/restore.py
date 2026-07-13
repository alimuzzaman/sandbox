from __future__ import annotations

import hashlib
import json

from .errors import RecoveryError
from .models import RestorePlan


def verify_manifest(drive, set_id: str) -> dict:
    try:
        manifest = json.loads(drive.get(f"sets/{set_id}/manifest.json"))
    except (ValueError, RecoveryError) as exc:
        raise RecoveryError("recovery manifest is unavailable or invalid", "invalid_manifest") from exc
    required = {"schema_version", "id", "status", "ciphertext_object", "ciphertext_sha256", "ciphertext_size"}
    if (manifest.get("schema_version") != 1 or manifest.get("id") != set_id or
            manifest.get("status") != "complete" or not required <= set(manifest)):
        raise RecoveryError("recovery set is not restorable", "invalid_manifest")
    ciphertext = drive.get(manifest["ciphertext_object"])
    if (len(ciphertext) != manifest["ciphertext_size"] or
            hashlib.sha256(ciphertext).hexdigest() != manifest["ciphertext_sha256"]):
        raise RecoveryError("recovery ciphertext does not match manifest", "ciphertext_verification_failed")
    return manifest


def build_restore_plan(drive, set_id: str, profiles: tuple[str, ...] = ()) -> RestorePlan:
    manifest = verify_manifest(drive, set_id)
    available = tuple(manifest.get("profiles") or profiles)
    selected = profiles or available
    if set(selected) - set(available):
        raise RecoveryError("restore profile is not in the recovery set", "unknown_profile")
    actions = tuple(f"restore:{profile}" for profile in selected)
    return RestorePlan(set_id, tuple(selected), actions, ("verify-ciphertext",),
                       tuple(f"checkpoint:{profile}" for profile in selected),
                       tuple(f"rollback:{profile}" for profile in reversed(selected)), ())
