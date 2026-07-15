"""Read-only scoped recovery planning tools."""
from __future__ import annotations

from pathlib import Path

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
def recovery_create(remote: str | None = None, backup_id: str | None = None,
                    profiles: list[str] | None = None,
                    artifacts: dict[str, str] | None = None,
                    confirm: bool = False) -> dict:
    """Capture explicit materialized artifacts; secrets are inherited, never arguments."""
    from sandbox.recovery.errors import RecoveryError, result
    if not confirm:
        return result(False, "create", remote=remote,
                      error=RecoveryError("recovery create requires confirmation", "confirmation_required"))
    if not backup_id:
        return result(False, "create", remote=remote,
                      error=RecoveryError("backup_id is required", "missing_backup_id"))
    if not profiles:
        return result(False, "create", remote=remote,
                      error=RecoveryError("at least one profile is required", "missing_profiles"))
    try:
        materialized = {name: Path(source) for name, source in (artifacts or {}).items()}
    except (TypeError, ValueError):
        return result(False, "create", remote=remote,
                      error=RecoveryError("artifacts must map names to paths", "invalid_artifact"))
    return _service().create(backup_id, materialized, tuple(profiles), confirm=True, remote=remote)


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


@mcp.tool()
def recovery_schedule_plan(remote: str | None = None, profiles: list[str] | None = None) -> dict:
    """Render disabled systemd recovery units; this never installs or enables them."""
    from sandbox.recovery.errors import result
    from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_units
    chosen = tuple(profiles or ()) or tuple(profile.profile_id for profile in _service().catalog.profiles)
    return result(True, "schedule", remote=remote, status="planned", data={
        "units": render_systemd_units(build_schedule_policy("recovery-daily", chosen, "daily", remote=remote))})


@mcp.tool()
def recovery_retention_plan(remote: str | None = None) -> dict:
    """Return a conservative empty retention plan until verified remote sets are supplied."""
    from sandbox.recovery.errors import result
    from sandbox.recovery.retention import build_retention_plan
    plan = build_retention_plan("sets/", ())
    return result(True, "retention", remote=remote, status="planned", data={
        "destination_prefix": plan.destination_prefix, "protected_sets": plan.protected_sets,
        "candidates": plan.candidates, "requires_confirmation": True})
