"""Pure v2 guest framing, transport projection, and effect contracts.

This module performs no I/O and owns no repository, resolver, credential, or
upstream implementation.  It gives the later T035 executable one exact guest
wire format and one typed, non-replayable effect boundary without enabling it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import re
import struct
import threading
from types import MappingProxyType
from typing import Any, Mapping

from .models import (
    EgressGrantSet,
    ManagedIsolationPolicy,
    parse_utc_timestamp,
    public_ipv4_network,
)


PROTOCOL = "credential-broker-guest-v2"
GUEST_PORT = 18443
INACTIVITY_TIMEOUT_SECONDS = 5
MAX_OPERATION_MILLISECONDS = 30_000
MAX_REQUEST_BODY_BYTES = 1024 * 1024
MAX_RESULT_BODY_BYTES = 4 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_DNS_ADDRESSES = 16
MAX_SEQUENCE = 9_007_199_254_740_991
_HEADER = struct.Struct("!4sBI")
_REQUEST_MAGIC = b"SBG2"
_RESULT_MAGIC = b"SBR2"
_VERSION = 2
_MAX_REQUEST_PACKET = _HEADER.size + ((MAX_REQUEST_BODY_BYTES + 2) // 3) * 4 + MAX_HEADER_BYTES + 16 * 1024
_MAX_RESULT_PACKET = _HEADER.size + ((MAX_RESULT_BODY_BYTES + 2) // 3) * 4 + MAX_HEADER_BYTES + 16 * 1024
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")
_BINDING = re.compile(r"^binding-[a-z0-9]{8,55}$")
_OPERATION = re.compile(r"^operation-[a-z0-9]{6,53}$")
_DECISION = re.compile(r"^decision-[a-z0-9]{7,54}$")
_LEASE = re.compile(r"^lease-[a-z0-9]{10,57}$")
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+/-]{0,126}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9a-z-]+$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FAILURE_CODES = frozenset((
    "admission_closed", "audit_unavailable", "authorization_expired",
    "binding_expired", "binding_mismatch", "binding_missing", "binding_stale",
    "capacity_exceeded", "deadline_exceeded", "egress_denied",
    "guest_disconnected", "internal_indeterminate", "internal_refusal",
    "lease_invalid", "proof_mismatch", "proof_unproven", "request_invalid",
    "revoked", "source_unavailable", "upstream_refused",
))
_INDETERMINATE_CODES = frozenset((
    "audit_unavailable", "deadline_exceeded", "guest_disconnected",
    "internal_indeterminate",
))
_RESULT_STATE_CODES = MappingProxyType({
    "refused": _FAILURE_CODES - (_INDETERMINATE_CODES - {"deadline_exceeded"}),
    "indeterminate": _INDETERMINATE_CODES | {"upstream_refused"},
})
_FORBIDDEN_HEADERS = frozenset((
    "authorization", "proxy-authorization", "x-api-key", "connection",
    "proxy-connection", "keep-alive", "transfer-encoding", "te", "trailer",
    "upgrade", "host", "content-length", "content-type",
))
_RESULT_HEADERS = frozenset((
    "cache-control", "content-language", "content-type", "etag", "expires",
    "last-modified", "retry-after", "vary",
))
_REQUEST_KEYS = frozenset((
    "protocol", "machine_id", "binding_id", "binding_version", "scheme",
    "host", "port", "method", "path", "headers", "body", "content_type",
    "deadline_ms", "correlation_id",
))
_SUCCESS_KEYS = frozenset((
    "protocol", "ok", "status", "headers", "body", "correlation_id",
))
_FAILURE_KEYS = frozenset((
    "protocol", "ok", "state", "code", "retryable", "correlation_id",
))
_PHASE_POST_COMBINATIONS = frozenset((
    ("pre_effect", "refused", "none", "upstream_refused"),
    ("pre_effect", "refused", "none", "deadline_exceeded"),
    ("pre_effect", "refused", "none", "revoked"),
    ("pre_effect", "refused", "none", "lease_invalid"),
    ("pre_effect", "refused", "none", "egress_denied"),
    ("effect_entered", "completed", "completed", "upstream_completed"),
    ("effect_entered", "indeterminate", "possible", "guest_disconnected"),
    ("effect_entered", "indeterminate", "possible", "deadline_exceeded"),
    ("effect_entered", "indeterminate", "possible", "audit_unavailable"),
    ("effect_entered", "indeterminate", "possible", "internal_indeterminate"),
    ("effect_entered", "indeterminate", "completed", "audit_unavailable"),
    ("effect_entered", "indeterminate", "completed", "internal_indeterminate"),
    ("effect_entered", "indeterminate", "completed", "upstream_refused"),
))
GUEST_PROTOCOL_REGISTRY = MappingProxyType({
    "protocol": PROTOCOL, "version": _VERSION,
    "envelopes": MappingProxyType({"request_magic": "SBG2", "result_magic": "SBR2",
                                   "header": "!4sBI", "header_bytes": _HEADER.size,
                                   "request_packet_bytes": _MAX_REQUEST_PACKET,
                                   "result_packet_bytes": _MAX_RESULT_PACKET}),
    "canonical_json": MappingProxyType({
        "encoding": "ascii", "sort_keys": True, "whitespace": False,
        "duplicate_keys": "refuse", "floats": "refuse", "nan": "refuse",
        "unknown_keys": "refuse", "missing_keys": "refuse",
        "trailing_bytes": "refuse", "base64": "rfc4648_canonical_validate",
    }),
    "transport": MappingProxyType({
        "family": "AF_INET", "socket_type": "SOCK_STREAM", "port": GUEST_PORT,
        "prefix_length": 30, "address_class": "rfc1918_only",
        "address_text": "canonical_ipv4_interface_string",
        "prohibited_address_classes": (
            "loopback", "link_local", "reserved", "unspecified", "multicast"),
        "default_egress": "deny", "default_route": False,
        "inactivity_seconds": INACTIVITY_TIMEOUT_SECONDS,
        "operation_milliseconds": MAX_OPERATION_MILLISECONDS,
        "interface_source": "managed_policy.network.veth",
        "broker_address_source": "managed_policy.network.host_address",
        "guest_address_source": "managed_policy.network.guest_address",
        "bind_to_device": True, "readback_required": True,
        "kernel_topology_required": True, "fallback": False,
        "requests_per_connection": 1, "results_per_connection": 1,
        "observation_fields": (
            "machine_id", "family", "socket_type", "interface",
            "bind_to_device_readback", "subnet", "local_address", "local_port",
            "peer_address", "forwarded", "loopback", "route_interface",
            "route_source", "network_namespace_isolated",
            "default_egress_denied", "default_route_absent"),
        "observation_fixed": MappingProxyType({
            "family": "AF_INET", "socket_type": "SOCK_STREAM",
            "forwarded": False, "loopback": False,
            "network_namespace_isolated": True,
            "default_egress_denied": True, "default_route_absent": True,
        }),
    }),
    "bounds": MappingProxyType({
        "request_body_bytes": MAX_REQUEST_BODY_BYTES,
        "result_body_bytes": MAX_RESULT_BODY_BYTES,
        "header_bytes": MAX_HEADER_BYTES,
        "path_characters": 2048, "content_type_characters": 127,
        "correlation_id_characters": 64, "descriptor_bytes": 16384,
        "sequence_min": 1, "sequence_max": MAX_SEQUENCE,
        "active_effect_identities": 16,
        "dns_addresses": MAX_DNS_ADDRESSES,
        "timestamp_min_unix_ms": 1_700_000_000_000,
        "timestamp_max_unix_ms": 4_102_444_800_000,
    }),
    "scalar_patterns": MappingProxyType({
        "machine_id": _MACHINE.pattern, "binding_id": _BINDING.pattern,
        "operation_id": _OPERATION.pattern, "decision_id": _DECISION.pattern,
        "lease_id": _LEASE.pattern, "epoch": _EPOCH.pattern,
        "digest": _DIGEST.pattern, "correlation_id": _CORRELATION.pattern,
        "hostname": _HOST.pattern, "content_type": _CONTENT_TYPE.pattern,
        "header_name": _HEADER_NAME.pattern,
    }),
    "request": MappingProxyType({
        "keys": tuple(sorted(_REQUEST_KEYS)),
        "types": MappingProxyType({
            "protocol": "exact_string", "machine_id": "machine_id",
            "binding_id": "binding_id", "binding_version": "positive_sequence",
            "scheme": "exact_https", "host": "canonical_hostname",
            "port": "exact_integer_443", "method": "method_enum",
            "path": "canonical_ascii_path", "headers": "sorted_unique_pairs",
            "body": "canonical_base64", "content_type": "null_or_canonical",
            "deadline_ms": "integer_1_30000", "correlation_id": "correlation_id",
        }),
        "methods": ("DELETE", "GET", "PATCH", "POST", "PUT"),
        "forbidden_keys": (
            "auth", "auth_form", "authorization", "credential", "digest",
            "lease", "operation", "policy", "proof", "source"),
        "forbidden_headers": tuple(sorted(_FORBIDDEN_HEADERS)),
    }),
    "result": MappingProxyType({
        "success_keys": tuple(sorted(_SUCCESS_KEYS)),
        "failure_keys": tuple(sorted(_FAILURE_KEYS)),
        "success_types": MappingProxyType({
            "protocol": "exact_string", "ok": "exact_true",
            "status": "integer_200_299", "headers": "sorted_unique_allowlist",
            "body": "canonical_base64", "correlation_id": "correlation_id",
        }),
        "failure_types": MappingProxyType({
            "protocol": "exact_string", "ok": "exact_false",
            "state": "state_enum", "code": "failure_code_enum",
            "retryable": "exact_false", "correlation_id": "correlation_id",
        }),
        "success_status_min": 200, "success_status_max": 299,
        "redirect_delivery": False, "retryable": False,
        "states": ("indeterminate", "refused"),
        "state_codes": MappingProxyType({
            state: tuple(sorted(codes)) for state, codes in _RESULT_STATE_CODES.items()
        }),
        "headers": tuple(sorted(_RESULT_HEADERS)),
        "failure_codes": tuple(sorted(_FAILURE_CODES)),
    }),
    "post": MappingProxyType({
        "phases": ("effect_entered", "pre_effect"),
        "combinations": tuple(sorted(_PHASE_POST_COMBINATIONS)),
        "semantic_result_code_equals_reason": True,
        "correlation_equals_request": True,
        "effect_entered_deadline": ("indeterminate", "possible", "deadline_exceeded"),
    }),
    "egress": MappingProxyType({
        "hostname": "canonical_non_numeric", "sni": "exact_hostname",
        "port": 443, "address_family": "public_ipv4",
        "address_set": "complete_unique_sorted_canonical",
        "hostname_and_full_address_intersection": True,
        "projection_digest_required": True, "reresolve_after_authorization": False,
        "nft_set_source": "authorized_decision.resolved_addresses",
        "dns_authority": "typed_cancellable_non_daemon_process",
        "dns_deadline": "absolute_monotonic",
        "dns_calls": 1, "dns_cleanup": "terminate_join_or_retain",
    }),
    "effect": MappingProxyType({
        "context": "immutable", "entry_before_executor": True,
        "context_fields": (
            "request", "egress_decision", "egress_digest", "machine_id",
            "broker_epoch", "controller_epoch", "operation_id", "request_digest",
            "binding_id", "binding_version", "decision_id", "authorization_digest",
            "auth_form", "lease_id", "lease_sequence", "descriptor_size",
            "request_deadline_unix_ms", "binding_expires_at_unix_ms",
            "authorization_expires_at_unix_ms", "lease_expires_at_unix_ms",
            "activation_expires_at_unix_ms"),
        "tombstone_before_executor": True, "retry_after_entry": False,
        "executor_exception": "effect_indeterminate",
        "invalid_executor_result": "effect_indeterminate",
        "auth_forms": ("authorization_bearer", "x_api_key"),
        "deadline_order": "lease_lte_authorization_lte_binding_activation_request",
    }),
})


class GuestProtocolV2Error(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else "guest_protocol_refused"
        super().__init__(self.code)


def _header_tuple(value: Any, *, result: bool = False) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (tuple, list)):
        raise GuestProtocolV2Error("headers_invalid")
    selected = []
    size = 0
    names = set()
    for item in value:
        if (not isinstance(item, (tuple, list)) or len(item) != 2
                or not isinstance(item[0], str) or not isinstance(item[1], str)):
            raise GuestProtocolV2Error("headers_invalid")
        name, text = item
        if (not _HEADER_NAME.fullmatch(name) or name in names
                or (not result and name in _FORBIDDEN_HEADERS)
                or (result and name not in _RESULT_HEADERS)
                or any(ord(character) < 32 or ord(character) == 127 for character in text)):
            raise GuestProtocolV2Error("headers_invalid")
        try:
            size += len(name.encode("ascii")) + len(text.encode("utf-8")) + 4
        except UnicodeEncodeError:
            raise GuestProtocolV2Error("headers_invalid") from None
        names.add(name)
        selected.append((name, text))
    if size > MAX_HEADER_BYTES or selected != sorted(selected, key=lambda item: item[0]):
        raise GuestProtocolV2Error("headers_invalid")
    return tuple(selected)


def _path(value: Any) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not value.startswith("/"):
        return False
    if any(ord(character) < 32 or ord(character) > 126 for character in value):
        return False
    lowered = value.lower()
    return ("#" not in value and "@" not in value and "/../" not in value
            and "/./" not in value and not lowered.endswith(("/..", "/."))
            and "%2f" not in lowered and "%5c" not in lowered)


@dataclass(frozen=True, slots=True)
class GuestRequestV2:
    machine_id: str
    binding_id: str
    binding_version: int
    scheme: str
    host: str
    port: int
    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    content_type: str | None
    deadline_ms: int
    correlation_id: str

    def __post_init__(self) -> None:
        try:
            headers = _header_tuple(self.headers)
        except GuestProtocolV2Error:
            raise
        if (not isinstance(self.machine_id, str) or _MACHINE.fullmatch(self.machine_id) is None
                or not isinstance(self.binding_id, str) or _BINDING.fullmatch(self.binding_id) is None
                or type(self.binding_version) is not int or not 1 <= self.binding_version <= MAX_SEQUENCE
                or self.scheme != "https" or type(self.port) is not int or self.port != 443
                or not isinstance(self.method, str)
                or self.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                or not isinstance(self.host, str) or not _HOST.fullmatch(self.host)
                or self.host != self.host.lower().rstrip(".")
                or not _path(self.path) or type(self.body) is not bytes
                or len(self.body) > MAX_REQUEST_BODY_BYTES
                or (self.content_type is not None and (not isinstance(self.content_type, str)
                    or _CONTENT_TYPE.fullmatch(self.content_type) is None))
                or type(self.deadline_ms) is not int or not 1 <= self.deadline_ms <= MAX_OPERATION_MILLISECONDS
                or not isinstance(self.correlation_id, str)
                or _CORRELATION.fullmatch(self.correlation_id) is None):
            raise GuestProtocolV2Error("request_invalid")
        object.__setattr__(self, "headers", headers)


@dataclass(frozen=True, slots=True)
class GuestResultV2:
    ok: bool
    correlation_id: str
    status: int | None = None
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    state: str | None = None
    code: str | None = None
    retryable: bool | None = None

    def __post_init__(self) -> None:
        if (type(self.ok) is not bool or not isinstance(self.correlation_id, str)
                or _CORRELATION.fullmatch(self.correlation_id) is None):
            raise GuestProtocolV2Error("result_invalid")
        if self.ok:
            headers = _header_tuple(self.headers, result=True)
            if (type(self.status) is not int or not 200 <= self.status <= 299
                    or type(self.body) is not bytes or len(self.body) > MAX_RESULT_BODY_BYTES
                    or any(value is not None for value in (self.state, self.code, self.retryable))):
                raise GuestProtocolV2Error("result_invalid")
            object.__setattr__(self, "headers", headers)
        elif (self.status is not None or self.headers != () or self.body != b""
              or not isinstance(self.state, str)
              or self.state not in {"refused", "indeterminate"}
              or not isinstance(self.code, str) or self.code not in _FAILURE_CODES
              or self.retryable is not False
              or self.code not in _RESULT_STATE_CODES[self.state]):
            raise GuestProtocolV2Error("result_invalid")

    @classmethod
    def success(cls, status: int, headers, body: bytes, correlation_id: str):
        return cls(True, correlation_id, status=status, headers=tuple(headers), body=body)

    @classmethod
    def failure(cls, *, state: str, code: str, retryable: bool, correlation_id: str):
        return cls(False, correlation_id, state=state, code=code, retryable=retryable)

    @property
    def outcome_code(self) -> str:
        """The semantic POST reason; success omits it from the exact wire schema."""
        return "upstream_completed" if self.ok else self.code


def _request_document(value: GuestRequestV2) -> dict[str, Any]:
    if type(value) is not GuestRequestV2:
        raise GuestProtocolV2Error("request_invalid")
    return {
        "protocol": PROTOCOL, "machine_id": value.machine_id,
        "binding_id": value.binding_id, "binding_version": value.binding_version,
        "scheme": value.scheme, "host": value.host, "port": value.port,
        "method": value.method, "path": value.path,
        "headers": [[name, text] for name, text in value.headers],
        "body": base64.b64encode(value.body).decode("ascii"),
        "content_type": value.content_type, "deadline_ms": value.deadline_ms,
        "correlation_id": value.correlation_id,
    }


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except Exception:
        raise GuestProtocolV2Error("canonical_encoding_invalid") from None


def guest_protocol_registry_digest_v2() -> str:
    def plain(value):
        if isinstance(value, Mapping):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [plain(item) for item in value]
        return value
    return hashlib.sha256(_canonical(plain(GUEST_PROTOCOL_REGISTRY))).hexdigest()


def encode_guest_request_v2(value: GuestRequestV2) -> bytes:
    payload = _canonical(_request_document(value))
    packet = _HEADER.pack(_REQUEST_MAGIC, _VERSION, len(payload)) + payload
    if len(packet) > _MAX_REQUEST_PACKET:
        raise GuestProtocolV2Error("request_invalid")
    return packet


def guest_request_digest_v2(value: GuestRequestV2) -> str:
    return hashlib.sha256(_canonical(_request_document(value))).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GuestProtocolV2Error("canonical_encoding_invalid")
        result[key] = value
    return result


def _json_integer(text: str) -> int:
    try:
        value = int(text)
    except Exception:
        raise GuestProtocolV2Error("canonical_encoding_invalid") from None
    if abs(value) > MAX_SEQUENCE:
        raise GuestProtocolV2Error("canonical_encoding_invalid")
    return value


def _decode(packet: Any, *, magic: bytes, limit: int) -> dict[str, Any]:
    if type(packet) is not bytes or not _HEADER.size <= len(packet) <= limit:
        raise GuestProtocolV2Error("frame_invalid")
    try:
        observed_magic, version, size = _HEADER.unpack(packet[:_HEADER.size])
        payload = packet[_HEADER.size:]
        value = json.loads(payload.decode("ascii"), object_pairs_hook=_object,
                           parse_int=_json_integer,
                           parse_float=lambda _value: (_ for _ in ()).throw(
                               GuestProtocolV2Error("canonical_encoding_invalid")))
    except GuestProtocolV2Error:
        raise
    except Exception:
        raise GuestProtocolV2Error("frame_invalid") from None
    if (observed_magic != magic or version != _VERSION or size != len(payload)
            or not isinstance(value, dict) or _canonical(value) != payload):
        raise GuestProtocolV2Error("frame_invalid")
    return value


def decode_guest_request_v2(packet: bytes) -> GuestRequestV2:
    value = _decode(packet, magic=_REQUEST_MAGIC, limit=_MAX_REQUEST_PACKET)
    if set(value) != _REQUEST_KEYS or value.get("protocol") != PROTOCOL:
        raise GuestProtocolV2Error("request_invalid")
    try:
        body = base64.b64decode(value["body"], validate=True)
        selected = GuestRequestV2(
            machine_id=value["machine_id"], binding_id=value["binding_id"],
            binding_version=value["binding_version"], scheme=value["scheme"],
            host=value["host"], port=value["port"], method=value["method"],
            path=value["path"], headers=tuple(tuple(item) for item in value["headers"]),
            body=body, content_type=value["content_type"],
            deadline_ms=value["deadline_ms"], correlation_id=value["correlation_id"],
        )
    except GuestProtocolV2Error:
        raise
    except Exception:
        raise GuestProtocolV2Error("request_invalid") from None
    if encode_guest_request_v2(selected) != packet:
        raise GuestProtocolV2Error("request_invalid")
    return selected


def _result_document(value: GuestResultV2) -> dict[str, Any]:
    if type(value) is not GuestResultV2:
        raise GuestProtocolV2Error("result_invalid")
    if value.ok:
        return {"protocol": PROTOCOL, "ok": True, "status": value.status,
                "headers": [[name, text] for name, text in value.headers],
                "body": base64.b64encode(value.body).decode("ascii"),
                "correlation_id": value.correlation_id}
    return {"protocol": PROTOCOL, "ok": False, "state": value.state,
            "code": value.code, "retryable": value.retryable,
            "correlation_id": value.correlation_id}


def encode_guest_result_v2(value: GuestResultV2) -> bytes:
    payload = _canonical(_result_document(value))
    packet = _HEADER.pack(_RESULT_MAGIC, _VERSION, len(payload)) + payload
    if len(packet) > _MAX_RESULT_PACKET:
        raise GuestProtocolV2Error("result_invalid")
    return packet


def decode_guest_result_v2(packet: bytes) -> GuestResultV2:
    value = _decode(packet, magic=_RESULT_MAGIC, limit=_MAX_RESULT_PACKET)
    if value.get("protocol") != PROTOCOL or type(value.get("ok")) is not bool:
        raise GuestProtocolV2Error("result_invalid")
    try:
        if value["ok"]:
            if set(value) != _SUCCESS_KEYS:
                raise GuestProtocolV2Error("result_invalid")
            selected = GuestResultV2.success(
                value["status"], tuple(tuple(item) for item in value["headers"]),
                base64.b64decode(value["body"], validate=True), value["correlation_id"])
        else:
            if set(value) != _FAILURE_KEYS:
                raise GuestProtocolV2Error("result_invalid")
            selected = GuestResultV2.failure(
                state=value["state"], code=value["code"], retryable=value["retryable"],
                correlation_id=value["correlation_id"])
    except GuestProtocolV2Error:
        raise
    except Exception:
        raise GuestProtocolV2Error("result_invalid") from None
    if encode_guest_result_v2(selected) != packet:
        raise GuestProtocolV2Error("result_invalid")
    return selected


@dataclass(frozen=True, slots=True)
class GuestTransportProjectionV2:
    machine_id: str
    interface: str
    subnet: str
    broker_address: str
    guest_address: str
    port: int = field(default=GUEST_PORT, init=False)


@dataclass(frozen=True, slots=True)
class GuestTransportObservationV2:
    machine_id: str
    family: str
    socket_type: str
    interface: str
    bind_to_device_readback: str
    subnet: str
    local_address: str
    local_port: int
    peer_address: str
    forwarded: bool
    loopback: bool
    route_interface: str
    route_source: str
    network_namespace_isolated: bool
    default_egress_denied: bool
    default_route_absent: bool


def _rfc1918_address(value: ipaddress.IPv4Address) -> bool:
    private_ranges = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    return (any(value in network for network in private_ranges)
            and not value.is_loopback and not value.is_link_local
            and not value.is_reserved and not value.is_unspecified
            and not value.is_multicast)


def build_guest_transport_projection_v2(policy: ManagedIsolationPolicy) -> GuestTransportProjectionV2:
    if not isinstance(policy, ManagedIsolationPolicy):
        raise GuestProtocolV2Error("transport_projection_invalid")
    network = policy.network
    try:
        if (type(network["host_address"]) is not str
                or type(network["guest_address"]) is not str):
            raise ValueError
        broker = ipaddress.ip_interface(network["host_address"])
        guest = ipaddress.ip_interface(network["guest_address"])
        interface = network["veth"]
    except Exception:
        raise GuestProtocolV2Error("transport_projection_invalid") from None
    usable = tuple(broker.network.hosts())
    if (broker.version != 4 or broker.network.prefixlen != 30
            or str(broker) != network["host_address"]
            or str(guest) != network["guest_address"]
            or guest.network != broker.network or broker.ip == guest.ip
            or broker.ip not in usable or guest.ip not in usable
            or not _rfc1918_address(broker.ip) or not _rfc1918_address(guest.ip)
            or not isinstance(interface, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface) is None
            or network.get("egress") != "deny"
            or network.get("default_route") is not False):
        raise GuestProtocolV2Error("transport_projection_invalid")
    return GuestTransportProjectionV2(
        policy.machine_id, interface, str(broker.network), str(broker.ip), str(guest.ip))


def sealed_guest_transport_projection_v2(policy: ManagedIsolationPolicy) -> Mapping[str, str]:
    """Project the reviewed listener tuple into one secret-free sealed config value."""

    projection = build_guest_transport_projection_v2(policy)
    return MappingProxyType({
        "machine_id": projection.machine_id,
        "base_policy_digest": policy.digest,
        "interface": projection.interface,
        "subnet": projection.subnet,
        "broker_address": projection.broker_address,
        "guest_address": projection.guest_address,
    })


def verify_guest_transport_v2(projection: GuestTransportProjectionV2,
                              observed: GuestTransportObservationV2) -> bool:
    return bool(
        isinstance(projection, GuestTransportProjectionV2)
        and isinstance(observed, GuestTransportObservationV2)
        and observed.machine_id == projection.machine_id
        and observed.family == "AF_INET" and observed.socket_type == "SOCK_STREAM"
        and observed.interface == projection.interface
        and observed.bind_to_device_readback == projection.interface
        and observed.subnet == projection.subnet
        and observed.local_address == projection.broker_address
        and observed.local_port == projection.port
        and observed.peer_address == projection.guest_address
        and observed.forwarded is False and observed.loopback is False
        and observed.route_interface == projection.interface
        and observed.route_source == projection.guest_address
        and observed.network_namespace_isolated is True
        and observed.default_egress_denied is True
        and observed.default_route_absent is True
    )


def build_egress_projection_v2(policy: ManagedIsolationPolicy,
                               grants: EgressGrantSet) -> Mapping[str, Any]:
    if (not isinstance(policy, ManagedIsolationPolicy)
            or not isinstance(grants, EgressGrantSet)
            or grants.machine_id != policy.machine_id
            or grants.base_policy_digest != policy.digest):
        raise GuestProtocolV2Error("egress_projection_invalid")
    projected = []
    for grant in grants.grants:
        projected.append({
            "grant_id": grant.grant_id, "owner": grant.owner, "kind": grant.kind,
            "destinations": list(grant.destinations), "ports": list(grant.ports),
            "expires_at": grant.expires_at, "revoked": grant.revoked,
        })
    value = {
        "machine_id": policy.machine_id, "base_policy_digest": policy.digest,
        "egress_digest": grants.digest, "version": grants.version,
        "grant_authority": grants.grant_authority, "grants": projected,
    }
    # Freeze through JSON so no caller-owned nested objects survive.
    copied = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    copied["grants"] = tuple(MappingProxyType({
        **item, "destinations": tuple(item["destinations"]),
        "ports": tuple(item["ports"]),
    }) for item in copied["grants"])
    return MappingProxyType(copied)


def canonical_egress_projection_v2(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return one JSON-safe, full-set egress projection."""

    if (not isinstance(value, Mapping)
            or set(value) != {"machine_id", "base_policy_digest", "egress_digest",
                              "version", "grant_authority", "grants"}
            or not isinstance(value.get("machine_id"), str)
            or _MACHINE.fullmatch(value["machine_id"]) is None
            or not isinstance(value.get("base_policy_digest"), str)
            or _DIGEST.fullmatch(value["base_policy_digest"]) is None
            or not isinstance(value.get("egress_digest"), str)
            or _DIGEST.fullmatch(value["egress_digest"]) is None
            or not isinstance(value.get("grants"), (tuple, list))):
        raise GuestProtocolV2Error("egress_projection_invalid")
    selected = []
    identities = []
    for raw in value["grants"]:
        if (not isinstance(raw, Mapping)
                or set(raw) != {"grant_id", "owner", "kind", "destinations", "ports", "expires_at", "revoked"}
                or not isinstance(raw.get("grant_id"), str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", raw["grant_id"]) is None
                or raw.get("kind") not in {"hostname_https", "public_cidr_tcp"}
                or not isinstance(raw.get("destinations"), (tuple, list))
                or not raw["destinations"] or not isinstance(raw.get("ports"), (tuple, list))
                or not raw["ports"] or any(type(port) is not int or not 1 <= port <= 65535
                                            for port in raw["ports"])
                or len(set(raw["ports"])) != len(raw["ports"])
                or not isinstance(raw.get("expires_at"), str)
                or type(raw.get("revoked")) is not bool):
            raise GuestProtocolV2Error("egress_projection_invalid")
        try:
            parse_utc_timestamp(raw["expires_at"])
        except Exception:
            raise GuestProtocolV2Error("egress_projection_invalid") from None
        try:
            destinations = tuple(
                item.lower().rstrip(".") if raw["kind"] == "hostname_https"
                else str(public_ipv4_network(item)) for item in raw["destinations"])
        except Exception:
            raise GuestProtocolV2Error("egress_projection_invalid") from None
        if (not destinations or len(set(destinations)) != len(destinations)
                or tuple(raw["destinations"]) != destinations
                or (raw["kind"] == "hostname_https" and tuple(raw["ports"]) != (443,))):
            raise GuestProtocolV2Error("egress_projection_invalid")
        identities.append(raw["grant_id"])
        selected.append({"grant_id": raw["grant_id"], "owner": raw["owner"],
                         "kind": raw["kind"],
                         "destinations": list(destinations), "ports": list(raw["ports"]),
                         "expires_at": raw["expires_at"], "revoked": raw["revoked"]})
    if len(set(identities)) != len(identities) or identities != sorted(identities):
        raise GuestProtocolV2Error("egress_projection_invalid")
    document = {"version": value["version"], "machine_id": value["machine_id"],
                "base_policy_digest": value["base_policy_digest"],
                "grant_authority": value["grant_authority"], "grants": selected,
                "grant_digest": value["egress_digest"]}
    try:
        verified = EgressGrantSet.from_dict(document)
    except Exception:
        raise GuestProtocolV2Error("egress_projection_invalid") from None
    return {"machine_id": verified.machine_id,
            "base_policy_digest": verified.base_policy_digest,
            "egress_digest": verified.digest, "version": verified.version,
            "grant_authority": verified.grant_authority, "grants": selected}


@dataclass(frozen=True, slots=True)
class AuthorizedEgressDecisionV2:
    hostname: str
    sni_hostname: str
    port: int
    resolved_addresses: tuple[str, ...]
    projection_digest: str

    def __post_init__(self) -> None:
        try:
            parsed = tuple(ipaddress.ip_address(value)
                           for value in self.resolved_addresses)
        except Exception:
            raise GuestProtocolV2Error("egress_decision_invalid") from None
        try:
            ipaddress.ip_address(self.hostname)
        except ValueError:
            numeric_hostname = False
        except Exception:
            raise GuestProtocolV2Error("egress_decision_invalid") from None
        else:
            numeric_hostname = True
        if (not isinstance(self.hostname, str) or not _HOST.fullmatch(self.hostname)
                or self.hostname != self.hostname.lower().rstrip(".")
                or numeric_hostname or self.sni_hostname != self.hostname
                or type(self.port) is not int or self.port != 443
                or type(self.resolved_addresses) is not tuple
                or not self.resolved_addresses
                or any(type(value) is not str for value in self.resolved_addresses)
                or any(address.version != 4 or not address.is_global for address in parsed)
                or tuple(str(address) for address in parsed) != self.resolved_addresses
                or tuple(sorted(self.resolved_addresses,
                                key=lambda value: int(ipaddress.ip_address(value))))
                   != self.resolved_addresses
                or len(set(self.resolved_addresses)) != len(self.resolved_addresses)
                or not isinstance(self.projection_digest, str)
                or _DIGEST.fullmatch(self.projection_digest) is None):
            raise GuestProtocolV2Error("egress_decision_invalid")

    @property
    def nft_destination_set(self) -> tuple[str, ...]:
        """The exact already-resolved set consumed by the later nft executor."""
        return self.resolved_addresses


def authorize_egress_decision_v2(projection: Mapping[str, Any], *, host: str,
                                 sni_hostname: str, port: int,
                                 resolved_addresses, now: str) -> AuthorizedEgressDecisionV2:
    try:
        projection = canonical_egress_projection_v2(projection)
        if (not isinstance(host, str) or not _HOST.fullmatch(host)
                or host != host.lower().rstrip(".") or sni_hostname != host
                or port != 443 or not isinstance(resolved_addresses, (tuple, list))
                or not resolved_addresses):
            raise GuestProtocolV2Error("egress_denied")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise GuestProtocolV2Error("egress_denied")
        instant = parse_utc_timestamp(now)
        if any(type(value) is not str for value in resolved_addresses):
            raise GuestProtocolV2Error("egress_denied")
        addresses = tuple(ipaddress.ip_address(value) for value in resolved_addresses)
        canonical_addresses = tuple(str(address) for address in addresses)
        if (canonical_addresses != tuple(resolved_addresses)
                or len(set(addresses)) != len(addresses)
                or any(address.version != 4 or not address.is_global for address in addresses)):
            raise GuestProtocolV2Error("egress_denied")
        canonical_addresses = tuple(sorted(canonical_addresses,
                                            key=lambda value: int(ipaddress.ip_address(value))))
        hostname_ok = False
        networks = []
        for grant in projection["grants"]:
            if (not isinstance(grant, Mapping)
                    or set(grant) != {"grant_id", "owner", "kind", "destinations", "ports", "expires_at", "revoked"}
                    or grant["revoked"] is not False
                    or parse_utc_timestamp(grant["expires_at"]) <= instant
                    or 443 not in grant["ports"]):
                continue
            if grant["kind"] == "hostname_https" and host in grant["destinations"]:
                hostname_ok = True
            elif grant["kind"] == "public_cidr_tcp":
                networks.extend(public_ipv4_network(value) for value in grant["destinations"])
        if not (hostname_ok and networks and all(
                any(address in network for network in networks) for address in addresses)):
            raise GuestProtocolV2Error("egress_denied")
        return AuthorizedEgressDecisionV2(
            host, host, 443, canonical_addresses, projection["egress_digest"])
    except GuestProtocolV2Error:
        raise
    except Exception:
        raise GuestProtocolV2Error("egress_denied") from None


@dataclass(frozen=True, slots=True)
class AuthorizedEffectContextV2:
    request: GuestRequestV2
    egress_decision: AuthorizedEgressDecisionV2
    egress_digest: str
    machine_id: str
    broker_epoch: str
    controller_epoch: str
    operation_id: str
    request_digest: str
    binding_id: str
    binding_version: int
    decision_id: str
    authorization_digest: str
    auth_form: str
    lease_id: str
    lease_sequence: int
    descriptor_size: int
    request_deadline_unix_ms: int
    binding_expires_at_unix_ms: int
    authorization_expires_at_unix_ms: int
    lease_expires_at_unix_ms: int
    activation_expires_at_unix_ms: int

    def __post_init__(self) -> None:
        deadlines = (self.request_deadline_unix_ms, self.binding_expires_at_unix_ms,
                     self.authorization_expires_at_unix_ms, self.lease_expires_at_unix_ms,
                     self.activation_expires_at_unix_ms)
        if (type(self.request) is not GuestRequestV2
                or type(self.egress_decision) is not AuthorizedEgressDecisionV2
                or self.egress_decision.hostname != self.request.host
                or self.egress_decision.sni_hostname != self.request.host
                or self.egress_decision.port != self.request.port
                or not isinstance(self.egress_digest, str)
                or self.egress_digest != self.egress_decision.projection_digest
                or self.machine_id != self.request.machine_id
                or self.binding_id != self.request.binding_id
                or self.binding_version != self.request.binding_version
                or not isinstance(self.broker_epoch, str)
                or _EPOCH.fullmatch(self.broker_epoch) is None
                or not isinstance(self.controller_epoch, str)
                or _EPOCH.fullmatch(self.controller_epoch) is None
                or not isinstance(self.operation_id, str)
                or _OPERATION.fullmatch(self.operation_id) is None
                or self.request_digest != guest_request_digest_v2(self.request)
                or not isinstance(self.decision_id, str)
                or _DECISION.fullmatch(self.decision_id) is None
                or not isinstance(self.authorization_digest, str)
                or _DIGEST.fullmatch(self.authorization_digest) is None
                or not isinstance(self.auth_form, str)
                or self.auth_form not in {"authorization_bearer", "x_api_key"}
                or not isinstance(self.lease_id, str)
                or _LEASE.fullmatch(self.lease_id) is None
                or type(self.lease_sequence) is not int
                or not 1 <= self.lease_sequence <= MAX_SEQUENCE
                or type(self.descriptor_size) is not int or not 1 <= self.descriptor_size <= 16384
                or any(type(value) is not int or not 1700000000000 <= value <= 4102444800000
                       for value in deadlines)
                or self.authorization_expires_at_unix_ms > min(
                    self.binding_expires_at_unix_ms,
                    self.activation_expires_at_unix_ms,
                    self.request_deadline_unix_ms)
                or min(self.binding_expires_at_unix_ms,
                       self.authorization_expires_at_unix_ms,
                       self.activation_expires_at_unix_ms,
                       self.request_deadline_unix_ms) < self.lease_expires_at_unix_ms):
            raise GuestProtocolV2Error("effect_context_invalid")

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.machine_id, self.broker_epoch, self.controller_epoch,
                self.operation_id, self.lease_id)


@dataclass(frozen=True, slots=True)
class EffectExecutionResultV2:
    guest_result: GuestResultV2
    effect_phase: str
    outcome_class: str
    effect_certainty: str
    reason_code: str

    def __post_init__(self) -> None:
        if (type(self.guest_result) is not GuestResultV2
                or not isinstance(self.effect_phase, str)
                or not isinstance(self.outcome_class, str)
                or not isinstance(self.effect_certainty, str)
                or not isinstance(self.reason_code, str)
                or (self.effect_phase, self.outcome_class, self.effect_certainty,
                    self.reason_code) not in _PHASE_POST_COMBINATIONS
                or (self.outcome_class == "completed") is not self.guest_result.ok
                or (self.outcome_class == "refused"
                    and self.guest_result.state != "refused")
                or (self.outcome_class == "indeterminate"
                    and self.guest_result.state != "indeterminate")
                or self.guest_result.outcome_code != self.reason_code):
            raise GuestProtocolV2Error("effect_result_invalid")


class EffectExecutionV2(ABC):
    """One entry per authorized identity; entry is recorded before effect code."""

    def __init__(self) -> None:
        self._entered: set[tuple[str, str, str, str, str]] = set()
        self._entry_lock = threading.Lock()

    def execute(self, context: AuthorizedEffectContextV2,
                descriptor: int) -> EffectExecutionResultV2:
        if type(context) is not AuthorizedEffectContextV2 or type(descriptor) is not int or descriptor < 0:
            raise GuestProtocolV2Error("effect_context_invalid")
        with self._entry_lock:
            if context.identity in self._entered:
                raise GuestProtocolV2Error("effect_replayed")
            if len(self._entered) >= 16:
                raise GuestProtocolV2Error("effect_capacity_exceeded")
            self._entered.add(context.identity)
        try:
            result = self.execute_authorized(context, descriptor)
            if (type(result) is not EffectExecutionResultV2
                    or result.effect_phase != "effect_entered"
                    or result.guest_result.correlation_id != context.request.correlation_id):
                raise GuestProtocolV2Error("effect_indeterminate")
        except Exception:
            raise GuestProtocolV2Error("effect_indeterminate") from None
        return result

    @abstractmethod
    def execute_authorized(self, context: AuthorizedEffectContextV2,
                           descriptor: int) -> EffectExecutionResultV2:
        """Perform one typed operation; callers may never retry after entry."""


__all__ = [
    "AuthorizedEffectContextV2", "AuthorizedEgressDecisionV2",
    "EffectExecutionResultV2", "EffectExecutionV2",
    "GUEST_PORT", "GUEST_PROTOCOL_REGISTRY", "GuestProtocolV2Error",
    "GuestRequestV2", "GuestResultV2",
    "GuestTransportObservationV2", "GuestTransportProjectionV2",
    "INACTIVITY_TIMEOUT_SECONDS", "MAX_DNS_ADDRESSES", "MAX_HEADER_BYTES", "MAX_OPERATION_MILLISECONDS",
    "MAX_REQUEST_BODY_BYTES", "MAX_RESULT_BODY_BYTES", "MAX_SEQUENCE", "PROTOCOL",
    "authorize_egress_decision_v2", "build_egress_projection_v2",
    "build_guest_transport_projection_v2",
    "canonical_egress_projection_v2",
    "decode_guest_request_v2", "decode_guest_result_v2", "encode_guest_request_v2",
    "encode_guest_result_v2", "guest_request_digest_v2",
    "guest_protocol_registry_digest_v2",
    "sealed_guest_transport_projection_v2",
    "verify_guest_transport_v2",
]
