"""Inert persistent-controller transport service for Credential Broker v2.

This module owns process/transport authentication and connection lifetime only,
including opaque one-use authenticated socket receipts. It has no binding,
source, proof, egress, lease semantics, audit, helper,
application-composition, or runtime-enablement dependency.  Construction and
import perform no I/O; callers must inject and explicitly start every boundary.

The security boundary is authenticated cross-process transport and
kernel-observed identity. The Python process is trusted; arbitrary in-process
reflection, monkeypatching, closure inspection, or low-level mutation is
process compromise, not an unforgeability property of Python receipt objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from array import array
import hashlib
import json
import re
import socket
import struct
from types import MappingProxyType
from typing import Any, Callable, Mapping

from sandbox.isolation.credential_controller_protocol_v2 import (
    DirectionalSequence,
    MAX_SEQUENCE,
    PROTOCOL,
    ProtocolV2Error,
    TemporalObservation,
    decode_controller_frame,
    digest_document,
    encode_controller_frame,
)


HANDSHAKE_TIMEOUT_SECONDS = 1.0
MAX_CONTROLLER_FRAME_BYTES = 16 * 1024
LEASE_ENDPOINT_IDENTITY_V2 = "v2-lease.sock"
LEASE_ENDPOINT_PURPOSE_V2 = "lease_delivery"
LEASE_ENDPOINT_PREFIX_V2 = b"\0sandbox-credential-lease-v2-"
LEASE_ENDPOINT_ADDRESS_BYTES_V2 = 93
_CREDENTIALS = struct.Struct("3i")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_MACHINE = re.compile(r"^[a-z0-9][a-z0-9-]{6,61}[a-z0-9]$")
_OPERATION = re.compile(r"^operation-[a-z0-9]{6,53}$")

_LEASE_ADDRESS_KEYS = (
    "protocol", "purpose", "endpoint_identity", "machine_id", "broker_epoch",
    "controller_epoch", "broker_digest", "broker_config_digest",
    "controller_config_digest", "operation_id", "authorization_digest",
)
LEASE_ENDPOINT_V2_REGISTRY = MappingProxyType({
    "protocol": PROTOCOL,
    "purpose": LEASE_ENDPOINT_PURPOSE_V2,
    "endpoint_identity": LEASE_ENDPOINT_IDENTITY_V2,
    "family": "AF_UNIX",
    "socket_type": "SOCK_SEQPACKET",
    "namespace": "linux_abstract",
    "address_prefix": "sandbox-credential-lease-v2-",
    "address_bytes": LEASE_ENDPOINT_ADDRESS_BYTES_V2,
    "digest_domain_ascii": "credential-broker-lease-address-v2",
    "digest_separator_byte": 0,
    "digest_fields": _LEASE_ADDRESS_KEYS,
    "connect_timeout_ms": 1000,
    "audit_ack_timeout_ms": 1000,
    "lease_ack_timeout_ms": 1000,
    "lease_terminal_grace_ms": 2000,
    "lease_frame_bytes": 732,
    "lease_ack_bytes": 444,
    "packets_per_endpoint": 1,
    "descriptors_per_packet": 1,
    "reuse": False,
    "fallback": False,
})


def _canonical_plain(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except Exception:
        raise ControllerServiceV2Error("lease_endpoint_invalid") from None


def lease_endpoint_registry_digest_v2() -> str:
    return hashlib.sha256(_canonical_plain(LEASE_ENDPOINT_V2_REGISTRY)).hexdigest()


def lease_endpoint_address_v2(*, machine_id: str, broker_epoch: str,
                              controller_epoch: str, broker_digest: str,
                              broker_config_digest: str,
                              controller_config_digest: str,
                              operation_id: str,
                              authorization_digest: str) -> bytes:
    values = {
        "protocol": PROTOCOL,
        "purpose": LEASE_ENDPOINT_PURPOSE_V2,
        "endpoint_identity": LEASE_ENDPOINT_IDENTITY_V2,
        "machine_id": machine_id,
        "broker_epoch": broker_epoch,
        "controller_epoch": controller_epoch,
        "broker_digest": broker_digest,
        "broker_config_digest": broker_config_digest,
        "controller_config_digest": controller_config_digest,
        "operation_id": operation_id,
        "authorization_digest": authorization_digest,
    }
    if (not isinstance(machine_id, str) or _MACHINE.fullmatch(machine_id) is None
            or not isinstance(broker_epoch, str) or _EPOCH.fullmatch(broker_epoch) is None
            or not isinstance(controller_epoch, str) or _EPOCH.fullmatch(controller_epoch) is None
            or not isinstance(operation_id, str) or _OPERATION.fullmatch(operation_id) is None
            or any(not isinstance(values[name], str) or _DIGEST.fullmatch(values[name]) is None
                   for name in ("broker_digest", "broker_config_digest",
                                "controller_config_digest", "authorization_digest"))):
        raise ControllerServiceV2Error("lease_endpoint_invalid")
    digest = hashlib.sha256(
        b"credential-broker-lease-address-v2\0" + _canonical_plain(values)
    ).hexdigest().encode("ascii")
    address = LEASE_ENDPOINT_PREFIX_V2 + digest
    if len(address) != LEASE_ENDPOINT_ADDRESS_BYTES_V2:
        raise ControllerServiceV2Error("lease_endpoint_invalid")
    return address


class ControllerServiceV2Error(RuntimeError):
    """Bounded, secret-free controller transport failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
            code = "controller_service_refused"
        self.code = code
        super().__init__(code)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "admission_open": False}


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    uid: int
    gid: int
    pid: int
    start_ticks: int
    executable_digest: str
    unit_digest: str
    config_digest: str

    def __post_init__(self) -> None:
        process_integers = (self.uid, self.gid, self.pid)
        if (any(type(value) is not int or value < 1 or value > 2**31 - 1
                for value in process_integers)
                or type(self.start_ticks) is not int
                or not 1 <= self.start_ticks <= MAX_SEQUENCE
                or any(not isinstance(value, str) or _DIGEST.fullmatch(value) is None for value in (
                    self.executable_digest, self.unit_digest, self.config_digest))):
            raise ControllerServiceV2Error("process_identity_invalid")

    def hello_fields(self, prefix: str) -> dict[str, Any]:
        if type(prefix) is not str or prefix not in {"broker", "controller"}:
            raise ControllerServiceV2Error("process_identity_prefix_invalid")
        return {
            f"{prefix}_pid": self.pid,
            f"{prefix}_start_ticks": self.start_ticks,
            f"{prefix}_executable_digest": self.executable_digest,
            f"{prefix}_unit_digest": self.unit_digest,
            f"{prefix}_config_digest": self.config_digest,
        }


@dataclass(frozen=True, slots=True)
class ControllerServiceConfig:
    machine_id: str
    controller: ProcessIdentity
    broker: ProcessIdentity
    policy_digest: str
    egress_digest: str
    broker_digest: str
    proof_digest: str
    effective_isolation_digest: str
    evidence_id: str | None

    def __post_init__(self) -> None:
        digests = (self.policy_digest, self.egress_digest, self.broker_digest,
                   self.proof_digest, self.effective_isolation_digest)
        if (type(self.controller) is not ProcessIdentity
                or type(self.broker) is not ProcessIdentity
                or not isinstance(self.machine_id, str)
                or _MACHINE.fullmatch(self.machine_id) is None
                or any(not isinstance(value, str) or _DIGEST.fullmatch(value) is None
                       for value in digests)
                or (self.evidence_id is not None
                    and (not isinstance(self.evidence_id, str)
                         or re.fullmatch(r"evidence-[a-z0-9]{7,54}",
                                        self.evidence_id) is None))):
            raise ControllerServiceV2Error("controller_config_invalid")

    def configured_digests(self) -> dict[str, Any]:
        return {
            "policy_digest": self.policy_digest,
            "egress_digest": self.egress_digest,
            "broker_digest": self.broker_digest,
            "proof_digest": self.proof_digest,
            "effective_isolation_digest": self.effective_isolation_digest,
            "evidence_id": self.evidence_id,
        }


class ExactProcessIdentityObserver:
    """Perform the required start/observe/start PID-reuse-safe sampling."""

    __slots__ = ("_expected", "_start_reader", "_detail_reader")

    def __init__(self, expected: ProcessIdentity, *, start_reader: Callable[[int], int],
                 detail_reader: Callable[[int], Mapping[str, Any]]) -> None:
        if (type(expected) is not ProcessIdentity or not callable(start_reader)
                or not callable(detail_reader)):
            raise ControllerServiceV2Error("identity_observer_invalid")
        self._expected = expected
        self._start_reader = start_reader
        self._detail_reader = detail_reader

    def __call__(self, pid: int, uid: int, gid: int) -> ProcessIdentity:
        if any(type(value) is not int or value < 1 for value in (pid, uid, gid)):
            raise ControllerServiceV2Error("peer_identity_unavailable")
        try:
            first = self._start_reader(pid)
            details = self._detail_reader(pid)
            second = self._start_reader(pid)
            observed = ProcessIdentity(
                uid=uid, gid=gid, pid=pid, start_ticks=first,
                executable_digest=details["executable_digest"],
                unit_digest=details["unit_digest"],
                config_digest=details["config_digest"],
            )
        except Exception:
            raise ControllerServiceV2Error("peer_identity_unavailable") from None
        if first != second or observed != self._expected:
            raise ControllerServiceV2Error("peer_identity_mismatch")
        return observed


class ExactBrokerSelfObserver:
    """Bind PID-safe self observation to one immutable sealed expectation set."""

    __slots__ = ("_expected", "_current_reader", "_start_reader",
                 "_detail_reader", "_sealed_reader")

    def __init__(self, expected: ControllerServiceConfig, *,
                 current_process_identity_reader, start_reader, detail_reader,
                 sealed_reader) -> None:
        if (type(expected) is not ControllerServiceConfig
                or not callable(current_process_identity_reader)
                or not callable(start_reader) or not callable(detail_reader)
                or not callable(sealed_reader)):
            raise ControllerServiceV2Error("broker_self_observer_invalid")
        self._expected = expected
        self._current_reader = current_process_identity_reader
        self._start_reader = start_reader
        self._detail_reader = detail_reader
        self._sealed_reader = sealed_reader

    def __call__(self) -> ControllerServiceConfig:
        try:
            current = self._current_reader()
            if (not isinstance(current, tuple) or len(current) != 3
                    or any(type(value) is not int or value < 1 for value in current)):
                raise ControllerServiceV2Error("broker_self_identity_unavailable")
            pid, uid, gid = current
            first = self._start_reader(pid)
            details = self._detail_reader(pid)
            sealed = self._sealed_reader()
            second = self._start_reader(pid)
            process = ProcessIdentity(
                uid=uid, gid=gid, pid=pid, start_ticks=first,
                executable_digest=details["executable_digest"],
                unit_digest=details["unit_digest"],
                config_digest=details["config_digest"],
            )
        except Exception:
            raise ControllerServiceV2Error("broker_self_identity_unavailable") from None
        if first != second or process != self._expected.broker or sealed != self._expected:
            raise ControllerServiceV2Error("broker_self_identity_mismatch")
        return self._expected


def abstract_controller_address(machine_id: str, broker_digest: str) -> bytes:
    """Return the fixed broker-owned abstract endpoint; this performs no I/O."""

    if (not isinstance(machine_id, str) or not isinstance(broker_digest, str)
            or _MACHINE.fullmatch(machine_id) is None
            or _DIGEST.fullmatch(broker_digest) is None):
        raise ControllerServiceV2Error("controller_address_invalid")
    suffix = hashlib.sha256(
        f"controller-v2:{machine_id}:{broker_digest}".encode("ascii")
    ).hexdigest()[:32]
    return b"\0sandbox-credential-controller-v2-" + suffix.encode("ascii")


def _connection_contract(value: Any) -> bool:
    try:
        return all(callable(getattr(value, name, None)) for name in (
            "getsockopt", "recvmsg", "sendall", "close",
        ))
    except Exception:
        return False


def _peer_credentials(connection: Any, *, so_peercred: int) -> tuple[int, int, int]:
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, so_peercred, _CREDENTIALS.size)
    except Exception:
        raise ControllerServiceV2Error("peer_credentials_unavailable") from None
    if not isinstance(raw, bytes) or len(raw) != _CREDENTIALS.size:
        raise ControllerServiceV2Error("peer_credentials_invalid")
    pid, uid, gid = _CREDENTIALS.unpack(raw)
    if any(type(value) is not int or value < 1 for value in (pid, uid, gid)):
        raise ControllerServiceV2Error("peer_credentials_invalid")
    return pid, uid, gid


def _rights_descriptors(payload: Any) -> tuple[int, ...]:
    if not isinstance(payload, bytes) or len(payload) % struct.calcsize("i"):
        return ()
    return tuple(struct.unpack_from("i", payload, offset)[0]
                 for offset in range(0, len(payload), struct.calcsize("i")))


def _prescan_and_close_rights(ancillary: Any, *, scm_rights: int,
                              closer: Callable[[int], Any]) -> tuple[bool, bool]:
    """Close every returned right before interpreting any other cmsg."""

    if not isinstance(ancillary, list):
        return False, False
    rights_seen = False
    cleanup_failed = False
    closed: set[int] = set()
    for item in ancillary:
        if not isinstance(item, tuple) or len(item) != 3:
            continue
        level, kind, payload = item
        if level != socket.SOL_SOCKET or kind != scm_rights:
            continue
        rights_seen = True
        for descriptor in _rights_descriptors(payload):
            if descriptor in closed:
                continue
            closed.add(descriptor)
            try:
                closer(descriptor)
            except Exception:
                cleanup_failed = True
    return rights_seen, cleanup_failed


def _packet_credentials(ancillary: Any, *, scm_credentials: int,
                        rights_seen: bool) -> tuple[int, int, int]:
    credentials: list[tuple[int, int, int]] = []
    if rights_seen:
        raise ControllerServiceV2Error("packet_rights_forbidden")
    if not isinstance(ancillary, list):
        raise ControllerServiceV2Error("packet_credentials_invalid")
    for item in ancillary:
        if not isinstance(item, tuple) or len(item) != 3:
            raise ControllerServiceV2Error("packet_credentials_invalid")
        level, kind, payload = item
        if level == socket.SOL_SOCKET and kind == scm_credentials:
            if not isinstance(payload, bytes) or len(payload) != _CREDENTIALS.size:
                raise ControllerServiceV2Error("packet_credentials_invalid")
            credentials.append(_CREDENTIALS.unpack(payload))
        else:
            raise ControllerServiceV2Error("packet_credentials_invalid")
    if len(credentials) != 1 or any(value < 1 for value in credentials[0]):
        raise ControllerServiceV2Error("packet_credentials_invalid")
    return credentials[0]


def receive_authenticated_packet(
    connection: Any, *, expected: ProcessIdentity,
    observer: Callable[[int, int, int], ProcessIdentity], so_peercred: int,
    scm_credentials: int, scm_rights: int, closer: Callable[[int], Any],
) -> tuple[bytes, ProcessIdentity]:
    """Authenticate connection and one packet before returning frame bytes."""

    if (type(expected) is not ProcessIdentity or not callable(observer)
            or not callable(closer)
            or any(type(value) is not int or value < 1
                   for value in (so_peercred, scm_credentials, scm_rights))):
        raise ControllerServiceV2Error("authenticated_packet_arguments_invalid")
    peer = _peer_credentials(connection, so_peercred=so_peercred)
    try:
        ancillary_bytes = socket.CMSG_SPACE(_CREDENTIALS.size)
        packet, ancillary, flags, _address = connection.recvmsg(
            MAX_CONTROLLER_FRAME_BYTES, ancillary_bytes,
            getattr(socket, "MSG_CMSG_CLOEXEC", 0),
        )
    except (TimeoutError, socket.timeout):
        raise
    except Exception:
        raise ControllerServiceV2Error("controller_receive_failed") from None
    rights_seen, cleanup_failed = _prescan_and_close_rights(
        ancillary, scm_rights=scm_rights, closer=closer,
    )
    if cleanup_failed:
        raise ControllerServiceV2Error("packet_rights_cleanup_failed")
    truncation = getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)
    if not packet or len(packet) > MAX_CONTROLLER_FRAME_BYTES or flags & truncation:
        raise ControllerServiceV2Error("controller_packet_invalid")
    packet_peer = _packet_credentials(
        ancillary, scm_credentials=scm_credentials, rights_seen=rights_seen,
    )
    if packet_peer != peer:
        raise ControllerServiceV2Error("packet_identity_drift")
    try:
        observed = observer(*peer)
    except Exception:
        raise ControllerServiceV2Error("peer_identity_unavailable") from None
    if observed != expected:
        raise ControllerServiceV2Error("peer_identity_mismatch")
    return packet, observed


_SESSION_LEASE_ISSUER = object()


class _SessionLeaseSocket:
    """Opaque session-registered socket; only ControllerBrokerSession can mint it."""

    __slots__ = ("_session", "_connection", "_observer", "_so_peercred",
                 "_scm_credentials", "_scm_rights", "_closer", "machine_id",
                 "broker_epoch", "controller_epoch", "owner", "operation_id",
                 "authorization_digest", "authorization_expires_at_unix_ms",
                 "request_deadline_unix_ms", "lease_address", "_connect_timeout_ms",
                 "_used", "_closed")

    def __init__(self, issuer, session, connection, *, observer, so_peercred,
                 scm_credentials, scm_rights, closer, operation_id=None,
                 authorization_digest=None, authorization_expires_at_unix_ms=None,
                 request_deadline_unix_ms=None,
                 lease_address=None, connect_timeout_ms=None) -> None:
        if issuer is not _SESSION_LEASE_ISSUER:
            raise ControllerServiceV2Error("lease_transport_invalid")
        self._session = session
        self._connection = connection
        self._observer = observer
        self._so_peercred = so_peercred
        self._scm_credentials = scm_credentials
        self._scm_rights = scm_rights
        self._closer = closer
        self.machine_id = session.config.machine_id
        self.broker_epoch = session.broker_epoch
        self.controller_epoch = session.controller_epoch
        self.owner = session.owner
        self.operation_id = operation_id
        self.authorization_digest = authorization_digest
        self.authorization_expires_at_unix_ms = authorization_expires_at_unix_ms
        self.request_deadline_unix_ms = request_deadline_unix_ms
        self.lease_address = lease_address
        self._connect_timeout_ms = connect_timeout_ms
        self._used = False
        self._closed = False

    def exchange(self, packet: bytes, descriptor: int, timeout_ms: int) -> bytes:
        self._session.consume_lease_socket(self)
        if (type(packet) is not bytes or type(descriptor) is not int or descriptor < 0
                or type(timeout_ms) is not int or not 0 <= timeout_ms <= 32000
                or self.operation_id is None or self.authorization_digest is None
                or self.authorization_expires_at_unix_ms is None
                or self.request_deadline_unix_ms is None
                or self.lease_address is None
                ):
            raise ControllerServiceV2Error("lease_transport_invalid")
        self._used = True
        setter = getattr(self._connection, "settimeout", None)
        if callable(setter):
            setter(timeout_ms / 1000)
        rights = array("i", [descriptor]).tobytes()
        sent = self._connection.sendmsg(
            [packet], [(socket.SOL_SOCKET, self._scm_rights, rights)])
        if sent != len(packet):
            raise ControllerServiceV2Error("lease_transport_invalid")
        try:
            acknowledgement, observed = receive_authenticated_packet(
                self._connection, expected=self._session.config.broker,
                observer=self._observer, so_peercred=self._so_peercred,
                scm_credentials=self._scm_credentials, scm_rights=self._scm_rights,
                closer=self._closer,
            )
        except ControllerServiceV2Error as exc:
            if exc.code == "packet_rights_cleanup_failed":
                if self._session._terminal_code == "controller_session_closed":
                    self._session._terminal_code = "packet_rights_cleanup_failed"
                self._session.admission_open = False
                self._session.close("lease_ack_cleanup_failed")
                try:
                    self.close()
                except Exception:
                    pass
                raise ControllerServiceV2Error(exc.code) from None
            code = ("lease_ack_invalid" if exc.code == "packet_size_invalid"
                    else "lease_ack_provenance_invalid")
            raise ControllerServiceV2Error(code) from None
        if observed != self._session.config.broker:
            raise ControllerServiceV2Error("lease_ack_provenance_invalid")
        if type(acknowledgement) is not bytes or len(acknowledgement) != 444:
            raise ControllerServiceV2Error("lease_ack_invalid")
        return acknowledgement

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._session._lease_sockets.pop(id(self), None)
            self._connection.close()


_COMPOSITION_ISSUER = object()
_BROKER_COMPOSITION_ISSUER = object()


class AuthenticatedBrokerCompositionReceiptV2:
    """Opaque one-use proof minted by one exact authenticated broker object."""

    __slots__ = ("_issuer", "_broker", "_broker_type", "_nonce", "_purpose",
                 "_machine_id", "_broker_epoch", "_controller_epoch", "_config",
                 "_used", "_sealed")

    def __init__(self, issuer, broker, purpose, nonce) -> None:
        if issuer is not _BROKER_COMPOSITION_ISSUER:
            raise ControllerServiceV2Error("composition_refused")
        object.__setattr__(self, "_issuer", issuer)
        object.__setattr__(self, "_broker", broker)
        object.__setattr__(self, "_broker_type", type(broker))
        object.__setattr__(self, "_nonce", nonce)
        object.__setattr__(self, "_purpose", purpose)
        object.__setattr__(self, "_machine_id", broker.config.machine_id)
        object.__setattr__(self, "_broker_epoch", broker.broker_epoch)
        object.__setattr__(self, "_controller_epoch", broker.controller_epoch)
        object.__setattr__(self, "_config", broker.config)
        object.__setattr__(self, "_used", False)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name, value) -> None:
        del name, value
        raise ControllerServiceV2Error("composition_refused")

    def consume_for_guest_bridge(self, broker) -> None:
        if (self._issuer is not _BROKER_COMPOSITION_ISSUER or self._used
                or self._purpose != "guest_bridge" or broker is not self._broker
                or type(broker) is not self._broker_type
                or getattr(broker, "_composition_nonce", None) is not self._nonce
                or getattr(broker, "authenticated", None) is not True
                or getattr(broker, "config", None) is not self._config
                or broker.config.machine_id != self._machine_id
                or getattr(broker, "broker_epoch", None) != self._broker_epoch
                or getattr(broker, "controller_epoch", None) != self._controller_epoch):
            raise ControllerServiceV2Error("composition_refused")
        object.__setattr__(self, "_used", True)

    def require_exact_broker(self, broker) -> None:
        if (self._issuer is not _BROKER_COMPOSITION_ISSUER or self._used
                or broker is not self._broker or type(broker) is not self._broker_type
                or getattr(broker, "_composition_nonce", None) is not self._nonce):
            raise ControllerServiceV2Error("composition_refused")


_BOUND_GUEST_SUBMIT_ISSUER = object()


class BoundGuestSubmitCapabilityV2:
    """One fixed-purpose trusted-process API callable.

    Exact construction prevents accidental handler laundering.  It is not an
    anti-reflection or cryptographic boundary against code already executing in
    this trusted process; the hostile guest crosses only the authenticated
    socket boundary and never receives this object.
    """

    __slots__ = ("_invoke",)

    def __init__(self, issuer, invoke) -> None:
        if issuer is not _BOUND_GUEST_SUBMIT_ISSUER or not callable(invoke):
            raise ControllerServiceV2Error("composition_refused")
        object.__setattr__(self, "_invoke", invoke)

    def __setattr__(self, name, value) -> None:
        del name, value
        raise ControllerServiceV2Error("composition_refused")

    def __delattr__(self, name) -> None:
        del name
        raise ControllerServiceV2Error("composition_refused")

    def __getattribute__(self, name):
        if name == "_invoke":
            raise AttributeError("guest submit capability internals are private")
        return object.__getattribute__(self, name)

    def __call__(self, request, *, connection_identity):
        invoke = object.__getattribute__(self, "_invoke")
        return invoke(request, connection_identity=connection_identity)

    def __repr__(self) -> str:
        return "BoundGuestSubmitCapabilityV2(purpose='guest_submit')"


def _mint_bound_guest_submit_capability_v2(invoke):
    return BoundGuestSubmitCapabilityV2(_BOUND_GUEST_SUBMIT_ISSUER, invoke)


def _mint_authenticated_broker_composition_receipt_v2(broker, *, purpose: str,
                                                        nonce):
    """Internal broker-module capability; never used by application composition."""

    if (purpose != "guest_bridge" or nonce is None
            or getattr(broker, "_composition_nonce", None) is not nonce
            or getattr(broker, "authenticated", None) is not True
            or getattr(broker, "controller_epoch", None) is None
            or getattr(broker, "config", None) is None):
        raise ControllerServiceV2Error("composition_refused")
    return AuthenticatedBrokerCompositionReceiptV2(
        _BROKER_COMPOSITION_ISSUER, broker, purpose, nonce,
    )


class AuthenticatedCompositionReceiptV2:
    """Opaque one-use proof minted by one authenticated controller session."""

    __slots__ = ("_issuer", "_session", "_purpose", "_used")

    def __init__(self, issuer, session, purpose) -> None:
        if issuer is not _COMPOSITION_ISSUER:
            raise ControllerServiceV2Error("composition_refused")
        self._issuer = issuer
        self._session = session
        self._purpose = purpose
        self._used = False

    def consume_for_controller(self, authority, *, purpose: str) -> None:
        session = self._session
        if (self._issuer is not _COMPOSITION_ISSUER or self._used
                or purpose != self._purpose
                or not isinstance(session, ControllerBrokerSession)
                or not session.authenticated or session.broker_epoch is None
                or getattr(authority, "session", None) is not session):
            raise ControllerServiceV2Error("composition_refused")
        self._used = True


class ControllerBrokerSession:
    """One authenticated broker connection owned by one controller epoch."""

    __slots__ = ("connection", "config", "controller_epoch", "owner", "sequences",
                 "authenticated", "admission_open", "broker_epoch", "_closed",
                 "_on_terminal", "_observation", "_terminal_code",
                 "_next_outgoing", "_last_received_frame",
                 "_last_received_consumed", "_lease_sockets")

    def __init__(self, connection: Any, config: ControllerServiceConfig,
                 controller_epoch: str, owner: str, *, on_terminal: Callable[[str], Any]) -> None:
        if (not _connection_contract(connection)
                or type(config) is not ControllerServiceConfig
                or not isinstance(controller_epoch, str)
                or _EPOCH.fullmatch(controller_epoch) is None
                or not isinstance(owner, str)
                or re.fullmatch(r"controller-session-[0-9a-f]{16,48}", owner) is None
                or not callable(on_terminal)):
            raise ControllerServiceV2Error("controller_session_invalid")
        self.connection = connection
        self.config = config
        self.controller_epoch = controller_epoch
        self.owner = owner
        self.sequences = DirectionalSequence()
        self.authenticated = False
        self.admission_open = False
        self.broker_epoch: str | None = None
        self._closed = False
        self._on_terminal = on_terminal
        self._observation = TemporalObservation()
        self._terminal_code = "controller_session_closed"
        self._next_outgoing = 1
        self._last_received_frame = None
        self._last_received_consumed = True
        self._lease_sockets: dict[int, _SessionLeaseSocket] = {}

    def accept_lease_socket(self, connection: Any, *, observer, so_peercred: int,
                            so_passcred: int, scm_credentials: int,
                            scm_rights: int, closer, operation_id=None,
                            authorization_digest=None,
                            authorization_expires_at_unix_ms=None,
                            request_deadline_unix_ms=None,
                            lease_address=None, connect_timeout_ms=None):
        """Authenticate and register one outbound lease socket for this session."""

        if (self._closed or not self.authenticated or self.broker_epoch is None
                or len(self._lease_sockets) >= 16
                or not callable(observer) or not callable(closer)
                or type(so_peercred) is not int or type(so_passcred) is not int
                or type(scm_credentials) is not int
                or type(scm_rights) is not int or min(
                    so_peercred, so_passcred, scm_credentials, scm_rights) < 1
                or not callable(getattr(connection, "setsockopt", None))
                or not callable(getattr(connection, "sendmsg", None))
                or not callable(getattr(connection, "recvmsg", None))
                or not callable(getattr(connection, "close", None))):
            raise ControllerServiceV2Error("lease_transport_invalid")
        try:
            connection.setsockopt(socket.SOL_SOCKET, so_passcred, 1)
            passcred_value = connection.getsockopt(socket.SOL_SOCKET, so_passcred)
            if type(passcred_value) is not int or passcred_value != 1:
                raise ControllerServiceV2Error("lease_passcred_unavailable")
        except ControllerServiceV2Error:
            raise
        except Exception:
            raise ControllerServiceV2Error("lease_passcred_unavailable") from None
        peer = _peer_credentials(connection, so_peercred=so_peercred)
        try:
            observed = observer(*peer)
        except Exception:
            raise ControllerServiceV2Error("peer_identity_unavailable") from None
        if observed != self.config.broker:
            raise ControllerServiceV2Error("peer_identity_mismatch")
        receipt = _SessionLeaseSocket(
            _SESSION_LEASE_ISSUER, self, connection, observer=observer,
            so_peercred=so_peercred, scm_credentials=scm_credentials,
            scm_rights=scm_rights, closer=closer, operation_id=operation_id,
            authorization_digest=authorization_digest,
            authorization_expires_at_unix_ms=authorization_expires_at_unix_ms,
            request_deadline_unix_ms=request_deadline_unix_ms,
            lease_address=lease_address, connect_timeout_ms=connect_timeout_ms)
        self._lease_sockets[id(receipt)] = receipt
        return receipt

    def connect_lease_endpoint_v2(self, *, operation_id: str,
                                  authorization_digest: str,
                                  authorization_expires_at_unix_ms: int,
                                  now_ms: int, connector, observer,
                                  so_peercred: int, so_passcred: int,
                                  scm_credentials: int, scm_rights: int, closer):
        """Independently derive and connect once to one operation endpoint."""

        if (self._closed or not self.authenticated or self.broker_epoch is None
                or not callable(connector) or not callable(observer) or not callable(closer)
                or type(so_peercred) is not int or so_peercred < 1
                or type(so_passcred) is not int or so_passcred < 1
                or type(scm_credentials) is not int or scm_credentials < 1
                or type(scm_rights) is not int or scm_rights < 1
                or type(now_ms) is not int
                or type(authorization_expires_at_unix_ms) is not int
                or authorization_expires_at_unix_ms <= now_ms):
            raise ControllerServiceV2Error("lease_transport_invalid")
        address = lease_endpoint_address_v2(
            machine_id=self.config.machine_id,
            broker_epoch=self.broker_epoch,
            controller_epoch=self.controller_epoch,
            broker_digest=self.config.broker_digest,
            broker_config_digest=self.config.broker.config_digest,
            controller_config_digest=self.config.controller.config_digest,
            operation_id=operation_id,
            authorization_digest=authorization_digest,
        )
        connection = None
        transferred = False
        timeout_ms = min(1000, authorization_expires_at_unix_ms - now_ms)
        try:
            connection = connector(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            setter = getattr(connection, "settimeout", None)
            if (not callable(setter)
                    or not callable(getattr(connection, "connect", None))
                    or not callable(getattr(connection, "getsockopt", None))
                    or not callable(getattr(connection, "sendmsg", None))
                    or not callable(getattr(connection, "recvmsg", None))
                    or not callable(getattr(connection, "close", None))):
                raise ControllerServiceV2Error("lease_transport_invalid")
            setter(timeout_ms / 1000)
            connection.connect(address)
            receipt = self.accept_lease_socket(
                connection, observer=observer, so_peercred=so_peercred,
                so_passcred=so_passcred,
                scm_credentials=scm_credentials, scm_rights=scm_rights,
                closer=closer, operation_id=operation_id,
                authorization_digest=authorization_digest,
                authorization_expires_at_unix_ms=authorization_expires_at_unix_ms,
                lease_address=address, connect_timeout_ms=timeout_ms)
            transferred = True
            return receipt
        except Exception:
            if connection is not None and not transferred:
                try:
                    connection.close()
                except Exception:
                    self._terminal_code = "lease_socket_cleanup_failed"
                    self._closed = True
                    raise ControllerServiceV2Error(self._terminal_code) from None
            raise ControllerServiceV2Error("lease_transport_invalid") from None

    def bind_lease_socket(self, receipt, *, operation_id: str,
                          authorization_digest: str,
                          authorization_expires_at_unix_ms: int,
                          request_deadline_unix_ms: int) -> None:
        expected_address = lease_endpoint_address_v2(
            machine_id=self.config.machine_id, broker_epoch=self.broker_epoch,
            controller_epoch=self.controller_epoch,
            broker_digest=self.config.broker_digest,
            broker_config_digest=self.config.broker.config_digest,
            controller_config_digest=self.config.controller.config_digest,
            operation_id=operation_id,
            authorization_digest=authorization_digest)
        if (not self.owns_lease_socket(receipt)
                or not isinstance(operation_id, str) or _OPERATION.fullmatch(operation_id) is None
                or not isinstance(authorization_digest, str)
                or _DIGEST.fullmatch(authorization_digest) is None
                or type(authorization_expires_at_unix_ms) is not int
                or type(request_deadline_unix_ms) is not int
                or (receipt.operation_id is not None and receipt.operation_id != operation_id)
                or (receipt.authorization_digest is not None
                    and receipt.authorization_digest != authorization_digest)
                or (receipt.authorization_expires_at_unix_ms is not None
                    and receipt.authorization_expires_at_unix_ms
                    != authorization_expires_at_unix_ms)
                or (receipt.request_deadline_unix_ms is not None
                    and receipt.request_deadline_unix_ms != request_deadline_unix_ms)
                or (receipt.lease_address is not None
                    and receipt.lease_address != expected_address)):
            raise ControllerServiceV2Error("lease_transport_invalid")
        receipt.operation_id = operation_id
        receipt.authorization_digest = authorization_digest
        receipt.authorization_expires_at_unix_ms = authorization_expires_at_unix_ms
        receipt.request_deadline_unix_ms = request_deadline_unix_ms
        receipt.lease_address = expected_address

    def mint_composition_receipt(self, purpose: str):
        """Mint one opaque, purpose-bound receipt from this live handshake."""

        if (self._closed or not self.authenticated or self.broker_epoch is None
                or purpose != "public_acceptance"):
            raise ControllerServiceV2Error("composition_refused")
        return AuthenticatedCompositionReceiptV2(
            _COMPOSITION_ISSUER, self, purpose,
        )

    def owns_lease_socket(self, receipt) -> bool:
        return (isinstance(receipt, _SessionLeaseSocket)
                and receipt._session is self and self._lease_sockets.get(id(receipt)) is receipt
                and not receipt._used and not receipt._closed)

    def consume_lease_socket(self, receipt) -> None:
        if not self.owns_lease_socket(receipt):
            raise ControllerServiceV2Error("lease_transport_invalid")
        del self._lease_sockets[id(receipt)]

    def handshake(self, *, observer: Callable[[int, int, int], ProcessIdentity],
                  now_ms: int, monotonic: Callable[[], float], so_peercred: int,
                  scm_credentials: int, scm_rights: int, closer: Callable[[int], Any],
                  pair_guard: Callable[[str], Any] | None = None) -> dict[str, Any]:
        try:
            started = monotonic()
            setter = getattr(self.connection, "settimeout", None)
            if callable(setter):
                setter(HANDSHAKE_TIMEOUT_SECONDS)
            packet, _identity = receive_authenticated_packet(
                self.connection, expected=self.config.broker, observer=observer,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
            if monotonic() - started > HANDSHAKE_TIMEOUT_SECONDS:
                raise ControllerServiceV2Error("handshake_timeout")
            hello = decode_controller_frame(
                packet, direction="broker_to_controller", now_ms=now_ms,
                observation=self._observation,
            )
            if hello["type"] != "HELLO_V2":
                raise ControllerServiceV2Error("handshake_required")
            self.sequences.accept("broker_to_controller", hello["sequence"])
            expected = {
                "machine_id": self.config.machine_id,
                **self.config.broker.hello_fields("broker"),
                **self.config.configured_digests(),
            }
            if any(hello.get(key) != value for key, value in expected.items()):
                raise ControllerServiceV2Error("broker_hello_mismatch")
            self.broker_epoch = hello["broker_epoch"]
            if pair_guard is not None:
                pair_guard(self.broker_epoch)
            handshake_values = {
                "protocol": PROTOCOL, "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch,
                "controller_epoch": self.controller_epoch,
                **self.config.broker.hello_fields("broker"),
                **self.config.controller.hello_fields("controller"),
                **self.config.configured_digests(),
            }
            ack = {
                "protocol": PROTOCOL, "type": "HELLO_ACK_V2",
                "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch,
                "controller_epoch": self.controller_epoch,
                "sequence": 1, "reply_to": 1, "accepted": True,
                **self.config.controller.hello_fields("controller"),
                "handshake_digest": digest_document("handshake_digest", handshake_values),
            }
            self.sequences.accept("controller_to_broker", ack["sequence"])
            self._next_outgoing = 2
            frame = encode_controller_frame(
                ack, direction="controller_to_broker", now_ms=now_ms,
                temporal_context={"handshake_digest": ack["handshake_digest"]},
                observation=self._observation,
            )
            self.connection.sendall(frame)
            if monotonic() - started > HANDSHAKE_TIMEOUT_SECONDS:
                raise ControllerServiceV2Error("handshake_timeout")
            self.authenticated = True
            return {"ok": True, "code": "controller_authenticated",
                    "admission_open": False, "broker_epoch": self.broker_epoch}
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

    def close(self, reason: str = "controller_disconnected") -> dict[str, Any]:
        if not self._closed:
            self._closed = True
            self.authenticated = False
            self.admission_open = False
            for receipt in tuple(self._lease_sockets.values()):
                try:
                    receipt.close()
                except Exception:
                    if self._terminal_code == "controller_session_closed":
                        self._terminal_code = "lease_socket_cleanup_failed"
            try:
                self.connection.close()
            except Exception:
                if self._terminal_code == "controller_session_closed":
                    self._terminal_code = "controller_socket_cleanup_failed"
            try:
                self._on_terminal(
                    reason if isinstance(reason, str) else "controller_disconnected"
                )
            except Exception:
                if self._terminal_code == "controller_session_closed":
                    self._terminal_code = "terminal_callback_failed"
        return {"ok": self._terminal_code == "controller_session_closed",
                "code": self._terminal_code, "admission_open": False}

    def receive_frame(self, *, observer, now_ms: int, so_peercred: int,
                      scm_credentials: int, scm_rights: int, closer,
                      temporal_context=None) -> dict[str, Any]:
        """Decode one post-handshake broker frame with pinned clock/sequence state."""

        if self._closed or not self.authenticated or self.broker_epoch is None:
            raise ControllerServiceV2Error("controller_connection_closed")
        try:
            packet, _observed = receive_authenticated_packet(
                self.connection, expected=self.config.broker, observer=observer,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
            value = decode_controller_frame(
                packet, direction="broker_to_controller", now_ms=now_ms,
                temporal_context=temporal_context, observation=self._observation,
            )
            if (value["type"] == "HELLO_V2"
                    or value["machine_id"] != self.config.machine_id
                    or value["broker_epoch"] != self.broker_epoch
                    or value["controller_epoch"] != self.controller_epoch):
                raise ControllerServiceV2Error("broker_frame_identity_mismatch")
            if value["type"] in {"AUDIT_PRE_V2", "AUDIT_POST_V2"}:
                self.sequences.accept_audit_retry(
                    "broker_to_controller", value["sequence"])
            else:
                self.sequences.accept("broker_to_controller", value["sequence"])
            self._last_received_frame = dict(value)
            self._last_received_consumed = False
            return value
        except Exception:
            closed = self.close("broker_frame_refused")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else "broker_frame_refused"
            ) from None

    def require_received_frame(self, value: Mapping[str, Any], *,
                               message_type: str) -> None:
        """Consume the exact last frame accepted by this session once."""

        if (self._closed or not self.authenticated
                or not isinstance(value, Mapping)
                or not isinstance(message_type, str)
                or self._last_received_consumed
                or self._last_received_frame != dict(value)
                or value.get("type") != message_type):
            raise ControllerServiceV2Error("controller_frame_not_accepted")
        self._last_received_consumed = True

    def send_frame(self, value: Mapping[str, Any], *, now_ms: int,
                   temporal_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send one authority frame with the session-owned next sequence."""

        if self._closed or not self.authenticated or self.broker_epoch is None:
            raise ControllerServiceV2Error("controller_connection_closed")
        try:
            message = dict(value)
            if "sequence" in message:
                raise ControllerServiceV2Error("controller_sequence_owned")
            message.update({
                "protocol": PROTOCOL,
                "machine_id": self.config.machine_id,
                "broker_epoch": self.broker_epoch,
                "controller_epoch": self.controller_epoch,
                "sequence": self._next_outgoing,
            })
            frame = encode_controller_frame(
                message, direction="controller_to_broker", now_ms=now_ms,
                temporal_context=temporal_context, observation=self._observation,
            )
            self.sequences.accept("controller_to_broker", self._next_outgoing)
            self._next_outgoing += 1
            self.connection.sendall(frame)
            return message
        except ControllerServiceV2Error:
            raise
        except ProtocolV2Error as exc:
            raise ControllerServiceV2Error(exc.code) from None
        except Exception:
            closed = self.close("controller_send_failed")
            raise ControllerServiceV2Error(
                closed["code"] if not closed["ok"] else "controller_send_failed"
            ) from None


class PersistentControllerService:
    """Explicitly-started one-process controller service; always starts closed."""

    __slots__ = ("config", "_epoch_factory", "_owner_factory", "controller_epoch",
                 "session", "admission_open", "_started", "_stopped", "_seen_pairs",
                 "_terminal_result")

    def __init__(self, config: ControllerServiceConfig, *, epoch_factory: Callable[[], str],
                 owner_factory: Callable[[], str]) -> None:
        if (type(config) is not ControllerServiceConfig or not callable(epoch_factory)
                or not callable(owner_factory)):
            raise ControllerServiceV2Error("controller_service_invalid")
        self.config = config
        self._epoch_factory = epoch_factory
        self._owner_factory = owner_factory
        self.controller_epoch: str | None = None
        self.session: ControllerBrokerSession | None = None
        self.admission_open = False
        self._started = False
        self._stopped = False
        self._seen_pairs: set[tuple[str, str]] = set()
        self._terminal_result: dict[str, Any] | None = None

    def start(self, *, platform: str, enabled: bool) -> dict[str, Any]:
        if self._started or self._stopped or enabled is not True or platform != "linux":
            raise ControllerServiceV2Error("controller_start_refused")
        try:
            epoch = self._epoch_factory()
        except Exception:
            raise ControllerServiceV2Error("controller_epoch_invalid") from None
        if not isinstance(epoch, str) or _EPOCH.fullmatch(epoch) is None:
            raise ControllerServiceV2Error("controller_epoch_invalid")
        self.controller_epoch = epoch
        self._started = True
        return {"ok": True, "code": "controller_started", "admission_open": False}

    def attach(self, connection: Any, *, observer: Callable[[int, int, int], ProcessIdentity],
               now_ms: int, monotonic: Callable[[], float], so_peercred: int,
               scm_credentials: int, scm_rights: int, closer: Callable[[int], Any]) -> dict[str, Any]:
        if self._terminal_result is not None:
            raise ControllerServiceV2Error(self._terminal_result["code"])
        if not self._started or self._stopped or self.controller_epoch is None or self.session is not None:
            raise ControllerServiceV2Error("controller_connection_refused")
        try:
            owner = self._owner_factory()
            session = ControllerBrokerSession(
                connection, self.config, self.controller_epoch, owner,
                on_terminal=lambda _reason: setattr(self, "admission_open", False),
            )
        except Exception:
            cleanup_failed = False
            try:
                connection.close()
            except Exception:
                cleanup_failed = True
            code = ("controller_socket_cleanup_failed" if cleanup_failed
                    else "controller_connection_refused")
            if cleanup_failed:
                self._terminal_result = {"ok": False, "code": code,
                                         "admission_open": False}
            raise ControllerServiceV2Error(code) from None
        self.session = session
        try:
            def reserve_pair(broker_epoch: str) -> None:
                pair = (self.controller_epoch, broker_epoch)
                if pair in self._seen_pairs:
                    raise ControllerServiceV2Error("epoch_pair_replayed")
                self._seen_pairs.add(pair)

            result = session.handshake(
                observer=observer, now_ms=now_ms, monotonic=monotonic,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer, pair_guard=reserve_pair,
            )
            return result
        except ControllerServiceV2Error as exc:
            self.session = None
            closed = session.close("handshake_refused")
            if not closed["ok"]:
                self._terminal_result = closed
                raise ControllerServiceV2Error(closed["code"]) from None
            raise ControllerServiceV2Error(exc.code) from None

    def connect(self, *, connector: Callable[[int, int, int], Any], observer,
                now_ms: int, monotonic: Callable[[], float], so_peercred: int,
                scm_credentials: int, scm_rights: int,
                closer: Callable[[int], Any]) -> dict[str, Any]:
        """Explicitly connect to the one fixed abstract seqpacket endpoint."""

        if not callable(connector):
            raise ControllerServiceV2Error("controller_connector_invalid")
        if self._terminal_result is not None:
            return dict(self._terminal_result)
        if (not self._started or self._stopped or self.controller_epoch is None
                or self.session is not None):
            raise ControllerServiceV2Error("controller_connection_refused")
        connection = None
        transferred = False
        try:
            connection = connector(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)
            connection.connect(abstract_controller_address(
                self.config.machine_id, self.config.broker_digest,
            ))
            transferred = True
            return self.attach(
                connection, observer=observer, now_ms=now_ms, monotonic=monotonic,
                so_peercred=so_peercred, scm_credentials=scm_credentials,
                scm_rights=scm_rights, closer=closer,
            )
        except ControllerServiceV2Error as exc:
            if self._terminal_result is not None:
                return dict(self._terminal_result)
            raise ControllerServiceV2Error(exc.code) from None
        except Exception:
            if connection is not None and not transferred:
                try:
                    connection.close()
                except Exception:
                    self._terminal_result = {
                        "ok": False, "code": "controller_socket_cleanup_failed",
                        "admission_open": False,
                    }
                    return dict(self._terminal_result)
            raise ControllerServiceV2Error("controller_connection_refused") from None

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

    def stop(self) -> dict[str, Any]:
        if self._terminal_result is not None:
            self._stopped = True
            return dict(self._terminal_result)
        if not self._stopped:
            result = self.disconnect()
            self._stopped = True
            if not result["ok"]:
                return result
        return {"ok": True, "code": "controller_stopped", "admission_open": False}


__all__ = [
    "AuthenticatedBrokerCompositionReceiptV2", "AuthenticatedCompositionReceiptV2",
    "ControllerBrokerSession", "ControllerServiceConfig", "ControllerServiceV2Error",
    "ExactBrokerSelfObserver", "ExactProcessIdentityObserver",
    "HANDSHAKE_TIMEOUT_SECONDS", "LEASE_ENDPOINT_ADDRESS_BYTES_V2",
    "LEASE_ENDPOINT_IDENTITY_V2", "LEASE_ENDPOINT_V2_REGISTRY", "ProcessIdentity",
    "PersistentControllerService", "abstract_controller_address",
    "lease_endpoint_address_v2", "lease_endpoint_registry_digest_v2",
    "receive_authenticated_packet",
]
