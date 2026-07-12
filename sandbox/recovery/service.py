from __future__ import annotations

from .catalog import RecoveryCatalog
from .errors import RecoveryError, result
from .planner import build_plan


class RecoveryService:
    def __init__(self, catalog: RecoveryCatalog, *, inventory=None) -> None:
        self.catalog = catalog
        self.inventory = inventory

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
