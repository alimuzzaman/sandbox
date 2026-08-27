#!/usr/bin/env python3
"""Offline-only guarded credential-broker transport and validation seams.

The Linux listener, descriptor, and dispatcher code is explicit opt-in and
closed by default.  This is a library contract, not a runnable service: no
application/runtime path constructs it and no default is enabled.  Code
presence is not live Ubuntu proof or support-tier authority.  Pure validation
and injectable kernel seams keep local tests non-privileged; T036 owns any
later helper/service wiring and proof.
"""

from __future__ import annotations

from array import array
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import struct
import sys
import threading
import time
from typing import Any


PROTOCOL_VERSION = 1
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_FRAME_BYTES = 4096
MAX_GUEST_FRAME_BYTES = 64 * 1024
MAX_ACTIVE_REQUESTS = 16
MAX_SAFE_DOCUMENT_BYTES = 1024
HELPER_VERBS = (
    "credential-broker-start",
    "credential-broker-status",
    "credential-broker-stop",
)
REQUIRED_SEALS = frozenset(("write", "grow", "shrink", "seal"))
# Guarded library seams exist.  This does not mean a runnable service, live
# proof, or an adoptable/default runtime path exists.
LIVE_TRANSPORT_IMPLEMENTED = True
FIXED_EXECUTABLE = "/usr/libexec/sandbox/native-credential-broker"
_FRAME_MAGIC = b"SBCL"
_FRAME_HEADER = struct.Struct("!4sBI")
_GUEST_MAGIC = b"SBGR"
_PEER_CREDENTIALS = struct.Struct("3i")

_IDENTITY = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9._:@/-]{0,255}$")
_MACHINE_ID = re.compile(r"^sb-[a-f0-9]{12}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SAFE_CODE = re.compile(r"^[a-z0-9_.-]{1,64}$")

_SERVICE_FIELDS = frozenset((
    "machine_id", "broker_epoch", "pid", "process_start_identity",
    "service_uid", "unit_identity", "cgroup_identity", "executable_digest",
    "config_digest", "policy_digest", "egress_digest", "broker_digest",
    "guest_interface", "host_address", "guest_address", "guest_port",
))
_PEER_FIELDS = frozenset((
    "pid", "uid", "process_start_identity", "unit_identity",
    "cgroup_identity", "executable_digest", "config_digest",
))
_GUEST_OBSERVATION_FIELDS = frozenset((
    "machine_id", "broker_epoch", "interface", "local_address", "local_port",
    "peer_address", "forwarded", "loopback", "connection_identity",
    "peer_verified",
))
_GUEST_REQUEST_FIELDS = frozenset((
    "machine_id", "binding_id", "binding_version",
))
_FRAME_FIELDS = frozenset((
    "protocol_version", "lease_id", "broker_epoch", "machine_id",
    "binding_id", "binding_version", "policy_digest", "egress_digest",
    "broker_digest", "expires_at", "descriptor_size",
))
_DESCRIPTOR_FIELDS = frozenset((
    "descriptor_count", "anonymous_memfd", "close_on_exec", "size", "seals",
))

_SAFE_SUMMARIES = {
    "broker_epoch_stale": "broker epoch is stale",
    "broker_identity_mismatch": "broker process identity does not match",
    "descriptor_extra": "lease frame has extra descriptors",
    "descriptor_missing": "lease frame descriptor is missing",
    "descriptor_oversize": "lease descriptor exceeds the fixed bound",
    "descriptor_seals_invalid": "lease descriptor seals are invalid",
    "descriptor_size_mismatch": "lease descriptor size does not match",
    "descriptor_type_invalid": "lease descriptor type is invalid",
    "dispatcher_denied": "lease dispatcher identity is denied",
    "frame_invalid": "lease frame is invalid",
    "guest_frame_invalid": "guest request frame is invalid",
    "guest_listener_closed": "guest listener is closed",
    "guest_listener_unavailable": "guest listener is unavailable",
    "lease_handoff_required": "lease requires an internal broker handoff",
    "lease_ack_indeterminate": "lease acknowledgement is indeterminate",
    "lease_not_pending": "lease has no matching pending request",
    "request_limit": "broker active request limit is reached",
    "root_execution_denied": "credential broker transport refuses root execution",
    "lease_channel_closed": "trusted lease channel is closed",
    "lease_channel_unavailable": "trusted lease channel is unavailable",
    "lease_expired": "lease has expired",
    "lease_identity_mismatch": "lease identity does not match",
    "lease_replayed": "lease was already consumed",
    "connection_peer_denied": "connection peer identity is denied",
    "connection_replayed": "bound connection state was already consumed",
    "transport_denied": "guest transport identity is denied",
    "live_transport_unproven": "guarded transport seams have no live proof",
}


def _mapping(value: Any, fields: frozenset[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or frozenset(value) != fields:
        return None
    return value


def _identity(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTITY.fullmatch(value))


def _machine(value: Any) -> bool:
    return isinstance(value, str) and bool(_MACHINE_ID.fullmatch(value))


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _integer(value: Any, *, minimum: int = 0, maximum: int = 2**63 - 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) \
        and minimum <= value <= maximum


def _address(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(ipaddress.ip_address(value)) == value
    except ValueError:
        return False


def _safe_code(value: Any) -> str:
    if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
        return value
    return "broker_failed"


def _bounded_document(value: dict[str, Any]) -> dict[str, Any]:
    """Refuse accidental expansion of a caller-visible schema."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SAFE_DOCUMENT_BYTES:
        return {
            "ok": False,
            "code": "response_oversize",
            "message": "broker response exceeds the fixed bound",
            "retryable": False,
        }
    return value


def bounded_error(code: str, _diagnostic: Any = None, *, retryable: bool = False) -> dict[str, Any]:
    """Return a stable error without reflecting lower-layer diagnostics."""
    safe = _safe_code(code)
    return _bounded_document({
        "ok": False,
        "code": safe,
        "message": _SAFE_SUMMARIES.get(safe, "credential broker request refused"),
        "retryable": bool(retryable),
    })


def terminal_indeterminate(lease_id: str) -> dict[str, Any]:
    """Preserve only the opaque lease identity after an uncertain send effect."""
    if not _identity(lease_id):
        return bounded_error("lease_ack_indeterminate")
    return _bounded_document({
        "ok": False,
        "lease_id": lease_id,
        "outcome": "indeterminate",
        "code": "lease_ack_indeterminate",
    })


def _running_as_root() -> bool:
    return bool(hasattr(os, "geteuid") and os.geteuid() == 0)


def _validate_service_identity(value: Any) -> bool:
    item = _mapping(value, _SERVICE_FIELDS)
    return bool(item) and all((
        _machine(item["machine_id"]),
        _identity(item["broker_epoch"]),
        _integer(item["pid"], minimum=1, maximum=2**31 - 1),
        _identity(item["process_start_identity"]),
        _integer(item["service_uid"], minimum=1, maximum=2**31 - 1),
        _identity(item["unit_identity"]),
        _identity(item["cgroup_identity"]),
        _digest(item["executable_digest"]),
        _digest(item["config_digest"]),
        _digest(item["policy_digest"]),
        _digest(item["egress_digest"]),
        _digest(item["broker_digest"]),
        _identity(item["guest_interface"]),
        _address(item["host_address"]),
        _address(item["guest_address"]),
        _integer(item["guest_port"], minimum=1, maximum=65535),
        item["unit_identity"]
        == f"sandbox-credential-broker@{item['machine_id']}.service",
    ))


def validate_guest_admission(
    service: Any,
    observation: Any,
    request: Any,
) -> dict[str, Any]:
    """Validate the exact private-veth tuple before guest request parsing."""
    observed = _mapping(observation, _GUEST_OBSERVATION_FIELDS)
    requested = _mapping(request, _GUEST_REQUEST_FIELDS)
    transport = validate_guest_transport(service, observed)
    if not transport["ok"] or requested is None:
        return bounded_error("transport_denied")
    if not all((
        _machine(requested["machine_id"]),
        _identity(requested["binding_id"]),
        _integer(requested["binding_version"], minimum=1),
    )):
        return bounded_error("transport_denied")
    if requested["machine_id"] != service["machine_id"]:
        return bounded_error("transport_denied")
    return {"ok": True, "code": "admitted"}


def validate_guest_transport(service: Any, observation: Any) -> dict[str, Any]:
    """Validate kernel-derived connection state before reading guest bytes."""
    observed = _mapping(observation, _GUEST_OBSERVATION_FIELDS)
    if not _validate_service_identity(service) or observed is None or not all((
        _machine(observed["machine_id"]),
        _identity(observed["broker_epoch"]),
        _identity(observed["interface"]),
        _address(observed["local_address"]),
        _integer(observed["local_port"], minimum=1, maximum=65535),
        _address(observed["peer_address"]),
        isinstance(observed["forwarded"], bool),
        isinstance(observed["loopback"], bool),
        _identity(observed["connection_identity"]),
        isinstance(observed["peer_verified"], bool),
    )):
        return bounded_error("transport_denied")
    exact = (
        observed["machine_id"] == service["machine_id"]
        and observed["broker_epoch"] == service["broker_epoch"]
        and observed["interface"] == service["guest_interface"]
        and observed["local_address"] == service["host_address"]
        and observed["local_port"] == service["guest_port"]
        and observed["peer_address"] == service["guest_address"]
        and observed["forwarded"] is False
        and observed["loopback"] is False
        and observed["peer_verified"] is True
    )
    return {"ok": True, "code": "transport_verified"} \
        if exact else bounded_error("transport_denied")


def validate_dispatch_peer(service: Any, peer: Any, frame: Any) -> dict[str, Any]:
    """Match the connected dispatcher to the exact supervised broker process."""
    observed = _mapping(peer, _PEER_FIELDS)
    if not _validate_service_identity(service) or observed is None:
        return bounded_error("broker_identity_mismatch")
    if not all((
        _integer(observed["pid"], minimum=1, maximum=2**31 - 1),
        _integer(observed["uid"], minimum=1, maximum=2**31 - 1),
        _identity(observed["process_start_identity"]),
        _identity(observed["unit_identity"]),
        _identity(observed["cgroup_identity"]),
        _digest(observed["executable_digest"]),
        _digest(observed["config_digest"]),
    )):
        return bounded_error("broker_identity_mismatch")
    for peer_key, service_key in (
        ("pid", "pid"),
        ("uid", "service_uid"),
        ("process_start_identity", "process_start_identity"),
        ("unit_identity", "unit_identity"),
        ("cgroup_identity", "cgroup_identity"),
        ("executable_digest", "executable_digest"),
        ("config_digest", "config_digest"),
    ):
        if observed[peer_key] != service[service_key]:
            return bounded_error("broker_identity_mismatch")
    if not isinstance(frame, dict):
        return bounded_error("frame_invalid")
    if frame.get("broker_epoch") != service["broker_epoch"]:
        return bounded_error("broker_epoch_stale")
    if frame.get("machine_id") != service["machine_id"]:
        return bounded_error("lease_identity_mismatch")
    return {"ok": True, "code": "broker_identity_verified"}


class BrokerEpochState:
    """Broker-owned freshness state for verified connections.

    The epoch is non-secret metadata.  It is compared with the supervised
    broker state, never accepted as a guest bearer capability.  Connection
    identities supplied here are observations from the verified transport
    boundary, not caller-provided authorization tokens.
    """

    __slots__ = ("_epoch", "_connections")

    def __init__(self, epoch: str) -> None:
        if not _identity(epoch):
            raise ValueError("broker epoch is invalid")
        self._epoch = epoch
        self._connections: set[str] = set()

    def __repr__(self) -> str:
        return f"BrokerEpochState(connection_count={len(self._connections)})"

    def admit(
        self,
        observed_epoch: str,
        connection_identity: str,
        *,
        peer_verified: bool,
    ) -> dict[str, Any]:
        if peer_verified is not True or not _identity(connection_identity):
            return bounded_error("connection_peer_denied")
        if observed_epoch != self._epoch:
            return bounded_error("broker_epoch_stale")
        if connection_identity in self._connections:
            return bounded_error("connection_replayed")
        self._connections.add(connection_identity)
        return {"ok": True, "code": "connection_bound"}

    def rotate(self, epoch: str) -> None:
        if not _identity(epoch) or epoch == self._epoch:
            raise ValueError("broker epoch rotation is invalid")
        self._epoch = epoch
        self._connections.clear()


def _validate_frame_identity(service: Any, frame: Any, *, now: int) -> dict[str, Any]:
    value = _mapping(frame, _FRAME_FIELDS)
    if not _validate_service_identity(service) or value is None:
        return bounded_error("frame_invalid")
    if not all((
        value["protocol_version"] == PROTOCOL_VERSION,
        _identity(value["lease_id"]),
        _identity(value["broker_epoch"]),
        _machine(value["machine_id"]),
        _identity(value["binding_id"]),
        _integer(value["binding_version"], minimum=1),
        _digest(value["policy_digest"]),
        _digest(value["egress_digest"]),
        _digest(value["broker_digest"]),
        _integer(value["expires_at"], minimum=1),
        _integer(value["descriptor_size"], minimum=1),
        _integer(now, minimum=0),
    )):
        return bounded_error("frame_invalid")
    if value["broker_epoch"] != service["broker_epoch"]:
        return bounded_error("broker_epoch_stale")
    if value["machine_id"] != service["machine_id"] or any((
        value["policy_digest"] != service["policy_digest"],
        value["egress_digest"] != service["egress_digest"],
        value["broker_digest"] != service["broker_digest"],
    )):
        return bounded_error("lease_identity_mismatch")
    if value["expires_at"] <= now:
        return bounded_error("lease_expired")
    if value["descriptor_size"] > MAX_DESCRIPTOR_BYTES:
        return bounded_error("descriptor_oversize")
    return {"ok": True, "code": "frame_verified"}


def _validate_descriptor(frame: dict[str, Any], descriptor: Any) -> dict[str, Any]:
    value = _mapping(descriptor, _DESCRIPTOR_FIELDS)
    if value is None:
        return bounded_error("frame_invalid")
    count = value["descriptor_count"]
    if count == 0:
        return bounded_error("descriptor_missing")
    if count != 1:
        return bounded_error("descriptor_extra")
    if value["anonymous_memfd"] is not True or value["close_on_exec"] is not True:
        return bounded_error("descriptor_type_invalid")
    if not _integer(value["size"], minimum=1):
        return bounded_error("descriptor_type_invalid")
    try:
        seals = frozenset(value["seals"])
    except (TypeError, ValueError):
        return bounded_error("descriptor_seals_invalid")
    if seals != REQUIRED_SEALS:
        return bounded_error("descriptor_seals_invalid")
    if value["size"] > MAX_DESCRIPTOR_BYTES or frame["descriptor_size"] > MAX_DESCRIPTOR_BYTES:
        return bounded_error("descriptor_oversize")
    if value["size"] != frame["descriptor_size"]:
        return bounded_error("descriptor_size_mismatch")
    return {"ok": True, "code": "descriptor_verified"}


def validate_lease_frame(
    service: Any,
    frame: Any,
    descriptor: Any,
    *,
    dispatcher_peer: Any,
    control_plane_uid: int,
    now: int,
) -> dict[str, Any]:
    """Pure validation seam for one bounded seqpacket frame and descriptor."""
    if not _integer(control_plane_uid, minimum=1, maximum=2**31 - 1) \
            or not isinstance(dispatcher_peer, dict) \
            or dispatcher_peer != {"uid": control_plane_uid}:
        return bounded_error("dispatcher_denied")
    checked = _validate_frame_identity(service, frame, now=now)
    if not checked["ok"]:
        return checked
    return _validate_descriptor(frame, descriptor)


def encode_lease_frame(frame: Any) -> bytes:
    """Encode one canonical metadata-only seqpacket frame."""
    value = _mapping(frame, _FRAME_FIELDS)
    if value is None:
        raise ValueError("lease frame is invalid")
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("lease frame is invalid") from exc
    size = _FRAME_HEADER.size + len(payload)
    if size > MAX_FRAME_BYTES:
        raise ValueError("lease frame exceeds the fixed bound")
    return _FRAME_HEADER.pack(_FRAME_MAGIC, PROTOCOL_VERSION, len(payload)) + payload


def parse_lease_frame(packet: Any) -> dict[str, Any]:
    """Parse exactly one bounded packet without accepting trailing bytes."""
    if not isinstance(packet, bytes) or not _FRAME_HEADER.size <= len(packet) <= MAX_FRAME_BYTES:
        raise ValueError("lease frame is invalid")
    try:
        magic, version, payload_size = _FRAME_HEADER.unpack(packet[:_FRAME_HEADER.size])
    except struct.error as exc:
        raise ValueError("lease frame is invalid") from exc
    payload = packet[_FRAME_HEADER.size:]
    if magic != _FRAME_MAGIC or version != PROTOCOL_VERSION \
            or payload_size != len(payload):
        raise ValueError("lease frame is invalid")
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("lease frame is invalid") from exc
    if _mapping(value, _FRAME_FIELDS) is None:
        raise ValueError("lease frame is invalid")
    # Re-encoding equality rejects duplicate JSON keys, alternate whitespace,
    # and other non-canonical encodings before identity checks.
    if encode_lease_frame(value) != packet:
        raise ValueError("lease frame is invalid")
    return value


def encode_guest_request(request: Any) -> bytes:
    value = _mapping(request, _GUEST_REQUEST_FIELDS)
    if value is None:
        raise ValueError("guest request is invalid")
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    if _FRAME_HEADER.size + len(payload) > MAX_GUEST_FRAME_BYTES:
        raise ValueError("guest request exceeds the fixed bound")
    return _FRAME_HEADER.pack(_GUEST_MAGIC, PROTOCOL_VERSION, len(payload)) + payload


def parse_guest_request(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, bytes) or not _FRAME_HEADER.size <= len(packet) <= MAX_GUEST_FRAME_BYTES:
        raise ValueError("guest request is invalid")
    try:
        magic, version, payload_size = _FRAME_HEADER.unpack(packet[:_FRAME_HEADER.size])
        payload = packet[_FRAME_HEADER.size:]
        value = json.loads(payload.decode("ascii"))
    except (struct.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("guest request is invalid") from exc
    if magic != _GUEST_MAGIC or version != PROTOCOL_VERSION or payload_size != len(payload) \
            or _mapping(value, _GUEST_REQUEST_FIELDS) is None \
            or encode_guest_request(value) != packet:
        raise ValueError("guest request is invalid")
    return value


class ActiveRequestTracker:
    """Bounded process-local request ownership; no request body is retained."""

    def __init__(self, limit: int = MAX_ACTIVE_REQUESTS) -> None:
        if not _integer(limit, minimum=1, maximum=MAX_ACTIVE_REQUESTS):
            raise ValueError("active request limit is invalid")
        self.limit = limit
        self._items: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._condition = threading.Condition()

    @property
    def count(self) -> int:
        with self._condition:
            return len(self._items)

    def begin(
        self, lease_id: str, binding_id: str, binding_version: int, expires_at: int,
    ) -> bool:
        with self._condition:
            if self._closed or lease_id in self._items or len(self._items) >= self.limit \
                    or not _integer(expires_at, minimum=1):
                return False
            self._items[lease_id] = {
                "binding_id": binding_id,
                "binding_version": binding_version,
                "state": "pending",
                "cancelled": False,
                "expires_at": expires_at,
            }
            return True

    def activate(self, lease_id: str) -> bool:
        with self._condition:
            item = self._items.get(lease_id)
            if item is None or item["state"] != "pending" or item["cancelled"]:
                return False
            item["state"] = "active"
            return True

    def finish(self, lease_id: str) -> None:
        with self._condition:
            self._items.pop(lease_id, None)
            self._condition.notify_all()

    def cancelled(self, lease_id: str) -> bool:
        with self._condition:
            item = self._items.get(lease_id)
            return item is None or bool(item["cancelled"])

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        with self._condition:
            selected = tuple(
                lease_id for lease_id, item in self._items.items()
                if item["binding_id"] == binding_id
                and (binding_version is None or item["binding_version"] == binding_version)
            )
            for lease_id in selected:
                item = self._items[lease_id]
                if item["state"] == "pending":
                    self._items.pop(lease_id, None)
                else:
                    item["cancelled"] = True
            self._condition.notify_all()
            return selected

    def close(self) -> tuple[str, ...]:
        with self._condition:
            self._closed = True
            selected = tuple(self._items)
            for lease_id in selected:
                item = self._items[lease_id]
                if item["state"] == "pending":
                    self._items.pop(lease_id, None)
                else:
                    item["cancelled"] = True
            self._condition.notify_all()
            return selected

    def expire(self, now: int) -> tuple[str, ...]:
        with self._condition:
            selected = tuple(
                lease_id for lease_id, item in self._items.items()
                if item["expires_at"] <= now
            )
            for lease_id in selected:
                item = self._items[lease_id]
                if item["state"] == "pending":
                    self._items.pop(lease_id, None)
                else:
                    item["cancelled"] = True
            self._condition.notify_all()
            return selected

    def drain(self, timeout_seconds: float) -> bool:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
                or timeout_seconds < 0 or timeout_seconds > 5:
            raise ValueError("active request drain deadline is invalid")
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self._items:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


class PendingRequestRegistry:
    """Rendezvous metadata for verified guest requests and trusted leases."""

    def __init__(self, service: Any, *, tracker: ActiveRequestTracker | None = None) -> None:
        if not _validate_service_identity(service):
            raise ValueError("pending request service identity is invalid")
        self.service = dict(service)
        self.tracker = tracker or ActiveRequestTracker()
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._closed = False

    def register(
        self,
        request: Any,
        observation: Any,
        authorization: Any,
        *,
        now: int,
    ) -> dict[str, Any]:
        if self._closed or not validate_guest_admission(self.service, observation, request)["ok"] \
                or not isinstance(authorization, dict):
            return bounded_error("transport_denied")
        expected = {"lease_id", "expires_at"}
        if set(authorization) != expected or not _identity(authorization["lease_id"]) \
                or not _integer(authorization["expires_at"], minimum=1) \
                or authorization["expires_at"] <= now:
            return bounded_error("lease_not_pending")
        lease_id = authorization["lease_id"]
        if not self.tracker.begin(
            lease_id, request["binding_id"], request["binding_version"],
            authorization["expires_at"],
        ):
            return bounded_error("request_limit")
        record = {
            "lease_id": lease_id,
            "machine_id": self.service["machine_id"],
            "broker_epoch": self.service["broker_epoch"],
            "binding_id": request["binding_id"],
            "binding_version": request["binding_version"],
            "policy_digest": self.service["policy_digest"],
            "egress_digest": self.service["egress_digest"],
            "broker_digest": self.service["broker_digest"],
            "expires_at": authorization["expires_at"],
            "request": dict(request),
        }
        with self._lock:
            if self._closed or lease_id in self._pending:
                self.tracker.finish(lease_id)
                return bounded_error("lease_not_pending")
            self._pending[lease_id] = record
        return {"ok": True, "state": "credential_pending", "lease_id": lease_id}

    def consume(self, frame: Any, *, now: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not isinstance(frame, dict) or not _identity(frame.get("lease_id")):
            return None, bounded_error("lease_not_pending")
        lease_id = frame["lease_id"]
        with self._lock:
            record = self._pending.pop(lease_id, None)
        if record is None:
            return None, bounded_error("lease_not_pending")
        fields = (
            "lease_id", "machine_id", "broker_epoch", "binding_id", "binding_version",
            "policy_digest", "egress_digest", "broker_digest", "expires_at",
        )
        if any(record[field] != frame.get(field) for field in fields) \
                or record["expires_at"] <= now:
            self.tracker.finish(lease_id)
            return None, bounded_error("lease_identity_mismatch")
        if not self.tracker.activate(lease_id):
            self.tracker.finish(lease_id)
            return None, bounded_error("lease_not_pending")
        return record, {"ok": True, "code": "lease_rendezvous"}

    def finish(self, lease_id: str) -> None:
        self.tracker.finish(lease_id)

    def cancel(self, lease_id: str) -> bool:
        with self._lock:
            removed = self._pending.pop(lease_id, None)
        self.tracker.finish(lease_id)
        return removed is not None

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        with self._lock:
            selected = tuple(
                lease_id for lease_id, item in self._pending.items()
                if item["binding_id"] == binding_id
                and (binding_version is None or item["binding_version"] == binding_version)
            )
            for lease_id in selected:
                self._pending.pop(lease_id, None)
        self.tracker.revoke(binding_id, binding_version)
        return selected

    def expire(self, now: int) -> tuple[str, ...]:
        with self._lock:
            selected = tuple(
                lease_id for lease_id, item in self._pending.items()
                if item["expires_at"] <= now
            )
            for lease_id in selected:
                self._pending.pop(lease_id, None)
        tracked = self.tracker.expire(now)
        return tuple(sorted(set(selected) | set(tracked)))

    def restart(self, service: Any) -> tuple[str, ...]:
        if not _validate_service_identity(service) or service["broker_epoch"] == self.service["broker_epoch"]:
            raise ValueError("broker restart requires a fresh exact identity")
        with self._lock:
            self._closed = True
            selected = tuple(self._pending)
            self._pending.clear()
            self.service = dict(service)
        self.tracker.close()
        return selected

    def close(self) -> tuple[str, ...]:
        with self._lock:
            self._closed = True
            selected = tuple(self._pending)
            self._pending.clear()
        self.tracker.close()
        return selected


class LeaseReceiver:
    """Track terminal lease IDs without owning sockets or credential bytes."""

    __slots__ = ("_service", "_control_plane_uid", "_clock", "_consumed")

    def __init__(self, service: Any, *, control_plane_uid: int, clock) -> None:
        if not _validate_service_identity(service) \
                or not _integer(control_plane_uid, minimum=1, maximum=2**31 - 1) \
                or not callable(clock):
            raise ValueError("lease receiver configuration is invalid")
        self._service = dict(service)
        self._control_plane_uid = control_plane_uid
        self._clock = clock
        self._consumed: set[str] = set()

    def consumed(self, lease_id: str) -> bool:
        return lease_id in self._consumed

    def accept(self, frame: Any, descriptor: Any, *, dispatcher_peer: Any) -> dict[str, Any]:
        if not isinstance(dispatcher_peer, dict) \
                or dispatcher_peer != {"uid": self._control_plane_uid}:
            return bounded_error("dispatcher_denied")
        now = self._clock()
        checked = _validate_frame_identity(self._service, frame, now=now)
        if not checked["ok"]:
            return checked
        lease_id = frame["lease_id"]
        if lease_id in self._consumed:
            return bounded_error("lease_replayed")
        # Terminal consumption precedes descriptor inspection.  A malformed
        # descriptor therefore cannot be repaired and replayed under this ID.
        self._consumed.add(lease_id)
        checked = _validate_descriptor(frame, descriptor)
        if not checked["ok"]:
            return checked
        return bounded_error("lease_handoff_required")


def _require_linux_transport() -> None:
    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED") \
            or not hasattr(socket, "SOCK_SEQPACKET") \
            or not hasattr(socket, "MSG_CMSG_CLOEXEC"):
        raise RuntimeError(
            "trusted lease transport requires Linux SO_PEERCRED, SOCK_SEQPACKET, "
            "and MSG_CMSG_CLOEXEC",
        )


def _require_linux_guest_listener() -> None:
    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_BINDTODEVICE"):
        raise RuntimeError("guest listener requires Linux SO_BINDTODEVICE")


def _recv_exact(connection, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not isinstance(chunk, bytes) or not chunk:
            raise ValueError("bounded frame is truncated")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_guest_frame(connection) -> bytes:
    header = _recv_exact(connection, _FRAME_HEADER.size)
    magic, version, payload_size = _FRAME_HEADER.unpack(header)
    if magic != _GUEST_MAGIC or version != PROTOCOL_VERSION \
            or payload_size > MAX_GUEST_FRAME_BYTES - _FRAME_HEADER.size:
        raise ValueError("guest request frame is invalid")
    packet = header + _recv_exact(connection, payload_size)
    if _readily_observable_trailing(connection):
        raise ValueError("guest request has trailing data")
    return packet


def _readily_observable_trailing(connection) -> bool:
    """Peek without requiring client EOF before sending the response."""
    fake_probe = getattr(connection, "peek_trailing", None)
    if callable(fake_probe):
        return bool(fake_probe())
    peek = getattr(socket, "MSG_PEEK", None)
    nonblocking = getattr(socket, "MSG_DONTWAIT", None)
    if peek is None or nonblocking is None:
        return False
    flags = peek | nonblocking
    try:
        return bool(connection.recv(1, flags))
    except (BlockingIOError, InterruptedError):
        return False


class LinuxGuestEndpoint:
    """Opt-in exact private-veth listener; no default runtime constructs it."""

    def __init__(
        self,
        service: Any,
        *,
        registry: PendingRequestRegistry,
        connection_observer,
        request_authorizer,
        enabled: bool = False,
        socket_factory=None,
        clock=None,
    ) -> None:
        if not _validate_service_identity(service) or not isinstance(registry, PendingRequestRegistry) \
                or registry.service != service or not callable(connection_observer) \
                or not callable(request_authorizer) or not isinstance(enabled, bool):
            raise ValueError("guest listener configuration is invalid")
        self.service = dict(service)
        self.registry = registry
        self.connection_observer = connection_observer
        self.request_authorizer = request_authorizer
        self.enabled = enabled
        self.socket_factory = socket_factory or socket.socket
        self.clock = clock or time.time
        self.listener = None
        self.admission_open = False
        self.terminal_closed = False

    def start(self) -> dict[str, Any]:
        if not self.enabled or self.terminal_closed or self.listener is not None:
            return bounded_error("guest_listener_closed")
        if _running_as_root():
            return bounded_error("root_execution_denied")
        try:
            _require_linux_guest_listener()
            listener = self.socket_factory(socket.AF_INET, socket.SOCK_STREAM, 0)
            listener.settimeout(5.0)
            interface = self.service["guest_interface"].encode("ascii") + b"\0"
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface)
            observed = listener.getsockopt(
                socket.SOL_SOCKET, socket.SO_BINDTODEVICE, len(interface) + 16,
            )
            if not isinstance(observed, bytes) or observed.rstrip(b"\0") != interface.rstrip(b"\0"):
                raise OSError("guest listener interface binding did not persist")
            listener.bind((self.service["host_address"], self.service["guest_port"]))
            listener.listen(MAX_ACTIVE_REQUESTS)
        except (OSError, RuntimeError):
            try:
                listener.close()
            except (NameError, OSError):
                pass
            return bounded_error("guest_listener_unavailable")
        self.listener = listener
        return {"ok": True, "code": "guest_listener_started", "admission_open": False}

    def open_admission(self) -> dict[str, Any]:
        if self.listener is None or self.registry.service != self.service:
            return bounded_error("guest_listener_closed")
        self.admission_open = True
        return {"ok": True, "code": "guest_admission_open"}

    def receive_once(self) -> dict[str, Any]:
        if self.listener is None or not self.admission_open:
            return bounded_error("guest_listener_closed")
        connection = None
        try:
            connection, _address = self.listener.accept()
            connection.settimeout(5.0)
            local = connection.getsockname()
            peer = connection.getpeername()
            if local[:2] != (self.service["host_address"], self.service["guest_port"]) \
                    or peer[0] != self.service["guest_address"]:
                return bounded_error("transport_denied")
            # This observer is kernel-derived state.  It must run before any
            # guest bytes are read or parsed.
            observation = self.connection_observer(connection)
            checked = validate_guest_transport(self.service, observation)
            if not checked["ok"]:
                return checked
            try:
                request = parse_guest_request(_recv_guest_frame(connection))
            except ValueError:
                return bounded_error("guest_frame_invalid")
            checked = validate_guest_admission(self.service, observation, request)
            if not checked["ok"]:
                return checked
            authorization = self.request_authorizer(dict(request))
            result = self.registry.register(
                request, observation, authorization, now=int(self.clock()),
            )
            payload = json.dumps(
                _bounded_document(result), sort_keys=True, separators=(",", ":"),
            ).encode("ascii")
            connection.sendall(payload)
            return result
        except Exception:
            return bounded_error("guest_listener_unavailable")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        self.admission_open = False
        return self.registry.revoke(binding_id, binding_version)

    def expire(self, now: int) -> tuple[str, ...]:
        selected = self.registry.expire(now)
        if selected:
            self.admission_open = False
        return selected

    def close(self) -> dict[str, Any]:
        self.admission_open = False
        self.terminal_closed = True
        listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        self.registry.close()
        return {"ok": True, "code": "guest_listener_closed", "admission_open": False}


def _abstract_lease_address(service: dict[str, Any]) -> bytes:
    identity = hashlib.sha256(
        f"{service['machine_id']}:{service['broker_digest']}".encode("ascii")
    ).hexdigest()[:32]
    return b"\0sandbox-credential-broker-" + identity.encode("ascii")


def _peer_uid(connection) -> int:
    raw = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size,
    )
    if not isinstance(raw, bytes) or len(raw) != _PEER_CREDENTIALS.size:
        raise ValueError("peer credentials are unavailable")
    _pid, uid, _gid = _PEER_CREDENTIALS.unpack(raw)
    if not _integer(uid, minimum=1, maximum=2**31 - 1):
        raise ValueError("peer credentials are invalid")
    return uid


def _extract_one_descriptor(ancillary: Any) -> int:
    if not isinstance(ancillary, list):
        raise ValueError("descriptor control data is invalid")
    descriptors: list[int] = []
    for item in ancillary:
        if not isinstance(item, tuple) or len(item) != 3:
            raise ValueError("descriptor control data is invalid")
        level, kind, data = item
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS \
                or not isinstance(data, bytes) or len(data) != array("i").itemsize:
            raise ValueError("descriptor control data is invalid")
        values = array("i")
        values.frombytes(data)
        descriptors.extend(values)
    if len(descriptors) != 1 or descriptors[0] < 0:
        raise ValueError("exactly one descriptor is required")
    return descriptors[0]


def _close_received_descriptors(ancillary: Any) -> None:
    if not isinstance(ancillary, list):
        return
    for item in ancillary:
        if not isinstance(item, tuple) or len(item) != 3:
            continue
        level, kind, data = item
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS \
                or not isinstance(data, bytes):
            continue
        usable = len(data) - (len(data) % array("i").itemsize)
        values = array("i")
        values.frombytes(data[:usable])
        for descriptor in values:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _linux_descriptor_observation(descriptor: int) -> dict[str, Any]:
    """Observe a descriptor without reading its credential-bearing bytes."""
    _require_linux_transport()
    metadata = os.fstat(descriptor)
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise ValueError("anonymous descriptor identity is unavailable") from exc
    get_seals = getattr(fcntl, "F_GET_SEALS", None)
    seal_values = {
        "write": getattr(fcntl, "F_SEAL_WRITE", None),
        "grow": getattr(fcntl, "F_SEAL_GROW", None),
        "shrink": getattr(fcntl, "F_SEAL_SHRINK", None),
        "seal": getattr(fcntl, "F_SEAL_SEAL", None),
    }
    if get_seals is None or any(value is None for value in seal_values.values()):
        raise ValueError("descriptor seal observation is unavailable")
    seals = fcntl.fcntl(descriptor, get_seals)
    descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    return {
        "descriptor_count": 1,
        "anonymous_memfd": bool(
            stat.S_ISREG(metadata.st_mode)
            and re.fullmatch(r"/memfd:[^/]+ \(deleted\)", target)
        ),
        "close_on_exec": bool(descriptor_flags & fcntl.FD_CLOEXEC),
        "size": metadata.st_size,
        "seals": tuple(
            name for name, value in seal_values.items() if seals & int(value)
        ),
    }


def _wipe(buffer: bytearray | None) -> None:
    if buffer is not None:
        for index in range(len(buffer)):
            buffer[index] = 0


def _read_descriptor_once(descriptor: int, size: int) -> bytearray:
    if not _integer(size, minimum=1, maximum=MAX_DESCRIPTOR_BYTES):
        raise ValueError("descriptor size is invalid")
    os.lseek(descriptor, 0, os.SEEK_SET)
    value = os.read(descriptor, size + 1)
    if len(value) != size:
        raise ValueError("descriptor read size does not match")
    return bytearray(value)


def _parse_acknowledgement(payload: Any, lease_id: str) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SAFE_DOCUMENT_BYTES:
        raise ValueError("lease acknowledgement is invalid")
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("lease acknowledgement is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"lease_id", "outcome"} \
            or value["lease_id"] != lease_id \
            or value["outcome"] not in {"completed", "refused", "indeterminate"}:
        raise ValueError("lease acknowledgement is invalid")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    if canonical != payload:
        raise ValueError("lease acknowledgement is invalid")
    return value


class LinuxKernelFacade:
    """Linux syscalls for dispatcher use; tests inject a non-kernel fake."""

    def require(self) -> None:
        _require_linux_transport()
        if _running_as_root():
            raise RuntimeError("credential broker dispatcher refuses root execution")
        required = (
            "memfd_create", "MFD_CLOEXEC", "MFD_ALLOW_SEALING",
        )
        if any(not hasattr(os, name) for name in required) \
                or not hasattr(fcntl, "F_ADD_SEALS"):
            raise RuntimeError("sealed memfd support is unavailable")

    def connect(self, service: dict[str, Any]):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
        connection.settimeout(5.0)
        connection.connect(_abstract_lease_address(service))
        return connection

    def peer_credentials(self, connection) -> dict[str, int]:
        raw = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size,
        )
        pid, uid, gid = _PEER_CREDENTIALS.unpack(raw)
        return {"pid": pid, "uid": uid, "gid": gid}

    def create_memfd(self) -> int:
        return os.memfd_create(
            "sandbox-credential-lease",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )

    def write_and_seal(self, descriptor: int, material: bytearray) -> None:
        offset = 0
        view = memoryview(material)
        while offset < len(material):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("memfd write did not progress")
            offset += written
        seals = (
            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_SEAL
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)

    def send_descriptor(self, connection, packet: bytes, descriptor: int) -> None:
        rights = array("i", (descriptor,)).tobytes()
        sent = connection.sendmsg(
            [packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)],
        )
        if sent != len(packet):
            raise OSError("lease frame send was incomplete")

    def receive_ack(self, connection) -> bytes:
        value = connection.recv(MAX_SAFE_DOCUMENT_BYTES + 1)
        if len(value) > MAX_SAFE_DOCUMENT_BYTES:
            raise ValueError("lease acknowledgement exceeds the fixed bound")
        return value

    def close(self, value) -> None:
        value.close() if hasattr(value, "close") else os.close(value)


class LinuxLeaseDispatcher:
    """One-attempt trusted dispatcher with no public plaintext-return path."""

    def __init__(
        self,
        service: Any,
        *,
        broker_identity_observer,
        kernel=None,
        clock=None,
        enabled: bool = False,
    ) -> None:
        if not _validate_service_identity(service) or not callable(broker_identity_observer) \
                or not isinstance(enabled, bool):
            raise ValueError("lease dispatcher configuration is invalid")
        self.service = dict(service)
        self.broker_identity_observer = broker_identity_observer
        self.kernel = kernel or LinuxKernelFacade()
        self.clock = clock or time.time
        self.enabled = enabled
        self._attempted: set[str] = set()
        self._attempted_lock = threading.Lock()

    def dispatch(self, frame: Any, lease: Any) -> dict[str, Any]:
        if not self.enabled:
            return bounded_error("lease_channel_closed")
        if not callable(getattr(lease, "consume", None)):
            return bounded_error("lease_handoff_required")
        checked = _validate_frame_identity(self.service, frame, now=int(self.clock()))
        if not checked["ok"]:
            return checked
        lease_id = frame["lease_id"]
        with self._attempted_lock:
            if lease_id in self._attempted:
                return bounded_error("lease_replayed")
            self._attempted.add(lease_id)
        connection = None
        try:
            self.kernel.require()
            connection = self.kernel.connect(self.service)
            kernel_peer = self.kernel.peer_credentials(connection)
            peer = self.broker_identity_observer(connection)
            if not isinstance(peer, dict) or peer.get("pid") != kernel_peer.get("pid") \
                    or peer.get("uid") != kernel_peer.get("uid"):
                return bounded_error("broker_identity_mismatch")
            checked = validate_dispatch_peer(self.service, peer, frame)
            if not checked["ok"]:
                return checked

            def transfer(material: Any) -> dict[str, Any]:
                if not isinstance(material, (bytes, bytearray, memoryview)):
                    return bounded_error("lease_handoff_required")
                buffer = bytearray(material)
                descriptor = None
                sent = False
                try:
                    if not 0 < len(buffer) <= MAX_DESCRIPTOR_BYTES \
                            or len(buffer) != frame["descriptor_size"]:
                        return bounded_error("descriptor_size_mismatch")
                    descriptor = self.kernel.create_memfd()
                    self.kernel.write_and_seal(descriptor, buffer)
                    observation = _linux_descriptor_observation(descriptor) \
                        if isinstance(self.kernel, LinuxKernelFacade) \
                        else self.kernel.descriptor_observation(descriptor)
                    checked_descriptor = _validate_descriptor(frame, observation)
                    if not checked_descriptor["ok"]:
                        return checked_descriptor
                    self.kernel.send_descriptor(
                        connection, encode_lease_frame(frame), descriptor,
                    )
                    sent = True
                    try:
                        acknowledgement = _parse_acknowledgement(
                            self.kernel.receive_ack(connection), frame["lease_id"],
                        )
                    except (OSError, ValueError):
                        return terminal_indeterminate(frame["lease_id"])
                    return {"ok": acknowledgement["outcome"] == "completed",
                            **acknowledgement}
                except Exception:
                    return terminal_indeterminate(frame["lease_id"]) \
                        if sent else bounded_error("lease_channel_unavailable")
                finally:
                    _wipe(buffer)
                    if descriptor is not None:
                        try:
                            self.kernel.close(descriptor)
                        except OSError:
                            pass

            result = lease.consume(transfer)
            if isinstance(result, dict) and result == terminal_indeterminate(frame["lease_id"]):
                return result
            if not isinstance(result, dict) or set(result) != {"ok", "lease_id", "outcome"} \
                    or result.get("lease_id") != frame["lease_id"] \
                    or result.get("outcome") not in {"completed", "refused", "indeterminate"} \
                    or not isinstance(result.get("ok"), bool):
                return bounded_error("lease_channel_unavailable")
            return _bounded_document(result)
        except Exception:
            return bounded_error("lease_channel_unavailable")
        finally:
            if connection is not None:
                try:
                    self.kernel.close(connection)
                except OSError:
                    pass
class LinuxLeaseEndpoint:
    """Opt-in, broker-owned abstract seqpacket lease endpoint.

    Construction is inert and admission is closed.  ``start`` is Linux-only
    and requires an exact external process-identity observer.  No runtime code
    constructs this class by default; T036 owns any later lifecycle wiring.
    """

    __slots__ = (
        "_service", "_control_plane_uid", "_identity_observer", "_socket_factory",
        "_listener", "_enabled", "_admission_open", "_consumed", "_clock",
        "_terminal_closed", "_registry", "_internal_handoff", "_descriptor_reader",
        "_consumed_lock",
    )

    def __init__(
        self,
        service: Any,
        *,
        control_plane_uid: int,
        identity_observer,
        registry: PendingRequestRegistry | None = None,
        internal_handoff=None,
        descriptor_reader=None,
        enabled: bool = False,
        socket_factory=None,
        clock=None,
    ) -> None:
        if not _validate_service_identity(service) \
                or not _integer(control_plane_uid, minimum=1, maximum=2**31 - 1) \
                or not callable(identity_observer) or not isinstance(enabled, bool):
            raise ValueError("trusted lease endpoint configuration is invalid")
        if socket_factory is not None and not callable(socket_factory):
            raise ValueError("trusted lease socket factory is invalid")
        if clock is not None and not callable(clock):
            raise ValueError("trusted lease clock is invalid")
        if enabled and (not isinstance(registry, PendingRequestRegistry)
                        or registry.service != service or not callable(internal_handoff)):
            raise ValueError("enabled lease endpoint requires pending rendezvous and handoff")
        if descriptor_reader is not None and not callable(descriptor_reader):
            raise ValueError("trusted descriptor reader is invalid")
        self._service = dict(service)
        self._control_plane_uid = control_plane_uid
        self._identity_observer = identity_observer
        self._socket_factory = socket_factory or socket.socket
        self._listener = None
        self._enabled = enabled
        self._admission_open = False
        self._consumed: set[str] = set()
        self._consumed_lock = threading.Lock()
        self._clock = clock or __import__("time").time
        self._terminal_closed = False
        self._registry = registry
        self._internal_handoff = internal_handoff
        self._descriptor_reader = descriptor_reader or _read_descriptor_once

    @property
    def admission_open(self) -> bool:
        return self._listener is not None and self._admission_open

    def __repr__(self) -> str:
        with self._consumed_lock:
            consumed_count = len(self._consumed)
        return (
            "LinuxLeaseEndpoint("
            f"started={self._listener is not None!r}, admission_open={self.admission_open!r}, "
            f"consumed_count={consumed_count})"
        )

    def _identity_fresh(self) -> bool:
        try:
            observed = self._identity_observer()
        except Exception:
            return False
        return _validate_service_identity(observed) and observed == self._service

    def start(self) -> dict[str, Any]:
        if not self._enabled or self._terminal_closed:
            return bounded_error("lease_channel_closed")
        if _running_as_root():
            return bounded_error("root_execution_denied")
        try:
            _require_linux_transport()
        except RuntimeError:
            return bounded_error("lease_channel_unavailable")
        if self._listener is not None:
            return bounded_error("lease_channel_closed")
        if not self._identity_fresh():
            return bounded_error("broker_identity_mismatch")
        listener = None
        try:
            listener = self._socket_factory(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            listener.settimeout(5.0)
            listener.bind(_abstract_lease_address(self._service))
            listener.listen(1)
        except OSError:
            if listener is not None:
                listener.close()
            return bounded_error("lease_channel_unavailable")
        self._listener = listener
        return {"ok": True, "code": "lease_channel_started", "admission_open": False}

    def open_admission(self, observed_service: Any) -> dict[str, Any]:
        if self._listener is None or observed_service != self._service \
                or not _validate_service_identity(observed_service) \
                or not self._identity_fresh():
            return bounded_error("broker_identity_mismatch")
        self._admission_open = True
        return {"ok": True, "code": "lease_admission_open"}

    def close_admission(self) -> None:
        self._admission_open = False

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        self.close_admission()
        return self._registry.revoke(binding_id, binding_version) \
            if self._registry is not None else ()

    def expire(self, now: int) -> tuple[str, ...]:
        selected = self._registry.expire(now) if self._registry is not None else ()
        if selected:
            self.close_admission()
        return selected

    def drain(self, timeout_seconds: float = 5.0) -> bool:
        return self._registry.tracker.drain(timeout_seconds) \
            if self._registry is not None else True

    def _send_result(self, connection, value: dict[str, Any]) -> None:
        payload = json.dumps(
            _bounded_document(value), sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        if len(payload) <= MAX_SAFE_DOCUMENT_BYTES:
            try:
                connection.sendall(payload)
            except OSError:
                pass

    def receive_once(self) -> dict[str, Any]:
        if self._listener is None or not self._admission_open:
            return bounded_error("lease_channel_closed")
        if not self._identity_fresh():
            self.close_admission()
            return bounded_error("broker_identity_mismatch")
        connection = None
        descriptor = None
        received_ancillary = None
        material = None
        pending = None
        try:
            connection, _address = self._listener.accept()
            connection.settimeout(5.0)
            if _peer_uid(connection) != self._control_plane_uid:
                result = bounded_error("dispatcher_denied")
                self._send_result(connection, result)
                return result
            ancillary_size = socket.CMSG_SPACE(array("i").itemsize)
            flags = getattr(socket, "MSG_CMSG_CLOEXEC", 0)
            packet, ancillary, message_flags, _peer = connection.recvmsg(
                MAX_FRAME_BYTES, ancillary_size, flags,
            )
            received_ancillary = ancillary
            truncation = getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)
            if message_flags & truncation:
                result = bounded_error("frame_invalid")
                self._send_result(connection, result)
                return result
            try:
                frame = parse_lease_frame(packet)
            except ValueError:
                result = bounded_error("frame_invalid")
                self._send_result(connection, result)
                return result
            checked = _validate_frame_identity(
                self._service, frame, now=int(self._clock()),
            )
            if not checked["ok"]:
                if isinstance(frame.get("lease_id"), str):
                    self._registry.cancel(frame["lease_id"])
                self._send_result(connection, checked)
                return checked
            lease_id = frame["lease_id"]
            with self._consumed_lock:
                if lease_id in self._consumed:
                    replayed = True
                else:
                    self._consumed.add(lease_id)
                    replayed = False
            if replayed:
                self._registry.cancel(lease_id)
                result = bounded_error("lease_replayed")
                self._send_result(connection, result)
                return result
            # Terminal consumption happens before descriptor extraction,
            # metadata inspection, or any future credential application.
            pending, rendezvous = self._registry.consume(
                frame, now=int(self._clock()),
            )
            if not rendezvous["ok"]:
                self._send_result(
                    connection, lease_acknowledgement(lease_id, "refused"),
                )
                return rendezvous
            try:
                descriptor = _extract_one_descriptor(ancillary)
                observation = _linux_descriptor_observation(descriptor)
            except (OSError, RuntimeError, ValueError):
                result = bounded_error("descriptor_type_invalid")
                self._send_result(connection, lease_acknowledgement(lease_id, "refused"))
                return result
            checked = _validate_descriptor(frame, observation)
            if not checked["ok"]:
                self._send_result(connection, lease_acknowledgement(lease_id, "refused"))
                return checked
            try:
                material = self._descriptor_reader(descriptor, frame["descriptor_size"])
                if not isinstance(material, bytearray) \
                        or len(material) != frame["descriptor_size"]:
                    raise ValueError("descriptor reader returned an invalid buffer")
                if self._registry.tracker.cancelled(lease_id):
                    outcome = "refused"
                else:
                    handoff = self._internal_handoff(dict(pending["request"]), material)
                    outcome = handoff.get("outcome") if isinstance(handoff, dict) else None
                    if self._registry.tracker.cancelled(lease_id):
                        outcome = "refused"
                if outcome not in {"completed", "refused", "indeterminate"}:
                    outcome = "indeterminate"
            except Exception:
                outcome = "indeterminate"
            result = {"ok": outcome == "completed",
                      **lease_acknowledgement(lease_id, outcome)}
            self._send_result(connection, lease_acknowledgement(lease_id, outcome))
            return result
        except Exception:
            return bounded_error("lease_channel_unavailable")
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            elif received_ancillary is not None:
                _close_received_descriptors(received_ancillary)
            _wipe(material)
            if pending is not None:
                self._registry.finish(pending["lease_id"])
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def close(self) -> dict[str, Any]:
        self.close_admission()
        self._terminal_closed = True
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._consumed_lock:
            self._consumed.clear()
        if self._registry is not None:
            self._registry.close()
        return {"ok": True, "code": "lease_channel_closed", "admission_open": False}


def service_status(service: Any, *, state: str, admission_open: bool) -> dict[str, Any]:
    if not _validate_service_identity(service) or state not in {
        "credential_pending", "ready", "draining", "closed", "blocked",
    } or not isinstance(admission_open, bool):
        return bounded_error("status_invalid")
    return _bounded_document({
        "ok": True,
        "state": state,
        "admission_open": admission_open,
        "machine_id": service["machine_id"],
        "broker_epoch": service["broker_epoch"],
        "pid": service["pid"],
        "process_start_identity": service["process_start_identity"],
        "policy_digest": service["policy_digest"],
        "egress_digest": service["egress_digest"],
        "broker_digest": service["broker_digest"],
    })


def lease_acknowledgement(lease_id: str, outcome: str, *, ok: bool | None = None) -> dict[str, Any]:
    if not _identity(lease_id) or outcome not in {"completed", "refused", "indeterminate"}:
        return {"lease_id": "invalid", "outcome": "refused"}
    # The wire acknowledgement is intentionally exactly these two fields.
    return {"lease_id": lease_id, "outcome": outcome}


def helper_argv(helper: str, verb: str, service: Any) -> tuple[str, ...]:
    if not isinstance(helper, str) or not helper.startswith("/") \
            or verb not in HELPER_VERBS or not _validate_service_identity(service):
        raise ValueError("credential broker helper request is invalid")
    return (
        helper,
        verb,
        service["machine_id"],
        service["policy_digest"],
        service["egress_digest"],
        service["broker_digest"],
    )


def render_inert_service_contract(service: Any, *, executable: str) -> dict[str, Any]:
    """Render a secret-free description without installing or starting it."""
    if not _validate_service_identity(service) or executable != FIXED_EXECUTABLE:
        raise ValueError("credential broker service contract is invalid")
    argv = (executable, "--machine-id", service["machine_id"], "--closed")
    environment = {
        "SANDBOX_BROKER_EPOCH": service["broker_epoch"],
        "SANDBOX_POLICY_DIGEST": service["policy_digest"],
        "SANDBOX_EGRESS_DIGEST": service["egress_digest"],
        "SANDBOX_BROKER_DIGEST": service["broker_digest"],
    }
    unit = {
        "user": service["service_uid"],
        "executable_digest": service["executable_digest"],
        "config_digest": service["config_digest"],
        "no_new_privileges": True,
        "admission_open": False,
    }
    config = {
        key: service[key] for key in (
            "machine_id", "broker_epoch", "policy_digest", "egress_digest",
            "broker_digest", "guest_interface", "host_address", "guest_address",
            "guest_port",
        )
    }
    return {
        "argv": argv,
        "environment": environment,
        "unit": unit,
        "config": config,
        "status": {"state": "credential_pending", "admission_open": False},
        "stdout": "",
        "stderr": "",
    }


def live_transport_status() -> dict[str, Any]:
    """Never represent local contract code as working Linux transport proof."""
    if not sys.platform.startswith("linux"):
        return bounded_error("live_transport_platform_unsupported")
    if not LIVE_TRANSPORT_IMPLEMENTED:
        return bounded_error("live_transport_unimplemented")
    # This branch remains unreachable until a later reviewed implementation
    # deliberately changes the explicit guard and supplies live proof.
    return bounded_error("live_transport_unproven")


def main(_argv: list[str] | None = None) -> int:
    print(json.dumps(live_transport_status(), sort_keys=True))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
