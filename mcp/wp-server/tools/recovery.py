"""Read-only scoped recovery planning tools."""
from __future__ import annotations

from app import SANDBOX_ROOT, mcp


def _service():
    from sandbox.recovery.context import recovery_service
    return recovery_service(SANDBOX_ROOT)


@mcp.tool()
def recovery_profiles(remote: str | None = None) -> dict:
    """List committed non-secret recovery profile identifiers."""
    return _service().profiles(remote)


@mcp.tool()
def recovery_plan(remote: str | None = None, profiles: list[str] | None = None) -> dict:
    """Build a side-effect-free recovery plan for all or selected profiles."""
    return _service().plan(tuple(profiles or ()), remote)
