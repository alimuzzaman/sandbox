from __future__ import annotations

from .catalog import RecoveryCatalog
from .errors import RecoveryError, result
from .planner import build_plan


class RecoveryService:
    def __init__(self, catalog: RecoveryCatalog, *, inventory=None, drive=None, capture=None) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self.drive = drive
        self.capture = capture

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
            manifests = tuple(item for item in objects if str(item.get("Path", "")).endswith("manifest.json"))
            pending = tuple(item for item in objects if item not in manifests)
        except RecoveryError as exc:
            return result(False, "list", remote=remote, error=exc)
        return result(True, "list", remote=remote, status="listed", data={"complete_manifests": manifests, "pending": pending})

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
