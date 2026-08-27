"""Non-privileged runtime contracts for the closed native credential broker."""

from __future__ import annotations

from array import array
import hashlib
import json
import stat
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import unittest
from unittest import mock

from tests.test_credential_broker_service_contract import (
    BINDING, BROKER_DIGEST, CONFIG_DIGEST, EGRESS_DIGEST, EPOCH, EXECUTABLE_DIGEST,
    MACHINE, POLICY_DIGEST, FakeGuestConnection, descriptor_observation,
    guest_observation, guest_request, lease_frame, module, peer_identity,
    service_identity,
)


CONTROLLER = {
    "uid": 501,
    "pid": 9001,
    "process_start_identity": "9001:1234",
    "executable_digest": "9" * 64,
}


class ConfigKernel:
    def __init__(self, payload, *, uid=0, gid=501, mode=0o640, size=None):
        self.payload = payload
        self.offset = 0
        self.uid = uid
        self.gid = gid
        self.mode = mode
        self.size = len(payload) if size is None else size
        self.flags = None
        self.closed = []

    def open(self, path, flags):
        self.path = path
        self.flags = flags
        return 41

    def fstat(self, descriptor):
        assert descriptor == 41
        return SimpleNamespace(
            st_mode=stat.S_IFREG | self.mode, st_uid=self.uid, st_gid=self.gid,
            st_size=self.size,
        )

    def read(self, descriptor, size):
        assert descriptor == 41
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self, descriptor):
        self.closed.append(descriptor)


class RuntimeConnection:
    def __init__(self, broker, packet, *, pid=9001, uid=501,
                 packet_pid=None, packet_uid=None, descriptors=(), flags=0,
                 recv_error=None, send_error=None):
        self.broker = broker
        self.packet = packet
        self.pid = pid
        self.uid = uid
        self.packet_pid = pid if packet_pid is None else packet_pid
        self.packet_uid = uid if packet_uid is None else packet_uid
        self.descriptors = tuple(descriptors)
        self.flags = flags
        self.ancillary_override = None
        self.sent = []
        self.closed = False
        self.timeouts = []
        self.recv_error = recv_error
        self.send_error = send_error

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def getsockopt(self, _level, _kind, _size):
        return self.broker._PEER_CREDENTIALS.pack(self.pid, self.uid, 20)

    def recvmsg(self, _frame_size, _ancillary_size, _flags):
        if self.recv_error is not None:
            raise self.recv_error
        if self.ancillary_override is not None:
            return self.packet, self.ancillary_override, self.flags, None
        ancillary = []
        if self.descriptors:
            ancillary.append((
                self.broker.socket.SOL_SOCKET, self.broker.socket.SCM_RIGHTS,
                array("i", self.descriptors).tobytes(),
            ))
        if not self.descriptors:
            ancillary.append((
                self.broker.socket.SOL_SOCKET,
                getattr(self.broker.socket, "SCM_CREDENTIALS", 2),
                self.broker._PEER_CREDENTIALS.pack(
                    self.packet_pid, self.packet_uid, 20,
                ),
            ))
        return self.packet, ancillary, self.flags, None

    def sendall(self, payload):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)

    def close(self):
        self.closed = True


class RuntimeListener:
    def __init__(self, connections):
        self.connections = list(connections)
        self.options = []
        self.bound = None
        self.closed = False
        self.accept_calls = 0

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, address):
        self.bound = address

    def listen(self, backlog):
        self.backlog = backlog

    def accept(self):
        self.accept_calls += 1
        return self.connections.pop(0), None

    def close(self):
        self.closed = True


class FakeSelector:
    def __init__(self, events):
        self.events = list(events)
        self.closed = False

    def select(self, _timeout):
        events, self.events = self.events, []
        return [(SimpleNamespace(data=value), 1) for value in events]

    def close(self):
        self.closed = True


def coordinator(broker, *, audit=None, clock=None, callback=None):
    from sandbox.isolation.credential_request_broker import BrokerResponse

    callback = callback or (
        lambda request, _material: BrokerResponse(
            200, {"content-type": "application/json"}, b"{}",
            request["correlation_id"],
        )
    )
    return broker.CredentialBrokerCoordinator(
        service_identity(), controller=CONTROLLER,
        adapter=broker.OfflineTestOperationAdapter(callback, offline_test=True),
        descriptor_reader=lambda _descriptor, size: bytearray(size),
        descriptor_closer=lambda _descriptor: None,
        audit_sink=audit, clock=clock or (lambda: 1_900_000_000), enabled=True,
    )


def activate(broker_coordinator, *, sequence=1, connection="controller-runtime"):
    return broker_coordinator.handle_controller({
        "type": "ACTIVATE", "machine_id": MACHINE, "broker_epoch": EPOCH,
        "sequence": sequence,
    }, observed_peer=CONTROLLER, connection_identity=connection)


class TestCredentialBrokerServiceRuntime(unittest.TestCase):
    def test_config_is_fixed_canonical_nofollow_owned_and_secret_free(self):
        broker = module()
        value = {
            "version": 1,
            "service": {
                key: value for key, value in service_identity().items()
                if key not in {"pid", "process_start_identity"}
            },
            "controller": CONTROLLER,
            "control_plane_uid": 501,
        }
        payload = broker.canonical_runtime_config(value)
        digest = hashlib.sha256(payload).hexdigest()
        kernel = ConfigKernel(payload)
        loaded = broker.load_runtime_config(
            broker.runtime_config_path(MACHINE), machine_id=MACHINE,
            expected_group_gid=501,
            expected_digest=digest, kernel=kernel,
        )
        self.assertEqual(loaded, value)
        self.assertTrue(kernel.flags & getattr(broker.os, "O_NOFOLLOW", 0))
        self.assertEqual(kernel.closed, [41])

        for changed in (
            {**value, "secret": "forbidden"},
            {**value, "control_plane_uid": 502},
        ):
            with self.assertRaises(ValueError):
                broker.canonical_runtime_config(changed)
        with self.assertRaises(ValueError):
            broker.load_runtime_config(
                "/tmp/credential.json", machine_id=MACHINE, expected_group_gid=501,
                expected_digest=digest, kernel=ConfigKernel(payload),
            )
        for bad_kernel in (
            ConfigKernel(payload, uid=502), ConfigKernel(payload, mode=0o644),
            ConfigKernel(payload + b" "),
        ):
            with self.assertRaises(ValueError):
                broker.load_runtime_config(
                    broker.runtime_config_path(MACHINE), machine_id=MACHINE,
                    expected_group_gid=501,
                    expected_digest=digest, kernel=bad_kernel,
                )

    def test_entrypoint_and_lifecycle_observer_remain_closed_and_secret_free(self):
        broker = module()
        instance = coordinator(broker)
        observer = broker.BoundedLifecycleObserver(service_identity(), instance)
        self.assertEqual(observer.observe()["state"], "credential_pending")
        self.assertFalse(observer.observe()["admission_open"])
        self.assertEqual(observer.observe()["service_uid"], service_identity()["service_uid"])
        self.assertEqual(observer.observe()["config_digest"], CONFIG_DIGEST)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(broker.main(["--unexpected", "private-value"]), 4)
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "runtime_config_invalid")
        self.assertNotIn("private-value", repr(result))
        self.assertFalse(instance.admission_open)

    def test_controller_endpoint_authenticates_activate_quiesce_and_packet_peer(self):
        broker = module()
        observer = broker.LinuxPeerIdentityObserver(
            CONTROLLER, start_reader=lambda _pid: CONTROLLER["process_start_identity"],
            digest_reader=lambda _pid: CONTROLLER["executable_digest"],
        )
        self.assertEqual(observer(None, 9001, 501), CONTROLLER)
        self.assertEqual(observer(None, 9002, 501), {})
        instance = coordinator(broker)
        self.assertFalse(instance.admission_open)
        denied_claim = instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="claim-cannot-activate")
        self.assertEqual(denied_claim["code"], "controller_denied")
        self.assertFalse(instance.admission_open)
        activate_packet = broker.encode_controller_message({
            "type": "ACTIVATE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 1,
        })
        quiesce_packet = broker.encode_controller_message({
            "type": "QUIESCE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 2,
        })
        good = RuntimeConnection(broker, activate_packet)
        mismatch = RuntimeConnection(broker, activate_packet, packet_pid=9002)
        listener = RuntimeListener((good,))
        endpoint = broker.LinuxControllerEndpoint(
            service_identity(), instance, controller=CONTROLLER,
            identity_observer=lambda _connection, _pid, _uid: CONTROLLER,
            enabled=True, socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker, "_running_as_root", return_value=False), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker.socket, "SCM_CREDENTIALS", 2, create=True), \
                mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            self.assertTrue(endpoint.start()["ok"])
            endpoint._admission_open = True
            result = endpoint.receive_once()
        self.assertEqual(result, {"type": "ACTIVATE", "admission_open": True})
        self.assertEqual(broker.parse_controller_message(good.sent[0]), result)
        self.assertFalse(good.closed)

        denied_endpoint = broker.LinuxControllerEndpoint(
            service_identity(), instance, controller=CONTROLLER,
            identity_observer=lambda _connection, _pid, _uid: CONTROLLER,
            enabled=True, socket_factory=lambda *_args: RuntimeListener((mismatch,)),
        )
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker, "_running_as_root", return_value=False), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker.socket, "SCM_CREDENTIALS", 2, create=True), \
                mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            denied_endpoint.start()
            self.assertEqual(denied_endpoint.receive_once()["code"], "controller_denied")

        connection_identity = next(iter(endpoint.connections))
        good.packet = quiesce_packet
        with mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker.socket, "SCM_CREDENTIALS", 2, create=True):
            result = endpoint.receive_connection(connection_identity)
        self.assertEqual(result, {"type": "QUIESCE", "admission_open": False})
        self.assertFalse(instance.admission_open)

    def test_persistent_controller_disconnect_terminalizes_owned_claim(self):
        broker = module()
        instance = coordinator(broker)
        activate_packet = broker.encode_controller_message({
            "type": "ACTIVATE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 1,
        })
        connection = RuntimeConnection(broker, activate_packet)
        endpoint = broker.LinuxControllerEndpoint(
            service_identity(), instance, controller=CONTROLLER,
            identity_observer=lambda *_args: CONTROLLER, enabled=True,
        )
        endpoint.listener = RuntimeListener((connection,))
        with mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker.socket, "SCM_CREDENTIALS", 2, create=True):
            endpoint.receive_once()
            identity = next(iter(endpoint.connections))
            guest = FakeGuestConnection(b"")
            instance.retain_guest(guest, guest_observation(), guest_request())
            connection.packet = broker.encode_controller_message({
                "type": "CLAIM_NEXT", "machine_id": MACHINE,
                "broker_epoch": EPOCH, "sequence": 2,
            })
            self.assertEqual(endpoint.receive_connection(identity)["type"], "CLAIMED")
            connection.packet = b""
            self.assertEqual(endpoint.receive_connection(identity)["code"], "controller_denied")
        self.assertTrue(connection.closed)
        self.assertEqual(
            broker.parse_guest_terminal_result(guest.sent[0])["code"],
            "operation_cancelled",
        )

    def test_controller_rejects_zero_multiple_credentials_and_rights(self):
        broker = module()
        packet = broker.encode_controller_message({
            "type": "ACTIVATE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 1,
        })
        credential = (
            broker.socket.SOL_SOCKET, 2,
            broker._PEER_CREDENTIALS.pack(9001, 501, 20),
        )
        rights = (
            broker.socket.SOL_SOCKET, broker.socket.SCM_RIGHTS,
            array("i", (88,)).tobytes(),
        )
        for ancillary in ([], [credential, credential], [credential, rights]):
            with self.subTest(ancillary=len(ancillary)):
                instance = coordinator(broker)
                connection = RuntimeConnection(broker, packet)
                connection.ancillary_override = ancillary
                endpoint = broker.LinuxControllerEndpoint(
                    service_identity(), instance, controller=CONTROLLER,
                    identity_observer=lambda *_args: CONTROLLER, enabled=True,
                )
                endpoint.listener = RuntimeListener((connection,))
                endpoint._admission_open = True
                closed = []
                with mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                        mock.patch.object(broker.socket, "SCM_CREDENTIALS", 2, create=True), \
                        mock.patch.object(broker.os, "close", side_effect=closed.append):
                    self.assertEqual(endpoint.receive_once()["code"], "controller_denied")
                self.assertTrue(connection.closed)
                if rights in ancillary:
                    self.assertEqual(closed, [88])

    def test_claim_owner_is_internal_and_post_audit_failure_is_indeterminate(self):
        broker = module()
        audit = broker.BoundedAuditSink(
            lambda record: not (
                record["event"] == "credential_effect" and record["phase"] == "post"
            )
        )
        instance = coordinator(broker, audit=audit)
        activate(instance)
        guest = FakeGuestConnection(b"")
        instance.retain_guest(guest, guest_observation(), guest_request())
        claimed = instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="claim-owner-internal")
        frame = lease_frame(
            operation_id=claimed["operation_id"],
            request_digest=claimed["request_digest"],
        )
        with self.assertRaises(TypeError):
            instance.accept_descriptor(
                frame, 71, descriptor_observation(), dispatcher_peer=CONTROLLER,
                claim_owner="forged-owner",
            )
        with self.assertRaises(TypeError):
            instance.accept_descriptor(
                frame, 70, descriptor_observation(), dispatcher_peer=CONTROLLER,
                prebound=True,
            )
        with self.assertRaises(TypeError):
            instance.begin_lease(
                frame, dispatcher_peer=CONTROLLER, recorded=True,
            )
        result = instance.accept_descriptor(
            frame, 72, descriptor_observation(), dispatcher_peer=CONTROLLER,
        )
        self.assertEqual(result["outcome"], "indeterminate")
        terminal = broker.parse_guest_terminal_result(guest.sent[0])
        self.assertEqual(terminal["code"], "operation_indeterminate")
        self.assertNotIn("operation_id", repr(audit.records))
        self.assertNotIn("lease_id", repr(audit.records))

        effects = []
        pre_audit = broker.BoundedAuditSink(
            lambda record: record["event"] != "credential_effect",
        )
        refused_instance = coordinator(
            broker, audit=pre_audit,
            callback=lambda *_args: effects.append(True) or {"outcome": "completed"},
        )
        activate(refused_instance, connection="pre-audit-lifecycle")
        refused_guest = FakeGuestConnection(b"")
        refused_instance.retain_guest(
            refused_guest, guest_observation(connection_identity="pre-audit-guest"),
            guest_request(correlation_id="corr-pre-audit"),
        )
        refused_claim = refused_instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="pre-audit-owner")
        refused_frame = lease_frame(
            lease_id="lease-pre-audit-0123456789",
            operation_id=refused_claim["operation_id"],
            request_digest=refused_claim["request_digest"],
        )
        refused = refused_instance.accept_descriptor(
            refused_frame, 73, descriptor_observation(), dispatcher_peer=CONTROLLER,
        )
        self.assertEqual(refused["outcome"], "refused")
        self.assertEqual(effects, [])

    def test_quiesce_closes_guest_and_lease_before_any_accept_or_parse(self):
        broker = module()
        instance = coordinator(broker)
        guest_listener = RuntimeListener(())
        lease_listener = RuntimeListener(())
        guest_endpoint = broker.LinuxGuestEndpoint(
            service_identity(), registry=instance.registry,
            connection_observer=lambda _connection: guest_observation(),
            coordinator=instance, enabled=True,
        )
        guest_endpoint.listener = guest_listener
        lease_endpoint = broker.CoordinatorLeaseEndpoint(
            service_identity(), instance, control_plane_uid=501,
            peer_observer=lambda *_args: CONTROLLER, enabled=True,
        )
        lease_endpoint.listener = lease_listener
        instance.attach_endpoints(guest=guest_endpoint, lease=lease_endpoint)
        self.assertTrue(activate(instance)["admission_open"])
        self.assertTrue(guest_endpoint.admission_open)
        self.assertTrue(lease_endpoint.admission_open)
        quiesced = instance.handle_controller({
            "type": "QUIESCE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 2,
        }, observed_peer=CONTROLLER, connection_identity="controller-runtime")
        self.assertFalse(quiesced["admission_open"])
        self.assertEqual(guest_endpoint.receive_once()["code"], "guest_listener_closed")
        self.assertEqual(lease_endpoint.receive_once()["code"], "lease_channel_closed")
        self.assertEqual(guest_listener.accept_calls, 0)
        self.assertEqual(lease_listener.accept_calls, 0)

    def test_activation_audit_follows_endpoint_open_and_failure_stays_closed(self):
        broker = module()
        audit = broker.BoundedAuditSink()
        instance = coordinator(broker, audit=audit)
        opened = SimpleNamespace(
            coordinator=instance,
            activate_from_coordinator=lambda: {"ok": True},
            quiesce_from_coordinator=lambda: None,
        )
        failed = SimpleNamespace(
            coordinator=instance,
            activate_from_coordinator=lambda: {"ok": False},
            quiesce_from_coordinator=lambda: None,
        )
        instance.attach_endpoints(guest=opened, lease=failed)
        result = activate(instance)
        self.assertFalse(result["ok"])
        self.assertFalse(instance.admission_open)
        self.assertFalse(any(record.get("outcome") == "activated" for record in audit.records))

    def test_prepared_attempt_token_refuses_forgery_reuse_cross_lease_and_coordinator(self):
        broker = module()

        def claimed(instance, suffix):
            activate(instance, connection=f"lifecycle-{suffix}")
            guest = FakeGuestConnection(b"")
            instance.retain_guest(
                guest, guest_observation(connection_identity=f"guest-{suffix}-identity"),
                guest_request(correlation_id=f"corr-{suffix}"),
            )
            claim = instance.handle_controller({
                "type": "CLAIM_NEXT", "machine_id": MACHINE,
                "broker_epoch": EPOCH, "sequence": 1,
            }, observed_peer=CONTROLLER, connection_identity=f"owner-{suffix}")
            return lease_frame(
                lease_id=f"lease-{suffix}-0123456789",
                operation_id=claim["operation_id"], request_digest=claim["request_digest"],
            )

        first = coordinator(broker)
        frame = claimed(first, "token-a")
        prepared = first.begin_lease(frame, dispatcher_peer=CONTROLLER)["prepared_attempt"]
        forged = first._accept_prepared_descriptor(
            frame, 91, descriptor_observation(), prepared_attempt=object(),
        )
        self.assertEqual(forged["outcome"], "refused")
        cross_frame = {**frame, "lease_id": "lease-token-cross-012345"}
        cross = first._accept_prepared_descriptor(
            cross_frame, 92, descriptor_observation(), prepared_attempt=prepared,
        )
        self.assertEqual(cross["outcome"], "refused")
        reused = first._accept_prepared_descriptor(
            frame, 93, descriptor_observation(), prepared_attempt=prepared,
        )
        self.assertEqual(reused["outcome"], "refused")

        second = coordinator(broker)
        second_frame = claimed(second, "token-b")
        second_prepared = second.begin_lease(
            second_frame, dispatcher_peer=CONTROLLER,
        )["prepared_attempt"]
        cross_coordinator = first._accept_prepared_descriptor(
            frame, 94, descriptor_observation(), prepared_attempt=second_prepared,
        )
        self.assertEqual(cross_coordinator["outcome"], "refused")

    def test_prepared_attempt_binds_every_canonical_security_field(self):
        broker = module()
        mutations = {
            "protocol_version": 2,
            "broker_epoch": "f" * 32,
            "machine_id": "sb-fedcba987654",
            "binding_id": "binding-mutated-012345",
            "binding_version": 8,
            "policy_digest": "0" * 64,
            "egress_digest": "1" * 64,
            "broker_digest": "2" * 64,
            "request_digest": "3" * 64,
            "expires_at": 2_000_000_001,
            "descriptor_size": 33,
        }
        for index, (field, changed) in enumerate(mutations.items()):
            with self.subTest(field=field):
                instance = coordinator(broker)
                activate(instance, connection=f"frame-lifecycle-{index}")
                guest = FakeGuestConnection(b"")
                instance.retain_guest(
                    guest, guest_observation(connection_identity=f"frame-guest-{index}"),
                    guest_request(correlation_id=f"corr-frame-{index}"),
                )
                claim = instance.handle_controller({
                    "type": "CLAIM_NEXT", "machine_id": MACHINE,
                    "broker_epoch": EPOCH, "sequence": 1,
                }, observed_peer=CONTROLLER, connection_identity=f"frame-owner-{index}")
                frame = lease_frame(
                    lease_id=f"lease-frame-{index}-0123456789",
                    operation_id=claim["operation_id"], request_digest=claim["request_digest"],
                )
                prepared = instance.begin_lease(
                    frame, dispatcher_peer=CONTROLLER,
                )["prepared_attempt"]
                mutated = {**frame, field: changed}
                refused = instance._accept_prepared_descriptor(
                    mutated, 150 + index, descriptor_observation(),
                    prepared_attempt=prepared,
                )
                self.assertEqual(refused["outcome"], "refused")
                self.assertEqual(len(guest.sent), 1)
                terminal = broker.parse_guest_terminal_result(guest.sent[0])
                self.assertEqual(terminal["code"], "lease_unavailable")
                replay = instance.accept_descriptor(
                    frame, 170 + index, descriptor_observation(),
                    dispatcher_peer=CONTROLLER,
                )
                self.assertEqual(replay["outcome"], "refused")
                self.assertEqual(len(guest.sent), 1)

    def test_guest_disconnect_clears_only_its_prepared_attempt(self):
        broker = module()
        instance = coordinator(broker)
        activate(instance)
        prepared = []
        guests = []
        frames = []
        for index in range(2):
            guest = FakeGuestConnection(b"")
            identity = f"isolated-guest-{index}"
            instance.retain_guest(
                guest, guest_observation(connection_identity=identity),
                guest_request(correlation_id=f"corr-isolated-{index}"),
            )
            claim = instance.handle_controller({
                "type": "CLAIM_NEXT", "machine_id": MACHINE,
                "broker_epoch": EPOCH, "sequence": 1,
            }, observed_peer=CONTROLLER, connection_identity=f"isolated-owner-{index}")
            frame = lease_frame(
                lease_id=f"lease-isolated-{index}-012345",
                operation_id=claim["operation_id"], request_digest=claim["request_digest"],
            )
            prepared.append(instance.begin_lease(
                frame, dispatcher_peer=CONTROLLER,
            )["prepared_attempt"])
            guests.append(guest)
            frames.append(frame)

        instance.guest_disconnected("isolated-guest-0")
        first = instance._accept_prepared_descriptor(
            frames[0], 181, descriptor_observation(), prepared_attempt=prepared[0],
        )
        second = instance._accept_prepared_descriptor(
            frames[1], 182, descriptor_observation(), prepared_attempt=prepared[1],
        )
        self.assertEqual(first["outcome"], "refused")
        self.assertEqual(second["outcome"], "completed")
        self.assertEqual(len(guests[1].sent), 1)

    def test_coordinator_lease_endpoint_exact_fd_and_ack_cleanup(self):
        broker = module()
        instance = coordinator(broker)
        activate(instance)
        guest = FakeGuestConnection(b"")
        instance.retain_guest(guest, guest_observation(), guest_request())
        claimed = instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="lease-owner")
        frame = lease_frame(
            operation_id=claimed["operation_id"], request_digest=claimed["request_digest"],
        )
        connection = RuntimeConnection(
            broker, broker.encode_lease_frame(frame), descriptors=(77,),
        )
        listener = RuntimeListener((connection,))
        endpoint = broker.CoordinatorLeaseEndpoint(
            service_identity(), instance, control_plane_uid=501,
            peer_observer=lambda _connection, _pid, _uid: CONTROLLER,
            descriptor_observer=lambda _descriptor: descriptor_observation(),
            enabled=True, socket_factory=lambda *_args: listener,
        )
        closed = []
        instance.closer = lambda descriptor: closed.append(descriptor)
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker, "_running_as_root", return_value=False), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
            self.assertTrue(endpoint.start()["ok"])
            endpoint._admission_open = True
            result = endpoint.receive_once()
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(closed, [77])
        acknowledgement = json.loads(connection.sent[0])
        self.assertEqual(set(acknowledgement), {"lease_id", "outcome"})
        self.assertEqual(acknowledgement["outcome"], "completed")

    def test_descriptor_validation_exception_is_owned_terminal_and_acknowledged(self):
        broker = module()
        instance = coordinator(broker)
        activate(instance)
        guest = FakeGuestConnection(b"")
        instance.retain_guest(guest, guest_observation(), guest_request())
        claim = instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="validate-owner")
        frame = lease_frame(
            lease_id="lease-validate-raise-012345",
            operation_id=claim["operation_id"], request_digest=claim["request_digest"],
        )
        connection = RuntimeConnection(
            broker, broker.encode_lease_frame(frame), descriptors=(190,),
        )
        endpoint = broker.CoordinatorLeaseEndpoint(
            service_identity(), instance, control_plane_uid=501,
            peer_observer=lambda *_args: CONTROLLER,
            descriptor_observer=lambda _fd: descriptor_observation(), enabled=True,
        )
        endpoint.listener = RuntimeListener((connection,))
        endpoint._admission_open = True
        closed = []
        instance.closer = closed.append
        with mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker, "_validate_descriptor", side_effect=RuntimeError("boom")), \
                mock.patch.object(broker.os, "close", side_effect=closed.append):
            result = endpoint.receive_once()
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(closed, [190])
        self.assertEqual(len(guest.sent), 1)
        self.assertEqual(len(connection.sent), 1)
        self.assertEqual(json.loads(connection.sent[0])["outcome"], "refused")
        replay = instance.accept_descriptor(
            frame, 191, descriptor_observation(), dispatcher_peer=CONTROLLER,
        )
        self.assertEqual(replay["outcome"], "refused")
        self.assertEqual(closed, [190, 191])
        self.assertEqual(len(guest.sent), 1)

    def test_lease_endpoint_zero_and_multiple_fds_refuse_and_close_all(self):
        broker = module()
        for index, descriptors in enumerate(((), (80, 81))):
            with self.subTest(descriptors=descriptors):
                instance = coordinator(broker)
                activate(instance, connection=f"lifecycle-{index}")
                guest = FakeGuestConnection(b"")
                observation = guest_observation(connection_identity=f"fd-guest-{index}")
                instance.retain_guest(
                    guest, observation, guest_request(correlation_id=f"corr-fd-{index}"),
                )
                claimed = instance.handle_controller({
                    "type": "CLAIM_NEXT", "machine_id": MACHINE,
                    "broker_epoch": EPOCH, "sequence": 1,
                }, observed_peer=CONTROLLER, connection_identity=f"fd-owner-{index}")
                frame = lease_frame(
                    lease_id=f"lease-fd-{index}-0123456789",
                    operation_id=claimed["operation_id"],
                    request_digest=claimed["request_digest"],
                )
                connection = RuntimeConnection(
                    broker, broker.encode_lease_frame(frame), descriptors=descriptors,
                )
                endpoint = broker.CoordinatorLeaseEndpoint(
                    service_identity(), instance, control_plane_uid=501,
                    peer_observer=lambda *_args: CONTROLLER, enabled=True,
                )
                endpoint.listener = RuntimeListener((connection,))
                endpoint._admission_open = True
                closed = []
                with mock.patch.object(broker.os, "close", side_effect=closed.append), \
                        mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
                    result = endpoint.receive_once()
                self.assertEqual(result["outcome"], "refused")
                self.assertEqual(sorted(closed), sorted(descriptors))
                self.assertEqual(set(json.loads(connection.sent[0])), {"lease_id", "outcome"})
                self.assertEqual(len(guest.sent), 1)

    def test_lease_observer_failure_terminalizes_and_closes_fd_once(self):
        broker = module()
        instance = coordinator(broker)
        activate(instance)
        guest = FakeGuestConnection(b"")
        instance.retain_guest(guest, guest_observation(), guest_request())
        claimed = instance.handle_controller({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=CONTROLLER, connection_identity="observer-owner")
        frame = lease_frame(
            lease_id="lease-observer-fail-012345",
            operation_id=claimed["operation_id"],
            request_digest=claimed["request_digest"],
        )
        connection = RuntimeConnection(
            broker, broker.encode_lease_frame(frame), descriptors=(89,),
        )
        replay = RuntimeConnection(broker, broker.encode_lease_frame(frame), descriptors=(90,))
        endpoint = broker.CoordinatorLeaseEndpoint(
            service_identity(), instance, control_plane_uid=501,
            peer_observer=lambda *_args: CONTROLLER,
            descriptor_observer=lambda _fd: (_ for _ in ()).throw(OSError("metadata")),
            enabled=True,
        )
        endpoint.listener = RuntimeListener((connection, replay))
        endpoint._admission_open = True
        closed = []
        with mock.patch.object(broker.os, "close", side_effect=closed.append), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
            result = endpoint.receive_once()
            replay_result = endpoint.receive_once()
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(closed, [89, 90])
        self.assertEqual(json.loads(connection.sent[0])["outcome"], "refused")
        self.assertEqual(len(guest.sent), 1)
        self.assertEqual(replay_result["outcome"], "refused")

    def test_lease_endpoint_trailing_and_truncated_packets_close_descriptors(self):
        broker = module()
        for index, (packet_suffix, flags) in enumerate((
            (b"x", 0), (b"", getattr(broker.socket, "MSG_TRUNC", 0x20)),
        )):
            with self.subTest(index=index):
                instance = coordinator(broker)
                activate(instance, connection=f"malformed-lifecycle-{index}")
                frame = lease_frame(lease_id=f"lease-malformed-{index}-012345")
                connection = RuntimeConnection(
                    broker, broker.encode_lease_frame(frame) + packet_suffix,
                    descriptors=(82 + index,), flags=flags,
                )
                endpoint = broker.CoordinatorLeaseEndpoint(
                    service_identity(), instance, control_plane_uid=501,
                    peer_observer=lambda *_args: CONTROLLER, enabled=True,
                )
                endpoint.listener = RuntimeListener((connection,))
                endpoint._admission_open = True
                closed = []
                with mock.patch.object(broker.os, "close", side_effect=closed.append), \
                        mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
                    result = endpoint.receive_once()
                self.assertFalse(result["ok"])
                self.assertEqual(closed, [82 + index])

    def test_identifiable_truncation_and_trailing_terminalize_before_fd_use(self):
        broker = module()
        variants = (
            getattr(broker.socket, "MSG_TRUNC", 0x20),
            getattr(broker.socket, "MSG_CTRUNC", 0x08),
            0,
        )
        for index, flags in enumerate(variants):
            with self.subTest(flags=flags):
                instance = coordinator(broker)
                activate(instance, connection=f"trunc-lifecycle-{index}")
                guest = FakeGuestConnection(b"")
                instance.retain_guest(
                    guest, guest_observation(connection_identity=f"trunc-guest-{index}"),
                    guest_request(correlation_id=f"corr-trunc-{index}"),
                )
                claim = instance.handle_controller({
                    "type": "CLAIM_NEXT", "machine_id": MACHINE,
                    "broker_epoch": EPOCH, "sequence": 1,
                }, observed_peer=CONTROLLER, connection_identity=f"trunc-owner-{index}")
                frame = lease_frame(
                    lease_id=f"lease-trunc-{index}-0123456789",
                    operation_id=claim["operation_id"], request_digest=claim["request_digest"],
                )
                packet = broker.encode_lease_frame(frame) + (b"trailing" if flags == 0 else b"")
                connection = RuntimeConnection(broker, packet, descriptors=(95 + index,), flags=flags)
                endpoint = broker.CoordinatorLeaseEndpoint(
                    service_identity(), instance, control_plane_uid=501,
                    peer_observer=lambda *_args: CONTROLLER,
                    descriptor_observer=lambda _fd: self.fail("FD must not be inspected"),
                    enabled=True,
                )
                endpoint.listener = RuntimeListener((connection,))
                endpoint._admission_open = True
                closed = []
                with mock.patch.object(broker.os, "close", side_effect=closed.append), \
                        mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
                    result = endpoint.receive_once()
                self.assertEqual(result["outcome"], "refused")
                self.assertEqual(closed, [95 + index])
                self.assertEqual(len(guest.sent), 1)
                replay = instance.accept_descriptor(
                    frame, 120 + index, descriptor_observation(), dispatcher_peer=CONTROLLER,
                )
                self.assertEqual(replay["outcome"], "refused")

    def test_lease_receive_and_ack_timeout_cleanup_never_reopens(self):
        broker = module()
        for index, error_kind in enumerate(("recv", "send")):
            with self.subTest(error_kind=error_kind):
                instance = coordinator(broker)
                closed = []
                instance.closer = closed.append
                activate(instance, connection=f"timeout-lifecycle-{index}")
                guest = None
                if error_kind == "send":
                    guest = FakeGuestConnection(b"")
                    instance.retain_guest(
                        guest, guest_observation(connection_identity="timeout-guest"),
                        guest_request(correlation_id="corr-timeout"),
                    )
                    claim = instance.handle_controller({
                        "type": "CLAIM_NEXT", "machine_id": MACHINE,
                        "broker_epoch": EPOCH, "sequence": 1,
                    }, observed_peer=CONTROLLER, connection_identity="timeout-owner")
                    frame = lease_frame(
                        lease_id="lease-timeout-send-012345",
                        operation_id=claim["operation_id"],
                        request_digest=claim["request_digest"],
                    )
                else:
                    frame = lease_frame(lease_id="lease-timeout-recv-012345")
                connection = RuntimeConnection(
                    broker, broker.encode_lease_frame(frame), descriptors=(130 + index,),
                    recv_error=TimeoutError("receive") if error_kind == "recv" else None,
                    send_error=TimeoutError("send") if error_kind == "send" else None,
                )
                endpoint = broker.CoordinatorLeaseEndpoint(
                    service_identity(), instance, control_plane_uid=501,
                    peer_observer=lambda *_args: CONTROLLER, enabled=True,
                )
                endpoint.listener = RuntimeListener((connection,))
                endpoint._admission_open = True
                with mock.patch.object(broker.os, "close", side_effect=closed.append), \
                        mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True):
                    result = endpoint.receive_once()
                self.assertEqual(result["code"], "lease_channel_unavailable")
                self.assertEqual(connection.timeouts, [5.0])
                self.assertTrue(connection.closed)
                self.assertEqual(closed, [] if error_kind == "recv" else [130 + index])
                self.assertTrue(instance.admission_open)
                if guest is not None:
                    self.assertEqual(len(guest.sent), 1)
                    replay = instance.accept_descriptor(
                        frame, 140, descriptor_observation(), dispatcher_peer=CONTROLLER,
                    )
                    self.assertEqual(replay["outcome"], "refused")

    def test_revoke_expiry_and_close_prevent_new_accepts(self):
        broker = module()
        actions = (
            lambda instance: instance.revoke(BINDING, 3),
            lambda instance: instance.expire(1_900_000_100),
            lambda instance: instance.close(),
        )
        for index, action in enumerate(actions):
            with self.subTest(index=index):
                instance = coordinator(broker)
                guest_listener = RuntimeListener(())
                lease_listener = RuntimeListener(())
                guest_endpoint = broker.LinuxGuestEndpoint(
                    service_identity(), registry=instance.registry,
                    connection_observer=lambda _connection: guest_observation(),
                    coordinator=instance, enabled=True,
                )
                guest_endpoint.listener = guest_listener
                lease_endpoint = broker.CoordinatorLeaseEndpoint(
                    service_identity(), instance, control_plane_uid=501,
                    peer_observer=lambda *_args: CONTROLLER, enabled=True,
                )
                lease_endpoint.listener = lease_listener
                instance.attach_endpoints(guest=guest_endpoint, lease=lease_endpoint)
                activate(instance, connection=f"zero-accept-{index}")
                action(instance)
                guest_endpoint.receive_once()
                lease_endpoint.receive_once()
                self.assertEqual(guest_listener.accept_calls, 0)
                self.assertEqual(lease_listener.accept_calls, 0)

    def test_reactor_guest_trailing_deadline_bounds_and_shutdown_order(self):
        broker = module()
        now = [1_900_000_000]
        instance = coordinator(broker, clock=lambda: now[0])
        activate(instance)
        guest = FakeGuestConnection(b"")
        instance.retain_guest(guest, guest_observation(), guest_request(deadline_ms=1000))
        selector = FakeSelector((("guest", guest_observation()["connection_identity"]),))
        reactor = broker.CredentialBrokerServiceReactor(
            instance, selector=selector, guest_probe=lambda _identity: "trailing",
            clock=lambda: now[0], max_events=2,
        )
        self.assertTrue(reactor.run_once()["ok"])
        self.assertEqual(len(guest.sent), 1)
        self.assertEqual(
            broker.parse_guest_terminal_result(guest.sent[0])["code"], "guest_frame_invalid",
        )
        self.assertTrue(reactor.shutdown()["ok"])
        self.assertTrue(selector.closed)
        self.assertFalse(instance.admission_open)

        deadline_now = [1_900_000_000]
        deadline_instance = coordinator(broker, clock=lambda: deadline_now[0])
        activate(deadline_instance, connection="deadline-lifecycle")
        deadline_guest = FakeGuestConnection(b"")
        deadline_instance.retain_guest(
            deadline_guest,
            guest_observation(connection_identity="deadline-guest-identity"),
            guest_request(correlation_id="corr-deadline", deadline_ms=1000),
        )
        deadline_now[0] += 2
        deadline_reactor = broker.CredentialBrokerServiceReactor(
            deadline_instance, selector=FakeSelector(()), clock=lambda: deadline_now[0],
        )
        self.assertEqual(deadline_reactor.run_once()["expired"], 1)
        self.assertEqual(
            broker.parse_guest_terminal_result(deadline_guest.sent[0])["code"],
            "lease_expired",
        )
        self.assertFalse(deadline_instance.admission_open)

        eof_instance = coordinator(broker)
        activate(eof_instance, connection="eof-lifecycle")
        eof_guest = FakeGuestConnection(b"")
        eof_identity = "eof-guest-identity"
        eof_instance.retain_guest(
            eof_guest, guest_observation(connection_identity=eof_identity),
            guest_request(correlation_id="corr-eof"),
        )
        eof_reactor = broker.CredentialBrokerServiceReactor(
            eof_instance, selector=FakeSelector((("guest", eof_identity),)),
            guest_probe=lambda _identity: "eof",
        )
        self.assertTrue(eof_reactor.run_once()["ok"])
        self.assertEqual(eof_guest.sent, [])
        self.assertTrue(eof_guest.closed)

        overflow = coordinator(broker)
        activate(overflow, connection="overflow-lifecycle")
        overflow_reactor = broker.CredentialBrokerServiceReactor(
            overflow, selector=FakeSelector(("controller", "lease")), max_events=1,
        )
        self.assertEqual(overflow_reactor.run_once()["code"], "request_limit")
        self.assertFalse(overflow.admission_open)


if __name__ == "__main__":
    unittest.main()
