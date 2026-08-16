from __future__ import annotations

from dataclasses import replace
from datetime import timezone
import inspect
import math
import secrets

from .attribution import (
    CoverageObservation,
    apply_cleanup_guidance,
    network_capacity_pressure,
)
from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    CleanupPlan,
    CleanupRun,
    ResourceRequest,
    StorageScan,
    redact,
    utc_now,
)
from .plans import ResourcePlanError


CATEGORY_KINDS = {
    "docker_images": ("image",),
    "docker_containers": ("container",),
    "docker_volumes": ("volume",),
    "docker_networks": ("network",),
    "docker_build_cache": ("build_cache",),
    "docker_storage": ("engine_storage",),
    "host_filesystem": ("host_root",),
    "deploy_worktrees": ("worktree",),
    "sandbox_runtime": ("runtime", "download_cache"),
    "job_artifacts": ("job_artifact",),
}


def enrich_outcomes(outcomes, resources) -> tuple:
    """Say how much a category did measure before it ran out of time.

    A bare ``timed_out`` (or a ``not_measured`` sitting next to zero-size
    rows) reads like "nothing to see"; the measured total and the count of
    rows left unmeasured make a partial result usable evidence instead.
    """
    totals: dict[str, list[int]] = {}
    for item in resources:
        row = totals.setdefault(getattr(item, "kind", None), [0, 0, 0])
        size = getattr(item, "size_bytes", None)
        if getattr(item, "size_state", None) == "measured" and size is not None:
            row[0] += int(size)
            row[1] += 1
        else:
            row[2] += 1
    enriched = []
    for outcome in outcomes:
        selected = (
            CATEGORY_KINDS.get(outcome.get("category"))
            if isinstance(outcome, dict) else None
        )
        if not selected:
            enriched.append(outcome)
            continue
        rows = [totals.get(name, [0, 0, 0]) for name in selected]
        enriched.append({
            **outcome,
            "measured_bytes": sum(row[0] for row in rows),
            "measured_count": sum(row[1] for row in rows),
            "unmeasured_count": sum(row[2] for row in rows),
        })
    return tuple(enriched)


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

    @staticmethod
    def _supports_keyword(function, keyword: str) -> bool:
        """Do not require cancellation support from compatible providers."""
        try:
            parameters = inspect.signature(function).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == keyword
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    @staticmethod
    def _deep_scope_matches_capacity(
        deep_attribution, capacity: dict, capacity_scope_id: str | None,
        deep_scope_id: str | None,
    ) -> bool:
        if (capacity_scope_id is None) != (deep_scope_id is None):
            return False
        if capacity_scope_id is not None and capacity_scope_id != deep_scope_id:
            return False
        if capacity_scope_id is None:
            selected_scopes = {
                item.capacity_scope_id or item.filesystem_id
                for item in deep_attribution.filesystems
                if item.selected
            }
            if len(selected_scopes) > 1:
                return False
        used = int(capacity.get("used_bytes") or 0)
        # Capacity and the deep pass read the same live filesystem moments
        # apart, so exact byte equality is not evidence of a scope mismatch.
        # Use the same materiality threshold the drift contract already uses.
        tolerance = min(
            max(int(used * 0.01), 64 * 1024 * 1024), used // 10,
        )
        return abs(
            deep_attribution.reconciliation.used_bytes - used
        ) <= tolerance

    @staticmethod
    def _with_scope_mismatch(deep_attribution):
        coverage = CoverageObservation(
            category="reconciliation",
            boundary_id=None,
            status="partial",
            duration_ms=0,
            confidence="low",
            privilege_sufficient=False,
            reason="capacity_scope_mismatch",
        )
        return replace(
            deep_attribution,
            status="partial",
            coverage=(*deep_attribution.coverage, coverage),
        )

    @staticmethod
    def _terminal_status(request: ResourceRequest, outcomes, deep_attribution) -> str | None:
        states = {
            item.get("status")
            for item in outcomes
            if isinstance(item, dict) and isinstance(item.get("status"), str)
        }
        if deep_attribution is not None:
            states.update(item.status for item in deep_attribution.coverage)
        if request.is_cancelled() or "cancelled" in states:
            return "cancelled"
        if "disconnected" in states:
            return "disconnected"
        return None

    def _scan(
        self, *, thorough: bool, budget_seconds: float, progress=None,
        focus: str | None = None, deep: bool = False, cancelled=False,
        directory_cache: str | None = None,
    ) -> StorageScan:
        budget = self._budget(budget_seconds)
        request = ResourceRequest(budget, cancelled)
        started = self.clock().astimezone(timezone.utc)
        observe = self.adapter.observe
        supports_cancellation = self._supports_keyword(observe, "cancelled")
        if request.is_cancelled() and not supports_cancellation:
            raise ResourceError(
                "resource measurement was cancelled before provider execution",
                "request_cancelled",
            )
        observe_kwargs = {
            "thorough": bool(thorough or deep),
            "budget_seconds": budget,
            "progress": progress,
            "focus": focus,
            "deep": bool(deep),
        }
        if supports_cancellation:
            observe_kwargs["cancelled"] = cancelled
        if directory_cache and self._supports_keyword(observe, "directory_cache"):
            observe_kwargs["directory_cache"] = directory_cache
        snapshot = observe(**observe_kwargs)
        target = self.adapter.target()
        if snapshot.target != target:
            raise ResourceError(
                "target identity changed during measurement",
                "target_identity_changed",
            )
        category_outcomes = enrich_outcomes(
            tuple(snapshot.category_outcomes), snapshot.resources,
        )
        deep_attribution = snapshot.deep_attribution
        if snapshot.capacity is None and self._terminal_status(
            request, category_outcomes, deep_attribution,
        ) == "cancelled":
            completed = self.clock().astimezone(timezone.utc)
            if deep_attribution is not None:
                deep_attribution = apply_cleanup_guidance(
                    deep_attribution, snapshot.resources,
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
            return StorageScan(
                scan_id=secrets.token_hex(16),
                target=target,
                mode="deep" if deep else "thorough" if thorough else "fast",
                started_at=started,
                completed_at=completed,
                budget_seconds=budget,
                status="cancelled",
                capacity=None,
                resources=ordered,
                attributed_bytes=0,
                unknown_bytes=0,
                reclaimable_bytes=sum(
                    item.reclaimable_bytes for item in snapshot.resources
                ),
                confidence="low",
                category_outcomes=category_outcomes,
                drift=snapshot.drift,
                deep_attribution=deep_attribution,
                capacity_scope_id=getattr(snapshot, "capacity_scope_id", None),
                capacity_pressure=None,
            )
        if snapshot.capacity is None:
            if request.is_cancelled():
                raise ResourceError(
                    "resource measurement was cancelled",
                    "request_cancelled",
                )
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
        capacity_scope_id = getattr(snapshot, "capacity_scope_id", None)
        deep_scope_id = getattr(deep_attribution, "capacity_scope_id", None)
        if deep_attribution is not None:
            deep_attribution = apply_cleanup_guidance(
                deep_attribution, snapshot.resources,
            )
        scope_mismatch = bool(
            deep and deep_attribution is not None
            and not self._deep_scope_matches_capacity(
                deep_attribution, snapshot.capacity,
                capacity_scope_id, deep_scope_id,
            )
        )
        if scope_mismatch:
            deep_attribution = self._with_scope_mismatch(deep_attribution)
            category_outcomes += ({
                "category": "reconciliation",
                "status": "partial",
                "reason": "capacity_scope_mismatch",
            },)
        if deep and deep_attribution is not None and not scope_mismatch:
            reconciliation = deep_attribution.reconciliation
            attributed = reconciliation.accounted_bytes
            unknown = reconciliation.residual_unexplained_bytes
        elif scope_mismatch:
            # Do not combine managed-resource totals with a deep result that
            # explicitly describes a different capacity boundary.
            attributed = 0
            unknown = used
        else:
            attributed = min(raw_attributed, used)
            unknown = max(used - attributed, 0)
        reclaimable = sum(item.reclaimable_bytes for item in snapshot.resources)
        incomplete = any(
            not isinstance(item, dict)
            or item.get("status") not in {"complete", "observed"}
            for item in category_outcomes
        )
        if deep and (
            deep_attribution is None
            or deep_attribution.status != "complete"
        ):
            incomplete = True
        terminal_status = self._terminal_status(
            request, category_outcomes, deep_attribution,
        )
        status = terminal_status or ("partial" if incomplete else "complete")
        confidence = "low" if status != "complete" else (
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
        if deep_attribution is not None:
            reconciliation = deep_attribution.reconciliation
            drift = {
                **(drift or {}),
                "capacity_drift_bytes": reconciliation.capacity_drift_bytes,
                "attributed_drift_bytes": reconciliation.attributed_drift_bytes,
                "capacity_drift_material": (
                    reconciliation.capacity_drift_material
                ),
                "attributed_drift_material": (
                    reconciliation.attributed_drift_material
                ),
                "capacity_drift_threshold_bytes": max(
                    int(reconciliation.used_bytes * 0.01),
                    64 * 1024 * 1024,
                ),
            }
        if raw_attributed > used:
            drift = {
                **(drift or {}),
                "capacity_accounting_overage_bytes": raw_attributed - used,
                "reason": "filesystem_accounting_overlap_or_sparse_storage",
            }
        network_outcome = next((
            str(item.get("status"))
            for item in category_outcomes
            if isinstance(item, dict)
            and item.get("category") == "docker_networks"
        ), "unavailable")
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
            category_outcomes=category_outcomes,
            drift=drift,
            deep_attribution=deep_attribution,
            capacity_scope_id=capacity_scope_id,
            capacity_pressure=network_capacity_pressure(
                snapshot.resources,
                inventory_status=network_outcome,
            ),
        )

    def status(
        self, *, thorough: bool = False, budget_seconds: float = 15,
        progress=None, deep: bool = False, cancelled=False,
        directory_cache: str | None = None,
    ) -> dict:
        try:
            scan = self._scan(
                thorough=thorough,
                budget_seconds=budget_seconds,
                progress=progress,
                focus=None,
                deep=deep,
                cancelled=cancelled,
                directory_cache=directory_cache,
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
            terminal_status = (
                "cancelled" if error.code == "request_cancelled"
                else "disconnected" if error.code in {
                    "remote_disconnected", "measurement_disconnected",
                }
                else "unavailable" if error.code in {
                    "measurement_unavailable", "remote_unreachable",
                }
                else "failed"
            )
            return result(False, "status", status=terminal_status, error=error)

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
