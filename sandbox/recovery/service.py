from __future__ import annotations

import json
from pathlib import Path

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
        return result(True, "plan", remote=remote, status="planned", data=data)

    def create(self, set_id: str, artifacts: dict, profiles: tuple[str, ...], *,
               confirm: bool = False, remote: str | None = None, provenance: dict | None = None) -> dict:
        if not confirm:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery create requires explicit confirmation", "confirmation_required"))
        if self.capture is None:
            return result(False, "create", remote=remote, error=RecoveryError(
                "recovery capture is not configured", "recovery_not_configured"))
        try:
            manifest = self.capture.publish_files(set_id, artifacts, profiles=profiles, provenance=provenance)
        except RecoveryError as exc:
            return result(False, "create", remote=remote, error=exc)
        return result(True, "create", remote=remote, status="complete", data={"manifest": manifest})

    def list(self, remote: str | None = None) -> dict:
        if self.drive is None:
            return result(False, "list", remote=remote, error=RecoveryError(
                "recovery Drive is not configured", "recovery_not_configured"))
        try:
            objects = self.drive.list("sets")
            groups: dict[str, list[dict]] = {}
            legacy: list[dict] = []
            for item in objects:
                path = str(item.get("Path", ""))
                set_id = _set_id(path)
                if set_id is None:
                    legacy.append(item)
                else:
                    groups.setdefault(set_id, []).append(item)

            complete: list[dict] = []
            incomplete: list[dict] = []
            unverifiable: list[dict] = []
            for set_id, group in sorted(groups.items()):
                manifests = [item for item in group if str(item.get("Path", "")).endswith("/manifest.json")]
                if not manifests:
                    incomplete.extend(group)
                    continue
                if len(manifests) != 1:
                    unverifiable.extend(group)
                    continue
                manifest_item = manifests[0]
                try:
                    manifest = json.loads(self.drive.get(manifest_item["Path"]))
                    cipher_key = manifest["ciphertext_object"]
                    cipher_item = next(item for item in group if item.get("Path") == cipher_key)
                    valid = (manifest.get("schema_version") == 1 and manifest.get("id") == set_id
                             and manifest.get("status") == "complete"
                             and manifest.get("ciphertext_size") == cipher_item.get("Size"))
                except (KeyError, TypeError, ValueError, StopIteration, RecoveryError):
                    valid = False
                if valid:
                    complete.append(manifest_item)
                else:
                    unverifiable.extend(group)
            local_pending: list[dict] = []
            if self.pending_root and self.pending_root.is_dir():
                for path in sorted(self.pending_root.glob("*.archive.tar.gpg")):
                    if path.is_file():
                        local_pending.append({"Path": str(path), "Size": path.stat().st_size})
        except RecoveryError as exc:
            return result(False, "list", remote=remote, error=exc)
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
        return result(True, "verify", remote=remote, status="verified", data={"id": set_id, "manifest": manifest})

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
        return result(True, "restore", remote=remote, status="planned", data={
            "set_id": plan.set_id, "profiles": plan.profiles, "actions": plan.actions,
            "checkpoints": plan.checkpoints, "rollback": plan.rollback,
            "requires_confirmation": plan.requires_confirmation,
        })
