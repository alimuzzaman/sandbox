from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from typing import Mapping

from .catalog import RecoveryCatalog
from .errors import RecoveryError, result
from .planner import build_plan


def _set_id(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "sets":
        return None
    candidate = parts[1]
    if not candidate or candidate != Path(candidate).name or not candidate.replace("-", "").replace("_", "").isalnum():
        return None
    return candidate


def _listing_path(item: object) -> str:
    if not isinstance(item, dict) or not isinstance(item.get("Path"), str):
        raise RecoveryError("recovery listing contains an invalid path", "invalid_drive_listing")
    path = item["Path"]
    if (not path or path.startswith("/") or ".." in Path(path).parts or
            any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise RecoveryError("recovery listing contains an invalid path", "invalid_drive_listing")
    return path


def _retention_timestamp_valid(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


class RecoveryService:
    def __init__(self, catalog: RecoveryCatalog, *, inventory=None, drive=None, capture=None,
                 pending_root: str | Path | None = None) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self.drive = drive
        self.capture = capture
        self.pending_root = Path(pending_root) if pending_root else None

    def profiles(self, remote: str | None = None) -> dict:
        return result(True, "profiles", remote=remote, status="ready", data={
            "schema_version": self.catalog.schema_version,
            "profiles": [profile.profile_id for profile in self.catalog.profiles],
        })

    def plan(self, selected: tuple[str, ...] = (), remote: str | None = None) -> dict:
        try:
            plan = build_plan(self.catalog, selected)
        except RecoveryError as exc:
            return result(False, "plan", remote=remote, error=exc)
        data = plan.as_dict()
        if remote and self.inventory is not None:
            try:
                data["remote_inventory"] = self.inventory.discover(remote)
            except RecoveryError as exc:
                return result(False, "plan", remote=remote, error=exc)
            except (OSError, TypeError, ValueError) as exc:
                return result(False, "plan", remote=remote, error=RecoveryError(
                    "remote inventory returned invalid data", "inventory_failed"))
        return result(True, "plan", remote=remote, status="planned", data=data)

    def create(self, set_id: str, artifacts: dict, profiles: tuple[str, ...], *,
               confirm: bool = False, remote: str | None = None, provenance: dict | None = None) -> dict:
        if not confirm:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery create requires explicit confirmation", "confirmation_required"))
        if not profiles:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery create requires selected profiles", "missing_profiles"))
        try:
            plan = build_plan(self.catalog, profiles)
        except RecoveryError as exc:
            return result(False, "create", remote=remote, error=exc)
        if plan.warnings:
            return result(False, "create", remote=remote,
                          data={"profiles": plan.profiles, "warnings": plan.warnings},
                          error=RecoveryError(
                              "recovery capture inputs require explicit materialization",
                              "capture_not_ready"))
        if not artifacts:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery create requires captured artifacts", "empty_set"))
        if self.capture is None:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery capture is not configured", "recovery_not_configured"))
        try:
            manifest = self.capture.publish_files(set_id, artifacts, profiles=profiles, provenance=provenance)
        except RecoveryError as exc:
            return result(False, "create", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery capture failed", "capture_failed"))
        return result(True, "create", remote=remote, status="complete", data={"manifest": manifest})

    def list(self, remote: str | None = None) -> dict:
        if self.drive is None:
            return result(False, "list", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            objects = self.drive.list("")
            groups: dict[str, list[dict]] = {}
            legacy: list[dict] = []
            for item in objects:
                path = _listing_path(item)
                set_id = _set_id(path)
                if set_id is None:
                    legacy.append(item)
                else:
                    groups.setdefault(set_id, []).append(item)

            complete: list[dict] = []
            incomplete: list[dict] = []
            unverifiable: list[dict] = []
            for set_id, group in sorted(groups.items()):
                manifests = [item for item in group if _listing_path(item).endswith("/manifest.json")]
                if not manifests:
                    incomplete.extend(group)
                    continue
                if len(manifests) != 1:
                    unverifiable.extend(group)
                    continue
                manifest_item = manifests[0]
                try:
                    from .restore import verify_manifest
                    verify_manifest(self.drive, set_id)
                except (KeyError, TypeError, ValueError, RecoveryError):
                    unverifiable.extend(group)
                else:
                    complete.append(manifest_item)
            local_pending: list[dict] = []
            if self.pending_root and self.pending_root.is_dir():
                for path in sorted(self.pending_root.glob("*.archive.tar.gpg")):
                    if path.is_file():
                        local_pending.append({"Path": str(path), "Size": path.stat().st_size})
        except RecoveryError as exc:
            return result(False, "list", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "list", remote=remote, error=RecoveryError(
                "recovery listing is invalid", "list_failed"))
        return result(True, "list", remote=remote, status="listed", data={
            "complete_manifests": tuple(complete),
            "incomplete": tuple(incomplete),
            "legacy": tuple(legacy),
            "locally_pending": tuple(local_pending),
            "unverifiable": tuple(unverifiable),
            "pending": tuple(incomplete),
        })

    def verify(self, set_id: str, remote: str | None = None) -> dict:
        if self.drive is None:
            return result(False, "verify", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            from .restore import verify_manifest
            manifest = verify_manifest(self.drive, set_id)
        except RecoveryError as exc:
            return result(False, "verify", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "verify", remote=remote, error=RecoveryError(
                "recovery verification failed", "verify_failed"))
        return result(True, "verify", remote=remote, status="verified", data={"id": set_id, "manifest": manifest})

    def _current_passphrase_verifies(self, ciphertext_key: str) -> bool:
        """Check decryption with the configured capture crypto without persisting plaintext."""
        if self.drive is None or self.capture is None:
            return False
        crypto = getattr(self.capture, "crypto", None)
        if crypto is None:
            return False
        try:
            decrypt_file = getattr(crypto, "decrypt_file", None)
            with tempfile.TemporaryDirectory(prefix="sandbox-recovery-retention-") as directory:
                source = Path(directory) / "archive.gpg"
                plaintext = Path(directory) / "archive"
                if callable(decrypt_file):
                    get_file = getattr(self.drive, "get_file", None)
                    if callable(get_file):
                        get_file(ciphertext_key, source)
                    else:
                        source.write_bytes(self.drive.get(ciphertext_key))
                    decrypt_file(source, plaintext)
                    return plaintext.is_file()
            decrypt = getattr(crypto, "decrypt", None)
            if callable(decrypt):
                decrypt(self.drive.get(ciphertext_key))
                return True
            return False
        except (OSError, RecoveryError, TypeError, ValueError):
            return False

    def retention_plan(self, remote: str | None = None, *, keep_count: int = 1,
                       minimum_age_days: int = 0, now: datetime | None = None) -> dict:
        """Build a verified, non-destructive retention plan from complete remote sets."""
        if self.drive is None:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        if (not isinstance(minimum_age_days, int) or isinstance(minimum_age_days, bool)
                or minimum_age_days < 0):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "retention minimum age is invalid", "invalid_retention_policy"))
        try:
            from .restore import verify_manifest
            from .retention import build_retention_plan

            objects = self.drive.list("")
            manifest_ids = sorted({set_id for item in objects
                                   if (path := _listing_path(item))
                                   and (set_id := _set_id(path))
                                   and path.endswith("/manifest.json")})
            observed = []
            unclassified = []
            for set_id in manifest_ids:
                try:
                    manifest = verify_manifest(self.drive, set_id)
                    current = self._current_passphrase_verifies(manifest["ciphertext_object"])
                    reasons = []
                    if not current:
                        reasons.append("passphrase_not_current")
                    if not _retention_timestamp_valid(manifest.get("created_at")):
                        reasons.append("invalid_created_at")
                    observed.append({
                        "id": set_id, "prefix": "sets/", "status": "complete",
                        "verified": True, "passphrase_current": current,
                        "created_at": manifest.get("created_at"),
                    })
                    if reasons:
                        unclassified.append({"id": set_id, "reason": ",".join(reasons)})
                except RecoveryError as exc:
                    unclassified.append({"id": set_id, "reason": exc.code})
            plan = build_retention_plan(
                "sets/", tuple(observed), keep_count=keep_count,
                minimum_age=timedelta(days=minimum_age_days), now=now,
            )
        except RecoveryError as exc:
            return result(False, "retention", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery retention inventory is invalid", "retention_failed"))
        return result(True, "retention", remote=remote, status="planned", data={
            "destination_prefix": plan.destination_prefix,
            "protected_sets": plan.protected_sets,
            "candidates": plan.candidates,
            "unclassified": tuple(unclassified),
            "requires_confirmation": plan.requires_confirmation,
        })

    def restore_plan(self, set_id: str, profiles: tuple[str, ...] = (), *, remote: str | None = None) -> dict:
        if self.drive is None:
            return result(False, "restore", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            from .restore import build_restore_plan
            dependencies = {profile.profile_id: profile.dependencies for profile in self.catalog.profiles}
            plan = build_restore_plan(self.drive, set_id, profiles, dependencies=dependencies)
        except RecoveryError as exc:
            return result(False, "restore", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "restore", remote=remote, error=RecoveryError(
                "recovery restore plan is invalid", "restore_failed"))
        return result(True, "restore", remote=remote, status="planned", data={
            "set_id": plan.set_id, "profiles": plan.profiles, "actions": plan.actions,
            "checkpoints": plan.checkpoints, "rollback": plan.rollback,
            "requires_confirmation": plan.requires_confirmation,
        })

    def restore_apply(self, plan, adapters: Mapping[str, object], *, confirm: bool = False,
                      remote: str | None = None) -> dict:
        """Apply an explicit in-process restore plan; targets remain adapter-owned."""
        from .models import RestorePlan
        if not isinstance(plan, RestorePlan):
            return result(False, "restore", remote=remote, error=RecoveryError(
                "restore plan is invalid", "invalid_restore_plan"))
        if self.drive is None:
            return result(False, "restore", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            from .restore import apply_restore, verify_manifest
            verify_manifest(self.drive, plan.set_id)
            outcome = apply_restore(plan, adapters, confirm=confirm)
        except RecoveryError as exc:
            return result(False, "restore", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "restore", remote=remote, error=RecoveryError(
                "recovery restore apply failed", "restore_failed"))
        return result(True, "restore", remote=remote, status=outcome["status"], data={
            "set_id": plan.set_id, "events": outcome["events"],
        })

    def retention_apply(self, plan, delete, *, fresh_candidates: tuple[str, ...] | None = None,
                        confirm: bool = False, remote: str | None = None) -> dict:
        """Apply a reviewed retention plan only with a caller-supplied fresh candidate list."""
        from .models import RetentionPlan
        if not isinstance(plan, RetentionPlan):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "retention plan is invalid", "invalid_retention_plan"))
        if fresh_candidates is None or not isinstance(fresh_candidates, tuple):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "fresh retention candidates are required", "stale_retention_plan"))
        if self.drive is None:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            from .retention import apply_retention
            outcome = apply_retention(plan, delete, confirm=confirm, fresh_candidates=fresh_candidates)
        except RecoveryError as exc:
            return result(False, "retention", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery retention apply failed", "retention_failed"))
        return result(True, "retention", remote=remote, status=outcome["status"], data={
            "candidates": outcome["candidates"], "requires_confirmation": False,
        })
