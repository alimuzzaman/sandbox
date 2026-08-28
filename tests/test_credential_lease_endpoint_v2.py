import hashlib
import struct
import unittest

from sandbox.isolation.credential_controller_service_v2 import (
    ControllerServiceV2Error,
    LEASE_ENDPOINT_ADDRESS_BYTES_V2,
    LEASE_ENDPOINT_V2_REGISTRY,
    lease_endpoint_address_v2,
    lease_endpoint_registry_digest_v2,
)
from tests.test_credential_controller_authority_v2 import (
    BROKER,
    BROKER_EPOCH,
    CONFIG,
    CONTROLLER_EPOCH,
    MACHINE,
    controller_session,
)


AUTHORIZATION_DIGEST = "d" * 64


def address(**changes):
    values = {
        "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH,
        "controller_epoch": CONTROLLER_EPOCH,
        "broker_digest": CONFIG.broker_digest,
        "broker_config_digest": CONFIG.broker.config_digest,
        "controller_config_digest": CONFIG.controller.config_digest,
        "operation_id": "operation-012345",
        "authorization_digest": AUTHORIZATION_DIGEST,
    }
    values.update(changes)
    return lease_endpoint_address_v2(**values)


class LeaseSocket:
    def __init__(self):
        self.timeout = None
        self.connected = []
        self.closed = 0
        self.options = []
        self.passcred = 1

    def settimeout(self, value):
        self.timeout = value

    def setsockopt(self, *args):
        self.options.append(args)

    def connect(self, value):
        self.connected.append(value)

    def getsockopt(self, *args):
        if args == (__import__("socket").SOL_SOCKET, 4):
            return self.passcred
        return struct.pack("3i", BROKER.pid, BROKER.uid, BROKER.gid)

    def sendmsg(self, *_args):
        return 732

    def recvmsg(self, *_args):
        return b"", [], 0, None

    def close(self):
        self.closed += 1


class TestLeaseEndpointAddressV2(unittest.TestCase):
    def test_golden_address_is_exact_abstract_93_bytes(self):
        value = lease_endpoint_address_v2(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, broker_digest="a" * 64,
            broker_config_digest="b" * 64,
            controller_config_digest="c" * 64,
            operation_id="operation-012345",
            authorization_digest="d" * 64,
        )
        self.assertEqual(len(value), LEASE_ENDPOINT_ADDRESS_BYTES_V2)
        self.assertEqual(value, (
            b"\0sandbox-credential-lease-v2-"
            b"53a45cdea1600f84702b2eb05281dcd2f0ca940898e9bd1ee8076d27e2e04871"
        ))
        self.assertNotIn(b"/", value)

    def test_every_bound_field_mutates_address(self):
        baseline = address()
        mutations = {
            "machine_id": "sb-1123456789ab",
            "broker_epoch": "03" * 16,
            "controller_epoch": "04" * 16,
            "broker_digest": "1" * 64,
            "broker_config_digest": "2" * 64,
            "controller_config_digest": "3" * 64,
            "operation_id": "operation-112345",
            "authorization_digest": "4" * 64,
        }
        for name, value in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(address(**{name: value}), baseline)

    def test_invalid_and_hostile_types_are_bounded(self):
        for name, value in (
            ("machine_id", None), ("broker_epoch", True),
            ("operation_id", "../lease"), ("authorization_digest", "A" * 64),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ControllerServiceV2Error, "lease_endpoint_invalid"):
                address(**{name: value})

    def test_registry_digest_is_pinned_and_security_rules_are_covered(self):
        self.assertEqual(
            lease_endpoint_registry_digest_v2(),
            "07a6570d317d943bd4af6f7ac3cfe29934987cdd1b5cdfa54801bf02cc5dc616",
        )
        self.assertEqual(LEASE_ENDPOINT_V2_REGISTRY["address_bytes"], 93)
        self.assertEqual(LEASE_ENDPOINT_V2_REGISTRY["packets_per_endpoint"], 1)
        self.assertEqual(LEASE_ENDPOINT_V2_REGISTRY["lease_terminal_grace_ms"],
                         LEASE_ENDPOINT_V2_REGISTRY["audit_ack_timeout_ms"] +
                         LEASE_ENDPOINT_V2_REGISTRY["lease_ack_timeout_ms"])
        self.assertFalse(LEASE_ENDPOINT_V2_REGISTRY["fallback"])
        changed = dict(LEASE_ENDPOINT_V2_REGISTRY)
        changed["connect_timeout_ms"] = 1001
        encoded = __import__("json").dumps(
            changed, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
        self.assertNotEqual(hashlib.sha256(encoded).hexdigest(),
                            lease_endpoint_registry_digest_v2())

    def test_controller_derives_once_sets_one_second_and_authenticates_peer(self):
        session = controller_session()
        raw = LeaseSocket()
        calls = []
        receipt = session.connect_lease_endpoint_v2(
            operation_id="operation-012345",
            authorization_digest=AUTHORIZATION_DIGEST,
            authorization_expires_at_unix_ms=1_800_000_000_500,
            now_ms=1_800_000_000_000,
            connector=lambda *args: calls.append(args) or raw,
            observer=lambda *_peer: BROKER,
            so_peercred=1, so_passcred=4, scm_credentials=3, scm_rights=2,
            closer=lambda _fd: None,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(raw.timeout, 0.5)
        self.assertEqual(raw.connected, [address()])
        self.assertEqual(raw.options, [(__import__("socket").SOL_SOCKET, 4, 1)])
        self.assertEqual(receipt.operation_id, "operation-012345")
        self.assertEqual(receipt.authorization_digest, AUTHORIZATION_DIGEST)
        self.assertEqual(receipt.lease_address, address())
        self.assertEqual(receipt.authorization_expires_at_unix_ms,
                         1_800_000_000_500)
        self.assertTrue(session.owns_lease_socket(receipt))
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_transport_invalid"):
            receipt.exchange(b"x" * 732, 7, 501)
        receipt.close()
        self.assertEqual(raw.closed, 1)

    def test_passcred_readback_failure_closes_before_receipt_transfer(self):
        session = controller_session()
        raw = LeaseSocket()
        raw.passcred = 0
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_transport_invalid"):
            session.connect_lease_endpoint_v2(
                operation_id="operation-012345",
                authorization_digest=AUTHORIZATION_DIGEST,
                authorization_expires_at_unix_ms=1_800_000_001_000,
                now_ms=1_800_000_000_000, connector=lambda *_args: raw,
                observer=lambda *_peer: BROKER, so_peercred=1, so_passcred=4,
                scm_credentials=3, scm_rights=2, closer=lambda _fd: None)
        self.assertEqual(raw.closed, 1)
        self.assertEqual(session._lease_sockets, {})

    def test_expired_authorization_refuses_before_socket_creation(self):
        session = controller_session()
        calls = []
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_transport_invalid"):
            session.connect_lease_endpoint_v2(
                operation_id="operation-012345",
                authorization_digest=AUTHORIZATION_DIGEST,
                authorization_expires_at_unix_ms=1_800_000_000_000,
                now_ms=1_800_000_000_000,
                connector=lambda *_args: calls.append(1),
                observer=lambda *_peer: BROKER, so_peercred=1, so_passcred=4,
                scm_credentials=3, scm_rights=2, closer=lambda _fd: None)
        self.assertEqual(calls, [])

    def test_receipt_address_mutation_refuses_exact_authorization_binding(self):
        session = controller_session()
        raw = LeaseSocket()
        receipt = session.connect_lease_endpoint_v2(
            operation_id="operation-012345",
            authorization_digest=AUTHORIZATION_DIGEST,
            authorization_expires_at_unix_ms=1_800_000_001_000,
            now_ms=1_800_000_000_000, connector=lambda *_args: raw,
            observer=lambda *_peer: BROKER, so_peercred=1, so_passcred=4,
            scm_credentials=3, scm_rights=2, closer=lambda _fd: None)
        receipt.lease_address = b"\0" + b"x" * 92
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_transport_invalid"):
            session.bind_lease_socket(
            receipt, operation_id="operation-012345",
            authorization_digest=AUTHORIZATION_DIGEST,
            authorization_expires_at_unix_ms=1_800_000_001_000,
            request_deadline_unix_ms=1_800_000_010_000)
        receipt.close()

    def test_connect_failure_closes_exactly_once_and_never_falls_back(self):
        session = controller_session()
        raw = LeaseSocket()
        def refused(_address):
            raise OSError("refused")
        raw.connect = refused
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_transport_invalid"):
            session.connect_lease_endpoint_v2(
                operation_id="operation-012345",
                authorization_digest=AUTHORIZATION_DIGEST,
                authorization_expires_at_unix_ms=1_800_000_001_000,
                now_ms=1_800_000_000_000,
                connector=lambda *_args: raw,
                observer=lambda *_peer: BROKER,
                so_peercred=1, so_passcred=4, scm_credentials=3, scm_rights=2,
                closer=lambda _fd: None,
            )
        self.assertEqual(raw.closed, 1)

    def test_invalid_observer_and_socket_constants_refuse_before_connect(self):
        session = controller_session()
        calls = []
        for observer, so_peercred, scm_rights in (
            (None, 1, 2), (lambda *_peer: BROKER, True, 2),
            (lambda *_peer: BROKER, 1, 0),
        ):
            with self.subTest(observer=observer, so_peercred=so_peercred,
                              scm_rights=scm_rights), self.assertRaisesRegex(
                    ControllerServiceV2Error, "lease_transport_invalid"):
                session.connect_lease_endpoint_v2(
                    operation_id="operation-012345",
                    authorization_digest=AUTHORIZATION_DIGEST,
                    authorization_expires_at_unix_ms=1_800_000_001_000,
                    now_ms=1_800_000_000_000,
                    connector=lambda *_args: calls.append(1) or LeaseSocket(),
                    observer=observer, so_peercred=so_peercred,
                    so_passcred=4, scm_credentials=3, scm_rights=scm_rights,
                    closer=lambda _fd: None,
                )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
