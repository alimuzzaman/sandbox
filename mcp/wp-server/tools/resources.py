"""Thin MCP adapters for shared host resource monitoring and cleanup policy."""

from __future__ import annotations


_service_factory = None


def _service(remote: str | None):
    if _service_factory is None:
        raise RuntimeError("resource service dependency is not configured")
    return _service_factory(remote)


def resource_status(
    remote: str | None = None,
    thorough: bool = False,
    budget_seconds: float = 15,
) -> dict:
    """Inspect local or named-remote host storage without mutation."""
    return _service(remote).status(
        thorough=thorough,
        budget_seconds=budget_seconds,
    )


def resource_cleanup_plan(
    scope: str,
    remote: str | None = None,
    thorough: bool = True,
    budget_seconds: float = 60,
) -> dict:
    """Create a read-only, target-bound cache or stale-resource cleanup plan."""
    return _service(remote).plan(
        scope,
        thorough=thorough,
        budget_seconds=budget_seconds,
    )


def resource_cleanup_apply(
    plan_id: str,
    remote: str | None = None,
    confirm: bool = False,
) -> dict:
    """Apply one current plan; confirm must be true and candidates are revalidated."""
    if not confirm:
        from sandbox.resources.service import ResourceError, result
        return result(
            False,
            "cleanup",
            status="refused",
            error=ResourceError(
                "resource cleanup requires explicit confirmation",
                "confirmation_required",
            ),
        )
    return _service(remote).cleanup(plan_id, confirm=True)


def register(server, dependencies) -> None:
    """Register resource tools against an explicitly supplied service factory."""
    global _service_factory
    _service_factory = dependencies.require("resource_service_factory")
    for function in (
        resource_status,
        resource_cleanup_plan,
        resource_cleanup_apply,
    ):
        server.tool()(function)
