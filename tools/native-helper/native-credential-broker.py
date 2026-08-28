#!/usr/bin/env python3
"""Closed-by-default credential-broker transport and coordinator.

The Linux listener, descriptor, and dispatcher probes plus guest/result codecs
and pure controller/operation state are explicit opt-in and closed by default.
The local coordinator is runnable only with reviewed injected dependencies; no
ordinary application/runtime path constructs or enables it. Code presence is
not live Ubuntu proof or support-tier authority. Pure validation and injectable
seams keep local tests non-privileged; T036 owns helper/service wiring and proof.
"""

from __future__ import annotations

from array import array
import base64
import fcntl
import hashlib
import ipaddress
import json
import multiprocessing
import os
import re
import selectors
import socket
import stat
import struct
import sys
import threading
import time
import uuid
from types import MappingProxyType
from typing import Any, Mapping
from sandbox.isolation.credential_upstream import (
    CredentialUpstreamError,
    VerifiedHttpsUpstream,
)

from sandbox.isolation.credential_controller_protocol_v2 import (
    AuthorizationIdentity as AuthorizationIdentityV2,
    AuthorizationRegistry as AuthorizationRegistryV2,
    DirectionalSequence as DirectionalSequenceV2,
    LeaseSequence as LeaseSequenceV2,
    PROTOCOL as CONTROLLER_PROTOCOL_V2,
    ProtocolV2Error,
    TemporalObservation as TemporalObservationV2,
    decode_controller_frame as decode_controller_frame_v2,
    decode_lease_frame as decode_lease_frame_v2,
    digest_document as digest_document_v2,
    encode_lease_ack as encode_lease_ack_v2,
    encode_controller_frame as encode_controller_frame_v2,
    post_ack_deadline_v2,
    broker_ack_send_deadline_v2,
    lease_ack_deadline_v2,
    validate_controller_message as validate_controller_message_v2,
)
from sandbox.isolation.credential_controller_lifecycle_v2 import (
    DerivedServiceConfigV2,
    validate_reciprocal_service_plans_v2,
)
from sandbox.isolation.credential_guest_protocol_v2 import (
    AuthorizedEffectContextV2,
    AuthorizedEgressDecisionV2,
    EffectExecutionResultV2,
    EffectExecutionV2,
    GuestProtocolV2Error,
    GuestRequestV2,
    GuestResultV2,
    GuestTransportObservationV2,
    GuestTransportProjectionV2,
    INACTIVITY_TIMEOUT_SECONDS as GUEST_V2_INACTIVITY_SECONDS,
    MAX_DNS_ADDRESSES as GUEST_V2_MAX_DNS_ADDRESSES,
    MAX_OPERATION_MILLISECONDS as GUEST_V2_MAX_OPERATION_MS,
    authorize_egress_decision_v2,
    canonical_egress_projection_v2,
    decode_guest_request_v2,
    encode_guest_result_v2,
    guest_request_digest_v2,
    guest_protocol_registry_digest_v2,
    verify_guest_transport_v2,
)
from sandbox.isolation.credential_controller_service_v2 import (
    BoundGuestSubmitCapabilityV2,
    ControllerServiceConfig as ControllerServiceConfigV2,
    ControllerServiceV2Error,
    ExactProcessIdentityObserver as ExactProcessIdentityObserverV2,
    ProcessIdentity as ProcessIdentityV2,
    _mint_authenticated_broker_composition_receipt_v2,
    _mint_bound_guest_submit_capability_v2,
    abstract_controller_address as abstract_controller_address_v2,
    lease_endpoint_address_v2,
    receive_authenticated_packet as receive_authenticated_packet_v2,
    _peer_credentials as observe_socket_peer_credentials_v2,
)
PROTOCOL_VERSION = 1
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_FRAME_BYTES = 4096
MAX_GUEST_HEADERS_BYTES = 64 * 1024
MAX_GUEST_BODY_BYTES = 1024 * 1024
MAX_GUEST_FRAME_BYTES = (
    ((MAX_GUEST_BODY_BYTES + 2) // 3) * 4 + MAX_GUEST_HEADERS_BYTES + 16 * 1024
)
MAX_ACTIVE_REQUESTS = 16
MAX_REPLAY_LEASES = MAX_ACTIVE_REQUESTS * 4
MAX_GUEST_RESULT_BODY_BYTES = 4 * 1024 * 1024
MAX_GUEST_RESULT_FRAME_BYTES = (
    ((MAX_GUEST_RESULT_BODY_BYTES + 2) // 3) * 4 + MAX_GUEST_HEADERS_BYTES + 16 * 1024
)
MAX_SAFE_DOCUMENT_BYTES = 1024
HELPER_VERBS = (
    "credential-broker-start",
    "credential-broker-status",
    "credential-broker-stop",
)
REQUIRED_SEALS = frozenset(("write", "grow", "shrink", "seal"))
_GUEST_V2_RESULT_HEADERS = frozenset((
    "cache-control", "content-language", "content-type", "etag", "expires",
    "last-modified", "retry-after", "vary",
))


def _authorized_dns_worker_v2(host: str, sender) -> None:
    """Isolated cancellable DNS worker; parent receives at most 17 values."""
    try:
        values = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        selected = []
        for item in values:
            address = item[4][0]
            if address not in selected:
                selected.append(address)
            if len(selected) > GUEST_V2_MAX_DNS_ADDRESSES:
                break
        sender.send((True, tuple(selected)))
    except Exception:
        try:
            sender.send((False, ()))
        except Exception:
            pass
    finally:
        try:
            sender.close()
        except Exception:
            pass


class AuthorizedDnsResolverV2:
    """One-call deadline-bound DNS authority with owned process cancellation."""

    __slots__ = ("_context", "_monotonic", "_active", "_lock", "_closed")

    def __init__(self, *, context=None, monotonic=time.monotonic) -> None:
        selected = context or multiprocessing.get_context("spawn")
        if (not callable(monotonic) or not hasattr(selected, "Pipe")
                or not hasattr(selected, "Process")):
            raise ControllerServiceV2Error("dns_authority_invalid")
        self._context = selected
        self._monotonic = monotonic
        self._active = set()
        self._lock = threading.Lock()
        self._closed = False

    def resolve_until(self, host: str, absolute_monotonic_deadline: float) -> tuple[str, ...]:
        if (self._closed or not isinstance(host, str)
                or isinstance(absolute_monotonic_deadline, bool)
                or not isinstance(absolute_monotonic_deadline, (int, float))):
            raise ControllerServiceV2Error("egress_denied")
        try:
            started = self._monotonic()
            if (isinstance(started, bool) or not isinstance(started, (int, float))
                    or started >= absolute_monotonic_deadline):
                raise ValueError
            receiver, sender = self._context.Pipe(duplex=False)
            process = self._context.Process(
                target=_authorized_dns_worker_v2, args=(host, sender),
                name="credential-dns-v2", daemon=False)
            with self._lock:
                if self._closed:
                    raise ValueError
                self._active.add(process)
            process.start()
            sender.close()
            remaining = absolute_monotonic_deadline - self._monotonic()
            if remaining <= 0 or not receiver.poll(remaining):
                raise TimeoutError
            ok, values = receiver.recv()
            if (ok is not True or type(values) is not tuple
                    or not 1 <= len(values) <= GUEST_V2_MAX_DNS_ADDRESSES
                    or any(not isinstance(value, str) for value in values)):
                raise ValueError
            process.join(max(0.0, absolute_monotonic_deadline - self._monotonic()))
            if process.is_alive():
                raise TimeoutError
            return values
        except Exception:
            if "process" in locals() and process.is_alive():
                try:
                    process.terminate()
                except Exception:
                    pass
                try:
                    process.join(1.0)
                except Exception:
                    pass
            if "process" in locals() and process.is_alive():
                raise ControllerServiceV2Error("dns_cleanup_incomplete") from None
            raise ControllerServiceV2Error("egress_denied") from None
        finally:
            if "receiver" in locals():
                try:
                    receiver.close()
                except Exception:
                    pass
            if "sender" in locals():
                try:
                    sender.close()
                except Exception:
                    pass
            if "process" in locals() and not process.is_alive():
                with self._lock:
                    self._active.discard(process)

    def close(self) -> dict[str, Any]:
        self._closed = True
        incomplete = False
        with self._lock:
            active = tuple(self._active)
        for process in active:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(1.0)
            except Exception:
                incomplete = True
            if process.is_alive():
                incomplete = True
            else:
                with self._lock:
                    self._active.discard(process)
        return {"ok": not incomplete,
                "code": "dns_authority_closed" if not incomplete
                        else "dns_cleanup_incomplete"}


def resolve_authorized_guest_egress_v2(plan: DerivedServiceConfigV2,
                                        request: GuestRequestV2,
                                        upstream: VerifiedHttpsUpstream,
                                        dns_authority: AuthorizedDnsResolverV2, *,
                                        now: str, deadline_unix_ms: int,
                                        wall_clock_ms) -> AuthorizedEgressDecisionV2:
    """Resolve once, then authorize the complete pinned answer from sealed grants."""

    if (not isinstance(plan, DerivedServiceConfigV2) or plan.component != "broker"
            or type(request) is not GuestRequestV2
            or not isinstance(upstream, VerifiedHttpsUpstream)
            or type(dns_authority) is not AuthorizedDnsResolverV2
            or not isinstance(now, str)
            or type(deadline_unix_ms) is not int
            or not callable(wall_clock_ms)):
        raise ControllerServiceV2Error("egress_denied")
    try:
        started_wall = wall_clock_ms()
        started_elapsed = upstream.clock()
        if (type(started_wall) is not int or started_wall >= deadline_unix_ms
                or isinstance(started_elapsed, bool)
                or not isinstance(started_elapsed, (int, float))):
            raise ValueError
        absolute_elapsed_deadline = started_elapsed + min(
            upstream.total_seconds,
            (deadline_unix_ms - started_wall) / 1000.0)
        addresses = dns_authority.resolve_until(
            request.host, absolute_elapsed_deadline)
        finished_elapsed = upstream.clock()
        finished_wall = wall_clock_ms()
        remaining_seconds = (deadline_unix_ms - started_wall) / 1000.0
        if (type(finished_wall) is not int or finished_wall < started_wall
                or finished_wall >= deadline_unix_ms
                or isinstance(finished_elapsed, bool)
                or not isinstance(finished_elapsed, (int, float))
                or finished_elapsed < started_elapsed
                or finished_elapsed - started_elapsed >= min(
                    upstream.total_seconds, remaining_seconds)):
            raise ValueError
        return authorize_egress_decision_v2(
            canonical_egress_projection_v2(plan.document["egress_projection"]),
            host=request.host, sni_hostname=request.host, port=request.port,
            resolved_addresses=addresses, now=now,
        )
    except ControllerServiceV2Error as exc:
        if exc.code == "dns_cleanup_incomplete":
            raise ControllerServiceV2Error("dns_cleanup_incomplete") from None
        raise ControllerServiceV2Error("egress_denied") from None
    except Exception:
        raise ControllerServiceV2Error("egress_denied") from None
# Guarded library seams exist.  This does not mean a runnable service, live
# proof, or an adoptable/default runtime path exists.
LIVE_TRANSPORT_IMPLEMENTED = True
FIXED_EXECUTABLE = "/usr/libexec/sandbox/native-credential-broker"
FIXED_CONFIG_ROOT = "/etc/sandbox/credential-broker"
MAX_CONFIG_BYTES = 16 * 1024
_FRAME_MAGIC = b"SBCL"
_FRAME_HEADER = struct.Struct("!4sBI")
_GUEST_MAGIC = b"SBGR"
_CONTROLLER_MAGIC = b"SBCC"
_GUEST_RESULT_MAGIC = b"SBRS"
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
    "machine_id", "binding_id", "binding_version", "scheme", "host", "port",
    "method", "path", "headers", "body", "content_type", "deadline_ms",
    "correlation_id",
))
_FRAME_FIELDS = frozenset((
    "protocol_version", "lease_id", "broker_epoch", "machine_id",
    "binding_id", "binding_version", "policy_digest", "egress_digest",
    "broker_digest", "operation_id", "request_digest", "expires_at",
    "descriptor_size",
))
_CONTROLLER_IDENTITY_FIELDS = frozenset((
    "uid", "pid", "process_start_identity", "executable_digest",
))
_CLAIM_NEXT_FIELDS = frozenset((
    "type", "machine_id", "broker_epoch", "sequence",
))
_REFUSE_FIELDS = frozenset((
    "type", "machine_id", "broker_epoch", "sequence", "operation_id",
    "request_digest", "code",
))
_CLAIMED_FIELDS = frozenset((
    "type", "machine_id", "broker_epoch", "operation_id", "request_digest",
    "binding_id", "binding_version", "scheme", "host", "port", "method",
    "path", "header_bytes", "body_bytes", "content_type", "deadline_ms",
    "correlation_id",
))
_CONTROL_REFUSAL_FIELDS = frozenset(("type", "code"))
_NO_PENDING_FIELDS = frozenset(("type",))
_LIFECYCLE_FIELDS = frozenset((
    "type", "machine_id", "broker_epoch", "sequence",
))
_LIFECYCLE_RESULT_FIELDS = frozenset(("type", "admission_open"))
_RUNTIME_CONFIG_FIELDS = frozenset((
    "version", "service", "controller", "control_plane_uid",
))
_CONFIG_SERVICE_FIELDS = _SERVICE_FIELDS - {"pid", "process_start_identity"}
CONTROLLER_REFUSAL_CODES = frozenset((
    "binding_unknown", "binding_not_ready", "binding_expired",
    "proof_unavailable", "egress_not_authorized", "request_scope_mismatch",
    "source_unavailable", "lease_unavailable", "operation_cancelled",
))
GUEST_ERROR_CODES = frozenset((
    "adapter_invalid", "binding_expired", "binding_not_ready", "binding_unknown",
    "broker_closed", "concurrency_limit", "egress_not_authorized",
    "guest_coordinator_unavailable", "guest_frame_invalid", "lease_expired",
    "lease_unavailable", "operation_cancelled", "operation_indeterminate",
    "proof_unavailable", "request_limit", "request_scope_mismatch",
    "response_body_too_large", "source_unavailable", "transport_denied",
    "upstream_failed", "upstream_timeout",
))
GUEST_RESPONSE_HEADERS = frozenset((
    "cache-control", "content-language", "content-type", "etag",
    "last-modified", "retry-after",
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
    "guest_request_pending": "guest request is pending",
    "guest_listener_closed": "guest listener is closed",
    "guest_listener_unavailable": "guest listener is unavailable",
    "guest_coordinator_unavailable": "guest coordinator is not implemented",
    "lease_handoff_required": "lease requires an internal broker handoff",
    "lease_ack_indeterminate": "lease acknowledgement is indeterminate",
    "lease_not_pending": "lease has no matching pending request",
    "request_limit": "broker active request limit is reached",
    "controller_denied": "controller identity is denied",
    "controller_message_invalid": "controller message is invalid",
    "operation_not_pending": "broker operation is not pending",
    "operation_claimed": "broker operation is already claimed",
    "operation_indeterminate": "broker operation outcome is indeterminate",
    "operation_cancelled": "broker operation was refused before lease use",
    "request_digest_mismatch": "broker request digest does not match",
    "adapter_invalid": "credential operation adapter is invalid",
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
    "runtime_config_invalid": "credential broker runtime config is invalid",
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


def canonical_runtime_config(value: Any) -> bytes:
    """Return the one accepted secret-free runtime configuration encoding."""
    item = _mapping(value, _RUNTIME_CONFIG_FIELDS)
    service = _mapping(item.get("service"), _CONFIG_SERVICE_FIELDS) if item else None
    if item is None or item["version"] != PROTOCOL_VERSION or service is None:
        raise ValueError("credential broker runtime config is invalid")
    if not _validate_service_identity({
        **service, "pid": 1, "process_start_identity": "runtime:unstarted",
    }):
        raise ValueError("credential broker runtime config is invalid")
    controller = _mapping(item["controller"], _CONTROLLER_IDENTITY_FIELDS)
    if controller is None or not all((
        _integer(controller["uid"], minimum=1, maximum=2**31 - 1),
        _integer(controller["pid"], minimum=1, maximum=2**31 - 1),
        _identity(controller["process_start_identity"]),
        _digest(controller["executable_digest"]),
        item["control_plane_uid"] == controller["uid"],
    )):
        raise ValueError("credential broker runtime config is invalid")
    payload = json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii") + b"\n"
    if len(payload) > MAX_CONFIG_BYTES:
        raise ValueError("credential broker runtime config is invalid")
    return payload


def runtime_config_path(machine_id: str) -> str:
    if not _machine(machine_id):
        raise ValueError("credential broker machine identity is invalid")
    return f"{FIXED_CONFIG_ROOT}/{machine_id}.json"


def runtime_config_path_v2(machine_id: str, component: str = "broker") -> str:
    if not _machine(machine_id) or component not in {"controller", "broker"}:
        raise ValueError("credential broker machine identity is invalid")
    return f"/etc/sandbox/credential-v2/{component}/{machine_id}.json"


class _ConfigKernel:
    open = staticmethod(os.open)
    fstat = staticmethod(os.fstat)
    read = staticmethod(os.read)
    close = staticmethod(os.close)


def load_runtime_config(
    path: str, *, machine_id: str, expected_group_gid: int,
    expected_digest: str, kernel=None,
) -> dict[str, Any]:
    """Load only the fixed canonical config with no symlink or metadata drift."""
    if path != runtime_config_path(machine_id) or not _integer(
        expected_group_gid, minimum=1, maximum=2**31 - 1,
    ) or not _digest(expected_digest):
        raise ValueError("credential broker runtime config authority is invalid")
    kernel = kernel or _ConfigKernel()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = kernel.open(path, flags)
    try:
        observed = kernel.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_uid != 0 \
                or observed.st_gid != expected_group_gid \
                or stat.S_IMODE(observed.st_mode) != 0o640 \
                or not 1 <= observed.st_size <= MAX_CONFIG_BYTES:
            raise ValueError("credential broker runtime config metadata is invalid")
        chunks = bytearray()
        while len(chunks) <= MAX_CONFIG_BYTES:
            chunk = kernel.read(descriptor, min(4096, MAX_CONFIG_BYTES + 1 - len(chunks)))
            if not isinstance(chunk, bytes):
                raise ValueError("credential broker runtime config read is invalid")
            if not chunk:
                break
            chunks.extend(chunk)
        raw = bytes(chunks)
        if len(raw) != observed.st_size or hashlib.sha256(raw).hexdigest() != expected_digest:
            raise ValueError("credential broker runtime config digest is invalid")
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("credential broker runtime config is invalid") from exc
        if canonical_runtime_config(value) != raw:
            raise ValueError("credential broker runtime config is not canonical")
        if value["service"]["machine_id"] != machine_id:
            raise ValueError("credential broker runtime config machine is invalid")
        return value
    finally:
        kernel.close(descriptor)


def load_runtime_config_v2(path: str, *, machine_id: str, component: str,
                           expected_group_gid: int,
                           kernel=None) -> DerivedServiceConfigV2:
    """Load only the exact canonical derived v2 broker config."""
    if (path != runtime_config_path_v2(machine_id, component)
            or component not in {"controller", "broker"}
            or not _integer(expected_group_gid, minimum=1, maximum=2**31 - 1)
            ):
        raise ValueError("credential broker v2 config authority is invalid")
    kernel = kernel or _ConfigKernel()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = kernel.open(path, flags)
    try:
        observed = kernel.fstat(descriptor)
        if (not stat.S_ISREG(observed.st_mode) or observed.st_uid != 0
                or observed.st_gid != expected_group_gid
                or stat.S_IMODE(observed.st_mode) != 0o640
                or not 1 <= observed.st_size <= MAX_CONFIG_BYTES):
            raise ValueError("credential broker v2 config metadata is invalid")
        raw = bytearray()
        while len(raw) <= MAX_CONFIG_BYTES:
            chunk = kernel.read(descriptor, min(4096, MAX_CONFIG_BYTES + 1 - len(raw)))
            if not isinstance(chunk, bytes):
                raise ValueError("credential broker v2 config read is invalid")
            if not chunk:
                break
            raw.extend(chunk)
        payload = bytes(raw)
        if len(payload) != observed.st_size:
            raise ValueError("credential broker v2 config digest is invalid")
        try:
            document = json.loads(payload.decode("ascii"))
            plan = DerivedServiceConfigV2.derive(document)
        except Exception:
            raise ValueError("credential broker v2 config is invalid") from None
        if (plan.component != component or plan.machine_id != machine_id
                or plan.service_gid != expected_group_gid
                or plan.canonical_bytes != payload
                or plan.config_digest != hashlib.sha256(payload).hexdigest()):
            raise ValueError("credential broker v2 config identity is invalid")
        return plan
    finally:
        kernel.close(descriptor)


def _canonical_guest_request(value: Any) -> dict[str, Any]:
    """Mirror the existing BrokerRequest validator and retain machine binding."""
    item = _mapping(value, _GUEST_REQUEST_FIELDS)
    if item is None or not _machine(item["machine_id"]):
        raise ValueError("guest request is invalid")
    try:
        from sandbox.isolation.credential_request_broker import BrokerRequest

        request = BrokerRequest.from_mapping({
            key: item[key] for key in _GUEST_REQUEST_FIELDS if key != "machine_id"
        })
    except Exception as exc:
        raise ValueError("guest request is invalid") from exc
    return {
        "machine_id": item["machine_id"],
        "binding_id": request.binding_id,
        "binding_version": request.binding_version,
        "scheme": request.scheme,
        "host": request.host,
        "port": request.port,
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "body": bytes(request.body),
        "content_type": request.content_type,
        "deadline_ms": request.deadline_ms,
        "correlation_id": request.correlation_id,
    }


def _wire_guest_request(value: Any) -> dict[str, Any]:
    item = _canonical_guest_request(value)
    wire = dict(item)
    wire["body"] = base64.b64encode(item["body"]).decode("ascii")
    return wire


def guest_request_digest(value: Any) -> str:
    wire = _wire_guest_request(value)
    payload = json.dumps(
        wire, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


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
    try:
        requested = _canonical_guest_request(request)
    except ValueError:
        requested = None
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


def validate_control_peer(service: Any, expected: Any, observed: Any, frame: Any) -> dict[str, Any]:
    """Authenticate the controller/dispatcher process seen by the broker."""
    authority = _mapping(expected, _CONTROLLER_IDENTITY_FIELDS)
    peer = _mapping(observed, _CONTROLLER_IDENTITY_FIELDS)
    if not _validate_service_identity(service) or authority is None or peer != authority:
        return bounded_error("dispatcher_denied")
    if not all((
        _integer(peer["uid"], minimum=1, maximum=2**31 - 1),
        _integer(peer["pid"], minimum=1, maximum=2**31 - 1),
        _identity(peer["process_start_identity"]), _digest(peer["executable_digest"]),
    )):
        return bounded_error("dispatcher_denied")
    if not isinstance(frame, dict) or frame.get("broker_epoch") != service["broker_epoch"] \
            or frame.get("machine_id") != service["machine_id"]:
        return bounded_error("broker_epoch_stale")
    return {"ok": True, "code": "dispatcher_verified"}


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
        _identity(value["operation_id"]),
        _digest(value["request_digest"]),
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


def _recover_canonical_lease_prefix(packet: Any) -> dict[str, Any] | None:
    """Recover only a complete canonical frame declared at the packet prefix."""
    if not isinstance(packet, bytes) or len(packet) < _FRAME_HEADER.size:
        return None
    try:
        magic, version, payload_size = _FRAME_HEADER.unpack(packet[:_FRAME_HEADER.size])
    except struct.error:
        return None
    size = _FRAME_HEADER.size + payload_size
    if magic != _FRAME_MAGIC or version != PROTOCOL_VERSION \
            or size > MAX_FRAME_BYTES or size > len(packet):
        return None
    try:
        return parse_lease_frame(packet[:size])
    except ValueError:
        return None


def encode_guest_request(request: Any) -> bytes:
    value = _wire_guest_request(request)
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
        wire = json.loads(payload.decode("ascii"))
        if _mapping(wire, _GUEST_REQUEST_FIELDS) is None \
                or not isinstance(wire["body"], str):
            raise ValueError("guest request is invalid")
        value = dict(wire)
        value["body"] = base64.b64decode(wire["body"], validate=True)
        value = _canonical_guest_request(value)
    except (struct.error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("guest request is invalid") from exc
    if magic != _GUEST_MAGIC or version != PROTOCOL_VERSION or payload_size != len(payload) \
            or encode_guest_request(value) != packet:
        raise ValueError("guest request is invalid")
    return value


def encode_controller_message(message: Any) -> bytes:
    if not isinstance(message, dict):
        raise ValueError("controller message is invalid")
    fields = {
        "ACTIVATE": _LIFECYCLE_FIELDS if "sequence" in message else _LIFECYCLE_RESULT_FIELDS,
        "QUIESCE": _LIFECYCLE_FIELDS if "sequence" in message else _LIFECYCLE_RESULT_FIELDS,
        "CLAIM_NEXT": _CLAIM_NEXT_FIELDS,
        "CLAIMED": _CLAIMED_FIELDS,
        "NO_PENDING": _NO_PENDING_FIELDS,
        "REFUSE": _REFUSE_FIELDS if "operation_id" in message else _CONTROL_REFUSAL_FIELDS,
    }.get(message.get("type"))
    if fields is None or _mapping(message, fields) is None:
        raise ValueError("controller message is invalid")
    kind = message["type"]
    if kind in {"ACTIVATE", "QUIESCE"} and "sequence" not in message:
        if message["admission_open"] is not (kind == "ACTIVATE"):
            raise ValueError("controller message is invalid")
    if kind in {"ACTIVATE", "QUIESCE", "CLAIM_NEXT", "CLAIMED"} \
            and "machine_id" in message and not all((
        _machine(message["machine_id"]), _identity(message["broker_epoch"]),
    )):
        raise ValueError("controller message is invalid")
    if kind in {"ACTIVATE", "QUIESCE", "CLAIM_NEXT"} and "sequence" in message \
            and not _integer(message["sequence"], minimum=1):
        raise ValueError("controller message is invalid")
    if kind == "CLAIMED" and not all((
        _identity(message["operation_id"]), _digest(message["request_digest"]),
        _identity(message["binding_id"]),
        _integer(message["binding_version"], minimum=1),
        message["scheme"] == "https", _identity(message["host"]),
        _integer(message["port"], minimum=1, maximum=65535),
        _identity(message["method"]), isinstance(message["path"], str),
        _integer(message["header_bytes"], maximum=MAX_GUEST_HEADERS_BYTES),
        _integer(message["body_bytes"], maximum=MAX_GUEST_BODY_BYTES),
        message["content_type"] is None or isinstance(message["content_type"], str),
        _integer(message["deadline_ms"], minimum=1, maximum=30_000),
        _identity(message["correlation_id"]),
    )):
        raise ValueError("controller message is invalid")
    if kind == "REFUSE" and "operation_id" in message and not all((
        _machine(message["machine_id"]), _identity(message["broker_epoch"]),
        _integer(message["sequence"], minimum=1), _identity(message["operation_id"]),
        _digest(message["request_digest"]), message["code"] in CONTROLLER_REFUSAL_CODES,
    )):
        raise ValueError("controller message is invalid")
    if kind == "REFUSE" and "operation_id" not in message \
            and _safe_code(message["code"]) != message["code"]:
        raise ValueError("controller message is invalid")
    payload = json.dumps(
        message, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    if _FRAME_HEADER.size + len(payload) > MAX_FRAME_BYTES:
        raise ValueError("controller message exceeds the fixed bound")
    return _FRAME_HEADER.pack(_CONTROLLER_MAGIC, PROTOCOL_VERSION, len(payload)) + payload


def parse_controller_message(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, bytes) or not _FRAME_HEADER.size <= len(packet) <= MAX_FRAME_BYTES:
        raise ValueError("controller message is invalid")
    try:
        magic, version, payload_size = _FRAME_HEADER.unpack(packet[:_FRAME_HEADER.size])
        payload = packet[_FRAME_HEADER.size:]
        value = json.loads(payload.decode("ascii"))
    except (struct.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("controller message is invalid") from exc
    if magic != _CONTROLLER_MAGIC or version != PROTOCOL_VERSION \
            or payload_size != len(payload) or encode_controller_message(value) != packet:
        raise ValueError("controller message is invalid")
    return value


def encode_guest_terminal_result(result: Any) -> bytes:
    """Encode one terminal guest result without operation or lease identities."""
    if not isinstance(result, dict) or "operation_id" in result or "lease_id" in result:
        raise ValueError("guest terminal result is invalid")
    if result.get("ok") is True:
        fields = {"ok", "status", "headers", "body", "correlation_id"}
        if set(result) != fields or not _integer(result["status"], minimum=100, maximum=599) \
                or not isinstance(result["headers"], dict) \
                or not isinstance(result["body"], bytes) \
                or len(result["body"]) > MAX_GUEST_RESULT_BODY_BYTES \
                or not _identity(result["correlation_id"]):
            raise ValueError("guest terminal result is invalid")
        header_bytes = 0
        normalized_names: set[str] = set()
        for name, text in result["headers"].items():
            normalized = name.lower() if isinstance(name, str) else ""
            if not isinstance(name, str) or not re.fullmatch(
                r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name,
            ) or name != normalized or normalized in normalized_names \
                    or normalized not in GUEST_RESPONSE_HEADERS \
                    or not isinstance(text, str) \
                    or any(ord(character) < 32 or ord(character) == 127 for character in text):
                raise ValueError("guest terminal result is invalid")
            normalized_names.add(normalized)
            try:
                header_bytes += len(name.encode("ascii")) + len(text.encode("utf-8")) + 4
            except UnicodeEncodeError as exc:
                raise ValueError("guest terminal result is invalid") from exc
        if header_bytes > MAX_GUEST_HEADERS_BYTES:
            raise ValueError("guest terminal result is invalid")
        wire = dict(result)
        wire["body"] = base64.b64encode(result["body"]).decode("ascii")
    else:
        fields = {"ok", "code", "message", "retryable", "correlation_id"}
        if set(result) != fields or result.get("ok") is not False \
                or result["code"] not in GUEST_ERROR_CODES \
                or not isinstance(result["message"], str) or len(result["message"]) > 256 \
                or not isinstance(result["retryable"], bool) \
                or not _identity(result["correlation_id"]):
            raise ValueError("guest terminal result is invalid")
        expected_message = _SAFE_SUMMARIES.get(
            result["code"], "credential broker request refused",
        )
        if result["message"] != expected_message:
            raise ValueError("guest terminal result is invalid")
        wire = dict(result)
    payload = json.dumps(
        wire, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    if _FRAME_HEADER.size + len(payload) > MAX_GUEST_RESULT_FRAME_BYTES:
        raise ValueError("guest terminal result exceeds the fixed bound")
    return _FRAME_HEADER.pack(
        _GUEST_RESULT_MAGIC, PROTOCOL_VERSION, len(payload),
    ) + payload


def parse_guest_terminal_result(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, bytes) or not _FRAME_HEADER.size <= len(packet) \
            <= MAX_GUEST_RESULT_FRAME_BYTES:
        raise ValueError("guest terminal result is invalid")
    try:
        magic, version, payload_size = _FRAME_HEADER.unpack(packet[:_FRAME_HEADER.size])
        payload = packet[_FRAME_HEADER.size:]
        wire = json.loads(payload.decode("ascii"))
        value = dict(wire)
        if value.get("ok") is True:
            value["body"] = base64.b64decode(value["body"], validate=True)
    except (struct.error, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError("guest terminal result is invalid") from exc
    if magic != _GUEST_RESULT_MAGIC or version != PROTOCOL_VERSION \
            or payload_size != len(payload) or encode_guest_terminal_result(value) != packet:
        raise ValueError("guest terminal result is invalid")
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


class LegacyPendingLeaseRegistry:
    """Isolated pre-controller lease test seam; not the operation protocol.

    This remains only so the guarded descriptor endpoint can be tested before a
    real coordinator exists.  Its records now bind operation and request digest
    metadata, but it MUST NOT be wired as the guest/controller operation path.
    """

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
        expected = {"lease_id", "operation_id", "request_digest", "expires_at"}
        if set(authorization) != expected or not _identity(authorization["lease_id"]) \
                or not _identity(authorization["operation_id"]) \
                or not _digest(authorization["request_digest"]) \
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
            "operation_id": authorization["operation_id"],
            "request_digest": authorization["request_digest"],
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
            "lease_id", "operation_id", "request_digest", "machine_id",
            "broker_epoch", "binding_id", "binding_version",
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


class PendingOperationRegistry:
    """Private guest/result rendezvous using broker-generated operation IDs."""

    def __init__(
        self,
        service: Any,
        *,
        id_factory=None,
        limit: int = MAX_ACTIVE_REQUESTS,
    ) -> None:
        if not _validate_service_identity(service) \
                or not _integer(limit, minimum=1, maximum=MAX_ACTIVE_REQUESTS):
            raise ValueError("pending operation registry configuration is invalid")
        self.service = dict(service)
        self._id_factory = id_factory or (lambda: f"op-{uuid.uuid4().hex}")
        if not callable(self._id_factory):
            raise ValueError("pending operation ID factory is invalid")
        self._limit = limit
        self._items: dict[str, dict[str, Any]] = {}
        self._connection_index: dict[str, str] = {}
        self._lock = threading.Lock()
        self._closed = False

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def submit(
        self,
        request: Any,
        observation: Any,
        *,
        now: int,
    ) -> dict[str, Any]:
        checked = validate_guest_admission(self.service, observation, request)
        if not checked["ok"] or not _integer(now, minimum=0):
            return bounded_error("transport_denied")
        try:
            canonical = _canonical_guest_request(request)
            digest = guest_request_digest(canonical)
            operation_id = self._id_factory()
        except (TypeError, ValueError):
            return bounded_error("guest_frame_invalid")
        connection_id = observation["connection_identity"]
        if not _identity(operation_id):
            return bounded_error("operation_not_pending")
        with self._lock:
            active = sum(item["result"] is None for item in self._items.values())
            if self._closed or active >= self._limit:
                return bounded_error("request_limit")
            if len(self._items) >= self._limit * 2:
                for stale_id, stale in tuple(self._items.items()):
                    if stale["result"] is not None:
                        self._items.pop(stale_id, None)
                        self._connection_index.pop(stale["connection_identity"], None)
                        if len(self._items) < self._limit * 2:
                            break
            if operation_id in self._items or connection_id in self._connection_index:
                return bounded_error("operation_not_pending")
            self._items[operation_id] = {
                "operation_id": operation_id,
                "request_digest": digest,
                "request": canonical,
                "connection_identity": connection_id,
                "created_at": now,
                "deadline_at": now + max(1, (canonical["deadline_ms"] + 999) // 1000),
                "state": "pending",
                "claim_owner": None,
                "lease_id": None,
                "expires_at": None,
                "result": None,
            }
            self._connection_index[connection_id] = operation_id
        return _bounded_document({
            "ok": True,
            "state": "credential_pending",
            "correlation_id": canonical["correlation_id"],
        })

    def claim_next(self, owner: str, *, now: int) -> dict[str, Any]:
        if not _identity(owner) or not _integer(now, minimum=0):
            return bounded_error("controller_denied")
        with self._lock:
            for item in self._items.values():
                if item["state"] == "pending":
                    if item["deadline_at"] <= now:
                        item["state"] = "refused"
                        item["result"] = bounded_error("lease_expired")
                        continue
                    item["state"] = "claimed"
                    item["claim_owner"] = owner
                    request = item["request"]
                    header_bytes = sum(
                        len(name.encode("ascii")) + len(text.encode("utf-8")) + 4
                        for name, text in request["headers"].items()
                    )
                    claimed = {
                        "type": "CLAIMED",
                        "operation_id": item["operation_id"],
                        "request_digest": item["request_digest"],
                        "machine_id": self.service["machine_id"],
                        "broker_epoch": self.service["broker_epoch"],
                        "binding_id": request["binding_id"],
                        "binding_version": request["binding_version"],
                        "scheme": request["scheme"],
                        "host": request["host"],
                        "port": request["port"],
                        "method": request["method"],
                        "path": request["path"],
                        "header_bytes": header_bytes,
                        "body_bytes": len(request["body"]),
                        "content_type": request["content_type"],
                        "deadline_ms": request["deadline_ms"],
                        "correlation_id": request["correlation_id"],
                    }
                    try:
                        encode_controller_message(claimed)
                    except ValueError:
                        item["state"] = "refused"
                        item["result"] = bounded_error("request_scope_mismatch")
                        continue
                    return claimed
        return {"type": "NO_PENDING"}

    def bind_lease(self, frame: Any, *, owner: str, now: int) -> dict[str, Any]:
        checked = _validate_frame_identity(self.service, frame, now=now)
        if not checked["ok"]:
            return checked
        operation_id = frame["operation_id"]
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "claimed" or item["claim_owner"] != owner:
                return bounded_error("operation_not_pending")
            if item["deadline_at"] <= now:
                item["state"] = "refused"
                item["result"] = bounded_error("lease_expired")
                return bounded_error("lease_expired")
            if item["request_digest"] != frame["request_digest"]:
                item["state"] = "refused"
                item["result"] = bounded_error("operation_cancelled")
                return bounded_error("request_digest_mismatch")
            request = item["request"]
            if request["binding_id"] != frame["binding_id"] \
                    or request["binding_version"] != frame["binding_version"]:
                item["state"] = "refused"
                item["result"] = bounded_error("operation_cancelled")
                return bounded_error("lease_identity_mismatch")
            item["state"] = "lease_bound"
            item["lease_id"] = frame["lease_id"]
            item["expires_at"] = frame["expires_at"]
            return {"ok": True, "code": "lease_rendezvous"}

    def trusted_request(self, operation_id: str, request_digest: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["state"] != "lease_bound":
                return None
            return dict(item["request"])

    def connection_identity(self, operation_id: str) -> str | None:
        """Return private rendezvous identity; never serialized to either peer."""
        with self._lock:
            item = self._items.get(operation_id)
            return item["connection_identity"] if item is not None else None

    def claim_owner(self, operation_id: str) -> str | None:
        """Resolve private claim ownership; callers cannot supply an owner value."""
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "claimed":
                return None
            return item["claim_owner"]

    def operation_terminal(self, operation_id: str) -> bool:
        with self._lock:
            item = self._items.get(operation_id)
            return item is not None and item["result"] is not None

    def complete(self, operation_id: str, request_digest: str, result: Any) -> dict[str, Any]:
        from sandbox.isolation.credential_request_broker import BrokerResponse

        if not isinstance(result, BrokerResponse):
            return self.complete_indeterminate(operation_id, request_digest)
        public = _guest_public_result(result)
        try:
            encode_guest_terminal_result(public)
        except ValueError:
            return self.complete_indeterminate(operation_id, request_digest)
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["state"] != "lease_bound":
                return bounded_error("operation_not_pending")
            if result.correlation_id != item["request"]["correlation_id"]:
                item["state"] = "indeterminate"
                item["result"] = bounded_error("operation_indeterminate")
                return item["result"]
            item["state"] = "completed"
            item["result"] = public
        return public

    def complete_refused(
        self, operation_id: str, request_digest: str, *, code: str,
    ) -> dict[str, Any]:
        if code not in GUEST_ERROR_CODES or code == "operation_indeterminate":
            return self.complete_indeterminate(operation_id, request_digest)
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["state"] != "lease_bound":
                return bounded_error("operation_not_pending")
            result = bounded_error(code)
            item["state"] = "refused"
            item["result"] = result
            return result

    def complete_indeterminate(
        self, operation_id: str, request_digest: str,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["state"] != "lease_bound":
                return bounded_error("operation_not_pending")
            result = bounded_error("operation_indeterminate")
            item["state"] = "indeterminate"
            item["result"] = result
            return result

    def fail_lease_attempt(
        self, operation_id: str, request_digest: str, *, post_use: bool = False,
    ) -> bool:
        """Terminalize an exact claimed operation after a failed lease attempt."""
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["state"] not in {"claimed", "lease_bound"}:
                return False
            uncertain = post_use or item["state"] == "lease_bound"
            item["state"] = "indeterminate" if uncertain else "refused"
            item["result"] = bounded_error(
                "operation_indeterminate" if uncertain else "operation_cancelled")
            return True

    def refuse(
        self, operation_id: str, request_digest: str, *, owner: str, code: str,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["request_digest"] != request_digest \
                    or item["claim_owner"] != owner or item["state"] != "claimed":
                return bounded_error("operation_not_pending")
            result = bounded_error(code)
            item["state"] = "refused"
            item["result"] = result
            return result

    def controller_disconnected(self, owner: str) -> tuple[str, ...]:
        selected = []
        with self._lock:
            for operation_id, item in self._items.items():
                if item["claim_owner"] == owner and item["state"] in {"claimed", "lease_bound"}:
                    if item["state"] == "claimed":
                        item["state"] = "refused"
                        item["result"] = bounded_error("operation_cancelled")
                    else:
                        item["state"] = "indeterminate"
                        item["result"] = bounded_error("operation_indeterminate")
                    selected.append(operation_id)
        return tuple(selected)

    def guest_result(self, connection_identity: str, *, consume: bool = False) -> dict[str, Any]:
        with self._lock:
            operation_id = self._connection_index.get(connection_identity)
            item = self._items.get(operation_id) if operation_id else None
            if item is None:
                return bounded_error("operation_not_pending")
            result = item["result"]
            if result is None:
                return _bounded_document({
                    "ok": True,
                    "state": "credential_pending",
                    "correlation_id": item["request"]["correlation_id"],
                })
            public = dict(result)
            if public.get("ok") is False and "correlation_id" not in public:
                public["correlation_id"] = item["request"]["correlation_id"]
            if consume:
                self._items.pop(operation_id, None)
                self._connection_index.pop(connection_identity, None)
            return public

    def guest_disconnected(self, connection_identity: str) -> dict[str, Any]:
        """Terminalize and reclaim the private record when its guest leaves."""
        with self._lock:
            operation_id = self._connection_index.pop(connection_identity, None)
            item = self._items.pop(operation_id, None) if operation_id else None
            if item is None:
                return bounded_error("operation_not_pending")
            if item["result"] is not None:
                return dict(item["result"])
            if item["state"] == "lease_bound":
                return bounded_error("operation_indeterminate")
            return bounded_error("operation_cancelled")

    def fail_guest_transport(self, connection_identity: str) -> bool:
        with self._lock:
            operation_id = self._connection_index.get(connection_identity)
            item = self._items.get(operation_id) if operation_id else None
            if item is None or item["result"] is not None:
                return False
            if item["state"] == "lease_bound":
                item["state"] = "indeterminate"
                item["result"] = bounded_error("operation_indeterminate")
            else:
                item["state"] = "refused"
                item["result"] = bounded_error("guest_frame_invalid")
            return True

    def close(self) -> tuple[str, ...]:
        with self._lock:
            self._closed = True
            selected = tuple(self._items)
            for item in self._items.values():
                if item["result"] is None:
                    if item["state"] == "lease_bound":
                        item["state"] = "indeterminate"
                        item["result"] = bounded_error("operation_indeterminate")
                    else:
                        item["state"] = "refused"
                        item["result"] = bounded_error("operation_cancelled")
            return selected

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        selected = []
        with self._lock:
            for operation_id, item in self._items.items():
                request = item["request"]
                if request["binding_id"] == binding_id \
                        and (binding_version is None
                             or request["binding_version"] == binding_version) \
                        and item["result"] is None:
                    if item["state"] == "lease_bound":
                        item["state"] = "indeterminate"
                        item["result"] = bounded_error("operation_indeterminate")
                    else:
                        item["state"] = "refused"
                        item["result"] = bounded_error("operation_cancelled")
                    selected.append(operation_id)
        return tuple(selected)

    def expire(self, now: int) -> tuple[str, ...]:
        if not _integer(now, minimum=0):
            return ()
        selected = []
        with self._lock:
            for operation_id, item in self._items.items():
                expired = item["deadline_at"] <= now \
                    or (item["expires_at"] is not None and item["expires_at"] <= now)
                if expired and item["result"] is None:
                    if item["state"] == "lease_bound":
                        item["state"] = "indeterminate"
                        item["result"] = bounded_error("operation_indeterminate")
                    else:
                        item["state"] = "refused"
                        item["result"] = bounded_error("lease_expired")
                    selected.append(operation_id)
        return tuple(selected)


def _guest_public_result(value: Any) -> dict[str, Any]:
    """Return only the reviewed guest response/error shape; never internal IDs."""
    try:
        from sandbox.isolation.credential_request_broker import BrokerResponse

        if isinstance(value, BrokerResponse):
            return {
                "ok": True,
                "status": value.status,
                "headers": dict(value.headers),
                "body": bytes(value.body),
                "correlation_id": value.correlation_id,
            }
    except ImportError:
        pass
    return bounded_error("adapter_invalid")


class ControllerClaimChannel:
    """Pure authenticated CLAIM_NEXT/REFUSE state machine for seqpacket use."""

    def __init__(
        self, service: Any, registry: PendingOperationRegistry, controller: Any,
        *, clock=None,
    ) -> None:
        identity = _mapping(controller, _CONTROLLER_IDENTITY_FIELDS)
        if not _validate_service_identity(service) or not isinstance(registry, PendingOperationRegistry) \
                or registry.service != service or identity is None or not all((
                    _integer(identity["uid"], minimum=1, maximum=2**31 - 1),
                    _integer(identity["pid"], minimum=1, maximum=2**31 - 1),
                    _identity(identity["process_start_identity"]),
                    _digest(identity["executable_digest"]),
                )) or (clock is not None and not callable(clock)):
            raise ValueError("controller claim channel configuration is invalid")
        self.service = dict(service)
        self.registry = registry
        self.controller = dict(identity)
        self._sequences: dict[str, int] = {}
        self._lock = threading.Lock()
        self._clock = clock or time.time

    def handle(self, message: Any, *, observed_peer: Any, connection_identity: str) -> dict[str, Any]:
        if observed_peer != self.controller or not _identity(connection_identity):
            return bounded_error("controller_denied")
        if not isinstance(message, dict):
            return bounded_error("controller_message_invalid")
        if message.get("type") not in {"ACTIVATE", "QUIESCE", "CLAIM_NEXT", "REFUSE"}:
            return bounded_error("controller_message_invalid")
        expected = (_LIFECYCLE_FIELDS if message["type"] in {"ACTIVATE", "QUIESCE"}
                    else _CLAIM_NEXT_FIELDS if message["type"] == "CLAIM_NEXT"
                    else _REFUSE_FIELDS)
        value = _mapping(message, expected)
        if value is None or value["machine_id"] != self.service["machine_id"] \
                or value["broker_epoch"] != self.service["broker_epoch"] \
                or not _integer(value["sequence"], minimum=1):
            return bounded_error("controller_message_invalid")
        if value["type"] == "REFUSE" and (
            not _identity(value["operation_id"])
            or not _digest(value["request_digest"])
            or value["code"] not in CONTROLLER_REFUSAL_CODES
        ):
            return bounded_error("controller_message_invalid")
        with self._lock:
            expected_sequence = self._sequences.get(connection_identity, 0) + 1
            if value["sequence"] != expected_sequence:
                return bounded_error("controller_message_invalid")
            self._sequences[connection_identity] = expected_sequence
        if value["type"] in {"ACTIVATE", "QUIESCE"}:
            return {"type": value["type"]}
        if value["type"] == "CLAIM_NEXT":
            return self.registry.claim_next(
                connection_identity, now=int(self._clock()),
            )
        refused = self.registry.refuse(
            value["operation_id"], value["request_digest"], owner=connection_identity,
            code=value["code"],
        )
        return {"type": "REFUSE", "code": refused.get("code", "broker_failed")}

    def disconnect(self, connection_identity: str) -> tuple[str, ...]:
        with self._lock:
            self._sequences.pop(connection_identity, None)
        return self.registry.controller_disconnected(connection_identity)


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
    """Opt-in private-veth endpoint; coordinator injection retains the guest."""

    def __init__(
        self,
        service: Any,
        *,
        registry: PendingOperationRegistry,
        connection_observer,
        enabled: bool = False,
        socket_factory=None,
        clock=None,
        coordinator=None,
    ) -> None:
        if not _validate_service_identity(service) or not isinstance(registry, PendingOperationRegistry) \
                or registry.service != service or not callable(connection_observer) \
                or not isinstance(enabled, bool):
            raise ValueError("guest listener configuration is invalid")
        self.service = dict(service)
        self.registry = registry
        self.connection_observer = connection_observer
        self.enabled = enabled
        self.socket_factory = socket_factory or socket.socket
        self.clock = clock or time.time
        self.listener = None
        self.admission_open = False
        self.terminal_closed = False
        if coordinator is not None and (not isinstance(coordinator, CredentialBrokerCoordinator)
                                        or coordinator.service != self.service):
            raise ValueError("guest coordinator is invalid")
        self.coordinator = coordinator

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
        if self.coordinator is not None:
            opened = self.coordinator.open_admission(self.service)
            if not opened.get("ok"):
                return opened
        self.admission_open = True
        return {"ok": True, "code": "guest_admission_open"}

    def activate_from_coordinator(self) -> dict[str, Any]:
        if self.listener is None or self.coordinator is None:
            return bounded_error("guest_listener_closed")
        self.admission_open = True
        return {"ok": True, "code": "guest_admission_open"}

    def quiesce_from_coordinator(self) -> None:
        self.admission_open = False

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
            if self.coordinator is not None:
                result = self.coordinator.retain_guest(connection, observation, request)
                if result.get("ok"):
                    connection = None
                    return result
                if "correlation_id" not in result:
                    result = {**result, "correlation_id": request["correlation_id"]}
            else:
                result = {**bounded_error("guest_coordinator_unavailable"),
                          "correlation_id": request["correlation_id"]}
            connection.sendall(encode_guest_terminal_result(result))
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
        return (self.coordinator.revoke(binding_id, binding_version)
                if self.coordinator is not None else self.registry.revoke(binding_id, binding_version))

    def expire(self, now: int) -> tuple[str, ...]:
        selected = (self.coordinator.expire(now) if self.coordinator is not None
                    else self.registry.expire(now))
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
        self.coordinator.close() if self.coordinator is not None else self.registry.close()
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


def _close_received_descriptors(ancillary: Any, *, exclude: Any = ()) -> None:
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
            if descriptor >= 0 and descriptor not in exclude:
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


class CredentialOperationAdapter:
    """Descriptor-backed execution through the existing typed request broker."""

    def __init__(self, target: Any, *, binding: Any = None) -> None:
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker
        from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream

        if isinstance(target, VerifiedHttpsUpstream):
            raise ValueError("direct verified upstream adapter is forbidden")
        if not isinstance(target, CredentialRequestBroker) \
                or not isinstance(getattr(target, "upstream", None), VerifiedHttpsUpstream):
            raise ValueError("credential operation adapter target is invalid")
        if binding is not None:
            from sandbox.isolation.credential_binding import CredentialBinding
            if not isinstance(binding, CredentialBinding) \
                    or binding.instance_id != target.instance_id:
                raise ValueError("credential operation adapter binding is invalid")
        self._target = target
        self._binding = binding

    def execute(self, request: Any, material: bytearray | None, *, machine_id: str):
        if machine_id != self._target.instance_id or not isinstance(material, bytearray) \
                or not 0 < len(material) <= MAX_DESCRIPTOR_BYTES:
            raise ValueError("descriptor-backed credential operation is invalid")
        consumed = False

        class DescriptorLease:
            def consume(_self, callback):
                nonlocal consumed
                if consumed or not callable(callback):
                    raise ValueError("descriptor credential lease is consumed")
                consumed = True
                return callback(bytes(material))

        broker_request = dict(request)
        if broker_request.pop("machine_id", None) != machine_id:
            raise ValueError("descriptor-backed request identity is invalid")
        if self._binding is not None and (
            broker_request.get("binding_id") != self._binding.binding_id
            or broker_request.get("binding_version") != self._binding.version
        ):
            raise ValueError("descriptor binding changed")
        return self._target.request_with_lease(
            broker_request, DescriptorLease(), transport_identity=machine_id,
        )


class OfflineTestOperationAdapter:
    """Explicit fake-only adapter; construction requires an offline test gate."""

    def __init__(self, callback, *, offline_test: bool = False) -> None:
        if offline_test is not True or not callable(callback):
            raise ValueError("offline credential adapter is disabled")
        self._callback = callback

    def execute(self, request: Any, material: bytearray | None, *, machine_id: str):
        return self._callback(dict(request), material)


class BoundedAuditSink:
    """Bounded secret-free lifecycle/effect audit seam."""

    _FIELDS = frozenset(("event", "phase", "machine_id", "outcome"))
    _EVENTS = frozenset(("credential_effect", "lifecycle"))
    _PHASES = frozenset(("pre", "post"))
    _OUTCOMES = frozenset((
        "attempted", "completed", "refused", "indeterminate", "activated", "quiesced",
    ))

    def __init__(self, callback=None, *, limit: int = 128) -> None:
        if callback is not None and not callable(callback):
            raise ValueError("credential audit callback is invalid")
        if not _integer(limit, minimum=1, maximum=1024):
            raise ValueError("credential audit limit is invalid")
        self._callback = callback
        self._limit = limit
        self._records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._records)

    def append(self, value: Any) -> bool:
        item = _mapping(value, self._FIELDS)
        if item is None or item["event"] not in self._EVENTS \
                or item["phase"] not in self._PHASES \
                or not _machine(item["machine_id"]) \
                or item["outcome"] not in self._OUTCOMES:
            return False
        with self._lock:
            if len(self._records) >= self._limit:
                return False
            if self._callback is not None:
                try:
                    accepted = self._callback(dict(item)) is True
                except Exception:
                    accepted = False
                if not accepted:
                    return False
            self._records.append(dict(item))
            return True


class CredentialBrokerCoordinator:
    """Retain guest connections through claim, descriptor use, and terminal SBRS."""

    def __init__(self, service: Any, *, controller: Any, adapter: Any,
                 descriptor_reader=None, descriptor_closer=None, clock=None,
                 audit_sink=None,
                 replay_limit: int = MAX_REPLAY_LEASES,
                 enabled: bool = False) -> None:
        if not _validate_service_identity(service) or not isinstance(enabled, bool) \
                or not isinstance(adapter, (CredentialOperationAdapter, OfflineTestOperationAdapter)) \
                or (audit_sink is not None and not isinstance(audit_sink, BoundedAuditSink)) \
                or not _integer(replay_limit, minimum=1, maximum=MAX_REPLAY_LEASES):
            raise ValueError("credential coordinator configuration is invalid")
        self.service = dict(service)
        self.registry = PendingOperationRegistry(service)
        self.claims = ControllerClaimChannel(service, self.registry, controller, clock=clock)
        self.adapter = adapter
        self.reader = descriptor_reader or _read_descriptor_once
        self.closer = descriptor_closer or os.close
        self.clock = clock or time.time
        self.audit = audit_sink or BoundedAuditSink()
        self.enabled = enabled
        self.admission_open = False
        self._guests: dict[str, Any] = {}
        self._consumed: dict[str, tuple[int, str]] = {}
        self._prepared: dict[object, dict[str, Any]] = {}
        self._replay_limit = replay_limit
        self._lock = threading.Lock()
        self._closed = False
        self._quiesced = False
        self._guest_endpoint = None
        self._lease_endpoint = None

    def attach_endpoints(self, *, guest=None, lease=None) -> None:
        if guest is not None and getattr(guest, "coordinator", None) is not self:
            raise ValueError("guest endpoint authority is invalid")
        if lease is not None and getattr(lease, "coordinator", None) is not self:
            raise ValueError("lease endpoint authority is invalid")
        self._guest_endpoint = guest
        self._lease_endpoint = lease

    def open_admission(self, observed_service: Any) -> dict[str, Any]:
        if not self.enabled or self._closed or self._quiesced \
                or not self.admission_open or observed_service != self.service \
                or not _validate_service_identity(observed_service):
            return bounded_error("guest_listener_closed")
        return {"ok": True, "code": "guest_admission_open"}

    def _activate(self) -> dict[str, Any]:
        if not self.enabled or self._closed or self._quiesced:
            return bounded_error("guest_listener_closed")
        for endpoint in (self._guest_endpoint, self._lease_endpoint):
            if endpoint is not None and endpoint.activate_from_coordinator().get("ok") is not True:
                for opened in (self._guest_endpoint, self._lease_endpoint):
                    if opened is not None: opened.quiesce_from_coordinator()
                return bounded_error("guest_listener_closed")
        event = {"event": "lifecycle", "phase": "post",
                 "machine_id": self.service["machine_id"], "outcome": "activated"}
        if not self.audit.append(event):
            self._quiesce_endpoints()
            return bounded_error("operation_indeterminate")
        self.admission_open = True
        return {"type": "ACTIVATE", "admission_open": True}

    def quiesce(self) -> dict[str, Any]:
        self.admission_open = False
        self._quiesce_endpoints()
        self._quiesced = True
        self._clear_prepared_attempts()
        affected = self.registry.close()
        for operation_id in affected:
            guest = self.registry.connection_identity(operation_id)
            if guest is not None:
                self._deliver(guest)
        self.audit.append({"event": "lifecycle", "phase": "post",
                           "machine_id": self.service["machine_id"],
                           "outcome": "quiesced"})
        return {"type": "QUIESCE", "admission_open": False}

    def _quiesce_endpoints(self) -> None:
        self.admission_open = False
        for endpoint in (self._guest_endpoint, self._lease_endpoint):
            if endpoint is not None: endpoint.quiesce_from_coordinator()

    def retain_guest(self, connection: Any, observation: Any, request: Any) -> dict[str, Any]:
        if not self.admission_open or connection is None:
            return bounded_error("guest_listener_closed")
        checked = validate_guest_admission(self.service, observation, request)
        if not checked["ok"]:
            return checked
        identity = observation["connection_identity"]
        result = self.registry.submit(request, observation, now=int(self.clock()))
        if not result.get("ok"):
            return result
        with self._lock:
            if self._closed or identity in self._guests:
                self.registry.guest_disconnected(identity)
                return bounded_error("operation_not_pending")
            self._guests[identity] = connection
        return result

    def handle_controller(self, message: Any, *, observed_peer: Any,
                          connection_identity: str) -> dict[str, Any]:
        kind = message.get("type") if isinstance(message, dict) else None
        if kind in {"ACTIVATE", "QUIESCE"}:
            result = self.claims.handle(message, observed_peer=observed_peer,
                                        connection_identity=connection_identity)
            if result.get("type") == "ACTIVATE":
                return self._activate()
            if result.get("type") == "QUIESCE":
                return self.quiesce()
            return result
        if not self.admission_open:
            return bounded_error("controller_denied")
        result = self.claims.handle(message, observed_peer=observed_peer,
                                    connection_identity=connection_identity)
        if isinstance(message, dict) and message.get("type") == "REFUSE" \
                and result.get("type") == "REFUSE" \
                and result.get("code") in GUEST_ERROR_CODES:
            guest = self.registry.connection_identity(message.get("operation_id"))
            if guest is not None:
                self._deliver(guest)
        return result

    def _deliver(self, connection_identity: str) -> dict[str, Any]:
        result = self.registry.guest_result(connection_identity)
        try:
            packet = encode_guest_terminal_result(result)
        except ValueError:
            result = {**bounded_error("operation_indeterminate"),
                      "correlation_id": result.get("correlation_id", "corr-invalid")}
            packet = encode_guest_terminal_result(result)
        with self._lock:
            connection = self._guests.pop(connection_identity, None)
        if connection is None:
            return bounded_error("operation_not_pending")
        try:
            connection.sendall(packet)
        except Exception:
            self.registry.guest_disconnected(connection_identity)
            try: connection.close()
            except Exception: pass
            return bounded_error("operation_indeterminate")
        self.registry.guest_result(connection_identity, consume=True)
        try: connection.close()
        except Exception: pass
        return result

    def _deliver_operation(self, operation_id: str, lease_id: str) -> dict[str, Any]:
        connection_identity = self.registry.connection_identity(operation_id)
        if connection_identity is None:
            return {"ok": False, "lease_id": lease_id, "outcome": "indeterminate"}
        delivered = self._deliver(connection_identity)
        return {"ok": bool(delivered.get("ok")), "lease_id": lease_id,
                "outcome": "completed" if delivered.get("ok") else
                "indeterminate" if delivered.get("code") == "operation_indeterminate"
                else "refused"}

    def _fail_lease_attempt(self, frame: Any, lease_id: str, *, post_use: bool = False) -> dict[str, Any]:
        operation_id = frame.get("operation_id") if isinstance(frame, dict) else None
        request_digest = frame.get("request_digest") if isinstance(frame, dict) else None
        if _identity(operation_id) and _digest(request_digest):
            self.registry.fail_lease_attempt(
                operation_id, request_digest, post_use=post_use,
            )
            if self.registry.operation_terminal(operation_id) \
                    and self.registry.connection_identity(operation_id) is not None:
                return self._deliver_operation(operation_id, lease_id)
        return {"ok": False, "lease_id": lease_id, "outcome": "refused"}

    def _record_lease_attempt(self, lease_id: str, expires_at: Any) -> str:
        now = int(self.clock())
        if not _integer(expires_at, minimum=1):
            return "invalid"
        with self._lock:
            for consumed_id, (expiry, epoch) in tuple(self._consumed.items()):
                if expiry <= now or epoch != self.service["broker_epoch"]:
                    self._consumed.pop(consumed_id, None)
            if lease_id in self._consumed:
                return "replayed"
            if len(self._consumed) >= self._replay_limit:
                return "exhausted"
            self._consumed[lease_id] = (expires_at, self.service["broker_epoch"])
            return "recorded"

    def _clear_prepared_attempts(
        self, *, owner: str | None = None, connection_identity: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        affected = []
        with self._lock:
            for token, prepared in tuple(self._prepared.items()):
                if owner is not None and prepared["owner"] != owner:
                    continue
                if connection_identity is not None \
                        and prepared["connection_identity"] != connection_identity:
                    continue
                self._prepared.pop(token, None)
                affected.append(dict(prepared))
        return tuple(affected)

    def _prepare_lease(self, frame: Any, *, dispatcher_peer: Any) -> dict[str, Any]:
        lease_id = frame.get("lease_id") if isinstance(frame, dict) else None
        if not self.admission_open or not _identity(lease_id):
            return {"ok": False, "lease_id": lease_id or "invalid", "outcome": "refused"}
        attempt = self._record_lease_attempt(lease_id, frame.get("expires_at"))
        if attempt != "recorded":
            if attempt == "exhausted":
                return self._fail_lease_attempt(frame, lease_id)
            return {"ok": False, "lease_id": lease_id, "outcome": "refused"}
        if not validate_control_peer(self.service, self.claims.controller, dispatcher_peer, frame)["ok"]:
            return self._fail_lease_attempt(frame, lease_id)
        operation_id = frame.get("operation_id")
        owner = self.registry.claim_owner(operation_id)
        if owner is None or not self.registry.bind_lease(frame, owner=owner, now=int(self.clock())).get("ok"):
            return self._fail_lease_attempt(frame, lease_id)
        try:
            canonical_frame = encode_lease_frame(frame)
        except ValueError:
            return self._fail_lease_attempt(frame, lease_id, post_use=True)
        token = object()
        exhausted = False
        with self._lock:
            if len(self._prepared) >= self._replay_limit:
                exhausted = True
            else:
                self._prepared[token] = {
                    "lease_id": lease_id,
                    "operation_id": operation_id,
                    "request_digest": frame["request_digest"],
                    "owner": owner,
                    "connection_identity": self.registry.connection_identity(operation_id),
                    "expires_at": frame["expires_at"],
                    "epoch": self.service["broker_epoch"],
                    "frame_digest": hashlib.sha256(canonical_frame).digest(),
                }
        if exhausted:
            return self._fail_lease_attempt(frame, lease_id, post_use=True)
        return {"ok": True, "lease_id": lease_id, "outcome": "pending",
                "prepared_attempt": token}

    def _consume_prepared_attempt(
        self, token: Any, frame: Any,
    ) -> tuple[bool, dict[str, Any] | None]:
        with self._lock:
            prepared = self._prepared.pop(token, None)
        if prepared is None:
            return False, None
        try:
            now = int(self.clock())
            frame_digest = hashlib.sha256(encode_lease_frame(frame)).digest()
        except Exception:
            now = prepared["expires_at"]
            frame_digest = None
        matched = prepared["expires_at"] > now \
            and prepared["epoch"] == self.service["broker_epoch"] \
            and frame_digest == prepared["frame_digest"]
        return matched, prepared

    def _terminalize_prepared_refusal(self, prepared: dict[str, Any]) -> dict[str, Any]:
        self.registry.complete_refused(
            prepared["operation_id"], prepared["request_digest"],
            code="lease_unavailable",
        )
        return self._deliver_operation(prepared["operation_id"], prepared["lease_id"])

    def _discard_prepared_attempt(self, token: Any) -> None:
        with self._lock:
            self._prepared.pop(token, None)

    def accept_descriptor(self, frame: Any, descriptor: Any, observation: Any, *,
                          dispatcher_peer: Any) -> dict[str, Any]:
        prepared = self._prepare_lease(frame, dispatcher_peer=dispatcher_peer)
        return self._accept_prepared_descriptor(
            frame, descriptor, observation,
            prepared_attempt=prepared.get("prepared_attempt"),
        )

    def _accept_prepared_descriptor(self, frame: Any, descriptor: Any, observation: Any, *,
                                    prepared_attempt: Any) -> dict[str, Any]:
        material = None
        effect_attempted = False
        prepared = None
        try:
            lease_id = frame.get("lease_id") if isinstance(frame, dict) else None
            if not self.admission_open or not _identity(lease_id):
                return bounded_error("lease_channel_closed")
            matched, prepared = self._consume_prepared_attempt(prepared_attempt, frame)
            if not matched:
                if prepared is not None:
                    return self._terminalize_prepared_refusal(prepared)
                return {"ok": False, "lease_id": lease_id, "outcome": "refused"}
            # Consumption is terminal before descriptor inspection or credential read.
            checked = _validate_descriptor(frame, observation)
            if not checked["ok"]:
                self.registry.complete_refused(frame["operation_id"], frame["request_digest"],
                                               code="lease_unavailable")
                return self._deliver_operation(frame["operation_id"], lease_id)
            try:
                material = self.reader(descriptor, frame["descriptor_size"])
            except Exception:
                self.registry.complete_indeterminate(
                    frame["operation_id"], frame["request_digest"])
                return self._deliver_operation(frame["operation_id"], lease_id)
            if not isinstance(material, bytearray) or len(material) != frame["descriptor_size"]:
                self.registry.complete_indeterminate(
                    frame["operation_id"], frame["request_digest"])
                return self._deliver_operation(frame["operation_id"], lease_id)
            request = self.registry.trusted_request(frame["operation_id"], frame["request_digest"])
            if request is None:
                self.registry.complete_indeterminate(
                    frame["operation_id"], frame["request_digest"])
                return self._deliver_operation(frame["operation_id"], lease_id)
            pre_effect = {"event": "credential_effect", "phase": "pre",
                          "machine_id": self.service["machine_id"], "outcome": "attempted"}
            if not self.audit.append(pre_effect):
                self.registry.complete_refused(
                    frame["operation_id"], frame["request_digest"], code="lease_unavailable")
                return self._deliver_operation(frame["operation_id"], lease_id)
            effect_attempted = True
            try:
                response = self.adapter.execute(request, material,
                                                machine_id=self.service["machine_id"])
            except Exception:
                response = None
                outcome = "indeterminate"
            else:
                from sandbox.isolation.credential_request_broker import BrokerResponse
                if isinstance(response, BrokerResponse):
                    outcome = "completed"
                elif isinstance(response, dict) and response.get("outcome") == "refused":
                    outcome = "refused"
                else:
                    outcome = "indeterminate"
            post_effect = {"event": "credential_effect", "phase": "post",
                           "machine_id": self.service["machine_id"], "outcome": outcome}
            if not self.audit.append(post_effect):
                outcome = "indeterminate"
            if outcome == "completed":
                self.registry.complete(frame["operation_id"], frame["request_digest"], response)
            elif outcome == "refused":
                self.registry.complete_refused(
                    frame["operation_id"], frame["request_digest"], code="broker_failed")
            else:
                self.registry.complete_indeterminate(
                    frame["operation_id"], frame["request_digest"])
            return self._deliver_operation(frame["operation_id"], lease_id)
        except Exception:
            if prepared is None:
                lease_id = frame.get("lease_id") if isinstance(frame, dict) else None
                return {"ok": False, "lease_id": lease_id or "invalid",
                        "outcome": "refused"}
            if effect_attempted:
                self.registry.complete_indeterminate(
                    prepared["operation_id"], prepared["request_digest"],
                )
            else:
                self.registry.complete_refused(
                    prepared["operation_id"], prepared["request_digest"],
                    code="lease_unavailable",
                )
            return self._deliver_operation(
                prepared["operation_id"], prepared["lease_id"],
            )
        finally:
            _wipe(material)
            try: self.closer(descriptor)
            except Exception: pass

    def begin_lease(self, frame: Any, *, dispatcher_peer: Any) -> dict[str, Any]:
        return self._prepare_lease(frame, dispatcher_peer=dispatcher_peer)

    def reject_bound_descriptor(self, frame: Any, *, prepared_attempt: Any) -> dict[str, Any]:
        matched, prepared = self._consume_prepared_attempt(prepared_attempt, frame)
        if not matched:
            if prepared is not None:
                return self._terminalize_prepared_refusal(prepared)
            return {"ok": False, "lease_id": frame.get("lease_id", "invalid"),
                    "outcome": "refused"}
        self.registry.complete_refused(frame["operation_id"], frame["request_digest"], code="lease_unavailable")
        return self._deliver_operation(frame["operation_id"], frame["lease_id"])

    def reject_lease(self, frame: Any) -> dict[str, Any]:
        """Consume and terminalize a parsed lease whose ancillary data is invalid."""
        lease_id = frame.get("lease_id") if isinstance(frame, dict) else None
        if not self.admission_open or not _identity(lease_id):
            return {"lease_id": lease_id if _identity(lease_id) else "invalid",
                    "outcome": "refused"}
        attempt = self._record_lease_attempt(lease_id, frame.get("expires_at"))
        if attempt == "replayed":
            return {"lease_id": lease_id, "outcome": "refused"}
        return lease_acknowledgement(
            lease_id, self._fail_lease_attempt(frame, lease_id).get("outcome", "refused"),
        )

    def controller_disconnected(self, connection_identity: str) -> tuple[str, ...]:
        self._clear_prepared_attempts(owner=connection_identity)
        affected = self.claims.disconnect(connection_identity)
        for operation_id in affected:
            guest = self.registry.connection_identity(operation_id)
            if guest is not None:
                self._deliver(guest)
        return affected

    def guest_disconnected(self, connection_identity: str) -> dict[str, Any]:
        prepared = self._clear_prepared_attempts(connection_identity=connection_identity)
        for attempt in prepared:
            self.registry.fail_lease_attempt(
                attempt["operation_id"], attempt["request_digest"], post_use=True,
            )
        with self._lock:
            connection = self._guests.pop(connection_identity, None)
        result = self.registry.guest_disconnected(connection_identity)
        if connection is not None:
            try: connection.close()
            except Exception: pass
        return result

    def guest_trailing(self, connection_identity: str) -> dict[str, Any]:
        if not self.registry.fail_guest_transport(connection_identity):
            return bounded_error("operation_not_pending")
        return self._deliver(connection_identity)

    def revoke(self, binding_id: str, binding_version: int | None = None) -> tuple[str, ...]:
        self._quiesce_endpoints()
        prepared = self._clear_prepared_attempts()
        affected = self.registry.revoke(binding_id, binding_version)
        for attempt in prepared:
            if attempt["operation_id"] not in affected:
                self._terminalize_prepared_refusal(attempt)
        for operation_id in affected:
            guest = self.registry.connection_identity(operation_id)
            if guest is not None: self._deliver(guest)
        return affected

    def expire(self, now: int) -> tuple[str, ...]:
        affected = self.registry.expire(now)
        prepared = self._clear_prepared_attempts()
        self._quiesce_endpoints()
        for attempt in prepared:
            if attempt["operation_id"] not in affected:
                self._terminalize_prepared_refusal(attempt)
        for operation_id in affected:
            guest = self.registry.connection_identity(operation_id)
            if guest is not None: self._deliver(guest)
        return affected

    def close(self) -> dict[str, Any]:
        self._quiesce_endpoints()
        self._closed = True
        self._clear_prepared_attempts()
        affected = self.registry.close()
        for operation_id in affected:
            guest = self.registry.connection_identity(operation_id)
            if guest is not None: self._deliver(guest)
        with self._lock:
            leftovers = tuple(self._guests.values()); self._guests.clear()
        for connection in leftovers:
            try: connection.close()
            except Exception: pass
        return {"ok": True, "code": "coordinator_closed", "admission_open": False}


def _abstract_controller_address(service: dict[str, Any]) -> bytes:
    identity = hashlib.sha256(
        f"controller:{service['machine_id']}:{service['broker_digest']}".encode("ascii")
    ).hexdigest()[:32]
    return b"\0sandbox-credential-controller-" + identity.encode("ascii")


def _peer_pid_uid(connection) -> tuple[int, int]:
    raw = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size,
    )
    if not isinstance(raw, bytes) or len(raw) != _PEER_CREDENTIALS.size:
        raise ValueError("peer credentials are unavailable")
    pid, uid, _gid = _PEER_CREDENTIALS.unpack(raw)
    if not _integer(pid, minimum=1, maximum=2**31 - 1) \
            or not _integer(uid, minimum=1, maximum=2**31 - 1):
        raise ValueError("peer credentials are invalid")
    return pid, uid


def _packet_pid_uid(ancillary: Any) -> tuple[int, int]:
    credentials = []
    scm_credentials = getattr(socket, "SCM_CREDENTIALS", None)
    if scm_credentials is None or not isinstance(ancillary, list):
        raise ValueError("packet credentials are unavailable")
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            raise ValueError("packet rights are forbidden")
        if level == socket.SOL_SOCKET and kind == scm_credentials:
            if not isinstance(payload, bytes) or len(payload) != _PEER_CREDENTIALS.size:
                raise ValueError("packet credentials are invalid")
            credentials.append(_PEER_CREDENTIALS.unpack(payload)[:2])
    if len(credentials) != 1:
        raise ValueError("packet credentials are invalid")
    pid, uid = credentials[0]
    if not _integer(pid, minimum=1, maximum=2**31 - 1) \
            or not _integer(uid, minimum=1, maximum=2**31 - 1):
        raise ValueError("packet credentials are invalid")
    return pid, uid


def _linux_process_start_identity(pid: int) -> str:
    if not sys.platform.startswith("linux") or not _integer(pid, minimum=1):
        raise ValueError("Linux process identity is unavailable")
    with open(f"/proc/{pid}/stat", "r", encoding="ascii") as stream:
        value = stream.read(4096)
    end = value.rfind(")")
    fields = value[end + 2:].split() if end >= 0 else []
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError("Linux process identity is unavailable")
    return f"{pid}:{fields[19]}"


def _linux_executable_digest(pid: int) -> str:
    if not sys.platform.startswith("linux") or not _integer(pid, minimum=1):
        raise ValueError("Linux executable identity is unavailable")
    digest = hashlib.sha256()
    with open(f"/proc/{pid}/exe", "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def process_cgroup_identity_v2(plan: DerivedServiceConfigV2) -> str:
    if not isinstance(plan, DerivedServiceConfigV2):
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return f"/system.slice/{plan.document['unit_identity']}"


def _linux_start_ticks_v2(pid: int) -> int:
    value = _linux_process_start_identity(pid)
    try:
        selected = int(value.rsplit(":", 1)[1])
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if selected < 1:
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return selected


def _linux_cgroup_pid_v2(plan: DerivedServiceConfigV2) -> int:
    path = f"/sys/fs/cgroup{process_cgroup_identity_v2(plan)}/cgroup.procs"
    try:
        with open(path, "r", encoding="ascii") as stream:
            values = stream.read(128).splitlines()
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError
        pid = int(values[0])
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if not 1 <= pid <= 2**31 - 1:
        raise ControllerServiceV2Error("peer_identity_unavailable")
    return pid


def _linux_process_details_v2(pid: int, plan: DerivedServiceConfigV2) -> Mapping[str, Any]:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="ascii") as stream:
            status = stream.read(16384).splitlines()
        uid_line = next(line for line in status if line.startswith("Uid:"))
        gid_line = next(line for line in status if line.startswith("Gid:"))
        uids = uid_line.split()[1:]
        gids = gid_line.split()[1:]
        if len(uids) != 4 or len(gids) != 4 or len(set(uids)) != 1 or len(set(gids)) != 1:
            raise ValueError
        with open(f"/proc/{pid}/cgroup", "r", encoding="ascii") as stream:
            cgroup = stream.read(4096).splitlines()
        if cgroup != [f"0::{process_cgroup_identity_v2(plan)}"]:
            raise ValueError
        return {
            "uid": int(uids[0]), "gid": int(gids[0]),
            "executable_digest": _linux_executable_digest(pid),
            "unit_digest": plan.document["unit_digest"],
            "config_digest": plan.document["own_config_digest"],
        }
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None


def pin_process_identity_v2(plan: DerivedServiceConfigV2, *,
                            cgroup_pid_reader=_linux_cgroup_pid_v2,
                            start_reader=_linux_start_ticks_v2,
                            detail_reader=_linux_process_details_v2) -> ProcessIdentityV2:
    """Pin one exact systemd-cgroup process using start/observe/start."""

    if (not isinstance(plan, DerivedServiceConfigV2)
            or not callable(cgroup_pid_reader) or not callable(start_reader)
            or not callable(detail_reader)
            or plan.document["process_identity_authority"] !=
               "sealed_systemd_cgroup_v2"):
        raise ControllerServiceV2Error("peer_identity_unavailable")
    try:
        pid = cgroup_pid_reader(plan)
        first = start_reader(pid)
        details = detail_reader(pid, plan)
        second = start_reader(pid)
        identity = ProcessIdentityV2(
            uid=details["uid"], gid=details["gid"], pid=pid,
            start_ticks=first,
            executable_digest=details["executable_digest"],
            unit_digest=details["unit_digest"],
            config_digest=details["config_digest"])
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if (first != second
            or identity.uid != plan.document["service_uid"]
            or identity.gid != plan.document["service_gid"]
            or identity.executable_digest != plan.document["executable_digest"]
            or identity.unit_digest != plan.document["unit_digest"]
            or identity.config_digest != plan.document["own_config_digest"]):
        raise ControllerServiceV2Error("peer_identity_mismatch")
    return identity


def pin_reciprocal_process_identities_v2(controller: DerivedServiceConfigV2,
                                         broker_plan: DerivedServiceConfigV2,
                                         **readers):
    """Validate both plans before the first cgroup or proc observation."""

    validate_reciprocal_service_plans_v2(controller, broker_plan)
    return (
        pin_process_identity_v2(controller, **readers),
        pin_process_identity_v2(broker_plan, **readers),
    )


def pinned_process_identity_observer_v2(expected: ProcessIdentityV2):
    if type(expected) is not ProcessIdentityV2:
        raise ControllerServiceV2Error("identity_observer_invalid")
    def observe(pid: int, uid: int, gid: int) -> ProcessIdentityV2:
        if (pid, uid, gid) != (expected.pid, expected.uid, expected.gid):
            raise ControllerServiceV2Error("peer_identity_mismatch")
        return expected
    return observe


class LinuxKernelTopologyObserverV2:
    """Observe socket-owned facts and refuse facts unavailable without authority."""

    __slots__ = ()

    def __call__(self, connection, projection: GuestTransportProjectionV2, peer):
        if (type(projection) is not GuestTransportProjectionV2
                or not isinstance(peer, tuple) or len(peer) < 2):
            raise ControllerServiceV2Error("guest_transport_denied")
        try:
            local = connection.getsockname()
            device = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 32)
            interface = device.rstrip(b"\0").decode("ascii")
            # A TCP peer tuple cannot prove the peer namespace or the nft
            # default-deny ruleset. Never synthesize those authority facts.
            return GuestTransportObservationV2(
                machine_id=projection.machine_id, family="AF_INET",
                socket_type="SOCK_STREAM", interface=interface,
                bind_to_device_readback=interface, subnet=projection.subnet,
                local_address=local[0], local_port=local[1],
                peer_address=peer[0], forwarded=False, loopback=False,
                route_interface=interface, route_source=peer[0],
                network_namespace_isolated=False,
                default_egress_denied=False, default_route_absent=False)
        except Exception:
            raise ControllerServiceV2Error("guest_transport_denied") from None


class LinuxNftDestinationSetObserverV2:
    """Closed production observer until T022 supplies reviewed nft evidence."""

    __slots__ = ()

    def __call__(self, _decision):
        raise ControllerServiceV2Error("egress_denied")


class LinuxPeerIdentityObserver:
    """Recheck PID-start and executable digest after kernel peer authentication."""

    def __init__(self, expected: Any, *, start_reader=None, digest_reader=None) -> None:
        if _mapping(expected, _CONTROLLER_IDENTITY_FIELDS) is None \
                and _mapping(expected, _PEER_FIELDS) is None:
            raise ValueError("Linux peer identity authority is invalid")
        self.expected = dict(expected)
        self.start_reader = start_reader or _linux_process_start_identity
        self.digest_reader = digest_reader or _linux_executable_digest

    def __call__(self, _connection, pid: int, uid: int) -> dict[str, Any]:
        if pid != self.expected["pid"] or uid != self.expected["uid"]:
            return {}
        try:
            start = self.start_reader(pid)
            digest = self.digest_reader(pid)
            confirmed_start = self.start_reader(pid)
        except Exception:
            return {}
        if start != confirmed_start or start != self.expected["process_start_identity"] \
                or digest != self.expected["executable_digest"]:
            return {}
        return dict(self.expected)


def observe_runtime_service(config: Any, *, pid=None, start_reader=None,
                            digest_reader=None) -> dict[str, Any]:
    item = _mapping(config, _RUNTIME_CONFIG_FIELDS)
    if item is None:
        raise ValueError("credential broker runtime identity is invalid")
    pid = os.getpid() if pid is None else pid
    start_reader = start_reader or _linux_process_start_identity
    digest_reader = digest_reader or _linux_executable_digest
    first = start_reader(pid)
    digest = digest_reader(pid)
    second = start_reader(pid)
    service = {**item["service"], "pid": pid, "process_start_identity": first}
    if first != second or digest != service["executable_digest"] \
            or not _validate_service_identity(service):
        raise ValueError("credential broker runtime identity is invalid")
    return service


class LinuxControllerEndpoint:
    """Closed-by-default authenticated controller seqpacket endpoint."""

    def __init__(self, service: Any, coordinator: CredentialBrokerCoordinator, *,
                 controller: Any, identity_observer, enabled: bool = False,
                 socket_factory=None) -> None:
        if not _validate_service_identity(service) or coordinator.service != service \
                or _mapping(controller, _CONTROLLER_IDENTITY_FIELDS) is None \
                or not callable(identity_observer) or not isinstance(enabled, bool) \
                or (socket_factory is not None and not callable(socket_factory)):
            raise ValueError("controller endpoint configuration is invalid")
        self.service = dict(service)
        self.coordinator = coordinator
        self.controller = dict(controller)
        self.identity_observer = identity_observer
        self.enabled = enabled
        self.socket_factory = socket_factory or socket.socket
        self.listener = None
        self.connections: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        if not self.enabled or self.listener is not None:
            return bounded_error("controller_denied")
        if _running_as_root():
            return bounded_error("root_execution_denied")
        try:
            _require_linux_transport()
            listener = self.socket_factory(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            if hasattr(socket, "SO_PASSCRED"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            listener.bind(_abstract_controller_address(self.service))
            listener.listen(MAX_ACTIVE_REQUESTS)
        except (OSError, RuntimeError):
            try: listener.close()
            except (NameError, OSError): pass
            return bounded_error("controller_denied")
        self.listener = listener
        return {"ok": True, "code": "controller_started", "admission_open": False}

    def _process(self, connection, connection_identity: str) -> dict[str, Any]:
        ancillary = None
        try:
            peer = _peer_pid_uid(connection)
            ancillary_size = socket.CMSG_SPACE(_PEER_CREDENTIALS.size)
            packet, ancillary, flags, _address = connection.recvmsg(
                MAX_FRAME_BYTES, ancillary_size, getattr(socket, "MSG_CMSG_CLOEXEC", 0),
            )
            truncation = getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)
            if not packet:
                self.coordinator.controller_disconnected(connection_identity)
                return bounded_error("controller_denied")
            packet_peer = _packet_pid_uid(ancillary)
            observed = self.identity_observer(connection, peer[0], peer[1])
            if flags & truncation or peer != packet_peer or observed != self.controller \
                    or peer != (self.controller["pid"], self.controller["uid"]):
                return bounded_error("controller_denied")
            message = parse_controller_message(packet)
            result = self.coordinator.handle_controller(
                message, observed_peer=observed,
                connection_identity=connection_identity,
            )
            connection.sendall(encode_controller_message(result))
            return result
        except Exception:
            self.coordinator.controller_disconnected(connection_identity)
            return bounded_error("controller_denied")
        finally:
            if ancillary is not None:
                _close_received_descriptors(ancillary)

    def receive_once(self) -> dict[str, Any]:
        if self.listener is None:
            return bounded_error("controller_denied")
        try:
            connection, _address = self.listener.accept()
            setter = getattr(connection, "settimeout", None)
            if callable(setter): setter(5.0)
            connection_identity = f"controller-connection-{uuid.uuid4().hex}"
            self.connections[connection_identity] = connection
            result = self._process(connection, connection_identity)
            if not result.get("ok", True) and result.get("code") == "controller_denied":
                self.disconnect(connection_identity)
            return result
        except Exception:
            return bounded_error("controller_denied")

    def receive_connection(self, connection_identity: str) -> dict[str, Any]:
        connection = self.connections.get(connection_identity)
        if connection is None:
            return bounded_error("controller_denied")
        result = self._process(connection, connection_identity)
        if result.get("code") == "controller_denied":
            self.disconnect(connection_identity)
        return result

    def disconnect(self, connection_identity: str) -> None:
        connection = self.connections.pop(connection_identity, None)
        self.coordinator.controller_disconnected(connection_identity)
        if connection is not None:
            try: connection.close()
            except OSError: pass

    def close(self) -> dict[str, Any]:
        for identity in tuple(self.connections):
            self.disconnect(identity)
        listener, self.listener = self.listener, None
        if listener is not None:
            try: listener.close()
            except OSError: pass
        return {"ok": True, "code": "controller_closed"}


class _V2OperationRegistry:
    """Private max-16 epoch registry for canonical guest operations."""

    __slots__ = ("_machine_id", "_broker_epoch", "_controller_epoch", "_owner",
                 "_id_factory", "_items", "_tombstones", "_closed", "_lock",
                 "_terminal_code")

    def __init__(self, *, machine_id: str, broker_epoch: str, controller_epoch: str,
                 owner: str, id_factory=None) -> None:
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{6,61}[a-z0-9]", machine_id)
                or not re.fullmatch(r"[0-9a-f]{32}", broker_epoch)
                or not re.fullmatch(r"[0-9a-f]{32}", controller_epoch)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", owner)
                or (id_factory is not None and not callable(id_factory))):
            raise ControllerServiceV2Error("operation_registry_invalid")
        self._machine_id = machine_id
        self._broker_epoch = broker_epoch
        self._controller_epoch = controller_epoch
        self._owner = owner
        self._id_factory = id_factory or (lambda: f"operation-{uuid.uuid4().hex}")
        self._items: dict[str, dict[str, Any]] = {}
        self._tombstones: set[str] = set()
        self._closed = False
        self._lock = threading.Lock()
        self._terminal_code = None

    def submit(self, request: Any, *, connection_identity: str, now_ms: int,
               canonical_guest_validator) -> dict[str, Any]:
        if (not callable(canonical_guest_validator)
                or not _identity(connection_identity) or type(now_ms) is not int):
            raise ControllerServiceV2Error("request_invalid")
        try:
            if canonical_guest_validator(request) is not True:
                raise ControllerServiceV2Error("request_invalid")
            canonical = _canonical_guest_request(request)
            if canonical["machine_id"] != self._machine_id:
                raise ControllerServiceV2Error("request_invalid")
            header_items = tuple(sorted(
                (name, value) for name, value in canonical["headers"].items()
                if name != "content-type"
            ))
            if ("content-type" in canonical["headers"]
                    and canonical["headers"]["content-type"] != canonical["content_type"]):
                raise ControllerServiceV2Error("request_invalid")
            typed = GuestRequestV2(
                machine_id=canonical["machine_id"],
                binding_id=canonical["binding_id"],
                binding_version=canonical["binding_version"],
                scheme=canonical["scheme"], host=canonical["host"],
                port=canonical["port"], method=canonical["method"],
                path=canonical["path"],
                headers=header_items,
                body=canonical["body"], content_type=canonical["content_type"],
                deadline_ms=canonical["deadline_ms"],
                correlation_id=canonical["correlation_id"],
            )
            request_digest = guest_request_digest_v2(typed)
        except ControllerServiceV2Error:
            raise
        except Exception:
            raise ControllerServiceV2Error("request_invalid") from None
        deadline = now_ms + min(typed.deadline_ms, GUEST_V2_MAX_OPERATION_MS)
        with self._lock:
            if (self._closed or len(self._items) >= 16
                    or any(item["connection_identity"] == connection_identity
                           for item in self._items.values())):
                raise ControllerServiceV2Error("capacity_exceeded")
            try:
                operation_id = self._id_factory()
            except Exception:
                raise ControllerServiceV2Error("request_invalid") from None
            if (not isinstance(operation_id, str) or re.fullmatch(
                    r"operation-[a-z0-9]{6,53}", operation_id) is None):
                raise ControllerServiceV2Error("request_invalid")
            if operation_id in self._items or operation_id in self._tombstones:
                raise ControllerServiceV2Error("capacity_exceeded")
            self._items[operation_id] = {
                "operation_id": operation_id, "request_digest": request_digest,
                "request": typed, "connection_identity": connection_identity,
                "request_received_at_unix_ms": now_ms,
                "request_deadline_unix_ms": deadline, "state": "pending",
                "claim_owner": None, "authorization": None, "identity": None,
                "descriptor": None, "descriptor_closer": None,
                "lease": None, "lease_connection": None,
                "terminal_code": None, "result": None,
            }
        return {"ok": True, "code": "credential_pending",
                "correlation_id": typed.correlation_id}

    def submit_typed_v2(self, request: GuestRequestV2, *, connection_identity: str,
                        now_ms: int) -> dict[str, Any]:
        """Admit only the canonical v2 guest type; never reconstruct BrokerRequest."""

        if (type(request) is not GuestRequestV2 or not _identity(connection_identity)
                or type(now_ms) is not int or request.machine_id != self._machine_id):
            raise ControllerServiceV2Error("request_invalid")
        deadline = now_ms + min(request.deadline_ms, GUEST_V2_MAX_OPERATION_MS)
        with self._lock:
            if (self._closed or len(self._items) >= MAX_ACTIVE_REQUESTS
                    or any(item["connection_identity"] == connection_identity
                           for item in self._items.values())):
                raise ControllerServiceV2Error("capacity_exceeded")
            try:
                operation_id = self._id_factory()
            except Exception:
                raise ControllerServiceV2Error("request_invalid") from None
            if (not isinstance(operation_id, str) or re.fullmatch(
                    r"operation-[a-z0-9]{6,53}", operation_id) is None
                    or operation_id in self._items or operation_id in self._tombstones):
                raise ControllerServiceV2Error("capacity_exceeded")
            self._items[operation_id] = {
                "operation_id": operation_id,
                "request_digest": guest_request_digest_v2(request),
                "request": request,
                "connection_identity": connection_identity,
                "request_received_at_unix_ms": now_ms,
                "request_deadline_unix_ms": deadline, "state": "pending",
                "claim_owner": None, "authorization": None, "identity": None,
                "descriptor": None, "descriptor_closer": None,
                "lease": None, "lease_connection": None,
                "terminal_code": None, "result": None,
            }
        return {"ok": True, "code": "credential_pending",
                "correlation_id": request.correlation_id,
                "operation_id": operation_id}

    def claim_next(self, *, owner: str, reply_to: int, sequence: int,
                   now_ms: int) -> dict[str, Any]:
        if owner != self._owner:
            raise ControllerServiceV2Error("claim_owner_mismatch")
        with self._lock:
            for item in self._items.values():
                if item["state"] != "pending":
                    continue
                if item["request_deadline_unix_ms"] <= now_ms:
                    self._terminalize_locked(item, "deadline_exceeded")
                    continue
                item["state"] = "claimed"
                item["claim_owner"] = owner
                request = item["request"]
                header_bytes = sum(len(name.encode("ascii")) + len(value.encode("utf-8")) + 4
                                   for name, value in request.headers)
                return {
                    "protocol": CONTROLLER_PROTOCOL_V2, "type": "CLAIMED_V2",
                    "machine_id": self._machine_id, "broker_epoch": self._broker_epoch,
                    "controller_epoch": self._controller_epoch, "sequence": sequence,
                    "reply_to": reply_to, "claim_state": "claimed",
                    "operation_id": item["operation_id"],
                    "request_digest": item["request_digest"],
                    "binding_id": request.binding_id,
                    "binding_version": request.binding_version,
                    "scheme": request.scheme, "host": request.host,
                    "port": request.port, "method": request.method,
                    "path": request.path, "content_type": request.content_type,
                    "header_bytes": header_bytes, "body_bytes": len(request.body),
                    "request_deadline_unix_ms": item["request_deadline_unix_ms"],
                    "correlation_id": request.correlation_id,
                }
        return {
            "protocol": CONTROLLER_PROTOCOL_V2, "type": "CLAIMED_V2",
            "machine_id": self._machine_id, "broker_epoch": self._broker_epoch,
            "controller_epoch": self._controller_epoch, "sequence": sequence,
            "reply_to": reply_to, "claim_state": "no_pending", "retry_after_ms": 50,
        }

    def authorize(self, message: Mapping[str, Any], *, identity: AuthorizationIdentityV2,
                  now_ms: int) -> None:
        with self._lock:
            item = self._items.get(message["operation_id"])
            if (item is None or item["state"] != "claimed"
                    or item["claim_owner"] != self._owner
                    or item["request_digest"] != message["request_digest"]
                    or item["request"].binding_id != message["binding_id"]
                    or item["request"].binding_version != message["binding_version"]
                    or item["request_deadline_unix_ms"] <= now_ms):
                if item is not None:
                    self._terminalize_locked(item, "binding_mismatch")
                raise ControllerServiceV2Error("authorization_mismatch")
            item["state"] = "authorized"
            item["authorization"] = dict(message)
            item["identity"] = identity

    def authorization_deadlines(self, operation_id: str) -> tuple[int, int]:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "claimed":
                raise ControllerServiceV2Error("authorization_mismatch")
            return item["request_deadline_unix_ms"], item["request"].binding_version

    def claim_temporal_context(self, operation_id: str) -> dict[str, int]:
        with self._lock:
            item = self._items.get(operation_id)
            if (item is None or item["state"] != "claimed"
                    or item["claim_owner"] != self._owner):
                raise ControllerServiceV2Error("claim_owner_mismatch")
            return {
                "original_guest_request_receipt_unix_ms":
                    item["request_received_at_unix_ms"],
            }

    def refuse(self, message: Mapping[str, Any]) -> None:
        with self._lock:
            item = self._items.get(message["operation_id"])
            if (item is None or item["state"] != "claimed"
                    or item["claim_owner"] != self._owner
                    or item["request_digest"] != message["request_digest"]
                    or item["request"].binding_id != message["binding_id"]
                    or item["request"].binding_version != message["binding_version"]):
                raise ControllerServiceV2Error("refusal_mismatch")
            self._terminalize_locked(item, message["reason_code"])

    def known_claim(self, operation_id: Any) -> bool:
        with self._lock:
            item = self._items.get(operation_id)
            return item is not None and item["state"] == "claimed"

    def terminalize_known(self, operation_id: Any, code: str) -> bool:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None:
                return False
            if item["state"] not in {"refused", "completed"}:
                self._terminalize_locked(item, code)
            return True

    def authorization_snapshot(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "authorized":
                raise ControllerServiceV2Error("lease_invalid")
            return {
                "authorization": dict(item["authorization"]),
                "identity": item["identity"],
                "request_deadline_unix_ms": item["request_deadline_unix_ms"],
            }

    def begin_delivery(self, operation_id: str, *, registry: AuthorizationRegistryV2,
                       now_ms: int) -> dict[str, Any]:
        """Consume the exact authorization before any delivery bytes are decoded."""

        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "authorized":
                if item is not None and item["state"] not in {"refused", "completed"}:
                    self._terminalize_locked(item, "lease_invalid")
                raise ControllerServiceV2Error("lease_invalid")
            snapshot = {
                "authorization": dict(item["authorization"]),
                "identity": item["identity"],
                "request_deadline_unix_ms": item["request_deadline_unix_ms"],
            }
            try:
                registry.match_and_consume(item["identity"], now_ms=now_ms)
            except Exception:
                self._terminalize_locked(item, "lease_invalid")
                raise ControllerServiceV2Error("lease_invalid") from None
            item["state"] = "lease_attempted"
            return snapshot

    def bind_delivery_descriptor(self, operation_id: str, frame: Mapping[str, Any], *,
                                 lease_sequences: LeaseSequenceV2, descriptor: int,
                                 descriptor_observer, descriptor_closer,
                                 lease_connection=None) -> None:
        if not callable(descriptor_observer) or not callable(descriptor_closer):
            raise ControllerServiceV2Error("lease_invalid")
        try:
            with self._lock:
                item = self._items.get(operation_id)
                if item is None or item["state"] != "lease_attempted":
                    raise ControllerServiceV2Error("lease_invalid")
                authorization = item["authorization"]
                compared = (
                    "machine_id", "broker_epoch", "controller_epoch", "operation_id",
                    "request_digest", "binding_id", "binding_version", "auth_form",
                    "policy_digest", "egress_digest", "broker_digest", "proof_digest",
                    "effective_isolation_digest", "evidence_id", "decision_id",
                    "authorization_digest", "authorization_expires_at_unix_ms",
                )
                if any(frame[name] != authorization[name] for name in compared):
                    raise ControllerServiceV2Error("lease_invalid")
                lease_sequences.accept(
                    frame["controller_epoch"], frame["broker_epoch"],
                    frame["lease_sequence"],
                )
            observed = descriptor_observer(descriptor)
            if (not isinstance(observed, Mapping)
                    or set(observed) != {"anonymous_memfd", "close_on_exec", "size", "seals"}
                    or observed["anonymous_memfd"] is not True
                    or observed["close_on_exec"] is not True
                    or observed["size"] != frame["descriptor_size"]
                    or frozenset(observed["seals"]) != REQUIRED_SEALS):
                raise ControllerServiceV2Error("lease_invalid")
            with self._lock:
                item = self._items[operation_id]
                if item["state"] != "lease_attempted":
                    raise ControllerServiceV2Error("lease_invalid")
                item["state"] = "lease_bound"
                item["descriptor"] = descriptor
                item["descriptor_closer"] = descriptor_closer
                item["lease"] = dict(frame)
                item["lease_connection"] = lease_connection
        except Exception as exc:
            with self._lock:
                item = self._items.get(operation_id)
                if item is not None:
                    self._terminalize_locked(item, "lease_invalid")
            raise ControllerServiceV2Error(
                "lease_sequence_invalid" if isinstance(exc, ProtocolV2Error)
                else "lease_invalid"
            ) from None

    def _terminalize_locked(self, item: dict[str, Any], code: str) -> bool:
        descriptor = item.get("descriptor")
        closer = item.get("descriptor_closer")
        effect_possible = item.get("state") in {
            "pre_audited", "effect_possible", "post_audited",
        }
        item["descriptor"] = None
        item["descriptor_closer"] = None
        lease_connection = item.get("lease_connection")
        item["lease_connection"] = None
        item["state"] = "indeterminate" if effect_possible else "refused"
        item["terminal_code"] = (
            code if _SAFE_CODE.fullmatch(code or "") else
            ("internal_indeterminate" if effect_possible else "internal_refusal")
        )
        item["result"] = {
            "ok": False, "state": item["state"],
            "code": item["terminal_code"],
            "correlation_id": item["request"].correlation_id,
        }
        self._tombstones.add(item["operation_id"])
        first_failure = None
        if descriptor is not None and callable(closer):
            try:
                closer(descriptor)
            except Exception:
                self._closed = True
                first_failure = "descriptor_cleanup_failed"
        if lease_connection is not None:
            try:
                lease_connection.close()
            except Exception:
                self._closed = True
                first_failure = first_failure or "lease_socket_cleanup_failed"
        if first_failure is not None and self._terminal_code is None:
            self._terminal_code = first_failure
        return first_failure is None

    def _raise_sticky(self) -> None:
        if self._terminal_code is not None:
            raise ControllerServiceV2Error(self._terminal_code)

    def terminalize_all(self, code: str) -> int:
        with self._lock:
            selected = [item for item in self._items.values()
                        if item["state"] not in {"refused", "completed", "indeterminate"}]
            for item in selected:
                self._terminalize_locked(item, code)
            self._closed = True
            count = len(selected)
        self._raise_sticky()
        return count

    def revoke(self, binding_id: str) -> int:
        with self._lock:
            selected = [item for item in self._items.values()
                        if item["request"].binding_id == binding_id
                        and item["state"] not in {"refused", "completed"}]
            for item in selected:
                self._terminalize_locked(item, "revoked")
            count = len(selected)
        self._raise_sticky()
        return count

    def expire(self, now_ms: int) -> int:
        with self._lock:
            selected = [item for item in self._items.values()
                        if item["state"] in {"pending", "claimed", "authorized",
                                             "lease_attempted", "lease_bound",
                                             "pre_audited"}
                        and (item["request_deadline_unix_ms"] <= now_ms
                             or (item["authorization"] is not None and
                                 item["authorization"]["authorization_expires_at_unix_ms"] <= now_ms))]
            for item in selected:
                self._terminalize_locked(item, "deadline_exceeded")
            count = len(selected)
        self._raise_sticky()
        return count

    def state(self, operation_id: str) -> str | None:
        with self._lock:
            item = self._items.get(operation_id)
            return item["state"] if item is not None else None

    def active_count(self) -> int:
        with self._lock:
            return sum(item["state"] not in {"refused", "completed", "indeterminate"}
                       for item in self._items.values())

    def connection_identity(self, operation_id: str) -> str:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None:
                raise ControllerServiceV2Error("request_invalid")
            return item["connection_identity"]

    def quiesce_pre_effect(self, code: str) -> int:
        """Close operations that cannot have an effect; retain possible effects to drain."""

        with self._lock:
            selected = [item for item in self._items.values()
                        if item["state"] in {"pending", "claimed", "authorized",
                                             "lease_attempted", "lease_bound"}]
            for item in selected:
                self._terminalize_locked(item, code)
            remaining = sum(item["state"] in {"pre_audited", "effect_possible", "post_audited"}
                            for item in self._items.values())
            self._closed = True
        self._raise_sticky()
        return remaining

    def effect_context(self, operation_id: str, *, now_ms: int) -> dict[str, Any]:
        """Return the exact bound effect context without reading the descriptor."""

        with self._lock:
            item = self._items.get(operation_id)
            if (item is None or item["state"] != "lease_bound"
                    or item["request_deadline_unix_ms"] <= now_ms
                    or item["authorization"]["authorization_expires_at_unix_ms"] <= now_ms
                    or item["descriptor"] is None):
                if item is not None and item["state"] not in {"refused", "completed"}:
                    self._terminalize_locked(item, "deadline_exceeded")
                raise ControllerServiceV2Error("lease_invalid")
            return {
                "request": item["request"],
                "descriptor": item["descriptor"],
                "authorization": dict(item["authorization"]),
                "lease": dict(item["lease"]),
                "lease_connection": item["lease_connection"],
                "request_deadline_unix_ms": item["request_deadline_unix_ms"],
            }

    def transition_effect(self, operation_id: str, expected: str, target: str) -> None:
        if target not in {"pre_audited", "effect_possible", "post_audited"}:
            raise ControllerServiceV2Error("effect_state_invalid")
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != expected:
                raise ControllerServiceV2Error("effect_state_invalid")
            item["state"] = target

    def close_effect_descriptor(self, operation_id: str) -> None:
        """Close the descriptor while retaining post_audited until ACK delivery."""

        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "post_audited":
                raise ControllerServiceV2Error("effect_state_invalid")
            descriptor = item["descriptor"]
            closer = item["descriptor_closer"]
            item["descriptor"] = None
            item["descriptor_closer"] = None
        try:
            closer(descriptor)
        except Exception:
            with self._lock:
                item["state"] = "indeterminate"
                item["terminal_code"] = "descriptor_cleanup_failed"
            self._closed = True
            if self._terminal_code is None:
                self._terminal_code = "descriptor_cleanup_failed"
            raise ControllerServiceV2Error("descriptor_cleanup_failed") from None

    def finalize_effect(self, operation_id: str, *, outcome_class: str,
                        reason_code: str) -> None:
        target = {"completed": "completed", "refused": "refused",
                  "indeterminate": "indeterminate"}.get(outcome_class)
        if target is None or not isinstance(reason_code, str) \
                or _SAFE_CODE.fullmatch(reason_code) is None:
            raise ControllerServiceV2Error("effect_state_invalid")
        with self._lock:
            item = self._items.get(operation_id)
            if item is None or item["state"] != "post_audited":
                raise ControllerServiceV2Error("effect_state_invalid")
            if item["descriptor"] is not None or item["descriptor_closer"] is not None:
                raise ControllerServiceV2Error("effect_state_invalid")
            lease_connection = item.get("lease_connection")
            item["lease_connection"] = None
            item["state"] = target
            item["terminal_code"] = reason_code
            item["result"] = {
                "ok": target == "completed", "state": target,
                "code": item["terminal_code"],
                "correlation_id": item["request"].correlation_id,
            }
            self._tombstones.add(operation_id)
        try:
            lease_connection.close()
        except Exception:
            self._closed = True
            if self._terminal_code is None:
                self._terminal_code = "lease_socket_cleanup_failed"
            raise ControllerServiceV2Error("lease_socket_cleanup_failed") from None

    def guest_result(self, connection_identity: str, *, consume: bool = False) -> dict[str, Any]:
        """Return one bounded v2 result without controller or lease identities."""

        if not _identity(connection_identity) or type(consume) is not bool:
            raise ControllerServiceV2Error("request_invalid")
        with self._lock:
            selected = [item for item in self._items.values()
                        if item["connection_identity"] == connection_identity]
            if len(selected) != 1:
                raise ControllerServiceV2Error("request_invalid")
            item = selected[0]
            result = item["result"]
            if result is None:
                result = {"ok": True, "state": "credential_pending",
                          "code": "credential_pending",
                          "correlation_id": item["request"].correlation_id}
            public = dict(result)
            if consume and item["state"] in {"refused", "completed", "indeterminate"}:
                self._items.pop(item["operation_id"], None)
            return public

    def mark_indeterminate(self, operation_id: str, code: str) -> None:
        with self._lock:
            item = self._items.get(operation_id)
            if item is None:
                return
            descriptor, closer = item.get("descriptor"), item.get("descriptor_closer")
            item["descriptor"] = None
            item["descriptor_closer"] = None
            lease_connection = item.get("lease_connection")
            item["lease_connection"] = None
            item["state"] = "indeterminate"
            item["terminal_code"] = code if _SAFE_CODE.fullmatch(code or "") else "internal_indeterminate"
            item["result"] = {
                "ok": False, "state": "indeterminate",
                "code": item["terminal_code"],
                "correlation_id": item["request"].correlation_id,
            }
            self._tombstones.add(operation_id)
        if descriptor is not None and callable(closer):
            try:
                closer(descriptor)
            except Exception:
                if self._terminal_code is None:
                    self._terminal_code = "descriptor_cleanup_failed"
        if lease_connection is not None:
            try:
                lease_connection.close()
            except Exception:
                if self._terminal_code is None:
                    self._terminal_code = "lease_socket_cleanup_failed"
        self._closed = True

    @property
    def terminal_code(self) -> str | None:
        return self._terminal_code


class PinnedHTTPSCredentialEffectV2(EffectExecutionV2):
    """One no-retry HTTPS effect over an already authorized DNS/IP decision."""

    def __init__(self, upstream: VerifiedHttpsUpstream, *, destination_authority,
                 descriptor_reader=os.pread, wall_clock_ms=None) -> None:
        if (not isinstance(upstream, VerifiedHttpsUpstream) or not callable(descriptor_reader)
                or type(destination_authority) is not ExactNftDestinationSetAuthorityV2
                or (wall_clock_ms is not None and not callable(wall_clock_ms))):
            raise ControllerServiceV2Error("effect_executor_invalid")
        super().__init__()
        self._upstream = upstream
        self._descriptor_reader = descriptor_reader
        self._destination_authority = destination_authority
        self._wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))

    def execute_authorized(self, context: AuthorizedEffectContextV2,
                           descriptor: int) -> EffectExecutionResultV2:
        material = bytearray()
        transport = None
        headers = {}
        reflected = bytearray()
        raw = b""
        credential = None
        result = None
        response_headers = None
        try:
            raw = self._descriptor_reader(descriptor, context.descriptor_size, 0)
            if type(raw) is not bytes or len(raw) != context.descriptor_size:
                raise GuestProtocolV2Error("effect_indeterminate")
            material.extend(raw)
            raw = b""
            try:
                credential = material.decode("ascii")
            except UnicodeDecodeError:
                raise GuestProtocolV2Error("effect_indeterminate") from None
            if (not credential or any(ord(character) < 33 or ord(character) > 126
                                      for character in credential)):
                raise GuestProtocolV2Error("effect_indeterminate")
            request = context.request
            current = self._wall_clock_ms()
            deadline = min(
                context.request_deadline_unix_ms,
                context.binding_expires_at_unix_ms,
                context.authorization_expires_at_unix_ms,
                context.lease_expires_at_unix_ms,
                context.activation_expires_at_unix_ms,
            )
            if type(current) is not int or current >= deadline:
                return EffectExecutionResultV2(
                    GuestResultV2.failure(
                        state="indeterminate", code="deadline_exceeded",
                        retryable=False, correlation_id=request.correlation_id),
                    "effect_entered", "indeterminate", "possible",
                    "deadline_exceeded")
            remaining_seconds = (deadline - current) / 1000.0
            headers = dict(request.headers)
            headers["host"] = context.egress_decision.hostname
            headers["content-length"] = str(len(request.body))
            if request.content_type is not None:
                headers["content-type"] = request.content_type
            if context.auth_form == "authorization_bearer":
                headers["authorization"] = "Bearer " + credential
            else:
                headers["x-api-key"] = credential
            try:
                header_bytes = sum(
                    len(name.encode("ascii")) + len(value.encode("latin-1")) + 4
                    for name, value in headers.items())
            except Exception:
                raise GuestProtocolV2Error("effect_indeterminate") from None
            if header_bytes > 64 * 1024:
                raise GuestProtocolV2Error("effect_indeterminate")
            timeout = min(
                self._upstream.connect_seconds, self._upstream.idle_seconds,
                request.deadline_ms / 1000.0, remaining_seconds,
            )
            address = self._destination_authority.consume(context.egress_decision)
            transport = self._upstream.connector(
                address, context.egress_decision.sni_hostname,
                context.egress_decision.port, timeout, self._upstream.ssl_context,
            )
            after_connect = self._wall_clock_ms()
            if type(after_connect) is not int or after_connect < current or after_connect >= deadline:
                raise GuestProtocolV2Error("effect_indeterminate")
            remaining_seconds = (deadline - after_connect) / 1000.0
            result = transport.request(
                request.method, request.path, headers, request.body,
                min(self._upstream.idle_seconds, request.deadline_ms / 1000.0,
                    remaining_seconds),
            )
            if not isinstance(result, Mapping):
                raise GuestProtocolV2Error("effect_indeterminate")
            status, body = result.get("status"), result.get("body", b"")
            response_headers = result.get("headers", {})
            if (type(status) is not int or type(body) is not bytes
                    or not isinstance(response_headers, Mapping)
                    or len(body) > MAX_GUEST_RESULT_BODY_BYTES):
                raise GuestProtocolV2Error("effect_indeterminate")
            if not 200 <= status <= 299:
                guest = GuestResultV2.failure(
                    state="indeterminate", code="upstream_refused", retryable=False,
                    correlation_id=request.correlation_id,
                )
                return EffectExecutionResultV2(
                    guest, "effect_entered", "indeterminate", "completed",
                    "upstream_refused")
            # The credential boundary never reflects upstream-controlled fields.
            # Status is the only successful upstream datum delivered to the guest;
            # all body/header/ETag fields are consumed and wiped below.
            reflected.extend(body)
            guest = GuestResultV2.success(
                status, (), b"", request.correlation_id)
            return EffectExecutionResultV2(
                guest, "effect_entered", "completed", "completed",
                "upstream_completed")
        except GuestProtocolV2Error:
            raise
        except Exception:
            raise GuestProtocolV2Error("effect_indeterminate") from None
        finally:
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            for index in range(len(material)):
                material[index] = 0
            for index in range(len(reflected)):
                reflected[index] = 0
            headers.clear()
            raw = b""
            credential = None
            transport = None
            response_headers = None
            result = None


class ExactNftDestinationSetReceiptV2:
    __slots__ = ("projection_digest", "addresses", "selected_address", "_sealed")

    def __init__(self, projection_digest: str, addresses: tuple[str, ...],
                 selected_address: str) -> None:
        object.__setattr__(self, "projection_digest", projection_digest)
        object.__setattr__(self, "addresses", addresses)
        object.__setattr__(self, "selected_address", selected_address)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name, _value):
        if getattr(self, "_sealed", False):
            raise AttributeError("immutable receipt")
        object.__setattr__(self, _name, _value)


class ExactNftDestinationSetAuthorityV2:
    """Consume one kernel-observed exact nft set and return its pinned peer."""

    __slots__ = ("_observer",)

    def __init__(self, observer) -> None:
        if not callable(observer):
            raise ControllerServiceV2Error("egress_authority_invalid")
        self._observer = observer

    def consume(self, decision: AuthorizedEgressDecisionV2) -> str:
        if type(decision) is not AuthorizedEgressDecisionV2:
            raise ControllerServiceV2Error("egress_authority_invalid")
        try:
            receipt = self._observer(decision)
        except Exception:
            raise ControllerServiceV2Error("egress_authority_unavailable") from None
        if (type(receipt) is not ExactNftDestinationSetReceiptV2
                or receipt.projection_digest != decision.projection_digest
                or receipt.addresses != decision.nft_destination_set
                or receipt.selected_address not in receipt.addresses):
            raise ControllerServiceV2Error("egress_authority_denied")
        return receipt.selected_address


_ACCEPTED_LEASE_SOCKET_ISSUER = object()


class _V2AcceptedLeaseSocketReceipt:
    __slots__ = ("parent", "operation_id", "raw", "peer", "_used", "_issuer")

    def __init__(self, issuer, parent, operation_id, raw, peer) -> None:
        if issuer is not _ACCEPTED_LEASE_SOCKET_ISSUER:
            raise ControllerServiceV2Error("lease_invalid")
        self.parent = parent
        self.operation_id = operation_id
        self.raw = raw
        self.peer = peer
        self._used = False
        self._issuer = issuer

    def consume(self, endpoint):
        if (self._issuer is not _ACCEPTED_LEASE_SOCKET_ISSUER or self._used
                or self.parent is not endpoint._connection
                or self.operation_id != endpoint.operation_id):
            raise ControllerServiceV2Error("lease_invalid")
        self._used = True
        return self.raw, self.peer


class _V2AuthenticatedLeaseConnection:
    """Concrete endpoint-issued, exact-session lease connection."""

    __slots__ = ("_raw", "machine_id", "broker_epoch", "controller_epoch",
                 "owner", "peer", "_closed", "_acked")

    def __init__(self, endpoint, raw, peer) -> None:
        parent = endpoint._connection
        if (not isinstance(endpoint, _V2LeaseDeliveryEndpoint)
                or endpoint._connection is not parent or not callable(getattr(raw, "sendall", None))
                or not callable(getattr(raw, "close", None))
                or peer != parent.config.controller):
            raise ControllerServiceV2Error("lease_invalid")
        self._raw = raw
        self.machine_id = parent.config.machine_id
        self.broker_epoch = parent.broker_epoch
        self.controller_epoch = parent.controller_epoch
        self.owner = parent.owner
        self.peer = peer
        self._closed = False
        self._acked = False

    def send_ack(self, packet: bytes) -> None:
        if self._closed or self._acked or type(packet) is not bytes or len(packet) != 444:
            raise ControllerServiceV2Error("lease_ack_failed")
        self._raw.sendall(packet)
        self._acked = True

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()


class _V2LeaseDeliveryEndpoint:
    """One exact authorization's one-attempt delivery endpoint."""

    __slots__ = ("_connection", "operation_id", "address", "listener",
                 "_attempted", "_lock", "_closed")

    def __init__(self, connection, operation_id: str) -> None:
        self._connection = connection
        self.operation_id = operation_id
        self.address = None
        self.listener = None
        self._attempted = False
        self._lock = threading.Lock()
        self._closed = False

    def arm(self, address: bytes, factory) -> None:
        if (self._closed or self.listener is not None or type(address) is not bytes
                or len(address) != 93 or not callable(factory)):
            raise ControllerServiceV2Error("lease_endpoint_invalid")
        self.address = address
        try:
            listener = factory(address, self)
        except ControllerServiceV2Error:
            self.address = None
            raise
        except OSError as exc:
            self.address = None
            if getattr(exc, "errno", None) == getattr(__import__("errno"), "EADDRINUSE"):
                raise ControllerServiceV2Error("lease_endpoint_collision") from None
            raise ControllerServiceV2Error("lease_endpoint_unavailable") from None
        except Exception:
            self.address = None
            raise ControllerServiceV2Error("lease_endpoint_unavailable") from None
        if (listener is None or not callable(getattr(listener, "close", None))):
            self.address = None
            raise ControllerServiceV2Error("lease_endpoint_unavailable")
        self.listener = listener

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            listener, self.listener = self.listener, None
            if listener is not None:
                listener.close()

    def accept(self, packet: bytes, descriptors: Any, *, descriptor_observer,
               descriptor_closer, now_ms: int, accepted_socket_receipt) -> dict[str, Any]:
        if not isinstance(accepted_socket_receipt, _V2AcceptedLeaseSocketReceipt):
            raise ControllerServiceV2Error("lease_invalid")
        accepted_connection, observed_controller = accepted_socket_receipt.consume(self)
        with self._lock:
            first = not self._attempted
            self._attempted = True
        lease_connection = None
        if observed_controller == self._connection.config.controller:
            lease_connection = _V2AuthenticatedLeaseConnection(
                self, accepted_connection, observed_controller)
        try:
            return self._connection._accept_bound_lease_v2(
                self.operation_id, packet, descriptors,
                observed_controller=observed_controller,
                descriptor_observer=descriptor_observer,
                descriptor_closer=descriptor_closer, now_ms=now_ms,
                lease_connection=lease_connection,
                first_attempt=first,
            )
        finally:
            if lease_connection is None:
                try:
                    accepted_connection.close()
                except Exception:
                    pass


class LinuxLeaseOperationV2Listener:
    """One armed per-authorization abstract listener; no address fallback."""

    __slots__ = ("address", "endpoint", "listener", "_observer", "_now_ms",
                 "_descriptor_observer", "_descriptor_closer", "_so_peercred",
                 "_so_passcred", "_scm_credentials", "_scm_rights", "_closed",
                 "_accepted", "_terminal_code", "_accepted_connection",
                 "_ownership_lock", "_listener_closed")

    def __init__(self, address: bytes, endpoint: _V2LeaseDeliveryEndpoint, *,
                 observer, now_ms, descriptor_observer, descriptor_closer,
                 socket_factory=socket.socket, so_peercred=None, so_passcred=None,
                 scm_credentials=None, scm_rights=None) -> None:
        so_peercred = getattr(socket, "SO_PEERCRED", None) if so_peercred is None else so_peercred
        so_passcred = getattr(socket, "SO_PASSCRED", None) if so_passcred is None else so_passcred
        scm_credentials = getattr(socket, "SCM_CREDENTIALS", None) \
            if scm_credentials is None else scm_credentials
        scm_rights = socket.SCM_RIGHTS if scm_rights is None else scm_rights
        if (type(address) is not bytes or len(address) != 93 or address[:1] != b"\0"
                or not isinstance(endpoint, _V2LeaseDeliveryEndpoint)
                or endpoint.address != address
                or not callable(observer) or not callable(now_ms)
                or not callable(descriptor_observer) or not callable(descriptor_closer)
                or not callable(socket_factory)
                or any(type(value) is not int or value < 1 for value in (
                    so_peercred, so_passcred, scm_credentials, scm_rights))):
            raise ControllerServiceV2Error("lease_endpoint_invalid")
        self.address = address
        self.endpoint = endpoint
        self._observer = observer
        self._now_ms = now_ms
        self._descriptor_observer = descriptor_observer
        self._descriptor_closer = descriptor_closer
        self._so_peercred = so_peercred
        self._so_passcred = so_passcred
        self._scm_credentials = scm_credentials
        self._scm_rights = scm_rights
        self._closed = False
        self._accepted = False
        self._terminal_code = None
        self._accepted_connection = None
        self._ownership_lock = threading.Lock()
        self._listener_closed = False
        listener = None
        try:
            listener = socket_factory(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            listener.setsockopt(socket.SOL_SOCKET, so_passcred, 1)
            listener.bind(address)
            listener.listen(1)
            setter = getattr(listener, "settimeout", None)
            if not callable(setter):
                raise ControllerServiceV2Error("lease_endpoint_unavailable")
            setter(1.0)
        except Exception as exc:
            code = ("lease_endpoint_collision"
                    if isinstance(exc, OSError)
                    and getattr(exc, "errno", None) == getattr(__import__("errno"), "EADDRINUSE")
                    else "lease_endpoint_unavailable")
            cleanup_failed = False
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    cleanup_failed = True
            raise ControllerServiceV2Error(
                "lease_endpoint_cleanup_failed" if cleanup_failed else code
            ) from None
        self.listener = listener

    def _track_accepted(self, connection) -> bool:
        with self._ownership_lock:
            if self._closed or self._accepted_connection is not None:
                return False
            self._accepted_connection = connection
            return True

    def _release_accepted(self, connection):
        with self._ownership_lock:
            if self._accepted_connection is connection:
                self._accepted_connection = None
                return connection
            return None

    def _take_listener(self):
        with self._ownership_lock:
            if self._listener_closed:
                return None
            self._listener_closed = True
            return self.listener

    def receive_once(self) -> dict[str, Any]:
        if self._closed or self._accepted:
            raise ControllerServiceV2Error(
                self._terminal_code or "lease_endpoint_consumed")
        connection = None
        descriptors = []
        transferred = False
        result = None
        failure = None
        cleanup_failure = None
        self._accepted = True
        try:
            connection, _address = self.listener.accept()
            if not self._track_accepted(connection):
                try:
                    connection.close()
                except Exception:
                    cleanup_failure = "lease_socket_cleanup_failed"
                raise ControllerServiceV2Error("lease_endpoint_consumed")
            setter = getattr(connection, "settimeout", None)
            if not callable(setter):
                raise ControllerServiceV2Error("lease_endpoint_unavailable")
            setter(1.0)
            peer = observe_socket_peer_credentials_v2(
                connection, so_peercred=self._so_peercred)
            observed = self._observer(*peer)
            if observed != self.endpoint._connection.config.controller:
                raise ControllerServiceV2Error("lease_ancillary_invalid")
            ancillary_size = (socket.CMSG_SPACE(_PEER_CREDENTIALS.size)
                              + socket.CMSG_SPACE(array("i", [0]).itemsize * 16))
            packet, ancillary, flags, _source = connection.recvmsg(
                732, ancillary_size, getattr(socket, "MSG_CMSG_CLOEXEC", 0))
            credentials = []
            malformed = False
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == self._scm_rights:
                    values = array("i")
                    aligned = len(payload) - (len(payload) % values.itemsize)
                    if aligned:
                        values.frombytes(payload[:aligned])
                    descriptors.extend(values.tolist())
                    malformed = malformed or aligned != len(payload)
                elif level == socket.SOL_SOCKET and kind == self._scm_credentials:
                    if len(payload) != _PEER_CREDENTIALS.size:
                        malformed = True
                    else:
                        credentials.append(_PEER_CREDENTIALS.unpack(payload))
                else:
                    malformed = True
            truncation = getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)
            if (malformed or flags & truncation or len(packet) != 732 or len(credentials) != 1
                    or credentials[0] != peer
                    or len(descriptors) != 1):
                raise ControllerServiceV2Error("lease_ancillary_invalid")
            receipt = self.endpoint._connection._authenticated_lease_socket_receipt_v2(
                self.endpoint.operation_id, connection, observer=self._observer,
                so_peercred=self._so_peercred)
            self._release_accepted(connection)
            transferred = True
            result = self.endpoint.accept(
                packet, descriptors,
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=self._descriptor_closer,
                now_ms=self._now_ms(), accepted_socket_receipt=receipt)
            self.endpoint._connection._finish_lease_endpoint_attempt_v2(
                self.endpoint.operation_id, self, failed=False)
        except Exception as exc:
            failure = (
                exc.code if isinstance(exc, ControllerServiceV2Error)
                else "lease_endpoint_unavailable")
            try:
                self.endpoint._connection._finish_lease_endpoint_attempt_v2(
                    self.endpoint.operation_id, self, failed=True)
            except Exception as cleanup_exc:
                cleanup_failure = (
                    cleanup_exc.code if isinstance(cleanup_exc, ControllerServiceV2Error)
                    else "operation_cleanup_failed")
        finally:
            if not transferred:
                for descriptor in descriptors:
                    try:
                        self._descriptor_closer(descriptor)
                    except Exception:
                        cleanup_failure = cleanup_failure or "descriptor_cleanup_failed"
                owned_connection = (self._release_accepted(connection)
                                    if connection is not None else None)
                if owned_connection is not None:
                    try:
                        owned_connection.close()
                    except Exception:
                        cleanup_failure = cleanup_failure or "lease_socket_cleanup_failed"
            listener = self._take_listener()
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    cleanup_failure = cleanup_failure or "lease_endpoint_cleanup_failed"
            with self._ownership_lock:
                self._closed = True
        self._terminal_code = cleanup_failure or failure
        if cleanup_failure is not None:
            self.endpoint._connection._record_lease_endpoint_cleanup_failure_v2(
                cleanup_failure)
        if self._terminal_code is not None:
            raise ControllerServiceV2Error(self._terminal_code) from None
        return result

    def close(self) -> None:
        with self._ownership_lock:
            already_closed = self._closed
            self._closed = True
            accepted, self._accepted_connection = self._accepted_connection, None
        if already_closed and accepted is None:
            if self._terminal_code in {
                    "descriptor_cleanup_failed", "lease_socket_cleanup_failed",
                    "lease_endpoint_cleanup_failed"}:
                raise ControllerServiceV2Error(self._terminal_code)
            return
        failure = None
        if accepted is not None:
            try:
                accepted.close()
            except Exception:
                failure = "lease_socket_cleanup_failed"
        listener = self._take_listener()
        if listener is not None:
            try:
                listener.close()
            except Exception:
                failure = failure or "lease_endpoint_cleanup_failed"
        if failure is not None:
            self._terminal_code = failure
            raise ControllerServiceV2Error(self._terminal_code) from None

class BrokerControllerV2Connection:
    protocol = CONTROLLER_PROTOCOL_V2
    """One v2 controller connection with one permanently pinned registry.

    This class owns only transport authentication, handshake, sequences, and
    connection terminalization.  T041 owns authorization state transitions.
    The controller process is trusted; Python reflection/monkeypatching inside
    it is process compromise.  The hostile boundary enforced here is the
    authenticated cross-process socket and kernel-observed identity.
    """

    __slots__ = (
        "connection", "config", "broker_epoch", "owner", "sequences",
        "registry", "controller_epoch", "authenticated", "admission_open", "_closed",
        "_registry_factory", "_on_terminal", "_observation",
        "_registry_disconnected", "_terminal_code", "operations",
        "_next_outgoing", "_lease_sequences", "_activation_expires_at",
        "_authority_lock", "_lease_endpoints", "_quiesced", "_claim_anchor",
        "_audit_lock",
        "_audit_condition", "_audit_acks",
        "_composition_nonce", "_guest_submit_capability",
        "_guest_submit_validator", "_guest_submit_clock",
        "_lease_endpoint_factory",
    )

    def __init__(self, connection: Any, config: ControllerServiceConfigV2,
                 broker_epoch: str, owner: str, *, registry_factory=AuthorizationRegistryV2,
                 on_terminal=None, lease_endpoint_factory=None) -> None:
        try:
            connection_valid = all(callable(getattr(connection, name, None)) for name in (
                "getsockopt", "recvmsg", "sendall", "close",
            ))
        except Exception:
            connection_valid = False
        if (not connection_valid
                or type(config) is not ControllerServiceConfigV2
                or not isinstance(broker_epoch, str)
                or re.fullmatch(r"[0-9a-f]{32}", broker_epoch) is None
                or not isinstance(owner, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", owner) is None
                or not callable(registry_factory)
                or (on_terminal is not None and not callable(on_terminal))
                or (lease_endpoint_factory is not None
                    and not callable(lease_endpoint_factory))):
            raise ControllerServiceV2Error("broker_connection_invalid")
        self.connection = connection
        self.config = config
        self.broker_epoch = broker_epoch
        self.owner = owner
        self.sequences = DirectionalSequenceV2()
        self.registry = None
        self.controller_epoch = None
        self.authenticated = False
        self.admission_open = False
        self._closed = False
        self._registry_factory = registry_factory
        self._on_terminal = on_terminal or (lambda _reason: None)
        self._observation = TemporalObservationV2()
        self._registry_disconnected = False
        self._terminal_code = "broker_controller_closed"
        self.operations = None
        self._next_outgoing = 2
        self._lease_sequences = None
        self._activation_expires_at = None
        self._authority_lock = threading.RLock()
        self._lease_endpoints = {}
        self._quiesced = False
        self._claim_anchor = None
        self._audit_lock = threading.Lock()
        self._audit_condition = threading.Condition()
        self._audit_acks = {}
        self._composition_nonce = object()
        self._guest_submit_capability = None
        self._guest_submit_validator = None
        self._guest_submit_clock = None
        self._lease_endpoint_factory = lease_endpoint_factory

    def mint_guest_bridge_receipt_v2(self):
        """Mint one exact-session, one-use guest bridge capability."""

        with self._authority_lock:
            self._terminal_guard_v2()
            if not self.authenticated or self.controller_epoch is None:
                raise ControllerServiceV2Error("composition_refused")
            return _mint_authenticated_broker_composition_receipt_v2(
                self, purpose="guest_bridge", nonce=self._composition_nonce,
            )

    def bind_guest_submit_capability_v2(self, receipt, *,
                                        canonical_guest_validator, now_ms):
        """Consume one composition receipt and freeze the guest-only policy."""

        with self._authority_lock:
            self._terminal_guard_v2()
            if (self._guest_submit_capability is not None
                    or not callable(canonical_guest_validator)
                    or not callable(now_ms)):
                raise ControllerServiceV2Error("composition_refused")
            receipt.consume_for_guest_bridge(self)
            self._guest_submit_validator = canonical_guest_validator
            self._guest_submit_clock = now_ms

            def invoke(request, *, connection_identity):
                return self._submit_bound_guest_v2(
                    capability, request,
                    connection_identity=connection_identity,
                )

            capability = _mint_bound_guest_submit_capability_v2(invoke)
            self._guest_submit_capability = capability
            return capability

    def _hello(self) -> dict[str, Any]:
        return {
            "protocol": CONTROLLER_PROTOCOL_V2, "type": "HELLO_V2",
            "machine_id": self.config.machine_id,
            "broker_epoch": self.broker_epoch, "sequence": 1,
            **self.config.broker.hello_fields("broker"),
            **self.config.configured_digests(),
        }

    def handshake(self, *, observer, now_ms: int, monotonic, so_peercred: int,
                  scm_credentials: int, scm_rights: int, closer) -> dict[str, Any]:
        try:
            if self._closed or self.authenticated or self.registry is not None:
                raise ControllerServiceV2Error("handshake_replayed")
            started = monotonic()
            setter = getattr(self.connection, "settimeout", None)
            if callable(setter):
                setter(1.0)
            hello = self._hello()
            self.sequences.accept("broker_to_controller", 1)
            self.connection.sendall(encode_controller_frame_v2(
                hello, direction="broker_to_controller", now_ms=now_ms,
                observation=self._observation,
            ))
            packet, observed = receive_authenticated_packet_v2(
                self.connection, expected=self.config.controller, observer=observer,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
            if monotonic() - started > 1.0:
                raise ControllerServiceV2Error("handshake_timeout")
            ack = decode_controller_frame_v2(
                packet, direction="controller_to_broker", now_ms=now_ms,
                observation=self._observation,
            )
            if ack["type"] != "HELLO_ACK_V2":
                raise ControllerServiceV2Error("handshake_required")
            self.sequences.accept("controller_to_broker", ack["sequence"])
            expected_fields = {
                "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch,
                **self.config.controller.hello_fields("controller"),
            }
            if observed != self.config.controller \
                    or any(ack.get(key) != value for key, value in expected_fields.items()):
                raise ControllerServiceV2Error("controller_hello_mismatch")
            digest_values = {
                "protocol": CONTROLLER_PROTOCOL_V2,
                "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch,
                "controller_epoch": ack["controller_epoch"],
                **self.config.broker.hello_fields("broker"),
                **self.config.controller.hello_fields("controller"),
                **self.config.configured_digests(),
            }
            expected_digest = digest_document_v2("handshake_digest", digest_values)
            if ack["handshake_digest"] != expected_digest:
                raise ControllerServiceV2Error("handshake_digest_mismatch")
            # Exactly one construction site exists.  There is no reset,
            # replacement, reconnect, or capacity-recovery construction path.
            registry = self._registry_factory(
                machine_id=self.config.machine_id,
                broker_epoch=self.broker_epoch,
                controller_epoch=ack["controller_epoch"], owner=self.owner,
            )
            if not isinstance(registry, AuthorizationRegistryV2):
                raise ControllerServiceV2Error("authorization_registry_invalid")
            self.registry = registry
            self.controller_epoch = ack["controller_epoch"]
            self.operations = _V2OperationRegistry(
                machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                controller_epoch=self.controller_epoch, owner=self.owner,
            )
            self._lease_sequences = LeaseSequenceV2(self.controller_epoch, self.broker_epoch)
            self.authenticated = True
            return {"ok": True, "code": "broker_controller_authenticated",
                    "admission_open": False,
                    "controller_epoch": ack["controller_epoch"]}
        except ControllerServiceV2Error as exc:
            closed = self.close("handshake_refused")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else exc.code
            ) from None
        except Exception:
            closed = self.close("handshake_refused")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else "handshake_refused"
            ) from None

    def receive_frame(self, *, observer, now_ms: int, so_peercred: int,
                      scm_credentials: int, scm_rights: int, closer,
                      temporal_context=None) -> dict[str, Any]:
        if self._closed or not self.authenticated or self.registry is None:
            raise ControllerServiceV2Error("broker_connection_closed")
        try:
            packet, _observed = receive_authenticated_packet_v2(
                self.connection, expected=self.config.controller, observer=observer,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
            value = decode_controller_frame_v2(
                packet, direction="controller_to_broker", now_ms=now_ms,
                temporal_context=temporal_context,
                observation=self._observation,
            )
            if value["type"] == "HELLO_ACK_V2" \
                    or value["machine_id"] != self.config.machine_id \
                    or value["broker_epoch"] != self.broker_epoch \
                    or value["controller_epoch"] != self.controller_epoch:
                raise ControllerServiceV2Error("controller_frame_identity_mismatch")
            self.sequences.accept("controller_to_broker", value["sequence"])
            return value
        except ControllerServiceV2Error as exc:
            closed = self.close("controller_frame_refused")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else exc.code
            ) from None
        except Exception:
            closed = self.close("controller_frame_refused")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else "controller_frame_refused"
            ) from None

    def _send(self, value: Mapping[str, Any], *, now_ms: int,
              temporal_context=None) -> dict[str, Any]:
        try:
            message = dict(value)
            message["sequence"] = self._next_outgoing
            frame = encode_controller_frame_v2(
                message, direction="broker_to_controller", now_ms=now_ms,
                temporal_context=temporal_context, observation=self._observation,
            )
            self.sequences.accept("broker_to_controller", self._next_outgoing)
            self._next_outgoing += 1
            self.connection.sendall(frame)
            return message
        except Exception:
            closed = self.close("broker_send_failed")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else "broker_send_failed"
            ) from None

    def route_audit_ack_v2(self, value: Mapping[str, Any]) -> None:
        """Route one authenticated ACK from the sole controller reader."""

        if (not isinstance(value, Mapping) or value.get("type") != "AUDIT_ACK_V2"
                or type(value.get("reply_to")) is not int):
            raise ControllerServiceV2Error("audit_ack_invalid")
        with self._audit_condition:
            reply_to = value["reply_to"]
            if reply_to in self._audit_acks:
                raise ControllerServiceV2Error("audit_ack_invalid")
            self._audit_acks[reply_to] = dict(value)
            self._audit_condition.notify_all()

    def wait_audit_ack_v2(self, reply_to: int, timeout_seconds: float) -> dict[str, Any] | None:
        if (type(reply_to) is not int or isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not 0 <= timeout_seconds <= 1.0):
            raise ControllerServiceV2Error("audit_ack_invalid")
        deadline = time.monotonic() + timeout_seconds
        with self._audit_condition:
            while reply_to not in self._audit_acks and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining < 0:
                    return None
                self._audit_condition.wait(remaining)
            return self._audit_acks.pop(reply_to, None)

    def _submit_bound_guest_v2(self, capability, request: Any, *,
                               connection_identity: str) -> dict[str, Any]:
        with self._authority_lock:
            self._terminal_guard_v2()
            if (type(capability) is not BoundGuestSubmitCapabilityV2
                    or capability is not self._guest_submit_capability):
                raise ControllerServiceV2Error("composition_refused")
            try:
                now_ms = self._guest_submit_clock()
            except Exception:
                raise ControllerServiceV2Error("request_invalid") from None
            if type(now_ms) is not int:
                raise ControllerServiceV2Error("request_invalid")
            self._require_admission_v2(now_ms)
            return self.operations.submit(
                request, connection_identity=connection_identity, now_ms=now_ms,
                canonical_guest_validator=self._guest_submit_validator,
            )

    def guest_result_v2(self, connection_identity: str, *, consume: bool = False) -> dict[str, Any]:
        """Project the exact terminal guest result, including after quiesce."""

        if self.operations is None:
            raise ControllerServiceV2Error("request_invalid")
        return self.operations.guest_result(connection_identity, consume=consume)

    def set_admission_v2(self, *, admission_open: bool,
                         activation_expires_at_unix_ms: int | None,
                         now_ms: int) -> dict[str, Any]:
        """Test-only T041 seam; production-style state uses lifecycle v2 frames."""

        with self._authority_lock:
            self._terminal_guard_v2()
            if (not self.authenticated or self.operations is None or self.registry is None
                    or type(admission_open) is not bool or type(now_ms) is not int):
                raise ControllerServiceV2Error("admission_closed")
            if admission_open:
                if (self.config.evidence_id is None
                        or type(activation_expires_at_unix_ms) is not int
                        or not now_ms < activation_expires_at_unix_ms <= now_ms + 30000):
                    self.admission_open = False
                    self._activation_expires_at = None
                    raise ControllerServiceV2Error("admission_closed")
                self.admission_open = True
                self._activation_expires_at = activation_expires_at_unix_ms
                return {"ok": True, "code": "admission_open"}
            self.admission_open = False
            self._activation_expires_at = None
            self._quiesced = True
            self._terminalize_pre_effect_v2("revoked")
            if self._terminal_code != "broker_controller_closed":
                raise ControllerServiceV2Error(self._terminal_code)
            return {"ok": True, "code": "admission_closed"}

    def handle_lifecycle_v2(self, message: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        """Apply an exact ACTIVATE/QUIESCE frame with closed-first ordering."""

        with self._authority_lock:
            if (self._closed or not self.authenticated or self.operations is None
                    or self.registry is None or not isinstance(message, Mapping)
                    or type(now_ms) is not int):
                raise ControllerServiceV2Error("admission_closed")
            kind = message.get("type")
            base = {
                "protocol": CONTROLLER_PROTOCOL_V2, "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch, "controller_epoch": self.controller_epoch,
            }
            identifiable = (kind in {"ACTIVATE_V2", "QUIESCE_V2"}
                            and type(message.get("sequence")) is int
                            and isinstance(message.get(
                                "activation_digest" if kind == "ACTIVATE_V2"
                                else "quiesce_digest"), str)
                            and re.fullmatch(r"[0-9a-f]{64}", message.get(
                                "activation_digest" if kind == "ACTIVATE_V2"
                                else "quiesce_digest", "")) is not None)

            def terminal_refusal(reason: str) -> dict[str, Any]:
                self.admission_open = False
                self._activation_expires_at = None
                if kind == "ACTIVATE_V2":
                    expiry = message.get("activation_expires_at_unix_ms")
                    if (not identifiable or type(expiry) is not int
                            or not now_ms < expiry <= now_ms + 30000):
                        raise ControllerServiceV2Error("admission_closed")
                    count = self.operations.active_count()
                    selected = "admission_closed" if count else reason
                    return self._send({**base, "type": "ACTIVATE_ACK_V2",
                        "reply_to": message["sequence"],
                        "activation_digest": message["activation_digest"],
                        "admission_state": "closed", "activate_decision": "refused",
                        "active_operation_count": count,
                        "acknowledged_at_unix_ms": now_ms,
                        "activation_expires_at_unix_ms": expiry,
                        "reason_code": selected,
                    }, now_ms=now_ms, temporal_context={
                        "request_receipt_unix_ms": now_ms,
                        "activation_expires_at_unix_ms": expiry,
                    })
                deadline = message.get("drain_deadline_unix_ms")
                if (not identifiable or type(deadline) is not int
                        or not now_ms < deadline <= now_ms + 5000):
                    raise ControllerServiceV2Error("admission_closed")
                self._quiesced = True
                return self._send({**base, "type": "QUIESCE_ACK_V2",
                    "reply_to": message["sequence"],
                    "quiesce_digest": message["quiesce_digest"],
                    "admission_state": "closed", "drain_status": "refused",
                    "active_operation_count": 0, "acknowledged_at_unix_ms": now_ms,
                    "drain_deadline_unix_ms": deadline,
                    "reason_code": "identity_mismatch",
                }, now_ms=now_ms, temporal_context={
                    "request_receipt_unix_ms": now_ms,
                    "drain_deadline_unix_ms": deadline,
                })
            if any(message.get(key) != value for key, value in base.items()):
                return terminal_refusal("identity_mismatch")
            if kind == "QUIESCE_V2":
                # Close synchronously before validation or cleanup can block/fail.
                self.admission_open = False
                self._activation_expires_at = None
                self._quiesced = True
            try:
                validate_controller_message_v2(
                    message, direction="controller_to_broker", now_ms=now_ms)
            except Exception:
                return terminal_refusal("digest_mismatch")
            if kind == "ACTIVATE_V2":
                active_count = self.operations.active_count()
                if (self.admission_open or active_count != 0 or self._quiesced
                        or self._terminal_code != "broker_controller_closed"):
                    self.admission_open = False
                    self._activation_expires_at = None
                    return self._send({**base, "type": "ACTIVATE_ACK_V2",
                        "reply_to": message["sequence"],
                        "activation_digest": message.get("activation_digest"),
                        "admission_state": "closed", "activate_decision": "refused",
                        "active_operation_count": active_count,
                        "acknowledged_at_unix_ms": now_ms,
                        "activation_expires_at_unix_ms": message.get(
                            "activation_expires_at_unix_ms"),
                        "reason_code": "admission_closed",
                    }, now_ms=now_ms, temporal_context={
                        "request_receipt_unix_ms": now_ms,
                        "activation_expires_at_unix_ms": message.get(
                            "activation_expires_at_unix_ms"),
                    })
                expected_values = {
                    **base, "type": "ACTIVATE_V2",
                    "request_sequence": message.get("sequence"),
                    **self.config.configured_digests(),
                    "activation_expires_at_unix_ms": message.get("activation_expires_at_unix_ms"),
                }
                valid = (
                    self.config.evidence_id is not None
                    and all(message.get(name) == value
                            for name, value in self.config.configured_digests().items())
                    and type(message.get("activation_expires_at_unix_ms")) is int
                    and now_ms < message["activation_expires_at_unix_ms"] <= now_ms + 30000
                    and message.get("activation_digest") == digest_document_v2(
                        "activation_digest", expected_values)
                )
                if not valid:
                    if self.config.evidence_id is None:
                        reason = "evidence_missing"
                    elif (type(message.get("activation_expires_at_unix_ms")) is not int
                          or not now_ms < message["activation_expires_at_unix_ms"] <= now_ms + 30000):
                        reason = "expired"
                    elif any(message.get(name) != value
                             for name, value in self.config.configured_digests().items()):
                        reason = "proof_mismatch"
                    else:
                        reason = "digest_mismatch"
                    return terminal_refusal(reason)
                self.admission_open = True
                self._activation_expires_at = message["activation_expires_at_unix_ms"]
                return self._send({**base, "type": "ACTIVATE_ACK_V2",
                    "reply_to": message["sequence"],
                    "activation_digest": message["activation_digest"],
                    "admission_state": "open", "activate_decision": "activated",
                    "active_operation_count": active_count,
                    "acknowledged_at_unix_ms": now_ms,
                    "activation_expires_at_unix_ms": message["activation_expires_at_unix_ms"],
                    "reason_code": "activated",
                }, now_ms=now_ms, temporal_context={
                    "request_receipt_unix_ms": now_ms,
                    "activation_expires_at_unix_ms": message["activation_expires_at_unix_ms"],
                })
            if kind != "QUIESCE_V2":
                raise ControllerServiceV2Error("message_type_unknown")
            expected_digest = digest_document_v2("quiesce_digest", {
                **base, "type": "QUIESCE_V2", "request_sequence": message.get("sequence"),
                "reason_code": message.get("reason_code"),
                "drain_deadline_unix_ms": message.get("drain_deadline_unix_ms"),
            })
            if (message.get("quiesce_digest") != expected_digest
                    or type(message.get("drain_deadline_unix_ms")) is not int
                    or not now_ms < message["drain_deadline_unix_ms"] <= now_ms + 5000):
                raise ControllerServiceV2Error("admission_closed")
            failures = []
            try:
                if not self._registry_disconnected:
                    self.registry.disconnect(
                        machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                        controller_epoch=self.controller_epoch, owner=self.owner)
                    self._registry_disconnected = True
            except Exception:
                failures.append("registry_disconnect_refused")
            try:
                self.registry.quiesce()
            except Exception:
                failures.append("registry_quiesce_failed")
            try:
                count = self.operations.quiesce_pre_effect("revoked")
            except ControllerServiceV2Error as exc:
                failures.append(exc.code)
                count = self.operations.active_count()
            except Exception:
                failures.append("operation_cleanup_failed")
                count = self.operations.active_count()
            try:
                self._close_armed_lease_endpoints_v2()
            except ControllerServiceV2Error as exc:
                failures.append(exc.code)
            if failures:
                if self._terminal_code == "broker_controller_closed":
                    self._terminal_code = failures[0]
                raise ControllerServiceV2Error(failures[0])
            status = "drained" if count == 0 else "timeout"
            return self._send({**base, "type": "QUIESCE_ACK_V2",
                "reply_to": message["sequence"], "quiesce_digest": expected_digest,
                "admission_state": "closed", "drain_status": status,
                "active_operation_count": count, "acknowledged_at_unix_ms": now_ms,
                "drain_deadline_unix_ms": message["drain_deadline_unix_ms"],
                "reason_code": "drained" if status == "drained" else "drain_timeout",
            }, now_ms=now_ms, temporal_context={
                "request_receipt_unix_ms": now_ms,
                "drain_deadline_unix_ms": message["drain_deadline_unix_ms"],
            })

    def _terminalize_pre_effect_v2(self, code: str) -> None:
        if not self._registry_disconnected:
            self._registry_disconnected = True
            try:
                self.registry.disconnect(
                    machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                    controller_epoch=self.controller_epoch, owner=self.owner,
                )
            except Exception:
                if self._terminal_code == "broker_controller_closed":
                    self._terminal_code = "registry_disconnect_refused"
        try:
            self.operations.terminalize_all(code)
        except ControllerServiceV2Error as exc:
            if self._terminal_code == "broker_controller_closed":
                self._terminal_code = exc.code
        try:
            self._close_armed_lease_endpoints_v2()
        except ControllerServiceV2Error as exc:
            if self._terminal_code == "broker_controller_closed":
                self._terminal_code = exc.code
        self._claim_anchor = None
        if self._terminal_code != "broker_controller_closed":
            self._quiesced = True
            self.admission_open = False

    def _terminal_guard_v2(self) -> None:
        """Make every T041 entry point observe the same sticky terminal state."""

        if (self._terminal_code == "broker_controller_closed"
                and self.operations is not None
                and self.operations.terminal_code is not None):
            self._terminal_code = self.operations.terminal_code
        if self._terminal_code != "broker_controller_closed":
            self._quiesced = True
            self.admission_open = False
            raise ControllerServiceV2Error(self._terminal_code)
        if self._quiesced or self._closed:
            raise ControllerServiceV2Error("admission_closed")

    def _require_admission_v2(self, now_ms: int) -> None:
        if self._quiesced:
            raise ControllerServiceV2Error(
                self._terminal_code if self._terminal_code != "broker_controller_closed"
                else "admission_closed"
            )
        if (type(now_ms) is not int or not self.authenticated
                or not self.admission_open or self.operations is None
                or self.registry is None or self._activation_expires_at is None):
            raise ControllerServiceV2Error("admission_closed")
        if now_ms >= self._activation_expires_at:
            self.admission_open = False
            self._activation_expires_at = None
            self._quiesced = True
            self._terminalize_pre_effect_v2("authorization_expired")
            raise ControllerServiceV2Error(
                self._terminal_code if self._terminal_code != "broker_controller_closed"
                else "admission_closed"
            )

    def _authorization_mismatch_v2(self, operation_id: Any) -> None:
        anchor = self._claim_anchor
        self._claim_anchor = None
        if self.operations is not None and anchor is not None:
            self.operations.terminalize_known(anchor["operation_id"], "authorization_mismatch")
            if (self.operations.terminal_code is not None
                    and self._terminal_code == "broker_controller_closed"):
                self._terminal_code = self.operations.terminal_code
        raise ControllerServiceV2Error("authorization_mismatch")

    def _refusal_mismatch_v2(self, operation_id: Any) -> None:
        anchor = self._claim_anchor
        self._claim_anchor = None
        if self.operations is not None and anchor is not None:
            self.operations.terminalize_known(anchor["operation_id"], "refusal_mismatch")
            if (self.operations.terminal_code is not None
                    and self._terminal_code == "broker_controller_closed"):
                self._terminal_code = self.operations.terminal_code
        raise ControllerServiceV2Error("refusal_mismatch")

    def handle_authority_v2(self, message: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        """Apply one already-decoded controller frame; no adapter/audit execution."""

        with self._authority_lock:
            self._terminal_guard_v2()
            self._require_admission_v2(now_ms)
            if (not isinstance(message, Mapping) or self.controller_epoch is None):
                raise ControllerServiceV2Error("message_type_unknown")
            kind = message.get("type")
            base = {
                "protocol": CONTROLLER_PROTOCOL_V2, "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch, "controller_epoch": self.controller_epoch,
            }
            if any(message.get(key) != value for key, value in base.items()):
                if kind == "AUTHORIZE_V2":
                    self._authorization_mismatch_v2(message.get("operation_id"))
                if kind == "REFUSE_V2":
                    self._refusal_mismatch_v2(message.get("operation_id"))
                raise ControllerServiceV2Error("controller_frame_identity_mismatch")
            if kind == "CLAIM_NEXT_V2":
                if self._claim_anchor is not None:
                    raise ControllerServiceV2Error("claim_pending")
                claimed = self.operations.claim_next(
                    owner=self.owner, reply_to=message["sequence"],
                    sequence=self._next_outgoing, now_ms=now_ms,
                )
                sent = self._send(
                    {key: value for key, value in claimed.items() if key != "sequence"},
                    now_ms=now_ms,
                    temporal_context=(
                        self.operations.claim_temporal_context(claimed["operation_id"])
                        if claimed["claim_state"] == "claimed" else None
                    ),
                )
                if claimed["claim_state"] == "claimed":
                    self._claim_anchor = {
                        key: claimed[key] for key in (
                            "operation_id", "request_digest", "binding_id", "binding_version",
                        )
                    }
                return sent
            if kind == "REFUSE_V2":
                operation_id = message.get("operation_id")
                anchor = self._claim_anchor
                if (anchor is None or any(message.get(key) != value
                                          for key, value in anchor.items())):
                    self._refusal_mismatch_v2(operation_id)
                try:
                    self.operations.refuse(message)
                except Exception:
                    self._refusal_mismatch_v2(operation_id)
                self._claim_anchor = None
                return {"ok": True, "code": "operation_refused"}
            if kind != "AUTHORIZE_V2":
                raise ControllerServiceV2Error("message_type_unknown")
            operation_id = message.get("operation_id")
            anchor = self._claim_anchor
            if (anchor is None or any(message.get(key) != value
                                      for key, value in anchor.items())
                    or not self.operations.known_claim(anchor["operation_id"])):
                self._authorization_mismatch_v2(operation_id)
            try:
                request_deadline, _binding_version = self.operations.authorization_deadlines(
                    operation_id
                )
                digest_values = {key: message[key] for key in (
                    "protocol", "machine_id", "broker_epoch", "controller_epoch",
                    "operation_id", "request_digest", "binding_id", "binding_version",
                    "auth_form", "policy_digest", "egress_digest", "broker_digest",
                    "proof_digest", "effective_isolation_digest", "evidence_id",
                    "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms",
                    "decision_id",
                )}
                valid = (
                    message["auth_form"] in {"authorization_bearer", "x_api_key"}
                    and message["authorization_digest"] == digest_document_v2(
                        "authorization_digest", digest_values)
                    and all(message[key] == value for key, value in self.config.configured_digests().items())
                    and self.config.evidence_id is not None
                    and now_ms < message["authorization_expires_at_unix_ms"] <= min(
                        now_ms + 5000, message["binding_expires_at_unix_ms"],
                        self._activation_expires_at, request_deadline)
                    and message["binding_expires_at_unix_ms"] > message["authorization_expires_at_unix_ms"]
                )
            except Exception:
                valid = False
            if not valid:
                self._authorization_mismatch_v2(operation_id)
            endpoint = None
            try:
                if self._lease_endpoint_factory is None:
                    raise ControllerServiceV2Error("lease_endpoint_unavailable")
                identity = AuthorizationIdentityV2(
                    owner=self.owner, machine_id=self.config.machine_id,
                    broker_epoch=self.broker_epoch, controller_epoch=self.controller_epoch,
                    operation_id=message["operation_id"], request_digest=message["request_digest"],
                    binding_id=message["binding_id"], binding_version=message["binding_version"],
                    decision_id=message["decision_id"],
                    authorization_digest=message["authorization_digest"],
                    expires_at_unix_ms=message["authorization_expires_at_unix_ms"],
                    binding_expires_at_unix_ms=message["binding_expires_at_unix_ms"],
                    activation_expires_at_unix_ms=self._activation_expires_at,
                    request_deadline_unix_ms=request_deadline,
                )
                self.registry.insert(identity, now_ms=now_ms)
                self.operations.authorize(message, identity=identity, now_ms=now_ms)
                endpoint = _V2LeaseDeliveryEndpoint(self, operation_id)
                address = lease_endpoint_address_v2(
                    machine_id=self.config.machine_id,
                    broker_epoch=self.broker_epoch,
                    controller_epoch=self.controller_epoch,
                    broker_digest=self.config.broker_digest,
                    broker_config_digest=self.config.broker.config_digest,
                    controller_config_digest=self.config.controller.config_digest,
                    operation_id=operation_id,
                    authorization_digest=message["authorization_digest"],
                )
                endpoint.arm(address, self._lease_endpoint_factory)
                self._lease_endpoints[operation_id] = endpoint
            except Exception as exc:
                if endpoint is not None:
                    try:
                        endpoint.close()
                    except Exception:
                        if self._terminal_code == "broker_controller_closed":
                            self._terminal_code = "lease_endpoint_cleanup_failed"
                cleanup_failed = False
                try:
                    self.registry.revoke(
                        machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                        controller_epoch=self.controller_epoch, owner=self.owner,
                        operation_id=operation_id,
                    )
                except Exception:
                    cleanup_failed = True
                if cleanup_failed:
                    try:
                        self._authorization_mismatch_v2(operation_id)
                    except ControllerServiceV2Error:
                        pass
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = "registry_revoke_refused"
                    self._quiesced = True
                    self.admission_open = False
                    self._terminalize_pre_effect_v2("revoked")
                    raise ControllerServiceV2Error(self._terminal_code) from None
                if (isinstance(exc, ControllerServiceV2Error)
                        and exc.code.startswith("lease_endpoint_")):
                    self._terminal_code = (
                        self._terminal_code if self._terminal_code != "broker_controller_closed"
                        else exc.code
                    )
                    self._quiesced = True
                    self.admission_open = False
                    self._terminalize_pre_effect_v2("revoked")
                    raise ControllerServiceV2Error(self._terminal_code) from None
                self._authorization_mismatch_v2(operation_id)
            self._claim_anchor = None
            response = self._send({**base, "type": "AUTHORIZED_V2",
                "reply_to": message["sequence"], "operation_id": message["operation_id"],
                "request_digest": message["request_digest"], "binding_id": message["binding_id"],
                "binding_version": message["binding_version"], "decision_id": message["decision_id"],
                "authorization_digest": message["authorization_digest"],
                "authorization_expires_at_unix_ms": message["authorization_expires_at_unix_ms"],
            }, now_ms=now_ms, temporal_context={
                "authorization_expires_at_unix_ms": message["authorization_expires_at_unix_ms"],
            })
            return response

    def lease_endpoint_v2(self, operation_id: str) -> _V2LeaseDeliveryEndpoint:
        with self._authority_lock:
            self._terminal_guard_v2()
            if not isinstance(operation_id, str):
                raise ControllerServiceV2Error("lease_invalid")
            endpoint = self._lease_endpoints.get(operation_id)
            if endpoint is None:
                raise ControllerServiceV2Error("lease_invalid")
            return endpoint

    def _close_armed_lease_endpoints_v2(self, operation_ids=None) -> None:
        selected = (set(self._lease_endpoints) if operation_ids is None
                    else set(operation_ids))
        failure = None
        for operation_id in tuple(selected):
            endpoint = self._lease_endpoints.pop(operation_id, None)
            if endpoint is not None:
                try:
                    endpoint.close()
                except Exception:
                    failure = failure or "lease_endpoint_cleanup_failed"
        if failure is not None:
            raise ControllerServiceV2Error(failure)

    def _finish_lease_endpoint_attempt_v2(self, operation_id: str, listener,
                                          *, failed: bool) -> None:
        with self._authority_lock:
            endpoint = self._lease_endpoints.pop(operation_id, None)
            if endpoint is not None and endpoint.listener is listener:
                endpoint.listener = None
                endpoint._closed = True
            if failed and self.operations is not None:
                self.operations.terminalize_known(operation_id, "lease_invalid")
                try:
                    self.registry.release_failed_delivery(
                        machine_id=self.config.machine_id,
                        broker_epoch=self.broker_epoch,
                        controller_epoch=self.controller_epoch, owner=self.owner,
                        operation_id=operation_id)
                except Exception:
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = "registry_revoke_refused"
                    self._quiesced = True
                    self.admission_open = False
                    raise ControllerServiceV2Error(self._terminal_code) from None

    def _record_lease_endpoint_cleanup_failure_v2(self, code: str) -> None:
        with self._authority_lock:
            if self._terminal_code == "broker_controller_closed":
                self._terminal_code = (code if code in {
                    "descriptor_cleanup_failed", "lease_socket_cleanup_failed",
                    "lease_endpoint_cleanup_failed", "operation_cleanup_failed",
                } else "lease_endpoint_cleanup_failed")
            self._quiesced = True
            self.admission_open = False
            self._terminalize_pre_effect_v2("revoked")

    def _authenticated_lease_socket_receipt_v2(
            self, operation_id: str, connection, *, observer, so_peercred: int):
        """T040-only handoff after its kernel peer and packet authentication."""
        with self._authority_lock:
            self._terminal_guard_v2()
            try:
                peer = observe_socket_peer_credentials_v2(
                    connection, so_peercred=so_peercred)
                observed_controller = observer(*peer)
            except Exception:
                raise ControllerServiceV2Error("lease_invalid") from None
            if (observed_controller != self.config.controller
                    or operation_id not in self._lease_endpoints
                    or not callable(getattr(connection, "sendall", None))
                    or not callable(getattr(connection, "close", None))):
                raise ControllerServiceV2Error("lease_invalid")
            return _V2AcceptedLeaseSocketReceipt(
                _ACCEPTED_LEASE_SOCKET_ISSUER, self, operation_id,
                connection, observed_controller)

    def _accept_bound_lease_v2(self, operation_id: str, packet: bytes,
                               descriptors: Any, *,
                               observed_controller: ProcessIdentityV2,
                               descriptor_observer, descriptor_closer,
                               now_ms: int, first_attempt: bool,
                               lease_connection=None) -> dict[str, Any]:
        """Consume the endpoint authorization before decoding any delivery bytes."""

        received = tuple(descriptors) if isinstance(descriptors, (list, tuple)) else ()
        closed_descriptors: set[int] = set()

        def close_received(values) -> None:
            failed = False
            for value in values:
                if type(value) is int and value >= 0 and value not in closed_descriptors:
                    closed_descriptors.add(value)
                    try:
                        descriptor_closer(value)
                    except Exception:
                        failed = True
            if failed and self._terminal_code == "broker_controller_closed":
                self._terminal_code = "descriptor_cleanup_failed"

        with self._authority_lock:
            try:
                self._terminal_guard_v2()
                self._require_admission_v2(now_ms)
                if not first_attempt:
                    raise ControllerServiceV2Error("lease_invalid")
                snapshot = self.operations.begin_delivery(
                    operation_id, registry=self.registry, now_ms=now_ms,
                )
                authorization = snapshot["authorization"]
                if (observed_controller != self.config.controller
                        or self._lease_sequences is None
                        or not callable(descriptor_closer)):
                    raise ControllerServiceV2Error("lease_invalid")
                try:
                    decoded = decode_lease_frame_v2(packet, now_ms=now_ms, deadline_caps={
                        "authorization_expires_at_unix_ms": authorization["authorization_expires_at_unix_ms"],
                        "binding_expires_at_unix_ms": authorization["binding_expires_at_unix_ms"],
                        "activation_expires_at_unix_ms": self._activation_expires_at,
                        "request_deadline_unix_ms": snapshot["request_deadline_unix_ms"],
                    })
                except Exception:
                    raise ControllerServiceV2Error("lease_invalid") from None
                if decoded["operation_id"] != operation_id:
                    raise ControllerServiceV2Error("lease_invalid")
                if (len(received) != 1 or type(received[0]) is not int or received[0] < 0):
                    raise ControllerServiceV2Error("lease_invalid")
                descriptor = received[0]
                self.operations.bind_delivery_descriptor(
                    operation_id, decoded, lease_sequences=self._lease_sequences,
                    descriptor=descriptor, descriptor_observer=descriptor_observer,
                    descriptor_closer=descriptor_closer,
                    lease_connection=lease_connection,
                )
                return {"ok": True, "code": "lease_bound", "operation_id": operation_id}
            except Exception as exc:
                if first_attempt:
                    self.operations.terminalize_known(operation_id, "lease_invalid")
                    try:
                        self.registry.release_failed_delivery(
                            machine_id=self.config.machine_id,
                            broker_epoch=self.broker_epoch,
                            controller_epoch=self.controller_epoch,
                            owner=self.owner, operation_id=operation_id)
                    except Exception:
                        if self._terminal_code == "broker_controller_closed":
                            self._terminal_code = "registry_revoke_refused"
                    if (self.operations.terminal_code is not None
                            and self._terminal_code == "broker_controller_closed"):
                        self._terminal_code = self.operations.terminal_code
                close_received(received)
                if lease_connection is not None:
                    try:
                        lease_connection.close()
                    except Exception:
                        if self._terminal_code == "broker_controller_closed":
                            self._terminal_code = "lease_socket_cleanup_failed"
                code = (exc.code if isinstance(exc, ControllerServiceV2Error)
                        and exc.code in {"admission_closed", "descriptor_cleanup_failed"}
                        else "lease_invalid")
                if self._terminal_code != "broker_controller_closed":
                    self._quiesced = True
                    self.admission_open = False
                    self._terminalize_pre_effect_v2("revoked")
                    code = self._terminal_code
                raise ControllerServiceV2Error(code) from None

    def _indeterminate_disconnect_v2(self, operation_id: str, code: str) -> None:
        """Preserve first uncertainty and exhaustively close all connection-owned state."""

        with self._authority_lock:
            self.admission_open = False
            self._activation_expires_at = None
            self._quiesced = True
            self.operations.mark_indeterminate(operation_id, code)
            if self._terminal_code == "broker_controller_closed":
                self._terminal_code = code
            self._terminalize_pre_effect_v2("revoked")
        self.close(code)

    def execute_effect_v2(self, operation_id: str, *,
                          egress_decision: AuthorizedEgressDecisionV2,
                          audit_id_factory, executor: EffectExecutionV2,
                          monotonic, wall_clock) -> dict[str, Any]:
        """Run one connection-owned PRE/effect/POST/ACK flow without effect replay."""

        return self._execute_effect_flow_v2(
            operation_id, egress_decision=egress_decision,
            audit_id_factory=audit_id_factory, executor=executor,
            monotonic=monotonic, wall_clock=wall_clock,
            pre_effect_refusal=None)

    def refuse_effect_v2(self, operation_id: str, *, reason_code: str,
                         audit_id_factory, monotonic, wall_clock) -> dict[str, Any]:
        """Durably refuse after PRE without entering or replaying an effect."""

        if reason_code != "egress_denied":
            raise ControllerServiceV2Error("effect_executor_invalid")
        return self._execute_effect_flow_v2(
            operation_id, egress_decision=None,
            audit_id_factory=audit_id_factory, executor=None,
            monotonic=monotonic, wall_clock=wall_clock,
            pre_effect_refusal=reason_code)

    def _execute_effect_flow_v2(self, operation_id: str, *,
                                egress_decision,
                                audit_id_factory, executor,
                                monotonic, wall_clock,
                                pre_effect_refusal: str | None) -> dict[str, Any]:

        typed_effect = pre_effect_refusal is None
        if (((typed_effect and (type(egress_decision) is not AuthorizedEgressDecisionV2
                                or type(executor) is EffectExecutionV2
                                or not isinstance(executor, EffectExecutionV2)))
                or (not typed_effect and (pre_effect_refusal != "egress_denied"
                                           or egress_decision is not None
                                           or executor is not None)))
                or not callable(audit_id_factory)
                or not callable(monotonic) or not callable(wall_clock)):
            raise ControllerServiceV2Error("effect_executor_invalid")

        def fresh_now() -> int:
            try:
                value = wall_clock()
                return self._observation.observe(value)
            except Exception:
                raise ControllerServiceV2Error("clock_uncertain") from None

        def exchange_semantic(message, *, phase, phase_id, fingerprint,
                              absolute_wall_deadline: int):
            started = monotonic()
            if not isinstance(started, (int, float)):
                raise ControllerServiceV2Error("clock_uncertain")
            for attempt in range(2):
                current = monotonic()
                if (not isinstance(current, (int, float)) or current < started
                        or current - started >= 1.0):
                    return None
                sent_at = fresh_now()
                if sent_at > absolute_wall_deadline:
                    return None
                sent = self._send(message, now_ms=sent_at)
                before_receive = monotonic()
                if (not isinstance(before_receive, (int, float))
                        or before_receive < current or before_receive - started >= 1.0):
                    return None
                wall_now = fresh_now()
                if wall_now > absolute_wall_deadline:
                    return None
                remaining = min(
                    1.0 - (before_receive - started),
                    (absolute_wall_deadline - wall_now) / 1000.0,
                )
                if remaining <= 0:
                    return None
                try:
                    ack = self.wait_audit_ack_v2(sent["sequence"], remaining)
                    if ack is None:
                        if attempt == 0:
                            continue
                        return None
                except (TimeoutError, socket.timeout):
                    if attempt == 0:
                        continue
                    return None
                received = monotonic()
                if (not isinstance(received, (int, float))
                        or received < before_receive or received - started > 1.0):
                    return None
                if (ack.get("type") != "AUDIT_ACK_V2"
                        or ack.get("reply_to") != sent["sequence"]
                        or ack.get("audit_root_id") != message["audit_root_id"]
                        or ack.get("phase") != phase or ack.get("phase_id") != phase_id
                        or ack.get("audit_fingerprint") != fingerprint
                        or ack.get("disposition") != "committed"):
                    raise ControllerServiceV2Error("audit_ack_invalid")
                return ack
            return None

        with self._audit_lock:
            start_now = fresh_now()
            with self._authority_lock:
                self._terminal_guard_v2()
                self._require_admission_v2(start_now)
                context = self.operations.effect_context(operation_id, now_ms=start_now)
            authorization, lease = context["authorization"], context["lease"]
            request = context["request"]
            try:
                effect_context = None if not typed_effect else AuthorizedEffectContextV2(
                    request=request, egress_decision=egress_decision,
                    egress_digest=authorization["egress_digest"],
                    machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                    controller_epoch=self.controller_epoch, operation_id=operation_id,
                    request_digest=authorization["request_digest"],
                    binding_id=authorization["binding_id"],
                    binding_version=authorization["binding_version"],
                    decision_id=authorization["decision_id"],
                    authorization_digest=authorization["authorization_digest"],
                    auth_form=authorization["auth_form"], lease_id=lease["lease_id"],
                    lease_sequence=lease["lease_sequence"],
                    descriptor_size=lease["descriptor_size"],
                    request_deadline_unix_ms=context["request_deadline_unix_ms"],
                    binding_expires_at_unix_ms=authorization["binding_expires_at_unix_ms"],
                    authorization_expires_at_unix_ms=authorization[
                        "authorization_expires_at_unix_ms"],
                    lease_expires_at_unix_ms=lease["lease_expires_at_unix_ms"],
                    activation_expires_at_unix_ms=self._activation_expires_at)
            except Exception:
                self.operations.terminalize_known(operation_id, "internal_refusal")
                raise ControllerServiceV2Error("effect_context_invalid") from None
            lease_connection = context["lease_connection"]
            if (not isinstance(lease_connection, _V2AuthenticatedLeaseConnection)
                    or lease_connection.machine_id != self.config.machine_id
                    or lease_connection.broker_epoch != self.broker_epoch
                    or lease_connection.controller_epoch != self.controller_epoch
                    or lease_connection.owner != self.owner
                    or lease_connection.peer != self.config.controller):
                self._indeterminate_disconnect_v2(operation_id, "lease_ack_failed")
                raise ControllerServiceV2Error("lease_ack_failed")
            try:
                audit_root_id = audit_id_factory("root")
                pre_phase_id = audit_id_factory("pre")
                post_phase_id = audit_id_factory("post")
            except Exception:
                self._indeterminate_disconnect_v2(operation_id, "audit_unavailable")
                raise ControllerServiceV2Error("audit_unavailable") from None
            if (any(not isinstance(value, str) or re.fullmatch(
                    r"audit-[a-z0-9]{10,57}", value) is None
                    for value in (audit_root_id, pre_phase_id, post_phase_id))
                    or len({audit_root_id, pre_phase_id, post_phase_id}) != 3):
                self._indeterminate_disconnect_v2(operation_id, "audit_unavailable")
                raise ControllerServiceV2Error("audit_unavailable")
            base = {
                "protocol": CONTROLLER_PROTOCOL_V2, "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch, "controller_epoch": self.controller_epoch,
                "operation_id": operation_id, "binding_id": authorization["binding_id"],
                "binding_version": authorization["binding_version"],
                "decision_id": authorization["decision_id"], "audit_root_id": audit_root_id,
            }
            pre_values = {**base, "phase_id": pre_phase_id,
                          "event_code": "credential_effect_pre"}
            pre_fingerprint = digest_document_v2("audit_pre_fingerprint", {
                key: pre_values[key] for key in (
                    "machine_id", "operation_id", "binding_id", "binding_version",
                    "decision_id", "audit_root_id", "phase_id", "event_code")})
            pre_message = {**base, "type": "AUDIT_PRE_V2", "phase_id": pre_phase_id,
                           "audit_fingerprint": pre_fingerprint,
                           "event_code": "credential_effect_pre"}
            try:
                pre_deadline = min(
                    start_now + 1000, context["request_deadline_unix_ms"])
                pre_ack = exchange_semantic(pre_message, phase="pre",
                                            phase_id=pre_phase_id,
                                            fingerprint=pre_fingerprint,
                                            absolute_wall_deadline=pre_deadline)
            except Exception as exc:
                self._indeterminate_disconnect_v2(operation_id, "audit_unavailable")
                raise ControllerServiceV2Error("audit_unavailable") from None
            if pre_ack is None:
                self._indeterminate_disconnect_v2(operation_id, "audit_unavailable")
                raise ControllerServiceV2Error("audit_unavailable")
            self.operations.transition_effect(operation_id, "lease_bound", "pre_audited")

            after_pre = fresh_now()
            before_effect = fresh_now()
            expired = (before_effect < after_pre or self._quiesced
                       or before_effect >= context["request_deadline_unix_ms"]
                       or before_effect >= authorization["authorization_expires_at_unix_ms"]
                       or before_effect >= lease["lease_expires_at_unix_ms"]
                       or self._activation_expires_at is None
                       or before_effect >= self._activation_expires_at)
            if pre_effect_refusal is not None:
                guest = GuestResultV2.failure(
                    state="refused", code=pre_effect_refusal,
                    retryable=False, correlation_id=request.correlation_id)
                result = EffectExecutionResultV2(
                    guest, "pre_effect", "refused", "none", pre_effect_refusal)
                prior_state = "pre_audited"
            elif expired:
                guest = GuestResultV2.failure(
                    state="refused",
                    code="revoked" if self._quiesced else "deadline_exceeded",
                    retryable=False, correlation_id=request.correlation_id)
                result = EffectExecutionResultV2(
                    guest, "pre_effect", "refused", "none", guest.outcome_code)
                prior_state = "pre_audited"
            else:
                self.operations.transition_effect(operation_id, "pre_audited", "effect_possible")
                prior_state = "effect_possible"
                try:
                    result = executor.execute(effect_context, context["descriptor"])
                    if (type(result) is not EffectExecutionResultV2
                            or result.effect_phase != "effect_entered"):
                        raise GuestProtocolV2Error("effect_indeterminate")
                except Exception:
                    result = EffectExecutionResultV2(
                        GuestResultV2.failure(
                            state="indeterminate", code="internal_indeterminate",
                            retryable=False, correlation_id=request.correlation_id),
                        "effect_entered", "indeterminate", "possible",
                        "internal_indeterminate")

            post_values = {**base, "phase_id": post_phase_id,
                           "pre_commit_id": pre_ack["commit_id"],
                           "outcome_class": result.outcome_class,
                           "effect_certainty": result.effect_certainty,
                           "reason_code": result.reason_code}
            post_fingerprint = digest_document_v2("audit_post_fingerprint", {
                key: post_values[key] for key in (
                    "machine_id", "operation_id", "binding_id", "binding_version",
                    "decision_id", "audit_root_id", "phase_id", "pre_commit_id",
                    "outcome_class", "effect_certainty", "reason_code")})
            post_message = {**base, "type": "AUDIT_POST_V2", "phase_id": post_phase_id,
                            "audit_fingerprint": post_fingerprint,
                            "pre_commit_id": pre_ack["commit_id"],
                            "outcome_class": result.outcome_class,
                            "effect_certainty": result.effect_certainty,
                            "reason_code": result.reason_code}
            try:
                post_started = fresh_now()
                post_deadline = post_ack_deadline_v2(
                    post_started, context["request_deadline_unix_ms"])
                post_ack = exchange_semantic(post_message, phase="post",
                                             phase_id=post_phase_id,
                                             fingerprint=post_fingerprint,
                                             absolute_wall_deadline=post_deadline)
            except Exception:
                post_ack = None
            if post_ack is None:
                self._indeterminate_disconnect_v2(operation_id, "effect_indeterminate")
                raise ControllerServiceV2Error("effect_indeterminate")
            self.operations.transition_effect(operation_id, prior_state, "post_audited")
            try:
                post_ack_received = fresh_now()
                ack_send_deadline = broker_ack_send_deadline_v2(
                    post_ack_received, context["request_deadline_unix_ms"])
                ack_now = fresh_now()
                if ack_now > ack_send_deadline:
                    raise ControllerServiceV2Error("lease_ack_failed")
                acknowledgement = encode_lease_ack_v2({
                    "type": "LEASE_ACK_V2", "machine_id": self.config.machine_id,
                    "broker_epoch": self.broker_epoch,
                    "controller_epoch": self.controller_epoch,
                    "lease_id": lease["lease_id"],
                    "lease_sequence": lease["lease_sequence"],
                    "authorization_digest": authorization["authorization_digest"],
                    "audit_root_id": audit_root_id, "post_phase_id": post_phase_id,
                    "post_commit_id": post_ack["commit_id"],
                    "outcome_class": result.outcome_class,
                    "effect_certainty": result.effect_certainty,
                    "reason_code": result.reason_code})
                self.operations.close_effect_descriptor(operation_id)
                lease_connection.send_ack(acknowledgement)
                self.operations.finalize_effect(
                    operation_id, outcome_class=result.outcome_class,
                    reason_code=result.reason_code,
                )
            except Exception:
                self._indeterminate_disconnect_v2(operation_id, "lease_ack_failed")
                raise ControllerServiceV2Error("lease_ack_failed") from None
            return {"ok": result.outcome_class == "completed", "code": result.reason_code,
                    "outcome_class": result.outcome_class,
                    "effect_certainty": result.effect_certainty,
                    "effect_phase": result.effect_phase,
                    "guest_result": result.guest_result,
                    "audit_root_id": audit_root_id, "post_phase_id": post_phase_id,
                    "post_commit_id": post_ack["commit_id"]}

    def revoke_v2(self, binding_id: str) -> int:
        """Atomically invalidate both operation and pinned authorization state."""

        with self._authority_lock:
            self._terminal_guard_v2()
            if not isinstance(binding_id, str):
                raise ControllerServiceV2Error("revoke_scope_invalid")
            failure_code = None
            try:
                removed_auth = self.registry.revoke(
                    machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
                    controller_epoch=self.controller_epoch, owner=self.owner,
                    binding_id=binding_id,
                )
            except Exception:
                removed_auth = 0
                failure_code = "registry_revoke_refused"
            try:
                removed_ops = self.operations.revoke(binding_id)
            except ControllerServiceV2Error as exc:
                removed_ops = 0
                if failure_code is None:
                    failure_code = exc.code
            except Exception:
                removed_ops = 0
                if failure_code is None:
                    failure_code = "operation_cleanup_failed"
            terminal_endpoints = tuple(
                operation_id for operation_id in self._lease_endpoints
                if self.operations.state(operation_id) in {
                    "refused", "completed", "indeterminate"})
            try:
                self._close_armed_lease_endpoints_v2(terminal_endpoints)
            except ControllerServiceV2Error as exc:
                if failure_code is None:
                    failure_code = exc.code
            if failure_code is not None:
                if self._terminal_code == "broker_controller_closed":
                    self._terminal_code = failure_code
                self._quiesced = True
                self.admission_open = False
                self._terminalize_pre_effect_v2("revoked")
                raise ControllerServiceV2Error(self._terminal_code) from None
            return max(removed_auth, removed_ops)

    def close(self, reason: str = "controller_disconnected") -> dict[str, Any]:
        if not self._closed:
            self._closed = True
            self._quiesced = True
            self.authenticated = False
            self.admission_open = False
            for endpoint in tuple(self._lease_endpoints.values()):
                try:
                    endpoint.close()
                except Exception:
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = "lease_endpoint_cleanup_failed"
            self._lease_endpoints.clear()
            if self.registry is not None:
                if not self._registry_disconnected:
                    self._registry_disconnected = True
                    try:
                        self.registry.disconnect(
                            machine_id=self.config.machine_id,
                            broker_epoch=self.broker_epoch,
                            controller_epoch=self.controller_epoch,
                            owner=self.owner,
                        )
                    except Exception:
                        if self._terminal_code == "broker_controller_closed":
                            self._terminal_code = "registry_disconnect_refused"
                try:
                    self.registry.quiesce()
                except Exception:
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = "registry_quiesce_failed"
            if self.operations is not None:
                try:
                    self.operations.terminalize_all("revoked")
                except ControllerServiceV2Error as exc:
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = exc.code
                except Exception:
                    if self._terminal_code == "broker_controller_closed":
                        self._terminal_code = "operation_cleanup_failed"
            try:
                self.connection.close()
            except Exception:
                if self._terminal_code == "broker_controller_closed":
                    self._terminal_code = "controller_socket_cleanup_failed"
            with self._audit_condition:
                self._audit_condition.notify_all()
            try:
                self._on_terminal(
                    reason if isinstance(reason, str) else "controller_disconnected"
                )
            except Exception:
                if self._terminal_code == "broker_controller_closed":
                    self._terminal_code = "terminal_callback_failed"
        return {"ok": self._terminal_code == "broker_controller_closed",
                "code": self._terminal_code, "admission_open": False}


class LinuxControllerV2Listener:
    """Inert broker-owned abstract SOCK_SEQPACKET v2 listener.

    Explicit ``start`` is the only I/O path.  One listener object represents
    one broker process epoch and can authenticate at most one connection.
    """

    __slots__ = (
        "config", "_epoch_factory", "_owner_factory", "_socket_factory",
        "_lease_endpoint_factory",
        "broker_epoch", "listener", "session", "admission_open", "_started",
        "_stopped", "_authenticated_once", "_terminal_result",
    )

    def __init__(self, config: ControllerServiceConfigV2, *, epoch_factory,
                 owner_factory, socket_factory=socket.socket,
                 lease_endpoint_factory=None) -> None:
        if (type(config) is not ControllerServiceConfigV2
                or not callable(epoch_factory) or not callable(owner_factory)
                or not callable(socket_factory)
                or (lease_endpoint_factory is not None
                    and not callable(lease_endpoint_factory))):
            raise ControllerServiceV2Error("controller_listener_invalid")
        self.config = config
        self._epoch_factory = epoch_factory
        self._owner_factory = owner_factory
        self._socket_factory = socket_factory
        self._lease_endpoint_factory = lease_endpoint_factory
        self.broker_epoch = None
        self.listener = None
        self.session = None
        self.admission_open = False
        self._started = False
        self._stopped = False
        self._authenticated_once = False
        self._terminal_result = None

    def start(self, *, platform: str, enabled: bool, effective_uid: int,
              self_observer) -> dict[str, Any]:
        if self._terminal_result is not None:
            raise ControllerServiceV2Error(self._terminal_result["code"])
        if (self._started or self._stopped or enabled is not True or platform != "linux"
                or type(effective_uid) is not int or effective_uid < 1
                or effective_uid != self.config.broker.uid
                or not callable(self_observer)
                or not hasattr(socket, "SOCK_SEQPACKET")
                or type(getattr(socket, "SO_PASSCRED", None)) is not int
                or getattr(socket, "SO_PASSCRED", 0) < 1):
            raise ControllerServiceV2Error("controller_listener_start_refused")
        try:
            if self_observer() != self.config:
                raise ControllerServiceV2Error("broker_self_identity_mismatch")
        except Exception:
            raise ControllerServiceV2Error("controller_listener_start_refused") from None
        try:
            epoch = self._epoch_factory()
        except Exception:
            raise ControllerServiceV2Error("broker_epoch_invalid") from None
        if not isinstance(epoch, str) or re.fullmatch(r"[0-9a-f]{32}", epoch) is None:
            raise ControllerServiceV2Error("broker_epoch_invalid")
        listener = None
        try:
            listener = self._socket_factory(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            listener.bind(abstract_controller_address_v2(
                self.config.machine_id, self.config.broker_digest,
            ))
            listener.listen(1)
        except Exception:
            cleanup_failed = False
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                self._stopped = True
                self._terminal_result = {
                    "ok": False, "code": "listener_cleanup_failed",
                    "admission_open": False,
                }
            raise ControllerServiceV2Error(
                "listener_cleanup_failed" if cleanup_failed
                else "controller_listener_start_refused"
            ) from None
        self.broker_epoch = epoch
        self.listener = listener
        self._started = True
        return {"ok": True, "code": "controller_listener_started", "admission_open": False}

    def accept_once(self, *, observer, now_ms: int, monotonic, so_peercred: int,
                    scm_credentials: int, scm_rights: int, closer,
                    registry_factory=AuthorizationRegistryV2) -> dict[str, Any]:
        if self._terminal_result is not None:
            raise ControllerServiceV2Error(self._terminal_result["code"])
        if (not self._started or self._stopped or self.listener is None
                or self._authenticated_once or self.session is not None):
            raise ControllerServiceV2Error("controller_connection_refused")
        connection = None
        session = None
        transferred = False
        try:
            connection, _address = self.listener.accept()
            owner = self._owner_factory()
            session = BrokerControllerV2Connection(
                connection, self.config, self.broker_epoch, owner,
                registry_factory=registry_factory,
                on_terminal=lambda _reason: setattr(self, "admission_open", False),
                lease_endpoint_factory=self._lease_endpoint_factory,
            )
            transferred = True
            result = session.handshake(
                observer=observer, now_ms=now_ms, monotonic=monotonic,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
            self.session = session
            self._authenticated_once = True
            return result
        except ControllerServiceV2Error as exc:
            cleanup_failed = False
            if transferred and session is not None:
                closed = session.close("handshake_refused")
                if not closed["ok"]:
                    self._terminal_result = dict(closed)
                    raise ControllerServiceV2Error(closed["code"]) from None
            elif connection is not None:
                try:
                    connection.close()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                self._terminal_result = {
                    "ok": False, "code": "controller_socket_cleanup_failed",
                    "admission_open": False,
                }
            raise ControllerServiceV2Error(
                "controller_socket_cleanup_failed" if cleanup_failed else exc.code
            ) from None
        except Exception:
            cleanup_failed = False
            if transferred and session is not None:
                closed = session.close("handshake_refused")
                if not closed["ok"]:
                    self._terminal_result = dict(closed)
                    raise ControllerServiceV2Error(closed["code"]) from None
            elif connection is not None:
                try:
                    connection.close()
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                self._terminal_result = {
                    "ok": False, "code": "controller_socket_cleanup_failed",
                    "admission_open": False,
                }
            raise ControllerServiceV2Error(
                "controller_socket_cleanup_failed" if cleanup_failed
                else "controller_connection_refused"
            ) from None

    def disconnect(self) -> dict[str, Any]:
        if self._terminal_result is not None:
            return dict(self._terminal_result)
        session, self.session = self.session, None
        if session is not None:
            result = session.close("controller_disconnected")
            if not result["ok"]:
                self.admission_open = False
                self._terminal_result = dict(result)
                return dict(result)
        self.admission_open = False
        return {"ok": True, "code": "controller_disconnected", "admission_open": False}

    def close(self) -> dict[str, Any]:
        result = self._terminal_result or {
            "ok": True, "code": "controller_listener_closed",
            "admission_open": False,
        }
        if not self._stopped:
            disconnected = self.disconnect()
            if not disconnected["ok"]:
                result = disconnected
            listener, self.listener = self.listener, None
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    if result["ok"]:
                        result = {"ok": False, "code": "listener_cleanup_failed",
                                  "admission_open": False}
            self._stopped = True
            self._terminal_result = result
        return dict(result)


class CoordinatorLeaseEndpoint:
    """Coordinator-connected one-frame SCM_RIGHTS receive endpoint."""

    def __init__(self, service: Any, coordinator: CredentialBrokerCoordinator, *,
                 control_plane_uid: int, peer_observer, descriptor_observer=None,
                 enabled: bool = False, socket_factory=None) -> None:
        if not _validate_service_identity(service) or coordinator.service != service \
                or not _integer(control_plane_uid, minimum=1, maximum=2**31 - 1) \
                or not callable(peer_observer) or not isinstance(enabled, bool) \
                or (descriptor_observer is not None and not callable(descriptor_observer)) \
                or (socket_factory is not None and not callable(socket_factory)):
            raise ValueError("coordinator lease endpoint configuration is invalid")
        self.service = dict(service)
        self.coordinator = coordinator
        self.control_plane_uid = control_plane_uid
        self.peer_observer = peer_observer
        self.descriptor_observer = descriptor_observer or _linux_descriptor_observation
        self.enabled = enabled
        self.socket_factory = socket_factory or socket.socket
        self.listener = None
        self._admission_open = False

    @property
    def admission_open(self) -> bool:
        return self.listener is not None and self._admission_open

    def activate_from_coordinator(self) -> dict[str, Any]:
        if self.listener is None or not self.coordinator.enabled:
            return bounded_error("lease_channel_closed")
        self._admission_open = True
        return {"ok": True, "code": "lease_admission_open"}

    def quiesce_from_coordinator(self) -> None:
        self._admission_open = False

    def start(self) -> dict[str, Any]:
        if not self.enabled or self.listener is not None:
            return bounded_error("lease_channel_closed")
        if _running_as_root():
            return bounded_error("root_execution_denied")
        try:
            _require_linux_transport()
            listener = self.socket_factory(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            listener.bind(_abstract_lease_address(self.service))
            listener.listen(MAX_ACTIVE_REQUESTS)
        except (OSError, RuntimeError):
            try: listener.close()
            except (NameError, OSError): pass
            return bounded_error("lease_channel_unavailable")
        self.listener = listener
        return {"ok": True, "code": "lease_channel_started", "admission_open": False}

    def receive_once(self) -> dict[str, Any]:
        if self.listener is None or not self._admission_open \
                or not self.coordinator.admission_open:
            return bounded_error("lease_channel_closed")
        connection = None
        ancillary = None
        descriptor = None
        transferred_descriptor = None
        frame = None
        prepared_attempt = None
        try:
            connection, _address = self.listener.accept()
            setter = getattr(connection, "settimeout", None)
            if callable(setter): setter(5.0)
            pid, uid = _peer_pid_uid(connection)
            if uid != self.control_plane_uid:
                return bounded_error("dispatcher_denied")
            packet, ancillary, flags, _address = connection.recvmsg(
                MAX_FRAME_BYTES, socket.CMSG_SPACE(array("i").itemsize * 2),
                getattr(socket, "MSG_CMSG_CLOEXEC", 0),
            )
            truncation = getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)
            observed = self.peer_observer(connection, pid, uid)
            if not isinstance(observed, dict) or not observed:
                frame = _recover_canonical_lease_prefix(packet)
                if frame is None:
                    return bounded_error("frame_invalid")
                acknowledgement = self.coordinator.reject_lease(frame)
                connection.sendall(json.dumps(
                    acknowledgement, sort_keys=True, separators=(",", ":"),
                ).encode("ascii"))
                return {"ok": False, **acknowledgement}
            malformed = bool(flags & truncation)
            if not malformed:
                try:
                    frame = parse_lease_frame(packet)
                except ValueError:
                    frame = _recover_canonical_lease_prefix(packet)
                    malformed = True
            else:
                frame = _recover_canonical_lease_prefix(packet)
            if malformed:
                if frame is None:
                    return bounded_error("frame_invalid")
                begun = self.coordinator.begin_lease(frame, dispatcher_peer=observed)
                token = begun.get("prepared_attempt")
                if begun.get("ok"):
                    failed = self.coordinator.reject_bound_descriptor(
                        frame, prepared_attempt=token,
                    )
                    acknowledgement = lease_acknowledgement(
                        frame["lease_id"], failed.get("outcome", "refused"),
                    )
                else:
                    acknowledgement = lease_acknowledgement(
                        frame["lease_id"], begun.get("outcome", "refused"),
                    )
                connection.sendall(json.dumps(
                    acknowledgement, sort_keys=True, separators=(",", ":"),
                ).encode("ascii"))
                return {"ok": False, **acknowledgement}
            begun = self.coordinator.begin_lease(frame, dispatcher_peer=observed)
            prepared_attempt = begun.get("prepared_attempt")
            if not begun.get("ok"):
                acknowledgement = lease_acknowledgement(frame["lease_id"], begun.get("outcome", "refused"))
                connection.sendall(json.dumps(acknowledgement, sort_keys=True, separators=(",", ":")).encode("ascii"))
                return {"ok": False, **acknowledgement}
            try:
                descriptor = _extract_one_descriptor(ancillary)
                transferred_descriptor = descriptor
            except Exception:
                failed = self.coordinator.reject_bound_descriptor(
                    frame, prepared_attempt=begun.get("prepared_attempt"),
                )
                acknowledgement = lease_acknowledgement(frame["lease_id"], failed.get("outcome", "refused"))
                connection.sendall(json.dumps(
                    acknowledgement, sort_keys=True, separators=(",", ":"),
                ).encode("ascii"))
                return {"ok": False, **acknowledgement}
            try:
                observation = self.descriptor_observer(descriptor)
            except Exception:
                failed = self.coordinator.reject_bound_descriptor(
                    frame, prepared_attempt=begun.get("prepared_attempt"),
                )
                acknowledgement = lease_acknowledgement(frame["lease_id"], failed.get("outcome", "refused"))
                connection.sendall(json.dumps(acknowledgement, sort_keys=True, separators=(",", ":")).encode("ascii"))
                return {"ok": False, **acknowledgement}
            owned_descriptor = descriptor
            descriptor = None  # Ownership transfers before coordinator acceptance.
            result = self.coordinator._accept_prepared_descriptor(
                frame, owned_descriptor, observation,
                prepared_attempt=begun.get("prepared_attempt"),
            )
            acknowledgement = lease_acknowledgement(
                frame["lease_id"], result.get("outcome", "indeterminate"),
            )
            connection.sendall(json.dumps(
                acknowledgement, sort_keys=True, separators=(",", ":"),
            ).encode("ascii"))
            return {"ok": result.get("ok") is True, **acknowledgement}
        except Exception:
            return bounded_error("lease_channel_unavailable")
        finally:
            self.coordinator._discard_prepared_attempt(prepared_attempt)
            if descriptor is not None:
                try: os.close(descriptor)
                except OSError: pass
            if ancillary is not None:
                _close_received_descriptors(
                    ancillary,
                    exclude=(() if transferred_descriptor is None else (transferred_descriptor,)),
                )
            if connection is not None:
                try: connection.close()
                except OSError: pass

    def close(self) -> dict[str, Any]:
        self._admission_open = False
        listener, self.listener = self.listener, None
        if listener is not None:
            try: listener.close()
            except OSError: pass
        return {"ok": True, "code": "lease_channel_closed", "admission_open": False}


class CredentialBrokerServiceReactor:
    """Bounded injected selector loop; construction performs no I/O."""

    def __init__(self, coordinator: CredentialBrokerCoordinator, *, selector=None,
                 controller_endpoint=None, lease_endpoint=None, guest_probe=None,
                 clock=None, max_events: int = 64) -> None:
        if not isinstance(coordinator, CredentialBrokerCoordinator) \
                or not _integer(max_events, minimum=1, maximum=256) \
                or (guest_probe is not None and not callable(guest_probe)):
            raise ValueError("credential broker reactor configuration is invalid")
        self.coordinator = coordinator
        self.selector = selector or selectors.DefaultSelector()
        self.controller = controller_endpoint
        self.lease = lease_endpoint
        self.guest_probe = guest_probe or (lambda _identity: "ready")
        self.clock = clock or time.time
        self.max_events = max_events
        self.closed = False

    def run_once(self, timeout: float = 0.0) -> dict[str, Any]:
        if self.closed or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 5:
            return bounded_error("live_transport_unproven")
        try:
            events = self.selector.select(timeout)
        except Exception:
            self.coordinator.quiesce()
            return bounded_error("live_transport_unproven")
        if len(events) > self.max_events:
            self.coordinator.quiesce()
            return bounded_error("request_limit")
        handled = 0
        for key, _mask in events:
            data = key.data
            if data == "controller" and self.controller is not None:
                self.controller.receive_once(); handled += 1
            elif data == "lease" and self.lease is not None:
                self.lease.receive_once(); handled += 1
            elif isinstance(data, tuple) and len(data) == 2 and data[0] == "guest":
                identity = data[1]
                state = self.guest_probe(identity)
                if state == "eof":
                    self.coordinator.guest_disconnected(identity)
                elif state == "trailing":
                    self.coordinator.guest_trailing(identity)
                handled += 1
        expired = self.coordinator.expire(int(self.clock()))
        return {"ok": True, "handled": handled, "expired": len(expired)}

    def shutdown(self) -> dict[str, Any]:
        if self.closed:
            return {"ok": True, "code": "reactor_closed"}
        self.coordinator.quiesce()
        if self.lease is not None: self.lease.close()
        if self.controller is not None: self.controller.close()
        self.coordinator.close()
        try: self.selector.close()
        except Exception: pass
        self.closed = True
        return {"ok": True, "code": "reactor_closed"}


class LinuxLeaseEndpoint:
    """Offline-only legacy descriptor endpoint probe.

    Construction is inert and admission is closed. Enabling requires both the
    explicit offline adapter and an injected socket factory, so this legacy
    registry can never become a real production endpoint.
    """

    __slots__ = (
        "_service", "_control_plane_uid", "_identity_observer", "_socket_factory",
        "_listener", "_enabled", "_admission_open", "_consumed", "_clock",
        "_terminal_closed", "_registry", "_adapter", "_descriptor_reader",
        "_consumed_lock",
    )

    def __init__(
        self,
        service: Any,
        *,
        control_plane_uid: int,
        identity_observer,
        registry: LegacyPendingLeaseRegistry | None = None,
        adapter: OfflineTestOperationAdapter | None = None,
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
        if enabled and (not isinstance(registry, LegacyPendingLeaseRegistry)
                        or registry.service != service
                        or not isinstance(adapter, OfflineTestOperationAdapter)
                        or socket_factory is None):
            raise ValueError("legacy lease endpoint requires offline fake seams")
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
        self._adapter = adapter
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
                    adapted = self._adapter.execute(
                        dict(pending["request"]), material,
                        machine_id=self._service["machine_id"],
                    )
                    if isinstance(adapted, dict):
                        outcome = adapted.get("outcome")
                    else:
                        try:
                            from sandbox.isolation.credential_request_broker import BrokerResponse
                            outcome = "completed" if isinstance(adapted, BrokerResponse) else None
                        except ImportError:
                            outcome = None
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
        **{field: service[field] for field in sorted(_SERVICE_FIELDS)},
    })


class BoundedLifecycleObserver:
    """Secret-free local state projection; it never opens or probes admission."""

    def __init__(self, service: Any, coordinator: CredentialBrokerCoordinator) -> None:
        if not _validate_service_identity(service) or coordinator.service != service:
            raise ValueError("credential lifecycle observer is invalid")
        self.service = dict(service)
        self.coordinator = coordinator

    def observe(self) -> dict[str, Any]:
        if self.coordinator._closed:
            state = "closed"
        elif self.coordinator._quiesced:
            state = "draining"
        elif self.coordinator.admission_open:
            state = "ready"
        else:
            state = "credential_pending"
        return service_status(
            self.service, state=state,
            admission_open=self.coordinator.admission_open,
        )


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


class _GuestV2Connection:
    """Exactly one canonical request and one terminal result send attempt."""

    __slots__ = ("raw", "identity", "request", "received_at_unix_ms",
                 "deadline_unix_ms", "_closed", "_result_attempted")

    def __init__(self, raw, identity: str, request: GuestRequestV2,
                 received_at_unix_ms: int) -> None:
        self.raw = raw
        self.identity = identity
        self.request = request
        self.received_at_unix_ms = received_at_unix_ms
        self.deadline_unix_ms = received_at_unix_ms + request.deadline_ms
        self._closed = False
        self._result_attempted = False

    def deliver(self, result: GuestResultV2) -> None:
        if (self._closed or self._result_attempted or type(result) is not GuestResultV2
                or result.correlation_id != self.request.correlation_id):
            raise ControllerServiceV2Error("guest_result_invalid")
        self._result_attempted = True
        try:
            self.raw.sendall(encode_guest_result_v2(result))
        except Exception:
            raise ControllerServiceV2Error("guest_disconnected") from None
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self.raw.close()
            self._closed = True


class LinuxGuestV2Listener:
    """Closed-first SBG2 listener over one sealed private-veth tuple."""

    __slots__ = ("plan", "projection", "_topology_observer", "_socket_factory",
                 "_clock", "_so_bindtodevice", "listener", "admission_open", "_closed",
                 "_active", "_terminal_code", "_capacity_lock", "_reservations")

    def __init__(self, plan: DerivedServiceConfigV2, *, topology_observer,
                 socket_factory=socket.socket, clock=None, so_bindtodevice=None) -> None:
        so_bindtodevice = getattr(socket, "SO_BINDTODEVICE", None) \
            if so_bindtodevice is None else so_bindtodevice
        if (not isinstance(plan, DerivedServiceConfigV2) or plan.component != "broker"
                or plan.document["guest_endpoint_identity"] != "credential-broker-guest-v2"
                or not callable(topology_observer) or not callable(socket_factory)
                or (clock is not None and not callable(clock))
                or type(so_bindtodevice) is not int or so_bindtodevice < 1):
            raise ControllerServiceV2Error("guest_listener_invalid")
        item = plan.document["guest_transport_projection"]
        self.plan = plan
        self.projection = GuestTransportProjectionV2(
            item["machine_id"], item["interface"], item["subnet"],
            item["broker_address"], item["guest_address"])
        self._topology_observer = topology_observer
        self._socket_factory = socket_factory
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._so_bindtodevice = so_bindtodevice
        self.listener = None
        self.admission_open = False
        self._closed = False
        self._active = {}
        self._capacity_lock = threading.Lock()
        self._reservations = set()
        self._terminal_code = None

    def _reserve(self) -> str:
        with self._capacity_lock:
            if (self._closed or self.listener is None or not self.admission_open
                    or len(self._reservations) >= MAX_ACTIVE_REQUESTS):
                raise ControllerServiceV2Error("admission_closed")
            identity = "guest-v2-" + uuid.uuid4().hex
            self._reservations.add(identity)
            return identity

    def release_reservation(self, identity: str) -> None:
        with self._capacity_lock:
            self._reservations.discard(identity)

    def start(self, *, platform: str, effective_uid: int) -> dict[str, Any]:
        if (self._closed or self.listener is not None or platform != "linux"
                or type(effective_uid) is not int or effective_uid < 1
                or effective_uid != self.plan.document["service_uid"]):
            raise ControllerServiceV2Error("guest_listener_start_refused")
        listener = None
        try:
            listener = self._socket_factory(socket.AF_INET, socket.SOCK_STREAM, 0)
            interface = self.projection.interface.encode("ascii") + b"\0"
            listener.setsockopt(socket.SOL_SOCKET, self._so_bindtodevice, interface)
            readback = listener.getsockopt(
                socket.SOL_SOCKET, self._so_bindtodevice, len(interface) + 16)
            if (type(readback) is not bytes
                    or readback.rstrip(b"\0") != interface.rstrip(b"\0")):
                raise ControllerServiceV2Error("guest_listener_start_refused")
            listener.bind((self.projection.broker_address, self.projection.port))
            listener.listen(MAX_ACTIVE_REQUESTS)
        except Exception:
            cleanup_failed = False
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    cleanup_failed = True
            raise ControllerServiceV2Error(
                "guest_listener_cleanup_failed" if cleanup_failed
                else "guest_listener_start_refused") from None
        self.listener = listener
        return {"ok": True, "code": "guest_listener_started",
                "admission_open": False}

    def set_admission(self, value: bool) -> None:
        with self._capacity_lock:
            if type(value) is not bool or self._closed or self.listener is None:
                raise ControllerServiceV2Error("guest_listener_closed")
            self.admission_open = value

    @staticmethod
    def _receive_packet(connection) -> bytes:
        connection.settimeout(GUEST_V2_INACTIVITY_SECONDS)
        header = _recv_exact(connection, 9)
        try:
            magic, version, size = struct.unpack("!4sBI", header)
        except Exception:
            raise ControllerServiceV2Error("request_invalid") from None
        if (magic != b"SBG2" or version != 2
                or size > MAX_GUEST_FRAME_BYTES - 9):
            raise ControllerServiceV2Error("request_invalid")
        packet = header + _recv_exact(connection, size)
        try:
            trailing = connection.recv(
                1, getattr(socket, "MSG_PEEK", 0) | getattr(socket, "MSG_DONTWAIT", 0))
        except (BlockingIOError, InterruptedError):
            trailing = b""
        if trailing:
            raise ControllerServiceV2Error("request_invalid")
        return packet

    @staticmethod
    def receive_packet_bounded(connection, *, accepted_at: float, monotonic) -> bytes:
        """Read one frame with progress inactivity and one absolute bound."""

        if (not callable(monotonic) or isinstance(accepted_at, bool)
                or not isinstance(accepted_at, (int, float))):
            raise ControllerServiceV2Error("clock_uncertain")
        absolute = accepted_at + (GUEST_V2_MAX_OPERATION_MS / 1000.0)
        idle = accepted_at + GUEST_V2_INACTIVITY_SECONDS
        data = bytearray()
        target = 9
        while len(data) < target:
            try:
                current = monotonic()
                if (isinstance(current, bool) or not isinstance(current, (int, float))
                        or current < accepted_at or current >= min(idle, absolute)):
                    raise ControllerServiceV2Error("deadline_exceeded")
                connection.settimeout(min(idle, absolute) - current)
                chunk = connection.recv(target - len(data))
            except ControllerServiceV2Error:
                raise
            except (TimeoutError, socket.timeout):
                raise ControllerServiceV2Error("deadline_exceeded") from None
            except Exception:
                raise ControllerServiceV2Error("request_invalid") from None
            if type(chunk) is not bytes or not chunk:
                raise ControllerServiceV2Error("request_invalid")
            data.extend(chunk)
            try:
                progressed = monotonic()
            except Exception:
                raise ControllerServiceV2Error("clock_uncertain") from None
            if (isinstance(progressed, bool) or not isinstance(progressed, (int, float))
                    or progressed < current or progressed >= absolute):
                raise ControllerServiceV2Error("deadline_exceeded")
            idle = min(absolute, progressed + GUEST_V2_INACTIVITY_SECONDS)
            if len(data) == 9:
                try:
                    magic, version, size = struct.unpack("!4sBI", data)
                except Exception:
                    raise ControllerServiceV2Error("request_invalid") from None
                if magic != b"SBG2" or version != 2 or size > MAX_GUEST_FRAME_BYTES - 9:
                    raise ControllerServiceV2Error("request_invalid")
                target = 9 + size
        return bytes(data)

    def accept_transport_once(self):
        """Accept and prove transport only; a service-owned worker reads bytes."""

        identity = self._reserve()
        connection = None
        try:
            connection, peer = self.listener.accept()
            observed = self._topology_observer(connection, self.projection, peer)
            if not verify_guest_transport_v2(self.projection, observed):
                raise ControllerServiceV2Error("guest_transport_denied")
            received = self._clock()
            if type(received) is not int:
                raise ControllerServiceV2Error("clock_uncertain")
            return connection, identity, received
        except Exception as exc:
            self.release_reservation(identity)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    self._terminal_code = self._terminal_code or "guest_socket_cleanup_failed"
            code = exc.code if isinstance(exc, ControllerServiceV2Error) else "request_invalid"
            raise ControllerServiceV2Error(self._terminal_code or code) from None

    def retain_request(self, connection, identity: str, received: int,
                       request: GuestRequestV2) -> _GuestV2Connection:
        with self._capacity_lock:
            if (type(request) is not GuestRequestV2
                    or request.machine_id != self.plan.machine_id
                    or identity not in self._reservations
                    or identity in self._active):
                raise ControllerServiceV2Error("request_invalid")
            owned = _GuestV2Connection(connection, identity, request, received)
            self._active[identity] = owned
        return owned

    def accept_once(self) -> _GuestV2Connection:
        identity = self._reserve()
        connection = None
        try:
            connection, peer = self.listener.accept()
            observed = self._topology_observer(connection, self.projection, peer)
            if not verify_guest_transport_v2(self.projection, observed):
                raise ControllerServiceV2Error("guest_transport_denied")
            received = self._clock()
            if type(received) is not int:
                raise ControllerServiceV2Error("clock_uncertain")
            request = decode_guest_request_v2(self._receive_packet(connection))
            if request.machine_id != self.plan.machine_id:
                raise ControllerServiceV2Error("request_invalid")
            owned = _GuestV2Connection(connection, identity, request, received)
            with self._capacity_lock:
                if identity not in self._reservations or identity in self._active:
                    raise ControllerServiceV2Error("request_invalid")
                self._active[identity] = owned
            return owned
        except Exception as exc:
            self.release_reservation(identity)
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    if self._terminal_code is None:
                        self._terminal_code = "guest_socket_cleanup_failed"
            code = exc.code if isinstance(exc, ControllerServiceV2Error) else "request_invalid"
            raise ControllerServiceV2Error(self._terminal_code or code) from None

    def release(self, identity: str) -> bool:
        with self._capacity_lock:
            connection = self._active.get(identity)
        if connection is not None:
            connection.close()
            with self._capacity_lock:
                if self._active.get(identity) is connection:
                    self._active.pop(identity, None)
                    self._reservations.discard(identity)
            return True
        with self._capacity_lock:
            self._reservations.discard(identity)
        return False

    def expire(self, now_ms: int) -> int:
        with self._capacity_lock:
            selected = [identity for identity, connection in self._active.items()
                        if connection.deadline_unix_ms <= now_ms]
        for identity in selected:
            self.release(identity)
        return len(selected)

    def close(self) -> dict[str, Any]:
        if not self._closed:
            with self._capacity_lock:
                self._closed = True
                self.admission_open = False
                active = tuple(self._active)
            for identity in active:
                try:
                    self.release(identity)
                except Exception:
                    self._terminal_code = self._terminal_code or "guest_socket_cleanup_failed"
            listener, self.listener = self.listener, None
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    self._terminal_code = self._terminal_code or "guest_listener_cleanup_failed"
        return {"ok": self._terminal_code is None,
                "code": self._terminal_code or "guest_listener_closed",
                "admission_open": False}


class CredentialBrokerServiceLoopV2:
    """Own controller, lease, guest and effect work for one broker epoch."""

    __slots__ = ("plan", "controller", "guest", "upstream", "dns_authority", "executor",
                 "_clock", "_now_text", "_lease_listeners", "_guests",
                 "_guest_operations", "_workers", "_closed", "_terminal_code", "_observer",
                 "_descriptor_observer", "_descriptor_closer", "_closer",
                 "_monotonic", "_audit_id_factory", "_so_peercred",
                 "_so_passcred", "_scm_credentials", "_scm_rights", "_selector_factory",
                 "_lease_socket_factory", "_reader_workers", "_pending_guest_sockets",
                 "_worker_lock")

    def __init__(self, plan: DerivedServiceConfigV2,
                 controller: LinuxControllerV2Listener,
                 guest: LinuxGuestV2Listener, upstream: VerifiedHttpsUpstream,
                 dns_authority: AuthorizedDnsResolverV2,
                 executor: EffectExecutionV2, *, clock, now_text, observer,
                 descriptor_observer, descriptor_closer, closer,
                 audit_id_factory, monotonic,
                 so_peercred: int, so_passcred: int, scm_credentials: int, scm_rights: int,
                 selector_factory=selectors.DefaultSelector,
                 lease_socket_factory=socket.socket) -> None:
        document = plan.document if isinstance(plan, DerivedServiceConfigV2) else {}
        configured = controller.config if isinstance(controller, LinuxControllerV2Listener) else None
        composition_valid = bool(
            configured is not None
            and all(document.get(name) == getattr(configured, name, None) for name in (
                "policy_digest", "egress_digest", "broker_digest", "proof_digest",
                "effective_isolation_digest", "evidence_id"))
            and configured.broker.uid == document.get("service_uid")
            and configured.broker.gid == document.get("service_gid")
            and configured.broker.executable_digest == document.get("executable_digest")
            and configured.broker.unit_digest == document.get("unit_digest")
            and configured.broker.config_digest == document.get("own_config_digest")
            and configured.controller.executable_digest == document.get("peer_executable_digest")
            and configured.controller.unit_digest == document.get("peer_unit_digest")
            and configured.controller.config_digest == document.get("peer_config_digest")
            and document.get("guest_protocol_registry_digest") == guest_protocol_registry_digest_v2()
            and document.get("guest_transport_projection") == dict(
                plan.document["guest_transport_projection"])
        )
        if (not isinstance(plan, DerivedServiceConfigV2) or plan.component != "broker"
                or not isinstance(controller, LinuxControllerV2Listener)
                or not isinstance(guest, LinuxGuestV2Listener) or guest.plan != plan
                or controller.config.machine_id != plan.machine_id
                or not composition_valid
                or not isinstance(upstream, VerifiedHttpsUpstream)
                or type(dns_authority) is not AuthorizedDnsResolverV2
                or not isinstance(executor, EffectExecutionV2)
                or any(not callable(value) for value in (
                    clock, now_text, observer, descriptor_observer,
                    descriptor_closer, closer, audit_id_factory, monotonic,
                    selector_factory, lease_socket_factory))
                or any(type(value) is not int or value < 1 for value in (
                    so_peercred, so_passcred, scm_credentials, scm_rights))):
            raise ControllerServiceV2Error("service_loop_invalid")
        self.plan, self.controller, self.guest = plan, controller, guest
        self.upstream, self.dns_authority, self.executor = (
            upstream, dns_authority, executor)
        self._clock, self._now_text = clock, now_text
        self._observer, self._descriptor_observer = observer, descriptor_observer
        self._descriptor_closer, self._closer = descriptor_closer, closer
        self._audit_id_factory, self._monotonic = audit_id_factory, monotonic
        self._so_peercred, self._scm_credentials = so_peercred, scm_credentials
        self._so_passcred = so_passcred
        self._scm_rights, self._selector_factory = scm_rights, selector_factory
        self._lease_socket_factory = lease_socket_factory
        self._lease_listeners = {}
        self._guests = {}
        self._guest_operations = {}
        self._workers = {}
        self._reader_workers = {}
        self._pending_guest_sockets = {}
        self._worker_lock = threading.Lock()
        self._closed = False
        self._terminal_code = None
        if self.controller._lease_endpoint_factory is not None:
            raise ControllerServiceV2Error("service_loop_invalid")
        self.controller._lease_endpoint_factory = self._lease_listener_factory

    def _lease_listener_factory(self, address, endpoint):
        listener = LinuxLeaseOperationV2Listener(
            address, endpoint, observer=self._observer, now_ms=self._clock,
            descriptor_observer=self._descriptor_observer,
            descriptor_closer=self._descriptor_closer,
            so_peercred=self._so_peercred,
            so_passcred=self._so_passcred,
            scm_credentials=self._scm_credentials,
            scm_rights=self._scm_rights,
            socket_factory=self._lease_socket_factory,
        )
        self._lease_listeners[endpoint.operation_id] = listener
        return listener

    def accept_guest_once(self) -> dict[str, Any]:
        session = self.controller.session
        if (self._closed or session is None or not session.admission_open):
            raise ControllerServiceV2Error("admission_closed")
        owned = self.guest.accept_once()
        try:
            admitted = session.operations.submit_typed_v2(
                owned.request, connection_identity=owned.identity,
                now_ms=owned.received_at_unix_ms)
            self._guests[owned.identity] = owned
            self._guest_operations[owned.identity] = admitted["operation_id"]
            return admitted
        except Exception:
            self.guest.release(owned.identity)
            raise

    def _read_guest_request(self, identity: str, connection, received: int,
                            accepted_at: float) -> None:
        try:
            packet = self.guest.receive_packet_bounded(
                connection, accepted_at=accepted_at, monotonic=self._monotonic)
            request = decode_guest_request_v2(packet)
            owned = self.guest.retain_request(connection, identity, received, request)
            session = self.controller.session
            if self._closed or session is None or not session.admission_open:
                raise ControllerServiceV2Error("admission_closed")
            admitted = session.operations.submit_typed_v2(
                owned.request, connection_identity=owned.identity,
                now_ms=owned.received_at_unix_ms)
            with self._worker_lock:
                if self._closed:
                    raise ControllerServiceV2Error("service_loop_closed")
                self._pending_guest_sockets.pop(identity, None)
                self._guests[identity] = owned
                self._guest_operations[identity] = admitted["operation_id"]
        except Exception:
            self._close_pending_guest_v2(identity, connection)
        finally:
            with self._worker_lock:
                self._reader_workers.pop(identity, None)

    def _close_pending_guest_v2(self, identity: str, connection) -> bool:
        with self._worker_lock:
            if self._pending_guest_sockets.get(identity) is not connection:
                return True
        try:
            with self.guest._capacity_lock:
                retained = self.guest._active.get(identity)
            if retained is None:
                connection.close()
            else:
                self.guest.release(identity)
        except Exception:
            self._terminal_code = self._terminal_code or "guest_socket_cleanup_failed"
            return False
        with self._worker_lock:
            if self._pending_guest_sockets.get(identity) is connection:
                self._pending_guest_sockets.pop(identity, None)
        self.guest.release_reservation(identity)
        return True

    def accept_guest_async(self) -> dict[str, Any]:
        if self._closed:
            raise ControllerServiceV2Error("service_loop_closed")
        connection, identity, received = self.guest.accept_transport_once()
        with self._worker_lock:
            self._pending_guest_sockets[identity] = connection
        try:
            accepted_at = self._monotonic()
            if (isinstance(accepted_at, bool)
                    or not isinstance(accepted_at, (int, float))):
                raise ControllerServiceV2Error("clock_uncertain")
            worker = threading.Thread(
                target=self._read_guest_request,
                args=(identity, connection, received, accepted_at),
                name="credential-guest-reader-v2")
            with self._worker_lock:
                if self._closed:
                    raise ControllerServiceV2Error("service_loop_closed")
                self._reader_workers[identity] = worker
            worker.start()
        except Exception as exc:
            with self._worker_lock:
                self._reader_workers.pop(identity, None)
            cleaned = self._close_pending_guest_v2(identity, connection)
            code = (exc.code if isinstance(exc, ControllerServiceV2Error)
                    else "guest_reader_unavailable")
            if not cleaned:
                code = self._terminal_code or "guest_socket_cleanup_failed"
                self.close()
            raise ControllerServiceV2Error(code) from None
        return {"ok": True, "code": "guest_read_pending"}

    def guest_disconnected(self, identity: str) -> None:
        owned = self._guests.pop(identity, None)
        operation_id = self._guest_operations.pop(identity, None)
        if owned is None or operation_id is None:
            return
        self.guest.release(identity)
        session = self.controller.session
        if session is None:
            return
        state = session.operations.state(operation_id)
        if state in {"pre_audited", "effect_possible", "post_audited"}:
            session._indeterminate_disconnect_v2(operation_id, "guest_disconnected")
        else:
            session.operations.terminalize_known(operation_id, "guest_disconnected")

    def process_controller_once(self, *, now_ms: int) -> dict[str, Any]:
        session = self.controller.session
        if self._closed or session is None:
            raise ControllerServiceV2Error("controller_connection_refused")
        temporal_context = None
        if session._claim_anchor is not None:
            request_deadline, _version = session.operations.authorization_deadlines(
                session._claim_anchor["operation_id"])
            temporal_context = {
                "activation_expires_at_unix_ms": session._activation_expires_at,
                "request_deadline_unix_ms": request_deadline,
            }
        message = session.receive_frame(
            observer=self._observer, now_ms=now_ms,
            so_peercred=self._so_peercred,
            scm_credentials=self._scm_credentials, scm_rights=self._scm_rights,
            closer=self._closer, temporal_context=temporal_context,
        )
        kind = message.get("type")
        if kind == "AUDIT_ACK_V2":
            session.route_audit_ack_v2(message)
            result = {"ok": True, "code": "audit_ack_routed"}
        elif kind in {"ACTIVATE_V2", "QUIESCE_V2"}:
            result = session.handle_lifecycle_v2(message, now_ms=now_ms)
            self.guest.set_admission(bool(session.admission_open))
        elif kind in {"CLAIM_NEXT_V2", "AUTHORIZE_V2", "REFUSE_V2"}:
            result = session.handle_authority_v2(message, now_ms=now_ms)
        else:
            session.close("controller_frame_refused")
            raise ControllerServiceV2Error("controller_frame_refused")
        self.synchronize()
        return result

    def _execute_operation(self, operation_id: str) -> None:
        session = self.controller.session
        try:
            raw = session.operations.effect_context(
                operation_id, now_ms=self._clock())
            request = raw["request"]
            common = dict(
                audit_id_factory=self._audit_id_factory,
                monotonic=self._monotonic, wall_clock=self._clock)
            try:
                request_deadline, _binding_version = (
                    session.operations.authorization_deadlines(operation_id))
                decision = resolve_authorized_guest_egress_v2(
                    self.plan, request, self.upstream, self.dns_authority,
                    now=self._now_text(),
                    deadline_unix_ms=request_deadline,
                    wall_clock_ms=self._clock)
            except ControllerServiceV2Error as exc:
                if exc.code == "dns_cleanup_incomplete":
                    raise
                result = session.refuse_effect_v2(
                    operation_id, reason_code="egress_denied", **common)
            else:
                result = session.execute_effect_v2(
                    operation_id, egress_decision=decision, executor=self.executor,
                    **common)
            identity = session.operations.connection_identity(operation_id)
            owned = self._guests.get(identity)
            if owned is None:
                raise ControllerServiceV2Error("guest_disconnected")
            owned.deliver(result["guest_result"])
            self._guests.pop(identity, None)
            self._guest_operations.pop(identity, None)
            self.guest.release(identity)
            session.guest_result_v2(identity, consume=True)
        except Exception as exc:
            code = exc.code if isinstance(exc, ControllerServiceV2Error) else "internal_indeterminate"
            try:
                session.operations.terminalize_known(operation_id, code)
            except Exception:
                pass
            self._terminal_code = self._terminal_code or (
                code if code in {"audit_unavailable", "effect_indeterminate",
                                 "lease_ack_failed", "guest_disconnected",
                                 "dns_cleanup_incomplete"}
                else None)
            if code == "dns_cleanup_incomplete":
                try:
                    self.guest.set_admission(False)
                except ControllerServiceV2Error:
                    pass
                try:
                    self.close()
                except Exception:
                    pass
        finally:
            self._workers.pop(operation_id, None)
            self._lease_listeners.pop(operation_id, None)
            try:
                self.synchronize()
            except Exception:
                self._terminal_code = self._terminal_code or "guest_result_delivery_failed"

    def process_lease_once(self, operation_id: str) -> dict[str, Any]:
        if self._closed or operation_id in self._workers:
            raise ControllerServiceV2Error("lease_endpoint_consumed")
        listener = self._lease_listeners.get(operation_id)
        if listener is None:
            raise ControllerServiceV2Error("lease_endpoint_invalid")
        result = listener.receive_once()
        worker = threading.Thread(
            target=self._execute_operation, args=(operation_id,),
            name="credential-effect-v2")
        self._workers[operation_id] = worker
        worker.start()
        return result

    def run_once(self, *, timeout: float = 1.0) -> dict[str, Any]:
        """Select one bounded event; all readiness remains closed on uncertainty."""

        if (self._closed or isinstance(timeout, bool)
                or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 1):
            raise ControllerServiceV2Error("service_loop_closed")
        selector = self._selector_factory()
        try:
            if self.controller.session is None:
                if self.controller.listener is not None:
                    selector.register(self.controller.listener, selectors.EVENT_READ,
                                      ("controller_accept", None))
            else:
                selector.register(self.controller.session.connection, selectors.EVENT_READ,
                                  ("controller", None))
            if self.guest.listener is not None and self.guest.admission_open:
                selector.register(self.guest.listener, selectors.EVENT_READ, ("guest", None))
            for identity, owned in tuple(self._guests.items()):
                selector.register(owned.raw, selectors.EVENT_READ,
                                  ("guest_disconnect", identity))
            for operation_id, listener in tuple(self._lease_listeners.items()):
                if listener.listener is not None:
                    selector.register(listener.listener, selectors.EVENT_READ,
                                      ("lease", operation_id))
            events = selector.select(timeout)
            for key, _mask in events[:1]:
                kind, operation_id = key.data
                now_ms = self._clock()
                if kind == "controller":
                    self.process_controller_once(now_ms=now_ms)
                elif kind == "guest":
                    self.accept_guest_async()
                elif kind == "lease":
                    self.process_lease_once(operation_id)
                elif kind == "guest_disconnect":
                    self.guest_disconnected(operation_id)
                else:
                    self.controller.accept_once(
                        observer=self._observer, now_ms=now_ms,
                        monotonic=self._monotonic,
                        so_peercred=self._so_peercred,
                        scm_credentials=self._scm_credentials,
                        scm_rights=self._scm_rights, closer=self._closer,
                    )
            return self.tick(self._clock())
        except Exception as exc:
            code = exc.code if isinstance(exc, ControllerServiceV2Error) \
                else "service_loop_failed"
            self._terminal_code = self._terminal_code or code
            self.close()
            raise ControllerServiceV2Error(self._terminal_code) from None
        finally:
            try:
                selector.close()
            except Exception:
                self._terminal_code = self._terminal_code or "selector_cleanup_failed"
                self.close()
                raise ControllerServiceV2Error(self._terminal_code) from None

    def run_forever(self) -> dict[str, Any]:
        while not self._closed:
            self.run_once(timeout=1.0)
        return self.close()

    def synchronize(self) -> int:
        """Deliver each terminal registry result once; pending guests remain owned."""

        session = self.controller.session
        if session is None:
            return 0
        delivered = 0
        for identity, owned in tuple(self._guests.items()):
            try:
                value = session.guest_result_v2(identity)
            except Exception:
                continue
            if value["state"] == "credential_pending":
                continue
            try:
                result = GuestResultV2.failure(
                    state="indeterminate" if value["state"] == "indeterminate" else "refused",
                    code=value["code"], retryable=False,
                    correlation_id=value["correlation_id"],
                ) if not value["ok"] else None
                if result is not None:
                    owned.deliver(result)
                self._guests.pop(identity, None)
                self._guest_operations.pop(identity, None)
                self.guest.release(identity)
                session.guest_result_v2(identity, consume=True)
                delivered += 1
            except Exception:
                self._terminal_code = self._terminal_code or "guest_result_delivery_failed"
                self.guest.release(identity)
                self._guests.pop(identity, None)
                self._guest_operations.pop(identity, None)
        return delivered

    def tick(self, now_ms: int) -> dict[str, Any]:
        if self._closed or type(now_ms) is not int:
            raise ControllerServiceV2Error("service_loop_closed")
        session = self.controller.session
        if session is None:
            self.guest.set_admission(False)
            return {"ok": True, "code": "controller_pending", "admission_open": False}
        self.guest.set_admission(bool(session.admission_open))
        for operation_id, listener in tuple(self._lease_listeners.items()):
            if listener._closed:
                self._lease_listeners.pop(operation_id, None)
        try:
            session.operations.expire(now_ms)
            self.synchronize()
            self.guest.expire(now_ms)
        except ControllerServiceV2Error as exc:
            self._terminal_code = self._terminal_code or exc.code
            self.close()
            raise ControllerServiceV2Error(self._terminal_code) from None
        return {"ok": True, "code": "service_loop_ready",
                "admission_open": self.guest.admission_open}

    def close(self) -> dict[str, Any]:
        effect_session = self.controller.session
        first_close = not self._closed
        if first_close:
            self._closed = True
        with self._worker_lock:
            pending = tuple(self._pending_guest_sockets.items())
            readers = tuple(self._reader_workers.values())
        for identity, connection in pending:
            self._close_pending_guest_v2(identity, connection)
        if first_close:
            guest_result = self.guest.close()
            self._guests.clear()
            self._guest_operations.clear()
            controller_result = self.controller.close()
            dns_result = self.dns_authority.close()
            for result in (guest_result, controller_result, dns_result):
                if not result["ok"] and self._terminal_code is None:
                    self._terminal_code = result["code"]
            current = threading.current_thread()
            for worker in readers:
                if worker is current:
                    continue
                worker.join(GUEST_V2_INACTIVITY_SECONDS + 1.0)
                if worker.is_alive():
                    self._terminal_code = self._terminal_code or "worker_cleanup_failed"
            with self._worker_lock:
                self._reader_workers = {
                    key: worker for key, worker in self._reader_workers.items()
                    if worker.is_alive()}
        with self._worker_lock:
            if self._pending_guest_sockets:
                self._terminal_code = self._terminal_code or (
                    "guest_socket_cleanup_failed")
        current = threading.current_thread()
        with self._worker_lock:
            effects = tuple(self._workers.items())
        session = effect_session
        for operation_id, worker in effects:
            if worker is current:
                continue
            try:
                request_deadline, _version = (
                    session.operations.authorization_deadlines(operation_id))
                terminal_deadline = lease_ack_deadline_v2(request_deadline)
                observed_now = self._clock()
                if type(observed_now) is not int:
                    raise ValueError
                remaining = max(0.0, (terminal_deadline - observed_now) / 1000.0)
            except Exception:
                remaining = 0.0
            worker.join(remaining)
            if worker.is_alive():
                self._terminal_code = self._terminal_code or "cleanup_incomplete"
            else:
                with self._worker_lock:
                    if self._workers.get(operation_id) is worker:
                        self._workers.pop(operation_id, None)
        return {"ok": self._terminal_code is None,
                "code": self._terminal_code or "service_loop_closed",
                "admission_open": False}


def prepare_standalone_authority_v2(machine_id: str, *, service_gid: int,
                                    plan_loader=load_runtime_config_v2,
                                    identity_pinner=pin_reciprocal_process_identities_v2):
    """Load both plans, validate reciprocity, then pin both runtime processes."""

    if (not _machine(machine_id) or not _integer(service_gid, minimum=1)
            or not callable(plan_loader) or not callable(identity_pinner)):
        raise ControllerServiceV2Error("runtime_config_invalid")
    try:
        controller_plan = plan_loader(
            runtime_config_path_v2(machine_id, "controller"),
            machine_id=machine_id, component="controller",
            expected_group_gid=service_gid)
        broker_plan = plan_loader(
            runtime_config_path_v2(machine_id, "broker"),
            machine_id=machine_id, component="broker",
            expected_group_gid=service_gid)
        validate_reciprocal_service_plans_v2(controller_plan, broker_plan)
        controller_identity, broker_identity = identity_pinner(
            controller_plan, broker_plan)
        config = ControllerServiceConfigV2(
            machine_id=machine_id, controller=controller_identity,
            broker=broker_identity,
            policy_digest=broker_plan.document["policy_digest"],
            egress_digest=broker_plan.document["egress_digest"],
            broker_digest=broker_plan.document["broker_digest"],
            proof_digest=broker_plan.document["proof_digest"],
            effective_isolation_digest=broker_plan.document[
                "effective_isolation_digest"],
            evidence_id=broker_plan.document["evidence_id"])
    except Exception:
        raise ControllerServiceV2Error("runtime_config_invalid") from None
    return MappingProxyType({
        "controller_plan": controller_plan, "broker_plan": broker_plan,
        "config": config,
        "controller_observer": pinned_process_identity_observer_v2(
            controller_identity),
        "broker_observer": pinned_process_identity_observer_v2(broker_identity),
    })


def compose_standalone_service_v2(prepared) -> dict[str, Any]:
    """Construct and run the fixed closed-first standalone graph."""

    if (type(prepared) is not MappingProxyType
            or set(prepared) != {"controller_plan", "broker_plan", "config",
                                 "controller_observer", "broker_observer"}
            or not isinstance(prepared["controller_plan"], DerivedServiceConfigV2)
            or not isinstance(prepared["broker_plan"], DerivedServiceConfigV2)
            or type(prepared["config"]) is not ControllerServiceConfigV2
            or not callable(prepared["controller_observer"])
            or not callable(prepared["broker_observer"])):
        raise ControllerServiceV2Error("service_composition_invalid")
    validate_reciprocal_service_plans_v2(
        prepared["controller_plan"], prepared["broker_plan"])
    config = prepared["config"]
    if (not sys.platform.startswith("linux") or _running_as_root()
            or os.geteuid() != config.broker.uid
            or os.getegid() != config.broker.gid
            or not all(type(getattr(socket, name, None)) is int for name in (
                "SO_PEERCRED", "SO_PASSCRED", "SCM_CREDENTIALS",
                "SCM_RIGHTS", "SO_BINDTODEVICE"))
            or not hasattr(socket, "SOCK_SEQPACKET")):
        raise ControllerServiceV2Error("live_transport_unproven")
    controller = guest = loop = dns = None
    terminal_error = None
    run_result = None
    try:
        topology = LinuxKernelTopologyObserverV2()
        dns = AuthorizedDnsResolverV2()
        upstream = VerifiedHttpsUpstream(
            resolver=lambda _host: (_ for _ in ()).throw(
                ControllerServiceV2Error("egress_denied")))
        destination_authority = ExactNftDestinationSetAuthorityV2(
            LinuxNftDestinationSetObserverV2())
        executor = PinnedHTTPSCredentialEffectV2(
            upstream, destination_authority=destination_authority)
        controller = LinuxControllerV2Listener(
            config, epoch_factory=lambda: uuid.uuid4().hex,
            owner_factory=lambda: "broker-owner-" + uuid.uuid4().hex[:16])
        guest = LinuxGuestV2Listener(
            prepared["broker_plan"], topology_observer=topology)

        def self_observer():
            prepared["broker_observer"](os.getpid(), os.geteuid(), os.getegid())
            return config

        controller.start(
            platform="linux", enabled=True, effective_uid=os.geteuid(),
            self_observer=self_observer)
        guest.start(platform="linux", effective_uid=os.geteuid())
        loop = CredentialBrokerServiceLoopV2(
            prepared["broker_plan"], controller, guest, upstream, dns, executor,
            clock=lambda: int(time.time() * 1000),
            now_text=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            observer=prepared["controller_observer"],
            descriptor_observer=_linux_descriptor_observation,
            descriptor_closer=os.close, closer=os.close,
            audit_id_factory=lambda kind: f"audit-{kind}{uuid.uuid4().hex[:16]}",
            monotonic=time.monotonic, so_peercred=socket.SO_PEERCRED,
            so_passcred=socket.SO_PASSCRED,
            scm_credentials=socket.SCM_CREDENTIALS,
            scm_rights=socket.SCM_RIGHTS)
        if (controller.admission_open or guest.admission_open
                or controller.session is not None):
            raise ControllerServiceV2Error("admission_closed")
        run_result = loop.run_forever()
    except Exception as exc:
        terminal_error = (exc.code if isinstance(exc, ControllerServiceV2Error)
                          else "live_transport_unproven")
    finally:
        for owned in (loop, guest, controller, dns):
            if owned is None:
                continue
            try:
                close_result = owned.close()
                if (type(close_result) is dict
                        and close_result.get("ok") is False
                        and terminal_error is None):
                    terminal_error = "live_transport_unproven"
            except Exception:
                if terminal_error is None:
                    terminal_error = "live_transport_unproven"
    if terminal_error is not None:
        raise ControllerServiceV2Error(terminal_error) from None
    if type(run_result) is not dict:
        raise ControllerServiceV2Error("live_transport_unproven")
    return run_result


def main(_argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if _argv is None else _argv)
    result = bounded_error("runtime_config_invalid")
    if (len(argv) == 4
            and argv[0:2] == ["--protocol", CONTROLLER_PROTOCOL_V2]
            and argv[2] == "--machine-id" and _machine(argv[3])
            and sys.platform.startswith("linux") and not _running_as_root()):
        try:
            prepared = prepare_standalone_authority_v2(
                argv[3], service_gid=os.getegid())
            plan = prepared["broker_plan"]
            if (os.geteuid() != plan.document["service_uid"]
                    or os.getegid() != plan.document["service_gid"]):
                raise ControllerServiceV2Error("runtime_config_invalid")
        except Exception:
            result = bounded_error("runtime_config_invalid")
        else:
            try:
                compose_standalone_service_v2(prepared)
            except Exception:
                result = bounded_error("live_transport_unproven")
            else:
                result = bounded_error("live_transport_unproven")
    print(json.dumps(result, sort_keys=True))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
