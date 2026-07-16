"""Conservative, destination-bounded retention candidate calculation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .errors import RecoveryError
from .models import RetentionPlan


def _normalise(prefix: str, sets) -> tuple[dict, ...]:
    if not prefix or not prefix.endswith("/") or ".." in prefix.split("/"):
        raise RecoveryError("retention destination prefix is invalid", "invalid_retention_prefix")
    try:
        observed = tuple(sets)
    except TypeError as exc:
        raise RecoveryError("retention inventory is invalid", "invalid_retention_inventory") from exc
    if not all(isinstance(item, dict) for item in observed):
        raise RecoveryError("retention inventory requires set metadata", "invalid_retention_inventory")
    return observed


def _created_at(item: dict) -> datetime | None:
    value = item.get("created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def build_retention_plan(
    prefix: str,
    complete_set_ids: tuple[str, ...] | tuple[dict, ...],
    *,
    keep_count: int = 1,
    minimum_age: timedelta = timedelta(0),
    now: datetime | None = None,
) -> RetentionPlan:
    if not isinstance(keep_count, int) or isinstance(keep_count, bool) or keep_count < 1:
        raise RecoveryError("retention keep count must be at least one", "invalid_retention_policy")
    if not isinstance(minimum_age, timedelta) or minimum_age < timedelta(0):
        raise RecoveryError("retention minimum age is invalid", "invalid_retention_policy")
    reference = now or datetime.now(timezone.utc)
    if not isinstance(reference, datetime) or reference.tzinfo is None:
        raise RecoveryError("retention reference time must include a timezone", "invalid_retention_policy")
    reference = reference.astimezone(timezone.utc)
    observed = _normalise(prefix, complete_set_ids)
    valid = [item for item in observed if item.get("prefix") == prefix and item.get("status") == "complete"
             and item.get("verified") is True and item.get("passphrase_current") is True
             and item.get("id") and _created_at(item) is not None]
    ordered = sorted(valid, key=lambda item: (_created_at(item), str(item["id"])))
    age_floor = reference - minimum_age
    retained = {item["id"] for item in ordered[-keep_count:]}
    retained.update(item["id"] for item in ordered if _created_at(item) > age_floor)
    protected = tuple(item["id"] for item in ordered if item["id"] in retained)
    candidates = tuple(item["id"] for item in ordered if item["id"] not in retained)
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
