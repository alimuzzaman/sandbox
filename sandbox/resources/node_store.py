"""Exact-name, plan-bound reclaim for opted-in Compose node stores."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .models import CleanupCandidate
from .service import ResourceError, result


_FAMILY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PLAN_ID = re.compile(r"^[0-9a-f]{32}$")


class NodeStoreReclaimService:
    """Plan and apply removal of one exact family-scoped named volume."""

    def __init__(self, adapter, root: Path) -> None:
        self.adapter = adapter
        self.root = Path(root)

    @staticmethod
    def _volume(family: object) -> str:
        if not isinstance(family, str) or not _FAMILY.fullmatch(family):
            raise ResourceError("node-store family must be an exact canonical id",
                                "node_store_family_invalid")
        return f"sandbox-nodestore-{family}"

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise ResourceError("node-store plan id is invalid", "invalid_plan_id")
        return self.root / f"node-store-{plan_id}.json"

    def _write(self, record: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, staging = tempfile.mkstemp(prefix=".node-store-", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, self._path(record["plan_id"]))
        except Exception:
            try:
                os.unlink(staging)
            except OSError:
                pass
            raise

    def _observe(self, volume: str, budget_seconds: float):
        snapshot = self.adapter.observe(
            thorough=True, budget_seconds=budget_seconds, progress=None,
            focus=None, deep=True,
        )
        rows = tuple(item for item in snapshot.resources
                     if item.kind == "volume" and item.locator == volume)
        if len(rows) > 1:
            raise ResourceError("node-store evidence is ambiguous",
                                "node_store_evidence_ambiguous")
        return snapshot.target, rows[0] if rows else None

    def plan(self, family: object, *, budget_seconds: float = 30) -> dict:
        try:
            volume = self._volume(family)
            target, item = self._observe(volume, budget_seconds)
            if item is not None and "engine_volume_identity" not in item.evidence:
                raise ResourceError("node-store engine identity is unavailable",
                                    "node_store_identity_unavailable")
            plan_id = secrets.token_hex(16)
            record = {
                "schema": 1, "plan_id": plan_id, "state": "planned",
                "family": family, "volume_name": volume,
                "target": target.to_dict(), "exists": item is not None,
                "volume_identity": item.resource_id if item is not None else None,
                "size_bytes": item.size_bytes if item is not None else None,
                "running_mounts": sorted(item.references) if item is not None else [],
                "evidence_digest": hashlib.sha256(
                    (volume + "\0" + repr(sorted(item.references) if item else [])).encode()
                ).hexdigest(),
                "requires_confirmation": True,
                "automatic_cleanup": False,
            }
            self._write(record)
            return result(True, "node_store_plan", status="planned", target=target,
                          data=record)
        except ResourceError as exc:
            return result(False, "node_store_plan", status="refused", error=exc)
        except Exception:
            return result(False, "node_store_plan", status="failed",
                          error=ResourceError("node-store observation failed",
                                              "node_store_observation_failed", retryable=True))

    def apply(self, plan_id: object, *, family: object = None, confirm: bool = False,
              budget_seconds: float = 60) -> dict:
        if confirm is not True:
            return result(False, "node_store_cleanup", status="refused",
                          error=ResourceError("node-store cleanup requires explicit confirmation",
                                              "confirmation_required"))
        try:
            path = self._path(plan_id)
            with path.open(encoding="utf-8") as handle:
                record = json.load(handle)
            if (not isinstance(record, dict) or record.get("schema") != 1
                    or record.get("plan_id") != plan_id
                    or record.get("state") != "planned"
                    or record.get("volume_name") != self._volume(record.get("family"))):
                raise ResourceError("node-store plan is invalid", "invalid_plan")
            if family is not None and record.get("family") != family:
                raise ResourceError("node-store family does not match the plan",
                                    "plan_family_mismatch")
            target, item = self._observe(record["volume_name"], budget_seconds)
            if target.to_dict() != record.get("target"):
                raise ResourceError("node-store plan target changed", "plan_target_mismatch")
            if item is None:
                record["state"] = "completed"
                self._write(record)
                return result(True, "node_store_cleanup", status="already_absent",
                              target=target, data={"plan_id": plan_id,
                                                   "volume_name": record["volume_name"]})
            if record.get("exists") is not True or record.get("volume_identity") != item.resource_id:
                raise ResourceError("node-store volume identity changed",
                                    "node_store_identity_changed")
            if item.references:
                raise ResourceError("node-store volume has running mounts",
                                    "node_store_mounted")
            candidate = CleanupCandidate(
                item.resource_id, item.kind, item.locator,
                hashlib.sha256(item.locator.encode()).hexdigest(),
                item.owner_kind, item.owner_id, (), item.size_bytes, 0,
                CleanupCandidate.evidence_digest_for(item),
            )
            outcome = self.adapter.remove(candidate)
            if outcome.status not in {"removed", "already_absent"}:
                raise ResourceError("node-store removal is indeterminate",
                                    "node_store_cleanup_indeterminate")
            record["state"] = "completed"
            self._write(record)
            return result(True, "node_store_cleanup", status=outcome.status,
                          target=target, data={"plan_id": plan_id,
                                               "volume_name": record["volume_name"]})
        except ResourceError as exc:
            return result(False, "node_store_cleanup", status="refused", error=exc)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return result(False, "node_store_cleanup", status="refused",
                          error=ResourceError("node-store plan is unavailable", "plan_not_found"))
