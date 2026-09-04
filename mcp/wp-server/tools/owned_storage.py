"""MCP tool adapters for owned storage authority operations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationError,
    OwnedStorageApplicationService,
    build_owned_storage_application_service,
)


_service: Optional[OwnedStorageApplicationService] = None


def get_service() -> OwnedStorageApplicationService:
    global _service
    if _service is None:
        _service = build_owned_storage_application_service()
    return _service


def register(server: Any, dependencies: Any = None) -> None:
    for tool in (owned_storage_capability, owned_storage_status, owned_storage_preview, owned_storage_reclaim):
        server.tool()(tool)


def owned_storage_capability(
    remote: str,
    project_identity: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect platform capability and truthful support status for owned storage authority."""
    try:
        from sandbox.owned_storage_lifecycle.service import build_authority_lifecycle_service
        lifecycle_service = build_authority_lifecycle_service()
        return lifecycle_service.evaluate_capability(remote_identity=remote)
    except Exception as exc:
        return {"ok": False, "code": "internal_indeterminate", "message": str(exc)}


def owned_storage_status(
    remote: str,
    project_identity: str,
    kind: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a bounded page of authority-owned objects for a project and remote."""
    try:
        return get_service().get_status(
            remote_identity=remote,
            project_identity=project_identity,
            kind=kind,
            limit=limit,
            cursor=cursor,
        )
    except OwnedStorageApplicationError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "code": "internal_indeterminate", "message": str(exc)}


def owned_storage_preview(
    remote: str,
    project_identity: str,
    kind: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect retained objects and create an immutable 15-minute reclamation preview."""
    try:
        return get_service().generate_preview(
            remote_identity=remote,
            project_identity=project_identity,
            kind=kind,
            limit=limit,
            cursor=cursor,
        )
    except OwnedStorageApplicationError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "code": "internal_indeterminate", "message": str(exc)}


def owned_storage_reclaim(
    remote: str,
    project_identity: str,
    preview_id: str,
    object_id: str,
    request_id: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Safely reclaim one exact eligible preview candidate."""
    try:
        return get_service().reclaim(
            remote_identity=remote,
            project_identity=project_identity,
            preview_id=preview_id,
            object_id=object_id,
            request_id=request_id,
            confirm=confirm,
        )
    except OwnedStorageApplicationError as exc:
        return {"ok": False, "code": exc.code, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "code": "internal_indeterminate", "message": str(exc)}


__all__ = [
    "register",
    "owned_storage_capability",
    "owned_storage_status",
    "owned_storage_preview",
    "owned_storage_reclaim",
]
