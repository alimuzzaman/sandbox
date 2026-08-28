import unittest
import socket
import dataclasses

from sandbox.isolation.credential_guest_protocol_v2 import (
    EffectExecutionResultV2, EffectExecutionV2, GuestResultV2,
)
from sandbox.isolation.credential_controller_authority_v2 import ControllerAuthorityV2Error
from sandbox.isolation.credential_controller_integration_v2 import (
    ConnectedOfflineCredentialV2,
    MemoryAuditRepositoryV2,
    OfflineV2Error,
)
from sandbox.isolation.credential_controller_lifecycle_v2 import (
    derived_config_document, process_unit_digest_v2,
)
from sandbox.isolation.credential_controller_protocol_v2 import encode_controller_frame
from sandbox.isolation.credential_controller_service_v2 import ControllerServiceV2Error
from sandbox.isolation.models import EgressGrantSet
from tests import test_credential_controller_authority_v2 as fixtures


class OneShotExecutor(EffectExecutionV2):
    def __init__(self):
        super().__init__()
        self.calls = []

    def execute_authorized(self, context, descriptor):
        self.calls.append((context.request.binding_id, descriptor))
        return EffectExecutionResultV2(
            GuestResultV2.success(
                200, (), b"", context.request.correlation_id),
            "effect_entered", "completed", "completed", "upstream_completed")


def valid_config():
    service_gid = fixtures.CONFIG.broker.gid
    selected_egress = EgressGrantSet(
        fixtures.MACHINE, fixtures.CONFIG.policy_digest).digest
    common = dict(
        machine_id=fixtures.MACHINE, service_gid=service_gid,
        policy_digest=fixtures.CONFIG.policy_digest,
        egress_digest=selected_egress,
        broker_digest=fixtures.CONFIG.broker_digest,
        proof_digest=fixtures.CONFIG.proof_digest,
        effective_isolation_digest=fixtures.CONFIG.effective_isolation_digest,
        evidence_id=fixtures.CONFIG.evidence_id,
        egress_projection={
            "machine_id": fixtures.MACHINE,
            "base_policy_digest": fixtures.CONFIG.policy_digest,
            "egress_digest": selected_egress, "version": 1,
            "grant_authority": "staged-v1", "grants": []},
        guest_transport_projection={
            "machine_id": fixtures.MACHINE,
            "base_policy_digest": fixtures.CONFIG.policy_digest,
            "interface": "veth-sb0", "subnet": "10.73.0.0/30",
            "broker_address": "10.73.0.1", "guest_address": "10.73.0.2"},
        controller_endpoint_identity="v2-controller.sock",
        lease_endpoint_identity="v2-lease.sock",
        guest_endpoint_identity="credential-broker-guest-v2")
    controller_plan = derived_config_document(
        component="controller", service_uid=992,
        unit_identity=(f"sandbox-credential-controller-v2@{fixtures.MACHINE}.service"),
        executable_digest=fixtures.CONFIG.controller.executable_digest,
        config_identity="sandbox-v2-controller-config",
        peer_executable_digest=fixtures.CONFIG.broker.executable_digest, **common)
    broker_plan = derived_config_document(
        component="broker", service_uid=993,
        unit_identity=(f"sandbox-credential-broker-v2@{fixtures.MACHINE}.service"),
        executable_digest=fixtures.CONFIG.broker.executable_digest,
        config_identity="sandbox-v2-broker-config",
        peer_executable_digest=fixtures.CONFIG.controller.executable_digest, **common)
    controller = dataclasses.replace(
        fixtures.CONFIG.controller, uid=992, gid=service_gid,
        unit_digest=process_unit_digest_v2(
            machine_id=fixtures.MACHINE, component="controller", service_uid=992,
            service_gid=service_gid,
            unit_identity=f"sandbox-credential-controller-v2@{fixtures.MACHINE}.service"),
        config_digest=controller_plan["own_config_digest"])
    broker_identity = dataclasses.replace(
        fixtures.CONFIG.broker, uid=993, gid=service_gid,
        unit_digest=process_unit_digest_v2(
            machine_id=fixtures.MACHINE, component="broker", service_uid=993,
            service_gid=service_gid,
            unit_identity=f"sandbox-credential-broker-v2@{fixtures.MACHINE}.service"),
        config_digest=broker_plan["own_config_digest"])
    return dataclasses.replace(
        fixtures.CONFIG, controller=controller, broker=broker_identity,
        egress_digest=selected_egress,
    )


def graph(*, repository=None, executor=None):
    events = []
    config = valid_config()
    runner = ConnectedOfflineCredentialV2(
        config=config,
        broker_connection_type=fixtures.broker.BrokerControllerV2Connection,
        controller_epoch=fixtures.CONTROLLER_EPOCH,
        broker_epoch=fixtures.BROKER_EPOCH,
        interfaces=fixtures.interfaces(events, config=config),
        repository=repository or MemoryAuditRepositoryV2(),
        executor=executor or OneShotExecutor(),
        now_ms=fixtures.NOW,
    )
    return runner, events


class TestConnectedCredentialControllerIntegrationV2(unittest.TestCase):
    @staticmethod
    def activated(**kwargs):
        runner, events = graph(**kwargs)
        runner.authenticate()
        runner.activate()
        return runner, events

    def test_one_authenticated_session_drives_the_entire_v2_graph(self):
        executor = OneShotExecutor()
        runner, authority_events = graph(executor=executor)
        runner.authenticate()
        self.assertIs(runner.operation_authority.session, runner.controller_session)
        self.assertEqual(runner.broker_session.controller_epoch,
                         runner.controller_session.controller_epoch)
        self.assertEqual(runner.broker_session.broker_epoch,
                         runner.controller_session.broker_epoch)

        runner.activate()
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-connected")
        result = runner.dispatch_and_execute(operation_id, ack)
        guest = runner.broker_session.guest_result_v2(
            "guest-connected", consume=True)
        receipt = runner.quiesce()
        cleanup = runner.close()

        self.assertEqual(result["code"], "upstream_completed")
        self.assertEqual(guest, {
            "ok": True, "state": "completed", "code": "upstream_completed",
            "correlation_id": "corr-1",
        })
        self.assertEqual(executor.calls, [("binding-01234567", 1071)])
        self.assertEqual(authority_events, [
            "binding", "source", "scope", "proof", "egress", "activation", "expiry",
            "resolve",
        ])
        self.assertEqual([record["phase"] for record in runner.repository.committed],
                         ["pre", "post"])
        self.assertEqual(runner.controller_fd_closed, [71])
        self.assertEqual(runner.broker_fd_closed, [1071])
        self.assertEqual(cleanup["controller_socket_closes"], 1)
        self.assertEqual(cleanup["broker_socket_closes"], 1)
        self.assertEqual([verb for verb, _component in runner.lifecycle_executor.calls], [
            "credential-controller-configure-v2", "credential-broker-configure-v2",
            "credential-controller-start-v2", "credential-broker-start-v2",
            "credential-broker-stop-v2", "credential-controller-stop-v2",
        ])
        self.assertEqual(runner.events, [
            "authenticated", "activated", "guest_submitted", "claimed", "authorized",
            "lease_bound", "pre_committed", "effect", "post_committed", "lease_acked",
            "quiesced", "broker_stopped", "controller_stopped", "cleanup_complete",
        ])

    def test_handshake_rejects_wrong_socket_peer_packet_identity_and_rights(self):
        cases = ("socket", "packet", "rights")
        for case in cases:
            with self.subTest(case=case):
                runner, _events = graph()
                if case == "socket":
                    runner.controller_transport.peer_identity = fixtures.CONTROLLER
                elif case == "packet":
                    runner.controller_transport.packet_peer_identity = fixtures.CONTROLLER
                else:
                    runner.controller_transport.packet_rights = (91,)
                with self.assertRaises(ControllerServiceV2Error):
                    runner.authenticate()
                if case == "rights":
                    self.assertEqual(runner.controller_fd_closed, [91])
                self.assertLessEqual(runner.controller_transport.close_count, 1)

    def test_epoch_reconnect_and_v1_or_unknown_frames_close_the_same_session(self):
        runner, _events = graph()
        runner.authenticate()
        with self.assertRaisesRegex(ControllerServiceV2Error, "handshake_replayed"):
            runner.broker_session.handshake(
                observer=runner._observer(fixtures.CONTROLLER), now_ms=fixtures.NOW,
                monotonic=lambda: 0.0, so_peercred=1, scm_credentials=2,
                scm_rights=socket.SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertFalse(runner.broker_session.authenticated)

        for replacement in (b"credential-broker-controller-v1", b"credential-broker-controller-x2"):
            with self.subTest(protocol=replacement):
                runner, _events = self.activated()
                runner.operation_authority.poll_claim(
                    now_ms=fixtures.NOW, wait_deadline_unix_ms=fixtures.NOW + 1000)
                packet = runner.broker_transport._packets.popleft()
                packet = packet.replace(b"credential-broker-controller-v2", replacement)
                runner.broker_transport._packets.appendleft(packet)
                with self.assertRaises(ControllerServiceV2Error):
                    runner._broker_receive()
                self.assertFalse(runner.broker_session.authenticated)

    def test_claim_and_authorization_replay_are_rejected_on_connected_sequences(self):
        runner, _events = self.activated()
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-replay")
        self.assertTrue(operation_id.startswith("operation-"))
        with self.assertRaisesRegex(ControllerAuthorityV2Error, "request_invalid"):
            runner.operation_authority.decide(runner.last_claim, now_ms=fixtures.NOW)

        replay = encode_controller_frame(
            runner.last_authorization, direction="controller_to_broker",
            now_ms=fixtures.NOW,
            temporal_context={"activation_expires_at_unix_ms": fixtures.NOW + 20_000,
                              "request_deadline_unix_ms": fixtures.NOW + 10_000},
        )
        runner.controller_transport.sendall(replay)
        with self.assertRaises(ControllerServiceV2Error):
            runner._broker_receive(temporal_context={
                "activation_expires_at_unix_ms": fixtures.NOW + 20_000,
                "request_deadline_unix_ms": fixtures.NOW + 10_000,
            })
        self.assertFalse(runner.broker_session.authenticated)
        self.assertEqual(ack["type"], "AUTHORIZED_V2")

    def test_cross_binding_authorization_is_terminal_before_lease(self):
        runner, _events = self.activated()
        request = fixtures.guest_request()
        runner.guest_submit(request, connection_identity="guest-cross")
        runner.operation_authority.poll_claim(
            now_ms=fixtures.NOW, wait_deadline_unix_ms=fixtures.NOW + 1000)
        runner.broker_session.handle_authority_v2(runner._broker_receive(), now_ms=fixtures.NOW)
        claim = runner._controller_receive(
            temporal_context={"original_guest_request_receipt_unix_ms": fixtures.NOW})
        runner.operation_authority.decide(claim, now_ms=fixtures.NOW)
        authorization = runner._broker_receive(temporal_context={
            "activation_expires_at_unix_ms": fixtures.NOW + 20_000,
            "request_deadline_unix_ms": fixtures.NOW + 10_000,
        })
        authorization["binding_id"] = "binding-deadbeef"
        with self.assertRaisesRegex(ControllerServiceV2Error, "authorization_mismatch"):
            runner.broker_session.handle_authority_v2(authorization, now_ms=fixtures.NOW)
        self.assertEqual(runner.broker_session.guest_result_v2("guest-cross")["code"],
                         "authorization_mismatch")

    def test_lease_receipt_and_endpoint_are_one_attempt_only(self):
        runner, _events = self.activated()
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-lease-replay")
        runner.dispatch_and_execute(operation_id, ack)
        closed = []
        with self.assertRaisesRegex(ControllerServiceV2Error, "lease_invalid"):
            runner.last_lease_endpoint.accept(
                runner.last_lease_packet, [2000],
                descriptor_observer=lambda _fd: {}, descriptor_closer=closed.append,
                now_ms=fixtures.NOW + 2,
                accepted_socket_receipt=runner.last_broker_lease_receipt,
            )
        self.assertEqual(closed, [])

    def test_quiesce_race_revokes_pre_effect_and_never_resolves(self):
        executor = OneShotExecutor()
        runner, authority_events = self.activated(executor=executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-quiesce")
        receipt = runner.quiesce()
        self.assertEqual(receipt.reason_code, "drained")
        with self.assertRaises((ControllerAuthorityV2Error, ControllerServiceV2Error)):
            runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual(executor.calls, [])
        self.assertEqual(authority_events.count("resolve"), 0)
        self.assertEqual(runner.broker_session.guest_result_v2("guest-quiesce")["code"],
                         "revoked")

    def test_capacity_is_exactly_sixteen_connected_guest_identities(self):
        runner, _events = graph()
        runner.authenticate()
        runner.activate()
        for index in range(16):
            request = dict(fixtures.guest_request())
            request["correlation_id"] = f"corr-{index + 1}"
            runner.guest_submit(request, connection_identity=f"guest-{index + 1}")
        with self.assertRaisesRegex(ControllerServiceV2Error, "capacity_exceeded"):
            runner.guest_submit(
                fixtures.guest_request(), connection_identity="guest-17")
        receipt = runner.quiesce()
        self.assertEqual((receipt.drain_status, receipt.active_operation_count),
                         ("drained", 0))

    def test_audit_pre_failure_and_post_uncertainty_never_replay_effect(self):
        pre_executor = OneShotExecutor()
        pre_repo = MemoryAuditRepositoryV2(append_hook=lambda _record: False)
        runner, _events = self.activated(repository=pre_repo, executor=pre_executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-pre-fail")
        with self.assertRaises(Exception):
            runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual(pre_executor.calls, [])
        self.assertEqual(pre_repo.committed, [])

        calls = []
        def only_pre(record):
            calls.append(record["phase"])
            return record["phase"] == "pre"
        post_executor = OneShotExecutor()
        post_repo = MemoryAuditRepositoryV2(append_hook=only_pre)
        runner, _events = self.activated(repository=post_repo, executor=post_executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-post-fail")
        with self.assertRaises(Exception):
            runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual(len(post_executor.calls), 1)
        self.assertEqual(calls, ["pre", "post"])
        self.assertEqual([item["phase"] for item in post_repo.committed], ["pre"])
        self.assertIn(runner.broker_session.guest_result_v2("guest-post-fail")["state"],
                      {"indeterminate", "refused"})

    def test_dropped_pre_ack_retries_same_durable_commit_and_effect_once(self):
        executor = OneShotExecutor()
        runner, _events = self.activated(executor=executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-audit-retry")
        runner.broker_transport.drop_next_sends = 1
        result = runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual(result["code"], "upstream_completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual([item["phase"] for item in runner.repository.committed],
                         ["pre", "post"])

    def test_egress_denial_is_pre_post_ack_then_guest_result_without_effect(self):
        executor = OneShotExecutor()
        runner, _events = self.activated(executor=executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-egress-denied")
        dispatch = runner.dispatch_and_execute(
            operation_id, ack, pre_effect_refusal="egress_denied")
        self.assertEqual(executor.calls, [])
        self.assertEqual([item["phase"] for item in runner.repository.committed],
                         ["pre", "post"])
        post = runner.repository.committed[-1]
        self.assertEqual((post["outcome_class"], post["effect_certainty"],
                          post["reason_code"]),
                         ("refused", "none", "egress_denied"))
        self.assertEqual(dispatch["code"], "egress_denied")
        self.assertEqual(runner.effect_result["effect_phase"], "pre_effect")
        self.assertEqual(runner.broker_session.guest_result_v2(
            "guest-egress-denied")["code"], "egress_denied")

    def test_new_authenticated_epoch_recovers_crash_after_durable_pre(self):
        def pre_only(record):
            return record["phase"] == "pre"
        repository = MemoryAuditRepositoryV2(append_hook=pre_only)
        runner, _events = self.activated(repository=repository, executor=OneShotExecutor())
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-crash")
        with self.assertRaises(Exception):
            runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual([item["phase"] for item in repository.committed], ["pre"])

        repository.append_hook = None
        config = valid_config()
        recovered = ConnectedOfflineCredentialV2(
            config=config,
            broker_connection_type=fixtures.broker.BrokerControllerV2Connection,
            controller_epoch="03" * 16, broker_epoch="04" * 16,
            interfaces=fixtures.interfaces([], config=config), repository=repository,
            executor=OneShotExecutor(), now_ms=fixtures.NOW + 100,
        )
        recovered.authenticate()
        self.assertEqual([(item["phase"], item.get("recovery"))
                          for item in repository.committed],
                         [("pre", None), ("post", True)])
        recovered.activate()

    def test_expiry_and_descriptor_cleanup_failure_are_sticky_and_bounded(self):
        runner, _events = self.activated()
        with self.assertRaisesRegex(ControllerServiceV2Error, "admission_closed"):
            runner.now_ms = fixtures.NOW + 20_000
            runner.guest_submit(
                fixtures.guest_request(), connection_identity="guest-expired")

        executor = OneShotExecutor()
        runner, _events = self.activated(executor=executor)
        operation_id, ack = runner.authorize(
            fixtures.guest_request(), connection_identity="guest-close-fail")
        closes = []
        def fail_close(fd):
            closes.append(fd)
            raise OSError("injected descriptor close")
        runner.broker_descriptor_closer = fail_close
        with self.assertRaises(Exception):
            runner.dispatch_and_execute(operation_id, ack)
        self.assertEqual(closes, [1071])
        self.assertEqual(runner.broker_session.operations.terminal_code,
                         "descriptor_cleanup_failed")
        self.assertEqual(runner.broker_session.guest_result_v2(
            "guest-close-fail")["state"], "indeterminate")

    def test_disconnect_and_cleanup_are_idempotent_and_close_once(self):
        runner, _events = self.activated()
        runner.quiesce()
        first = runner.close()
        second = runner.close()
        self.assertEqual(first["controller_socket_closes"], 1)
        self.assertEqual(first["broker_socket_closes"], 1)
        self.assertEqual(second["controller_socket_closes"], 1)
        self.assertEqual(second["broker_socket_closes"], 1)

    def test_helper_stop_and_cleanup_observation_failures_stay_sticky_after_socket_cleanup(self):
        for case in ("stop", "observation"):
            with self.subTest(case=case):
                runner, _events = self.activated()
                runner.quiesce()
                if case == "stop":
                    runner.lifecycle_executor.fail_verb = "credential-broker-stop-v2"
                    expected = "lifecycle_action_failed"
                else:
                    runner.lifecycle_executor.absence_overrides["broker"] = {
                        "observed": True, "owned": True, "unit_absent": True,
                        "process_absent": True, "socket_absent": False,
                        "cgroup_absent": True, "descriptor_absent": True,
                    }
                    expected = "cleanup_incomplete"
                with self.assertRaisesRegex(OfflineV2Error, expected):
                    runner.close()
                self.assertEqual(runner.controller_transport.close_count, 1)
                self.assertEqual(runner.broker_transport.close_count, 1)
                calls = list(runner.lifecycle_executor.calls)
                with self.assertRaisesRegex(OfflineV2Error, expected):
                    runner.close()
                self.assertEqual(runner.lifecycle_executor.calls, calls)


if __name__ == "__main__":
    unittest.main()
