"""MCP adapters for agent-aware source synchronization."""

from __future__ import annotations

from dependencies import ToolDependencies


_service = None


def register(server, dependencies: ToolDependencies) -> None:
    global _service
    _service = dependencies.require("sync_service")
    for tool in (sync_once, sync_status, sync_start, sync_stop):
        server.tool()(tool)


def _require_service():
    if _service is None:
        raise RuntimeError("sync service dependency is not configured")
    return _service


def sync_once(project_dir: str, remote: str, workspace_id: str, request_id: str,
              include: list[str] | None = None) -> dict:
    """Transfer one screened source generation to a disposable remote workspace."""
    try:
        return _require_service().once(
            project_dir, remote=remote, workspace_id=workspace_id,
            request_id=request_id, explicit_includes=tuple(include or ()),
        )
    except Exception:
        return {"ok": False, "status": "failed", "code": "sync_failed",
                "message": "synchronization operation failed"}


def sync_status(project_dir: str, remote: str, workspace_id: str) -> dict:
    """Read bounded local synchronization state."""
    try:
        return _require_service().status(
            project_dir, remote=remote, workspace_id=workspace_id,
        )
    except Exception:
        return {"ok": False, "status": "failed", "code": "sync_failed",
                "message": "synchronization status is unavailable"}


def sync_start(project_dir: str, remote: str, workspace_id: str, mode: str) -> dict:
    """Start live or checkpoint mode without transferring until requested."""
    try:
        return _require_service().start(
            project_dir, remote=remote, workspace_id=workspace_id, mode=mode,
        )
    except Exception:
        return {"ok": False, "status": "failed", "code": "sync_failed",
                "message": "synchronization mode could not be started"}


def sync_stop(project_dir: str, remote: str, workspace_id: str) -> dict:
    """Stop future automatic transfers while retaining accepted state."""
    try:
        return _require_service().stop(
            project_dir, remote=remote, workspace_id=workspace_id,
        )
    except Exception:
        return {"ok": False, "status": "failed", "code": "sync_failed",
                "message": "synchronization mode could not be stopped"}


__all__ = ["register", "sync_once", "sync_status", "sync_start", "sync_stop"]
