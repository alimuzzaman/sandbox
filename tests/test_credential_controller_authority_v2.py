import hashlib
import importlib.util
from pathlib import Path
from array import array
import struct
import socket
import threading
import unittest
from unittest import mock

from sandbox.isolation.credential_controller_authority_v2 import (
    ControllerAuthorityInterfaces,
    ControllerAuthorityV2Error,
    ControllerOperationAuthorityV2,
)
from sandbox.isolation.credential_controller_protocol_v2 import (
    AuthorizationIdentity,
    AuthorizationRegistry,
    PROTOCOL,
    decode_controller_frame,
    decode_lease_frame,
    digest_document,
    encode_controller_frame,
    encode_lease_frame,
    encode_lease_ack,
)
from sandbox.isolation.credential_controller_service_v2 import (
    ControllerBrokerSession,
    ControllerServiceConfig,
    ControllerServiceV2Error,
    ProcessIdentity,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "credential_broker_t041", ROOT / "tools/native-helper/native-credential-broker.py"
)
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)

NOW = 1_800_000_000_000
MACHINE = "sb-0123456789ab"
BROKER_EPOCH = "01" * 16
CONTROLLER_EPOCH = "02" * 16
DIGESTS = [hashlib.sha256(f"t041-{index}".encode()).hexdigest() for index in range(8)]
EVIDENCE = "evidence-0123456"


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
    effective_isolation_digest=DIGESTS[4], evidence_id=EVIDENCE,
)


class FakeConnection:
    def __init__(self):
        self.sent = []
        self.closed = 0

    def getsockopt(self, *_args):
        return struct.pack("3i", CONTROLLER.pid, CONTROLLER.uid, CONTROLLER.gid)
    def recvmsg(self, *_args): return b"", [], 0, None
    def sendall(self, packet): self.sent.append(packet)
    def close(self): self.closed += 1


class FakeArmedLeaseListener:
    def __init__(self): self.closed = 0
    def close(self): self.closed += 1


def controller_session():
    session = ControllerBrokerSession(
        FakeConnection(), CONFIG, CONTROLLER_EPOCH,
        "controller-session-0123456789abcdef", on_terminal=lambda _reason: None,
    )
    session.authenticated = True
    session.broker_epoch = BROKER_EPOCH
    session.sequences.accept("broker_to_controller", 1)
    session.sequences.accept("controller_to_broker", 1)
    session._next_outgoing = 2
    return session


def claim(operation="operation-012345"):
    return {
        "protocol": PROTOCOL, "type": "CLAIMED_V2", "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "sequence": 2, "reply_to": 2, "claim_state": "claimed",
        "operation_id": operation, "request_digest": DIGESTS[5],
        "binding_id": "binding-01234567", "binding_version": 1,
        "scheme": "https", "host": "api.example.test", "port": 443,
        "method": "POST", "path": "/v1/items", "content_type": "application/json",
        "header_bytes": 24, "body_bytes": 18,
        "request_deadline_unix_ms": NOW + 10_000, "correlation_id": "corr-1",
    }


def interfaces(events, *, auth_form="authorization_bearer", config=CONFIG):
    def binding_authority(value):
        events.append("binding")
        return {"binding_id": value["binding_id"], "binding_version": 1,
                "auth_form": auth_form, "binding_expires_at_unix_ms": NOW + 20_000,
                "source_handle": object()}

    return ControllerAuthorityInterfaces(
        binding_authority=binding_authority,
        source_authority=lambda binding: events.append("source") or binding["source_handle"],
        scope_authority=lambda _binding, _claim: events.append("scope") or True,
        proof_authority=lambda _binding, _claim: events.append("proof") or {
            "policy_digest": config.policy_digest, "proof_digest": config.proof_digest,
            "effective_isolation_digest": config.effective_isolation_digest,
            "evidence_id": config.evidence_id,
        },
        egress_authority=lambda _binding, _claim: events.append("egress") or {
            "egress_digest": config.egress_digest, "broker_digest": config.broker_digest,
        },
        activation_authority=lambda: events.append("activation") or {
            "admission_open": True,
            "activation_expires_at_unix_ms": NOW + 15_000,
        },
        expiry_authority=lambda _values, _now: events.append("expiry") or True,
        resolver=lambda _source: events.append("resolve") or bytearray(b"synthetic-value"),
    )


def authority(events, *, auth_form="authorization_bearer"):
    return ControllerOperationAuthorityV2(
        controller_session(), interfaces(events, auth_form=auth_form),
        decision_id_factory=lambda: "decision-0123456",
        lease_id_factory=lambda: "lease-0123456789",
    )


def lease_exchange(owner, callback=lambda *_args: True, *, durable=True,
                   ack_ancillary=None, ack_flags=0, ack_closer=None,
                   ack_delay_ms=0):
    class LeaseSocket:
        def __init__(self):
            self.ack = b""
            self.timeout_ms = 1000
            self.closed = 0

        def setsockopt(self, *_args):
            return None

        def getsockopt(self, *args):
            if args == (socket.SOL_SOCKET, 4):
                return 1
            return struct.pack("3i", BROKER.pid, BROKER.uid, BROKER.gid)

        def settimeout(self, value):
            self.timeout_ms = int(value * 1000)

        def sendmsg(self, buffers, ancillary):
            packet = buffers[0]
            descriptor = array("i")
            descriptor.frombytes(ancillary[0][2])
            accepted = callback(packet, descriptor[0], self.timeout_ms)
            if accepted is not True or durable is not True:
                self.ack = b""
                return len(packet)
            lease = decode_lease_frame(packet, now_ms=NOW + 1, deadline_caps={
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 15_000,
            "request_deadline_unix_ms": NOW + 10_000,
            })
            self.ack = encode_lease_ack({
            "type": "LEASE_ACK_V2", "machine_id": lease["machine_id"],
            "broker_epoch": lease["broker_epoch"],
            "controller_epoch": lease["controller_epoch"],
            "lease_id": lease["lease_id"], "lease_sequence": lease["lease_sequence"],
            "authorization_digest": lease["authorization_digest"],
            "audit_root_id": "audit-root0123456789",
            "post_phase_id": "audit-post012345678",
            "post_commit_id": "commit-post01234567",
            "outcome_class": "completed", "effect_certainty": "completed",
            "reason_code": "upstream_completed",
            })
            return len(packet)

        def recvmsg(self, _size, _ancillary_size, _flags=0):
            if ack_delay_ms > self.timeout_ms:
                raise socket.timeout
            ancillary = ack_ancillary
            if ancillary is None:
                ancillary = [(socket.SOL_SOCKET, 3, struct.pack(
                    "3i", BROKER.pid, BROKER.uid, BROKER.gid))]
            return self.ack, ancillary, ack_flags, None
        def close(self): self.closed += 1

    return owner.session.accept_lease_socket(
        LeaseSocket(), observer=lambda *_peer: BROKER,
        so_peercred=1, so_passcred=4, scm_credentials=3, scm_rights=2,
        closer=ack_closer or (lambda _fd: None))


def accept_controller_ack(owner, ack):
    owner.session.sequences.accept("broker_to_controller", ack["sequence"])
    owner.session._last_received_frame = dict(ack)
    owner.session._last_received_consumed = False


def accept_controller_claim(owner, value=None, *, now_ms=NOW):
    value = dict(value or claim())
    poll = owner.poll_claim(now_ms=now_ms, wait_deadline_unix_ms=now_ms + 1000)
    value["reply_to"] = poll["sequence"]
    owner.session.sequences.accept("broker_to_controller", value["sequence"])
    owner.session._last_received_frame = dict(value)
    owner.session._last_received_consumed = False
    return value


def decide_claim(owner, value=None, *, now_ms=NOW):
    return owner.decide(accept_controller_claim(owner, value, now_ms=now_ms), now_ms=now_ms)


def authorized_ack(authorization):
    return {
        "type": "AUTHORIZED_V2", "sequence": 3,
        **{key: authorization[key] for key in (
            "protocol", "machine_id", "broker_epoch", "controller_epoch",
            "operation_id", "request_digest", "binding_id", "binding_version",
            "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
        )}, "reply_to": authorization["sequence"],
    }


def guest_request():
    return {
        "machine_id": MACHINE, "binding_id": "binding-01234567",
        "binding_version": 1, "scheme": "https", "host": "api.example.test",
        "port": 443, "method": "POST", "path": "/v1/items",
        "headers": {"content-type": "application/json", "x-guest": "not-projected"},
        "body": b'{"not":"projected"}', "content_type": "application/json",
        "deadline_ms": 10_000, "correlation_id": "corr-1",
    }


def lease_address(authorization):
    return broker.lease_endpoint_address_v2(
        machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
        controller_epoch=CONTROLLER_EPOCH, broker_digest=CONFIG.broker_digest,
        broker_config_digest=CONFIG.broker.config_digest,
        controller_config_digest=CONFIG.controller.config_digest,
        operation_id=authorization["operation_id"],
        authorization_digest=authorization["authorization_digest"],
    )


class TestControllerAuthorityV2(unittest.TestCase):
    def test_claim_poll_is_controller_owned_exact_one_use_and_pre_authority(self):
        events = []
        owner = authority(events)
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            owner.decide(claim(), now_ms=NOW)
        self.assertEqual(events, [])

        poll = owner.poll_claim(now_ms=NOW, wait_deadline_unix_ms=NOW + 100)
        self.assertEqual(poll["type"], "CLAIM_NEXT_V2")
        self.assertEqual(poll["wait_deadline_unix_ms"], NOW + 100)
        unreceived = claim()
        unreceived["reply_to"] = poll["sequence"]
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            owner.decide(unreceived, now_ms=NOW)
        self.assertEqual(events, [])

        owner.session.sequences.accept("broker_to_controller", unreceived["sequence"])
        owner.session._last_received_frame = dict(unreceived)
        owner.session._last_received_consumed = False
        authorization = owner.decide(unreceived, now_ms=NOW)
        self.assertEqual(authorization["type"], "AUTHORIZE_V2")
        authority_events = list(events)
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            owner.decide(unreceived, now_ms=NOW)
        self.assertEqual(events, authority_events)

    def test_stale_and_no_pending_claim_replies_never_call_authorities(self):
        events = []
        owner = authority(events)
        stale = accept_controller_claim(owner, now_ms=NOW)
        owner._claim_poll["wait_deadline_unix_ms"] = NOW + 10
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            owner.decide(stale, now_ms=NOW + 11)
        self.assertEqual(events, [])

        owner = authority(events)
        poll = owner.poll_claim(now_ms=NOW, wait_deadline_unix_ms=NOW + 100)
        no_pending = {
            "protocol": PROTOCOL, "type": "CLAIMED_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 2, "reply_to": poll["sequence"],
            "claim_state": "no_pending", "retry_after_ms": 50,
        }
        owner.session.sequences.accept("broker_to_controller", no_pending["sequence"])
        owner.session._last_received_frame = dict(no_pending)
        owner.session._last_received_consumed = False
        self.assertEqual(owner.decide(no_pending, now_ms=NOW), {
            "ok": True, "code": "no_pending", "retry_after_ms": 50,
        })
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            owner.decide(no_pending, now_ms=NOW)
        self.assertEqual(events, [])

    def test_claim_reply_protocol_sequence_reply_and_epoch_mutations_are_pre_authority(self):
        mutations = {
            "protocol": "credential-broker-controller-v1",
            "sequence": 3,
            "reply_to": 99,
            "machine_id": "sb-ffffffffffff",
            "broker_epoch": "f" * 32,
            "controller_epoch": "e" * 32,
            "claim_state": "no_pending",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                events = []
                owner = authority(events)
                accepted = accept_controller_claim(owner)
                mutated = dict(accepted)
                mutated[field] = value
                with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
                    owner.decide(mutated, now_ms=NOW)
                self.assertEqual(events, [])

    def test_ack_requires_v2_exact_session_accepted_sequence_before_resolution(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        baseline = authorized_ack(authorization)
        for field, value in (("protocol", "credential-broker-controller-v1"),
                             ("sequence", 3), ("reply_to", 99)):
            with self.subTest(field=field):
                mutated = dict(baseline)
                mutated[field] = value
                with self.assertRaisesRegex(ControllerAuthorityV2Error,
                                            "authorization_ack_invalid"):
                    owner.acknowledge_and_dispatch(
                        mutated, now_ms=NOW + 1, lease_sequence=1,
                        memfd_factory=lambda _value: {}, dispatcher=lease_exchange(owner),
                        descriptor_closer=lambda _fd: None,
                    )
                self.assertNotIn("resolve", events)
        accept_controller_ack(owner, baseline)
        owner.acknowledge_and_dispatch(
            baseline, now_ms=NOW + 1, lease_sequence=1,
            memfd_factory=lambda material: {"descriptor": 9,
                "descriptor_size": len(material), "anonymous_memfd": True,
                "close_on_exec": True,
                "seals": {"write", "grow", "shrink", "seal"}},
            dispatcher=lease_exchange(owner), descriptor_closer=lambda _fd: None,
        )
        self.assertEqual(events.count("resolve"), 1)

    def test_import_and_construction_are_inert_and_interfaces_are_singular(self):
        events = []
        owner = authority(events)
        self.assertEqual(events, [])
        self.assertEqual(len(ControllerAuthorityInterfaces.__dataclass_fields__), 8)
        self.assertFalse(owner.session.admission_open)

    def test_decision_uses_all_injected_authorities_and_does_not_resolve(self):
        events = []
        owner = authority(events)
        result = decide_claim(owner)
        self.assertEqual(result["type"], "AUTHORIZE_V2")
        self.assertEqual(result["auth_form"], "authorization_bearer")
        self.assertEqual(events, ["binding", "source", "scope", "proof", "egress",
                                  "activation", "expiry"])
        self.assertNotIn("resolve", events)

    def test_fixed_auth_form_and_exact_digest_mutation(self):
        events = []
        owner = authority(events, auth_form="guest_supplied")
        refused = decide_claim(owner)
        self.assertEqual((refused["type"], refused["reason_code"]),
                         ("REFUSE_V2", "binding_mismatch"))
        self.assertNotIn("resolve", events)

    def test_resolution_occurs_only_after_exact_authorized_ack_and_dispatch_is_one_use(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        malformed = {
            "type": "AUTHORIZED_V2", **{key: authorization[key] for key in (
                "protocol", "machine_id", "broker_epoch", "controller_epoch",
                "operation_id", "request_digest", "binding_id", "binding_version",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "reply_to": authorization["sequence"] + 1, "sequence": 3,
        }
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "authorization_ack_invalid"):
            owner.acknowledge_and_dispatch(
                malformed, now_ms=NOW + 1, lease_sequence=1,
                memfd_factory=lambda _value: {}, dispatcher=lease_exchange(owner),
                descriptor_closer=lambda _fd: None,
            )
        self.assertNotIn("resolve", events)

    def test_exact_732_byte_lease_one_sealed_memfd_and_cleanup(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        ack = {
            "type": "AUTHORIZED_V2", "sequence": 3,
            **{key: authorization[key] for key in (
                "protocol", "machine_id", "broker_epoch", "controller_epoch",
                "operation_id", "request_digest", "binding_id", "binding_version",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "reply_to": authorization["sequence"],
        }
        accept_controller_ack(owner, ack)
        sent, closed, material_after = [], [], []

        def make_memfd(material):
            material_after.append(material)
            return {"descriptor": 17, "descriptor_size": len(material),
                    "anonymous_memfd": True, "close_on_exec": True,
                    "seals": {"write", "grow", "shrink", "seal"}}

        result = owner.acknowledge_and_dispatch(
            ack, now_ms=NOW + 1, lease_sequence=1, memfd_factory=make_memfd,
            dispatcher=lease_exchange(owner, lambda packet, descriptor, timeout: sent.append(
                (packet, descriptor, timeout)) or True),
            descriptor_closer=closed.append,
        )
        self.assertEqual(result["code"], "upstream_completed")
        self.assertEqual((len(sent[0][0]), sent[0][1], closed), (732, 17, [17]))
        self.assertEqual(material_after[0], bytearray(len(material_after[0])))
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "authorization_ack_invalid"):
            owner.acknowledge_and_dispatch(
                ack, now_ms=NOW + 2, lease_sequence=1, memfd_factory=make_memfd,
                dispatcher=lease_exchange(owner), descriptor_closer=closed.append,
            )

    def test_dispatch_failure_is_one_attempt_wipes_material_and_closes_descriptor(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        ack = {
            "type": "AUTHORIZED_V2", "sequence": 3,
            **{key: authorization[key] for key in (
                "protocol", "machine_id", "broker_epoch", "controller_epoch",
                "operation_id", "request_digest", "binding_id", "binding_version",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "reply_to": authorization["sequence"],
        }
        accept_controller_ack(owner, ack)
        attempts, closed, retained = [], [], []

        def memfd(material):
            retained.append(material)
            return {"descriptor": 71, "descriptor_size": len(material),
                    "anonymous_memfd": True, "close_on_exec": True,
                    "seals": {"write", "grow", "shrink", "seal"}}

        with self.assertRaisesRegex(ControllerAuthorityV2Error, "lease_ack_invalid"):
            owner.acknowledge_and_dispatch(
                ack, now_ms=NOW + 1, lease_sequence=1, memfd_factory=memfd,
                dispatcher=lease_exchange(owner, lambda *_args: attempts.append(1) or False),
                descriptor_closer=closed.append,
            )
        self.assertEqual((attempts, closed), ([1], [71]))
        self.assertEqual(retained[0], bytearray(len(retained[0])))

    def test_connected_lease_ack_accepts_r_plus_2000_and_refuses_r_plus_2001(self):
        for delay_ms, accepted in ((11_999, True), (12_000, False)):
            with self.subTest(delay_ms=delay_ms):
                events = []
                owner = authority(events)
                authorization = decide_claim(owner)
                ack = {
                    "type": "AUTHORIZED_V2", "sequence": 3,
                    **{key: authorization[key] for key in (
                        "protocol", "machine_id", "broker_epoch", "controller_epoch",
                        "operation_id", "request_digest", "binding_id", "binding_version",
                        "decision_id", "authorization_digest",
                        "authorization_expires_at_unix_ms",
                    )}, "reply_to": authorization["sequence"],
                }
                accept_controller_ack(owner, ack)
                call = lambda: owner.acknowledge_and_dispatch(
                    ack, now_ms=NOW + 1, lease_sequence=1,
                    memfd_factory=lambda material: {
                        "descriptor": 93, "descriptor_size": len(material),
                        "anonymous_memfd": True, "close_on_exec": True,
                        "seals": {"write", "grow", "shrink", "seal"}},
                    dispatcher=lease_exchange(owner, ack_delay_ms=delay_ms),
                    descriptor_closer=lambda _fd: None)
                if accepted:
                    self.assertEqual(call()["code"], "upstream_completed")
                else:
                    with self.assertRaisesRegex(ControllerAuthorityV2Error,
                                                "lease_dispatch_invalid"):
                        call()

    def test_lease_ack_requires_exact_credentials_and_refuses_all_rights(self):
        credentials = (socket.SOL_SOCKET, 3, struct.pack(
            "3i", BROKER.pid, BROKER.uid, BROKER.gid))
        cases = {
            "rights": [credentials, (socket.SOL_SOCKET, 2,
                                      array("i", [91]).tobytes())],
            "duplicate_credentials": [credentials, credentials],
            "unknown": [credentials, (socket.SOL_SOCKET, 9999, b"x")],
        }
        for name, ancillary in cases.items():
            with self.subTest(name=name):
                events = []
                owner = authority(events)
                authorization = decide_claim(owner)
                ack = {
                    "type": "AUTHORIZED_V2", "sequence": 3,
                    **{key: authorization[key] for key in (
                        "protocol", "machine_id", "broker_epoch", "controller_epoch",
                        "operation_id", "request_digest", "binding_id", "binding_version",
                        "decision_id", "authorization_digest",
                        "authorization_expires_at_unix_ms",
                    )}, "reply_to": authorization["sequence"],
                }
                accept_controller_ack(owner, ack)
                rights_closed = []
                dispatcher = lease_exchange(
                    owner, ack_ancillary=ancillary, ack_closer=rights_closed.append)
                with self.assertRaisesRegex(ControllerAuthorityV2Error,
                                            "lease_ack_invalid"):
                    owner.acknowledge_and_dispatch(
                        ack, now_ms=NOW + 1, lease_sequence=1,
                        memfd_factory=lambda material: {
                            "descriptor": 92, "descriptor_size": len(material),
                            "anonymous_memfd": True, "close_on_exec": True,
                            "seals": {"write", "grow", "shrink", "seal"}},
                        dispatcher=dispatcher, descriptor_closer=lambda _fd: None,
                    )
                self.assertEqual(dispatcher.operation_id,
                                 authorization["operation_id"])
                self.assertEqual(dispatcher.authorization_digest,
                                 authorization["authorization_digest"])
                self.assertTrue(dispatcher._used)
                self.assertTrue(dispatcher._closed)
                self.assertEqual(rights_closed, [91] if name == "rights" else [])

    def test_ack_rights_cleanup_failure_runs_full_session_cleanup_once(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        ack = {
            "type": "AUTHORIZED_V2", "sequence": 3,
            **{key: authorization[key] for key in (
                "protocol", "machine_id", "broker_epoch", "controller_epoch",
                "operation_id", "request_digest", "binding_id", "binding_version",
                "decision_id", "authorization_digest",
                "authorization_expires_at_unix_ms",
            )}, "reply_to": authorization["sequence"],
        }
        accept_controller_ack(owner, ack)
        second = lease_exchange(owner)
        cleanup_attempts = []
        def fail_right_close(descriptor):
            cleanup_attempts.append(descriptor)
            raise OSError("close refused")
        credentials = (socket.SOL_SOCKET, 3, struct.pack(
            "3i", BROKER.pid, BROKER.uid, BROKER.gid))
        failing = lease_exchange(
            owner, ack_ancillary=[
                credentials,
                (socket.SOL_SOCKET, 2, array("i", [93]).tobytes()),
            ],
            ack_closer=fail_right_close,
        )
        with self.assertRaisesRegex(ControllerAuthorityV2Error,
                                    "lease_dispatch_invalid"):
            owner.acknowledge_and_dispatch(
                ack, now_ms=NOW + 1, lease_sequence=1,
                memfd_factory=lambda material: {
                    "descriptor": 94, "descriptor_size": len(material),
                    "anonymous_memfd": True, "close_on_exec": True,
                    "seals": {"write", "grow", "shrink", "seal"}},
                dispatcher=failing, descriptor_closer=lambda _fd: None)
        self.assertEqual(cleanup_attempts, [93])
        self.assertEqual(owner.session.connection.closed, 1)
        self.assertEqual(second._connection.closed, 1)
        self.assertEqual(failing._connection.closed, 1)
        self.assertEqual(owner.session._lease_sockets, {})
        expected = {"ok": False, "code": "packet_rights_cleanup_failed",
                    "admission_open": False}
        self.assertEqual(owner.session.close(), expected)
        self.assertEqual(owner.session.close(), expected)
        self.assertEqual(owner.session.connection.closed, 1)
        self.assertEqual(second._connection.closed, 1)
        self.assertEqual(cleanup_attempts, [93])

    def test_descriptor_cleanup_failure_is_sticky_and_never_reported_success(self):
        events = []
        owner = authority(events)
        authorization = decide_claim(owner)
        ack = {
            "type": "AUTHORIZED_V2", "sequence": 3,
            **{key: authorization[key] for key in (
                "protocol", "machine_id", "broker_epoch", "controller_epoch",
                "operation_id", "request_digest", "binding_id", "binding_version",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "reply_to": authorization["sequence"],
        }
        accept_controller_ack(owner, ack)
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "descriptor_cleanup_failed"):
            owner.acknowledge_and_dispatch(
                ack, now_ms=NOW + 1, lease_sequence=1,
                memfd_factory=lambda material: {"descriptor": 81,
                    "descriptor_size": len(material), "anonymous_memfd": True,
                    "close_on_exec": True,
                    "seals": {"write", "grow", "shrink", "seal"}},
                dispatcher=lease_exchange(owner),
                descriptor_closer=lambda _fd: (_ for _ in ()).throw(OSError("host detail")),
            )
        self.assertEqual(owner.close(), {"ok": False, "code": "descriptor_cleanup_failed"})

    def test_all_injected_dispatch_failures_are_bounded_wiped_and_close_returned_fd(self):
        cases = ("resolver", "memfd", "metadata", "lease_id", "dispatcher")
        for case in cases:
            with self.subTest(case=case):
                events = []
                base_interfaces = interfaces(events)
                if case == "resolver":
                    selected = ControllerAuthorityInterfaces(
                        **{**{name: getattr(base_interfaces, name)
                             for name in base_interfaces.__dataclass_fields__ if name != "resolver"},
                           "resolver": lambda _source: (_ for _ in ()).throw(
                               RuntimeError("hostile resolver text"))}
                    )
                else:
                    selected = base_interfaces
                owner = ControllerOperationAuthorityV2(
                    controller_session(), selected,
                    decision_id_factory=lambda: "decision-0123456",
                    lease_id_factory=(
                        (lambda: (_ for _ in ()).throw(RuntimeError("hostile lease text")))
                        if case == "lease_id" else lambda: "lease-0123456789"),
                )
                authorization = decide_claim(owner)
                ack = authorized_ack(authorization)
                accept_controller_ack(owner, ack)
                retained, closed = [], []

                def memfd(material):
                    retained.append(material)
                    if case == "memfd":
                        raise RuntimeError("hostile memfd text")
                    value = {"descriptor": 91, "descriptor_size": len(material),
                             "anonymous_memfd": True, "close_on_exec": True,
                             "seals": {"write", "grow", "shrink", "seal"}}
                    if case == "metadata":
                        value["anonymous_memfd"] = False
                    return value

                def dispatch(*_args):
                    if case == "dispatcher":
                        raise RuntimeError("hostile dispatcher text")
                    return True

                expected = "source_unavailable" if case == "resolver" else "lease_dispatch_invalid"
                with self.assertRaisesRegex(ControllerAuthorityV2Error, expected) as raised:
                    owner.acknowledge_and_dispatch(
                        ack, now_ms=NOW + 1, lease_sequence=1,
                        memfd_factory=memfd, dispatcher=lease_exchange(owner, dispatch),
                        descriptor_closer=closed.append,
                    )
                self.assertNotIn("hostile", str(raised.exception))
                if case in {"metadata", "lease_id", "dispatcher"}:
                    self.assertEqual(closed, [91])
                if retained:
                    self.assertEqual(retained[0], bytearray(len(retained[0])))


class TestBrokerOperationAuthorityV2(unittest.TestCase):
    def setUp(self):
        self.connection = broker.BrokerControllerV2Connection(
            FakeConnection(), CONFIG, BROKER_EPOCH, "broker-owner-0123456789",
            lease_endpoint_factory=lambda *_args: FakeArmedLeaseListener(),
        )
        self.connection.controller_epoch = CONTROLLER_EPOCH
        self.connection.authenticated = True
        self.connection.admission_open = False
        self.connection.sequences.accept("broker_to_controller", 1)
        self.connection.sequences.accept("controller_to_broker", 1)
        self.connection.registry = AuthorizationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
        )
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: "operation-012345",
        )
        self.connection._lease_sequences = broker.LeaseSequenceV2(
            CONTROLLER_EPOCH, BROKER_EPOCH
        )
        self.connection.set_admission_v2(
            admission_open=True, activation_expires_at_unix_ms=NOW + 15_000,
            now_ms=NOW,
        )
        self._guest_capability = None
        def guest_submit(request, *, connection_identity):
            if self._guest_capability is None:
                self._guest_capability = self.connection.bind_guest_submit_capability_v2(
                    self.connection.mint_guest_bridge_receipt_v2(),
                    canonical_guest_validator=lambda _request: True,
                    now_ms=lambda: NOW,
                )
            return self._guest_capability(
                request, connection_identity=connection_identity)
        self.guest_submit = guest_submit

    def _claim_and_authorize(self):
        claimed, message = self._claimed_authorization_message()
        ack = self.connection.handle_authority_v2(message, now_ms=NOW)
        return claimed, message, ack

    def _receipt(self, operation_id, connection=None, peer=CONTROLLER):
        return self.connection._authenticated_lease_socket_receipt_v2(
            operation_id, connection or FakeConnection(),
            observer=lambda *_kernel_peer: peer, so_peercred=1)

    def _claimed_authorization_message(self, connection_identity="guest-1"):
        self.guest_submit(guest_request(), connection_identity=connection_identity)
        claimed = self.connection.operations.claim_next(
            owner=self.connection.owner, reply_to=2, sequence=2, now_ms=NOW,
        )
        self.connection._claim_anchor = {
            key: claimed[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
            )
        }
        values = {
            "protocol": PROTOCOL, "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "operation_id": claimed["operation_id"],
            "request_digest": claimed["request_digest"],
            "binding_id": claimed["binding_id"], "binding_version": 1,
            "auth_form": "authorization_bearer",
            "policy_digest": CONFIG.policy_digest, "egress_digest": CONFIG.egress_digest,
            "broker_digest": CONFIG.broker_digest, "proof_digest": CONFIG.proof_digest,
            "effective_isolation_digest": CONFIG.effective_isolation_digest,
            "evidence_id": CONFIG.evidence_id,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "decision_id": "decision-0123456",
        }
        message = {**values, "type": "AUTHORIZE_V2", "sequence": 3,
                   "authorization_digest": digest_document("authorization_digest", values)}
        return claimed, message

    def test_real_claim_path_uses_private_receipt_temporal_context_and_records_anchor(self):
        self.guest_submit(guest_request(), connection_identity="real-claim")
        claim_next = {
            "protocol": PROTOCOL, "type": "CLAIM_NEXT_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 2, "wait_deadline_unix_ms": NOW + 100,
        }
        packet = encode_controller_frame(
            claim_next, direction="controller_to_broker", now_ms=NOW,
        )
        with mock.patch.object(
            broker, "receive_authenticated_packet_v2",
            return_value=(packet, CONTROLLER),
        ):
            accepted = self.connection.receive_frame(
                observer=lambda *_args: CONTROLLER, now_ms=NOW,
                so_peercred=1, scm_credentials=2, scm_rights=3,
                closer=lambda _fd: None,
            )
        sent = self.connection.handle_authority_v2(accepted, now_ms=NOW)
        decoded = decode_controller_frame(
            self.connection.connection.sent[-1], direction="broker_to_controller",
            now_ms=NOW, temporal_context={
                "original_guest_request_receipt_unix_ms": NOW,
            },
        )
        self.assertEqual((sent, decoded), (decoded, sent))
        self.assertEqual(self.connection._claim_anchor, {
            key: decoded[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
            )
        })
        self.assertNotIn("receipt", repr(decoded).lower())
        self.assertNotIn("receipt", repr(self.connection._claim_anchor).lower())

    def _lease_packet(self, authorization, *, lease_sequence=1):
        lease = {
            "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
            "controller_epoch": CONTROLLER_EPOCH,
            **{key: authorization[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
                "auth_form", "policy_digest", "egress_digest", "broker_digest",
                "proof_digest", "effective_isolation_digest", "evidence_id",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "lease_id": f"lease-{lease_sequence:010d}",
            "lease_sequence": lease_sequence,
            "lease_expires_at_unix_ms": NOW + 4_000, "descriptor_size": 8,
        }
        return encode_lease_frame(lease, now_ms=NOW, deadline_caps={
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 15_000,
            "request_deadline_unix_ms": NOW + 10_000,
        })

    @staticmethod
    def _descriptor_observer(_fd):
        return {"anonymous_memfd": True, "close_on_exec": True, "size": 8,
                "seals": {"write", "grow", "shrink", "seal"}}

    def test_claim_projection_excludes_guest_header_values_and_body(self):
        self.guest_submit(guest_request(), connection_identity="guest-1")
        claimed = self.connection.operations.claim_next(
            owner=self.connection.owner, reply_to=2, sequence=2, now_ms=NOW,
        )
        encoded = repr(claimed)
        self.assertNotIn("not-projected", encoded)
        self.assertNotIn('{"not":"projected"}', encoded)
        self.assertEqual(claimed["claim_state"], "claimed")

    def test_no_pending_is_exact_and_contains_no_operation_identity(self):
        value = self.connection.operations.claim_next(
            owner=self.connection.owner, reply_to=2, sequence=2, now_ms=NOW,
        )
        self.assertEqual(set(value), {
            "protocol", "type", "machine_id", "broker_epoch", "controller_epoch",
            "sequence", "reply_to", "claim_state", "retry_after_ms",
        })
        self.assertEqual(value["claim_state"], "no_pending")

    def test_guest_validation_precedes_operation_identity_and_capacity_is_16_total(self):
        generated = []
        ids = iter(f"operation-{index:06d}" for index in range(16))
        registry = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: generated.append(1) or next(ids),
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "request_invalid"):
            registry.submit(guest_request(), connection_identity="denied", now_ms=NOW,
                            canonical_guest_validator=lambda _request: False)
        self.assertEqual(generated, [])
        for index in range(16):
            registry.submit(guest_request(), connection_identity=f"guest-{index}", now_ms=NOW,
                            canonical_guest_validator=lambda _request: True)
        with self.assertRaisesRegex(ControllerServiceV2Error, "capacity_exceeded"):
            registry.submit(guest_request(), connection_identity="guest-16", now_ms=NOW,
                            canonical_guest_validator=lambda _request: True)

    def test_authorized_ack_precedes_lease_and_exact_fields_are_enforced(self):
        claimed, authorization, ack = self._claim_and_authorize()
        self.assertEqual(ack["type"], "AUTHORIZED_V2")
        self.assertEqual(ack["authorization_digest"], authorization["authorization_digest"])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "authorized")

    def test_lease_endpoint_is_armed_before_authorized_ack(self):
        events = []
        original_send = self.connection.connection.sendall
        self.connection.connection.sendall = lambda packet: (
            events.append("authorized"), original_send(packet)
        )[-1]

        class Armed:
            def close(self):
                events.append("closed")

        self.connection._lease_endpoint_factory = lambda address, endpoint: (
            events.append(("armed", address, endpoint.operation_id)) or Armed()
        )
        claimed, authorization, ack = self._claim_and_authorize()
        self.assertEqual(events[0][0], "armed")
        self.assertEqual(events[1], "authorized")
        self.assertEqual(len(events[0][1]), 93)
        self.assertEqual(events[0][2], claimed["operation_id"])
        self.assertEqual(ack["authorization_digest"], authorization["authorization_digest"])

    def test_lease_endpoint_collision_is_terminal_before_authorized_ack(self):
        sent_before = len(self.connection.connection.sent)
        self.connection._lease_endpoint_factory = lambda *_args: (_ for _ in ()).throw(
            OSError(__import__("errno").EADDRINUSE, "occupied")
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_endpoint_collision"):
            self._claim_and_authorize()
        self.assertEqual(len(self.connection.connection.sent), sent_before)
        self.assertFalse(self.connection.admission_open)

    def test_missing_lease_endpoint_factory_refuses_before_authorized_ack(self):
        self.connection._lease_endpoint_factory = None
        sent_before = len(self.connection.connection.sent)
        claimed, _authorization = self._claimed_authorization_message()
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_endpoint_unavailable"):
            self.connection.handle_authority_v2(_authorization, now_ms=NOW)
        self.assertEqual(len(self.connection.connection.sent), sent_before)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertNotIn(claimed["operation_id"], self.connection._lease_endpoints)

    def test_lease_endpoint_factory_without_listener_refuses_before_ack(self):
        self.connection._lease_endpoint_factory = lambda *_args: None
        sent_before = len(self.connection.connection.sent)
        claimed, authorization = self._claimed_authorization_message()
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_endpoint_unavailable"):
            self.connection.handle_authority_v2(authorization, now_ms=NOW)
        self.assertEqual(len(self.connection.connection.sent), sent_before)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertEqual(self.connection._lease_endpoints, {})

    def test_lease_listener_prescans_all_rights_before_malformed_rejection(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection._lease_endpoints[claimed["operation_id"]]
        accepted = FakeConnection()
        accepted.settimeout = lambda _value: None
        rights = array("i", [41, 42]).tobytes()
        accepted.recvmsg = lambda *_args: (
            b"x" * 732,
            [(socket.SOL_SOCKET, 9999, b"bad"),
             (socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)],
            getattr(socket, "MSG_CTRUNC", 0), None,
        )

        class Listener:
            def __init__(self): self.closed = 0
            def setsockopt(self, *_args): pass
            def bind(self, _address): pass
            def listen(self, _backlog): pass
            def settimeout(self, _value): pass
            def accept(self): return accepted, None
            def close(self): self.closed += 1

        listener = Listener()
        closed = []
        selected_address = lease_address(authorization)
        endpoint.address = selected_address
        transport = broker.LinuxLeaseOperationV2Listener(
            selected_address, endpoint,
            observer=lambda *_peer: CONTROLLER, now_ms=lambda: NOW,
            descriptor_observer=self._descriptor_observer,
            descriptor_closer=closed.append,
            socket_factory=lambda *_args: listener,
            so_peercred=1, so_passcred=2,
            scm_credentials=3, scm_rights=socket.SCM_RIGHTS,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_ancillary_invalid"):
            transport.receive_once()
        self.assertEqual(closed, [41, 42])
        self.assertEqual(accepted.closed, 1)
        self.assertEqual(listener.closed, 1)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertEqual(self.connection.operations.active_count(), 0)
        self.assertNotIn(claimed["operation_id"], self.connection._lease_endpoints)

    def test_lease_listener_reports_rights_cleanup_failure_without_retry(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection._lease_endpoints[claimed["operation_id"]]
        accepted = FakeConnection()
        accepted.settimeout = lambda _value: None
        accepted.recvmsg = lambda *_args: (
            b"bad", [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                       array("i", [44]).tobytes())], 0, None)

        class Listener:
            def setsockopt(self, *_args): pass
            def bind(self, _address): pass
            def listen(self, _backlog): pass
            def settimeout(self, _value): pass
            def accept(self): return accepted, None
            def close(self): pass

        attempts = []
        def refuse_close(value):
            attempts.append(value)
            raise OSError("close refused")
        selected_address = lease_address(authorization)
        endpoint.address = selected_address
        transport = broker.LinuxLeaseOperationV2Listener(
            selected_address, endpoint,
            observer=lambda *_peer: CONTROLLER, now_ms=lambda: NOW,
            descriptor_observer=self._descriptor_observer,
            descriptor_closer=refuse_close,
            socket_factory=lambda *_args: Listener(),
            so_peercred=1, so_passcred=2,
            scm_credentials=3, scm_rights=socket.SCM_RIGHTS,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "descriptor_cleanup_failed"):
            transport.receive_once()
        with self.assertRaisesRegex(ControllerServiceV2Error, "descriptor_cleanup_failed"):
            transport.close()
        self.assertEqual(attempts, [44])
        self.assertEqual(self.connection.close(), {
            "ok": False, "code": "descriptor_cleanup_failed",
            "admission_open": False})
        self.assertEqual(attempts, [44])

    def test_first_attempt_peer_mismatch_terminalizes_authorized_operation(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection._lease_endpoints[claimed["operation_id"]]
        accepted = FakeConnection()
        accepted.settimeout = lambda _value: None
        reads = []
        accepted.recvmsg = lambda *_args: reads.append(1) or (b"", [], 0, None)

        class Listener:
            def __init__(self): self.closed = 0
            def setsockopt(self, *_args): pass
            def bind(self, _address): pass
            def listen(self, _backlog): pass
            def settimeout(self, _value): pass
            def accept(self): return accepted, None
            def close(self): self.closed += 1

        listener = Listener()
        selected_address = lease_address(authorization)
        endpoint.address = selected_address
        transport = broker.LinuxLeaseOperationV2Listener(
            selected_address, endpoint, observer=lambda *_peer: BROKER,
            now_ms=lambda: NOW, descriptor_observer=self._descriptor_observer,
            descriptor_closer=lambda _fd: None,
            socket_factory=lambda *_args: listener,
            so_peercred=1, so_passcred=2, scm_credentials=3,
            scm_rights=socket.SCM_RIGHTS,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_ancillary_invalid"):
            transport.receive_once()
        self.assertEqual(reads, [])
        self.assertEqual(accepted.closed, 1)
        self.assertEqual(listener.closed, 1)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertNotIn(claimed["operation_id"], self.connection._lease_endpoints)

    def test_sixteen_failed_deliveries_release_authorization_registry_capacity(self):
        ids = iter(f"operation-fail{index:02d}" for index in range(16))
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: next(ids))
        for index in range(16):
            claimed, authorization = self._claimed_authorization_message(
                connection_identity=f"guest-fail-{index}")
            _ack = self.connection.handle_authority_v2(authorization, now_ms=NOW)
            endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
            with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
                endpoint.accept(
                    b"x" * 732, [], descriptor_observer=self._descriptor_observer,
                    descriptor_closer=lambda _fd: None, now_ms=NOW,
                    accepted_socket_receipt=self._receipt(claimed["operation_id"]),
                )
            listener = endpoint.listener
            endpoint.close()
            self.connection._finish_lease_endpoint_attempt_v2(
                claimed["operation_id"], listener, failed=True)
            self.assertEqual(self.connection.registry._items, {})
            self.assertEqual(self.connection.registry._tombstones, {})
            self.assertEqual(self.connection.operations.state(
                claimed["operation_id"]), "refused")

        extra = AuthorizationIdentity(
            owner=self.connection.owner, machine_id=MACHINE,
            broker_epoch=BROKER_EPOCH, controller_epoch=CONTROLLER_EPOCH,
            operation_id="operation-extra17", request_digest=DIGESTS[5],
            binding_id="binding-extra123", binding_version=1,
            decision_id="decision-extra123",
            authorization_digest="e" * 64,
            expires_at_unix_ms=NOW + 1000,
            binding_expires_at_unix_ms=NOW + 2000,
            activation_expires_at_unix_ms=NOW + 5000,
            request_deadline_unix_ms=NOW + 5000)
        self.connection.registry.insert(extra, now_ms=NOW)
        self.assertIn(extra.operation_id, self.connection.registry._items)

    def test_quiesce_closes_blocked_accepted_recvmsg_exactly_once(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        entered = threading.Event()
        released = threading.Event()

        class Accepted(FakeConnection):
            def __init__(self):
                super().__init__()
                self.settimeout = lambda _value: None
            def recvmsg(self, *_args):
                entered.set()
                released.wait(2)
                raise OSError("closed")
            def close(self):
                self.closed += 1
                released.set()

        accepted = Accepted()
        class Listener:
            def __init__(self): self.closed = 0
            def setsockopt(self, *_args): pass
            def bind(self, _address): pass
            def listen(self, _backlog): pass
            def settimeout(self, _value): pass
            def accept(self): return accepted, None
            def close(self): self.closed += 1

        listener = Listener()
        selected_address = lease_address(authorization)
        endpoint.address = selected_address
        transport = broker.LinuxLeaseOperationV2Listener(
            selected_address, endpoint, observer=lambda *_peer: CONTROLLER,
            now_ms=lambda: NOW, descriptor_observer=self._descriptor_observer,
            descriptor_closer=lambda _fd: None,
            socket_factory=lambda *_args: listener,
            so_peercred=1, so_passcred=2, scm_credentials=3,
            scm_rights=socket.SCM_RIGHTS)
        endpoint.listener = transport
        errors = []
        def receive():
            try:
                transport.receive_once()
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=receive, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.connection.set_admission_v2(
            admission_open=False, activation_expires_at_unix_ms=None,
            now_ms=NOW)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(accepted.closed, 1)
        self.assertEqual(listener.closed, 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ControllerServiceV2Error)
        self.assertEqual(self.connection._lease_endpoints, {})

    def test_lease_listener_accepts_one_exact_packet_and_transfers_ownership_once(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection._lease_endpoints[claimed["operation_id"]]
        accepted = FakeConnection()
        accepted.settimeout = lambda _value: None
        packet = self._lease_packet(authorization)
        accepted.recvmsg = lambda *_args: (
            packet,
            [(socket.SOL_SOCKET, 3,
              struct.pack("3i", CONTROLLER.pid, CONTROLLER.uid, CONTROLLER.gid)),
             (socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [43]).tobytes())],
            0, None,
        )

        class Listener:
            def __init__(self): self.closed = 0
            def setsockopt(self, *_args): pass
            def bind(self, _address): pass
            def listen(self, _backlog): pass
            def settimeout(self, _value): pass
            def accept(self): return accepted, None
            def close(self): self.closed += 1

        listener = Listener()
        closed = []
        selected_address = lease_address(authorization)
        endpoint.address = selected_address
        transport = broker.LinuxLeaseOperationV2Listener(
            selected_address, endpoint,
            observer=lambda *_peer: CONTROLLER, now_ms=lambda: NOW,
            descriptor_observer=self._descriptor_observer,
            descriptor_closer=closed.append,
            socket_factory=lambda *_args: listener,
            so_peercred=1, so_passcred=2,
            scm_credentials=3, scm_rights=socket.SCM_RIGHTS,
        )
        self.assertEqual(transport.receive_once()["code"], "lease_bound")
        self.assertEqual(listener.closed, 1)
        self.assertEqual(accepted.closed, 0)
        self.assertEqual(closed, [])
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_endpoint_consumed"):
            transport.receive_once()
        self.connection.close("test_complete")
        self.assertEqual(accepted.closed, 1)
        self.assertEqual(closed, [43])

    def test_every_known_authorization_mismatch_terminalizes_and_corrected_retry_refuses(self):
        mutations = {
            "auth_form": "guest_supplied", "policy_digest": "f" * 64,
            "evidence_id": "evidence-9999999",
            "authorization_expires_at_unix_ms": NOW + 6_000,
            "binding_id": "binding-99999999",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.setUp()
                claimed, valid = self._claimed_authorization_message()
                mutated = dict(valid)
                mutated[field] = value
                if field not in {"auth_form", "authorization_expires_at_unix_ms"}:
                    digest_values = {key: mutated[key] for key in (
                        "protocol", "machine_id", "broker_epoch", "controller_epoch",
                        "operation_id", "request_digest", "binding_id", "binding_version",
                        "auth_form", "policy_digest", "egress_digest", "broker_digest",
                        "proof_digest", "effective_isolation_digest", "evidence_id",
                        "binding_expires_at_unix_ms", "authorization_expires_at_unix_ms",
                        "decision_id",
                    )}
                    mutated["authorization_digest"] = digest_document(
                        "authorization_digest", digest_values
                    )
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "authorization_mismatch"):
                    self.connection.handle_authority_v2(mutated, now_ms=NOW)
                self.assertEqual(self.connection.operations.state(
                    claimed["operation_id"]), "refused")
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "authorization_mismatch"):
                    self.connection.handle_authority_v2(valid, now_ms=NOW)

    def test_authorization_mismatch_leaves_unrelated_pending_operation_unchanged(self):
        ids = iter(("operation-111111", "operation-222222"))
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: next(ids),
        )
        claimed, valid = self._claimed_authorization_message()
        self.guest_submit(guest_request(), connection_identity="guest-2")
        mutated = dict(valid)
        mutated["broker_digest"] = "f" * 64
        with self.assertRaisesRegex(ControllerServiceV2Error, "authorization_mismatch"):
            self.connection.handle_authority_v2(mutated, now_ms=NOW)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        self.assertEqual(self.connection.operations.state("operation-222222"), "pending")

    def test_cross_operation_authorize_mismatch_terminalizes_recorded_claim_only(self):
        ids = iter(("operation-777777", "operation-888888"))
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: next(ids),
        )
        claimed, valid = self._claimed_authorization_message()
        self.guest_submit(guest_request(), connection_identity="cross-authorize")
        crossed = dict(valid)
        crossed["operation_id"] = "operation-888888"
        with self.assertRaisesRegex(ControllerServiceV2Error, "authorization_mismatch"):
            self.connection.handle_authority_v2(crossed, now_ms=NOW)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        self.assertEqual(self.connection.operations.state("operation-888888"), "pending")
        for retry in (valid, crossed):
            with self.assertRaisesRegex(ControllerServiceV2Error, "authorization_mismatch"):
                self.connection.handle_authority_v2(retry, now_ms=NOW)
        self.assertEqual(self.connection.operations.state("operation-888888"), "pending")

    def test_every_identifiable_refuse_mismatch_terminalizes_only_that_operation(self):
        mutations = {
            "request_digest": "f" * 64,
            "binding_id": "binding-99999999",
            "binding_version": 2,
            "machine_id": "sb-ffffffffffff",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.setUp()
                ids = iter(("operation-555555", "operation-666666"))
                self.connection.operations = broker._V2OperationRegistry(
                    machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
                    controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
                    id_factory=lambda: next(ids),
                )
                claimed, _authorization = self._claimed_authorization_message()
                self.guest_submit(
                    guest_request(), connection_identity="refuse-unrelated")
                valid = {
                    "protocol": PROTOCOL, "type": "REFUSE_V2", "machine_id": MACHINE,
                    "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
                    "sequence": 3, "operation_id": claimed["operation_id"],
                    "request_digest": claimed["request_digest"],
                    "binding_id": claimed["binding_id"],
                    "binding_version": claimed["binding_version"],
                    "decision_id": "decision-0123456", "reason_code": "binding_missing",
                }
                mutated = dict(valid)
                mutated[field] = value
                with self.assertRaisesRegex(ControllerServiceV2Error, "refusal_mismatch"):
                    self.connection.handle_authority_v2(mutated, now_ms=NOW)
                self.assertEqual(self.connection.operations.state(
                    claimed["operation_id"]), "refused")
                self.assertEqual(self.connection.operations.state(
                    "operation-666666"), "pending")
                with self.assertRaisesRegex(ControllerServiceV2Error, "refusal_mismatch"):
                    self.connection.handle_authority_v2(valid, now_ms=NOW)

    def test_cross_operation_refuse_mismatch_terminalizes_recorded_claim_only(self):
        ids = iter(("operation-999991", "operation-999992"))
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: next(ids),
        )
        claimed, _authorization = self._claimed_authorization_message()
        self.guest_submit(guest_request(), connection_identity="cross-refuse")
        valid = {
            "protocol": PROTOCOL, "type": "REFUSE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 3, "operation_id": claimed["operation_id"],
            "request_digest": claimed["request_digest"],
            "binding_id": claimed["binding_id"],
            "binding_version": claimed["binding_version"],
            "decision_id": "decision-0123456", "reason_code": "binding_missing",
        }
        crossed = dict(valid)
        crossed["operation_id"] = "operation-999992"
        with self.assertRaisesRegex(ControllerServiceV2Error, "refusal_mismatch"):
            self.connection.handle_authority_v2(crossed, now_ms=NOW)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        self.assertEqual(self.connection.operations.state("operation-999992"), "pending")
        for retry in (valid, crossed):
            with self.assertRaisesRegex(ControllerServiceV2Error, "refusal_mismatch"):
                self.connection.handle_authority_v2(retry, now_ms=NOW)
        self.assertEqual(self.connection.operations.state("operation-999992"), "pending")

    def test_admission_quiesce_is_irreversible_for_epoch(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        armed_listener = endpoint.listener
        self.assertEqual(self.connection.set_admission_v2(
            admission_open=False, activation_expires_at_unix_ms=None, now_ms=NOW,
        )["code"], "admission_closed")
        for open_value in (True, False):
            with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
                self.connection.set_admission_v2(
                    admission_open=open_value,
                    activation_expires_at_unix_ms=NOW + 1000 if open_value else None,
                    now_ms=NOW,
                )
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.guest_submit(
                guest_request(), connection_identity="after-quiesce")
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.connection.handle_authority_v2({
                "protocol": PROTOCOL, "type": "CLAIM_NEXT_V2", "machine_id": MACHINE,
                "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
                "sequence": 4, "wait_deadline_unix_ms": NOW + 100,
            }, now_ms=NOW)
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.connection.lease_endpoint_v2(claimed["operation_id"])
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.connection.revoke_v2("binding-01234567")
        closed = []
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            endpoint.accept(
                self._lease_packet(authorization), [104],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closed.append, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(closed, [])
        self.assertEqual(armed_listener.closed, 1)
        self.assertEqual(self.connection._lease_endpoints, {})

    def test_revoke_closes_and_removes_unaccepted_armed_listener(self):
        claimed, _authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        listener = endpoint.listener
        self.assertEqual(self.connection.revoke_v2("binding-01234567"), 1)
        self.assertEqual(listener.closed, 1)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertNotIn(claimed["operation_id"], self.connection._lease_endpoints)

    def test_quiesce_removes_armed_listener_and_keeps_cleanup_failure_sticky(self):
        calls = []
        class FailingListener:
            def close(self):
                calls.append(1)
                raise OSError("host detail")
        self.connection._lease_endpoint_factory = lambda *_args: FailingListener()
        claimed, _authorization, _ack = self._claim_and_authorize()
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "lease_endpoint_cleanup_failed"):
            self.connection.set_admission_v2(
                admission_open=False, activation_expires_at_unix_ms=None,
                now_ms=NOW)
        self.assertEqual(self.connection.operations.state(
            claimed["operation_id"]), "refused")
        self.assertEqual(self.connection._lease_endpoints, {})
        self.assertEqual(calls, [1])
        self.assertEqual(self.connection.close(), {
            "ok": False, "code": "lease_endpoint_cleanup_failed",
            "admission_open": False})
        self.assertEqual(calls, [1])

    def test_quiesce_returns_and_preserves_first_cleanup_failure(self):
        self.guest_submit(
            guest_request(), connection_identity="cleanup-pending")

        disconnect_calls = []

        class FailingRegistry:
            def disconnect(self, **_scope):
                disconnect_calls.append(1)
                raise RuntimeError("hostile disconnect text")

        self.connection.registry = FailingRegistry()
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "registry_disconnect_refused") as raised:
            self.connection.set_admission_v2(
                admission_open=False, activation_expires_at_unix_ms=None, now_ms=NOW,
            )
        self.assertNotIn("hostile", str(raised.exception))
        self.assertTrue(all(item["state"] == "refused"
                            for item in self.connection.operations._items.values()))
        for action in ("open", "close", "claim"):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "registry_disconnect_refused"):
                    if action == "open":
                        self.connection.set_admission_v2(
                            admission_open=True,
                            activation_expires_at_unix_ms=NOW + 1000, now_ms=NOW,
                        )
                    elif action == "close":
                        self.connection.set_admission_v2(
                            admission_open=False,
                            activation_expires_at_unix_ms=None, now_ms=NOW,
                        )
                    else:
                        self.connection.handle_authority_v2({}, now_ms=NOW)
        for surface in ("endpoint", "revoke"):
            with self.subTest(surface=surface):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "registry_disconnect_refused"):
                    if surface == "endpoint":
                        self.connection.lease_endpoint_v2("operation-012345")
                    else:
                        self.connection.revoke_v2("binding-01234567")
        self.connection.close()
        self.assertEqual(disconnect_calls, [1])

    def test_revoke_injected_failure_cleans_all_and_is_sticky(self):
        claimed, _authorization, _ack = self._claim_and_authorize()
        calls = []

        class FailingRevokeRegistry:
            def revoke(self, **_scope):
                calls.append("revoke")
                raise RuntimeError("hostile revoke text")

            def disconnect(self, **_scope):
                calls.append("disconnect")

        self.connection.registry = FailingRevokeRegistry()
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "registry_revoke_refused") as raised:
            self.connection.revoke_v2("binding-01234567")
        self.assertNotIn("hostile", str(raised.exception))
        self.assertEqual(calls, ["revoke", "disconnect"])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        for _index in range(2):
            with self.assertRaisesRegex(ControllerServiceV2Error,
                                        "registry_revoke_refused"):
                self.connection.revoke_v2("binding-01234567")
        self.assertEqual(calls, ["revoke", "disconnect"])

    def test_malformed_bound_delivery_consumes_auth_and_blocks_valid_retry(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        closed = []
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
            endpoint.accept(
                b"truncated", [101],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closed.append, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(closed, [101])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        with self.assertRaises(Exception):
            self.connection.registry.match_and_consume(
                AuthorizationIdentity(
                    owner=self.connection.owner, machine_id=MACHINE,
                    broker_epoch=BROKER_EPOCH, controller_epoch=CONTROLLER_EPOCH,
                    operation_id=authorization["operation_id"],
                    request_digest=authorization["request_digest"],
                    binding_id=authorization["binding_id"], binding_version=1,
                    decision_id=authorization["decision_id"],
                    authorization_digest=authorization["authorization_digest"],
                    expires_at_unix_ms=NOW + 5_000,
                    binding_expires_at_unix_ms=NOW + 20_000,
                    activation_expires_at_unix_ms=NOW + 15_000,
                    request_deadline_unix_ms=NOW + 10_000,
                ), now_ms=NOW,
            )
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
            endpoint.accept(
                self._lease_packet(authorization), [102],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closed.append, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(closed, [101, 102])

    def test_wrong_delivery_peer_cannot_mint_accepted_socket_receipt(self):
        claimed, _authorization, _ack = self._claim_and_authorize()
        wrong_controller = ProcessIdentity(
            uid=CONTROLLER.uid, gid=CONTROLLER.gid, pid=CONTROLLER.pid + 1,
            start_ticks=CONTROLLER.start_ticks,
            executable_digest=CONTROLLER.executable_digest,
            unit_digest=CONTROLLER.unit_digest,
            config_digest=CONTROLLER.config_digest,
        )
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
            self._receipt(claimed["operation_id"], peer=wrong_controller)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]),
                         "authorized")

    def test_revoke_invalidates_operation_and_pinned_authorization_registry(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        snapshot = self.connection.operations.authorization_snapshot(claimed["operation_id"])
        self.assertEqual(self.connection.revoke_v2("binding-01234567"), 1)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        with self.assertRaises(Exception):
            self.connection.registry.match_and_consume(snapshot["identity"], now_ms=NOW)

    def test_activation_expiry_closes_each_boundary_and_lifecycle_wire_is_deferred(self):
        self.connection._activation_expires_at = NOW
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.guest_submit(
                guest_request(), connection_identity="guest-expired")
        self.assertFalse(self.connection.admission_open)
        self.setUp()
        before = len(self.connection.connection.sent)
        for kind in ("ACTIVATE_V2", "QUIESCE_V2"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ControllerServiceV2Error, "message_type_unknown"):
                    self.connection.handle_authority_v2({
                        "protocol": PROTOCOL, "type": kind, "machine_id": MACHINE,
                        "broker_epoch": BROKER_EPOCH,
                        "controller_epoch": CONTROLLER_EPOCH,
                    }, now_ms=NOW)
        self.assertEqual(len(self.connection.connection.sent), before)

    def test_activation_expiry_is_checked_at_claim_authorize_and_lease_boundaries(self):
        # Claim boundary.
        self.guest_submit(guest_request(), connection_identity="guest-claim")
        operation_id = next(iter(self.connection.operations._items))
        self.connection._activation_expires_at = NOW
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.connection.handle_authority_v2({
                "protocol": PROTOCOL, "type": "CLAIM_NEXT_V2", "machine_id": MACHINE,
                "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
                "sequence": 2, "wait_deadline_unix_ms": NOW + 100,
            }, now_ms=NOW)
        self.assertEqual(self.connection.operations.state(operation_id), "refused")

        # Authorization boundary.
        self.setUp()
        claimed, authorization = self._claimed_authorization_message()
        self.connection._activation_expires_at = NOW
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            self.connection.handle_authority_v2(authorization, now_ms=NOW)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")

        # Lease boundary also invalidates the pinned authorization.
        self.setUp()
        claimed, authorization, _ack = self._claim_and_authorize()
        snapshot = self.connection.operations.authorization_snapshot(claimed["operation_id"])
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        self.connection._activation_expires_at = NOW
        closed = []
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            endpoint.accept(
                self._lease_packet(authorization), [121],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closed.append, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(closed, [121])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")
        with self.assertRaises(Exception):
            self.connection.registry.match_and_consume(snapshot["identity"], now_ms=NOW)

    def test_wrong_guest_machine_and_validator_exception_allocate_no_operation(self):
        generated = []
        registry = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: generated.append(1) or "operation-999999",
        )
        wrong = dict(guest_request())
        wrong["machine_id"] = "sb-ffffffffffff"
        for request, validator in (
            (wrong, lambda _request: True),
            (guest_request(), lambda _request: (_ for _ in ()).throw(
                RuntimeError("hostile validator text"))),
        ):
            with self.assertRaisesRegex(ControllerServiceV2Error, "request_invalid") as raised:
                registry.submit(request, connection_identity="guest-invalid", now_ms=NOW,
                                canonical_guest_validator=validator)
            self.assertNotIn("hostile", str(raised.exception))
        self.assertEqual(generated, [])

    def test_multi_descriptor_cleanup_visits_all_once_and_keeps_first_failure(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        calls = []

        def closer(fd):
            calls.append(fd)
            if fd == 111:
                raise OSError("hostile close text")

        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "descriptor_cleanup_failed") as raised:
            endpoint.accept(
                self._lease_packet(authorization), [111, 112, 113],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closer, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(calls, [111, 112, 113])
        self.assertNotIn("hostile", str(raised.exception))
        surfaces = (
            ("submit", lambda: self.guest_submit(
                guest_request(), connection_identity="post-cleanup")),
            ("claim", lambda: self.connection.handle_authority_v2({
                "type": "CLAIM_NEXT_V2"}, now_ms=NOW)),
            ("authorize", lambda: self.connection.handle_authority_v2({
                "type": "AUTHORIZE_V2"}, now_ms=NOW)),
            ("refuse", lambda: self.connection.handle_authority_v2({
                "type": "REFUSE_V2"}, now_ms=NOW)),
            ("endpoint", lambda: self.connection.lease_endpoint_v2(
                claimed["operation_id"])),
            ("deliver", lambda: endpoint.accept(
                b"bad", [114],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closer, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]))),
            ("revoke", lambda: self.connection.revoke_v2("binding-01234567")),
            ("admission", lambda: self.connection.set_admission_v2(
                admission_open=True, activation_expires_at_unix_ms=NOW + 1000,
                now_ms=NOW)),
        )
        for name, invoke in surfaces:
            with self.subTest(surface=name):
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "descriptor_cleanup_failed"):
                    invoke()
        self.assertEqual(calls, [111, 112, 113])

    def test_descriptor_cleanup_failure_disconnects_and_sweeps_every_operation_once(self):
        class TrackingRegistry(AuthorizationRegistry):
            disconnects = 0

            def disconnect(self, **scope):
                type(self).disconnects += 1
                return super().disconnect(**scope)

        ids = iter(("operation-200001", "operation-200002", "operation-200003"))
        self.connection.operations = broker._V2OperationRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
            id_factory=lambda: next(ids),
        )
        self.connection.registry = TrackingRegistry(
            machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
            controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
        )
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        for index in (2, 3):
            self.guest_submit(
                guest_request(), connection_identity=f"sweep-{index}")
        calls = []

        def closer(fd):
            calls.append(fd)
            if fd == 201:
                raise OSError("hostile first close")

        with self.connection.operations._lock:
            for fd, operation_id in zip(
                    (202, 203), ("operation-200002", "operation-200003")):
                item = self.connection.operations._items[operation_id]
                item["state"] = "lease_bound"
                item["descriptor"] = fd
                item["descriptor_closer"] = closer
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "descriptor_cleanup_failed"):
            endpoint.accept(
                self._lease_packet(authorization), [201, 204],
                descriptor_observer=self._descriptor_observer,
                descriptor_closer=closer, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(calls, [201, 204, 202, 203])
        self.assertTrue(self.connection._registry_disconnected)
        self.assertEqual(TrackingRegistry.disconnects, 1)
        self.assertTrue(all(item["state"] == "refused"
                            for item in self.connection.operations._items.values()))
        self.connection.close()
        self.assertEqual(TrackingRegistry.disconnects, 1)
        self.assertEqual(calls, [201, 204, 202, 203])

    def test_all_registry_cleanup_modes_visit_every_retained_resource_once(self):
        for action in ("terminalize_all", "revoke", "expire"):
            with self.subTest(action=action):
                ids = iter(("operation-333333", "operation-444444"))
                registry = broker._V2OperationRegistry(
                    machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
                    controller_epoch=CONTROLLER_EPOCH, owner=self.connection.owner,
                    id_factory=lambda: next(ids),
                )
                for index in range(2):
                    registry.submit(
                        guest_request(), connection_identity=f"cleanup-guest-{index}",
                        now_ms=NOW, canonical_guest_validator=lambda _request: True,
                    )
                calls = []

                def closer(fd):
                    calls.append(fd)
                    if fd == 131:
                        raise OSError("hostile cleanup text")

                with registry._lock:
                    for descriptor, item in zip((131, 132), registry._items.values()):
                        item["state"] = "lease_bound"
                        item["descriptor"] = descriptor
                        item["descriptor_closer"] = closer
                        if action == "expire":
                            item["request_deadline_unix_ms"] = NOW
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "descriptor_cleanup_failed"):
                    if action == "terminalize_all":
                        registry.terminalize_all("revoked")
                    elif action == "revoke":
                        registry.revoke("binding-01234567")
                    else:
                        registry.expire(NOW)
                self.assertEqual(calls, [131, 132])
                with self.assertRaisesRegex(ControllerServiceV2Error,
                                            "descriptor_cleanup_failed"):
                    if action == "terminalize_all":
                        registry.terminalize_all("revoked")
                    elif action == "revoke":
                        registry.revoke("binding-01234567")
                    else:
                        registry.expire(NOW)
                self.assertEqual(calls, [131, 132])

    def test_atomic_one_use_consumption_has_exactly_one_duplicate_winner(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        lease = {
            "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
            "controller_epoch": CONTROLLER_EPOCH,
            **{key: authorization[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
                "auth_form", "policy_digest", "egress_digest", "broker_digest",
                "proof_digest", "effective_isolation_digest", "evidence_id",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )},
            "lease_id": "lease-0123456789", "lease_sequence": 1,
            "lease_expires_at_unix_ms": NOW + 4_000, "descriptor_size": 8,
        }
        packet = encode_lease_frame(lease, now_ms=NOW, deadline_caps={
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 5_000,
            "request_deadline_unix_ms": NOW + 10_000,
        })
        closed, results = [], []

        def attempt(descriptor):
            try:
                result = endpoint.accept(
                    packet, [descriptor],
                    descriptor_observer=lambda _fd: {
                        "anonymous_memfd": True, "close_on_exec": True, "size": 8,
                        "seals": {"write", "grow", "shrink", "seal"},
                    }, descriptor_closer=closed.append, now_ms=NOW,
                    accepted_socket_receipt=self._receipt(claimed["operation_id"]),
                )
                results.append(result["code"])
            except ControllerServiceV2Error as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=attempt, args=(fd,)) for fd in (31, 32)]
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(results.count("lease_bound"), 1)
        self.assertEqual(results.count("lease_invalid"), 1)
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "lease_bound")

    def test_descriptor_failure_consumes_authorization_and_closes_fd(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        lease = {
            "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
            "controller_epoch": CONTROLLER_EPOCH,
            **{key: authorization[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
                "auth_form", "policy_digest", "egress_digest", "broker_digest",
                "proof_digest", "effective_isolation_digest", "evidence_id",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "lease_id": "lease-0123456789", "lease_sequence": 1,
            "lease_expires_at_unix_ms": NOW + 4_000, "descriptor_size": 8,
        }
        packet = encode_lease_frame(lease, now_ms=NOW, deadline_caps={
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 5_000,
            "request_deadline_unix_ms": NOW + 10_000,
        })
        closed = []
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
            self.connection.lease_endpoint_v2(claimed["operation_id"]).accept(
                packet, [41],
                descriptor_observer=lambda _fd: {"anonymous_memfd": True,
                    "close_on_exec": True, "size": 7,
                    "seals": {"write", "grow", "shrink", "seal"}},
                descriptor_closer=closed.append, now_ms=NOW,
                accepted_socket_receipt=self._receipt(claimed["operation_id"]),
            )
        self.assertEqual(closed, [41])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")

    def test_revoke_closes_retained_lease_descriptor(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        lease = {
            "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
            "controller_epoch": CONTROLLER_EPOCH,
            **{key: authorization[key] for key in (
                "operation_id", "request_digest", "binding_id", "binding_version",
                "auth_form", "policy_digest", "egress_digest", "broker_digest",
                "proof_digest", "effective_isolation_digest", "evidence_id",
                "decision_id", "authorization_digest", "authorization_expires_at_unix_ms",
            )}, "lease_id": "lease-0123456789", "lease_sequence": 1,
            "lease_expires_at_unix_ms": NOW + 4_000, "descriptor_size": 8,
        }
        packet = encode_lease_frame(lease, now_ms=NOW, deadline_caps={
            "authorization_expires_at_unix_ms": NOW + 5_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 5_000,
            "request_deadline_unix_ms": NOW + 10_000,
        })
        closed = []
        self.connection.lease_endpoint_v2(claimed["operation_id"]).accept(
            packet, [51],
            descriptor_observer=lambda _fd: {"anonymous_memfd": True,
                "close_on_exec": True, "size": 8,
                "seals": {"write", "grow", "shrink", "seal"}},
            descriptor_closer=closed.append, now_ms=NOW,
            accepted_socket_receipt=self._receipt(claimed["operation_id"]),
        )
        self.assertEqual(self.connection.operations.revoke("binding-01234567"), 1)
        self.assertEqual(closed, [51])
        self.assertEqual(self.connection.operations.state(claimed["operation_id"]), "refused")

    def test_descriptor_close_failure_still_closes_authenticated_lease_socket_once(self):
        claimed, authorization, _ack = self._claim_and_authorize()
        endpoint = self.connection.lease_endpoint_v2(claimed["operation_id"])
        raw = FakeConnection()
        endpoint.accept(
            self._lease_packet(authorization), [52],
            descriptor_observer=self._descriptor_observer,
            descriptor_closer=lambda _fd: (_ for _ in ()).throw(OSError("injected")),
            now_ms=NOW,
            accepted_socket_receipt=self._receipt(claimed["operation_id"], raw),
        )
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "descriptor_cleanup_failed"):
            self.connection.operations.terminalize_all("revoked")
        self.assertEqual(raw.closed, 1)

    def test_expiry_quiesce_and_disconnect_close_exact_retained_descriptors(self):
        for action, expected_code in (("expiry", "broker_controller_closed"),
                                      ("quiesce", "broker_controller_closed"),
                                      ("disconnect", "broker_controller_closed")):
            with self.subTest(action=action):
                self.setUp()
                claimed, authorization, _ack = self._claim_and_authorize()
                closed = []
                self.connection.lease_endpoint_v2(claimed["operation_id"]).accept(
                    self._lease_packet(authorization), [61],
                    descriptor_observer=self._descriptor_observer,
                    descriptor_closer=closed.append, now_ms=NOW,
                    accepted_socket_receipt=self._receipt(claimed["operation_id"]),
                )
                if action == "expiry":
                    self.assertEqual(self.connection.operations.expire(NOW + 5_001), 1)
                elif action == "quiesce":
                    self.connection.operations.terminalize_all("revoked")
                else:
                    self.assertEqual(self.connection.close()["code"], expected_code)
                self.assertEqual(closed, [61])
                self.assertEqual(
                    self.connection.operations.state(claimed["operation_id"]), "refused"
                )


if __name__ == "__main__":
    unittest.main()
