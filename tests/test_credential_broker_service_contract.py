"""Offline contracts for the guarded native credential broker seams.

These tests are deliberately local and non-privileged.  They exercise pure
validation plus fake socket/kernel seams in ``native-credential-broker.py``;
they do not open real sockets, create real descriptors, invoke the helper, or
read real credential material.  They are not Ubuntu isolation proof.
"""

from __future__ import annotations

from array import array
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import time
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
        # A response header outside the reviewed allowlist is refused rather
        # than copied to the guest: it is dropped, and the terminal result is
        # still delivered. The encoder remains the fail-closed backstop for
        # anything that could ever bypass this filter.
        malformed = BrokerResponse(
            status=200, headers={"authorization": "not-reviewed"}, body=b"ok",
            correlation_id=guest_request()["correlation_id"],
        )
        delivered = invalid.complete(OPERATION, digest, malformed)
        self.assertTrue(delivered["ok"])
        self.assertEqual(delivered["headers"], {})
        self.assertNotIn("not-reviewed", repr(delivered))
        with self.assertRaises(ValueError):
            broker.encode_guest_terminal_result({
                "ok": True, "status": 200, "headers": {"authorization": "x"},
                "body": b"ok", "correlation_id": guest_request()["correlation_id"],
            })

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


# --- T035 coordinator contracts ---------------------------------------------
#
# These exercise the runnable coordinator's pure and fake-socket seams. They
# prove ordering, authentication, one-use consumption, typed execution, and
# terminal delivery locally. They open no real socket, create no real
# descriptor, and are not Ubuntu isolation, systemd, or live credential proof.


def credential_binding(**overrides):
    from sandbox.isolation.credential_binding import CredentialBinding

    value = {
        "binding_id": BINDING, "instance_id": MACHINE,
        "source_reference": "fixture/API_TOKEN", "policy_digest": POLICY_DIGEST,
        "egress_digest": EGRESS_DIGEST, "broker_digest": BROKER_DIGEST,
        "scheme": "https", "host": "api.example.test", "port": 443,
        "method": "POST", "path": "/v1/items", "auth_form": "bearer",
        "expires_at": "2999-01-01T00:00:00Z", "owner": "project:fixture",
        "version": 7, "state": "ready",
    }
    value.update(overrides)
    return CredentialBinding(**value)


class FakeTransport:
    def __init__(self, *, status=200, body=b'{"ok":true}', headers=None, error=None):
        self.calls = []
        self.status = status
        self.body = body
        self.headers = headers if headers is not None else {
            "content-type": "application/json", "set-cookie": "session=leak",
        }
        self.error = error

    def request(self, method, path, headers, body, timeout):
        self.calls.append((method, path, dict(headers), body, timeout))
        if self.error is not None:
            raise self.error
        return {"status": self.status, "headers": dict(self.headers), "body": self.body}

    def close(self):
        pass


def descriptor_broker(broker, *, transport=None, proof=True, egress=True,
                      binding=None):
    """Compose the descriptor-backed request broker with injected fakes."""
    from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream

    transport = transport or FakeTransport()
    item = binding if binding is not None else credential_binding()
    upstream = VerifiedHttpsUpstream(
        resolver=lambda _host: ("93.184.216.34",),
        connector=lambda *_args: transport,
    )
    request_broker = broker.descriptor_backed_request_broker(
        machine_id=MACHINE, binding_loader=lambda _identity: item,
        proof=lambda _binding: proof, egress=lambda _binding: egress,
        upstream=upstream, owner="project:fixture",
    )
    return request_broker, transport, item


class FakeControllerConnection:
    """A seqpacket peer that yields fixed packets and records replies."""

    def __init__(self, broker, packets, *, uid=501, pid=9001):
        self.broker = broker
        self.packets = list(packets)
        self.uid = uid
        self.pid = pid
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def getsockopt(self, _level, _kind, _size):
        return self.broker._PEER_CREDENTIALS.pack(self.pid, self.uid, 20)

    def recv(self, _size):
        return self.packets.pop(0) if self.packets else b""

    def sendall(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class TestCredentialBrokerCoordinator(unittest.TestCase):
    def assert_refusal(self, value, code):
        self.assertFalse(value["ok"])
        self.assertEqual(value["code"], code)

    def setUp(self):
        self.broker = module()
        # Local runs are unprivileged and often not Linux. Patching only the
        # platform gates keeps the production guard intact while the fake
        # socket seams exercise ordering and authentication.
        for name in ("_require_linux_transport", "_require_linux_guest_listener"):
            patcher = mock.patch.object(self.broker, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("SO_BINDTODEVICE", 25), ("SO_PEERCRED", 17)):
            option = mock.patch.object(self.broker.socket, name, value, create=True)
            option.start()
            self.addCleanup(option.stop)

    # --- descriptor-backed resolver and typed execution ---------------------

    def test_descriptor_resolver_is_one_use_and_has_no_plaintext_return(self):
        resolver = self.broker.DescriptorLeaseResolver()
        binding = credential_binding()
        with self.assertRaises(RuntimeError):
            resolver.issue(binding)
        with self.assertRaises(RuntimeError):
            resolver.resolve("fixture/API_TOKEN")
        resolver.bind(BINDING, 7, bytearray(b"synthetic-material"))
        lease = resolver.issue(binding)
        seen = []
        self.assertEqual(lease.consume(lambda value: seen.append(bytes(value)) or "done"),
                         "done")
        self.assertEqual(seen, [b"synthetic-material"])
        with self.assertRaises(RuntimeError):
            lease.consume(lambda _value: None)
        with self.assertRaises(RuntimeError):
            resolver.issue(binding)

    def test_descriptor_resolver_refuses_a_mismatched_binding(self):
        resolver = self.broker.DescriptorLeaseResolver()
        resolver.bind(BINDING, 7, bytearray(b"synthetic-material"))
        with self.assertRaises(RuntimeError):
            resolver.issue(credential_binding(version=8))

    def test_adapter_requires_a_descriptor_backed_broker_and_verified_upstream(self):
        from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream

        upstream = VerifiedHttpsUpstream(
            resolver=lambda _host: ("93.184.216.34",),
            connector=lambda *_args: FakeTransport(),
        )
        with self.assertRaises(ValueError):
            self.broker.CredentialOperationAdapter(upstream)
        with self.assertRaises(ValueError):
            self.broker.CredentialOperationAdapter(object())
        with self.assertRaises(ValueError):
            self.broker.CredentialOperationAdapter(
                self.broker.CredentialRequestBrokerFactoryProbe
                if hasattr(self.broker, "CredentialRequestBrokerFactoryProbe") else object(),
            )
        request_broker, _transport, _binding = descriptor_broker(self.broker)
        adapter = self.broker.CredentialOperationAdapter(request_broker)
        self.assertIsNotNone(adapter)

    def test_adapter_executes_typed_upstream_through_the_broker_gates(self):
        from sandbox.isolation.credential_request_broker import BrokerResponse

        request_broker, transport, _binding = descriptor_broker(self.broker)
        adapter = self.broker.CredentialOperationAdapter(request_broker)
        request = self.broker._canonical_guest_request(
            guest_request(host="api.example.test", path="/v1/items", method="POST"),
        )
        result = adapter.execute(request, bytearray(b"synthetic-material"),
                                 machine_id=MACHINE)
        self.assertIsInstance(result, BrokerResponse)
        self.assertEqual(result.status, 200)
        # The upstream saw the applied credential; the guest-visible result
        # never carries it and the disallowed response header is dropped.
        self.assertIn("authorization", {key.lower() for key in transport.calls[0][2]})
        public = self.broker._guest_public_result(result)
        self.assertNotIn("set-cookie", public["headers"])
        self.assertNotIn("synthetic-material", repr(public))

    def test_adapter_refuses_before_lease_use_and_is_indeterminate_after(self):
        request_broker, _transport, _binding = descriptor_broker(
            self.broker, proof=False,
        )
        adapter = self.broker.CredentialOperationAdapter(request_broker)
        request = self.broker._canonical_guest_request(guest_request(
            host="api.example.test", path="/v1/items", method="POST",
        ))
        refused = adapter.execute(request, bytearray(b"x" * 8), machine_id=MACHINE)
        self.assertEqual(refused["outcome"], "refused")
        self.assertIn(refused["code"], self.broker.GUEST_ERROR_CODES)

        broken = FakeTransport(error=OSError("synthetic upstream failure"))
        request_broker, _transport, _binding = descriptor_broker(
            self.broker, transport=broken,
        )
        adapter = self.broker.CredentialOperationAdapter(request_broker)
        after = adapter.execute(request, bytearray(b"x" * 8), machine_id=MACHINE)
        self.assertEqual(after["outcome"], "indeterminate")
        self.assertNotIn("synthetic upstream failure", repr(after))

    def test_adapter_refuses_a_transport_identity_that_is_not_this_instance(self):
        request_broker, _transport, _binding = descriptor_broker(self.broker)
        adapter = self.broker.CredentialOperationAdapter(request_broker)
        request = self.broker._canonical_guest_request(guest_request())
        result = adapter.execute(request, bytearray(b"x" * 8), machine_id=SIBLING)
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(result["code"], "transport_denied")

    # --- registry retention -------------------------------------------------

    def _registry(self):
        return self.broker.PendingOperationRegistry(
            service_identity(), id_factory=lambda: OPERATION,
        )

    def test_registry_waits_for_a_terminal_result_and_reclaims_the_record(self):
        registry = self._registry()
        submitted = registry.submit(guest_request(), guest_observation(), now=1_000)
        self.assertTrue(submitted["ok"])

        def terminalize():
            claimed = registry.claim_next("controller-1", now=1_000)
            self.assertEqual(claimed["type"], "CLAIMED")
            registry.refuse(claimed["operation_id"], claimed["request_digest"],
                            owner="controller-1", code="binding_unknown")

        worker = threading.Thread(target=terminalize)
        worker.start()
        result = registry.await_result(CONNECTION, timeout_seconds=5.0)
        worker.join()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "binding_unknown")
        self.assertEqual(result["correlation_id"], guest_request()["correlation_id"])
        # The private record is reclaimed with the guest's connection.
        self.assert_refusal(registry.guest_result(CONNECTION), "operation_not_pending")

    def test_registry_wait_times_out_without_inventing_a_result(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        result = registry.await_result(CONNECTION, timeout_seconds=0.05)
        self.assert_refusal(result, "lease_expired")

    # --- controller endpoint ------------------------------------------------

    def _controller_channel(self, registry=None):
        registry = registry or self._registry()
        channel = self.broker.ControllerClaimChannel(
            service_identity(), registry, {
                "uid": 501, "pid": 9001, "process_start_identity": "9001:551",
                "executable_digest": EXECUTABLE_DIGEST,
            },
            clock=lambda: 1_000,
        )
        return registry, channel

    def _controller_endpoint(self, packets, *, registry=None, uid=501, pid=9001,
                             observer=None):
        registry, channel = self._controller_channel(registry)
        connection = FakeControllerConnection(self.broker, packets, uid=uid, pid=pid)
        listener = FakeListener([connection])
        endpoint = self.broker.LinuxControllerEndpoint(
            service_identity(), channel=channel,
            identity_observer=observer or (lambda _connection: {
                "uid": 501, "pid": 9001, "process_start_identity": "9001:551",
                "executable_digest": EXECUTABLE_DIGEST,
            }),
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_000,
        )
        return registry, channel, endpoint, connection, listener

    def test_controller_endpoint_is_closed_by_default(self):
        _registry, channel = self._controller_channel()
        endpoint = self.broker.LinuxControllerEndpoint(
            service_identity(), channel=channel,
            identity_observer=lambda _connection: {},
        )
        self.assert_refusal(endpoint.start(), "controller_channel_closed")
        self.assertFalse(endpoint.admission_open)

    def test_controller_endpoint_authenticates_the_peer_before_parsing(self):
        registry, _channel, endpoint, connection, _listener = self._controller_endpoint(
            [b"not-a-frame"], uid=777,
        )
        registry.submit(guest_request(), guest_observation(), now=1_000)
        self.assertTrue(endpoint.start()["ok"])
        self.assert_refusal(endpoint.accept_once(), "controller_denied")
        self.assertEqual(connection.sent, [])

    def test_controller_endpoint_refuses_an_observer_kernel_mismatch(self):
        _registry, _channel, endpoint, _connection, _listener = self._controller_endpoint(
            [b""], observer=lambda _connection: {
                "uid": 501, "pid": 4242, "process_start_identity": "9001:551",
                "executable_digest": EXECUTABLE_DIGEST,
            },
        )
        self.assertTrue(endpoint.start()["ok"])
        self.assert_refusal(endpoint.accept_once(), "controller_denied")

    def test_controller_claim_and_refuse_round_trip_over_the_endpoint(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        claim = self.broker.encode_controller_message({
            "type": "CLAIM_NEXT", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 1,
        })
        registry, _channel, endpoint, connection, _listener = self._controller_endpoint(
            [claim], registry=registry,
        )
        self.assertTrue(endpoint.start()["ok"])
        self.assertTrue(endpoint.accept_once()["ok"])
        handled = endpoint.handle_next()
        self.assertTrue(handled["ok"])
        reply = self.broker.parse_controller_message(connection.sent[0])
        self.assertEqual(reply["type"], "CLAIMED")
        self.assertEqual(reply["operation_id"], OPERATION)
        self.assertNotIn("headers", reply)
        self.assertNotIn("body", reply)

    def test_controller_endpoint_enforces_monotonic_sequence(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        replay = self.broker.encode_controller_message({
            "type": "CLAIM_NEXT", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 2,
        })
        registry, _channel, endpoint, _connection, _listener = self._controller_endpoint(
            [replay], registry=registry,
        )
        endpoint.start()
        endpoint.accept_once()
        self.assert_refusal(endpoint.handle_next(), "controller_message_invalid")

    def test_controller_endpoint_refuses_a_stale_epoch(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        stale = self.broker.encode_controller_message({
            "type": "CLAIM_NEXT", "machine_id": MACHINE, "broker_epoch": NEXT_EPOCH,
            "sequence": 1,
        })
        registry, _channel, endpoint, _connection, _listener = self._controller_endpoint(
            [stale], registry=registry,
        )
        endpoint.start()
        endpoint.accept_once()
        self.assert_refusal(endpoint.handle_next(), "controller_message_invalid")

    def test_controller_disconnect_terminalizes_owned_operations(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        claim = self.broker.encode_controller_message({
            "type": "CLAIM_NEXT", "machine_id": MACHINE, "broker_epoch": EPOCH,
            "sequence": 1,
        })
        registry, _channel, endpoint, _connection, _listener = self._controller_endpoint(
            [claim], registry=registry,
        )
        endpoint.start()
        endpoint.accept_once()
        endpoint.handle_next()
        self.assertEqual(endpoint.disconnect(), (OPERATION,))
        self.assert_refusal(registry.guest_result(CONNECTION), "operation_cancelled")
        self.assertIsNone(endpoint.owner)

    # --- descriptor rendezvous ---------------------------------------------

    def _operation_endpoint(self, *, registry, frame, owner="controller-1",
                            adapter=None, uid=501, descriptor=77,
                            reader=None):
        packet = self.broker.encode_lease_frame(frame)
        connection = FakeConnection(self.broker, packet, uid=uid, descriptor=descriptor)
        listener = FakeListener([connection])
        endpoint = self.broker.LinuxOperationLeaseEndpoint(
            service_identity(), control_plane_uid=501, registry=registry,
            adapter=adapter or self.broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            owner_provider=lambda: owner,
            identity_observer=lambda: service_identity(),
            descriptor_reader=reader or (
                lambda _descriptor, size: bytearray(b"m" * size)
            ),
            descriptor_observer=lambda _descriptor: descriptor_observation(),
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_000,
        )
        return endpoint, connection

    def _claimed(self, registry, owner="controller-1"):
        registry.submit(guest_request(), guest_observation(), now=1_000)
        claimed = registry.claim_next(owner, now=1_000)
        self.assertEqual(claimed["type"], "CLAIMED")
        return claimed

    def test_operation_lease_endpoint_is_closed_by_default(self):
        registry = self._registry()
        endpoint = self.broker.LinuxOperationLeaseEndpoint(
            service_identity(), control_plane_uid=501, registry=registry,
            adapter=self.broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            owner_provider=lambda: None,
            identity_observer=lambda: service_identity(),
        )
        self.assert_refusal(endpoint.start(), "lease_channel_closed")
        self.assert_refusal(endpoint.receive_once(), "lease_channel_closed")

    def test_descriptor_rendezvous_requires_the_exact_claimed_operation(self):
        registry = self._registry()
        claimed = self._claimed(registry)
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest="0" * 64)
        endpoint, connection = self._operation_endpoint(registry=registry, frame=frame)
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assert_refusal(endpoint.receive_once(), "request_digest_mismatch")
        self.assertTrue(connection.closed)

    def test_descriptor_rendezvous_requires_the_claim_owner(self):
        registry = self._registry()
        claimed = self._claimed(registry, owner="controller-1")
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest=claimed["request_digest"])
        endpoint, _connection = self._operation_endpoint(
            registry=registry, frame=frame, owner="controller-other",
        )
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assert_refusal(endpoint.receive_once(), "operation_not_pending")

    def test_descriptor_transfer_is_one_use_and_never_replayed(self):
        registry = self._registry()
        claimed = self._claimed(registry)
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest=claimed["request_digest"])
        packet = self.broker.encode_lease_frame(frame)
        first = FakeConnection(self.broker, packet, uid=501, descriptor=77)
        second = FakeConnection(self.broker, packet, uid=501, descriptor=78)
        listener = FakeListener([first, second])
        endpoint = self.broker.LinuxOperationLeaseEndpoint(
            service_identity(), control_plane_uid=501, registry=registry,
            adapter=self.broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            owner_provider=lambda: "controller-1",
            identity_observer=lambda: service_identity(),
            descriptor_reader=lambda _descriptor, size: bytearray(b"m" * size),
            descriptor_observer=lambda _descriptor: descriptor_observation(),
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_000,
        )
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assertTrue(endpoint.receive_once()["ok"])
        self.assert_refusal(endpoint.receive_once(), "lease_replayed")

    def test_descriptor_endpoint_denies_a_foreign_dispatcher_uid(self):
        registry = self._registry()
        claimed = self._claimed(registry)
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest=claimed["request_digest"])
        endpoint, connection = self._operation_endpoint(
            registry=registry, frame=frame, uid=999,
        )
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assert_refusal(endpoint.receive_once(), "dispatcher_denied")
        self.assertFalse(connection.recvmsg_called)

    def test_typed_execution_delivers_the_terminal_result_to_the_guest_record(self):
        registry = self._registry()
        claimed = self._claimed(registry)
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest=claimed["request_digest"])
        request_broker, transport, _binding = descriptor_broker(self.broker)
        endpoint, _connection = self._operation_endpoint(
            registry=registry, frame=frame,
            adapter=self.broker.CredentialOperationAdapter(request_broker),
        )
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assertTrue(endpoint.receive_once()["ok"])
        result = registry.guest_result(CONNECTION)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(set(result["headers"]), {"content-type"})
        self.assertNotIn("m" * 8, repr(result))
        self.assertEqual(len(transport.calls), 1)

    def test_a_cancelled_operation_is_never_executed_after_revocation(self):
        registry = self._registry()
        claimed = self._claimed(registry)
        registry.revoke(BINDING)
        frame = lease_frame(operation_id=claimed["operation_id"],
                            request_digest=claimed["request_digest"])
        calls = []
        endpoint, _connection = self._operation_endpoint(
            registry=registry, frame=frame,
            adapter=self.broker.OfflineTestOperationAdapter(
                lambda request, material: calls.append(1) or {"outcome": "completed"},
                offline_test=True,
            ),
        )
        endpoint.start(); endpoint.open_admission(service_identity())
        self.assertFalse(endpoint.receive_once()["ok"])
        self.assertEqual(calls, [])

    # --- retained guest connection -----------------------------------------

    def _guest_endpoint(self, registry, connections, *, coordinator=None):
        listener = FakeGuestListener(connections)
        return self.broker.LinuxGuestEndpoint(
            service_identity(), registry=registry,
            connection_observer=lambda _connection: guest_observation(),
            operation_runner=coordinator,
            enabled=True, socket_factory=lambda *_args: listener,
            clock=lambda: 1_000,
        )

    def test_guest_connection_is_retained_until_one_terminal_result(self):
        registry = self._registry()
        packet = self.broker.encode_guest_request(guest_request())
        connection = FakeGuestConnection(packet)
        endpoint = self._guest_endpoint(registry, [connection])
        endpoint.start(); endpoint.open_admission()

        def terminalize():
            for _attempt in range(200):
                claimed = registry.claim_next("controller-1", now=1_000)
                if claimed["type"] == "CLAIMED":
                    registry.refuse(claimed["operation_id"], claimed["request_digest"],
                                    owner="controller-1", code="binding_not_ready")
                    return
                time.sleep(0.005)

        worker = threading.Thread(target=terminalize)
        worker.start()
        result = endpoint.serve_once(timeout_seconds=5.0)
        worker.join()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "binding_not_ready")
        self.assertEqual(len(connection.sent), 1)
        delivered = self.broker.parse_guest_terminal_result(connection.sent[0])
        self.assertEqual(delivered["code"], "binding_not_ready")
        self.assertTrue(connection.closed)

    def test_a_delivered_result_never_carries_internal_identifiers(self):
        registry = self._registry()
        packet = self.broker.encode_guest_request(guest_request())
        connection = FakeGuestConnection(packet)
        endpoint = self._guest_endpoint(registry, [connection])
        endpoint.start(); endpoint.open_admission()
        worker = threading.Thread(target=lambda: registry.expire(9_999_999_999))
        worker.start(); worker.join()
        endpoint.serve_once(timeout_seconds=1.0)
        surface = repr(connection.sent)
        for forbidden in (OPERATION, LEASE, REQUEST_DIGEST, "descriptor", "memfd"):
            self.assertNotIn(forbidden, surface)

    def test_a_guest_disconnect_reclaims_the_private_record(self):
        registry = self._registry()
        registry.submit(guest_request(), guest_observation(), now=1_000)
        self.assert_refusal(registry.guest_disconnected(CONNECTION), "operation_cancelled")
        self.assert_refusal(registry.guest_result(CONNECTION), "operation_not_pending")

    # --- kernel-derived guest observation ----------------------------------

    ROUTES = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "ve-sb0123456789\t0000CB0A\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
        "eth0\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\n"
    )

    def _observer(self, *, routes=None, interface="ve-sb0123456789",
                  device_error=None):
        def device_reader(_connection):
            if device_error is not None:
                raise device_error
            return interface

        return self.broker.LinuxGuestConnectionObserver(
            service_identity(),
            route_reader=lambda: self.ROUTES if routes is None else routes,
            device_reader=device_reader,
            id_factory=lambda: CONNECTION,
        )

    def test_the_observer_derives_verified_state_from_the_kernel(self):
        observation = self._observer()(FakeGuestConnection(b""))
        self.assertTrue(self.broker.validate_guest_transport(
            service_identity(), observation,
        )["ok"])
        self.assertTrue(observation["peer_verified"])
        self.assertFalse(observation["forwarded"])
        self.assertFalse(observation["loopback"])
        self.assertEqual(observation["interface"], "ve-sb0123456789")
        self.assertEqual(observation["peer_address"], "10.203.0.2")

    def test_a_peer_that_is_not_on_link_is_reported_as_forwarded(self):
        gateway_only = (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "ve-sb0123456789\t0000CB0A\t0100A8C0\t0003\t0\t0\t0\t00FFFFFF\n"
        )
        for routes in ("", gateway_only, "Iface\tDestination\n"):
            with self.subTest(routes=routes[:24]):
                observation = self._observer(routes=routes)(FakeGuestConnection(b""))
                self.assertTrue(observation["forwarded"])
                self.assertFalse(observation["peer_verified"])
                self.assert_refusal(self.broker.validate_guest_transport(
                    service_identity(), observation,
                ), "transport_denied")

    def test_a_route_on_another_device_never_verifies_this_one(self):
        other = (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "eth0\t0000CB0A\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
        )
        observation = self._observer(routes=other)(FakeGuestConnection(b""))
        self.assertTrue(observation["forwarded"])
        self.assertFalse(observation["peer_verified"])

    def test_an_unreadable_bound_device_is_a_closed_observation(self):
        observation = self._observer(device_error=OSError("no device"))(
            FakeGuestConnection(b""),
        )
        self.assertFalse(observation["peer_verified"])
        self.assertTrue(observation["forwarded"])
        self.assertTrue(observation["loopback"])
        self.assert_refusal(self.broker.validate_guest_transport(
            service_identity(), observation,
        ), "transport_denied")

    def test_a_foreign_bound_device_is_never_verified(self):
        observation = self._observer(interface="eth0")(FakeGuestConnection(b""))
        self.assertFalse(observation["peer_verified"])
        self.assert_refusal(self.broker.validate_guest_transport(
            service_identity(), observation,
        ), "transport_denied")

    def test_a_loopback_connection_is_never_verified(self):
        connection = FakeGuestConnection(b"", local=("127.0.0.1", 18443),
                                         peer=("127.0.0.1", 43100))
        observation = self._observer()(connection)
        self.assertTrue(observation["loopback"])
        self.assertFalse(observation["peer_verified"])

    def test_every_observation_carries_a_fresh_non_secret_identity(self):
        observer = self.broker.LinuxGuestConnectionObserver(
            service_identity(), route_reader=lambda: self.ROUTES,
            device_reader=lambda _connection: "ve-sb0123456789",
        )
        first = observer(FakeGuestConnection(b""))
        second = observer(FakeGuestConnection(b""))
        self.assertNotEqual(first["connection_identity"],
                            second["connection_identity"])
        for value in (first, second):
            self.assertTrue(self.broker._identity(value["connection_identity"]))
            self.assertNotIn(FORBIDDEN_MARKER, repr(value))
            self.assertNotIn("authorization", repr(value).lower())

    def test_a_rotated_epoch_invalidates_an_older_observation(self):
        observation = self._observer()(FakeGuestConnection(b""))
        self.assert_refusal(self.broker.validate_guest_transport(
            service_identity(broker_epoch=NEXT_EPOCH), observation,
        ), "transport_denied")

    # --- coordinator lifecycle ---------------------------------------------

    def _coordinator(self, **overrides):
        values = {
            "service": service_identity(),
            "control_plane_uid": 501,
            "controller_identity": {
                "uid": 501, "pid": 9001, "process_start_identity": "9001:551",
                "executable_digest": EXECUTABLE_DIGEST,
            },
            "adapter": self.broker.OfflineTestOperationAdapter(
                lambda _request, _material: {"outcome": "completed"}, offline_test=True,
            ),
            "identity_observer": lambda: service_identity(),
            "connection_observer": lambda _connection: guest_observation(),
            "clock": lambda: 1_000,
        }
        values.update(overrides)
        service = values.pop("service")
        return self.broker.BrokerCoordinator(service, **values)

    def test_coordinator_is_closed_by_default(self):
        coordinator = self._coordinator()
        self.assertFalse(coordinator.admission_open)
        self.assertEqual(coordinator.status()["state"], "credential_pending")
        self.assertFalse(coordinator.status()["admission_open"])
        self.assert_refusal(coordinator.start(), "broker_service_disabled")

    def test_coordinator_lifecycle_closes_admission_before_drain_and_stop(self):
        order = []

        class Endpoint:
            def __init__(self, name):
                self.name = name

            def start(self):
                order.append(f"start:{self.name}")
                return {"ok": True, "code": "started", "admission_open": False}

            def open_admission(self, *_args):
                order.append(f"open:{self.name}")
                return {"ok": True, "code": "open"}

            def close_admission(self):
                order.append(f"close:{self.name}")

            def drain(self, _timeout=5.0):
                order.append(f"drain:{self.name}")
                return True

            def close(self):
                order.append(f"stop:{self.name}")
                return {"ok": True, "code": "closed", "admission_open": False}

        coordinator = self._coordinator(enabled=True)
        coordinator._endpoints = (Endpoint("lease"), Endpoint("controller"),
                                  Endpoint("guest"))
        coordinator.stop()
        self.assertEqual(order.index("close:guest"), 0)
        self.assertLess(max(index for index, value in enumerate(order)
                            if value.startswith("close:")),
                        min(index for index, value in enumerate(order)
                            if value.startswith("drain:")))
        self.assertLess(max(index for index, value in enumerate(order)
                            if value.startswith("drain:")),
                        min(index for index, value in enumerate(order)
                            if value.startswith("stop:")))

    def test_coordinator_lifecycle_identity_exposes_the_contract_fields(self):
        coordinator = self._coordinator()
        identity = coordinator.lifecycle_identity()
        self.assertEqual(set(identity), {
            "ok", "machine_id", "broker_epoch", "pid", "process_start_identity",
            "service_uid", "unit_identity", "cgroup_identity", "executable_digest",
            "config_digest", "policy_digest", "egress_digest", "broker_digest",
            "state", "admission_open", "active_operations",
        })
        self.assertEqual(identity["state"], "credential_pending")
        self.assertFalse(identity["admission_open"])
        self.assertEqual(identity["active_operations"], 0)
        surface = repr(identity)
        for forbidden in ("source_reference", "lease", "descriptor", "operation_id",
                          "request_digest", "authorization", FORBIDDEN_MARKER):
            self.assertNotIn(forbidden, surface)

    def test_coordinator_revoke_and_expiry_close_admission_fail_closed(self):
        coordinator = self._coordinator(enabled=True)
        coordinator.registry.submit(guest_request(), guest_observation(), now=1_000)
        self.assertEqual(len(coordinator.revoke(BINDING)), 1)
        self.assertFalse(coordinator.admission_open)
        self.assert_refusal(coordinator.registry.guest_result(CONNECTION),
                            "operation_cancelled")

    def test_the_serving_loop_is_disabled_until_explicitly_enabled(self):
        coordinator = self._coordinator()
        self.assert_refusal(coordinator.run(stop_event=threading.Event()),
                            "broker_service_disabled")

    def test_the_serving_loop_starts_and_stops_every_worker(self):
        coordinator = self._coordinator(enabled=True)
        stop = threading.Event()
        stop.set()
        before = threading.active_count()
        result = coordinator.run(stop_event=stop, guest_workers=1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["admission_open"])
        self.assertLessEqual(threading.active_count(), before)

    def test_the_serving_loop_refuses_invalid_worker_bounds(self):
        coordinator = self._coordinator(enabled=True)
        for workers, poll in ((0, 0.05), (99, 0.05), (1, 0), (1, 5)):
            with self.subTest(workers=workers, poll=poll):
                self.assert_refusal(
                    coordinator.run(stop_event=threading.Event(),
                                    guest_workers=workers, poll_seconds=poll),
                    "broker_service_config_invalid",
                )

    def test_a_stopped_coordinator_does_not_run_again(self):
        coordinator = self._coordinator(enabled=True)
        coordinator.stop()
        self.assert_refusal(coordinator.run(stop_event=threading.Event()),
                            "broker_service_unavailable")

    # --- runnable configuration --------------------------------------------

    def test_service_config_is_strict_and_refuses_any_extra_field(self):
        document = {
            "enabled": False, "control_plane_uid": 501,
            "service": service_identity(),
            "controller": {
                "uid": 501, "pid": 9001, "process_start_identity": "9001:551",
                "executable_digest": EXECUTABLE_DIGEST,
            },
        }
        loaded = self.broker.parse_service_config(json.dumps(document))
        self.assertFalse(loaded["enabled"])
        self.assertEqual(loaded["service"]["machine_id"], MACHINE)
        for mutation in (
            {"credential_value": FORBIDDEN_MARKER},
            {"source_reference": "fixture/API_TOKEN"},
            {"authorization": "Bearer x"},
        ):
            with self.subTest(mutation=tuple(mutation)):
                with self.assertRaises(ValueError):
                    self.broker.parse_service_config(json.dumps({**document, **mutation}))
        with self.assertRaises(ValueError):
            self.broker.parse_service_config(json.dumps(
                {**document, "service": service_identity(machine_id="not-a-machine")},
            ))

    def test_the_executable_is_closed_by_default_on_every_invocation(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = self.broker.main([])
        self.assertNotEqual(code, 0)
        document = json.loads(stream.getvalue())
        self.assertFalse(document["ok"])
        self.assertIn(document["code"], self.broker._SAFE_SUMMARIES)

    def test_serving_requires_an_explicitly_enabled_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broker.json"
            path.write_text(json.dumps({
                "enabled": False, "control_plane_uid": 501,
                "service": service_identity(),
                "controller": {
                    "uid": 501, "pid": 9001, "process_start_identity": "9001:551",
                    "executable_digest": EXECUTABLE_DIGEST,
                },
            }))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = self.broker.main(["--serve", "--config", str(path)])
            self.assertNotEqual(code, 0)
            document = json.loads(stream.getvalue())
            self.assertFalse(document["ok"])
            self.assertEqual(document["code"], "broker_service_disabled")

    def test_no_runtime_composition_selects_the_coordinator(self):
        surface = BROKER.read_text()
        self.assertNotIn("sandbox.application.context", surface)
        self.assertNotIn("sandbox.runtimes", surface)
        production = (ROOT / "sandbox").rglob("*.py")
        for path in production:
            body = path.read_text()
            self.assertNotIn("native-credential-broker", body)
            self.assertNotIn("BrokerCoordinator", body)

    def test_support_state_is_not_promoted_by_this_module(self):
        document = self.broker.live_transport_status()
        self.assertFalse(document["ok"])
        # Off Ubuntu this is a platform refusal; on Linux it is still unproven.
        self.assertIn(document["code"], {"live_transport_unproven",
                                         "live_transport_platform_unsupported"})


if __name__ == "__main__":
    unittest.main()
