"""Adopt only identity evidence returned by the authenticated control service."""
from __future__ import annotations

from sandbox.resources.host_memory.models import HEX24
from sandbox.resources.host_memory.remote import RemoteProtocolError, validate_response


def discover_join_identity(name: str, record: dict, request, *, budget_seconds=15,
                           allow_revision_cache: bool = False) -> dict:
    """Read the installed schema-1 status contract without SSH or owner tokens.

    First admission trusts the selected tailnet service. Subsequent adoption
    preserves every existing pin, including the machine identity.
    """
    response = request(record, {"action": "host_memory_status", "remote_name": name,
                                "budget_seconds": budget_seconds})
    service = response.get("service") if isinstance(response, dict) else None
    if not isinstance(service, dict):
        raise RemoteProtocolError("remote_service_ownership_unknown", "service identity is unavailable")
    marker, revision = service.get("ownership_marker"), service.get("runtime_revision")
    if not isinstance(marker, str) or not HEX24.fullmatch(marker):
        raise RemoteProtocolError("remote_service_ownership_unknown", "service ownership is invalid")
    if not isinstance(revision, str) or not HEX24.fullmatch(revision):
        raise RemoteProtocolError("remote_runtime_revision_mismatch", "service revision is invalid")
    state = validate_response(response, marker=marker, revision=revision, action="host_memory_status")
    identity = state.get("target_identity")
    if (not isinstance(identity, str) or not HEX24.fullmatch(identity)
            or state.get("evidence_state") not in {"known", "partial", "unmanaged"}):
        raise RemoteProtocolError("recovery_target_identity_unavailable", "machine identity is unavailable")
    pinned_service = record.get("mcp_service", {})
    pinned_identity = record.get("control_identity", {})
    if not isinstance(pinned_service, dict) or not isinstance(pinned_identity, dict):
        raise RemoteProtocolError("remote_service_ownership_unknown", "registered identity is invalid")
    if 'control_identity' in record and (
            set(pinned_identity) != {'schema_version', 'target_identity'}
            or type(pinned_identity.get('schema_version')) is not int or pinned_identity['schema_version'] != 1
            or not isinstance(pinned_identity.get('target_identity'), str)
            or not HEX24.fullmatch(pinned_identity['target_identity'])):
        raise RemoteProtocolError('remote_target_identity_mismatch', 'registered machine identity is invalid')
    for field, observed, code in (
            ("ownership_marker", marker, "remote_service_ownership_unknown"),
            ("runtime_revision", revision, "remote_runtime_revision_mismatch")):
        if field == 'runtime_revision' and allow_revision_cache:
            continue  # Only the compatible-revision adapter may publish this cache change.
        if field in pinned_service and pinned_service[field] != observed:
            raise RemoteProtocolError(code, "registered service identity changed; adoption refused")
    if pinned_identity and pinned_identity != {"schema_version": 1, "target_identity": identity}:
        raise RemoteProtocolError("remote_target_identity_mismatch", "registered machine identity changed")
    # Preserve service metadata owned by lifecycle operations; adoption fills only
    # the two observed pins needed by the existing strict resource adapter.
    return {"mcp_service": {**pinned_service, "ownership_marker": marker,
                            "runtime_revision": revision},
            "control_identity": {"schema_version": 1, "target_identity": identity}}
