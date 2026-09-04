"""Owned storage authority protocol codecs and envelope validation."""

import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Tuple

PROTOCOL_VERSION = "owned-storage-authority-v1"
MAX_CONTROL_FRAME_BYTES = 65536  # 64 KiB

VALID_OPERATIONS: Set[str] = {
    "capability",
    "policy_set",
    "publish",
    "materialize",
    "reference_open",
    "reference_close",
    "status",
    "preview",
    "cleanup",
    "reconcile",
}

REQUEST_TOP_LEVEL_FIELDS: Set[str] = {
    "protocol",
    "operation",
    "request_id",
    "request_digest",
    "remote_identity",
    "project_identity",
    "authorization",
    "qualification",
    "deadline_unix_ms",
    "input",
}

AUTHORIZATION_FIELDS: Set[str] = {
    "authorization_id",
    "controller_epoch",
    "sequence",
    "caller_identity_digest",
    "application_policy_digest",
    "policy_generation",
    "promotion_id",
    "authority_binding_id",
    "binding_generation",
    "expires_at",
}

QUALIFICATION_FIELDS: Set[str] = {
    "admission_id",
    "evidence_candidate_id",
    "fixture_id",
}


class StorageProtocolError(Exception):
    """Protocol encoding, decoding, or schema validation error."""


def _check_no_floats_or_invalid_types(obj: Any) -> None:
    if isinstance(obj, float):
        raise StorageProtocolError(f"Protocol forbids float values: {obj}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise StorageProtocolError(f"Dictionary keys must be strings: {k}")
            _check_no_floats_or_invalid_types(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _check_no_floats_or_invalid_types(item)


def _duplicate_key_check_hook(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    for key, value in pairs:
        if key in res:
            raise StorageProtocolError(f"Duplicate key in JSON control frame: {key}")
        res[key] = value
    return res


def canonical_json_dumps(obj: Any) -> bytes:
    """Serializes obj to canonical UTF-8 JSON bytes: sorted keys, no whitespace, no floats."""
    _check_no_floats_or_invalid_types(obj)
    dumped = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return dumped.encode("utf-8")


def canonical_json_loads(raw: bytes) -> Any:
    """Parses raw UTF-8 JSON bytes checking for size, duplicate keys, and no floats."""
    if len(raw) > MAX_CONTROL_FRAME_BYTES:
        raise StorageProtocolError(
            f"Oversized control frame ({len(raw)} bytes > limit {MAX_CONTROL_FRAME_BYTES})"
        )
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_duplicate_key_check_hook, parse_float=lambda x: float(x))
    except UnicodeDecodeError as exc:
        raise StorageProtocolError(f"Invalid UTF-8 control frame: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StorageProtocolError(f"Malformed JSON control frame: {exc}") from exc

    _check_no_floats_or_invalid_types(parsed)
    return parsed


def compute_request_digest(request: Dict[str, Any]) -> str:
    """Computes SHA-256 canonical digest of normalized request excluding transport-only fields."""
    normalized = {
        "protocol": request.get("protocol"),
        "operation": request.get("operation"),
        "request_id": request.get("request_id"),
        "remote_identity": request.get("remote_identity"),
        "project_identity": request.get("project_identity"),
        "authorization": request.get("authorization"),
        "qualification": request.get("qualification"),
        "input": request.get("input"),
    }
    encoded = canonical_json_dumps(normalized)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_request_schema(req: Dict[str, Any]) -> None:
    if not isinstance(req, dict):
        raise StorageProtocolError("Request must be a JSON object")

    extra_fields = set(req.keys()) - REQUEST_TOP_LEVEL_FIELDS
    if extra_fields:
        raise StorageProtocolError(f"Unknown field(s) in request: {sorted(extra_fields)}")

    if req.get("protocol") != PROTOCOL_VERSION:
        raise StorageProtocolError(f"Invalid protocol version: {req.get('protocol')}")

    op = req.get("operation")
    if not isinstance(op, str) or op not in VALID_OPERATIONS:
        raise StorageProtocolError(f"Unknown or missing operation: {op}")

    if not isinstance(req.get("request_id"), str) or not req.get("request_id"):
        raise StorageProtocolError("Missing or invalid request_id")

    if not isinstance(req.get("remote_identity"), str) or not req.get("remote_identity"):
        raise StorageProtocolError("Missing or invalid remote_identity")

    if not isinstance(req.get("project_identity"), str) or not req.get("project_identity"):
        raise StorageProtocolError("Missing or invalid project_identity")

    auth = req.get("authorization")
    if not isinstance(auth, dict):
        raise StorageProtocolError("Missing or invalid authorization object")

    extra_auth = set(auth.keys()) - AUTHORIZATION_FIELDS
    if extra_auth:
        raise StorageProtocolError(f"Unknown field(s) in authorization: {sorted(extra_auth)}")

    qual = req.get("qualification")
    if qual is not None:
        if not isinstance(qual, dict):
            raise StorageProtocolError("qualification must be null or an object")
        extra_qual = set(qual.keys()) - QUALIFICATION_FIELDS
        if extra_qual:
            raise StorageProtocolError(f"Unknown field(s) in qualification: {sorted(extra_qual)}")

    inp = req.get("input")
    if inp is not None and not isinstance(inp, dict):
        raise StorageProtocolError("input must be a dictionary")


def decode_request(raw_bytes: bytes) -> Dict[str, Any]:
    parsed = canonical_json_loads(raw_bytes)
    validate_request_schema(parsed)
    return parsed


def encode_request(request: Dict[str, Any]) -> bytes:
    validate_request_schema(request)
    encoded = canonical_json_dumps(request)
    if len(encoded) > MAX_CONTROL_FRAME_BYTES:
        raise StorageProtocolError(
            f"Oversized encoded control frame ({len(encoded)} bytes > limit {MAX_CONTROL_FRAME_BYTES})"
        )
    return encoded


def encode_success_response(
    operation: str,
    operation_id: str,
    request_id: str,
    status: str,
    obj: Optional[Dict[str, Any]] = None,
    replay: bool = False,
    complete: bool = True,
    reason_code: Optional[str] = None,
    observed_at: Optional[str] = None,
) -> bytes:
    resp: Dict[str, Any] = {
        "ok": True,
        "protocol": PROTOCOL_VERSION,
        "operation": operation,
        "operation_id": operation_id,
        "request_id": request_id,
        "status": status,
        "replay": replay,
        "complete": complete,
        "reason_code": reason_code,
    }
    if obj is not None:
        resp["object"] = obj
    if observed_at is not None:
        resp["observed_at"] = observed_at

    encoded = canonical_json_dumps(resp)
    if len(encoded) > MAX_CONTROL_FRAME_BYTES:
        raise StorageProtocolError(f"Oversized success response ({len(encoded)} bytes)")
    return encoded


def encode_failure_response(
    operation: str,
    operation_id: Optional[str],
    request_id: str,
    status: str,
    code: str,
    message: str,
    retryable: bool = False,
    object_id: Optional[str] = None,
    complete: bool = True,
) -> bytes:
    resp: Dict[str, Any] = {
        "ok": False,
        "protocol": PROTOCOL_VERSION,
        "operation": operation,
        "operation_id": operation_id,
        "request_id": request_id,
        "status": status,
        "code": code,
        "message": message,
        "retryable": retryable,
        "object_id": object_id,
        "complete": complete,
    }
    encoded = canonical_json_dumps(resp)
    if len(encoded) > MAX_CONTROL_FRAME_BYTES:
        raise StorageProtocolError(f"Oversized failure response ({len(encoded)} bytes)")
    return encoded
