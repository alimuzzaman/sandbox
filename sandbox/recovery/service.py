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
                 pending_root: str | Path | None = None, materializer=None,
                 server_capture=None) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self.drive = drive
        self.capture = capture
        self.pending_root = Path(pending_root) if pending_root else None
        self.materializer = materializer
        self.server_capture = server_capture

    def server_capture_start(self, remote: str | None, backup_id: str | None, profiles,
                             *, confirm: bool = False) -> dict:
        if self.server_capture is None:
            return result(False, "capture", remote=remote, error=RecoveryError(
                "server capture is not configured", "recovery_not_configured"))
        return self.server_capture.start(remote, backup_id, profiles, confirm=confirm)

    def server_capture_status(self, remote: str | None, backup_id: str | None) -> dict:
        if self.server_capture is None:
            return result(False, "status", remote=remote, error=RecoveryError(
                "server capture is not configured", "recovery_not_configured"))
        return self.server_capture.status(remote, backup_id)

    def server_capture_promote(self, remote: str | None, backup_id: str | None,
                               *, confirm: bool = False) -> dict:
        if self.server_capture is None:
            return result(False, "promote", remote=remote, error=RecoveryError(
                "server capture is not configured", "recovery_not_configured"))
        return self.server_capture.promote(remote, backup_id, confirm=confirm)

    def server_capture_retention(self, remote: str | None, backup_id: str | None = None,
                                 *, confirm: bool = False) -> dict:
        if self.server_capture is None:
            if not remote:
                return self.retention_plan()
            return result(False, "retention", remote=remote, error=RecoveryError(
                "server capture is not configured", "recovery_not_configured"))
        if not remote:
            return self.retention_plan()
        if not confirm:
            if backup_id is None:
                return self.server_capture.retention(remote)
            return self.server_capture.retire_plan(remote, backup_id)
        return self.server_capture.retire(remote, backup_id, confirm=confirm)

    def create_materialized(self, set_id: str, profiles: tuple[str, ...], *,
                            confirm: bool = False, remote: str | None = None) -> dict:
        """Capture selected declarations through the configured controller adapter.

        Arbitrary caller paths never authorize symbolic source materialization.
        The ordinary explicit-artifact entry point retains its existing gate.
        """
        try:
            from .capture import _valid_set_id
            if not confirm:
                raise RecoveryError("recovery create requires explicit confirmation", "confirmation_required")
            if not profiles:
                raise RecoveryError("recovery create requires selected profiles", "missing_profiles")
            if not _valid_set_id(set_id):
                raise RecoveryError("recovery set id is invalid", "invalid_set_id")
            plan = build_plan(self.catalog, profiles)
            if not remote or self.materializer is None or self.capture is None:
                raise RecoveryError("controller materialization is not configured", "recovery_not_configured")
            by_id = self.catalog.by_id()
            bindings = {
                profile_id: {"dependencies": list(by_id[profile_id].dependencies),
                             "restore_target": by_id[profile_id].restore_target,
                             "allowed_roots": list(by_id[profile_id].allowed_roots)}
                for profile_id in plan.profiles
            }
            manifest = self.materializer.publish(remote, plan, self.capture, set_id, bindings)
        except RecoveryError as exc:
            return result(False, "create", remote=remote, error=exc)
        except (OSError, TypeError, ValueError):
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery materialization failed", "materialization_failed"))
        except Exception:
            # Adapter implementations are controller-owned extensions. Keep
            # an unexpected implementation failure inside the stable recovery
            # envelope instead of leaking private paths or transport details.
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery materialization failed", "materialization_failed"))
        return result(True, "create", remote=remote, status="complete", data={"manifest": manifest})

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
            by_id = self.catalog.by_id()
            bindings = {
                profile_id: {"dependencies": list(by_id[profile_id].dependencies),
                             "restore_target": by_id[profile_id].restore_target,
                             "allowed_roots": list(by_id[profile_id].allowed_roots)}
                for profile_id in plan.profiles
            }
            manifest = self.capture.publish_files(
                set_id, artifacts, profiles=plan.profiles, provenance=provenance,
                profile_bindings=bindings,
            )
        except RecoveryError as exc:
            return result(False, "create", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery capture failed", "capture_failed"))
        return result(True, "create", remote=remote, status="complete", data={"manifest": manifest})

    def list(self, remote: str | None = None) -> dict:
        server_data = {}
        if remote and self.server_capture is not None:
            try:
                server_data = self.server_capture.captures(remote)
            except RecoveryError as exc:
                return result(False, "list", remote=remote, error=exc)
            except (OSError, TypeError, ValueError):
                return result(False, "list", remote=remote, error=RecoveryError(
                    "server capture listing is invalid", "list_failed"))
        if self.drive is None and not (remote and server_data):
            return result(False, "list", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            objects = self.drive.list("") if self.drive is not None else []
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
        data = {
            "complete_manifests": tuple(complete),
            "incomplete": tuple(incomplete),
            "legacy": tuple(legacy),
            "locally_pending": tuple(local_pending),
            "unverifiable": tuple(unverifiable),
            "pending": tuple(incomplete),
            **server_data,
        }
        if remote:
            data["drive"] = {"configured": self.drive is not None}
        return result(True, "list", remote=remote, status="listed", data=data)

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
            # Legacy paths are review-only candidates.  They are deliberately
            # withheld until at least one current-passphrase verified set exists.
            current_verified = any(item["verified"] and item["passphrase_current"]
                                   and _retention_timestamp_valid(item["created_at"])
                                   for item in observed)
            legacy_paths = tuple(sorted(_listing_path(item) for item in objects
                                        if not _listing_path(item).startswith("sets/")))
        except RecoveryError as exc:
            return result(False, "retention", remote=remote, error=exc)
        except (OSError, TypeError, ValueError) as exc:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery retention inventory is invalid", "retention_failed"))
        return result(True, "retention", remote=remote, status="planned", data={
            "destination_prefix": plan.destination_prefix,
            "protected_sets": plan.protected_sets,
            "candidates": plan.candidates,
            "legacy_candidates": legacy_paths if current_verified else (),
            "legacy_candidate_status": "review_required" if current_verified else "blocked_no_current_verified_set",
            "unclassified": tuple(unclassified),
            "requires_confirmation": plan.requires_confirmation,
        })

    def _profile_inventory(self, profile: str, keep_count: int) -> dict:
        """Verified sets of one profile, split into kept and prunable (newest kept)."""
        from .restore import verify_manifest
        from .retention import build_retention_plan
        objects = self.drive.list("")
        by_set: dict[str, list[str]] = {}
        for item in objects:
            path = _listing_path(item)
            set_id = _set_id(path)
            if set_id is not None and not item.get("IsDir"):
                by_set.setdefault(set_id, []).append(path)
        observed, digests, skipped = [], {}, []
        for set_id in sorted(set_id for set_id, paths in by_set.items()
                             if f"sets/{set_id}/manifest.json" not in paths):
            # Interrupted upload or prune; profile unknown, never deletable here.
            skipped.append({"id": set_id, "reason": "incomplete_no_manifest"})
        for set_id in sorted(set_id for set_id, paths in by_set.items()
                             if f"sets/{set_id}/manifest.json" in paths):
            try:
                # Cheap profile filter first, so other profiles' sets are not
                # downloaded; the full verification below re-reads the manifest.
                import json
                declared = json.loads(self.drive.get(f"sets/{set_id}/manifest.json"))
            except (RecoveryError, ValueError, TypeError):
                skipped.append({"id": set_id, "reason": "invalid_manifest"})
                continue
            if not isinstance(declared, dict) or declared.get("profiles") != [profile]:
                continue
            try:
                manifest = verify_manifest(self.drive, set_id)
            except RecoveryError as exc:
                skipped.append({"id": set_id, "reason": exc.code})
                continue
            if manifest.get("profiles") != [profile]:
                continue
            current = self._current_passphrase_verifies(manifest["ciphertext_object"])
            if not current or not _retention_timestamp_valid(manifest.get("created_at")):
                skipped.append({"id": set_id, "reason": "passphrase_not_current" if not current
                                else "invalid_created_at"})
                continue
            digests[set_id] = (manifest["ciphertext_sha256"], manifest["ciphertext_object"],
                               manifest["ciphertext_size"])
            observed.append({"id": set_id, "prefix": "sets/", "status": "complete", "verified": True,
                             "passphrase_current": True, "created_at": manifest.get("created_at")})
        plan = build_retention_plan("sets/", tuple(observed), keep_count=keep_count)
        newest = plan.protected_sets[-1] if plan.protected_sets else None
        return {
            "protected_sets": plan.protected_sets,
            "candidates": [{"id": set_id, "ciphertext_sha256": digests[set_id][0],
                            "ciphertext_object": digests[set_id][1]}
                           for set_id in plan.candidates],
            "newest_verified": newest, "skipped": skipped,
            "protected_objects": {set_id: (digests[set_id][1], digests[set_id][2])
                                  for set_id in plan.protected_sets},
            "objects": {set_id: tuple(paths) for set_id, paths in by_set.items()},
        }

    def _prune_recheck(self, set_id: str, profile: str, plan: dict) -> tuple[str, ...]:
        """Re-verify one candidate right before deleting it; return its object keys."""
        from .restore import verify_manifest
        expected = next(item for item in plan["candidates"] if item["id"] == set_id)
        manifest = verify_manifest(self.drive, set_id)
        if (manifest.get("profiles") != [profile]
                or manifest["ciphertext_sha256"] != expected["ciphertext_sha256"]
                or manifest["ciphertext_object"] != expected["ciphertext_object"]):
            raise RecoveryError("a candidate set changed since planning", "stale_retention_plan")
        keys = tuple(path for item in self.drive.list("")
                     if (path := _listing_path(item)).startswith(f"sets/{set_id}/")
                     and not item.get("IsDir"))
        if set(keys) != {f"sets/{set_id}/manifest.json", expected["ciphertext_object"]}:
            raise RecoveryError("a candidate set holds unexpected objects", "stale_retention_plan")
        return keys

    def _protected_present(self, protected: dict) -> bool:
        """Every protected manifest and ciphertext is still listed at its verified size."""
        sizes = {_listing_path(item): item.get("Size") for item in self.drive.list("")}
        return all(f"sets/{set_id}/manifest.json" in sizes and sizes.get(key) == size
                   for set_id, (key, size) in protected.items())

    def _current_keys(self, set_id: str) -> tuple[str, ...]:
        return tuple(path for item in self.drive.list("")
                     if (path := _listing_path(item)).startswith(f"sets/{set_id}/")
                     and not item.get("IsDir"))

    def _remnant_matches(self, key: str, digest: str) -> bool:
        from .integrity import sha256_file
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-prune-") as directory:
            target = Path(directory) / "object"
            self.drive.get_file(key, target)
            return sha256_file(target) == digest

    def retention_prune(self, profile: str, *, keep_count: int, state_root: Path,
                        plan_id: str | None = None, confirm: bool = False,
                        remote: str | None = None) -> dict:
        """Plan, or with a plan id and confirmation apply, a profile-scoped prune."""
        from .prune import PruneStore, apply_prune, plan_body, valid_profile
        if self.drive is None:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        if self.capture is None:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "retention verification needs RECOVERY_PASSPHRASE", "missing_passphrase"))
        if not valid_profile(profile):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "one recovery profile is required", "invalid_retention_policy"))
        if (not isinstance(keep_count, int) or isinstance(keep_count, bool)
                or not 1 <= keep_count <= 1000):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "retention keep count must be 1-1000", "invalid_retention_policy"))
        if confirm and not plan_id:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "a confirmed prune requires --plan-id from a reviewed plan", "confirmation_required"))
        if plan_id and not confirm:
            return result(False, "retention", remote=remote, error=RecoveryError(
                "applying a plan requires --confirm", "confirmation_required"))
        store = PruneStore(Path(state_root) / "retention")
        destination = getattr(self.drive, "destination", "")
        try:
            if plan_id:
                plan = store.load(plan_id)
                if (plan.get("destination") != destination or plan.get("profile") != profile
                        or plan.get("keep_count") != keep_count):
                    raise RecoveryError("retention plan selectors differ", "retention_plan_mismatch")
                fresh = self._profile_inventory(profile, keep_count)
                outcome = apply_prune(
                    store, plan, fresh, self.drive,
                    recheck=lambda set_id: self._prune_recheck(set_id, profile, plan),
                    protected_present=lambda: self._protected_present(fresh["protected_objects"]),
                    remnant_matches=self._remnant_matches,
                    current_keys=self._current_keys,
                )
                return result(True, "retention", remote=remote, status="pruned", data={
                    "profile": profile, "keep_count": keep_count, **outcome,
                    "requires_confirmation": False,
                })
            inventory = self._profile_inventory(profile, keep_count)
            record = store.save(plan_body(destination, profile, keep_count, inventory))
        except RecoveryError as exc:
            return result(False, "retention", remote=remote, error=exc)
        except (OSError, TypeError, ValueError, KeyError):
            return result(False, "retention", remote=remote, error=RecoveryError(
                "recovery retention prune failed", "retention_failed"))
        return result(True, "retention", remote=remote, status="planned", data={
            "profile": profile, "keep_count": keep_count, "plan_id": record["plan_id"],
            "plan_expires_at": record["expires_at"],
            "destination_prefix": "sets/",
            "protected_sets": tuple(record["protected_sets"]),
            "candidates": tuple(item["id"] for item in record["candidates"]),
            "unclassified": tuple(inventory["skipped"]),
            "requires_confirmation": bool(record["candidates"]),
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
