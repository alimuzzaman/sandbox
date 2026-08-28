"""Thin MCP adapters for shared host resource monitoring and cleanup policy."""

from __future__ import annotations


_service_factory = None
_reclaim_service_factory = None


def _service(remote: str | None):
    if _service_factory is None:
        raise RuntimeError("resource service dependency is not configured")
    return _service_factory(remote)


def _reclaim_service(remote: str | None):
    if _reclaim_service_factory is None:
        raise RuntimeError("reclaim service dependency is not configured")
    return _reclaim_service_factory(remote)


def _refusal(action: str, message: str, code: str, *, status: str = "failed") -> dict:
    """Return the public resource-service refusal envelope without a provider call."""
    from sandbox.resources.service import ResourceError, result

    return result(False, action, status=status, error=ResourceError(message, code))


def _valid_tier(tier: str, *, action: str) -> dict | None:
    """Reject an unknown tier before constructing a reclaim service or planning."""
    from sandbox.resources import reclaim

    try:
        reclaim.tier_rank(tier)
    except reclaim.ReclaimPolicyError as exc:
        return _refusal(action, str(exc), exc.code, status="refused")
    return None


def resource_status(
    remote: str | None = None,
    thorough: bool = False,
    deep: bool = False,
    budget_seconds: float = 15,
    cancelled: bool = False,
    fast: bool = False,
    refresh: bool = False,
) -> dict:
    """Inspect local or named-remote host storage without mutation.

    `fast` answers from the cached host directory index and never walks a
    filesystem; `refresh` rebuilds that index. They are mutually exclusive.
    """
    if fast and refresh:
        from sandbox.resources.service import ResourceError, result
        return result(
            False,
            "status",
            status="failed",
            error=ResourceError(
                "fast and refresh are mutually exclusive", "invalid_mode",
            ),
        )
    from sandbox.resources.models import resource_cancellation_signal

    kwargs = {
        "thorough": (thorough or deep) and not fast,
        "budget_seconds": budget_seconds,
        "deep": deep or fast or refresh,
    }
    kwargs["cancelled"] = resource_cancellation_signal(cancelled)
    if fast or refresh:
        kwargs["directory_cache"] = "cache_only" if fast else "refresh"
    return _service(remote).status(
        **kwargs,
    )


def resource_cleanup_plan(
    scope: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    thorough: bool = True,
    budget_seconds: float = 60,
) -> dict:
    """Create a read-only scope or tier cleanup plan.

    Exactly one of ``scope`` (the legacy cache/stale planner) and ``tier``
    (safe/tmp/all reclamation) is required.
    """
    if scope is not None and tier is not None:
        return _refusal(
            "plan", "scope and tier are mutually exclusive", "invalid_mode",
        )
    if scope is None and tier is None:
        return _refusal(
            "plan", "scope or tier is required", "invalid_scope",
        )
    if tier is not None:
        invalid = _valid_tier(tier, action="plan")
        if invalid is not None:
            return invalid
        return _reclaim_service(remote).plan(
            tier, budget_seconds=budget_seconds,
        )
    return _service(remote).plan(
        scope,
        thorough=thorough,
        budget_seconds=budget_seconds,
    )


def resource_cleanup_apply(
    plan_id: str | None = None,
    tier: str | None = None,
    remote: str | None = None,
    confirm: bool = False,
) -> dict:
    """Apply one scope plan or plan-and-apply a reclamation tier.

    Confirmation is checked before any planner/provider is constructed. A tier
    call is intentionally manual only; this tool exposes no automatic cleanup
    path.
    """
    if not confirm:
        return _refusal(
            "cleanup", "resource cleanup requires explicit confirmation",
            "confirmation_required", status="refused",
        )
    if plan_id is not None and tier is not None:
        return _refusal(
            "cleanup", "plan_id and tier are mutually exclusive", "invalid_mode",
            status="refused",
        )
    if plan_id is None and tier is None:
        return _refusal(
            "cleanup", "plan_id or tier is required", "invalid_mode",
            status="refused",
        )
    if tier is not None:
        invalid = _valid_tier(tier, action="cleanup")
        if invalid is not None:
            return invalid
        return _reclaim_service(remote).cleanup(tier=tier, confirm=True)
    return _service(remote).cleanup(plan_id, confirm=True)


def register(server, dependencies) -> None:
    """Register resource tools against an explicitly supplied service factory."""
    global _service_factory, _reclaim_service_factory
    _service_factory = dependencies.require("resource_service_factory")
    _reclaim_service_factory = dependencies.require("reclaim_service_factory")
    for function in (
        resource_status,
        resource_cleanup_plan,
        resource_cleanup_apply,
    ):
        server.tool()(function)
