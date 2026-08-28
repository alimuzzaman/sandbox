import json
import threading
import time
import unittest
from unittest import mock

from sandbox.isolation.credential_guest_protocol_v2 import (
    AuthorizedEgressDecisionV2, EffectExecutionResultV2, EffectExecutionV2,
    GuestResultV2,
)
from sandbox.isolation.credential_controller_protocol_v2 import (
    PROTOCOL,
    decode_lease_ack,
    decode_controller_frame,
    digest_document,
    encode_controller_frame,
)
from sandbox.isolation.credential_controller_service_v2 import ControllerServiceV2Error
from tests import test_credential_controller_authority_v2 as fixtures


CONFIG = fixtures.CONFIG
MACHINE = fixtures.MACHINE
BROKER_EPOCH = fixtures.BROKER_EPOCH
CONTROLLER_EPOCH = fixtures.CONTROLLER_EPOCH
NOW = fixtures.NOW
broker = fixtures.broker


class Executor(EffectExecutionV2):
    def __init__(self, result=None, *, raises=False):
        self.calls = []
        super().__init__()
        self.result = result
        self.raises = raises

    def execute_authorized(self, context, descriptor):
        self.calls.append((context.request.binding_id, descriptor))
        if self.raises:
            raise RuntimeError("host detail")
        return self.result or EffectExecutionResultV2(
            GuestResultV2.success(
                200, (), b"", context.request.correlation_id),
            "effect_entered", "completed", "completed", "upstream_completed")


class LeaseConnection:
    def __init__(self, *, fail=False):
        self.sent = []
        self.fail = fail
        self.closed = 0
    def sendall(self, packet):
        if self.fail:
            raise OSError("injected")
        self.sent.append(packet)
    def getsockopt(self, *_args):
        import struct
        return struct.pack("3i", CONFIG.controller.pid,
                           CONFIG.controller.uid, CONFIG.controller.gid)
    def close(self):
        self.closed += 1


def fixture():
    owner = fixtures.TestBrokerOperationAuthorityV2(
        methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced",
    )
    owner.setUp()
    claimed, authorization, _ack = owner._claim_and_authorize()
    closed = []
    lease = LeaseConnection()
    endpoint = owner.connection.lease_endpoint_v2(claimed["operation_id"])
    endpoint.accept(
        owner._lease_packet(authorization), [71],
        descriptor_observer=owner._descriptor_observer,
        descriptor_closer=closed.append, now_ms=NOW,
        accepted_socket_receipt=owner.connection._authenticated_lease_socket_receipt_v2(
            claimed["operation_id"], lease,
            observer=lambda *_peer: CONFIG.controller, so_peercred=1),
    )
    return owner.connection, claimed["operation_id"], closed, lease


class AuthenticatedAuditReceiver:
    def __init__(self, *, fail_first=False, fail_post=False):
        self.calls = []
        self.fail_first = fail_first
        self.fail_post = fail_post
        self.controller_sequence = 2

    def __call__(self, connection, **_kwargs):
        message = json.loads(connection.sent[-1].rstrip(b" \x00"))
        self.calls.append(dict(message))
        if self.fail_first:
            self.fail_first = False
            raise TimeoutError("injected")
        phase = "pre" if message["type"] == "AUDIT_PRE_V2" else "post"
        if self.fail_post and phase == "post":
            raise TimeoutError("injected")
        value = {
            "protocol": PROTOCOL, "type": "AUDIT_ACK_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": self.controller_sequence, "reply_to": message["sequence"],
            "audit_root_id": message["audit_root_id"], "phase": phase,
            "phase_id": message["phase_id"],
            "audit_fingerprint": message["audit_fingerprint"],
            "commit_id": ("commit-pre012345678" if phase == "pre"
                          else "commit-post01234567"),
            "disposition": "committed",
        }
        self.controller_sequence += 1
        return (encode_controller_frame(
            value, direction="controller_to_broker", now_ms=NOW + 1,
        ), CONFIG.controller)


def audit_ids(kind):
    return {"root": "audit-root0123456789", "pre": "audit-pre0123456789",
            "post": "audit-post012345678"}[kind]


class TestCredentialBrokerAuditRuntimeV2(unittest.TestCase):
    @staticmethod
    def run_effect(connection, operation_id, executor, receiver, **changes):
        values = dict(
            egress_decision=AuthorizedEgressDecisionV2(
                "api.example.test", "api.example.test", 443,
                ("8.8.8.8",), CONFIG.egress_digest),
            audit_id_factory=audit_ids, executor=executor, monotonic=lambda: 0.0,
            wall_clock=lambda: NOW + 1,
        )
        values.update(changes)
        stopped = threading.Event()
        seen = len(connection.connection.sent)

        def route():
            nonlocal seen
            while not stopped.is_set():
                if len(connection.connection.sent) > seen:
                    seen += 1
                    try:
                        packet, _peer = receiver(connection.connection)
                    except TimeoutError:
                        continue
                    value = decode_controller_frame(
                        packet, direction="controller_to_broker", now_ms=NOW + 1)
                    try:
                        connection.route_audit_ack_v2(value)
                    except ControllerServiceV2Error:
                        return
                else:
                    time.sleep(0.001)

        owner = threading.Thread(target=route)
        owner.start()
        try:
            return connection.execute_effect_v2(operation_id, **values)
        finally:
            stopped.set()
            owner.join(2)

    def test_pre_effect_post_ack_order_exact_same_socket_and_cleanup(self):
        connection, operation_id, closed, lease = fixture()
        executor = Executor()
        receiver = AuthenticatedAuditReceiver()
        result = self.run_effect(connection, operation_id, executor, receiver)
        self.assertEqual([item["type"] for item in receiver.calls],
                         ["AUDIT_PRE_V2", "AUDIT_POST_V2"])
        self.assertEqual(executor.calls, [("binding-01234567", 71)])
        self.assertEqual(closed, [71])
        self.assertEqual(len(lease.sent), 1)
        self.assertEqual(lease.closed, 1)
        self.assertEqual(len(lease.sent[0]), 444)
        ack = decode_lease_ack(lease.sent[0])
        self.assertEqual((ack["audit_root_id"], ack["post_phase_id"],
                          ack["post_commit_id"], ack["outcome_class"]),
                         (result["audit_root_id"], result["post_phase_id"],
                          result["post_commit_id"], "completed"))

    def test_one_transport_retry_preserves_semantics_and_effect_runs_once(self):
        connection, operation_id, closed, _lease = fixture()
        executor = Executor()
        receiver = AuthenticatedAuditReceiver(fail_first=True)
        self.run_effect(connection, operation_id, executor, receiver)
        first, retry = receiver.calls[:2]
        self.assertNotEqual(first["sequence"], retry["sequence"])
        for name in ("audit_root_id", "phase_id", "audit_fingerprint", "event_code"):
            self.assertEqual(first[name], retry[name])
        self.assertEqual(len(executor.calls), 1)

    def test_missing_post_ack_is_indeterminate_never_replays_effect_or_sends_lease_ack(self):
        connection, operation_id, closed, lease = fixture()
        executor = Executor()
        receiver = AuthenticatedAuditReceiver(fail_post=True)
        with self.assertRaisesRegex(ControllerServiceV2Error, "effect_indeterminate"):
            self.run_effect(connection, operation_id, executor, receiver)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(closed, [71])
        self.assertEqual(lease.sent, [])
        self.assertEqual(lease.closed, 1)
        self.assertEqual(connection.operations.state(operation_id), "indeterminate")
        self.assertFalse(connection.admission_open)

    def test_executor_exception_is_audited_indeterminate_possible(self):
        connection, operation_id, closed, lease = fixture()
        executor = Executor(raises=True)
        receiver = AuthenticatedAuditReceiver()
        result = self.run_effect(connection, operation_id, executor, receiver)
        self.assertEqual((result["outcome_class"], result["effect_certainty"], result["code"]),
                         ("indeterminate", "possible", "internal_indeterminate"))
        post = receiver.calls[-1]
        self.assertEqual((post["outcome_class"], post["effect_certainty"], post["reason_code"]),
                         ("indeterminate", "possible", "internal_indeterminate"))
        self.assertEqual(len(lease.sent), 1)

    def test_same_socket_ack_failure_never_leaves_terminal_success(self):
        connection, operation_id, closed, lease = fixture()
        lease.fail = True
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_ack_failed"):
            self.run_effect(connection, operation_id, Executor(),
                            AuthenticatedAuditReceiver())
        self.assertEqual(closed, [71])
        self.assertEqual(connection.operations.state(operation_id), "indeterminate")
        self.assertFalse(connection.admission_open)

    def test_late_audit_ack_exhausts_one_second_budget_without_running_effect(self):
        connection, operation_id, _closed, lease = fixture()
        executor = Executor()
        ticks = iter((0.0, 0.0, 0.0, 1.1))
        with self.assertRaisesRegex(ControllerServiceV2Error, "audit_unavailable"):
            self.run_effect(
                connection, operation_id, executor, AuthenticatedAuditReceiver(),
                monotonic=lambda: next(ticks),
            )
        self.assertEqual(executor.calls, [])
        self.assertEqual(lease.sent, [])
        self.assertEqual(connection.operations.state(operation_id), "indeterminate")

    def test_quiesce_closes_admission_while_effect_is_running_and_reports_real_count(self):
        connection, operation_id, _closed, lease = fixture()
        entered, release = threading.Event(), threading.Event()

        class BlockingExecutor(EffectExecutionV2):
            def execute_authorized(self, context, descriptor):
                del descriptor
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test timeout")
                return EffectExecutionResultV2(
                    GuestResultV2.success(
                        200, (), b"", context.request.correlation_id),
                    "effect_entered", "completed", "completed",
                    "upstream_completed")

        receiver = AuthenticatedAuditReceiver()
        result, errors = [], []

        def run():
            try:
                result.append(self.run_effect(
                    connection, operation_id, BlockingExecutor(), receiver))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(connection.operations.expire(NOW + 100_000), 0)
        self.assertEqual(connection.operations.state(operation_id), "effect_possible")
        quiesce = {
            "protocol": PROTOCOL, "type": "QUIESCE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 4, "reason_code": "operator_stop",
            "drain_deadline_unix_ms": NOW + 5_000,
        }
        quiesce["quiesce_digest"] = digest_document("quiesce_digest", {
            **{key: quiesce[key] for key in ("protocol", "type", "machine_id",
                "broker_epoch", "controller_epoch", "reason_code",
                "drain_deadline_unix_ms")}, "request_sequence": 4,
        })
        ack = connection.handle_lifecycle_v2(quiesce, now_ms=NOW)
        self.assertFalse(connection.admission_open)
        self.assertEqual((ack["drain_status"], ack["active_operation_count"],
                          ack["reason_code"]), ("timeout", 1, "drain_timeout"))
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0]["outcome_class"], "completed")
        self.assertEqual(len(lease.sent), 1)

    def test_exact_activate_then_irreversible_quiesce_lifecycle(self):
        owner = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced",
        )
        owner.setUp()
        connection = owner.connection
        connection.admission_open = False
        connection._activation_expires_at = None
        activation = {
            "protocol": PROTOCOL, "type": "ACTIVATE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 2, **CONFIG.configured_digests(),
            "activation_expires_at_unix_ms": NOW + 10_000,
        }
        activation["activation_digest"] = digest_document("activation_digest", {
            "protocol": PROTOCOL, "type": "ACTIVATE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "request_sequence": 2, **CONFIG.configured_digests(),
            "activation_expires_at_unix_ms": NOW + 10_000,
        })
        ack = connection.handle_lifecycle_v2(activation, now_ms=NOW)
        self.assertEqual((ack["type"], ack["admission_state"], ack["activation_digest"]),
                         ("ACTIVATE_ACK_V2", "open", activation["activation_digest"]))
        quiesce = {
            "protocol": PROTOCOL, "type": "QUIESCE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 3, "reason_code": "operator_stop",
            "drain_deadline_unix_ms": NOW + 5_000,
        }
        quiesce["quiesce_digest"] = digest_document("quiesce_digest", {
            **{key: quiesce[key] for key in ("protocol", "type", "machine_id",
                "broker_epoch", "controller_epoch", "reason_code",
                "drain_deadline_unix_ms")}, "request_sequence": 3,
        })
        closed = connection.handle_lifecycle_v2(quiesce, now_ms=NOW)
        self.assertEqual((closed["type"], closed["admission_state"], closed["drain_status"]),
                         ("QUIESCE_ACK_V2", "closed", "drained"))
        refused = connection.handle_lifecycle_v2(activation, now_ms=NOW + 1)
        self.assertEqual((refused["admission_state"], refused["activate_decision"],
                          refused["reason_code"]),
                         ("closed", "refused", "admission_closed"))

    def test_identifiable_lifecycle_mismatches_receive_one_exact_terminal_ack(self):
        owner = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced")
        owner.setUp()
        connection = owner.connection
        connection.admission_open = False
        connection._activation_expires_at = None
        activation = {
            "protocol": PROTOCOL, "type": "ACTIVATE_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "sequence": 2, **CONFIG.configured_digests(),
            "activation_expires_at_unix_ms": NOW + 10_000,
            "activation_digest": "f" * 64,
        }
        ack = connection.handle_lifecycle_v2(activation, now_ms=NOW)
        self.assertEqual((ack["type"], ack["admission_state"], ack["activate_decision"],
                          ack["active_operation_count"], ack["reason_code"], ack["reply_to"]),
                         ("ACTIVATE_ACK_V2", "closed", "refused", 0,
                          "digest_mismatch", 2))

        owner.setUp()
        connection = owner.connection
        quiesce = {
            "protocol": PROTOCOL, "type": "QUIESCE_V2",
            "machine_id": "sb-ffffffffffff", "broker_epoch": BROKER_EPOCH,
            "controller_epoch": CONTROLLER_EPOCH, "sequence": 2,
            "reason_code": "operator_stop", "drain_deadline_unix_ms": NOW + 5_000,
        }
        quiesce["quiesce_digest"] = digest_document("quiesce_digest", {
            **{key: quiesce[key] for key in ("protocol", "type", "machine_id",
                "broker_epoch", "controller_epoch", "reason_code",
                "drain_deadline_unix_ms")}, "request_sequence": 2,
        })
        ack = connection.handle_lifecycle_v2(quiesce, now_ms=NOW)
        self.assertEqual((ack["type"], ack["admission_state"], ack["drain_status"],
                          ack["active_operation_count"], ack["reason_code"], ack["reply_to"]),
                         ("QUIESCE_ACK_V2", "closed", "refused", 0,
                          "identity_mismatch", 2))


if __name__ == "__main__":
    unittest.main()
