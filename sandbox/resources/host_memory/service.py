"""Controller-owned planning and strict remote lifecycle orchestration."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import HostMemoryStatusProjection, RemoteSwapState, parse_utc
from .repository import RepositoryError


def envelope(action, status, *, target=None, data=None, error=None):
    return {
        "schema_version": 1,
        "ok": error is None and status not in {"refused", "partial", "failed", "rollback_incomplete"},
        "action": action,
        "status": status,
        "target": target,
        "data": data or {},
        "error": error,
    }


def failure(action, exc, target=None, status="refused"):
    code = getattr(exc, "code", None)
    if not code:
        candidate = str(exc)
        code = candidate if candidate.replace("_", "").isalnum() else "response_invalid"
    return envelope(
        action,
        status,
        target=target,
        error={
            "code": str(code)[:64],
            "message": str(exc).replace("\n", " ")[:240],
            "retryable": code in {"remote_unreachable", "response_invalid"},
        },
    )


class HostMemoryService:
    """Internal status adapter. Public consumers receive only value objects."""

    def __init__(self, remote, *, repo=None, now=None):
        self._remote = remote
        self._repo = repo
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._plans = {}

    @property
    def target(self):
        return {"kind": "remote", "name": self._remote.name}

    def status(self, budget_seconds=15):
        try:
            data = self._remote.call("host_memory_status", budget_seconds=budget_seconds)
        except Exception as exc:
            return failure("swap-status", exc, self.target, "failed")
        try:
            data = RemoteSwapState.from_dict(data, require_digest=True).to_dict()
        except (TypeError, ValueError):
            return failure("swap-status", RepositoryError("response_invalid"), self.target, "failed")
        return envelope(
            "swap-status",
            "complete" if data.get("evidence_state") == "known" else "partial",
            target=self.target,
            data=data,
            error=None if data.get("evidence_state") == "known" else {
                "code": "evidence_partial",
                "message": "host evidence is incomplete",
                "retryable": True,
            },
        )

    def projection(self, status):
        mem = status.get("memory") or {}
        areas = status.get("swap_areas") or []
        monitor = status.get("monitor") or {}
        return HostMemoryStatusProjection(
            target_identity=str(status.get("target_identity", "unknown")),
            observed_at=str(status.get("observed_at", "")),
            evidence_state=str(status.get("evidence_state", "unknown")),
            memory_total_bytes=mem.get("total_bytes"),
            memory_available_bytes=mem.get("available_bytes"),
            swap_total_bytes=sum(a.get("total_bytes", 0) for a in areas),
            swap_used_bytes=sum(a.get("used_bytes", 0) for a in areas),
            ownership=str(status.get("ownership", "unknown")),
            monitor_freshness=str(monitor.get("freshness", "unknown")),
            sustained_swap_use=monitor.get("sustained_swap_use"),
            pressure_state=str(monitor.get("pressure_state", "unknown")),
            operation_block=(status.get("operation_block") or {}).get("reason"),
        )

    def plan(self, size_gib=4, budget_seconds=15):
        """Build one deterministic controller-owned enable plan from status evidence.

        Read-only: the only remote action is ``host_memory_status``. There is no
        remote plan action and no provider mutation on this path.
        """
        from .policy import PolicyRefusal, build_plan

        observed = self.status(budget_seconds=budget_seconds)
        state = observed.get("data") or {}
        target = {
            "remote_name": self._remote.name,
            "target_identity": str(state.get("target_identity", "unknown")),
            "service_ownership_marker": str(getattr(self._remote, "marker", "")),
            "runtime_revision": str(getattr(self._remote, "revision", "")),
        }
        try:
            plan = build_plan("enable", target, state, size_gib=size_gib, now=self._now())
        except PolicyRefusal as exc:
            return failure("swap-plan", exc, self.target)
        self._plans[plan["plan_id"]] = plan
        if self._repo is not None:
            self._repo.save_plan(plan)
        return envelope("swap-plan", plan["state"], target=self.target, data=plan)

    def apply(self, plan_or_id, *, confirmed=False, operation_id=None, budget_seconds=300):
        """Orchestrate protected host-memory apply with normative outcomes."""
        from .models import canonical_digest
        from .policy import PolicyRefusal, parse_utc

        if confirmed is not True:
            return failure(
                "swap-apply",
                PolicyRefusal("confirmation_required", "exact confirmation is required"),
                self.target,
            )

        plan = None
        if isinstance(plan_or_id, dict):
            plan = plan_or_id
        elif isinstance(plan_or_id, str):
            if plan_or_id in self._plans:
                plan = self._plans[plan_or_id]
            elif self._repo is not None:
                try:
                    plan = self._repo.load_plan(plan_or_id)
                except Exception as exc:
                    return failure("swap-apply", exc, self.target)
            else:
                return failure(
                    "swap-apply",
                    PolicyRefusal("plan_not_found", "canonical plan not found in repository"),
                    self.target,
                )
        else:
            return failure(
                "swap-apply",
                PolicyRefusal("plan_not_found", "canonical plan identity is invalid"),
                self.target,
            )

        try:
            if parse_utc(plan["expires_at"]) <= self._now():
                return failure(
                    "swap-apply",
                    PolicyRefusal("plan_expired", "plan has expired"),
                    self.target,
                )
        except Exception:
            return failure(
                "swap-apply",
                PolicyRefusal("plan_expired", "plan expiry is missing or invalid"),
                self.target,
            )

        target_info = plan.get("target") or {}
        canonical_plan = {
            "plan_id": plan["plan_id"],
            "operation": plan.get("operation", "enable"),
            "target_identity": target_info.get("target_identity", ""),
            "service_ownership_marker": target_info.get("service_ownership_marker", getattr(self._remote, "marker", "")),
            "runtime_revision": target_info.get("runtime_revision", getattr(self._remote, "revision", "")),
            "expires_at": plan["expires_at"],
            "observation_digest": plan["observation_digest"],
            "effective_policy": plan.get("effective_policy") or {},
            "intended_artifact_digests": plan.get("intended_artifact_digests") or [],
            "rollback_scope": plan.get("rollback_scope") or [],
        }

        op_id = operation_id or canonical_digest({
            "plan_id": plan["plan_id"],
            "target_identity": canonical_plan["target_identity"],
        })

        try:
            result = self._remote.call(
                "host_memory_apply",
                operation_id=op_id,
                plan=canonical_plan,
                confirmed=True,
                budget_seconds=budget_seconds,
            )
        except Exception as exc:
            return failure("swap-apply", exc, self.target, "failed")

        outcome = result.get("status", "applied")
        err = result.get("error")
        return envelope("swap-apply", outcome, target=self.target, data=result, error=err)

    def history(self, *, since=None, until=None, limit=288, budget_seconds=15):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            err = {"code": "invalid_limit", "message": "limit must be between 1 and 1000", "retryable": False}
            return envelope("swap-history", "refused", target=self.target, data={}, error=err)
        try:
            start = parse_utc(since) if since is not None else None
            end = parse_utc(until) if until is not None else None
        except (TypeError, ValueError):
            err = {"code": "invalid_range", "message": "timestamps must be valid UTC", "retryable": False}
            return envelope("swap-history", "refused", target=self.target, data={}, error=err)
        if start is not None and end is not None and start > end:
            err = {"code": "invalid_range", "message": "since cannot be after until", "retryable": False}
            return envelope("swap-history", "refused", target=self.target, data={}, error=err)
        try:
            response = self._remote.call(
                "host_memory_history",
                since=since,
                until=until,
                limit=limit,
                budget_seconds=budget_seconds,
            )
            status = "complete" if response.get("complete", True) else "partial"
            return envelope("swap-history", status, target=self.target, data=response, error=None)
        except Exception as exc:
            return failure("swap-history", exc, target=self.target, status="failed")
