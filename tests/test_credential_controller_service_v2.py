import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import struct
import unittest
from unittest import mock

from sandbox.isolation.credential_controller_protocol_v2 import (
    AuthorizationRegistry,
    PROTOCOL,
    digest_document,
    encode_controller_frame,
)
from sandbox.isolation.credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceConfig,
    ControllerServiceV2Error,
    ExactBrokerSelfObserver,
    ExactProcessIdentityObserver,
    ProcessIdentity,
    PersistentControllerService,
    abstract_controller_address,
    receive_authenticated_packet,
)


ROOT = Path(__file__).resolve().parents[1]
BROKER_PATH = ROOT / "tools/native-helper/native-credential-broker.py"
SPEC = importlib.util.spec_from_file_location("credential_broker_t040", BROKER_PATH)
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)

NOW = 1_800_000_000_000
MACHINE = "machine-01234567"
BROKER_EPOCH = "01" * 16
CONTROLLER_EPOCH = "02" * 16
DIGESTS = [hashlib.sha256(f"t040-{index}".encode()).hexdigest() for index in range(9)]
SO_PEERCRED = 17
SCM_CREDENTIALS = 2
SCM_RIGHTS = socket.SCM_RIGHTS
CREDS = struct.Struct("3i")


def identity(offset):
    return ProcessIdentity(
        uid=1000 + offset, gid=2000 + offset, pid=3000 + offset,
        start_ticks=4000 + offset, executable_digest=DIGESTS[offset],
        unit_digest=DIGESTS[offset + 2], config_digest=DIGESTS[offset + 4],
    )


CONTROLLER = identity(0)
BROKER = identity(1)
CONFIG = ControllerServiceConfig(
    machine_id=MACHINE, controller=CONTROLLER, broker=BROKER,
    policy_digest=DIGESTS[0], egress_digest=DIGESTS[1],
    broker_digest=DIGESTS[2], proof_digest=DIGESTS[3],
    effective_isolation_digest=DIGESTS[4], evidence_id="evidence-0123456",
)


def observer_for(expected, calls=None):
    def observe(pid, uid, gid):
        if calls is not None:
            calls.append((pid, uid, gid))
        if (pid, uid, gid) != (expected.pid, expected.uid, expected.gid):
            raise ControllerServiceV2Error("peer_identity_mismatch")
        return expected
    return observe


def self_observer_for(value=CONFIG, calls=None):
    def observe(*args):
        if calls is not None:
            calls.append(args)
        if args:
            raise AssertionError("configured identity was passed to current reader")
        return value
    return observe


def hello(sequence=1, broker_epoch=BROKER_EPOCH):
    return {
        "protocol": PROTOCOL, "type": "HELLO_V2", "machine_id": MACHINE,
        "broker_epoch": broker_epoch, "sequence": sequence,
        **BROKER.hello_fields("broker"), **CONFIG.configured_digests(),
    }


def ack(controller_epoch=CONTROLLER_EPOCH, sequence=1, digest=None):
    values = {
        "protocol": PROTOCOL, "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": controller_epoch,
        **BROKER.hello_fields("broker"), **CONTROLLER.hello_fields("controller"),
        **CONFIG.configured_digests(),
    }
    return {
        "protocol": PROTOCOL, "type": "HELLO_ACK_V2", "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": controller_epoch,
        "sequence": sequence, "reply_to": 1, "accepted": True,
        **CONTROLLER.hello_fields("controller"),
        "handshake_digest": digest or digest_document("handshake_digest", values),
    }


def no_pending(sequence):
    return {
        "protocol": PROTOCOL, "type": "CLAIMED_V2", "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "sequence": sequence, "reply_to": sequence,
        "claim_state": "no_pending", "retry_after_ms": 50,
    }
def frame(value, direction):
    return encode_controller_frame(value, direction=direction, now_ms=NOW)


class FakeConnection:
    def __init__(self, expected, packets):
        self.expected = expected
        self.packets = list(packets)
        self.sent = []
        self.closed = 0
        self.timeout = None
        self.connected = None

    def getsockopt(self, _level, option, size):
        if option != SO_PEERCRED or size != CREDS.size:
            raise OSError("unavailable")
        return CREDS.pack(self.expected.pid, self.expected.uid, self.expected.gid)

    def recvmsg(self, *_args):
        if not self.packets:
            return b"", [], 0, None
        packet = self.packets.pop(0)
        if len(packet) == 4:
            return packet
        return packet, [(socket.SOL_SOCKET, SCM_CREDENTIALS,
                         CREDS.pack(self.expected.pid, self.expected.uid,
                                    self.expected.gid))], 0, None

    def sendall(self, value):
        self.sent.append(value)

    def connect(self, address):
        self.connected = address

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed += 1


class FakeListener:
    def __init__(self, connections=()):
        self.connections = list(connections)
        self.created = None
        self.options = []
        self.bound = None
        self.backlog = None
        self.closed = 0

    def setsockopt(self, *args): self.options.append(args)
    def bind(self, address): self.bound = address
    def listen(self, backlog): self.backlog = backlog
    def accept(self): return self.connections.pop(0), None
    def close(self): self.closed += 1


class TestCredentialControllerServiceV2(unittest.TestCase):
    def test_exported_packet_authenticator_bounds_observer_failure(self):
        connection = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])

        def failing_observer(*_peer):
            raise RuntimeError("hostile observer detail")

        with self.assertRaises(ControllerServiceV2Error) as raised:
            receive_authenticated_packet(
                connection, expected=BROKER, observer=failing_observer,
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertEqual(str(raised.exception), "peer_identity_unavailable")

    def test_import_and_construction_have_no_io_or_epoch_generation(self):
        calls = []
        with mock.patch("socket.socket", side_effect=AssertionError("socket I/O")):
            service = PersistentControllerService(
                CONFIG, epoch_factory=lambda: calls.append("epoch"),
                owner_factory=lambda: calls.append("owner"),
            )
            listener = broker.LinuxControllerV2Listener(
                CONFIG, epoch_factory=lambda: calls.append("broker-epoch"),
                owner_factory=lambda: calls.append("broker-owner"),
                socket_factory=lambda *_args: calls.append("socket"),
            )
        self.assertEqual(calls, [])
        self.assertFalse(service.admission_open)
        self.assertFalse(listener.admission_open)

    def test_start_is_linux_only_closed_and_rotates_each_process_object(self):
        first = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        self.assertEqual(first.start(platform="linux", enabled=True)["admission_open"], False)
        with self.assertRaisesRegex(ControllerServiceV2Error, "controller_start_refused"):
            first.start(platform="linux", enabled=True)
        second = PersistentControllerService(
            CONFIG, epoch_factory=lambda: "03" * 16,
            owner_factory=lambda: "controller-session-1123456789abcdef",
        )
        second.start(platform="linux", enabled=True)
        self.assertNotEqual(first.controller_epoch, second.controller_epoch)
        denied = PersistentControllerService(
            CONFIG, epoch_factory=lambda: "04" * 16,
            owner_factory=lambda: "controller-session-2123456789abcdef",
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "controller_start_refused"):
            denied.start(platform="darwin", enabled=True)

    def test_identity_observer_requires_exact_symmetric_start_observe_start(self):
        starts = []
        verifier = ExactProcessIdentityObserver(
            BROKER,
            start_reader=lambda pid: starts.append(pid) or BROKER.start_ticks,
            detail_reader=lambda _pid: {
                "executable_digest": BROKER.executable_digest,
                "unit_digest": BROKER.unit_digest,
                "config_digest": BROKER.config_digest,
            },
        )
        self.assertEqual(verifier(BROKER.pid, BROKER.uid, BROKER.gid), BROKER)
        self.assertEqual(starts, [BROKER.pid, BROKER.pid])
        changing = iter((BROKER.start_ticks, BROKER.start_ticks + 1))
        drift = ExactProcessIdentityObserver(
            BROKER, start_reader=lambda _pid: next(changing),
            detail_reader=lambda _pid: {
                "executable_digest": BROKER.executable_digest,
                "unit_digest": BROKER.unit_digest,
                "config_digest": BROKER.config_digest,
            },
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "peer_identity_mismatch"):
            drift(BROKER.pid, BROKER.uid, BROKER.gid)
        current_calls = []
        exact_self = ExactBrokerSelfObserver(
            CONFIG,
            current_process_identity_reader=lambda *args: (
                current_calls.append(args) or
                (BROKER.pid, BROKER.uid, BROKER.gid)
            ),
            start_reader=lambda _pid: BROKER.start_ticks,
            detail_reader=lambda _pid: {
                "executable_digest": BROKER.executable_digest,
                "unit_digest": BROKER.unit_digest,
                "config_digest": BROKER.config_digest,
            },
            sealed_reader=lambda: CONFIG,
        )
        self.assertEqual(exact_self(), CONFIG)
        self.assertEqual(current_calls, [()])
        sealed_drift = ExactBrokerSelfObserver(
            CONFIG,
            current_process_identity_reader=lambda: (
                BROKER.pid, BROKER.uid, BROKER.gid),
            start_reader=lambda _pid: BROKER.start_ticks,
            detail_reader=lambda _pid: {
                "executable_digest": BROKER.executable_digest,
                "unit_digest": BROKER.unit_digest,
                "config_digest": BROKER.config_digest,
            },
            sealed_reader=lambda: dataclasses.replace(CONFIG, proof_digest=DIGESTS[8]),
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "broker_self_identity_mismatch"):
            sealed_drift()

    def test_controller_handshake_is_exact_deadlined_and_remains_closed(self):
        connection = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        times = iter((1.0, 1.1, 1.2))
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        result = session.handshake(
            observer=observer_for(BROKER), now_ms=NOW, monotonic=lambda: next(times),
            so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
            scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
        )
        self.assertEqual(result["code"], "controller_authenticated")
        self.assertFalse(result["admission_open"])
        self.assertEqual(connection.timeout, 1.0)
        decoded = broker.decode_controller_frame_v2(
            connection.sent[0], direction="controller_to_broker", now_ms=NOW,
        )
        self.assertEqual((decoded["type"], decoded["sequence"], decoded["reply_to"]),
                         ("HELLO_ACK_V2", 1, 1))
        self.assertEqual(decoded["handshake_digest"], ack()["handshake_digest"])

    def test_controller_refuses_same_epoch_pair_reconnect_before_second_ack(self):
        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        service.start(platform="linux", enabled=True)
        first = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        service.attach(
            first, observer=observer_for(BROKER), now_ms=NOW,
            monotonic=iter((1.0, 1.1, 1.2)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        service.disconnect()
        second = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        with self.assertRaisesRegex(ControllerServiceV2Error, "epoch_pair_replayed"):
            service.attach(
                second, observer=observer_for(BROKER), now_ms=NOW,
                monotonic=iter((2.0, 2.1)).__next__, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(second.sent, [])
        self.assertEqual(second.closed, 1)

    def test_controller_connection_also_pins_clock_state_and_closes_on_rollback(self):
        connection = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        session.handshake(
            observer=observer_for(BROKER), now_ms=NOW,
            monotonic=iter((1.0, 1.1, 1.2)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        pinned = session._observation
        connection.packets.extend((
            frame(no_pending(2), "broker_to_controller"),
            frame(no_pending(3), "broker_to_controller"),
        ))
        session.receive_frame(
            observer=observer_for(BROKER), now_ms=NOW + 100,
            so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
            scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "broker_frame_refused"):
            session.receive_frame(
                observer=observer_for(BROKER), now_ms=NOW - 1_000,
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertIs(session._observation, pinned)
        self.assertEqual(connection.closed, 1)

    def test_controller_connector_uses_only_abstract_seqpacket(self):
        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        service.start(platform="linux", enabled=True)
        connection = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        created = []
        result = service.connect(
            connector=lambda *args: created.append(args) or connection,
            observer=observer_for(BROKER), now_ms=NOW,
            monotonic=iter((1.0, 1.1, 1.2)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        self.assertEqual(result["code"], "controller_authenticated")
        self.assertEqual(created, [(socket.AF_UNIX, socket.SOCK_SEQPACKET, 0)])
        self.assertEqual(connection.connected,
                         abstract_controller_address(MACHINE, CONFIG.broker_digest))

    def test_packet_credential_mutations_rights_truncation_and_drift_close(self):
        mutations = [
            ([], 0),
            ([(socket.SOL_SOCKET, SCM_CREDENTIALS,
               CREDS.pack(BROKER.pid, BROKER.uid, BROKER.gid)),
              (socket.SOL_SOCKET, SCM_CREDENTIALS,
               CREDS.pack(BROKER.pid, BROKER.uid, BROKER.gid))], 0),
            ([(socket.SOL_SOCKET, SCM_CREDENTIALS,
               CREDS.pack(BROKER.pid + 1, BROKER.uid, BROKER.gid))], 0),
            ([(socket.SOL_SOCKET, SCM_CREDENTIALS,
               CREDS.pack(BROKER.pid, BROKER.uid, BROKER.gid))],
             getattr(socket, "MSG_TRUNC", 0) or 32),
        ]
        for ancillary, flags in mutations:
            with self.subTest(ancillary=ancillary, flags=flags):
                connection = FakeConnection(BROKER, [(
                    frame(hello(), "broker_to_controller"), ancillary, flags, None,
                )])
                terminal = []
                session = ControllerBrokerSession(
                    connection, CONFIG, CONTROLLER_EPOCH,
                    "controller-session-0123456789abcdef",
                    on_terminal=terminal.append,
                )
                with self.assertRaises(ControllerServiceV2Error):
                    session.handshake(
                        observer=observer_for(BROKER), now_ms=NOW,
                        monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                        scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                        closer=lambda _fd: None,
                    )
                session.close()
                self.assertEqual(connection.closed, 1)
                self.assertEqual(len(terminal), 1)
        closed_fds = []
        rights = struct.pack("i", 91)
        connection = FakeConnection(BROKER, [(
            frame(hello(), "broker_to_controller"),
            [(socket.SOL_SOCKET, SCM_CREDENTIALS,
              CREDS.pack(BROKER.pid, BROKER.uid, BROKER.gid)),
             (socket.SOL_SOCKET, SCM_RIGHTS, rights)], 0, None,
        )])
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        with self.assertRaises(ControllerServiceV2Error):
            session.handshake(
                observer=observer_for(BROKER), now_ms=NOW,
                monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=closed_fds.append,
            )
        self.assertEqual(closed_fds, [91])

    def test_all_rights_are_prescanned_and_closed_once_before_any_refusal(self):
        ctrunc = getattr(socket, "MSG_CTRUNC", 0) or 8
        cases = (
            ([(socket.SOL_SOCKET, SCM_RIGHTS, struct.pack("i", 71))], ctrunc,
             [71]),
            (["malformed", (socket.SOL_SOCKET, SCM_RIGHTS, struct.pack("i", 72))],
             0, [72]),
            ([(socket.SOL_SOCKET, SCM_RIGHTS, struct.pack("2i", 73, 74)),
              (socket.SOL_SOCKET, SCM_RIGHTS, struct.pack("2i", 74, 75))],
             0, [73, 74, 75]),
        )
        for ancillary, flags, expected_closed in cases:
            with self.subTest(ancillary=ancillary, flags=flags):
                closed = []
                connection = FakeConnection(BROKER, [(
                    frame(hello(), "broker_to_controller"), ancillary, flags, None,
                )])
                session = ControllerBrokerSession(
                    connection, CONFIG, CONTROLLER_EPOCH,
                    "controller-session-0123456789abcdef",
                    on_terminal=lambda _reason: None,
                )
                with self.assertRaises(ControllerServiceV2Error):
                    session.handshake(
                        observer=observer_for(BROKER), now_ms=NOW,
                        monotonic=iter((1.0, 1.1)).__next__,
                        so_peercred=SO_PEERCRED,
                        scm_credentials=SCM_CREDENTIALS,
                        scm_rights=SCM_RIGHTS, closer=closed.append,
                    )
                self.assertEqual(closed, expected_closed)
                self.assertEqual(connection.closed, 1)

    def test_rights_cleanup_failure_is_bounded_and_terminal(self):
        attempts = []

        def failed_close(descriptor):
            attempts.append(descriptor)
            raise RuntimeError("untrusted close failure")

        connection = FakeConnection(BROKER, [(
            frame(hello(), "broker_to_controller"),
            [(socket.SOL_SOCKET, SCM_RIGHTS, struct.pack("i", 81))], 0, None,
        )])
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "packet_rights_cleanup_failed"):
            session.handshake(
                observer=observer_for(BROKER), now_ms=NOW,
                monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=failed_close,
            )
        self.assertEqual(attempts, [81])
        self.assertEqual(connection.closed, 1)
        self.assertFalse(session.authenticated)

    def test_handshake_deadline_failure_closes_without_ack(self):
        connection = FakeConnection(BROKER, [frame(hello(), "broker_to_controller")])
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "handshake_timeout"):
            session.handshake(
                observer=observer_for(BROKER), now_ms=NOW,
                monotonic=iter((1.0, 2.01)).__next__, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(connection.sent, [])
        self.assertEqual(connection.closed, 1)

    def test_broker_handshake_constructs_exactly_one_registry_and_never_rebuilds(self):
        connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
        created = []

        def factory(**kwargs):
            created.append(dict(kwargs))
            return AuthorizationRegistry(**kwargs)

        terminal = []
        session = broker.BrokerControllerV2Connection(
            connection, CONFIG, BROKER_EPOCH, "broker-session-0123456789",
            registry_factory=factory, on_terminal=terminal.append,
        )
        result = session.handshake(
            observer=observer_for(CONTROLLER), now_ms=NOW,
            monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        original = session.registry
        self.assertEqual(result["code"], "broker_controller_authenticated")
        self.assertFalse(result["admission_open"])
        self.assertEqual(len(created), 1)
        with self.assertRaisesRegex(ControllerServiceV2Error, "handshake_replayed"):
            session.handshake(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 2.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertIs(session.registry, original)
        self.assertEqual(len(created), 1)
        session.close()
        self.assertEqual(len(terminal), 1)
        self.assertEqual(connection.closed, 1)

    def test_broker_listener_is_abstract_seqpacket_and_allows_no_reconnect(self):
        connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
        listener = FakeListener((connection,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *created: setattr(listener, "created", created) or listener,
        )
        current_reader_calls = []
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            started = endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(calls=current_reader_calls),
            )
        self.assertFalse(started["admission_open"])
        self.assertEqual(listener.created, (socket.AF_UNIX, socket.SOCK_SEQPACKET, 0))
        self.assertTrue(listener.bound.startswith(b"\0sandbox-credential-controller-v2-"))
        self.assertEqual(current_reader_calls, [()])
        endpoint.accept_once(
            observer=observer_for(CONTROLLER), now_ms=NOW,
            monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        endpoint.disconnect()
        with self.assertRaisesRegex(ControllerServiceV2Error, "controller_connection_refused"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW, monotonic=lambda: 2.0,
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )

    def test_listener_requires_exact_self_identity_and_mandatory_passcred(self):
        identity_fields = (
            "uid", "gid", "pid", "start_ticks", "executable_digest",
            "unit_digest", "config_digest",
        )
        for field_name in identity_fields:
            replacement = (BROKER.uid + 1 if field_name in {"uid", "gid", "pid", "start_ticks"}
                           else DIGESTS[8])
            observed = dataclasses.replace(BROKER, **{field_name: replacement})
            observed_config = dataclasses.replace(CONFIG, broker=observed)
            endpoint = broker.LinuxControllerV2Listener(
                CONFIG, epoch_factory=lambda: BROKER_EPOCH,
                owner_factory=lambda: "broker-session-0123456789",
                socket_factory=lambda *_args: FakeListener(),
            )
            with self.subTest(field=field_name), \
                    mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "controller_listener_start_refused"):
                    endpoint.start(
                        platform="linux", enabled=True, effective_uid=BROKER.uid,
                        self_observer=self_observer_for(observed_config),
                    )
        for field_name in (
                "policy_digest", "egress_digest", "broker_digest", "proof_digest",
                "effective_isolation_digest"):
            observed_config = dataclasses.replace(CONFIG, **{field_name: DIGESTS[8]})
            endpoint = broker.LinuxControllerV2Listener(
                CONFIG, epoch_factory=lambda: BROKER_EPOCH,
                owner_factory=lambda: "broker-session-0123456789",
                socket_factory=lambda *_args: FakeListener(),
            )
            with self.subTest(sealed_field=field_name), \
                    mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "controller_listener_start_refused"):
                    endpoint.start(
                        platform="linux", enabled=True, effective_uid=BROKER.uid,
                        self_observer=self_observer_for(observed_config),
                    )
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: FakeListener(),
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_listener_start_refused"):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: FakeListener(),
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "controller_listener_start_refused"):
                endpoint.start(
                    platform="linux", enabled=True,
                    effective_uid=BROKER.uid + 1,
                    self_observer=self_observer_for(),
                )

    def test_listener_and_connector_have_exact_single_socket_owner(self):
        invalid_owner_connection = FakeConnection(CONTROLLER, [])
        listener = FakeListener((invalid_owner_connection,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: [],
            socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "broker_connection_invalid"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(invalid_owner_connection.closed, 1)

        malformed = FakeConnection(CONTROLLER, [b"not-json"])
        listener = FakeListener((malformed,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "handshake_refused"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(malformed.closed, 1)

        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        service.start(platform="linux", enabled=True)
        failed = FakeConnection(BROKER, [b"not-json"])
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "handshake_refused"):
            service.connect(
                connector=lambda *_args: failed,
                observer=observer_for(BROKER), now_ms=NOW,
                monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(failed.closed, 1)

        class PasscredFailure(FakeListener):
            def setsockopt(self, *_args):
                raise OSError("unsupported")

        failed_listener = PasscredFailure()
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: failed_listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "controller_listener_start_refused"):
                endpoint.start(
                    platform="linux", enabled=True, effective_uid=BROKER.uid,
                    self_observer=self_observer_for(),
                )
        self.assertEqual(failed_listener.closed, 1)

        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        connector_calls = []
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_connection_refused"):
            service.connect(
                connector=lambda *_args: connector_calls.append(True),
                observer=observer_for(BROKER), now_ms=NOW, monotonic=lambda: 1.0,
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertEqual(connector_calls, [])

    def test_controller_cleanup_failure_is_first_terminal_result_forever(self):
        class CloseFailure(FakeConnection):
            def close(self):
                self.closed += 1
                raise RuntimeError("hostile close detail")

        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        service.start(platform="linux", enabled=True)
        connection = CloseFailure(BROKER, [b"not-a-frame"])
        first = service.connect(
            connector=lambda *_args: connection,
            observer=observer_for(BROKER), now_ms=NOW,
            monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        expected = {"ok": False, "code": "controller_socket_cleanup_failed",
                    "admission_open": False}
        self.assertEqual(first, expected)
        self.assertEqual(connection.closed, 1)
        self.assertIsNone(service.session)

        connector_calls = []
        repeated_connect = service.connect(
            connector=lambda *_args: connector_calls.append(True),
            observer=observer_for(BROKER), now_ms=NOW, monotonic=lambda: 2.0,
            so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
            scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
        )
        self.assertEqual(repeated_connect, expected)
        self.assertEqual(service.disconnect(), expected)
        self.assertEqual(service.stop(), expected)
        self.assertEqual(service.stop(), expected)
        self.assertEqual(connector_calls, [])
        self.assertEqual(connection.closed, 1)

    def test_first_monotonic_failure_is_session_owned_and_not_retained(self):
        service = PersistentControllerService(
            CONFIG, epoch_factory=lambda: CONTROLLER_EPOCH,
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        service.start(platform="linux", enabled=True)
        controller_connection = FakeConnection(BROKER, [])
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "handshake_refused"):
            service.attach(
                controller_connection, observer=observer_for(BROKER), now_ms=NOW,
                monotonic=lambda: (_ for _ in ()).throw(RuntimeError("clock")),
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertEqual(controller_connection.closed, 1)
        self.assertIsNone(service.session)

        broker_connection = FakeConnection(CONTROLLER, [])
        listener = FakeListener((broker_connection,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "handshake_refused"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: (_ for _ in ()).throw(RuntimeError("clock")),
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertEqual(broker_connection.closed, 1)
        self.assertIsNone(endpoint.session)

    def test_exported_types_refuse_hostile_values_with_bounded_error(self):
        hostile = ([], {}, 0, True, None)
        for value in hostile:
            with self.subTest(value=value):
                with self.assertRaises(ControllerServiceV2Error):
                    ProcessIdentity(
                        uid=value, gid=BROKER.gid, pid=BROKER.pid,
                        start_ticks=BROKER.start_ticks,
                        executable_digest=BROKER.executable_digest,
                        unit_digest=BROKER.unit_digest,
                        config_digest=BROKER.config_digest,
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    abstract_controller_address(value, CONFIG.broker_digest)
                with self.assertRaises(ControllerServiceV2Error):
                    ExactProcessIdentityObserver(
                        value, start_reader=lambda _pid: 1,
                        detail_reader=lambda _pid: {},
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    ExactBrokerSelfObserver(
                        value, current_process_identity_reader=lambda: (
                            BROKER.pid, BROKER.uid, BROKER.gid),
                        start_reader=lambda _pid: BROKER.start_ticks,
                        detail_reader=lambda _pid: {},
                        sealed_reader=lambda: CONFIG,
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    PersistentControllerService(
                        value, epoch_factory=lambda: CONTROLLER_EPOCH,
                        owner_factory=lambda: "controller-session-0123456789abcdef",
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    ControllerBrokerSession(
                        FakeConnection(BROKER, []), CONFIG, value,
                        "controller-session-0123456789abcdef",
                        on_terminal=lambda _reason: None,
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    broker.BrokerControllerV2Connection(
                        FakeConnection(CONTROLLER, []), value, BROKER_EPOCH,
                        "broker-session-0123456789",
                    )
                with self.assertRaises(ControllerServiceV2Error):
                    broker.LinuxControllerV2Listener(
                        value, epoch_factory=lambda: BROKER_EPOCH,
                        owner_factory=lambda: "broker-session-0123456789",
                    )
        with self.assertRaises(ControllerServiceV2Error):
            ControllerBrokerSession(
                [], CONFIG, CONTROLLER_EPOCH,
                "controller-session-0123456789abcdef",
                on_terminal=lambda _reason: None,
            )
        with self.assertRaises(ControllerServiceV2Error):
            broker.BrokerControllerV2Connection(
                [], CONFIG, BROKER_EPOCH, "broker-session-0123456789",
            )

    def test_all_injected_failures_are_bounded_and_cleanup_is_once(self):
        failing_identity = ExactProcessIdentityObserver(
            BROKER,
            start_reader=lambda _pid: (_ for _ in ()).throw(RuntimeError("reader")),
            detail_reader=lambda _pid: {},
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "peer_identity_unavailable"):
            failing_identity(BROKER.pid, BROKER.uid, BROKER.gid)

        service = PersistentControllerService(
            CONFIG,
            epoch_factory=lambda: (_ for _ in ()).throw(RuntimeError("epoch")),
            owner_factory=lambda: "controller-session-0123456789abcdef",
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_epoch_invalid"):
            service.start(platform="linux", enabled=True)

        endpoint = broker.LinuxControllerV2Listener(
            CONFIG,
            epoch_factory=lambda: (_ for _ in ()).throw(RuntimeError("epoch")),
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: (_ for _ in ()).throw(RuntimeError("socket")),
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "broker_epoch_invalid"):
                endpoint.start(
                    platform="linux", enabled=True, effective_uid=BROKER.uid,
                    self_observer=self_observer_for(),
                )
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: (_ for _ in ()).throw(RuntimeError("socket")),
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "controller_listener_start_refused"):
                endpoint.start(
                    platform="linux", enabled=True, effective_uid=BROKER.uid,
                    self_observer=self_observer_for(),
                )

        class StageFailure(FakeListener):
            def __init__(self, stage):
                super().__init__()
                self.stage = stage

            def setsockopt(self, *args):
                if self.stage == "setsockopt": raise RuntimeError("injected")
                return super().setsockopt(*args)

            def bind(self, address):
                if self.stage == "bind": raise RuntimeError("injected")
                return super().bind(address)

            def listen(self, backlog):
                if self.stage == "listen": raise RuntimeError("injected")
                return super().listen(backlog)

        for stage in ("setsockopt", "bind", "listen"):
            listener = StageFailure(stage)
            endpoint = broker.LinuxControllerV2Listener(
                CONFIG, epoch_factory=lambda: BROKER_EPOCH,
                owner_factory=lambda: "broker-session-0123456789",
                socket_factory=lambda *_args, value=listener: value,
            )
            with self.subTest(stage=stage), \
                    mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "controller_listener_start_refused"):
                    endpoint.start(
                        platform="linux", enabled=True, effective_uid=BROKER.uid,
                        self_observer=self_observer_for(),
                    )
                self.assertEqual(listener.closed, 1)

        class AcceptFailure(FakeListener):
            def accept(self): raise RuntimeError("injected")

        listener = AcceptFailure()
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_connection_refused"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        endpoint.close()
        self.assertEqual(listener.closed, 1)

        class ConnectionFailure(FakeConnection):
            def __init__(self, stage, packets):
                super().__init__(BROKER, packets)
                self.stage = stage

            def getsockopt(self, *args):
                if self.stage == "getsockopt": raise RuntimeError("injected")
                return super().getsockopt(*args)

            def recvmsg(self, *args):
                if self.stage == "recvmsg": raise RuntimeError("injected")
                return super().recvmsg(*args)

            def sendall(self, value):
                if self.stage == "sendall": raise RuntimeError("injected")
                return super().sendall(value)

            def close(self):
                self.closed += 1
                if self.stage == "close": raise RuntimeError("injected")

        for stage in ("getsockopt", "recvmsg", "sendall"):
            connection = ConnectionFailure(
                stage, [frame(hello(), "broker_to_controller")],
            )
            session = ControllerBrokerSession(
                connection, CONFIG, CONTROLLER_EPOCH,
                "controller-session-0123456789abcdef",
                on_terminal=lambda _reason: None,
            )
            with self.subTest(connection_stage=stage):
                with self.assertRaises(ControllerServiceV2Error):
                    session.handshake(
                        observer=observer_for(BROKER), now_ms=NOW,
                        monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                        scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                        closer=lambda _fd: None,
                    )
                self.assertEqual(connection.closed, 1)

        connection = ConnectionFailure("close", [])
        session = ControllerBrokerSession(
            connection, CONFIG, CONTROLLER_EPOCH,
            "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
        )
        self.assertEqual(session.close()["code"], "controller_socket_cleanup_failed")
        self.assertEqual(connection.closed, 1)

        accepted = ConnectionFailure("close", [])
        listener = FakeListener((accepted,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: [], socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=BROKER.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_socket_cleanup_failed"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(accepted.closed, 1)

        class StartCleanupFailure(FakeListener):
            def setsockopt(self, *_args): raise RuntimeError("injected")
            def close(self):
                self.closed += 1
                raise RuntimeError("injected")

        failed_listener = StartCleanupFailure()
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: "broker-session-0123456789",
            socket_factory=lambda *_args: failed_listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "listener_cleanup_failed"):
                endpoint.start(
                    platform="linux", enabled=True, effective_uid=BROKER.uid,
                    self_observer=self_observer_for(),
                )
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "listener_cleanup_failed"):
                endpoint.start(
                    platform="linux", enabled=True, effective_uid=BROKER.uid,
                    self_observer=self_observer_for(),
                )
        expected_cleanup = {"ok": False, "code": "listener_cleanup_failed",
                            "admission_open": False}
        self.assertEqual(endpoint.close(), expected_cleanup)
        self.assertEqual(endpoint.close(), expected_cleanup)
        self.assertEqual(failed_listener.closed, 1)

        connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
        session = broker.BrokerControllerV2Connection(
            connection, CONFIG, BROKER_EPOCH, "broker-session-0123456789",
            registry_factory=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("registry")),
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "handshake_refused"):
            session.handshake(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 1.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(connection.closed, 1)

    def test_handshake_mutations_fail_closed_without_registry(self):
        cases = (
            ack(sequence=2), ack(digest=DIGESTS[8]),
            {**ack(), "controller_config_digest": DIGESTS[8]},
        )
        for value in cases:
            with self.subTest(value=value):
                # Sequence=2 is schema-valid only until semantic validation.
                try:
                    packet = frame(value, "controller_to_broker")
                except Exception:
                    packet = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                connection = FakeConnection(CONTROLLER, [packet])
                session = broker.BrokerControllerV2Connection(
                    connection, CONFIG, BROKER_EPOCH, "broker-session-0123456789",
                )
                with self.assertRaises(ControllerServiceV2Error):
                    session.handshake(
                        observer=observer_for(CONTROLLER), now_ms=NOW,
                        monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                        scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                        closer=lambda _fd: None,
                    )
                self.assertIsNone(session.registry)
                self.assertFalse(session.admission_open)

    def test_address_and_errors_are_bounded_and_secret_free(self):
        first = abstract_controller_address(MACHINE, CONFIG.broker_digest)
        self.assertEqual(first, abstract_controller_address(MACHINE, CONFIG.broker_digest))
        self.assertLess(len(first), 108)
        error = ControllerServiceV2Error("peer_identity_mismatch")
        self.assertEqual(error.as_dict(), {
            "ok": False, "code": "peer_identity_mismatch", "admission_open": False,
        })
        self.assertNotIn("exception", repr(error.as_dict()).lower())


if __name__ == "__main__":
    unittest.main()
