"""MCP adapters for agent-aware source synchronization."""

from __future__ import annotations

from dependencies import ToolDependencies
from sandbox.sync.service import SyncServiceError


_service = None


def register(server, dependencies: ToolDependencies) -> None:
    global _service
    _service = dependencies.require("sync_service")
    for tool in (sync_once, sync_status, sync_start, sync_stop, sync_resolve):
        server.tool()(tool)


def _require_service():
    if _service is None:
        raise RuntimeError("sync service dependency is not configured")
    return _service


def _failure(message: str, exc: Exception) -> dict:
    code = exc.code if isinstance(exc, SyncServiceError) else "sync_failed"
    return {"ok": False, "status": "failed", "code": code, "message": message}


def sync_once(project_dir: str, remote: str, workspace_id: str, request_id: str,
              include: list[str] | None = None, checkpoint: bool = False,
              participant_id: str | None = None) -> dict:
    """Transfer one screened source generation to a disposable remote workspace."""
    try:
        return _require_service().once(
            project_dir, remote=remote, workspace_id=workspace_id,
            request_id=request_id, explicit_includes=tuple(include or ()),
            checkpoint=checkpoint, participant_id=participant_id,
        )
    except Exception as exc:
        return _failure("synchronization operation failed", exc)


def sync_status(project_dir: str, remote: str, workspace_id: str) -> dict:
    """Read bounded local synchronization state."""
    try:
        return _require_service().status(
            project_dir, remote=remote, workspace_id=workspace_id,
        )
    except Exception as exc:
        return _failure("synchronization status is unavailable", exc)


def sync_start(project_dir: str, remote: str, workspace_id: str, mode: str,
               participant_id: str | None = None) -> dict:
    """Start live or checkpoint mode without transferring until requested."""
    try:
        return _require_service().start(
            project_dir, remote=remote, workspace_id=workspace_id, mode=mode,
            participant_id=participant_id,
        )
    except Exception as exc:
        return _failure("synchronization mode could not be started", exc)


def sync_stop(project_dir: str, remote: str, workspace_id: str,
              participant_id: str | None = None) -> dict:
    """Stop future automatic transfers while retaining accepted state."""
    try:
        return _require_service().stop(
            project_dir, remote=remote, workspace_id=workspace_id,
            participant_id=participant_id,
        )
    except Exception as exc:
        return _failure("synchronization mode could not be stopped", exc)


def sync_resolve(project_dir: str, remote: str, workspace_id: str,
                 resolution: str, confirm: bool,
                 participant_id: str | None = None) -> dict:
    """Resolve divergence only through the explicit confirmation boundary."""
    try:
        return _require_service().resolve(
            project_dir, remote=remote, workspace_id=workspace_id,
            resolution=resolution, confirm=confirm,
            participant_id=participant_id,
        )
    except Exception as exc:
        return _failure("synchronization divergence was not resolved", exc)


__all__ = [
    "register", "sync_once", "sync_status", "sync_start", "sync_stop",
    "sync_resolve",
]
