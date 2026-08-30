"""Strict fixed-action host-memory protocol adapter."""

from __future__ import annotations

import json
from typing import Callable, Mapping

from .models import HEX24, HEX64, bounded

ACTIONS = frozenset({"host_memory_status", "host_memory_history", "host_memory_apply"})


class RemoteProtocolError(RuntimeError):
    def __init__(self, code, message): super().__init__(message); self.code=code


def validate_request(payload):
    if not isinstance(payload, dict): raise RemoteProtocolError("response_invalid", "request must be an object")
    action=payload.get("action")
    if action not in ACTIONS: raise RemoteProtocolError("remote_swap_protocol_mismatch", "unsupported host-memory action")
    allowed={
        "host_memory_status":{"action","remote_name","budget_seconds"},
        "host_memory_history":{"action","remote_name","since","until","limit","budget_seconds"},
        "host_memory_apply":{"action","remote_name","operation_id","plan","confirmed","budget_seconds"},
    }[action]
    if set(payload)-allowed: raise RemoteProtocolError("response_invalid", "unknown host-memory request field")
    if not isinstance(payload.get("remote_name"), str) or not payload["remote_name"]:
        raise RemoteProtocolError("remote_required", "registered remote is required")
    if action == "host_memory_history":
        limit=payload.get("limit",288)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1000: raise RemoteProtocolError("invalid_limit","history limit must be 1 through 1000")
    if action == "host_memory_apply":
        if payload.get("confirmed") is not True: raise RemoteProtocolError("confirmation_required","exact plan confirmation is required")
        plan=payload.get("plan"); op=payload.get("operation_id")
        if not isinstance(plan,dict) or not HEX64.fullmatch(str(plan.get("plan_id",""))) or not HEX64.fullmatch(str(op or "")):
            raise RemoteProtocolError("plan_not_found","canonical plan identity is invalid")
        canonical={"plan_id","operation","target_identity","service_ownership_marker","runtime_revision","expires_at","observation_digest","effective_policy","intended_artifact_digests","rollback_scope"}
        if set(plan)!=canonical: raise RemoteProtocolError("response_invalid","canonical plan fields do not match")
        if not HEX24.fullmatch(str(plan["service_ownership_marker"])) or not HEX24.fullmatch(str(plan["runtime_revision"])):
            raise RemoteProtocolError("remote_runtime_revision_mismatch","service evidence is invalid")
    return bounded(payload,64*1024)


def validate_response(response, *, marker, revision):
    if not isinstance(response,dict) or response.get("resource_schema")!=1:
        raise RemoteProtocolError("remote_swap_protocol_mismatch","resource protocol is unavailable")
    if response.get("host_memory_schema")!=1 or response.get("transport")!="control":
        raise RemoteProtocolError("remote_swap_protocol_mismatch","host-memory protocol is unavailable")
    service=response.get("service") or {}
    if service.get("ownership_marker")!=marker: raise RemoteProtocolError("remote_service_ownership_unknown","service ownership does not match")
    if service.get("runtime_revision")!=revision: raise RemoteProtocolError("remote_runtime_revision_mismatch","runtime revision does not match")
    result=response.get("result")
    if not isinstance(result,dict): raise RemoteProtocolError("response_invalid","remote response is invalid")
    return bounded(result)


class HostMemoryRemote:
    def __init__(self, remote_name, remote_record, request:Callable):
        self.name=remote_name; self.record=remote_record; self.request=request
        service=remote_record.get("mcp_service") or {}
        self.marker=service.get("ownership_marker",""); self.revision=service.get("runtime_revision","")
        if not HEX24.fullmatch(self.marker): raise RemoteProtocolError("remote_service_ownership_unknown","remote service ownership is unavailable")
        if not HEX24.fullmatch(self.revision): raise RemoteProtocolError("remote_runtime_revision_mismatch","remote runtime revision is unavailable")

    def call(self, action, **fields):
        payload=validate_request({"action":action,"remote_name":self.name,**fields})
        try: response=self.request(self.record,payload)
        except Exception as exc: raise RemoteProtocolError("remote_unreachable","remote control endpoint is unreachable") from exc
        return validate_response(response,marker=self.marker,revision=self.revision)
