from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .errors import RecoveryError
from .models import RestorePlan


_COMPATIBILITY = "sandbox-recovery-v1"


def _canonical_ciphertext_keys(set_id: str) -> tuple[str, ...]:
    return (f"sets/{set_id}/archive.bin", f"sets/{set_id}/archive.tar.gpg")


def verify_manifest(drive, set_id: str) -> dict:
    try:
        manifest = json.loads(drive.get(f"sets/{set_id}/manifest.json"))
    except (ValueError, RecoveryError) as exc:
        raise RecoveryError("recovery manifest is unavailable or invalid", "invalid_manifest") from exc
    required = {"schema_version", "id", "status", "ciphertext_object", "ciphertext_sha256", "ciphertext_size"}
    if (manifest.get("schema_version") != 1 or manifest.get("id") != set_id or
            manifest.get("status") != "complete" or not required <= set(manifest)):
        raise RecoveryError("recovery set is not restorable", "invalid_manifest")
    compatibility = manifest.get("restore_compatibility")
    if compatibility not in (None, _COMPATIBILITY):
        raise RecoveryError("recovery set requires an incompatible restore tool", "incompatible_restore")
    if manifest["ciphertext_object"] not in _canonical_ciphertext_keys(set_id):
        raise RecoveryError("recovery ciphertext is not bound to its manifest", "invalid_manifest")
    ciphertext = drive.get(manifest["ciphertext_object"])
    if (len(ciphertext) != manifest["ciphertext_size"] or
            hashlib.sha256(ciphertext).hexdigest() != manifest["ciphertext_sha256"]):
        raise RecoveryError("recovery ciphertext does not match manifest", "ciphertext_verification_failed")
    return manifest


def _ordered_profiles(selected: tuple[str, ...], dependencies: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    ordered, visiting, visited = [], set(), set()
    def visit(profile: str) -> None:
        if profile in visiting:
            raise RecoveryError("restore profile dependency cycle", "invalid_restore_dependencies")
        if profile in visited:
            return
        visiting.add(profile)
        for dependency in dependencies.get(profile, ()):
            if dependency not in selected:
                raise RecoveryError("selected restore omits a required dependency", "missing_restore_dependency")
            visit(dependency)
        visiting.remove(profile); visited.add(profile); ordered.append(profile)
    for profile in selected:
        visit(profile)
    return tuple(ordered)


def build_restore_plan(drive, set_id: str, profiles: tuple[str, ...] = (), *,
                       dependencies: Mapping[str, tuple[str, ...]] | None = None,
                       target_root: str | Path | None = None, required_bytes: int = 0) -> RestorePlan:
    manifest = verify_manifest(drive, set_id)
    available = tuple(manifest.get("profiles") or profiles)
    selected = profiles or available
    if set(selected) - set(available):
        raise RecoveryError("restore profile is not in the recovery set", "unknown_profile")
    ordered = _ordered_profiles(tuple(selected), dependencies or {})
    if target_root is not None and shutil.disk_usage(target_root).free < required_bytes:
        raise RecoveryError("restore target has insufficient free space", "insufficient_free_space")
    actions = tuple(f"restore:{profile}" for profile in ordered)
    return RestorePlan(set_id, ordered, actions, ("verify-ciphertext",),
                       tuple(f"checkpoint:{profile}" for profile in ordered),
                       tuple(f"rollback:{profile}" for profile in reversed(ordered)), ())


def apply_restore(plan: RestorePlan, adapters: Mapping[str, object], *, confirm: bool = False) -> dict:
    """Apply a fixture/disposable restore with ordered checkpoints and rollback.

    Production callers must supply dedicated per-profile adapters.  This generic
    coordinator never guesses a target and refuses to mutate until confirmation.
    """
    if not confirm:
        raise RecoveryError("restore apply requires explicit confirmation", "confirmation_required")
    touched: list[str] = []
    events: list[str] = []
    try:
        for profile in plan.profiles:
            adapter = adapters.get(profile)
            if adapter is None:
                raise RecoveryError("restore adapter is unavailable", "missing_restore_adapter")
            touched.append(profile)
            for operation in ("checkpoint", "quiesce", "stage", "swap", "import", "verify", "resume"):
                getattr(adapter, operation)()
                events.append(f"{operation}:{profile}")
    except Exception as exc:
        for profile in reversed(touched):
            adapter = adapters[profile]
            try:
                adapter.rollback(); events.append(f"rollback:{profile}")
                adapter.resume(); events.append(f"resume:{profile}")
            except Exception:
                events.append(f"manual-intervention:{profile}")
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("restore apply failed and rollback was attempted", "restore_apply_failed") from exc
    return {"status": "complete", "events": tuple(events)}
