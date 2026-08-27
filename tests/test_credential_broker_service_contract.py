"""Offline contracts for the guarded native credential broker seams.

These tests are deliberately local and non-privileged.  They exercise pure
validation plus fake socket/kernel seams in ``native-credential-broker.py``;
they do not open real sockets, create real descriptors, invoke the helper, or
read real credential material.  They are not Ubuntu isolation proof.
"""

from __future__ import annotations

from array import array
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "tools" / "native-helper" / "native-credential-broker.py"

MACHINE = "sb-0123456789ab"
SIBLING = "sb-fedcba987654"
BINDING = "binding-0123456789ab"
LEASE = "lease-0123456789ab"
EPOCH = "epoch-0123456789ab"
NEXT_EPOCH = "epoch-fedcba987654"
CONNECTION = "kernel-connection-0123456789ab"
OPERATION = "op-0123456789abcdef"
REQUEST_DIGEST = "f" * 64
POLICY_DIGEST = "a" * 64
EGRESS_DIGEST = "b" * 64
BROKER_DIGEST = "c" * 64
EXECUTABLE_DIGEST = "d" * 64
CONFIG_DIGEST = "e" * 64
FORBIDDEN_MARKER = "synthetic-credential-must-never-escape"


def module():
    """Load the future standalone executable without executing its CLI."""
    if not BROKER.is_file():
        raise AssertionError(
            "T035 production broker executable is absent: "
            "tools/native-helper/native-credential-broker.py"
        )
    spec = importlib.util.spec_from_file_location(
        "native_credential_broker_contract", BROKER,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("T035 broker executable cannot be imported")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def service_identity(**overrides):
    value = {
        "machine_id": MACHINE,
        "broker_epoch": EPOCH,
        "pid": 4123,
        "process_start_identity": "4123:991827",
        "service_uid": 991,
        "unit_identity": "sandbox-credential-broker@sb-0123456789ab.service",
        "cgroup_identity": "/sandbox.slice/credential-broker/sb-0123456789ab",
        "executable_digest": EXECUTABLE_DIGEST,
        "config_digest": CONFIG_DIGEST,
        "policy_digest": POLICY_DIGEST,
        "egress_digest": EGRESS_DIGEST,
        "broker_digest": BROKER_DIGEST,
        "guest_interface": "ve-sb0123456789",
        "host_address": "10.203.0.1",
        "guest_address": "10.203.0.2",
        "guest_port": 18443,
    }
    value.update(overrides)
    return value


def guest_observation(**overrides):
    value = {
        "machine_id": MACHINE,
        "broker_epoch": EPOCH,
        "interface": "ve-sb0123456789",
        "local_address": "10.203.0.1",
        "local_port": 18443,
        "peer_address": "10.203.0.2",
        "forwarded": False,
        "loopback": False,
        "connection_identity": CONNECTION,
        "peer_verified": True,
    }
    value.update(overrides)
    return value


def guest_request(**overrides):
    value = {
        "machine_id": MACHINE,
        "binding_id": BINDING,
        "binding_version": 7,
        "scheme": "https",
        "host": "api.example.test",
        "port": 443,
        "method": "POST",
        "path": "/v1/items",
        "headers": {"accept": "application/json"},
        "body": b"{}",
        "content_type": "application/json",
        "deadline_ms": 5_000,
        "correlation_id": "corr-0123456789abcdef",
    }
    value.update(overrides)
    return value


def peer_identity(**overrides):
    value = {
        "pid": 4123,
        "uid": 991,
        "process_start_identity": "4123:991827",
        "unit_identity": "sandbox-credential-broker@sb-0123456789ab.service",
        "cgroup_identity": "/sandbox.slice/credential-broker/sb-0123456789ab",
        "executable_digest": EXECUTABLE_DIGEST,
        "config_digest": CONFIG_DIGEST,
    }
    value.update(overrides)
    return value


def lease_frame(**overrides):
    value = {
        "protocol_version": 1,
        "lease_id": LEASE,
        "broker_epoch": EPOCH,
        "machine_id": MACHINE,
        "binding_id": BINDING,
        "binding_version": 7,
        "policy_digest": POLICY_DIGEST,
        "egress_digest": EGRESS_DIGEST,
        "broker_digest": BROKER_DIGEST,
        "operation_id": OPERATION,
        "request_digest": REQUEST_DIGEST,
        "expires_at": 2_000_000_000,
        "descriptor_size": 32,
    }
    value.update(overrides)
    return value


def descriptor_observation(**overrides):
    value = {
        "descriptor_count": 1,
        "anonymous_memfd": True,
        "close_on_exec": True,
        "size": 32,
        "seals": ("write", "grow", "shrink", "seal"),
    }
    value.update(overrides)
    return value


def legacy_authorization(**overrides):
    value = {
        "lease_id": LEASE,
        "operation_id": OPERATION,
        "request_digest": REQUEST_DIGEST,
        "expires_at": 2_000_000_000,
    }
    value.update(overrides)
    return value


class FakeConnection:
    def __init__(self, broker, packet, *, uid=501, descriptor=77):
        self.broker = broker
        self.packet = packet
        self.uid = uid
        self.descriptor = descriptor
        self.recvmsg_called = False
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def getsockopt(self, _level, _kind, _size):
        return self.broker._PEER_CREDENTIALS.pack(9001, self.uid, 20)

    def recvmsg(self, _frame_size, _ancillary_size, _flags):
        self.recvmsg_called = True
        rights = array("i", (self.descriptor,)).tobytes()
        return self.packet, [(self.broker.socket.SOL_SOCKET,
                              self.broker.socket.SCM_RIGHTS, rights)], 0, None

    def sendall(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class FakeListener:
    def __init__(self, connections):
        self.connections = list(connections)
        self.bound = None
        self.timeout = None
        self.backlog = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, address):
        self.bound = address

    def listen(self, backlog):
        self.backlog = backlog

    def accept(self):
        return self.connections.pop(0), None

    def close(self):
        self.closed = True


class FakeGuestConnection:
    def __init__(self, packet, *, local=("10.203.0.1", 18443),
                 peer=("10.203.0.2", 43100)):
        self.buffer = bytearray(packet)
        self.local = local
        self.peer = peer
        self.recv_calls = 0
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def getsockname(self):
        return self.local

    def getpeername(self):
        return self.peer

    def recv(self, size):
        self.recv_calls += 1
        if not self.buffer:
            return b""
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def peek_trailing(self):
        return bool(self.buffer)

    def sendall(self, value):
        self.sent.append(value)

    def close(self):
        self.closed = True


class FakeGuestListener(FakeListener):
    def __init__(self, connections):
        super().__init__(connections)
        self.options = {}

    def setsockopt(self, level, kind, value):
        self.options[(level, kind)] = value

    def getsockopt(self, level, kind, _size):
        return self.options[(level, kind)]


class FakeLease:
    def __init__(self, size=32):
        self.size = size
        self.calls = 0

    def consume(self, callback):
        self.calls += 1
        return callback(bytearray(self.size))


class FakeKernel:
    def __init__(self, broker, *, acknowledgement=None, acknowledgement_bytes=None,
                 acknowledgement_error=None, peer=None, fail_send=False):
        self.broker = broker
        self.connection = object()
        self.acknowledgement = acknowledgement or {
            "lease_id": LEASE, "outcome": "completed",
        }
        self.acknowledgement_bytes = acknowledgement_bytes
        self.acknowledgement_error = acknowledgement_error
        self.peer = peer or {"pid": 4123, "uid": 991, "gid": 20}
        self.fail_send = fail_send
        self.created = []
        self.written = []
        self.materials = []
        self.sent = []
        self.closed = []

    def require(self):
        pass

    def connect(self, _service):
        return self.connection

    def peer_credentials(self, _connection):
        return dict(self.peer)

    def create_memfd(self):
        self.created.append(88)
        return 88

    def write_and_seal(self, descriptor, material):
        self.written.append((descriptor, len(material)))
        self.materials.append(material)

    def descriptor_observation(self, _descriptor):
        return descriptor_observation()

    def send_descriptor(self, connection, packet, descriptor):
        self.sent.append((connection, packet, descriptor))
        if self.fail_send:
            raise OSError("synthetic send failure")

    def receive_ack(self, _connection):
        if self.acknowledgement_error is not None:
            raise self.acknowledgement_error
        if self.acknowledgement_bytes is not None:
            return self.acknowledgement_bytes
        return json.dumps(
            self.acknowledgement, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")

    def close(self, value):
        self.closed.append(value)


class TestCredentialBrokerServiceContract(unittest.TestCase):
    def assert_refusal(self, value, code):
        self.assertFalse(value["ok"])
        self.assertEqual(value["code"], code)
        self.assertLessEqual(len(json.dumps(value, sort_keys=True)), 1024)

    def test_guest_transport_denies_cross_instance_and_non_exact_network_paths(self):
        broker = module()
        identity = service_identity()
        for observation, request in (
            (guest_observation(machine_id=SIBLING), guest_request()),
            (guest_observation(peer_address="10.203.0.6"), guest_request(machine_id=SIBLING)),
            (guest_observation(interface="lo", loopback=True), guest_request()),
            (guest_observation(forwarded=True), guest_request()),
            (guest_observation(), guest_request(broker_epoch=EPOCH)),
            (guest_observation(), guest_request(transport_capability="guest-token")),
        ):
            with self.subTest(observation=observation):
                result = broker.validate_guest_admission(
                    identity, observation, request,
                )
                self.assert_refusal(result, "transport_denied")

    def test_lease_dispatch_requires_exact_machine_epoch_and_process_identity(self):
        broker = module()
        identity = service_identity()
        for changed in (
            {"pid": 4124},
            {"process_start_identity": "4123:991828"},
            {"unit_identity": "foreign.service"},
            {"cgroup_identity": "/foreign.slice"},
            {"executable_digest": "f" * 64},
            {"config_digest": "0" * 64},
        ):
            with self.subTest(changed=changed):
                result = broker.validate_dispatch_peer(
                    identity, peer_identity(**changed), lease_frame(),
                )
                self.assert_refusal(result, "broker_identity_mismatch")
        self.assert_refusal(
            broker.validate_dispatch_peer(
                identity, peer_identity(), lease_frame(machine_id=SIBLING),
            ),
            "lease_identity_mismatch",
        )
        self.assert_refusal(
            broker.validate_dispatch_peer(
                identity, peer_identity(), lease_frame(broker_epoch="stale-epoch"),
            ),
            "broker_epoch_stale",
        )

    def test_broker_epoch_rotation_refuses_stale_or_replayed_connection_state(self):
        broker = module()
        state = broker.BrokerEpochState(EPOCH)
        accepted = state.admit(EPOCH, CONNECTION, peer_verified=True)
        self.assertTrue(accepted["ok"])
        self.assert_refusal(
            state.admit(EPOCH, CONNECTION, peer_verified=True),
            "connection_replayed",
        )
        state.rotate(NEXT_EPOCH)
        self.assert_refusal(
            state.admit(EPOCH, "kernel-connection-stale", peer_verified=True),
            "broker_epoch_stale",
        )
        self.assert_refusal(
            state.admit(NEXT_EPOCH, "kernel-connection-unverified", peer_verified=False),
            "connection_peer_denied",
        )
        self.assertTrue(state.admit(
            NEXT_EPOCH, "kernel-connection-fedcba987654", peer_verified=True,
        )["ok"])
        self.assertNotIn(EPOCH, repr(state))
        self.assertNotIn(NEXT_EPOCH, repr(state))
        self.assertNotIn(CONNECTION, repr(state))

    def test_lease_id_is_consumed_before_descriptor_read_and_never_retried(self):
        broker = module()
        receiver = broker.LeaseReceiver(
            service_identity(), control_plane_uid=501, clock=lambda: 1_900_000_000,
        )
        descriptor = descriptor_observation()
        first = receiver.accept(
            lease_frame(), descriptor, dispatcher_peer={"uid": 501},
        )
        self.assert_refusal(first, "lease_handoff_required")
        self.assertNotIn("descriptor", first)
        self.assert_refusal(
            receiver.accept(lease_frame(), descriptor, dispatcher_peer={"uid": 501}),
            "lease_replayed",
        )
        self.assertTrue(receiver.consumed(LEASE))

    def test_descriptor_seals_size_and_frame_shape_fail_closed(self):
        broker = module()
        cases = (
            (lease_frame(), descriptor_observation(descriptor_count=0), "descriptor_missing"),
            (lease_frame(), descriptor_observation(descriptor_count=2), "descriptor_extra"),
            (lease_frame(), descriptor_observation(anonymous_memfd=False), "descriptor_type_invalid"),
            (lease_frame(), descriptor_observation(seals=("write", "grow", "shrink")), "descriptor_seals_invalid"),
            (lease_frame(), descriptor_observation(size=31), "descriptor_size_mismatch"),
            (lease_frame(descriptor_size=1_048_577), descriptor_observation(size=1_048_577), "descriptor_oversize"),
            ({**lease_frame(), "truncated": True}, descriptor_observation(), "frame_invalid"),
            ({**lease_frame(), "trailing_bytes": 1}, descriptor_observation(), "frame_invalid"),
            ({**lease_frame(), "unknown": "field"}, descriptor_observation(), "frame_invalid"),
        )
        for frame, descriptor, code in cases:
            with self.subTest(code=code):
                self.assert_refusal(
                    broker.validate_lease_frame(
                        service_identity(), frame, descriptor,
                        dispatcher_peer={"uid": 501}, control_plane_uid=501,
                        now=1_900_000_000,
                    ),
                    code,
                )

    def test_binary_frame_is_canonical_bounded_and_rejects_trailing_data(self):
        broker = module()
        packet = broker.encode_lease_frame(lease_frame())
        self.assertLessEqual(len(packet), broker.MAX_FRAME_BYTES)
        self.assertEqual(broker.parse_lease_frame(packet), lease_frame())
        for malformed in (
            packet + b"x",
            packet[:-1],
            b"WRONG" + packet[5:],
            packet[:broker._FRAME_HEADER.size] + b" "
            + packet[broker._FRAME_HEADER.size:],
        ):
            with self.subTest(malformed=malformed[:12]):
                with self.assertRaises(ValueError):
                    broker.parse_lease_frame(malformed)
        with self.assertRaises(ValueError):
            broker.encode_lease_frame({**lease_frame(), "unknown": True})

    def test_linux_endpoint_is_inert_closed_and_requires_explicit_opt_in(self):
        broker = module()
        factory = mock.Mock(side_effect=AssertionError("socket factory must stay unused"))
        endpoint = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(),
            socket_factory=factory,
        )
        self.assertFalse(endpoint.admission_open)
        self.assert_refusal(endpoint.start(), "lease_channel_closed")
        self.assertFalse(factory.called)
        self.assertNotIn(EPOCH, repr(endpoint))

        drift_factory = mock.Mock(side_effect=AssertionError("drift must precede socket creation"))
        drift_registry = broker.LegacyPendingLeaseRegistry(service_identity())
        drifted = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(pid=4124), enabled=True,
            registry=drift_registry,
            adapter=broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            socket_factory=drift_factory,
        )
        with mock.patch.object(broker, "_require_linux_transport"):
            self.assert_refusal(drifted.start(), "broker_identity_mismatch")
        self.assertFalse(drift_factory.called)

        root_factory = mock.Mock(side_effect=AssertionError("root must precede socket creation"))
        root_registry = broker.LegacyPendingLeaseRegistry(service_identity())
        root_endpoint = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(), enabled=True,
            registry=root_registry,
            adapter=broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            socket_factory=root_factory,
        )
        with mock.patch.object(broker.os, "geteuid", return_value=0):
            self.assert_refusal(root_endpoint.start(), "root_execution_denied")
        self.assertFalse(root_factory.called)

    def test_fake_linux_endpoint_checks_peer_and_consumes_before_descriptor_read(self):
        broker = module()
        packet = broker.encode_lease_frame(lease_frame())
        wrong_peer = FakeConnection(broker, packet, uid=502, descriptor=76)
        invalid_descriptor = FakeConnection(broker, packet, descriptor=77)
        replay = FakeConnection(broker, packet, descriptor=78)
        listener = FakeListener((wrong_peer, invalid_descriptor, replay))
        registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        endpoint = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(), enabled=True,
            registry=registry,
            adapter=broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            socket_factory=lambda *_args: listener,
            clock=lambda: 1_900_000_000,
        )
        invalid = descriptor_observation(seals=("write", "grow", "shrink"))
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(
                    broker, "_linux_descriptor_observation", return_value=invalid,
                ) as observe, \
                mock.patch.object(broker.os, "close") as close:
            self.assertTrue(endpoint.start()["ok"])
            self.assertIsInstance(listener.bound, bytes)
            self.assertTrue(listener.bound.startswith(b"\0"))
            self.assertTrue(endpoint.open_admission(service_identity())["ok"])
            self.assert_refusal(endpoint.receive_once(), "dispatcher_denied")
            self.assertFalse(wrong_peer.recvmsg_called)
            self.assert_refusal(
                endpoint.receive_once(), "descriptor_seals_invalid",
            )
            self.assert_refusal(endpoint.receive_once(), "lease_replayed")
            self.assertEqual(observe.call_count, 1)
            self.assertEqual(close.call_args_list, [mock.call(77), mock.call(78)])
        endpoint.close()
        self.assertTrue(listener.closed)
        self.assert_refusal(endpoint.start(), "lease_channel_closed")

    def test_legacy_endpoint_refuses_wrong_operation_before_descriptor_read(self):
        broker = module()
        connection = FakeConnection(
            broker, broker.encode_lease_frame(
                lease_frame(operation_id="op-wrong-operation"),
            ), descriptor=77,
        )
        listener = FakeListener((connection,))
        registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(registry.register(
            guest_request(), guest_observation(), legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        endpoint = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(), registry=registry,
            adapter=broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ), enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_900_000_000,
        )
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(broker, "_linux_descriptor_observation") as observe, \
                mock.patch.object(broker.os, "close") as close:
            self.assertTrue(endpoint.start()["ok"])
            self.assertTrue(endpoint.open_admission(service_identity())["ok"])
            self.assert_refusal(endpoint.receive_once(), "lease_identity_mismatch")
            self.assertFalse(observe.called)
            self.assertEqual(close.call_args_list, [mock.call(77)])
        endpoint.close()

    def test_fake_private_veth_listener_binds_exact_interface_before_guest_parse(self):
        broker = module()
        packet = broker.encode_guest_request(guest_request())
        wrong = FakeGuestConnection(packet, peer=("10.203.0.6", 43100))
        trailing = FakeGuestConnection(packet + b"x")
        good = FakeGuestConnection(packet)
        listener = FakeGuestListener((wrong, trailing, good))
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )

        def observe(connection):
            self.assertEqual(connection.recv_calls, 0)
            return guest_observation()

        endpoint = broker.LinuxGuestEndpoint(
            service_identity(), registry=registry,
            connection_observer=observe,
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_900_000_000,
        )
        root_listener = broker.LinuxGuestEndpoint(
            service_identity(), registry=broker.PendingOperationRegistry(service_identity()),
            connection_observer=observe,
            enabled=True,
            socket_factory=mock.Mock(
                side_effect=AssertionError("root must precede guest socket creation"),
            ),
        )
        with mock.patch.object(broker.os, "geteuid", return_value=0):
            self.assert_refusal(root_listener.start(), "root_execution_denied")
        with mock.patch.object(broker, "_require_linux_guest_listener"), \
                mock.patch.object(broker.socket, "SO_BINDTODEVICE", 25, create=True):
            self.assertTrue(endpoint.start()["ok"])
            self.assertEqual(listener.bound, ("10.203.0.1", 18443))
            self.assertEqual(listener.backlog, broker.MAX_ACTIVE_REQUESTS)
            bound = listener.options[(broker.socket.SOL_SOCKET, 25)]
            self.assertEqual(bound.rstrip(b"\0"), b"ve-sb0123456789")
            self.assertTrue(endpoint.open_admission()["ok"])
            self.assert_refusal(endpoint.receive_once(), "transport_denied")
            self.assertEqual(wrong.recv_calls, 0)
            self.assert_refusal(endpoint.receive_once(), "guest_frame_invalid")
            unavailable = endpoint.receive_once()
            self.assert_refusal(unavailable, "guest_coordinator_unavailable")
            self.assertEqual(
                broker.parse_guest_terminal_result(good.sent[-1]), unavailable,
            )
            self.assertEqual(unavailable["correlation_id"], guest_request()["correlation_id"])
            self.assertNotIn("lease_id", unavailable)
            self.assertNotIn("operation_id", unavailable)
            self.assertEqual(registry.count, 0)
        endpoint.close()

    def test_fake_dispatcher_proves_peer_sends_once_and_closes_every_path(self):
        broker = module()
        kernel = FakeKernel(broker)
        lease = FakeLease()
        dispatcher = broker.LinuxLeaseDispatcher(
            service_identity(), broker_identity_observer=lambda _connection: peer_identity(),
            kernel=kernel, clock=lambda: 1_900_000_000, enabled=True,
        )
        result = dispatcher.dispatch(lease_frame(), lease)
        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(lease.calls, 1)
        self.assertEqual(kernel.created, [88])
        self.assertEqual(kernel.written, [(88, 32)])
        self.assertTrue(all(value == 0 for value in kernel.materials[0]))
        self.assertEqual(len(kernel.sent), 1)
        self.assertEqual(kernel.closed, [88, kernel.connection])
        self.assert_refusal(
            dispatcher.dispatch(lease_frame(), FakeLease()), "lease_replayed",
        )
        self.assertEqual(len(kernel.sent), 1)

        denied_kernel = FakeKernel(broker, peer={"pid": 9999, "uid": 991, "gid": 20})
        denied_lease = FakeLease()
        denied = broker.LinuxLeaseDispatcher(
            service_identity(), broker_identity_observer=lambda _connection: peer_identity(),
            kernel=denied_kernel, clock=lambda: 1_900_000_000, enabled=True,
        ).dispatch(lease_frame(), denied_lease)
        self.assert_refusal(denied, "broker_identity_mismatch")
        self.assertEqual(denied_lease.calls, 0)
        self.assertEqual(denied_kernel.created, [])
        self.assertEqual(denied_kernel.closed, [denied_kernel.connection])

        failed_kernel = FakeKernel(broker, fail_send=True)
        failed = broker.LinuxLeaseDispatcher(
            service_identity(), broker_identity_observer=lambda _connection: peer_identity(),
            kernel=failed_kernel, clock=lambda: 1_900_000_000, enabled=True,
        ).dispatch(lease_frame(), FakeLease())
        self.assert_refusal(failed, "lease_channel_unavailable")
        self.assertEqual(failed_kernel.closed, [88, failed_kernel.connection])
        self.assertTrue(all(value == 0 for value in failed_kernel.materials[0]))

        ack_cases = (
            FakeKernel(broker, acknowledgement={"lease_id": LEASE, "outcome": "accepted"}),
            FakeKernel(broker, acknowledgement_bytes=b""),
            FakeKernel(broker, acknowledgement_error=TimeoutError("synthetic timeout")),
        )
        for ack_kernel in ack_cases:
            with self.subTest(ack_kernel=ack_kernel):
                ack_dispatcher = broker.LinuxLeaseDispatcher(
                    service_identity(),
                    broker_identity_observer=lambda _connection: peer_identity(),
                    kernel=ack_kernel, clock=lambda: 1_900_000_000, enabled=True,
                )
                uncertain = ack_dispatcher.dispatch(lease_frame(), FakeLease())
                self.assert_refusal(uncertain, "lease_ack_indeterminate")
                self.assertEqual(uncertain["lease_id"], LEASE)
                self.assertEqual(uncertain["outcome"], "indeterminate")
                self.assertEqual(len(ack_kernel.sent), 1)
                self.assertEqual(ack_kernel.closed, [88, ack_kernel.connection])
                self.assert_refusal(
                    ack_dispatcher.dispatch(lease_frame(), FakeLease()),
                    "lease_replayed",
                )
                self.assertEqual(len(ack_kernel.sent), 1)

        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker.os, "geteuid", return_value=0):
            with self.assertRaises(RuntimeError):
                broker.LinuxKernelFacade().require()

    def test_receiver_rendezvous_handoff_wipes_buffer_and_acks_terminal_outcome(self):
        broker = module()
        registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        packet = broker.encode_lease_frame(lease_frame())
        connection = FakeConnection(broker, packet, descriptor=79)
        listener = FakeListener((connection,))
        observed_buffers = []

        def read_once(_descriptor, size):
            value = bytearray([7] * size)
            observed_buffers.append(value)
            return value

        def handoff(request, material):
            self.assertEqual(request, guest_request())
            self.assertEqual(len(material), 32)
            return {"outcome": "completed", "ignored": FORBIDDEN_MARKER}

        endpoint = broker.LinuxLeaseEndpoint(
            service_identity(), control_plane_uid=501,
            identity_observer=lambda: service_identity(), registry=registry,
            adapter=broker.OfflineTestOperationAdapter(handoff, offline_test=True),
            descriptor_reader=read_once,
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_900_000_000,
        )
        with mock.patch.object(broker, "_require_linux_transport"), \
                mock.patch.object(broker.socket, "SO_PEERCRED", 17, create=True), \
                mock.patch.object(
                    broker, "_linux_descriptor_observation",
                    return_value=descriptor_observation(),
                ), mock.patch.object(broker.os, "close") as close:
            self.assertTrue(endpoint.start()["ok"])
            self.assertTrue(endpoint.open_admission(service_identity())["ok"])
            result = endpoint.receive_once()
            self.assertTrue(result["ok"])
            self.assertEqual(result["outcome"], "completed")
            self.assertNotIn("accepted", repr(result))
            self.assertNotIn(FORBIDDEN_MARKER, repr(result))
            self.assertEqual(registry.tracker.count, 0)
            self.assertEqual(close.call_args_list, [mock.call(79)])
        self.assertTrue(observed_buffers)
        self.assertTrue(all(value == 0 for value in observed_buffers[0]))
        acknowledgement = json.loads(connection.sent[-1])
        self.assertEqual(acknowledgement, {"lease_id": LEASE, "outcome": "completed"})
        endpoint.close()

    def test_simultaneous_dispatch_of_one_lease_has_one_send_and_one_handoff(self):
        broker = module()
        kernel = FakeKernel(broker)
        lease = FakeLease()
        dispatcher = broker.LinuxLeaseDispatcher(
            service_identity(), broker_identity_observer=lambda _connection: peer_identity(),
            kernel=kernel, clock=lambda: 1_900_000_000, enabled=True,
        )
        barrier = threading.Barrier(3)
        results = []

        def invoke():
            barrier.wait()
            results.append(dispatcher.dispatch(lease_frame(), lease))

        threads = [threading.Thread(target=invoke) for _index in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(value.get("ok") is True for value in results), 1)
        self.assertEqual(sum(value.get("code") == "lease_replayed" for value in results), 1)
        self.assertEqual(lease.calls, 1)
        self.assertEqual(len(kernel.sent), 1)

    def test_pending_lifecycle_enforces_sixteen_revoke_expiry_and_restart(self):
        broker = module()
        registry = broker.LegacyPendingLeaseRegistry(service_identity())
        for index in range(broker.MAX_ACTIVE_REQUESTS):
            result = registry.register(
                guest_request(), guest_observation(),
                legacy_authorization(
                    lease_id=f"lease-{index:012d}",
                    operation_id=f"op-{index:012d}",
                    request_digest=f"{index:064x}",
                ),
                now=1_900_000_000,
            )
            self.assertTrue(result["ok"])
        self.assertEqual(registry.tracker.count, 16)
        self.assert_refusal(registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(
                lease_id="lease-over-limit", operation_id="op-over-limit",
                request_digest="9" * 64,
            ),
            now=1_900_000_000,
        ), "request_limit")
        self.assertEqual(len(registry.revoke(BINDING, 7)), 16)
        self.assertEqual(registry.tracker.count, 0)

        self.assertTrue(registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(expires_at=1_900_000_001),
            now=1_900_000_000,
        )["ok"])
        self.assertEqual(registry.expire(1_900_000_001), (LEASE,))
        self.assertEqual(registry.tracker.count, 0)

        self.assertTrue(registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        restarted = service_identity(
            broker_epoch=NEXT_EPOCH, pid=4124,
            process_start_identity="4124:991900",
        )
        self.assertEqual(registry.restart(restarted), (LEASE,))
        self.assertEqual(registry.tracker.count, 0)

        mismatch_registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(mismatch_registry.register(
            guest_request(), guest_observation(),
            legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        pending, mismatch = mismatch_registry.consume(
            lease_frame(binding_version=8), now=1_900_000_000,
        )
        self.assertIsNone(pending)
        self.assert_refusal(mismatch, "lease_identity_mismatch")
        self.assertEqual(mismatch_registry.tracker.count, 0)

        digest_registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(digest_registry.register(
            guest_request(), guest_observation(), legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        pending, mismatch = digest_registry.consume(
            lease_frame(request_digest="0" * 64), now=1_900_000_000,
        )
        self.assertIsNone(pending)
        self.assert_refusal(mismatch, "lease_identity_mismatch")
        self.assertEqual(digest_registry.tracker.count, 0)

        operation_registry = broker.LegacyPendingLeaseRegistry(service_identity())
        self.assertTrue(operation_registry.register(
            guest_request(), guest_observation(), legacy_authorization(),
            now=1_900_000_000,
        )["ok"])
        pending, mismatch = operation_registry.consume(
            lease_frame(operation_id="op-wrong-operation"), now=1_900_000_000,
        )
        self.assertIsNone(pending)
        self.assert_refusal(mismatch, "lease_identity_mismatch")
        self.assertEqual(operation_registry.tracker.count, 0)

        tracker = broker.ActiveRequestTracker()
        self.assertTrue(tracker.begin(LEASE, BINDING, 7, 2_000_000_000))
        self.assertTrue(tracker.activate(LEASE))
        self.assertEqual(tracker.revoke(BINDING, 7), (LEASE,))
        self.assertTrue(tracker.cancelled(LEASE))
        self.assertFalse(tracker.drain(0))
        tracker.finish(LEASE)
        self.assertTrue(tracker.drain(0))

    def test_status_acknowledgement_and_errors_are_bounded_non_secret_schemas(self):
        broker = module()
        status = broker.service_status(
            service_identity(), state="credential_pending", admission_open=False,
        )
        acknowledgement = broker.lease_acknowledgement(LEASE, "completed")
        error = broker.bounded_error("frame_invalid", FORBIDDEN_MARKER * 1000)
        for value in (status, acknowledgement, error):
            encoded = json.dumps(value, sort_keys=True)
            self.assertLessEqual(len(encoded), 1024)
            self.assertNotIn(FORBIDDEN_MARKER, encoded)
            self.assertNotIn("credential", set(value))
        self.assertEqual(set(acknowledgement), {"lease_id", "outcome"})
        self.assertEqual(
            broker.lease_acknowledgement(LEASE, "accepted")["outcome"], "refused",
        )
        self.assertFalse(status["admission_open"])
        with mock.patch.object(broker.sys, "platform", "linux"):
            live = broker.live_transport_status()
        self.assert_refusal(live, "live_transport_unproven")

    def test_pure_guest_operation_state_claim_bind_and_result_hides_internal_ids(self):
        broker = module()
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        request = guest_request()
        submitted = registry.submit(
            request, guest_observation(), now=1_900_000_000,
        )
        self.assertEqual(submitted, {
            "ok": True, "state": "credential_pending",
            "correlation_id": request["correlation_id"],
        })
        self.assertNotIn("operation", repr(submitted))
        self.assertNotIn("lease", repr(submitted))

        controller = {
            "uid": 501,
            "pid": 9001,
            "process_start_identity": "9001:1234",
            "executable_digest": "9" * 64,
        }
        channel = broker.ControllerClaimChannel(service_identity(), registry, controller)
        claimed = channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-connection-1")
        self.assertEqual(claimed["type"], "CLAIMED")
        self.assertEqual({
            key: claimed[key] for key in (
                "scheme", "host", "port", "method", "path", "body_bytes",
                "content_type", "deadline_ms", "correlation_id",
            )
        }, {
            "scheme": "https", "host": "api.example.test", "port": 443,
            "method": "POST", "path": "/v1/items", "body_bytes": 2,
            "content_type": "application/json", "deadline_ms": 5_000,
            "correlation_id": request["correlation_id"],
        })
        self.assertGreater(claimed["header_bytes"], 0)
        self.assertNotIn("headers", claimed)
        self.assertNotIn("body", claimed)
        self.assertEqual(
            broker.parse_controller_message(broker.encode_controller_message(claimed)),
            claimed,
        )
        digest = broker.guest_request_digest(request)
        self.assertEqual(claimed["request_digest"], digest)
        frame = lease_frame(operation_id=OPERATION, request_digest=digest)
        self.assertTrue(registry.bind_lease(
            frame, owner="controller-connection-1", now=1_900_000_000,
        )["ok"])

        from sandbox.isolation.credential_request_broker import BrokerResponse
        response = BrokerResponse(
            status=201, headers={"content-type": "application/json"}, body=b"{\"ok\":true}",
            correlation_id=request["correlation_id"],
        )
        completed = registry.complete(OPERATION, digest, response)
        self.assertTrue(completed["ok"])
        public = registry.guest_result(CONNECTION, consume=True)
        self.assertEqual(public, completed)
        self.assertNotIn(OPERATION, repr(public))
        self.assertNotIn(LEASE, repr(public))
        self.assertNotIn(digest, repr(public))

    def test_full_guest_frame_enforces_https_bounds_and_security_headers(self):
        broker = module()
        request = guest_request()
        packet = broker.encode_guest_request(request)
        self.assertEqual(broker.parse_guest_request(packet), request)
        for invalid in (
            guest_request(scheme="http"),
            guest_request(headers={"authorization": "guest-value"}),
            guest_request(headers={"x-api-key": "guest-value"}),
            guest_request(headers={"x-long": "x" * (broker.MAX_GUEST_HEADERS_BYTES + 1)}),
            guest_request(body=b"x" * (broker.MAX_GUEST_BODY_BYTES + 1)),
            guest_request(deadline_ms=30_001),
        ):
            with self.subTest(invalid=repr(invalid)[:80]):
                with self.assertRaises(ValueError):
                    broker.encode_guest_request(invalid)

    def test_controller_claim_is_owned_once_and_disconnect_is_terminal(self):
        broker = module()
        operation_ids = iter((OPERATION, "op-fedcba9876543210"))
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: next(operation_ids),
        )
        self.assertTrue(registry.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        self.assertTrue(registry.submit(
            guest_request(correlation_id="corr-second"),
            guest_observation(connection_identity="kernel-connection-second"),
            now=1_900_000_000,
        )["ok"])
        controller = {
            "uid": 501, "pid": 9001, "process_start_identity": "9001:1234",
            "executable_digest": "9" * 64,
        }
        channel = broker.ControllerClaimChannel(service_identity(), registry, controller)
        first = channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-one")
        second = channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-two")
        self.assertNotEqual(first["operation_id"], second["operation_id"])
        self.assert_refusal(channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 2,
        }, observed_peer={**controller, "pid": 9002},
            connection_identity="controller-one"), "controller_denied")
        self.assertEqual(channel.disconnect("controller-one"), (first["operation_id"],))
        result = registry.guest_result(CONNECTION)
        self.assert_refusal(result, "operation_cancelled")
        self.assertNotIn(first["operation_id"], repr(result))

    def test_sixteen_simultaneous_guest_requests_get_distinct_private_operations(self):
        broker = module()
        id_lock = threading.Lock()
        next_id = 0

        def id_factory():
            nonlocal next_id
            with id_lock:
                next_id += 1
                return f"op-{next_id:016d}"

        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=id_factory,
        )
        barrier = threading.Barrier(broker.MAX_ACTIVE_REQUESTS + 1)
        results = []
        result_lock = threading.Lock()

        def submit(index):
            barrier.wait()
            result = registry.submit(
                guest_request(correlation_id=f"corr-{index}"),
                guest_observation(connection_identity=f"connection-{index:016d}"),
                now=1_900_000_000,
            )
            with result_lock:
                results.append(result)

        threads = [
            threading.Thread(target=submit, args=(index,))
            for index in range(broker.MAX_ACTIVE_REQUESTS)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), broker.MAX_ACTIVE_REQUESTS)
        self.assertTrue(all(result.get("ok") is True for result in results))
        self.assertEqual(registry.count, broker.MAX_ACTIVE_REQUESTS)
        self.assert_refusal(registry.submit(
            guest_request(correlation_id="corr-over-limit"),
            guest_observation(connection_identity="connection-over-limit"),
            now=1_900_000_000,
        ), "request_limit")

    def test_lease_binding_refuses_request_digest_or_claim_owner_mismatch(self):
        broker = module()
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(registry.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        self.assertEqual(registry.claim_next(
            "controller-one", now=1_900_000_000,
        )["type"], "CLAIMED")
        digest = broker.guest_request_digest(guest_request())
        self.assert_refusal(registry.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest=digest),
            owner="controller-two", now=1_900_000_000,
        ), "operation_not_pending")
        self.assert_refusal(registry.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest="0" * 64),
            owner="controller-one", now=1_900_000_000,
        ), "request_digest_mismatch")

    def test_controller_refusal_is_distinct_from_disconnect_indeterminate(self):
        broker = module()
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(registry.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        controller = {
            "uid": 501, "pid": 9001, "process_start_identity": "9001:1234",
            "executable_digest": "9" * 64,
        }
        channel = broker.ControllerClaimChannel(service_identity(), registry, controller)
        claimed = channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-one")
        refused = channel.handle({
            "type": "REFUSE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 2, "operation_id": claimed["operation_id"],
            "request_digest": claimed["request_digest"], "code": "binding_expired",
        }, observed_peer=controller, connection_identity="controller-one")
        self.assertEqual(refused, {"type": "REFUSE", "code": "binding_expired"})
        public = registry.guest_result(CONNECTION)
        self.assert_refusal(public, "binding_expired")
        self.assertNotEqual(public.get("code"), "operation_indeterminate")

    def test_controller_rejects_unknown_type_and_unreviewed_refusal_code(self):
        broker = module()
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(registry.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        controller = {
            "uid": 501, "pid": 9001, "process_start_identity": "9001:1234",
            "executable_digest": "9" * 64,
        }
        channel = broker.ControllerClaimChannel(
            service_identity(), registry, controller, clock=lambda: 1_900_000_000,
        )
        self.assert_refusal(channel.handle({
            "type": "COMPLETE", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-one"),
            "controller_message_invalid")
        claimed = channel.handle({
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }, observed_peer=controller, connection_identity="controller-one")
        self.assertEqual(claimed["type"], "CLAIMED")
        self.assert_refusal(channel.handle({
            "type": "REFUSE", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 2, "operation_id": claimed["operation_id"],
            "request_digest": claimed["request_digest"], "code": "arbitrary_code",
        }, observed_peer=controller, connection_identity="controller-one"),
            "controller_message_invalid")
        with self.assertRaises(ValueError):
            broker.encode_controller_message({
                "type": "REFUSE", "machine_id": MACHINE, "broker_epoch": EPOCH,
                "sequence": 2, "operation_id": claimed["operation_id"],
                "request_digest": claimed["request_digest"], "code": "arbitrary_code",
            })

    def test_operation_deadline_and_disconnect_certainty_follow_lease_boundary(self):
        broker = module()
        expired = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(expired.submit(
            guest_request(deadline_ms=1_000), guest_observation(), now=100,
        )["ok"])
        self.assertEqual(expired.claim_next("controller-one", now=101), {
            "type": "NO_PENDING",
        })
        self.assert_refusal(expired.guest_result(CONNECTION), "lease_expired")

        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(registry.submit(
            guest_request(deadline_ms=5_000), guest_observation(), now=100,
        )["ok"])
        claimed = registry.claim_next("controller-one", now=101)
        digest = claimed["request_digest"]
        self.assert_refusal(registry.bind_lease(
            lease_frame(
                operation_id=OPERATION, request_digest=digest,
                expires_at=2_000_000_000,
            ), owner="controller-one", now=105,
        ), "lease_expired")

        post_lease = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(post_lease.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        claimed = post_lease.claim_next("controller-one", now=1_900_000_001)
        digest = claimed["request_digest"]
        self.assertTrue(post_lease.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest=digest),
            owner="controller-one", now=1_900_000_002,
        )["ok"])
        self.assertEqual(post_lease.controller_disconnected("controller-one"), (OPERATION,))
        self.assert_refusal(post_lease.guest_result(CONNECTION), "operation_indeterminate")
        post_lease.guest_result(CONNECTION, consume=True)
        self.assertEqual(post_lease.count, 0)

    def test_registry_close_refuses_before_lease_and_is_indeterminate_after(self):
        broker = module()
        pending = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(pending.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        pending.close()
        self.assert_refusal(pending.guest_result(CONNECTION), "operation_cancelled")

        bound = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(bound.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        claimed = bound.claim_next("controller-one", now=1_900_000_001)
        self.assertTrue(bound.bind_lease(
            lease_frame(
                operation_id=OPERATION, request_digest=claimed["request_digest"],
            ), owner="controller-one", now=1_900_000_002,
        )["ok"])
        bound.close()
        self.assert_refusal(bound.guest_result(CONNECTION), "operation_indeterminate")

    def test_arbitrary_error_completion_becomes_indeterminate(self):
        broker = module()
        registry = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(registry.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        claimed = registry.claim_next("controller-one", now=1_900_000_001)
        digest = claimed["request_digest"]
        self.assertTrue(registry.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest=digest),
            owner="controller-one", now=1_900_000_002,
        )["ok"])
        self.assert_refusal(registry.complete(
            OPERATION, digest, {"ok": False, "code": "binding_expired"},
        ), "operation_indeterminate")
        self.assert_refusal(registry.guest_result(CONNECTION), "operation_indeterminate")

        from sandbox.isolation.credential_request_broker import BrokerResponse
        invalid = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(invalid.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        claimed = invalid.claim_next("controller-one", now=1_900_000_001)
        digest = claimed["request_digest"]
        self.assertTrue(invalid.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest=digest),
            owner="controller-one", now=1_900_000_002,
        )["ok"])
        malformed = BrokerResponse(
            status=200, headers={"authorization": "not-reviewed"}, body=b"ok",
            correlation_id=guest_request()["correlation_id"],
        )
        self.assert_refusal(
            invalid.complete(OPERATION, digest, malformed),
            "operation_indeterminate",
        )

        correlation = broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )
        self.assertTrue(correlation.submit(
            guest_request(), guest_observation(), now=1_900_000_000,
        )["ok"])
        claimed = correlation.claim_next("controller-one", now=1_900_000_001)
        digest = claimed["request_digest"]
        self.assertTrue(correlation.bind_lease(
            lease_frame(operation_id=OPERATION, request_digest=digest),
            owner="controller-one", now=1_900_000_002,
        )["ok"])
        mismatched = BrokerResponse(
            status=200, headers={"content-type": "application/json"}, body=b"{}",
            correlation_id="corr-wrong-request",
        )
        self.assert_refusal(
            correlation.complete(OPERATION, digest, mismatched),
            "operation_indeterminate",
        )
        self.assert_refusal(
            correlation.guest_result(CONNECTION), "operation_indeterminate",
        )

    def test_canonical_terminal_result_serializes_binary_without_internal_ids(self):
        broker = module()
        success = {
            "ok": True,
            "status": 200,
            "headers": {"content-type": "application/octet-stream"},
            "body": b"\x00\xffbinary",
            "correlation_id": "corr-binary",
        }
        packet = broker.encode_guest_terminal_result(success)
        self.assertEqual(broker.parse_guest_terminal_result(packet), success)
        self.assertNotIn(OPERATION.encode("ascii"), packet)
        self.assertNotIn(LEASE.encode("ascii"), packet)

        error = {
            **broker.bounded_error("operation_indeterminate"),
            "correlation_id": "corr-error",
        }
        encoded_error = broker.encode_guest_terminal_result(error)
        self.assertEqual(broker.parse_guest_terminal_result(encoded_error), error)
        with self.assertRaises(ValueError):
            broker.encode_guest_terminal_result({**success, "lease_id": LEASE})
        with self.assertRaises(ValueError):
            broker.encode_guest_terminal_result({
                **success, "headers": {"authorization": "not-allowed"},
            })
        for header in ("x-unreviewed", "set-cookie"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                broker.encode_guest_terminal_result({
                    **success, "headers": {header: "not-reviewed"},
                })
        with self.assertRaises(ValueError):
            broker.encode_guest_terminal_result({
                **success, "headers": {"X-Test": "one", "x-test": "two"},
            })
        with self.assertRaises(ValueError):
            broker.encode_guest_terminal_result({
                **error, "code": "made_up_guest_code",
                "message": "credential broker request refused",
            })

    def test_operation_adapter_refuses_direct_upstream_and_unintegrated_broker(self):
        broker = module()
        with self.assertRaises(ValueError):
            broker.CredentialOperationAdapter(lambda *_args: None)
        with self.assertRaises(ValueError):
            broker.OfflineTestOperationAdapter(lambda *_args: None)
        fake = broker.OfflineTestOperationAdapter(
            lambda _request, _material: {"outcome": "refused"}, offline_test=True,
        )
        self.assertEqual(fake.execute(
            guest_request(), bytearray(b"x"), machine_id=MACHINE,
        ), {"outcome": "refused"})
        with self.assertRaises(ValueError):
            broker.LinuxLeaseEndpoint(
                service_identity(), control_plane_uid=501,
                identity_observer=lambda: service_identity(),
                registry=broker.LegacyPendingLeaseRegistry(service_identity()),
                adapter=fake, enabled=True,
            )

        claim = {
            "type": "CLAIM_NEXT", "machine_id": MACHINE,
            "broker_epoch": EPOCH, "sequence": 1,
        }
        self.assertEqual(
            broker.parse_controller_message(broker.encode_controller_message(claim)), claim,
        )
        with self.assertRaises(ValueError):
            broker.parse_controller_message(broker.encode_controller_message(claim) + b"x")

        from sandbox.isolation.credential_request_broker import CredentialRequestBroker
        from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream
        with self.assertRaises(ValueError):
            broker.CredentialOperationAdapter(object.__new__(CredentialRequestBroker))
        with self.assertRaises(ValueError):
            broker.CredentialOperationAdapter(object.__new__(VerifiedHttpsUpstream))

        production_placeholder = object.__new__(broker.CredentialOperationAdapter)
        with self.assertRaises(ValueError):
            broker.LinuxLeaseEndpoint(
                service_identity(), control_plane_uid=501,
                identity_observer=lambda: service_identity(),
                registry=broker.LegacyPendingLeaseRegistry(service_identity()),
                adapter=production_placeholder, enabled=True,
            )

    def test_helper_boundary_has_only_fixed_digest_bound_lifecycle_verbs(self):
        broker = module()
        expected = {
            "credential-broker-start", "credential-broker-status",
            "credential-broker-stop",
        }
        self.assertEqual(set(broker.HELPER_VERBS), expected)
        for verb in sorted(expected):
            argv = broker.helper_argv(
                "/fixed/native-helper", verb, service_identity(),
            )
            self.assertEqual(tuple(argv), (
                "/fixed/native-helper", verb, MACHINE,
                POLICY_DIGEST, EGRESS_DIGEST, BROKER_DIGEST,
            ))
        for forbidden in (
            "credential-broker-send", "credential-broker-lease",
            "credential-broker-descriptor", "credential-broker-endpoint",
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    broker.helper_argv("/fixed/native-helper", forbidden, service_identity())

    def test_argv_environment_unit_config_and_output_never_carry_material(self):
        broker = module()
        rendered = broker.render_inert_service_contract(
            service_identity(), executable="/usr/libexec/sandbox/native-credential-broker",
        )
        surface = json.dumps(rendered, sort_keys=True)
        self.assertNotIn(FORBIDDEN_MARKER, surface)
        self.assertEqual(set(rendered), {
            "argv", "environment", "unit", "config", "status", "stdout", "stderr",
        })
        for key in ("argv", "environment", "unit", "config", "status", "stdout", "stderr"):
            self.assertNotIn("secret", repr(rendered[key]).lower())
            self.assertNotIn("credential_value", repr(rendered[key]).lower())
        self.assertNotIn("SCM_RIGHTS", surface)
        self.assertNotIn("memfd", surface.lower())
        self.assertNotIn("lease", repr(rendered["argv"]).lower())
        self.assertNotIn("lease", repr(rendered["environment"]).lower())
        tainted_identity = service_identity(credential_value=FORBIDDEN_MARKER)
        with self.assertRaises(ValueError):
            broker.render_inert_service_contract(
                tainted_identity,
                executable="/usr/libexec/sandbox/native-credential-broker",
            )
        with self.assertRaises(ValueError):
            broker.helper_argv(
                "/fixed/native-helper", "credential-broker-start", tainted_identity,
            )


if __name__ == "__main__":
    unittest.main()
