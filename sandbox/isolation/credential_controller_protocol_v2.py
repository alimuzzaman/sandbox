"""Pure Credential Broker controller/lease protocol v2 contracts.

This module deliberately owns no transport, process, repository, credential,
HTTP, audit-storage, configuration, or runtime concerns.  It accepts bytes and
plain non-secret metadata, and returns validated values or bounded error codes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import struct
from types import MappingProxyType
from typing import Any, Mapping


PROTOCOL = "credential-broker-controller-v2"
REVIEWED_REGISTRY_DIGEST = "8557648d370ea7c45a76336ee99b0aa6d165afaac92a3ce385fd3459d213da08"
LEASE_FRAME_BYTES = 732
LEASE_ACK_BYTES = 444
MAX_SEQUENCE = 9_007_199_254_740_991
MAX_AUTHORIZATIONS = 16
MAX_AUTHORIZATION_TOMBSTONES = MAX_AUTHORIZATIONS

_BOUNDS = {
    "activation_ttl_ms": 30000, "audit_ack_timeout_ms": 1000,
    "audit_transport_retries": 1, "authorization_ttl_ms": 5000,
    "clock_skew_ms": 250, "controller_frame_bytes": 16384,
    "drain_timeout_ms": 5000, "handshake_timeout_ms": 1000,
    "lease_ack_bytes": 444, "lease_ack_timeout_ms": 1000,
    "lease_bytes": 16384, "lease_frame_bytes": 732, "lease_ttl_ms": 5000,
    "max_active_operations": 16, "max_sequence": MAX_SEQUENCE,
    "min_sequence": 1, "no_pending_retry_max_ms": 1000,
    "no_pending_retry_min_ms": 50, "timestamp_max_unix_ms": 4102444800000,
    "timestamp_min_unix_ms": 1700000000000,
}
_DIGEST_DOCUMENTS = {
    "activation_digest": ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "request_sequence", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "activation_expires_at_unix_ms"),
    "audit_post_fingerprint": ("machine_id", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "pre_commit_id", "outcome_class", "effect_certainty", "reason_code"),
    "audit_pre_fingerprint": ("machine_id", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "event_code"),
    "authorization_digest": ("protocol", "machine_id", "broker_epoch", "controller_epoch", "operation_id", "request_digest", "binding_id", "binding_version", "auth_form", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms", "decision_id"),
    "handshake_digest": ("protocol", "machine_id", "broker_epoch", "controller_epoch", "broker_pid", "broker_start_ticks", "broker_executable_digest", "broker_unit_digest", "broker_config_digest", "controller_pid", "controller_start_ticks", "controller_executable_digest", "controller_unit_digest", "controller_config_digest", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id"),
    "quiesce_digest": ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "request_sequence", "reason_code", "drain_deadline_unix_ms"),
}
_ENUMS = {
    "activate_decision": ("activated", "refused"),
    "admission_state": ("closed", "open"),
    "audit_disposition": ("committed",), "audit_phase": ("pre", "post"),
    "claim_state": ("claimed", "no_pending"),
    "drain_status": ("drained", "timeout", "refused"),
    "effect_certainty": ("none", "possible", "completed"),
    "event_code": ("credential_effect_pre",),
    "outcome_class": ("completed", "refused", "indeterminate"),
    "post_pairs": (("completed", "completed"), ("refused", "none"),
                   ("indeterminate", "possible"), ("indeterminate", "completed")),
}
_FIELD_TYPES = {
    "accepted": "boolean_true", "acknowledged_at_unix_ms": "timestamp",
    "activation_digest": "digest", "activation_expires_at_unix_ms": "timestamp",
    "active_operation_count": "uint_0_16", "activate_decision": "activate_decision",
    "admission_state": "admission_state", "audit_fingerprint": "digest",
    "audit_root_id": "audit_id", "auth_form": "auth_form",
    "authorization_digest": "digest", "authorization_expires_at_unix_ms": "timestamp",
    "binding_expires_at_unix_ms": "timestamp", "binding_id": "binding_id",
    "binding_version": "positive_sequence", "body_bytes": "uint_0_1048576",
    "broker_config_digest": "digest", "broker_digest": "digest",
    "broker_epoch": "epoch", "broker_executable_digest": "digest",
    "broker_pid": "pid", "broker_start_ticks": "positive_sequence",
    "broker_unit_digest": "digest", "claim_state": "claim_state",
    "commit_id": "commit_id", "content_type": "content_type",
    "controller_config_digest": "digest", "controller_epoch": "epoch",
    "controller_executable_digest": "digest", "controller_pid": "pid",
    "controller_start_ticks": "positive_sequence", "controller_unit_digest": "digest",
    "correlation_id": "correlation_id", "decision_id": "decision_id",
    "drain_deadline_unix_ms": "timestamp", "drain_status": "drain_status",
    "disposition": "audit_disposition", "effect_certainty": "effect_certainty",
    "effective_isolation_digest": "digest", "egress_digest": "digest",
    "event_code": "event_code", "evidence_id": "evidence_id_or_null",
    "handshake_digest": "digest", "header_bytes": "uint_0_65536",
    "host": "dns_name", "lease_id": "lease_id",
    "lease_sequence": "positive_sequence", "machine_id": "machine_id",
    "method": "http_method", "operation_id": "operation_id",
    "outcome_class": "outcome_class", "path": "request_path",
    "phase": "audit_phase", "phase_id": "audit_id", "policy_digest": "digest",
    "port": "https_port_443", "post_commit_id": "commit_id",
    "post_phase_id": "audit_id", "pre_commit_id": "commit_id",
    "proof_digest": "digest", "protocol": "protocol_literal",
    "quiesce_digest": "digest", "reason_code": "reason_code",
    "reply_to": "positive_sequence", "request_deadline_unix_ms": "timestamp",
    "request_digest": "digest", "request_sequence": "positive_sequence",
    "retry_after_ms": "uint_50_1000", "scheme": "https_literal",
    "sequence": "positive_sequence", "type": "message_literal",
    "wait_deadline_unix_ms": "timestamp",
}
_IDENTIFIER_RULES = {
    "audit_id": (16, 63, r"^audit-[a-z0-9]{10,57}$"),
    "binding_id": (16, 63, r"^binding-[a-z0-9]{8,55}$"),
    "commit_id": (16, 63, r"^commit-[a-z0-9]{9,56}$"),
    "correlation_id": (1, 64, r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
    "decision_id": (16, 63, r"^decision-[a-z0-9]{7,54}$"),
    "dns_name": (1, 253, r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"),
    "epoch": (32, 32, r"^[0-9a-f]{32}$"),
    "evidence_id": (16, 63, r"^evidence-[a-z0-9]{7,54}$"),
    "lease_id": (16, 63, r"^lease-[a-z0-9]{10,57}$"),
    "machine_id": (8, 63, r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$"),
    "operation_id": (16, 63, r"^operation-[a-z0-9]{6,53}$"),
}
_MESSAGES = {
    "ACTIVATE_ACK_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "activation_digest", "admission_state", "activate_decision", "active_operation_count", "acknowledged_at_unix_ms", "activation_expires_at_unix_ms", "reason_code")),
    "ACTIVATE_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "activation_digest", "activation_expires_at_unix_ms")),
    "AUDIT_ACK_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "audit_root_id", "phase", "phase_id", "audit_fingerprint", "commit_id", "disposition")),
    "AUDIT_POST_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "audit_fingerprint", "pre_commit_id", "outcome_class", "effect_certainty", "reason_code")),
    "AUDIT_PRE_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "binding_id", "binding_version", "decision_id", "audit_root_id", "phase_id", "audit_fingerprint", "event_code")),
    "AUTHORIZE_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "request_digest", "binding_id", "binding_version", "auth_form", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id", "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms", "decision_id", "authorization_digest")),
    "AUTHORIZED_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "operation_id", "request_digest", "binding_id", "binding_version", "decision_id", "authorization_digest", "authorization_expires_at_unix_ms")),
    "CLAIMED_V2_CLAIMED": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "claim_state", "operation_id", "request_digest", "binding_id", "binding_version", "scheme", "host", "port", "method", "path", "content_type", "header_bytes", "body_bytes", "request_deadline_unix_ms", "correlation_id")),
    "CLAIMED_V2_NO_PENDING": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "claim_state", "retry_after_ms")),
    "CLAIM_NEXT_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "wait_deadline_unix_ms")),
    "HELLO_ACK_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "accepted", "controller_pid", "controller_start_ticks", "controller_executable_digest", "controller_unit_digest", "controller_config_digest", "handshake_digest")),
    "HELLO_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "sequence", "broker_pid", "broker_start_ticks", "broker_executable_digest", "broker_unit_digest", "broker_config_digest", "policy_digest", "egress_digest", "broker_digest", "proof_digest", "effective_isolation_digest", "evidence_id")),
    "QUIESCE_ACK_V2": ("broker_to_controller", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reply_to", "quiesce_digest", "admission_state", "drain_status", "active_operation_count", "acknowledged_at_unix_ms", "drain_deadline_unix_ms", "reason_code")),
    "QUIESCE_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "reason_code", "drain_deadline_unix_ms", "quiesce_digest")),
    "REFUSE_V2": ("controller_to_broker", ("protocol", "type", "machine_id", "broker_epoch", "controller_epoch", "sequence", "operation_id", "request_digest", "binding_id", "binding_version", "decision_id", "reason_code")),
}
_REASON_CODES = {
    "activate": ("activated", "admission_closed", "identity_mismatch", "proof_unproven", "proof_mismatch", "digest_mismatch", "evidence_missing", "expired", "quiescing"),
    "post": ("upstream_completed", "upstream_refused", "guest_disconnected", "deadline_exceeded", "revoked", "lease_invalid", "audit_unavailable", "internal_indeterminate"),
    "quiesce": ("operator_stop", "restart", "revoke", "expiry", "proof_drift", "egress_drift", "identity_drift", "cleanup", "drained", "drain_timeout", "identity_mismatch"),
    "refuse": ("admission_closed", "request_invalid", "binding_missing", "binding_mismatch", "binding_stale", "binding_expired", "source_unavailable", "proof_unproven", "proof_mismatch", "egress_denied", "authorization_expired", "lease_invalid", "revoked", "deadline_exceeded", "capacity_exceeded", "audit_unavailable", "internal_refusal"),
}
_TEMPORAL_RULES = {
    "acknowledged_at_unix_ms": {"messages": {"ACTIVATE_ACK_V2": "request_receipt_lte_value_lte_request_receipt_plus_1000", "QUIESCE_ACK_V2": "request_receipt_lte_value_lte_min_drain_deadline_or_request_receipt_plus_5000"}},
    "activation_expires_at_unix_ms": {"max_future_ms": 30000, "messages": {"ACTIVATE_V2": "request_receipt_lt_value_lte_request_receipt_plus_30000", "ACTIVATE_ACK_V2": "value_equals_ACTIVATE_V2_value_and_acknowledged_at_lt_value"}},
    "authorization_expires_at_unix_ms": {"max_future_ms": 5000, "messages": {"AUTHORIZE_V2": "request_receipt_lt_value_lte_request_receipt_plus_5000_and_value_lte_binding_activation_request_expiries", "AUTHORIZED_V2": "value_equals_AUTHORIZE_V2_value_and_acknowledgement_precedes_value"}},
    "binding_expires_at_unix_ms": {"max_future_ms": None, "messages": {"AUTHORIZE_V2": "durable_absolute_value_in_global_range_and_value_gt_authorization_expiry_no_relative_ttl_cap"}},
    "drain_deadline_unix_ms": {"max_future_ms": 5000, "messages": {"QUIESCE_V2": "request_receipt_lt_value_lte_request_receipt_plus_5000", "QUIESCE_ACK_V2": "value_equals_QUIESCE_V2_value_and_acknowledged_at_lte_value"}},
    "request_deadline_unix_ms": {"max_future_ms": 30000, "messages": {"CLAIMED_V2_CLAIMED": "claim_time_lt_value_lte_original_guest_request_receipt_plus_30000"}},
    "wait_deadline_unix_ms": {"max_future_ms": 1000, "messages": {"CLAIM_NEXT_V2": "request_receipt_lt_value_lte_request_receipt_plus_1000"}},
}


def _registry_plain() -> dict[str, Any]:
    rules = {name: {"max": maximum, "min": minimum, "pattern": pattern}
             for name, (minimum, maximum, pattern) in _IDENTIFIER_RULES.items()}
    messages: dict[str, Any] = {}
    for name, (direction, required) in _MESSAGES.items():
        item: dict[str, Any] = {"direction": direction}
        if name.startswith("CLAIMED_V2_"):
            item["wire_type"] = "CLAIMED_V2"
        item["required"] = list(required)
        messages[name] = item
    messages["LEASE_ACK_V2"] = {
        "direction": "broker_to_controller_same_lease_socket",
        "encoding": "fixed_binary_444",
        "required": ["type", "machine_id", "broker_epoch", "controller_epoch",
                     "lease_id", "lease_sequence", "authorization_digest",
                     "audit_root_id", "post_phase_id", "post_commit_id",
                     "outcome_class", "effect_certainty", "reason_code"],
    }
    return {
        "auth_forms": list(("authorization_bearer", "x_api_key")),
        "bounds": dict(_BOUNDS),
        "digest_documents": {key: list(value) for key, value in _DIGEST_DOCUMENTS.items()},
        "enums": {key: [list(item) if isinstance(item, tuple) else item for item in value]
                  for key, value in _ENUMS.items()},
        "field_exceptions": {"HELLO_V2": {"forbidden": ["controller_epoch"], "reason": "distributed_by_HELLO_ACK_V2"}},
        "field_types": dict(_FIELD_TYPES), "identifier_rules": rules,
        "messages": messages,
        "reason_codes": {key: list(value) for key, value in _REASON_CODES.items()},
        "temporal_rules": _TEMPORAL_RULES,
    }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProtocolV2Error("canonical_value_invalid") from None


_REGISTRY_PLAIN = _registry_plain()
if hashlib.sha256(_canonical_bytes(_REGISTRY_PLAIN)).hexdigest() != REVIEWED_REGISTRY_DIGEST:
    raise RuntimeError("controller v2 reviewed registry mismatch")
REVIEWED_REGISTRY = _freeze(_REGISTRY_PLAIN)


class ProtocolV2Error(ValueError):
    """A secret-free, bounded protocol refusal."""

    __slots__ = ("code",)
    _CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and self._CODE.fullmatch(code) else "protocol_refused"
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"ProtocolV2Error(code={self.code!r})"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code}


def registry_digest() -> str:
    return hashlib.sha256(_canonical_bytes(_REGISTRY_PLAIN)).hexdigest()


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Encode a JSON object canonically after rejecting unsupported primitives."""

    if not isinstance(value, Mapping):
        raise ProtocolV2Error("json_object_required")
    _validate_json_tree(value)
    encoded = _canonical_bytes(dict(value))
    if len(encoded) > _BOUNDS["controller_frame_bytes"]:
        raise ProtocolV2Error("frame_oversize")
    return encoded


def _validate_json_tree(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise ProtocolV2Error("json_shape_invalid")
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if type(value) is int:
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ProtocolV2Error("json_key_invalid")
        for item in value.values():
            _validate_json_tree(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_tree(item, depth=depth + 1)
        return
    raise ProtocolV2Error("json_type_invalid")


def _reject_float(_value: str) -> Any:
    raise ProtocolV2Error("json_float_refused")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolV2Error("json_duplicate_key")
        result[key] = value
    return result


def _decode_canonical(packet: bytes) -> dict[str, Any]:
    if not isinstance(packet, bytes):
        raise ProtocolV2Error("frame_type_invalid")
    if not packet or len(packet) > _BOUNDS["controller_frame_bytes"]:
        raise ProtocolV2Error("frame_size_invalid")
    try:
        text = packet.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_float=_reject_float, parse_constant=_reject_float)
    except ProtocolV2Error:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ProtocolV2Error("json_invalid") from None
    if not isinstance(value, dict):
        raise ProtocolV2Error("json_object_required")
    _validate_json_tree(value)
    if _canonical_bytes(value) != packet:
        raise ProtocolV2Error("json_noncanonical")
    return value


def _variant(value: Mapping[str, Any]) -> str:
    protocol = value.get("protocol")
    if protocol != PROTOCOL:
        raise ProtocolV2Error("protocol_version_refused")
    message_type = value.get("type")
    if not isinstance(message_type, str):
        raise ProtocolV2Error("message_type_invalid")
    if message_type == "CLAIMED_V2":
        state = value.get("claim_state")
        if state == "claimed":
            return "CLAIMED_V2_CLAIMED"
        if state == "no_pending":
            return "CLAIMED_V2_NO_PENDING"
        raise ProtocolV2Error("message_variant_invalid")
    if message_type == "LEASE_ACK_V2":
        raise ProtocolV2Error("binary_message_on_json_channel")
    if message_type not in _MESSAGES:
        if message_type.endswith("_V1") or re.search(r"_V\d+$", message_type):
            raise ProtocolV2Error("protocol_version_refused")
        raise ProtocolV2Error("message_type_unknown")
    return message_type


def _is_int(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")


def _valid_identifier(kind: str, value: Any) -> bool:
    minimum, maximum, pattern = _IDENTIFIER_RULES[kind]
    return isinstance(value, str) and minimum <= len(value) <= maximum and re.fullmatch(pattern, value) is not None


def _validate_path(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not value.startswith("/"):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or any(char in value for char in "?#@\\"):
        return False
    if "//" in value or re.search(r"%(?:2[eEfF]|5[cC])", value) or any(part in {".", ".."} for part in value.split("/")):
        return False
    return True


def _validate_field(name: str, value: Any, *, variant: str) -> None:
    kind = _FIELD_TYPES[name]
    valid = False
    if kind in _IDENTIFIER_RULES:
        valid = _valid_identifier(kind, value)
    elif kind == "evidence_id_or_null":
        valid = value is None or _valid_identifier("evidence_id", value)
    elif kind == "digest":
        valid = isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None
    elif kind == "positive_sequence":
        valid = _is_int(value, 1, MAX_SEQUENCE)
    elif kind == "timestamp":
        valid = _is_int(value, _BOUNDS["timestamp_min_unix_ms"], _BOUNDS["timestamp_max_unix_ms"])
    elif kind == "pid":
        valid = _is_int(value, 1, 4_194_304)
    elif kind == "uint_0_16":
        valid = _is_int(value, 0, 16)
    elif kind == "uint_0_65536":
        valid = _is_int(value, 0, 65_536)
    elif kind == "uint_0_1048576":
        valid = _is_int(value, 0, 1_048_576)
    elif kind == "uint_50_1000":
        valid = _is_int(value, 50, 1000)
    elif kind == "boolean_true":
        valid = value is True
    elif kind == "protocol_literal":
        valid = value == PROTOCOL
    elif kind == "message_literal":
        valid = value == (_MESSAGES.get(variant, (None, ()))[0] and ("CLAIMED_V2" if variant.startswith("CLAIMED_V2_") else variant))
    elif kind == "https_literal":
        valid = value == "https"
    elif kind == "https_port_443":
        valid = type(value) is int and value == 443
    elif kind == "http_method":
        valid = isinstance(value, str) and value in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    elif kind == "content_type":
        valid = isinstance(value, str) and len(value.encode("ascii", errors="ignore")) == len(value) and len(value) <= 127 and _CONTENT_TYPE_RE.fullmatch(value) is not None
    elif kind == "request_path":
        valid = _validate_path(value)
    elif kind == "auth_form":
        valid = isinstance(value, str) and value in {"authorization_bearer", "x_api_key"}
    elif kind in _ENUMS and kind != "post_pairs":
        valid = isinstance(value, str) and value in _ENUMS[kind]
    elif kind == "reason_code":
        contexts = {"ACTIVATE_ACK_V2": "activate", "QUIESCE_V2": "quiesce",
                    "QUIESCE_ACK_V2": "quiesce", "REFUSE_V2": "refuse",
                    "AUDIT_POST_V2": "post"}
        valid = (variant in contexts and isinstance(value, str)
                 and value in _REASON_CODES[contexts[variant]])
    if not valid:
        raise ProtocolV2Error("field_invalid")


def _context_int(context: Mapping[str, Any], name: str, *, required: bool = True) -> int | None:
    if not isinstance(context, Mapping):
        raise ProtocolV2Error("temporal_context_invalid")
    value = context.get(name)
    if value is None and not required:
        return None
    if not _is_int(value, _BOUNDS["timestamp_min_unix_ms"], _BOUNDS["timestamp_max_unix_ms"]):
        raise ProtocolV2Error("temporal_context_invalid")
    return value


class TemporalObservation:
    """Injected wall-clock observation with bounded uncertainty and rollback.

    Small backwards steps inside the reviewed 250 ms skew do not extend a
    deadline: validation continues from the greatest observed time.
    """

    __slots__ = ("_last", "_failed")

    def __init__(self) -> None:
        self._last: int | None = None
        self._failed = False

    def observe(self, now_ms: Any, *, uncertainty_ms: Any = 0) -> int:
        if self._failed:
            raise ProtocolV2Error("clock_closed")
        if not _is_int(now_ms, _BOUNDS["timestamp_min_unix_ms"],
                       _BOUNDS["timestamp_max_unix_ms"]):
            self._failed = True
            raise ProtocolV2Error("clock_invalid")
        if not _is_int(uncertainty_ms, 0, _BOUNDS["clock_skew_ms"]):
            self._failed = True
            raise ProtocolV2Error("clock_uncertain")
        candidate = now_ms + uncertainty_ms
        if self._last is not None and candidate + _BOUNDS["clock_skew_ms"] < self._last:
            self._failed = True
            raise ProtocolV2Error("clock_rollback")
        effective = max(candidate, self._last) if self._last is not None else candidate
        if effective > _BOUNDS["timestamp_max_unix_ms"]:
            self._failed = True
            raise ProtocolV2Error("clock_invalid")
        self._last = effective
        return effective

    @property
    def last_unix_ms(self) -> int | None:
        return self._last

    def __repr__(self) -> str:
        return "TemporalObservation(observed={!r}, closed={!r})".format(
            self._last is not None, self._failed)


def _validate_temporal(value: Mapping[str, Any], variant: str, now_ms: int,
                       context: Mapping[str, Any]) -> None:
    if not _is_int(now_ms, _BOUNDS["timestamp_min_unix_ms"], _BOUNDS["timestamp_max_unix_ms"]):
        raise ProtocolV2Error("clock_invalid")
    if variant == "ACTIVATE_V2":
        expiry = value["activation_expires_at_unix_ms"]
        if not now_ms < expiry <= now_ms + 30000:
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "ACTIVATE_ACK_V2":
        receipt = _context_int(context, "request_receipt_unix_ms")
        expiry = _context_int(context, "activation_expires_at_unix_ms")
        if value["activation_expires_at_unix_ms"] != expiry or not receipt <= value["acknowledged_at_unix_ms"] <= receipt + 1000 or not value["acknowledged_at_unix_ms"] < expiry:
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "AUTHORIZE_V2":
        authorization = value["authorization_expires_at_unix_ms"]
        binding = value["binding_expires_at_unix_ms"]
        caps = [binding, _context_int(context, "activation_expires_at_unix_ms"),
                _context_int(context, "request_deadline_unix_ms")]
        if not now_ms < authorization <= now_ms + 5000 or not binding > authorization or any(authorization > cap for cap in caps):
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "AUTHORIZED_V2":
        expected = _context_int(context, "authorization_expires_at_unix_ms")
        if value["authorization_expires_at_unix_ms"] != expected or now_ms >= expected:
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "QUIESCE_V2":
        deadline = value["drain_deadline_unix_ms"]
        if not now_ms < deadline <= now_ms + 5000:
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "QUIESCE_ACK_V2":
        receipt = _context_int(context, "request_receipt_unix_ms")
        deadline = _context_int(context, "drain_deadline_unix_ms")
        if value["drain_deadline_unix_ms"] != deadline or not receipt <= value["acknowledged_at_unix_ms"] <= min(deadline, receipt + 5000):
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "CLAIM_NEXT_V2":
        deadline = value["wait_deadline_unix_ms"]
        if not now_ms < deadline <= now_ms + 1000:
            raise ProtocolV2Error("temporal_invalid")
    elif variant == "CLAIMED_V2_CLAIMED":
        receipt = _context_int(context, "original_guest_request_receipt_unix_ms")
        if not now_ms < value["request_deadline_unix_ms"] <= receipt + 30000:
            raise ProtocolV2Error("temporal_invalid")


def _message_digest_values(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    result = {}
    for field in _DIGEST_DOCUMENTS[name]:
        source = "sequence" if field == "request_sequence" else field
        result[field] = value[source]
    return result


def _validate_semantics(value: Mapping[str, Any], variant: str,
                        context: Mapping[str, Any]) -> None:
    if variant == "HELLO_V2" and value["sequence"] != 1:
        raise ProtocolV2Error("handshake_sequence_invalid")
    if variant == "HELLO_ACK_V2" and (value["sequence"], value["reply_to"]) != (1, 1):
        raise ProtocolV2Error("handshake_sequence_invalid")
    self_digests = {
        "ACTIVATE_V2": ("activation_digest", "activation_digest"),
        "QUIESCE_V2": ("quiesce_digest", "quiesce_digest"),
        "AUTHORIZE_V2": ("authorization_digest", "authorization_digest"),
        "AUDIT_PRE_V2": ("audit_fingerprint", "audit_pre_fingerprint"),
        "AUDIT_POST_V2": ("audit_fingerprint", "audit_post_fingerprint"),
    }
    if variant in self_digests:
        field, document = self_digests[variant]
        if value[field] != digest_document(document, _message_digest_values(value, document)):
            raise ProtocolV2Error("digest_mismatch")
    copied_digests = {
        "ACTIVATE_ACK_V2": ("activation_digest", "activation_digest"),
        "AUTHORIZED_V2": ("authorization_digest", "authorization_digest"),
        "QUIESCE_ACK_V2": ("quiesce_digest", "quiesce_digest"),
        "AUDIT_ACK_V2": ("audit_fingerprint", "audit_fingerprint"),
        "HELLO_ACK_V2": ("handshake_digest", "handshake_digest"),
    }
    if variant in copied_digests:
        field, context_field = copied_digests[variant]
        expected = context.get(context_field)
        if expected is not None and value[field] != expected:
            raise ProtocolV2Error("digest_mismatch")
    if variant == "ACTIVATE_ACK_V2":
        activated = value["activate_decision"] == "activated"
        expected = ("open", 0, "activated") if activated else ("closed", value["active_operation_count"], value["reason_code"])
        if (value["admission_state"], value["active_operation_count"], value["reason_code"]) != expected or (not activated and value["reason_code"] == "activated"):
            raise ProtocolV2Error("state_invalid")
    elif variant == "QUIESCE_ACK_V2":
        allowed = {"drained": (0, "drained"), "refused": (0, "identity_mismatch")}
        if value["admission_state"] != "closed":
            raise ProtocolV2Error("state_invalid")
        if value["drain_status"] == "timeout":
            valid = value["active_operation_count"] > 0 and value["reason_code"] == "drain_timeout"
        else:
            valid = (value["active_operation_count"], value["reason_code"]) == allowed[value["drain_status"]]
        if not valid:
            raise ProtocolV2Error("state_invalid")
    elif variant == "AUDIT_POST_V2":
        triple = (value["outcome_class"], value["effect_certainty"], value["reason_code"])
        valid = {
            ("completed", "completed", "upstream_completed"),
            ("refused", "none", "upstream_refused"),
            ("refused", "none", "deadline_exceeded"),
            ("refused", "none", "revoked"), ("refused", "none", "lease_invalid"),
            ("indeterminate", "possible", "guest_disconnected"),
            ("indeterminate", "possible", "deadline_exceeded"),
            ("indeterminate", "possible", "audit_unavailable"),
            ("indeterminate", "possible", "internal_indeterminate"),
            ("indeterminate", "completed", "audit_unavailable"),
            ("indeterminate", "completed", "internal_indeterminate"),
        }
        if triple not in valid:
            raise ProtocolV2Error("audit_outcome_invalid")


def validate_controller_message(value: Mapping[str, Any], *, direction: str,
                                now_ms: int, temporal_context: Mapping[str, Any] | None = None,
                                observation: TemporalObservation | None = None,
                                clock_uncertainty_ms: int = 0) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolV2Error("message_object_required")
    if temporal_context is not None and not isinstance(temporal_context, Mapping):
        raise ProtocolV2Error("temporal_context_invalid")
    variant = _variant(value)
    expected_direction, required = _MESSAGES[variant]
    if direction != expected_direction:
        raise ProtocolV2Error("direction_invalid")
    if set(value) != set(required):
        raise ProtocolV2Error("message_keys_invalid")
    for name in required:
        _validate_field(name, value[name], variant=variant)
    if observation is not None and not isinstance(observation, TemporalObservation):
        raise ProtocolV2Error("clock_observer_invalid")
    effective_now = (observation.observe(now_ms, uncertainty_ms=clock_uncertainty_ms)
                     if observation is not None else TemporalObservation().observe(
                         now_ms, uncertainty_ms=clock_uncertainty_ms))
    context = temporal_context if temporal_context is not None else {}
    _validate_temporal(value, variant, effective_now, context)
    _validate_semantics(value, variant, context)
    return dict(value)


def encode_controller_frame(value: Mapping[str, Any], *, direction: str,
                            now_ms: int, temporal_context: Mapping[str, Any] | None = None,
                            observation: TemporalObservation | None = None,
                            clock_uncertainty_ms: int = 0) -> bytes:
    validated = validate_controller_message(value, direction=direction, now_ms=now_ms,
                                            temporal_context=temporal_context,
                                            observation=observation,
                                            clock_uncertainty_ms=clock_uncertainty_ms)
    return canonical_json(validated)


def decode_controller_frame(packet: bytes, *, direction: str, now_ms: int,
                            temporal_context: Mapping[str, Any] | None = None,
                            observation: TemporalObservation | None = None,
                            clock_uncertainty_ms: int = 0) -> dict[str, Any]:
    return validate_controller_message(_decode_canonical(packet), direction=direction,
                                       now_ms=now_ms, temporal_context=temporal_context,
                                       observation=observation,
                                       clock_uncertainty_ms=clock_uncertainty_ms)


def digest_document(name: str, values: Mapping[str, Any]) -> str:
    if (not isinstance(name, str) or not 1 <= len(name) <= 64
            or name not in _DIGEST_DOCUMENTS):
        raise ProtocolV2Error("digest_document_invalid")
    fields = _DIGEST_DOCUMENTS[name]
    if fields is None or not isinstance(values, Mapping) or set(values) != set(fields):
        raise ProtocolV2Error("digest_document_invalid")
    variants = {
        "activation_digest": "ACTIVATE_V2",
        "audit_post_fingerprint": "AUDIT_POST_V2",
        "audit_pre_fingerprint": "AUDIT_PRE_V2",
        "authorization_digest": "AUTHORIZE_V2",
        "handshake_digest": "HELLO_ACK_V2",
        "quiesce_digest": "QUIESCE_V2",
    }
    for field in fields:
        if field not in _FIELD_TYPES:
            raise ProtocolV2Error("digest_document_invalid")
        _validate_field(field, values[field], variant=variants[name])
    return hashlib.sha256(canonical_json(values)).hexdigest()


def authorization_digest(values: Mapping[str, Any]) -> str:
    return digest_document("authorization_digest", values)


def validate_digest(name: str, values: Mapping[str, Any], expected: Any) -> None:
    if (not isinstance(name, str) or not 1 <= len(name) <= 64
            or name not in _DIGEST_DOCUMENTS):
        raise ProtocolV2Error("digest_document_invalid")
    if not isinstance(expected, str) or _DIGEST_RE.fullmatch(expected) is None:
        raise ProtocolV2Error("digest_invalid")
    if digest_document(name, values) != expected:
        raise ProtocolV2Error("digest_mismatch")


class DirectionalSequence:
    """Pure, independent transport sequence state for both directions."""

    __slots__ = ("_next", "_closed")

    def __init__(self) -> None:
        self._next = {"broker_to_controller": 1, "controller_to_broker": 1}
        self._closed = False

    def accept(self, direction: str, sequence: Any) -> None:
        if self._closed or not isinstance(direction, str) or direction not in self._next or not _is_int(sequence, 1, MAX_SEQUENCE) or sequence != self._next[direction]:
            self._closed = True
            raise ProtocolV2Error("sequence_invalid")
        if sequence == MAX_SEQUENCE:
            self._closed = True
        else:
            self._next[direction] = sequence + 1

    def accept_audit_retry(self, direction: str, sequence: Any) -> None:
        """Accept the sole documented audit retry, including one lost packet.

        Callers must invoke this only after decoding an AUDIT_PRE_V2,
        AUDIT_POST_V2, or AUDIT_ACK_V2 frame.  A retry uses a fresh sequence, so
        loss of the first transport attempt can create exactly one gap.
        """
        if (self._closed or not isinstance(direction, str) or direction not in self._next
                or not _is_int(sequence, 1, MAX_SEQUENCE)
                or sequence not in {self._next[direction], self._next[direction] + 1}):
            self._closed = True
            raise ProtocolV2Error("sequence_invalid")
        if sequence == MAX_SEQUENCE:
            self._closed = True
        else:
            self._next[direction] = sequence + 1

    @property
    def closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:
        return f"DirectionalSequence(closed={self._closed!r})"


class LeaseSequence:
    """Exact next lease sequence scoped to one controller/broker epoch pair."""

    __slots__ = ("_pair", "_next", "_closed")

    def __init__(self, controller_epoch: str, broker_epoch: str) -> None:
        if not _valid_identifier("epoch", controller_epoch) or not _valid_identifier("epoch", broker_epoch):
            raise ProtocolV2Error("epoch_invalid")
        self._pair = (controller_epoch, broker_epoch)
        self._next = 1
        self._closed = False

    def accept(self, controller_epoch: str, broker_epoch: str, sequence: Any) -> None:
        if self._closed or (controller_epoch, broker_epoch) != self._pair or not _is_int(sequence, 1, MAX_SEQUENCE) or sequence != self._next:
            self._closed = True
            raise ProtocolV2Error("lease_sequence_invalid")
        if sequence == MAX_SEQUENCE:
            self._closed = True
        else:
            self._next += 1

    def __repr__(self) -> str:
        return f"LeaseSequence(closed={self._closed!r})"


_LEASE_KEYS = frozenset({
    "machine_id", "broker_epoch", "controller_epoch", "operation_id",
    "request_digest", "binding_id", "binding_version", "auth_form",
    "policy_digest", "egress_digest", "broker_digest", "proof_digest",
    "effective_isolation_digest", "evidence_id", "decision_id",
    "authorization_digest", "authorization_expires_at_unix_ms", "lease_id",
    "lease_sequence", "lease_expires_at_unix_ms", "descriptor_size",
})
_DEADLINE_CAP_KEYS = frozenset({
    "authorization_expires_at_unix_ms", "binding_expires_at_unix_ms",
    "activation_expires_at_unix_ms", "request_deadline_unix_ms",
})
_LEASE_MAGIC = b"SBCLV2\0\0"
_ACK_MAGIC = b"SBACK2\0\0"
_AUTH_TAGS = {"authorization_bearer": 1, "x_api_key": 2}
_OUTCOME_TAGS = {"completed": 1, "refused": 2, "indeterminate": 3}
_EFFECT_TAGS = {"none": 0, "possible": 1, "completed": 2}
_REASON_TAGS = {name: index for index, name in enumerate(_REASON_CODES["post"], 1)}


def _text(value: Any, width: int, kind: str) -> bytes:
    if not _valid_identifier(kind, value):
        raise ProtocolV2Error("binary_field_invalid")
    encoded = value.encode("ascii")
    if len(encoded) >= width:
        raise ProtocolV2Error("binary_field_invalid")
    return encoded + bytes(width - len(encoded))


def _read_text(packet: bytes, offset: int, width: int, kind: str) -> str:
    field = packet[offset:offset + width]
    try:
        end = field.index(0)
    except ValueError:
        raise ProtocolV2Error("binary_padding_invalid") from None
    if not field[:end] or any(field[end:]):
        raise ProtocolV2Error("binary_padding_invalid")
    try:
        value = field[:end].decode("ascii")
    except UnicodeError:
        raise ProtocolV2Error("binary_field_invalid") from None
    if not _valid_identifier(kind, value):
        raise ProtocolV2Error("binary_field_invalid")
    return value


def _raw_digest(value: Any) -> bytes:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ProtocolV2Error("binary_digest_invalid")
    return bytes.fromhex(value)


def _epoch(value: Any) -> bytes:
    if not _valid_identifier("epoch", value):
        raise ProtocolV2Error("epoch_invalid")
    return bytes.fromhex(value)


def encode_lease_frame(value: Mapping[str, Any], *, now_ms: int,
                       deadline_caps: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping) or set(value) != _LEASE_KEYS:
        raise ProtocolV2Error("lease_keys_invalid")
    if not isinstance(deadline_caps, Mapping) or set(deadline_caps) != _DEADLINE_CAP_KEYS:
        raise ProtocolV2Error("temporal_context_invalid")
    for name, kind in (("machine_id", "machine_id"), ("operation_id", "operation_id"),
                       ("binding_id", "binding_id"), ("evidence_id", "evidence_id"),
                       ("decision_id", "decision_id"), ("lease_id", "lease_id")):
        if not _valid_identifier(kind, value[name]):
            raise ProtocolV2Error("binary_field_invalid")
    if (not isinstance(value["auth_form"], str) or value["auth_form"] not in _AUTH_TAGS
            or not _is_int(value["binding_version"], 1, MAX_SEQUENCE)
            or not _is_int(value["lease_sequence"], 1, MAX_SEQUENCE)
            or not _is_int(value["descriptor_size"], 1, 16384)):
        raise ProtocolV2Error("binary_field_invalid")
    if not _is_int(now_ms, _BOUNDS["timestamp_min_unix_ms"], _BOUNDS["timestamp_max_unix_ms"]):
        raise ProtocolV2Error("clock_invalid")
    expiry = value["lease_expires_at_unix_ms"]
    caps = [_context_int(deadline_caps, name) for name in ("authorization_expires_at_unix_ms", "binding_expires_at_unix_ms", "activation_expires_at_unix_ms", "request_deadline_unix_ms")]
    if not _is_int(expiry, _BOUNDS["timestamp_min_unix_ms"], _BOUNDS["timestamp_max_unix_ms"]) or not now_ms < expiry <= now_ms + 5000 or any(expiry > cap for cap in caps) or value["authorization_expires_at_unix_ms"] != deadline_caps["authorization_expires_at_unix_ms"]:
        raise ProtocolV2Error("temporal_invalid")
    prefix = b"".join((
        _LEASE_MAGIC, struct.pack(">HHI", 2, 732, 732),
        _text(value["machine_id"], 64, "machine_id"), _epoch(value["broker_epoch"]),
        _epoch(value["controller_epoch"]), _text(value["operation_id"], 64, "operation_id"),
        _raw_digest(value["request_digest"]), _text(value["binding_id"], 64, "binding_id"),
        struct.pack(">Q", value["binding_version"]), bytes((_AUTH_TAGS[value["auth_form"]],)), bytes(7),
        _raw_digest(value["policy_digest"]), _raw_digest(value["egress_digest"]),
        _raw_digest(value["broker_digest"]), _raw_digest(value["proof_digest"]),
        _raw_digest(value["effective_isolation_digest"]), _text(value["evidence_id"], 64, "evidence_id"),
        _text(value["decision_id"], 64, "decision_id"), _raw_digest(value["authorization_digest"]),
        struct.pack(">Q", value["authorization_expires_at_unix_ms"]),
        _text(value["lease_id"], 64, "lease_id"), struct.pack(">QQI", value["lease_sequence"], expiry, value["descriptor_size"]),
    ))
    if len(prefix) != 700:
        raise ProtocolV2Error("binary_layout_invalid")
    return prefix + hashlib.sha256(prefix).digest()


def decode_lease_frame(packet: bytes, *, now_ms: int,
                       deadline_caps: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, bytes) or len(packet) != 732:
        raise ProtocolV2Error("lease_size_invalid")
    if packet[:8] != _LEASE_MAGIC or struct.unpack(">HHI", packet[8:16]) != (2, 732, 732):
        raise ProtocolV2Error("lease_header_invalid")
    if any(packet[281:288]) or hashlib.sha256(packet[:700]).digest() != packet[700:732]:
        raise ProtocolV2Error("lease_integrity_invalid")
    auth_tag = packet[280]
    reverse_auth = {tag: name for name, tag in _AUTH_TAGS.items()}
    if auth_tag not in reverse_auth:
        raise ProtocolV2Error("binary_tag_invalid")
    value = {
        "machine_id": _read_text(packet, 16, 64, "machine_id"),
        "broker_epoch": packet[80:96].hex(), "controller_epoch": packet[96:112].hex(),
        "operation_id": _read_text(packet, 112, 64, "operation_id"),
        "request_digest": packet[176:208].hex(),
        "binding_id": _read_text(packet, 208, 64, "binding_id"),
        "binding_version": struct.unpack(">Q", packet[272:280])[0], "auth_form": reverse_auth[auth_tag],
        "policy_digest": packet[288:320].hex(), "egress_digest": packet[320:352].hex(),
        "broker_digest": packet[352:384].hex(), "proof_digest": packet[384:416].hex(),
        "effective_isolation_digest": packet[416:448].hex(),
        "evidence_id": _read_text(packet, 448, 64, "evidence_id"),
        "decision_id": _read_text(packet, 512, 64, "decision_id"),
        "authorization_digest": packet[576:608].hex(),
        "authorization_expires_at_unix_ms": struct.unpack(">Q", packet[608:616])[0],
        "lease_id": _read_text(packet, 616, 64, "lease_id"),
        "lease_sequence": struct.unpack(">Q", packet[680:688])[0],
        "lease_expires_at_unix_ms": struct.unpack(">Q", packet[688:696])[0],
        "descriptor_size": struct.unpack(">I", packet[696:700])[0],
    }
    encode_lease_frame(value, now_ms=now_ms, deadline_caps=deadline_caps)
    return value


_ACK_KEYS = frozenset(_registry_plain()["messages"]["LEASE_ACK_V2"]["required"])


def _valid_post(value: Mapping[str, Any]) -> bool:
    try:
        triple = (value["outcome_class"], value["effect_certainty"], value["reason_code"])
        return triple in {
        ("completed", "completed", "upstream_completed"),
        ("refused", "none", "upstream_refused"), ("refused", "none", "deadline_exceeded"),
        ("refused", "none", "revoked"), ("refused", "none", "lease_invalid"),
        ("indeterminate", "possible", "guest_disconnected"),
        ("indeterminate", "possible", "deadline_exceeded"),
        ("indeterminate", "possible", "audit_unavailable"),
        ("indeterminate", "possible", "internal_indeterminate"),
        ("indeterminate", "completed", "audit_unavailable"),
        ("indeterminate", "completed", "internal_indeterminate"),
        }
    except (KeyError, TypeError):
        return False


def encode_lease_ack(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping) or set(value) != _ACK_KEYS or value.get("type") != "LEASE_ACK_V2" or not _valid_post(value):
        raise ProtocolV2Error("lease_ack_invalid")
    if not _is_int(value["lease_sequence"], 1, MAX_SEQUENCE):
        raise ProtocolV2Error("lease_ack_invalid")
    prefix = b"".join((
        _ACK_MAGIC, struct.pack(">HH", 2, 444), _text(value["machine_id"], 64, "machine_id"),
        _epoch(value["broker_epoch"]), _epoch(value["controller_epoch"]),
        _text(value["lease_id"], 64, "lease_id"), struct.pack(">Q", value["lease_sequence"]),
        _raw_digest(value["authorization_digest"]), _text(value["audit_root_id"], 64, "audit_id"),
        _text(value["post_phase_id"], 64, "audit_id"), _text(value["post_commit_id"], 64, "commit_id"),
        bytes((_OUTCOME_TAGS[value["outcome_class"]], _EFFECT_TAGS[value["effect_certainty"]], _REASON_TAGS[value["reason_code"]])), bytes(5),
    ))
    if len(prefix) != 412:
        raise ProtocolV2Error("binary_layout_invalid")
    return prefix + hashlib.sha256(prefix).digest()


def decode_lease_ack(packet: bytes) -> dict[str, Any]:
    if not isinstance(packet, bytes) or len(packet) != 444:
        raise ProtocolV2Error("lease_ack_size_invalid")
    if packet[:8] != _ACK_MAGIC or struct.unpack(">HH", packet[8:12]) != (2, 444):
        raise ProtocolV2Error("lease_ack_header_invalid")
    if any(packet[407:412]) or hashlib.sha256(packet[:412]).digest() != packet[412:444]:
        raise ProtocolV2Error("lease_ack_integrity_invalid")
    outcomes = {tag: name for name, tag in _OUTCOME_TAGS.items()}
    effects = {tag: name for name, tag in _EFFECT_TAGS.items()}
    reasons = {tag: name for name, tag in _REASON_TAGS.items()}
    try:
        value = {
            "type": "LEASE_ACK_V2", "machine_id": _read_text(packet, 12, 64, "machine_id"),
            "broker_epoch": packet[76:92].hex(), "controller_epoch": packet[92:108].hex(),
            "lease_id": _read_text(packet, 108, 64, "lease_id"),
            "lease_sequence": struct.unpack(">Q", packet[172:180])[0],
            "authorization_digest": packet[180:212].hex(),
            "audit_root_id": _read_text(packet, 212, 64, "audit_id"),
            "post_phase_id": _read_text(packet, 276, 64, "audit_id"),
            "post_commit_id": _read_text(packet, 340, 64, "commit_id"),
            "outcome_class": outcomes[packet[404]], "effect_certainty": effects[packet[405]],
            "reason_code": reasons[packet[406]],
        }
    except KeyError:
        raise ProtocolV2Error("binary_tag_invalid") from None
    encode_lease_ack(value)
    return value


@dataclass(frozen=True)
class AuthorizationIdentity:
    owner: str
    machine_id: str
    broker_epoch: str
    controller_epoch: str
    operation_id: str
    request_digest: str
    binding_id: str
    binding_version: int
    decision_id: str
    authorization_digest: str
    expires_at_unix_ms: int
    binding_expires_at_unix_ms: int
    activation_expires_at_unix_ms: int
    request_deadline_unix_ms: int

    def __post_init__(self) -> None:
        _validate_authorization_identity(self)

    def __repr__(self) -> str:
        return "AuthorizationIdentity(validated=True)"


def _validate_authorization_identity(item: AuthorizationIdentity) -> None:
    if not isinstance(item, AuthorizationIdentity) or not isinstance(item.owner, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", item.owner):
        raise ProtocolV2Error("authorization_identity_invalid")
    checks = (("machine_id", item.machine_id), ("epoch", item.broker_epoch),
              ("epoch", item.controller_epoch), ("operation_id", item.operation_id),
              ("binding_id", item.binding_id), ("decision_id", item.decision_id))
    digests = (item.request_digest, item.authorization_digest)
    deadlines = (item.expires_at_unix_ms, item.binding_expires_at_unix_ms,
                 item.activation_expires_at_unix_ms, item.request_deadline_unix_ms)
    if (any(not _valid_identifier(kind, value) for kind, value in checks)
            or not _is_int(item.binding_version, 1, MAX_SEQUENCE)
            or any(not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None
                   for value in digests)
            or any(not _is_int(value, _BOUNDS["timestamp_min_unix_ms"],
                               _BOUNDS["timestamp_max_unix_ms"])
                   for value in deadlines)
            or not item.expires_at_unix_ms < item.binding_expires_at_unix_ms
            or item.expires_at_unix_ms > item.activation_expires_at_unix_ms
            or item.expires_at_unix_ms > item.request_deadline_unix_ms):
        raise ProtocolV2Error("authorization_identity_invalid")


class AuthorizationRegistry:
    """Bounded, exact-match, one-use authorization state for one broker.

    At most 16 total active identities plus epoch-lifetime tombstones exist.
    Tombstones are never pruned in this registry: after 16 distinct operation
    IDs, capacity refuses until an explicit new-epoch registry is constructed.
    """

    __slots__ = ("_items", "_tombstones", "_closed", "_clock",
                 "_machine_id", "_broker_epoch", "_controller_epoch", "_owner")

    def __init__(self, *, machine_id: Any, broker_epoch: Any,
                 controller_epoch: Any, owner: Any) -> None:
        if (not _valid_identifier("machine_id", machine_id)
                or not _valid_identifier("epoch", broker_epoch)
                or not _valid_identifier("epoch", controller_epoch)
                or not isinstance(owner, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", owner) is None):
            raise ProtocolV2Error("authorization_registry_identity_invalid")
        self._machine_id = machine_id
        self._broker_epoch = broker_epoch
        self._controller_epoch = controller_epoch
        self._owner = owner
        self._items: dict[str, AuthorizationIdentity] = {}
        self._tombstones: dict[str, int] = {}
        self._closed = False
        self._clock = TemporalObservation()

    def _matches_pinned(self, item: AuthorizationIdentity) -> bool:
        return (item.machine_id == self._machine_id
                and item.broker_epoch == self._broker_epoch
                and item.controller_epoch == self._controller_epoch
                and item.owner == self._owner)

    def _require_pinned(self, *, machine_id: Any, broker_epoch: Any,
                        controller_epoch: Any, owner: Any) -> None:
        if (machine_id != self._machine_id or broker_epoch != self._broker_epoch
                or controller_epoch != self._controller_epoch or owner != self._owner):
            raise ProtocolV2Error("authorization_registry_identity_mismatch")

    def _observe(self, now_ms: Any, uncertainty_ms: Any) -> int:
        try:
            return self._clock.observe(now_ms, uncertainty_ms=uncertainty_ms)
        except ProtocolV2Error:
            self._closed = True
            for item in tuple(self._items.values()):
                self._tombstone(item)
            self._items.clear()
            raise

    def _tombstone(self, item: AuthorizationIdentity) -> None:
        if item.operation_id in self._tombstones:
            return
        if len(self._tombstones) >= MAX_AUTHORIZATION_TOMBSTONES:
            raise ProtocolV2Error("authorization_tombstone_capacity")
        self._tombstones[item.operation_id] = item.expires_at_unix_ms

    def insert(self, item: AuthorizationIdentity, *, now_ms: int,
               clock_uncertainty_ms: int = 0) -> None:
        _validate_authorization_identity(item)
        if not self._matches_pinned(item):
            raise ProtocolV2Error("authorization_registry_identity_mismatch")
        effective_now = self._observe(now_ms, clock_uncertainty_ms)
        self._expire_at(effective_now)
        if (self._closed or item.expires_at_unix_ms <= effective_now
                or item.expires_at_unix_ms > effective_now + _BOUNDS["authorization_ttl_ms"]):
            raise ProtocolV2Error("authorization_closed")
        if item.operation_id in self._items or item.operation_id in self._tombstones:
            raise ProtocolV2Error("authorization_duplicate")
        if len(self._items) >= MAX_AUTHORIZATIONS:
            raise ProtocolV2Error("authorization_capacity")
        if len(self._items) + len(self._tombstones) >= MAX_AUTHORIZATION_TOMBSTONES:
            raise ProtocolV2Error("authorization_tombstone_capacity")
        self._items[item.operation_id] = item

    def match_and_consume(self, expected: AuthorizationIdentity, *, now_ms: int,
                          clock_uncertainty_ms: int = 0) -> AuthorizationIdentity:
        _validate_authorization_identity(expected)
        if not self._matches_pinned(expected):
            raise ProtocolV2Error("authorization_registry_identity_mismatch")
        if self._closed:
            raise ProtocolV2Error("authorization_closed")
        effective_now = self._observe(now_ms, clock_uncertainty_ms)
        self._expire_at(effective_now)
        actual = self._items.get(expected.operation_id)
        if actual is None:
            raise ProtocolV2Error("authorization_mismatch")
        if actual != expected:
            del self._items[expected.operation_id]
            self._tombstone(actual)
            raise ProtocolV2Error("authorization_mismatch_consumed")
        del self._items[expected.operation_id]
        self._tombstone(actual)
        return actual

    def _expire_at(self, now_ms: int) -> int:
        expired = [key for key, item in self._items.items() if item.expires_at_unix_ms <= now_ms]
        for key in expired:
            item = self._items.pop(key)
            self._tombstone(item)
        return len(expired)

    def expire(self, *, now_ms: int, clock_uncertainty_ms: int = 0) -> int:
        return self._expire_at(self._observe(now_ms, clock_uncertainty_ms))

    def revoke(self, *, machine_id: Any = None, broker_epoch: Any = None,
               controller_epoch: Any = None, owner: Any = None,
               binding_id: str | None = None,
               operation_id: str | None = None) -> int:
        self._require_pinned(machine_id=machine_id, broker_epoch=broker_epoch,
                             controller_epoch=controller_epoch, owner=owner)
        if (binding_id is None) == (operation_id is None):
            raise ProtocolV2Error("revoke_scope_invalid")
        if binding_id is not None and not _valid_identifier("binding_id", binding_id):
            raise ProtocolV2Error("revoke_scope_invalid")
        if operation_id is not None and not _valid_identifier("operation_id", operation_id):
            raise ProtocolV2Error("revoke_scope_invalid")
        removed = [key for key, item in self._items.items()
                   if item.binding_id == binding_id or item.operation_id == operation_id]
        for key in removed:
            item = self._items.pop(key)
            self._tombstone(item)
        return len(removed)

    def quiesce(self) -> int:
        self._closed = True
        count = len(self._items)
        for item in tuple(self._items.values()):
            self._tombstone(item)
        self._items.clear()
        return count

    def disconnect(self, *, machine_id: Any = None, broker_epoch: Any = None,
                   controller_epoch: Any = None, owner: Any = None) -> int:
        self._require_pinned(machine_id=machine_id, broker_epoch=broker_epoch,
                             controller_epoch=controller_epoch, owner=owner)
        removed = list(self._items)
        for key in removed:
            item = self._items.pop(key)
            self._tombstone(item)
        return len(removed)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"AuthorizationRegistry(active={len(self._items)}, closed={self._closed!r})"

    @property
    def tombstone_count(self) -> int:
        return len(self._tombstones)


__all__ = [
    "AuthorizationIdentity", "AuthorizationRegistry", "DirectionalSequence",
    "LeaseSequence", "LEASE_ACK_BYTES", "LEASE_FRAME_BYTES", "MAX_SEQUENCE",
    "MAX_AUTHORIZATIONS", "MAX_AUTHORIZATION_TOMBSTONES", "TemporalObservation",
    "PROTOCOL", "ProtocolV2Error", "REVIEWED_REGISTRY",
    "REVIEWED_REGISTRY_DIGEST", "authorization_digest", "canonical_json",
    "decode_controller_frame", "decode_lease_ack", "decode_lease_frame",
    "digest_document", "encode_controller_frame", "encode_lease_ack",
    "encode_lease_frame", "registry_digest", "validate_controller_message",
    "validate_digest",
]
