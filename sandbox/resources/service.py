from __future__ import annotations

from datetime import timezone
import math
import secrets

from .attribution import apply_cleanup_guidance
from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    CleanupPlan,
    CleanupRun,
    StorageScan,
    redact,
    utc_now,
)
from .plans import ResourcePlanError


class ResourceError(RuntimeError):
    def __init__(self, message: str, code: str = "resource_error", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def result(
    ok: bool,
    action: str,
    *,
    status: str,
    target=None,
    data: dict | None = None,
    error: ResourceError | ResourcePlanError | None = None,
) -> dict:
    return redact({
        "schema_version": 1,
        "ok": bool(ok),
        "action": action,
        "status": status,
        "target": None if target is None else target.to_dict(),
        "data": data or {},
        "error": None if error is None else {
            "code": getattr(error, "code", "resource_error"),
            "message": str(error),
            "retryable": bool(getattr(error, "retryable", False)),
        },
    })


class ResourceService:
    def __init__(self, adapter, plan_store, *, clock=utc_now) -> None:
        self.adapter = adapter
        self.plan_store = plan_store
        self.clock = clock or utc_now

    @staticmethod
    def _budget(value) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > 3600
        ):
            raise ResourceError(
                "budget must be between 0 and 3600 seconds",
                "invalid_budget",
            )
        return float(value)

    def _scan(
        self, *, thorough: bool, budget_seconds: float, progress=None,
        focus: str | None = None, deep: bool = False,
    ) -> StorageScan:
        budget = self._budget(budget_seconds)
        started = self.clock().astimezone(timezone.utc)
        snapshot = self.adapter.observe(
            thorough=bool(thorough or deep),
            budget_seconds=budget,
            progress=progress,
            focus=focus,
            deep=bool(deep),
        )
        target = self.adapter.target()
        if snapshot.target != target:
            raise ResourceError(
                "target identity changed during measurement",
                "target_identity_changed",
            )
        if snapshot.capacity is None:
            raise ResourceError(
                "host capacity could not be measured",
                "measurement_unavailable",
                retryable=True,
            )
        completed = self.clock().astimezone(timezone.utc)
        measured = tuple(
            item for item in snapshot.resources
            if item.size_state == "measured" and item.size_bytes is not None
        )
        explicitly_accounted = tuple(
            item for item in measured if item.capacity_accounted
        )
        accounting_items = explicitly_accounted or measured
        raw_attributed = sum(item.size_bytes or 0 for item in accounting_items)
        used = int((snapshot.capacity or {}).get("used_bytes") or 0)
        deep_attribution = snapshot.deep_attribution
        if deep_attribution is not None:
            deep_attribution = apply_cleanup_guidance(
                deep_attribution, snapshot.resources,
            )
        if deep and deep_attribution is not None:
            reconciliation = deep_attribution.reconciliation
            attributed = reconciliation.accounted_bytes
            unknown = reconciliation.residual_unexplained_bytes
        else:
            attributed = min(raw_attributed, used)
            unknown = max(used - attributed, 0)
        reclaimable = sum(item.reclaimable_bytes for item in snapshot.resources)
        incomplete = any(
            item.get("status") not in {"complete", "observed"}
            for item in snapshot.category_outcomes
        )
        if deep and (
            deep_attribution is None
            or deep_attribution.status != "complete"
        ):
            incomplete = True
        status = "partial" if incomplete else "complete"
        confidence = "low" if incomplete else (
            "high" if thorough else "medium"
        )
        ordered = tuple(sorted(
            snapshot.resources,
            key=lambda item: (
                item.size_bytes is not None,
                item.size_bytes or -1,
                item.resource_id,
            ),
            reverse=True,
        ))
        drift = snapshot.drift
        if raw_attributed > used:
            drift = {
                **(drift or {}),
                "capacity_accounting_overage_bytes": raw_attributed - used,
                "reason": "filesystem_accounting_overlap_or_sparse_storage",
            }
        return StorageScan(
            scan_id=secrets.token_hex(16),
            target=target,
            mode="deep" if deep else "thorough" if thorough else "fast",
            started_at=started,
            completed_at=completed,
            budget_seconds=budget,
            status=status,
            capacity=snapshot.capacity,
            resources=ordered,
            attributed_bytes=attributed,
            unknown_bytes=unknown,
            reclaimable_bytes=reclaimable,
            confidence=confidence,
            category_outcomes=snapshot.category_outcomes,
            drift=drift,
            deep_attribution=deep_attribution,
        )

    def status(
        self, *, thorough: bool = False, budget_seconds: float = 15,
        progress=None, deep: bool = False,
    ) -> dict:
        try:
            scan = self._scan(
                thorough=thorough,
                budget_seconds=budget_seconds,
                progress=progress,
                focus=None,
                deep=deep,
            )
            return result(
                True, "status", status=scan.status, target=scan.target,
                data=scan.to_dict(),
            )
        except (ResourceError, OSError, RuntimeError) as exc:
            error = exc if isinstance(exc, ResourceError) else ResourceError(
                "resource measurement failed", "measurement_unavailable",
                retryable=True,
            )
            return result(False, "status", status="failed", error=error)

    def plan(
        self,
        scope: str,
        *,
        thorough: bool = True,
        budget_seconds: float = 60,
        progress=None,
    ) -> dict:
        if scope not in {"cache", "stale"}:
            return result(
                False, "plan", status="failed",
                error=ResourceError("scope must be cache or stale", "invalid_scope"),
            )
        try:
            scan = self._scan(
                thorough=thorough,
                budget_seconds=budget_seconds,
                progress=progress,
                focus=scope,
                deep=False,
            )
            candidates = []
            exclusions = []
            for item in scan.resources:
                eligible = (
                    scope == "cache"
                    and item.classification == "disposable_cache"
                    and item.kind not in {"volume", "worktree"}
                    and item.size_state == "measured"
                    and item.reclaimable_bytes > 0
                ) or (
                    scope == "stale"
                    and item.classification == "stale_candidate"
                    and item.kind in {"volume", "worktree"}
                    and item.size_state == "measured"
                    and item.reclaimable_bytes > 0
                )
                if eligible:
                    candidates.append(CleanupCandidate.from_observation(item))
                else:
                    exclusions.append({
                        "resource_id": item.resource_id,
                        "reason": (
                            "persistent_resource_requires_stale_scope"
                            if scope == "cache" and item.kind in {"volume", "worktree"}
                            else item.classification
                            if item.size_state == "measured"
                            else item.size_state
                        ),
                    })
            plan = CleanupPlan.create(
                scan.target, scope, candidates, exclusions,
                scan_id=scan.scan_id, now=self.clock(),
            )
            self.plan_store.save(plan)
            public = plan.to_dict(public=True)
            public["requires_confirmation"] = public.pop("confirmation_required")
            return result(
                True, "plan", status="planned", target=scan.target,
                data=public,
            )
        except (ResourceError, ResourcePlanError, OSError, RuntimeError) as exc:
            error = exc if isinstance(exc, (ResourceError, ResourcePlanError)) else ResourceError(
                "resource cleanup plan failed", "cleanup_failed",
            )
            return result(False, "plan", status="failed", error=error)

    @staticmethod
    def _still_matches(candidate: CleanupCandidate, current) -> bool:
        try:
            refreshed = CleanupCandidate.from_observation(current)
        except (TypeError, ValueError):
            return False
        return (
            refreshed.locator_digest == candidate.locator_digest
            and refreshed.expected_owner_kind == candidate.expected_owner_kind
            and refreshed.expected_owner_id == candidate.expected_owner_id
            and refreshed.evidence_digest == candidate.evidence_digest
        )

    def cleanup(self, plan_id: str, *, confirm: bool = False) -> dict:
        if not confirm:
            return result(
                False, "cleanup", status="refused",
                error=ResourceError(
                    "resource cleanup requires explicit confirmation",
                    "confirmation_required",
                ),
            )
        started = self.clock().astimezone(timezone.utc)
        try:
            before_snapshot = self.adapter.observe(
                thorough=False, budget_seconds=15, progress=None,
            )
            target = before_snapshot.target
            if self.adapter.target() != target:
                raise ResourceError(
                    "target identity changed during measurement",
                    "target_identity_changed",
                )
            plan = self.plan_store.begin(plan_id, target)
        except (ResourcePlanError, ResourceError, OSError, RuntimeError) as exc:
            error = exc if isinstance(exc, (ResourcePlanError, ResourceError)) else ResourceError(
                "cleanup target could not be resolved", "cleanup_failed",
            )
            return result(False, "cleanup", status="refused", error=error)

        try:
            outcomes = []
            indeterminate = False
            for candidate in plan.candidates:
                try:
                    current = self.adapter.revalidate(candidate)
                    if current is None:
                        outcomes.append(CleanupItemOutcome(
                            candidate.resource_id, "already_absent",
                            "already_absent", candidate.expected_size_bytes,
                            False, self.clock(),
                        ))
                        continue
                    if not self._still_matches(candidate, current):
                        outcomes.append(CleanupItemOutcome(
                            candidate.resource_id, "skipped",
                            "evidence_changed", current.size_bytes,
                            True, self.clock(),
                        ))
                        continue
                    outcome = self.adapter.remove(candidate)
                    outcomes.append(outcome)
                    if outcome.status == "timed_out":
                        indeterminate = True
                except TimeoutError:
                    indeterminate = True
                    outcomes.append(CleanupItemOutcome(
                        candidate.resource_id, "timed_out",
                        "cleanup_timed_out", candidate.expected_size_bytes,
                        False, self.clock(),
                    ))
                except Exception:
                    outcomes.append(CleanupItemOutcome(
                        candidate.resource_id, "failed",
                        "cleanup_failed", candidate.expected_size_bytes,
                        False, self.clock(),
                    ))
            after_snapshot = self.adapter.observe(
                thorough=False, budget_seconds=15, progress=None,
            )
            before_used = int((before_snapshot.capacity or {}).get("used_bytes") or 0)
            after_used = int((after_snapshot.capacity or {}).get("used_bytes") or 0)
            observed_reclaimed = max(before_used - after_used, 0)
            if indeterminate:
                run_status = "indeterminate"
            else:
                run_status = (
                    "partial" if any(item.status in {"skipped", "failed", "timed_out"}
                                     for item in outcomes)
                    else "completed"
                )
            completed = self.clock().astimezone(timezone.utc)
            itemized = sum(
                item.observed_bytes or 0
                for item in outcomes
                if item.status == "removed"
            )
            drift = None
            if itemized != observed_reclaimed:
                drift = {
                    "capacity_delta_bytes": observed_reclaimed,
                    "itemized_removed_bytes": itemized,
                    "difference_bytes": observed_reclaimed - itemized,
                    "reason": "concurrent_or_shared_storage_change",
                }
            run = CleanupRun(
                run_id=secrets.token_hex(16),
                plan_id=plan.plan_id,
                target=target,
                status=run_status,
                started_at=started,
                completed_at=completed,
                planned_bytes=plan.estimated_reclaimable_bytes,
                observed_reclaimed_bytes=observed_reclaimed,
                outcomes=tuple(outcomes),
                capacity_before=before_snapshot.capacity,
                capacity_after=after_snapshot.capacity,
                drift=drift,
            )
            self.plan_store.record_run(run)
            self.plan_store.finish(
                plan.plan_id,
                "indeterminate" if indeterminate else "completed",
            )
            return result(
                not indeterminate,
                "cleanup",
                status=run_status,
                target=target,
                data=run.to_dict(),
                error=ResourceError(
                    "cleanup outcome is indeterminate; rescan before any retry",
                    "plan_indeterminate",
                ) if indeterminate else None,
            )
        except Exception:
            try:
                self.plan_store.finish(plan.plan_id, "indeterminate")
            except Exception:
                pass
            return result(
                False, "cleanup", status="indeterminate", target=plan.target,
                error=ResourceError(
                    "cleanup outcome is indeterminate; rescan before any retry",
                    "plan_indeterminate",
                ),
            )
