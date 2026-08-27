"""Connected, inert protocol-v2 acceptance graph.

This module is deliberately offline.  It provides injected in-memory kernel
transports so the real controller session, broker session, operation authority,
lifecycle authority, durable audit authority, and lease codecs can be exercised
as one graph.  It is not imported by runtime composition and performs no I/O at
construction time.
"""

from __future__ import annotations

from array import array
from collections import deque
import socket
import struct
import threading
from typing import Any, Callable, Mapping

from .credential_controller_audit_v2 import (
    ControllerAuditAuthorityV2,
    CredentialEffectExecutorV2,
    DurableAuditRepositoryV2,
)
from .credential_controller_authority_v2 import (
    ControllerAuthorityInterfaces,
    ControllerOperationAuthorityV2,
)
from .credential_controller_lifecycle_v2 import (
    ControllerLifecycleAuthorityV2,
    DerivedServiceConfigV2,
    FixedLifecycleExecutorV2,
    ManagedCredentialLifecycleV2,
    derived_config_document,
)
from .credential_controller_service_v2 import (
    ControllerServiceConfig,
    ControllerServiceV2Error,
    PersistentControllerService,
    ProcessIdentity,
)


_CREDS = struct.Struct("3i")


class OfflineV2Error(RuntimeError):
    """Bounded failure from the connected offline graph."""

    def __init__(self, code: str) -> None:
        self.code = code if isinstance(code, str) else "offline_graph_failed"
        super().__init__(self.code)


class MemoryAuditRepositoryV2(DurableAuditRepositoryV2):
    """Small durable-repository double with exact append boundaries."""

    def __init__(self, *, append_hook: Callable[[Mapping[str, Any]], bool] | None = None) -> None:
        self.committed: list[dict[str, Any]] = []
        self.append_hook = append_hook

    def records(self, machine_id: str):
        return tuple(item for item in self.committed if item["machine_id"] == machine_id)

    def append(self, record: Mapping[str, Any]) -> bool:
        value = dict(record)
        if self.append_hook is not None and self.append_hook(value) is not True:
            return False
        self.committed.append(value)
        return True


class _OfflineLifecycleExecutor(FixedLifecycleExecutorV2):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_verb = None
        self.absence_overrides: dict[str, Mapping[str, Any]] = {}

    def execute(self, verb, plan):
        self.calls.append((verb, plan.component))
        passed = verb != self.fail_verb
        return {"ok": passed, "code": "completed" if passed else "failed"}

    def observe_absence(self, plan):
        return self.absence_overrides.get(plan.component, {
            "observed": True, "owned": True, "unit_absent": True,
            "process_absent": True, "socket_absent": True,
            "cgroup_absent": True, "descriptor_absent": True,
        })


class _SeqpacketEnd:
    """Blocking SOCK_SEQPACKET-shaped endpoint with authenticated packets."""

    def __init__(self, identity: ProcessIdentity, peer_identity: ProcessIdentity) -> None:
        self.identity = identity
        self.peer_identity = peer_identity
        self.peer: _SeqpacketEnd | None = None
        self.packet_peer_identity = peer_identity
        self.packet_rights: tuple[int, ...] = ()
        self._packets: deque[bytes] = deque()
        self._condition = threading.Condition()
        self._closed = False
        self.close_count = 0
        self.timeout: float | None = 1.0
        self.connected = None
        self.drop_next_sends = 0
        self.timeout_next_receives = 0

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, address) -> None:
        self.connected = address

    def getsockopt(self, _level, _option, _size):
        peer = self.peer_identity
        return _CREDS.pack(peer.pid, peer.uid, peer.gid)

    def sendall(self, packet: bytes) -> None:
        peer = self.peer
        if self._closed or peer is None:
            raise OSError("closed")
        if self.drop_next_sends:
            self.drop_next_sends -= 1
            return
        with peer._condition:
            if peer._closed:
                raise OSError("closed")
            peer._packets.append(bytes(packet))
            peer._condition.notify_all()

    def recvmsg(self, *_args):
        with self._condition:
            if self.timeout_next_receives:
                self.timeout_next_receives -= 1
                raise TimeoutError("offline injected timeout")
            if not self._packets and not self._closed:
                self._condition.wait(self.timeout)
            if not self._packets:
                raise TimeoutError("offline packet timeout")
            packet = self._packets.popleft()
        peer = self.packet_peer_identity
        ancillary = [(socket.SOL_SOCKET, 2,
                      _CREDS.pack(peer.pid, peer.uid, peer.gid))]
        if self.packet_rights:
            ancillary.append((socket.SOL_SOCKET, socket.SCM_RIGHTS,
                              array("i", self.packet_rights).tobytes()))
        return packet, ancillary, 0, None

    def close(self) -> None:
        with self._condition:
            if not self._closed:
                self._closed = True
                self.close_count += 1
                self._condition.notify_all()


def _seqpacket_pair(controller: ProcessIdentity, broker: ProcessIdentity):
    controller_end = _SeqpacketEnd(controller, broker)
    broker_end = _SeqpacketEnd(broker, controller)
    controller_end.peer = broker_end
    broker_end.peer = controller_end
    return controller_end, broker_end


class _LeaseControllerEnd:
    def __init__(self, broker_identity: ProcessIdentity) -> None:
        self.broker_identity = broker_identity
        self.send_callback = None
        self._ack = b""
        self._condition = threading.Condition()
        self._closed = False
        self.close_count = 0
        self.timeout = 1.0

    def getsockopt(self, *_args):
        peer = self.broker_identity
        return _CREDS.pack(peer.pid, peer.uid, peer.gid)

    def settimeout(self, value):
        self.timeout = value

    def sendmsg(self, buffers, ancillary):
        if self._closed or self.send_callback is None:
            raise OSError("closed")
        descriptor = array("i")
        descriptor.frombytes(ancillary[0][2])
        packet = bytes(buffers[0])
        self.send_callback(packet, descriptor[0])
        return len(packet)

    def recv(self, _size):
        with self._condition:
            if not self._ack and not self._closed:
                self._condition.wait(self.timeout)
            return self._ack

    def deliver_ack(self, packet: bytes) -> None:
        with self._condition:
            self._ack = bytes(packet)
            self._condition.notify_all()

    def close(self):
        if not self._closed:
            self._closed = True
            self.close_count += 1


class _LeaseBrokerEnd:
    def __init__(self, controller_identity: ProcessIdentity, controller_end: _LeaseControllerEnd) -> None:
        self.controller_identity = controller_identity
        self.controller_end = controller_end
        self._closed = False
        self.close_count = 0

    def getsockopt(self, *_args):
        peer = self.controller_identity
        return _CREDS.pack(peer.pid, peer.uid, peer.gid)

    def sendall(self, packet: bytes) -> None:
        if self._closed:
            raise OSError("closed")
        self.controller_end.deliver_ack(packet)

    def close(self):
        if not self._closed:
            self._closed = True
            self.close_count += 1


class ConnectedOfflineCredentialV2:
    """One connected controller/broker process graph for local acceptance."""

    def __init__(self, *, config: ControllerServiceConfig, broker_connection_type,
                 controller_epoch: str, broker_epoch: str,
                 interfaces: ControllerAuthorityInterfaces,
                 repository: DurableAuditRepositoryV2,
                 executor: CredentialEffectExecutorV2,
                 now_ms: int) -> None:
        if (type(config) is not ControllerServiceConfig
                or not callable(broker_connection_type)
                or not isinstance(interfaces, ControllerAuthorityInterfaces)
                or not isinstance(repository, DurableAuditRepositoryV2)
                or not isinstance(executor, CredentialEffectExecutorV2)):
            raise OfflineV2Error("offline_graph_invalid")
        self.config = config
        self.now_ms = now_ms
        self.controller_epoch = controller_epoch
        self.broker_epoch = broker_epoch
        self.interfaces = interfaces
        self.repository = repository
        self.executor = executor
        self.events: list[str] = []
        self.controller_fd_closed: list[int] = []
        self.broker_fd_closed: list[int] = []
        self.effect_result = None
        self.effect_error = None
        self.audit_errors: list[Exception] = []
        self.audit_receive_count = 2
        self.last_claim = None
        self.last_authorization = None
        self.last_lease_endpoint = None
        self.last_broker_lease_receipt = None
        self.last_lease_packet = None
        self._last_material_size = None
        self.broker_descriptor_closer = lambda fd: self.broker_fd_closed.append(fd)
        self.controller_transport, self.broker_transport = _seqpacket_pair(
            config.controller, config.broker)
        self.controller_service = PersistentControllerService(
            config, epoch_factory=lambda: controller_epoch,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        self.broker_session = broker_connection_type(
            self.broker_transport, config, broker_epoch, "broker-owner-0123456789",
        )
        self.guest_submit = None
        self.controller_session = None
        self.operation_authority = None
        self.audit_authority = None
        self.lifecycle_authority = None
        self.managed_lifecycle = None
        self.lifecycle_executor = None
        self._close_result = None
        self._close_error = None

    @staticmethod
    def _observer(expected: ProcessIdentity):
        def observe(pid, uid, gid):
            if (pid, uid, gid) != (expected.pid, expected.uid, expected.gid):
                raise ControllerServiceV2Error("peer_identity_mismatch")
            return expected
        return observe

    def authenticate(self) -> None:
        self.controller_service.start(platform="linux", enabled=True)
        errors = []

        def broker_handshake():
            try:
                self.broker_session.handshake(
                    observer=self._observer(self.config.controller), now_ms=self.now_ms,
                    monotonic=lambda: 0.0, so_peercred=1, scm_credentials=2,
                    scm_rights=socket.SCM_RIGHTS, closer=lambda fd: self.broker_fd_closed.append(fd),
                )
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=broker_handshake)
        worker.start()
        self.controller_service.attach(
            self.controller_transport, observer=self._observer(self.config.broker),
            now_ms=self.now_ms, monotonic=lambda: 0.0, so_peercred=1,
            scm_credentials=2, scm_rights=socket.SCM_RIGHTS,
            closer=lambda fd: self.controller_fd_closed.append(fd),
        )
        worker.join(2)
        if worker.is_alive() or errors:
            raise OfflineV2Error("handshake_failed")
        self.controller_session = self.controller_service.session
        self.operation_authority = ControllerOperationAuthorityV2(
            self.controller_session, self.interfaces,
            decision_id_factory=lambda: "decision-0123456",
            lease_id_factory=lambda: "lease-0123456789",
        )
        self.audit_authority = ControllerAuditAuthorityV2(
            self.controller_session, self.repository,
            commit_id_factory=iter(("commit-pre012345678", "commit-post01234567")).__next__,
            phase_id_factory=lambda: "audit-recovery0123456",
        )
        self.audit_authority.recover_unclosed_pre(now_ms=self.now_ms)
        common = dict(
            machine_id=self.config.machine_id, service_gid=self.config.broker.gid,
            policy_digest=self.config.policy_digest, egress_digest=self.config.egress_digest,
            broker_digest=self.config.broker_digest, proof_digest=self.config.proof_digest,
            effective_isolation_digest=self.config.effective_isolation_digest,
            evidence_id=self.config.evidence_id,
            controller_endpoint_identity="v2-controller.sock",
            lease_endpoint_identity="v2-lease.sock",
            guest_endpoint_identity="v2-guest.sock",
        )
        controller_plan = DerivedServiceConfigV2.derive(derived_config_document(
            component="controller", unit_identity=(
                f"sandbox-credential-controller-v2@{self.config.machine_id}.service"),
            service_uid=992, executable_digest=self.config.controller.executable_digest,
            config_identity="sandbox-v2-controller-config",
            peer_executable_digest=self.config.broker.executable_digest,
            peer_config_digest=self.config.broker.config_digest,
            own_config_digest=self.config.controller.config_digest, **common,
        ))
        broker_plan = DerivedServiceConfigV2.derive(derived_config_document(
            component="broker", unit_identity=(
                f"sandbox-credential-broker-v2@{self.config.machine_id}.service"),
            service_uid=993, executable_digest=self.config.broker.executable_digest,
            config_identity="sandbox-v2-broker-config",
            peer_executable_digest=self.config.controller.executable_digest,
            peer_config_digest=self.config.controller.config_digest,
            own_config_digest=self.config.broker.config_digest, **common,
        ))
        self.lifecycle_executor = _OfflineLifecycleExecutor()
        self.managed_lifecycle = ManagedCredentialLifecycleV2(
            controller_plan, broker_plan, self.lifecycle_executor,
            self.controller_session,
        )
        self.managed_lifecycle.start_closed()
        self.lifecycle_authority = ControllerLifecycleAuthorityV2(
            self.controller_session, plan_identity=self.managed_lifecycle.plan_identity,
        )
        self.guest_submit = self.broker_session.bind_guest_submit_capability_v2(
            self.broker_session.mint_guest_bridge_receipt_v2(),
            canonical_guest_validator=lambda _request: True,
            now_ms=lambda: self.now_ms,
        )
        self.events.append("authenticated")

    def _controller_receive(self, *, temporal_context=None):
        return self.controller_session.receive_frame(
            observer=self._observer(self.config.broker), now_ms=self.now_ms,
            so_peercred=1, scm_credentials=2, scm_rights=socket.SCM_RIGHTS,
            closer=lambda fd: self.controller_fd_closed.append(fd),
            temporal_context=temporal_context,
        )

    def _broker_receive(self, *, temporal_context=None):
        return self.broker_session.receive_frame(
            observer=self._observer(self.config.controller), now_ms=self.now_ms,
            so_peercred=1, scm_credentials=2, scm_rights=socket.SCM_RIGHTS,
            closer=lambda fd: self.broker_fd_closed.append(fd),
            temporal_context=temporal_context,
        )

    def activate(self) -> None:
        readiness = {**self.config.configured_digests(), "binding_ready": True,
                     "proof_ready": True, "egress_ready": True,
                     "sealed_expectations_ready": True, "active_operation_count": 0,
                     "drain_status": "drained"}
        self.lifecycle_authority.activate(
            now_ms=self.now_ms, expires_at_unix_ms=self.now_ms + 20_000,
            audit_authority=self.audit_authority, readiness_observer=lambda: readiness,
        )
        request = self._broker_receive()
        self.broker_session.handle_lifecycle_v2(request, now_ms=self.now_ms)
        ack = self._controller_receive(temporal_context={
            "request_receipt_unix_ms": self.now_ms,
            "activation_expires_at_unix_ms": self.now_ms + 20_000,
        })
        result = self.lifecycle_authority.acknowledge_activation(ack, now_ms=self.now_ms)
        if not result["ok"]:
            raise OfflineV2Error(result["code"])
        self.events.append("activated")

    def authorize(self, request: Mapping[str, Any], *, connection_identity: str) -> tuple[str, dict[str, Any]]:
        self.guest_submit(request, connection_identity=connection_identity)
        self.events.append("guest_submitted")
        poll = self.operation_authority.poll_claim(
            now_ms=self.now_ms, wait_deadline_unix_ms=self.now_ms + 1000)
        claim_request = self._broker_receive()
        self.broker_session.handle_authority_v2(claim_request, now_ms=self.now_ms)
        del poll
        claim = self._controller_receive(temporal_context={
            "original_guest_request_receipt_unix_ms": self.now_ms,
        })
        authorization = self.operation_authority.decide(claim, now_ms=self.now_ms)
        self.last_claim = dict(claim)
        self.last_authorization = dict(authorization)
        broker_authorization = self._broker_receive(temporal_context={
            "activation_expires_at_unix_ms": self.now_ms + 20_000,
            "request_deadline_unix_ms": self.now_ms + request["deadline_ms"],
        })
        self.broker_session.handle_authority_v2(broker_authorization, now_ms=self.now_ms)
        ack = self._controller_receive(temporal_context={
            "authorization_expires_at_unix_ms": authorization["authorization_expires_at_unix_ms"],
        })
        self.events.extend(("claimed", "authorized"))
        return claim["operation_id"], ack

    def dispatch_and_execute(self, operation_id: str, ack: Mapping[str, Any]) -> dict[str, Any]:
        endpoint = self.broker_session.lease_endpoint_v2(operation_id)
        self.last_lease_endpoint = endpoint
        controller_lease = _LeaseControllerEnd(self.config.broker)
        broker_lease = _LeaseBrokerEnd(self.config.controller, controller_lease)
        broker_receipt = self.broker_session._authenticated_lease_socket_receipt_v2(
            operation_id, broker_lease, observer=self._observer(self.config.controller),
            so_peercred=1,
        )
        self.last_broker_lease_receipt = broker_receipt
        controller_receipt = self.controller_session.accept_lease_socket(
            controller_lease, observer=self._observer(self.config.broker),
            so_peercred=1, scm_rights=socket.SCM_RIGHTS,
        )

        audit_errors = []
        self.audit_errors = audit_errors
        effect_worker = [None]

        def audit_pump():
            try:
                self.controller_transport.settimeout(3.0)
                for _ in range(self.audit_receive_count):
                    message = self._controller_receive()
                    self.audit_authority.handle(message, now_ms=self.now_ms + 1)
            except Exception as exc:
                audit_errors.append(exc)

        pump = threading.Thread(target=audit_pump)
        pump.start()

        def run_effect():
            try:
                self.effect_result = self.broker_session.execute_effect_v2(
                    operation_id,
                    audit_id_factory=lambda kind: {
                        "root": "audit-root0123456789",
                        "pre": "audit-pre0123456789",
                        "post": "audit-post012345678",
                    }[kind],
                    executor=self.executor,
                    observer=self._observer(self.config.controller),
                    so_peercred=1, scm_credentials=2, scm_rights=socket.SCM_RIGHTS,
                    closer=lambda fd: self.broker_fd_closed.append(fd),
                    monotonic=lambda: 0.0, wall_clock=lambda: self.now_ms + 1,
                )
            except Exception as exc:
                self.effect_error = exc

        def deliver(packet: bytes, descriptor: int) -> None:
            self.last_lease_packet = packet
            broker_descriptor = descriptor + 1000
            endpoint.accept(
                packet, [broker_descriptor],
                descriptor_observer=lambda fd: {
                    "anonymous_memfd": True, "close_on_exec": True,
                    "size": self._last_material_size,
                    "seals": {"write", "grow", "shrink", "seal"},
                },
                descriptor_closer=self.broker_descriptor_closer,
                now_ms=self.now_ms + 1, accepted_socket_receipt=broker_receipt,
            )
            effect_worker[0] = threading.Thread(target=run_effect)
            effect_worker[0].start()

        controller_lease.send_callback = deliver
        result = None
        dispatch_error = None
        try:
            result = self.operation_authority.acknowledge_and_dispatch(
                ack, now_ms=self.now_ms + 1, lease_sequence=1,
                memfd_factory=self._make_memfd, dispatcher=controller_receipt,
                descriptor_closer=lambda fd: self.controller_fd_closed.append(fd),
            )
        except Exception as exc:
            dispatch_error = exc
        if effect_worker[0] is not None:
            effect_worker[0].join(3)
        pump.join(3)
        if pump.is_alive() or audit_errors or self.effect_error is not None:
            raise OfflineV2Error("effect_graph_failed")
        if dispatch_error is not None:
            raise dispatch_error
        self.events.extend(("lease_bound", "pre_committed", "effect", "post_committed", "lease_acked"))
        return result

    def _make_memfd(self, material: bytearray) -> dict[str, Any]:
        self._last_material_size = len(material)
        return {"descriptor": 71, "descriptor_size": len(material),
                "anonymous_memfd": True, "close_on_exec": True,
                "seals": {"write", "grow", "shrink", "seal"}}

    def quiesce(self):
        self.lifecycle_authority.quiesce(
            now_ms=self.now_ms + 2, drain_deadline_unix_ms=self.now_ms + 5000,
            reason_code="operator_stop",
        )
        request = self._broker_receive()
        self.broker_session.handle_lifecycle_v2(request, now_ms=self.now_ms + 2)
        ack = self._controller_receive(temporal_context={
            "request_receipt_unix_ms": self.now_ms + 2,
            "drain_deadline_unix_ms": self.now_ms + 5000,
        })
        receipt = self.lifecycle_authority.acknowledge_quiesce(ack, now_ms=self.now_ms + 2)
        self.managed_lifecycle.retain_quiesce_ack(receipt)
        self.events.append("quiesced")
        return receipt

    def close(self) -> dict[str, Any]:
        if self._close_result is not None:
            if self._close_error is not None:
                raise OfflineV2Error(self._close_error)
            return dict(self._close_result)
        authority = self.operation_authority.close() if self.operation_authority else None
        lifecycle = None
        try:
            lifecycle = self.managed_lifecycle.stop() if self.managed_lifecycle else None
        except Exception as exc:
            self._close_error = getattr(exc, "code", "cleanup_incomplete")
        broker = self.broker_session.close("offline_complete")
        controller = self.controller_service.stop()
        self.events.extend(("broker_stopped", "controller_stopped", "cleanup_complete"))
        self._close_result = {
            "authority": authority, "lifecycle": lifecycle,
            "broker": broker, "controller": controller,
            "controller_socket_closes": self.controller_transport.close_count,
            "broker_socket_closes": self.broker_transport.close_count,
        }
        if self._close_error is not None:
            raise OfflineV2Error(self._close_error)
        return dict(self._close_result)


__all__ = [
    "ConnectedOfflineCredentialV2", "MemoryAuditRepositoryV2", "OfflineV2Error",
]
