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
import math
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
    redact,
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

    # -- scheduled monitor -----------------------------------------------

    @staticmethod
    def _monitor_policy(policy_config: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the already-resolved monitor policy before host contact.

        Configuration resolution normally calls ``normalize_storage_monitor``.
        The service still validates the small set of values it consumes so a
        direct caller cannot bypass the safe-tier or threshold gate by handing
        in an arbitrary mapping.  In particular, the automatic tier is
        rejected before the provider is asked for an inventory.
        """
        if not isinstance(policy_config, Mapping):
            raise ResourceError(
                "storage monitor policy is invalid", "invalid_schedule_field",
            )

        def ratio(name: str, default: float) -> float:
            value = policy_config.get(name, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ResourceError(
                    f"storage monitor {name} is invalid", "invalid_threshold",
                )
            value = float(value)
            if not math.isfinite(value) or not 0 < value < 1:
                raise ResourceError(
                    f"storage monitor {name} is invalid", "invalid_threshold",
                )
            return value

        warn_ratio = ratio("warn_ratio", policy.DEFAULT_WARN_RATIO)
        critical_ratio = ratio("critical_ratio", policy.DEFAULT_CRITICAL_RATIO)
        auto_ratio_raw = policy_config.get("auto_ratio", critical_ratio)
        auto_ratio = critical_ratio if auto_ratio_raw is None else ratio(
            "auto_ratio", critical_ratio,
        )
        if critical_ratio > warn_ratio or auto_ratio > warn_ratio:
            raise ResourceError(
                "storage monitor thresholds are ordered incorrectly",
                "invalid_threshold_order",
            )

        auto_tier = policy_config.get("auto_tier", "safe")
        # Keep the policy module's public refusal code/message as the source of
        # truth while doing the check before any provider operation.
        if auto_tier != "safe":
            try:
                policy.disk_capacity_pressure(None, auto_tier=auto_tier)
            except policy.ReclaimPolicyError as exc:
                raise ResourceError(str(exc), exc.code) from None
            raise ResourceError("automatic reclamation is limited to the safe tier",
                                "invalid_auto_tier")

        auto_enabled = policy_config.get("auto_enabled", False)
        reap_enabled = policy_config.get("reap_enabled", False)
        if type(auto_enabled) is not bool or type(reap_enabled) is not bool:
            raise ResourceError(
                "storage monitor enable flags are invalid", "invalid_flag",
            )
        return {
            "warn_ratio": warn_ratio,
            "critical_ratio": critical_ratio,
            "auto_ratio": auto_ratio,
            "auto_enabled": auto_enabled,
            "auto_tier": auto_tier,
            "reap_enabled": reap_enabled,
            "reap_ttl": policy_config.get("reap_ttl"),
        }

    @staticmethod
    def _monitor_error(exc: Exception, fallback: str) -> dict[str, str]:
        """Return bounded, non-sensitive error evidence for a run record."""
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not code or len(code) > 64 \
                or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                       for char in code):
            code = fallback
        try:
            message = redact(
                str(exc).replace("\n", " ").replace("\r", " ").strip()
            )
        except Exception:
            message = "operation failed"
        if not message:
            message = "operation failed"
        return {"code": code, "message": message[:240]}

    @staticmethod
    def _monitor_reclaimed_bytes(payload: Mapping[str, Any] | None) -> int:
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return 0
        for field in ("observed_reclaimed_bytes", "reclaimed_bytes"):
            value = data.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return 0

    @staticmethod
    def _monitor_candidate_count(payload: Mapping[str, Any] | None) -> int:
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return 0
        candidates = data.get("candidates")
        if isinstance(candidates, (list, tuple)):
            return len(candidates)
        for field in ("planned_candidates", "processed_candidates"):
            value = data.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return 0

    def monitor(self, policy: Mapping[str, Any], *,
                trigger: str = "manual", dry_run: bool = False,
                budget_seconds: float = 900) -> dict:
        """Run one bounded storage-pressure monitor pass.

        The monitor owns orchestration only.  Capacity classification remains
        in :func:`sandbox.resources.reclaim.disk_capacity_pressure`, cleanup
        remains confirmation-gated through :meth:`cleanup`, and retention
        remains the existing :meth:`reap` path.  A monitor invocation always
        holds the persistent per-target guard and writes one last-run record
        after the evidence/actions have completed.
        """
        try:
            resolved = self._monitor_policy(policy)
        except ResourceError as exc:
            return result(False, "monitor", status="refused", error=exc)

        if trigger not in {"manual", "scheduled"}:
            return result(
                False, "monitor", status="refused",
                error=ResourceError("monitor trigger is invalid", "invalid_trigger"),
            )
        if (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, (int, float))
            or not math.isfinite(float(budget_seconds))
            or not 0 < float(budget_seconds) <= 3600
        ):
            return result(
                False, "monitor", status="refused",
                error=ResourceError(
                    "budget must be between 0 and 3600 seconds", "invalid_budget",
                ),
            )
        if type(dry_run) is not bool:
            return result(
                False, "monitor", status="refused",
                error=ResourceError("dry_run must be a boolean", "invalid_flag"),
            )
        budget = float(budget_seconds)

        try:
            target = self.target()
            target_record = {"kind": target.kind, "name": target.name}
        except Exception as exc:
            error = self._monitor_error(exc, "target_unavailable")
            return result(
                False, "monitor", status="refused",
                error=ResourceError(error["message"], error["code"]),
            )

        # Imported lazily to keep the reclaim service usable by the existing
        # resource paths without making the record store a module dependency at
        # import time.
        from . import monitor as monitor_store

        try:
            lease = monitor_store.monitor_lock(target_record)
        except Exception as exc:
            error = self._monitor_error(exc, "lock_unavailable")
            return result(
                False, "monitor", status="refused", target=target,
                error=ResourceError(error["message"], error["code"]),
            )

        with lease:
            if not lease.acquired:
                return result(
                    True, "monitor", status="skipped", target=target,
                    data={"reason": "lock_held"},
                )

            errors: list[dict[str, str]] = []
            try:
                block, capacity = self._evidence(
                    budget_seconds=budget, directory_cache="cache_only",
                )
            except Exception as exc:
                block, capacity = {}, None
                errors.append(self._monitor_error(exc, "reclaim_inventory_unavailable"))

            # ``disk_capacity_pressure`` is the single classifier used by the
            # status and monitor surfaces.  The tier was checked above, so this
            # call cannot downgrade an unsafe setting after host contact.
            from . import reclaim as reclaim_policy

            try:
                pressure = reclaim_policy.disk_capacity_pressure(
                    capacity,
                    warn_ratio=resolved["warn_ratio"],
                    critical_ratio=resolved["critical_ratio"],
                    auto_tier=resolved["auto_tier"] if resolved["auto_enabled"] else None,
                    auto_ratio=resolved["auto_ratio"],
                )
            except Exception as exc:
                # A normalized policy should make this unreachable.  Keep the
                # run record fail-closed if a direct caller supplied a value
                # that the classifier cannot represent.
                errors.append(self._monitor_error(exc, "capacity_classification_failed"))
                pressure = reclaim_policy.disk_capacity_pressure(None)
                pressure.update({
                    "warn_ratio": resolved["warn_ratio"],
                    "critical_ratio": resolved["critical_ratio"],
                    "auto_ratio": resolved["auto_ratio"],
                })

            level = str(pressure.get("level") or "unknown")
            auto_eligible = bool(
                resolved["auto_enabled"] and pressure.get("auto_eligible")
            )
            auto = {
                "enabled": resolved["auto_enabled"],
                "eligible": auto_eligible,
                "tier": resolved["auto_tier"] if resolved["auto_enabled"] else None,
                "ran": False,
                "reclaimed_bytes": 0,
                "run_id": None,
                "reason": "disabled" if not resolved["auto_enabled"] else None,
            }

            if resolved["auto_enabled"] and not auto_eligible:
                auto["reason"] = (
                    "capacity_unknown" if level == "unknown"
                    else "threshold_not_reached"
                )
            elif resolved["auto_enabled"] and dry_run:
                auto["reason"] = "dry_run"
            elif auto_eligible:
                try:
                    cleanup_payload = self.cleanup(
                        tier="safe", confirm=True, trigger="scheduled_auto",
                        budget_seconds=budget,
                        directory_cache="cache_only",
                    )
                    auto["ran"] = True
                    auto["reclaimed_bytes"] = self._monitor_reclaimed_bytes(
                        cleanup_payload,
                    )
                    data = cleanup_payload.get("data")
                    if isinstance(data, Mapping) and isinstance(data.get("run_id"), str):
                        auto["run_id"] = data["run_id"]
                    action_status = str(cleanup_payload.get("status") or "")
                    if cleanup_payload.get("ok") and action_status == "completed":
                        auto["reason"] = "completed"
                    elif action_status == "partial":
                        auto["reason"] = "partial"
                    elif action_status:
                        auto["reason"] = action_status
                    else:
                        error = cleanup_payload.get("error")
                        auto["reason"] = (
                            error.get("code") if isinstance(error, Mapping)
                            else "cleanup_failed"
                        )
                        if isinstance(error, Mapping):
                            errors.append({
                                "code": str(error.get("code") or "cleanup_failed")
                                .replace("\n", " ").replace("\r", " ")[:64],
                                "message": redact(
                                    str(error.get("message") or "cleanup failed")
                                    .replace("\n", " ").replace("\r", " ")
                                )[:240],
                            })
                except Exception as exc:
                    auto["reason"] = "cleanup_failed"
                    errors.append(self._monitor_error(exc, "cleanup_failed"))

            reap_dry_run = bool(dry_run or not resolved["reap_enabled"])
            reap = {
                "enabled": resolved["reap_enabled"],
                "dry_run": reap_dry_run,
                "candidates": 0,
                "reclaimed_bytes": 0,
                "reason": "dry_run" if reap_dry_run else None,
            }
            try:
                reap_payload = self.reap(
                    dry_run=reap_dry_run,
                    ttl=resolved["reap_ttl"],
                    confirm=not reap_dry_run,
                    budget_seconds=budget,
                    directory_cache="cache_only",
                )
                reap["candidates"] = self._monitor_candidate_count(reap_payload)
                reap["reclaimed_bytes"] = self._monitor_reclaimed_bytes(reap_payload)
                action_status = str(reap_payload.get("status") or "")
                if reap_payload.get("ok") and action_status in {"planned", "completed"}:
                    reap["reason"] = "dry_run" if reap_dry_run else "completed"
                elif action_status == "partial":
                    reap["reason"] = "partial"
                elif action_status:
                    reap["reason"] = action_status
                else:
                    error = reap_payload.get("error")
                    reap["reason"] = (
                        error.get("code") if isinstance(error, Mapping)
                        else "reap_failed"
                    )
                    if isinstance(error, Mapping):
                        errors.append({
                            "code": str(error.get("code") or "reap_failed")
                            .replace("\n", " ").replace("\r", " ")[:64],
                            "message": redact(
                                str(error.get("message") or "reap failed")
                                .replace("\n", " ").replace("\r", " ")
                            )[:240],
                        })
            except Exception as exc:
                reap["reason"] = "reap_failed"
                errors.append(self._monitor_error(exc, "reap_failed"))

            try:
                stamped = self.clock().astimezone(timezone.utc)
                at = stamped.isoformat().replace("+00:00", "Z")
            except Exception:
                at = utc_now().isoformat().replace("+00:00", "Z")
            record = {
                "schema": 1,
                "target": target_record,
                "at": at,
                "trigger": trigger,
                "level": level if level in {"normal", "warning", "critical", "unknown"}
                else "unknown",
                "free_bytes": pressure.get("free_bytes"),
                "total_bytes": pressure.get("total_bytes"),
                "free_ratio": pressure.get("free_ratio"),
                "warn_ratio": resolved["warn_ratio"],
                "critical_ratio": resolved["critical_ratio"],
                "auto_ratio": resolved["auto_ratio"],
                "threshold_crossed": pressure.get("threshold_crossed"),
                "guidance": str(pressure.get("guidance") or "capacity is unmeasured; rerun with --refresh"),
                "auto": auto,
                "reap": reap,
                "inventory_status": str(block.get("status") or "unknown"),
                "errors": errors,
            }

            try:
                monitor_store.write_record(record)
            except Exception as exc:
                errors.append(self._monitor_error(exc, "record_write_failed"))
                record["errors"] = errors

            status = record["level"]
            ok = status in {"normal", "warning"} and not errors
            first_error = errors[0] if errors else None
            return result(
                ok, "monitor", status=status, target=target, data=record,
                error=(
                    ResourceError(first_error["message"], first_error["code"])
                    if first_error else None
                ),
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
             confirm: bool = False, budget_seconds: float = 900,
             directory_cache: str | None = None) -> dict:
        """Reclaim expired, not-in-use workspaces and one-shot base targets."""
        if ttl is not None:
            try:
                policy.parse_duration(ttl)
            except policy.ReclaimPolicyError as exc:
                return result(False, "reap", status="failed",
                              error=ResourceError(str(exc), exc.code))
        if dry_run:
            planned = self.plan("all", budget_seconds=min(budget_seconds, 120),
                                directory_cache=directory_cache,
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
                               directory_cache=directory_cache,
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
