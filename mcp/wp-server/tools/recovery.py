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


@mcp.tool()
def recovery_list(remote: str | None = None) -> dict:
    """List complete manifests and incomplete recovery objects without mutation."""
    return _service().list(remote)


@mcp.tool()
def recovery_verify(backup_id: str, remote: str | None = None) -> dict:
    """Download and verify a recovery manifest and ciphertext hash without restore."""
    return _service().verify(backup_id, remote)


@mcp.tool()
def recovery_create(remote: str | None = None, confirm: bool = False) -> dict:
    """Request a configured recovery capture; secrets are inherited only, never arguments."""
    from sandbox.recovery.errors import RecoveryError, result
    if not confirm:
        return result(False, "create", remote=remote,
                      error=RecoveryError("recovery create requires confirmation", "confirmation_required"))
    return result(False, "create", remote=remote, error=RecoveryError(
        "profile capture requires a configured remote adapter", "recovery_not_configured"))


@mcp.tool()
def recovery_restore_plan(backup_id: str, remote: str | None = None, profiles: list[str] | None = None) -> dict:
    """Build a non-mutating restore plan; it never writes a target."""
    return _service().restore_plan(backup_id, tuple(profiles or ()), remote=remote)


@mcp.tool()
def recovery_restore_apply(backup_id: str, remote: str | None = None, confirm: bool = False) -> dict:
    """Reserved protected operation; live restore adapters are not configured by this tool."""
    from sandbox.recovery.errors import RecoveryError, result
    if not confirm:
        return result(False, "restore", remote=remote,
                      error=RecoveryError("restore apply requires confirmation", "confirmation_required"))
    return result(False, "restore", remote=remote, error=RecoveryError(
        "restore apply requires disposable target adapters", "recovery_not_configured"))
