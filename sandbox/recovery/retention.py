from __future__ import annotations

from .models import RetentionPlan


def build_retention_plan(prefix: str, complete_set_ids: tuple[str, ...]) -> RetentionPlan:
    ordered = tuple(sorted(complete_set_ids))
    protected = ordered[-1:]  # Never remove the newest verified set.
    return RetentionPlan(prefix, protected, tuple(item for item in ordered if item not in protected))
