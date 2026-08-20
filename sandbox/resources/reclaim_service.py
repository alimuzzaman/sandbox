"""Tiered, manifest-backed reclamation of managed host storage.

Policy lives in :mod:`sandbox.resources.reclaim` (pure, unit tested); host-side
evidence and mutation live in the shipped probe.  This service is the seam
between them: it classifies, plans, confirms, executes, and reports — and it
never decides a protection rule of its own, so there is exactly one place where
"may this be deleted" is answered.
"""

from __future__ import annotations

from dataclasses import replace as _replace
from datetime import timedelta, timezone
import hashlib
import secrets
import time
from typing import Any, Mapping

from . import reclaim as policy
from .models import (
    CleanupCandidate,
    CleanupItemOutcome,
    CleanupPlan,
    CleanupRun,
    StorageTarget,
    utc_now,
)
from .plans import ResourcePlanError
from .service import ResourceError, result


_TERMINAL_ITEM_STATES = {"removed", "already_absent"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _to_cleanup_candidate(item: policy.ReclaimCandidate) -> CleanupCandidate:
    return CleanupCandidate(
        resource_id=item.identity(),
        kind=item.kind,
        locator=item.locator,
        locator_digest=_digest(item.locator),
        expected_owner_kind="workspace" if item.kind == "worktree" else "sandbox",
        expected_owner_id=item.display_name,
        expected_absence=(),
        expected_size_bytes=item.bytes,
        expected_reclaimable_bytes=item.bytes,
        evidence_digest=item.evidence_digest(),
    )


class ReclaimService:
    """Classify, plan, and execute tiered reclamation for one target."""

    def __init__(self, provider, plan_store, *, target: StorageTarget | None = None,
                 clock=utc_now, monotonic=time.time) -> None:
        self.provider = provider
        self.plan_store = plan_store
        self._target = target
        self.clock = clock or utc_now
        self.monotonic = monotonic

    # -- evidence ---------------------------------------------------------

    def target(self) -> StorageTarget:
        if self._target is not None:
            return self._target
        return self.provider.target()

    def _evidence(self, *, budget_seconds: float,
                  directory_cache: str | None) -> tuple[dict, dict | None]:
        payload = self.provider.inventory(
            budget_seconds=budget_seconds, directory_cache=directory_cache,
        )
        block = payload.get("reclaim")
        if not isinstance(block, Mapping):
            raise ResourceError(
                "host reclaim inventory is unavailable",
                "reclaim_inventory_unavailable", retryable=True,
            )
        return dict(block), payload.get("capacity")

    def inventory(self, *, budget_seconds: float = 30,
                  directory_cache: str | None = None,
                  warn_ratio: float = policy.DEFAULT_WARN_RATIO,
                  critical_ratio: float = policy.DEFAULT_CRITICAL_RATIO,
                  auto_tier: str | None = None) -> dict:
        """Return the categorised report block for ``resources status``."""
        block, capacity = self._evidence(
            budget_seconds=budget_seconds, directory_cache=directory_cache,
        )
        return policy.build_report(
            block, capacity, now=self.monotonic(), warn_ratio=warn_ratio,
            critical_ratio=critical_ratio, auto_tier=auto_tier,
        )

    # -- planning ---------------------------------------------------------

    def _selection(self, tier: str, *, budget_seconds: float,
                   directory_cache: str | None, exclude_kinds=()) -> tuple:
        block, capacity = self._evidence(
            budget_seconds=budget_seconds, directory_cache=directory_cache,
        )
        now = self.monotonic()
        selection = policy.tier_candidates(
            block, tier, now=now, hosted_sites=block.get("hosted_sites") or (),
        )
        if exclude_kinds:
            kept = tuple(
                item for item in selection.candidates
                if item.kind not in exclude_kinds
            )
            dropped = tuple(
                {
                    "kind": item.kind, "locator": item.locator,
                    "display_name": item.display_name, "class": item.lifecycle_class,
                    "reason": "excluded_by_request", "bytes": item.bytes,
                }
                for item in selection.candidates if item.kind in exclude_kinds
            )
            selection = _replace(
                selection,
                candidates=tuple(
                    _replace(item, seq=index + 1)
                    for index, item in enumerate(kept)
                ),
                skipped=selection.skipped + dropped,
            )
        return selection, block, capacity

    def plan(self, tier: str, *, budget_seconds: float = 60,
             directory_cache: str | None = None, exclude_kinds=()) -> dict:
        try:
            policy.tier_rank(tier)
        except policy.ReclaimPolicyError as exc:
            return result(False, "plan", status="failed",
                          error=ResourceError(str(exc), exc.code))
        try:
            selection, block, _capacity = self._selection(
                tier, budget_seconds=budget_seconds,
                directory_cache=directory_cache, exclude_kinds=exclude_kinds,
            )
            target = self.target()
            stored = CleanupPlan.create(
                target, tier,
                tuple(_to_cleanup_candidate(item) for item in selection.candidates),
                selection.skipped,
                now=self.clock(),
                ttl=timedelta(minutes=60),
                metadata={
                    "candidates": [item.to_dict() for item in selection.candidates],
                    "workspace_ids": dict(block.get("workspace_ids") or {}),
                    "tier": tier,
                },
            )
            self.plan_store.save(stored)
        except (ResourceError, ResourcePlanError, OSError, RuntimeError) as exc:
            error = exc if isinstance(exc, (ResourceError, ResourcePlanError)) else (
                ResourceError("reclaim plan failed", "cleanup_failed")
            )
            return result(False, "plan", status="failed", error=error)
        return result(
            True, "plan", status="planned", target=target,
            data={
                "plan_id": stored.plan_id,
                "tier": tier,
                "expires_at": stored.to_dict(public=True)["expires_at"],
                "candidates": [item.to_dict() for item in selection.candidates],
                "skipped": list(selection.skipped),
                "estimated_reclaimable_bytes": selection.estimated_bytes,
                "tier_totals": dict(selection.totals),
                "inventory_status": block.get("status"),
                "truncated": bool(block.get("truncated")),
                "unmeasured_count": int(block.get("unmeasured_count") or 0),
                "requires_confirmation": True,
            },
        )

    # -- execution --------------------------------------------------------

    def _load_for_execution(self, plan_id: str, target: StorageTarget):
        """Begin a plan, resuming one that a previous run left in progress."""
        try:
            return self.plan_store.begin(plan_id, target), False
        except ResourcePlanError as exc:
            if getattr(exc, "code", "") != "plan_already_used":
                raise
            stored = self.plan_store.load(plan_id)
            if stored.state != "in_progress":
                raise
            # An interrupted run is exactly the resumable case: the manifest
            # already records what it intended, and every candidate is
            # revalidated host-side before it is touched again.
            return stored, True

    def cleanup(self, *, tier: str | None = None, plan_id: str | None = None,
                confirm: bool = False, trigger: str = "manual",
                budget_seconds: float = 900,
                directory_cache: str | None = None,
                exclude_kinds=()) -> dict:
        if not confirm:
            return result(
                False, "cleanup", status="refused",
                error=ResourceError(
                    "resource cleanup requires explicit confirmation",
                    "confirmation_required",
                ),
            )
        if not plan_id and not tier:
            return result(
                False, "cleanup", status="refused",
                error=ResourceError("--tier or --plan-id is required",
                                    "invalid_tier"),
            )
        started = self.clock().astimezone(timezone.utc)
        try:
            if not plan_id:
                planned = self.plan(
                    tier, budget_seconds=min(budget_seconds, 120),
                    directory_cache=directory_cache, exclude_kinds=exclude_kinds,
                )
                if not planned.get("ok"):
                    return {**planned, "action": "cleanup", "status": "refused"}
                plan_id = planned["data"]["plan_id"]
            target = self.target()
            stored, resumed = self._load_for_execution(plan_id, target)
        except (ResourcePlanError, ResourceError, OSError, RuntimeError) as exc:
            error = exc if isinstance(exc, (ResourcePlanError, ResourceError)) else (
                ResourceError("cleanup target could not be resolved",
                              "cleanup_failed")
            )
            return result(False, "cleanup", status="refused", error=error)

        candidates = list((stored.metadata or {}).get("candidates") or ())
        workspace_ids = dict((stored.metadata or {}).get("workspace_ids") or {})
        run_id = secrets.token_hex(16)
        try:
            response = self.provider.reclaim(
                [
                    {
                        "seq": item.get("seq"),
                        "kind": item.get("kind"),
                        "locator": item.get("locator"),
                        "bytes": item.get("bytes"),
                        "class": item.get("class"),
                        "tier": item.get("tier"),
                        "reason": item.get("reason"),
                        "expected_mtime": item.get("mtime"),
                        "stop_containers": item.get("stop_containers") or [],
                    }
                    for item in candidates
                ],
                run_id=run_id, trigger=trigger, workspace_ids=workspace_ids,
                budget_seconds=budget_seconds,
            )
        except Exception:
            self._finish(stored.plan_id, "indeterminate")
            return result(
                False, "cleanup", status="indeterminate", target=stored.target,
                error=ResourceError(
                    "cleanup outcome is indeterminate; rescan before any retry",
                    "plan_indeterminate",
                ),
            )
        if not isinstance(response, Mapping) or response.get("ok") is not True:
            reason = (response or {}).get("reason") if isinstance(response, Mapping) else None
            self._finish(stored.plan_id, "indeterminate")
            return result(
                False, "cleanup", status="indeterminate", target=stored.target,
                error=ResourceError(
                    f"host refused the reclaim run: {reason or 'unknown'}",
                    "reclaim_refused",
                ),
            )

        outcomes = []
        by_seq = {item.get("seq"): item for item in candidates}
        reclaimed = 0
        indeterminate = bool(response.get("budget_exhausted"))
        for item in response.get("outcomes") or ():
            planned_item = by_seq.get(item.get("seq")) or {}
            status = str(item.get("status") or "failed")
            if status == "removed":
                reclaimed += int(item.get("bytes") or 0)
            if status == "timed_out":
                indeterminate = True
            outcomes.append(CleanupItemOutcome(
                str(planned_item.get("locator") or item.get("locator") or "unknown"),
                status if status in {
                    "removed", "skipped", "failed", "timed_out", "already_absent",
                } else "failed",
                str(item.get("reason") or "cleanup_failed"),
                int(item.get("bytes") or 0),
                status == "skipped",
                self.clock(),
            ))
        run_status = (
            "indeterminate" if indeterminate
            else "partial" if any(
                item.status not in _TERMINAL_ITEM_STATES for item in outcomes
            ) or len(outcomes) < len(candidates)
            else "completed"
        )
        run = CleanupRun(
            run_id=run_id,
            plan_id=stored.plan_id,
            target=stored.target,
            status=run_status,
            started_at=started,
            completed_at=self.clock().astimezone(timezone.utc),
            planned_bytes=stored.estimated_reclaimable_bytes,
            observed_reclaimed_bytes=reclaimed,
            outcomes=tuple(outcomes),
            capacity_before=response.get("capacity_before"),
            capacity_after=response.get("capacity_after"),
            drift=None,
        )
        try:
            self.plan_store.record_run(run)
        except ResourcePlanError:
            pass
        self._finish(stored.plan_id,
                     "indeterminate" if indeterminate else "completed")
        payload = run.to_dict()
        payload.update({
            "tier": stored.scope,
            "resumed": resumed,
            "trigger": trigger,
            "manifest_path": response.get("manifest_path"),
            "reconciled": response.get("reconciled"),
            "budget_exhausted": bool(response.get("budget_exhausted")),
            "planned_candidates": len(candidates),
            "processed_candidates": len(outcomes),
        })
        return result(
            not indeterminate, "cleanup", status=run_status,
            target=stored.target, data=payload,
            error=ResourceError(
                "cleanup outcome is indeterminate; rescan before any retry",
                "plan_indeterminate",
            ) if indeterminate else None,
        )

    def _finish(self, plan_id: str, state: str) -> None:
        try:
            self.plan_store.finish(plan_id, state)
        except ResourcePlanError:
            pass

    # -- retention --------------------------------------------------------

    def release(self, name: str) -> dict:
        if not policy.valid_lease_name(name):
            return result(False, "release", status="failed",
                          error=ResourceError("workspace name is invalid",
                                              "workspace_identity_invalid"))
        response = self.provider.lease("release", name=name)
        return self._lease_result("release", name, response)

    def set_ttl(self, name: str, duration: str) -> dict:
        if not policy.valid_lease_name(name):
            return result(False, "ttl", status="failed",
                          error=ResourceError("workspace name is invalid",
                                              "workspace_identity_invalid"))
        try:
            seconds = policy.parse_duration(duration)
        except policy.ReclaimPolicyError as exc:
            return result(False, "ttl", status="failed",
                          error=ResourceError(str(exc), exc.code))
        expires_at = policy.iso(
            self.clock().astimezone(timezone.utc) + timedelta(seconds=seconds)
        )
        response = self.provider.lease("set", name=name, expires_at=expires_at)
        return self._lease_result("ttl", name, response,
                                  extra={"expires_at": expires_at,
                                         "ttl_seconds": seconds})

    def leases(self) -> dict:
        response = self.provider.lease("list")
        return self._lease_result("leases", None, response)

    def _lease_result(self, action: str, name: str | None, response,
                      extra: dict | None = None) -> dict:
        if not isinstance(response, Mapping) or response.get("ok") is not True:
            reason = (response or {}).get("reason") if isinstance(response, Mapping) else None
            return result(False, action, status="failed",
                          error=ResourceError(
                              f"workspace lease operation failed: {reason or 'unknown'}",
                              str(reason or "lease_failed")))
        data = {"name": name, "leases": response.get("leases") or {}}
        data.update(extra or {})
        return result(True, action, status="ok", target=self.target(), data=data)

    def reap(self, *, dry_run: bool = True, ttl: str | None = None,
             confirm: bool = False, budget_seconds: float = 900) -> dict:
        """Reclaim expired, not-in-use workspaces and one-shot base targets."""
        if ttl is not None:
            try:
                policy.parse_duration(ttl)
            except policy.ReclaimPolicyError as exc:
                return result(False, "reap", status="failed",
                              error=ResourceError(str(exc), exc.code))
        if dry_run:
            planned = self.plan("all", budget_seconds=min(budget_seconds, 120),
                                exclude_kinds=("runtime",))
            if planned.get("ok"):
                planned["action"] = "reap"
                planned["data"]["dry_run"] = True
            return planned
        if not confirm:
            return result(
                False, "reap", status="refused",
                error=ResourceError("workspace reap requires --confirm",
                                    "confirmation_required"),
            )
        outcome = self.cleanup(tier="all", confirm=True, trigger="reap",
                               budget_seconds=budget_seconds,
                               exclude_kinds=("runtime",))
        outcome["action"] = "reap"
        return outcome


class _RemoteReclaimProvider:
    """Adapt :class:`RemoteResourceAdapter` to the reclaim provider seam."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def target(self) -> StorageTarget:
        return self.adapter.target()

    def inventory(self, *, budget_seconds: float,
                  directory_cache: str | None) -> dict:
        snapshot = self.adapter.observe(
            thorough=False, budget_seconds=budget_seconds, progress=None,
            focus=None, deep=True,
            directory_cache=directory_cache or "auto",
        )
        return {
            "capacity": snapshot.capacity,
            "reclaim": getattr(snapshot, "reclaim", None),
        }

    def reclaim(self, candidates, **kwargs) -> dict:
        return self.adapter.reclaim(candidates, **kwargs)

    def lease(self, op, **kwargs) -> dict:
        return self.adapter.lease(op, **kwargs)


class _LocalReclaimProvider:
    """Adapt the local probe runner to the same seam."""

    def __init__(self, probe, target: StorageTarget) -> None:
        self.probe = probe
        self._target = target

    def target(self) -> StorageTarget:
        return self._target

    def inventory(self, *, budget_seconds: float,
                  directory_cache: str | None) -> dict:
        payload = self.probe.observe_reclaim(
            budget_seconds=budget_seconds,
            directory_cache=directory_cache or "auto",
        )
        return {
            "capacity": payload.get("capacity"),
            "reclaim": payload.get("reclaim"),
        }

    def reclaim(self, candidates, **kwargs) -> dict:
        return self.probe.reclaim(candidates, **kwargs)

    def lease(self, op, **kwargs) -> dict:
        return self.probe.lease(op, **kwargs)


__all__ = [
    "ReclaimService", "_LocalReclaimProvider", "_RemoteReclaimProvider",
]
