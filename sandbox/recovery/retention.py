"""Conservative, destination-bounded retention candidate calculation."""
from __future__ import annotations

from .errors import RecoveryError
from .models import RetentionPlan


def _normalise(prefix: str, sets) -> tuple[dict, ...]:
    if not prefix or not prefix.endswith("/") or ".." in prefix.split("/"):
        raise RecoveryError("retention destination prefix is invalid", "invalid_retention_prefix")
    if all(isinstance(item, str) for item in sets):
        return tuple({"id": item, "prefix": prefix, "status": "complete", "verified": True,
                      "created_at": item, "passphrase_current": True} for item in sets)
    return tuple(item for item in sets if isinstance(item, dict))


def build_retention_plan(prefix: str, complete_set_ids: tuple[str, ...] | tuple[dict, ...]) -> RetentionPlan:
    observed = _normalise(prefix, complete_set_ids)
    valid = [item for item in observed if item.get("prefix") == prefix and item.get("status") == "complete"
             and item.get("verified") is True and item.get("passphrase_current") is True and item.get("id")]
    ordered = sorted(valid, key=lambda item: (str(item.get("created_at", "")), str(item["id"])))
    protected = tuple(item["id"] for item in ordered[-1:])
    candidates = tuple(item["id"] for item in ordered[:-1])
    return RetentionPlan(prefix, protected, candidates)


def apply_retention(plan: RetentionPlan, delete, *, confirm: bool = False,
                    fresh_candidates: tuple[str, ...] | None = None) -> dict:
    if not confirm:
        raise RecoveryError("retention deletion requires explicit confirmation", "confirmation_required")
    if fresh_candidates is not None and tuple(fresh_candidates) != plan.candidates:
        raise RecoveryError("retention candidates are stale", "stale_retention_plan")
    for set_id in plan.candidates:
        delete(f"{plan.destination_prefix}{set_id}")
    return {"status": "deleted", "candidates": plan.candidates}
