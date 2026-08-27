import hashlib
import unittest
from types import MappingProxyType

from sandbox.isolation.credential_controller_lifecycle_v2 import (
    ControllerLifecycleAuthorityV2,
    DerivedServiceConfigV2,
    FixedLifecycleExecutorV2,
    LIFECYCLE_VERBS_V2,
    LifecycleV2Error,
    ManagedCredentialLifecycleV2,
    OwnershipObservationV2,
    canonical_config_bytes,
    derived_config_document,
    verify_owned_config,
)
from sandbox.runtimes.managed.services import compile_credential_service_plans_v2
from sandbox.isolation.models import EgressGrant, EgressGrantSet
from tests import test_credential_controller_audit_v2 as audit_fixtures


DIGESTS = [hashlib.sha256(f"lifecycle-{index}".encode()).hexdigest()
           for index in range(8)]


def egress_projection(policy_digest, *, with_grant=False):
    grants = ()
    if with_grant:
        grants = (EgressGrant(
            "grant-lifecycle", "sb-0123456789ab", "public_cidr_tcp",
            ("93.184.216.0/24",), (443,), "2999-01-01T00:00:00Z"),)
    selected = EgressGrantSet("sb-0123456789ab", policy_digest, grants)
    value = selected.to_dict()
    value["egress_digest"] = value.pop("grant_digest")
    return value


def document(component="controller", **changes):
    controller = component == "controller"
    value = derived_config_document(
        machine_id="sb-0123456789ab", component=component,
        unit_identity=f"sandbox-credential-{component}-v2@sb-0123456789ab.service",
        service_uid=992 if component == "controller" else 993,
        service_gid=2001, executable_digest=DIGESTS[0 if controller else 1],
        config_identity=f"sandbox-v2-{component}-config",
        policy_digest=audit_fixtures.CONFIG.policy_digest,
        egress_digest=audit_fixtures.CONFIG.egress_digest,
        broker_digest=audit_fixtures.CONFIG.broker_digest,
        proof_digest=audit_fixtures.CONFIG.proof_digest,
        effective_isolation_digest=audit_fixtures.CONFIG.effective_isolation_digest,
        evidence_id=audit_fixtures.CONFIG.evidence_id,
        egress_projection=egress_projection(audit_fixtures.CONFIG.policy_digest),
        peer_executable_digest=DIGESTS[1 if controller else 0],
        peer_config_digest=DIGESTS[7 if controller else 6],
        own_config_digest=DIGESTS[6 if controller else 7],
        controller_endpoint_identity="v2-controller.sock",
        lease_endpoint_identity="v2-lease.sock",
        guest_endpoint_identity="v2-guest.sock",
    )
    value.update(changes)
    return value


def quiesce_receipt(session=None, plan_identity=DIGESTS[0]):
    session = session or audit_fixtures.session()
    audit = audit_fixtures.authority(audit_fixtures.Repository(), session)
    lifecycle = ControllerLifecycleAuthorityV2(session, plan_identity=plan_identity)
    readiness = {
        "binding_ready": True, "proof_ready": True, "egress_ready": True,
        "sealed_expectations_ready": True, "active_operation_count": 0,
        "drain_status": "drained", **session.config.configured_digests(),
    }
    activation = lifecycle.activate(
        now_ms=audit_fixtures.NOW,
        expires_at_unix_ms=audit_fixtures.NOW + 10_000,
        audit_authority=audit, readiness_observer=lambda: readiness,
    )
    activation_ack = {
        "protocol": audit_fixtures.PROTOCOL, "type": "ACTIVATE_ACK_V2",
        "machine_id": audit_fixtures.MACHINE,
        "broker_epoch": audit_fixtures.BROKER_EPOCH,
        "controller_epoch": audit_fixtures.CONTROLLER_EPOCH,
        "sequence": 2, "reply_to": activation["sequence"],
        "activation_digest": activation["activation_digest"],
        "admission_state": "open", "activate_decision": "activated",
        "active_operation_count": 0, "acknowledged_at_unix_ms": audit_fixtures.NOW,
        "activation_expires_at_unix_ms": activation["activation_expires_at_unix_ms"],
        "reason_code": "activated",
    }
    audit_fixtures.accept(audit, activation_ack)
    lifecycle.acknowledge_activation(activation_ack, now_ms=audit_fixtures.NOW)
    quiesce = lifecycle.quiesce(
        now_ms=audit_fixtures.NOW,
        drain_deadline_unix_ms=audit_fixtures.NOW + 5_000,
        reason_code="operator_stop",
    )
    ack = {
        "protocol": audit_fixtures.PROTOCOL, "type": "QUIESCE_ACK_V2",
        "machine_id": audit_fixtures.MACHINE,
        "broker_epoch": audit_fixtures.BROKER_EPOCH,
        "controller_epoch": audit_fixtures.CONTROLLER_EPOCH,
        "sequence": 3, "reply_to": quiesce["sequence"],
        "quiesce_digest": quiesce["quiesce_digest"],
        "admission_state": "closed", "drain_status": "drained",
        "active_operation_count": 0, "acknowledged_at_unix_ms": audit_fixtures.NOW,
        "drain_deadline_unix_ms": quiesce["drain_deadline_unix_ms"],
        "reason_code": "drained",
    }
    audit_fixtures.accept(audit, ack)
    return lifecycle.acknowledge_quiesce(ack, now_ms=audit_fixtures.NOW)


class Executor(FixedLifecycleExecutorV2):
    def __init__(self, *, fail=None, observations=None):
        self.calls = []
        self.fail = fail
        self.observations = observations or {}
        self.observed = []

    def execute(self, verb, plan):
        self.calls.append((verb, plan.document["component"]))
        return {"ok": verb != self.fail,
                "code": "completed" if verb != self.fail else "failed"}

    def observe_absence(self, plan):
        self.observed.append(plan.component)
        return self.observations.get(plan.document["component"], {
            "observed": True, "owned": True, "unit_absent": True,
            "process_absent": True, "socket_absent": True, "cgroup_absent": True,
            "descriptor_absent": True,
        })


class TestCredentialControllerLifecycleV2(unittest.TestCase):
    def plans(self, executor=None, session=None):
        selected = executor or Executor()
        selected_session = session or audit_fixtures.session()
        return ManagedCredentialLifecycleV2(
            DerivedServiceConfigV2.derive(document("controller")),
            DerivedServiceConfigV2.derive(document("broker")), selected,
            selected_session,
        ), selected

    def test_secret_free_config_is_canonical_immutable_and_digest_bound(self):
        source = document()
        plan = DerivedServiceConfigV2.derive(source)
        source["service_uid"] = 9999
        self.assertEqual(plan.document["service_uid"], 992)
        self.assertEqual(plan.canonical_bytes, canonical_config_bytes(plan.document))
        self.assertEqual(plan.config_digest,
                         hashlib.sha256(plan.canonical_bytes).hexdigest())
        forbidden = (b"operation_id", b"lease_id", b"audit_root_id",
                     b"request_digest", b"source_reference", b"auth_form")
        self.assertTrue(all(item not in plan.canonical_bytes for item in forbidden))
        with self.assertRaises(TypeError):
            plan.document["service_uid"] = 9
        with self.assertRaises(TypeError):
            plan.document["bounds"]["drain_timeout_ms"] = 1
        with self.assertRaisesRegex(LifecycleV2Error, "config_invalid"):
            DerivedServiceConfigV2(
                document=MappingProxyType(dict(plan.document)),
                canonical_bytes=plan.canonical_bytes,
                config_digest=plan.config_digest, machine_id=plan.machine_id,
                component=plan.component, service_gid=9999,
            )

    def test_managed_service_compiler_derives_both_closed_plan_identities(self):
        projection = egress_projection(DIGESTS[1])
        plans = compile_credential_service_plans_v2(
            machine_id="sb-0123456789ab", service_gid=2001,
            controller_executable_digest=DIGESTS[0],
            broker_executable_digest=DIGESTS[1],
            controller_config_identity="sandbox-v2-controller-config",
            broker_config_identity="sandbox-v2-broker-config",
            policy_digest=DIGESTS[1], egress_digest=projection["egress_digest"],
            broker_digest=DIGESTS[3], proof_digest=DIGESTS[4],
            effective_isolation_digest=DIGESTS[5], evidence_id=None,
            egress_projection=projection,
            controller_config_digest=DIGESTS[6], broker_config_digest=DIGESTS[7],
            controller_endpoint_identity="v2-controller.sock",
            lease_endpoint_identity="v2-lease.sock",
            guest_endpoint_identity="v2-guest.sock",
        )
        self.assertEqual(set(plans), {"controller", "broker"})
        self.assertEqual(plans["controller"].document["service_uid"], 992)
        self.assertEqual(plans["broker"].document["service_uid"], 993)
        self.assertIsNone(plans["broker"].document["evidence_id"])
        self.assertEqual(plans["controller"].document["egress_projection"],
                         plans["broker"].document["egress_projection"])
        with self.assertRaises(TypeError):
            plans["broker"].document["egress_projection"]["grants"] = ()

    def test_unknown_secret_or_runtime_fields_are_refused(self):
        for name, value in (
            ("source_reference", "opaque/source"), ("operation_id", "operation-012345"),
            ("pid", 42), ("binding_id", "binding-01234567"),
        ):
            with self.subTest(name=name):
                changed = document()
                changed[name] = value
                with self.assertRaisesRegex(LifecycleV2Error, "config_invalid"):
                    DerivedServiceConfigV2.derive(changed)
        for name, value in (("controller_endpoint_identity", "secret.sock"),
                            ("unit_identity", "token.service")):
            with self.subTest(name=name):
                changed = document()
                changed[name] = value
                with self.assertRaises(LifecycleV2Error):
                    DerivedServiceConfigV2.derive(changed)

    def test_cross_plan_peer_identity_mismatch_is_refused(self):
        controller = DerivedServiceConfigV2.derive(document("controller"))
        broker_doc = document("broker", peer_executable_digest=DIGESTS[2])
        broker = DerivedServiceConfigV2.derive(broker_doc)
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_plan_invalid"):
            ManagedCredentialLifecycleV2(
                controller, broker, Executor(), audit_fixtures.session())

    def test_cross_plan_egress_projection_mismatch_is_refused(self):
        controller = DerivedServiceConfigV2.derive(document("controller"))
        changed_projection = egress_projection(
            audit_fixtures.CONFIG.policy_digest, with_grant=True)
        broker_doc = document(
            "broker", egress_digest=changed_projection["egress_digest"],
            egress_projection=changed_projection,
        )
        broker = DerivedServiceConfigV2.derive(broker_doc)
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_plan_invalid"):
            ManagedCredentialLifecycleV2(
                controller, broker, Executor(), audit_fixtures.session())

    def test_owned_config_requires_root_group_mode_canonical_regular_no_follow(self):
        plan = DerivedServiceConfigV2.derive(document())
        baseline = dict(
            regular_file=True, symlink=False, owner_uid=0,
            group_gid=plan.document["service_gid"], mode=0o640,
            size=len(plan.canonical_bytes), canonical_bytes=plan.canonical_bytes,
            digest=plan.config_digest,
        )
        self.assertTrue(verify_owned_config(plan, OwnershipObservationV2(**baseline)))
        for name, value in (("regular_file", False), ("symlink", True),
                            ("owner_uid", 1), ("group_gid", 1), ("mode", 0o644),
                            ("size", 1), ("canonical_bytes", b"{}"),
                            ("digest", "0" * 64)):
            with self.subTest(name=name):
                changed = dict(baseline, **{name: value})
                self.assertFalse(verify_owned_config(
                    plan, OwnershipObservationV2(**changed),
                ))

    def test_exact_fixed_verbs_and_controller_first_start_broker_first_stop(self):
        owner, executor = self.plans()
        owner.start_closed()
        owner.retain_quiesce_ack(quiesce_receipt(owner.session, owner.plan_identity))
        owner.stop()
        self.assertEqual([item[0] for item in executor.calls], [
            "credential-controller-configure-v2", "credential-broker-configure-v2",
            "credential-controller-start-v2", "credential-broker-start-v2",
            "credential-broker-stop-v2", "credential-controller-stop-v2",
        ])
        self.assertEqual(len(LIFECYCLE_VERBS_V2), 8)

    def test_quiesce_receipt_is_opaque_and_one_use(self):
        owner, _ = self.plans()
        owner.start_closed()
        with self.assertRaisesRegex(LifecycleV2Error, "quiesce_incomplete"):
            owner.retain_quiesce_ack({"forged": True})

        receipt_session = audit_fixtures.session()
        first, _ = self.plans(session=receipt_session)
        receipt = quiesce_receipt(receipt_session, first.plan_identity)
        with self.assertRaisesRegex(LifecycleV2Error, "quiesce_incomplete"):
            receipt.reason_code = "forged"
        first.start_closed()
        first.retain_quiesce_ack(receipt)
        first.stop()
        second, _ = self.plans()
        second.start_closed()
        with self.assertRaisesRegex(LifecycleV2Error, "quiesce_incomplete"):
            second.retain_quiesce_ack(receipt)

    def test_stop_refuses_before_public_close_quiesce_ack_and_drain(self):
        for mutation in ({"admission_state": "open"}, {"drain_status": "timeout"},
                         {"active_operation_count": 1}):
            with self.subTest(mutation=mutation):
                owner, executor = self.plans()
                owner.start_closed()
                ack = {
                    "machine_id": "sb-0123456789ab", "quiesce_digest": DIGESTS[0],
                    "admission_state": "closed", "drain_status": "drained",
                    "active_operation_count": 0, "reason_code": "drained",
                }
                ack.update(mutation)
                with self.assertRaisesRegex(LifecycleV2Error, "quiesce_incomplete"):
                    owner.retain_quiesce_ack(ack)
                self.assertNotIn("credential-broker-stop-v2",
                                 [item[0] for item in executor.calls])

    def test_first_action_error_is_sticky_and_cleanup_uncertainty_never_passes(self):
        failing = Executor(fail="credential-controller-start-v2")
        owner, _ = self.plans(failing)
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_action_failed"):
            owner.start_closed()
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_action_failed"):
            owner.stop()

        for mutation in (
            {"observed": False}, {"owned": False}, {"socket_absent": False},
            {"foreign": True}, {},
        ):
            with self.subTest(mutation=mutation):
                expected = {"observed": True, "owned": True, "unit_absent": True,
                            "process_absent": True, "socket_absent": True,
                            "cgroup_absent": True, "descriptor_absent": True}
                if mutation:
                    expected.update(mutation)
                executor = Executor(observations={"broker": expected})
                lifecycle, _ = self.plans(executor)
                if mutation or not expected:
                    detail = lifecycle.verify_cleanup()
                    self.assertFalse(detail["complete"])
                    self.assertEqual(executor.observed, ["broker", "controller"])

    def test_partial_start_and_stop_attempt_reverse_cleanup_and_preserve_first_failure(self):
        start_executor = Executor(fail="credential-broker-start-v2")
        lifecycle, _ = self.plans(start_executor)
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_action_failed"):
            lifecycle.start_closed()
        self.assertEqual([item[0] for item in start_executor.calls][-2:], [
            "credential-broker-start-v2", "credential-controller-stop-v2",
        ])

        stop_executor = Executor(fail="credential-broker-stop-v2")
        lifecycle, _ = self.plans(stop_executor)
        lifecycle.start_closed()
        lifecycle.retain_quiesce_ack(quiesce_receipt(
            lifecycle.session, lifecycle.plan_identity))
        with self.assertRaisesRegex(LifecycleV2Error, "lifecycle_action_failed"):
            lifecycle.stop()
        self.assertEqual([item[0] for item in stop_executor.calls][-2:], [
            "credential-broker-stop-v2", "credential-controller-stop-v2",
        ])

    def test_controller_activation_uses_recovered_audit_and_exact_ack_states(self):
        session = audit_fixtures.session()
        audit = audit_fixtures.authority(audit_fixtures.Repository(), session)
        lifecycle = ControllerLifecycleAuthorityV2(session, plan_identity=DIGESTS[0])
        readiness = {
            "binding_ready": True, "proof_ready": True, "egress_ready": True,
            "sealed_expectations_ready": True, "active_operation_count": 0,
            "drain_status": "drained", **session.config.configured_digests(),
        }
        sent = lifecycle.activate(
            now_ms=audit_fixtures.NOW,
            expires_at_unix_ms=audit_fixtures.NOW + 10_000,
            audit_authority=audit, readiness_observer=lambda: readiness,
        )
        ack = {
            "protocol": audit_fixtures.PROTOCOL, "type": "ACTIVATE_ACK_V2",
            "machine_id": audit_fixtures.MACHINE,
            "broker_epoch": audit_fixtures.BROKER_EPOCH,
            "controller_epoch": audit_fixtures.CONTROLLER_EPOCH,
            "sequence": 2, "reply_to": sent["sequence"],
            "activation_digest": sent["activation_digest"],
            "admission_state": "open", "activate_decision": "activated",
            "active_operation_count": 0,
            "acknowledged_at_unix_ms": audit_fixtures.NOW,
            "activation_expires_at_unix_ms": sent["activation_expires_at_unix_ms"],
            "reason_code": "activated",
        }
        audit_fixtures.accept(audit, ack)
        self.assertTrue(lifecycle.acknowledge_activation(
            ack, now_ms=audit_fixtures.NOW)["admission_open"])
        with self.assertRaisesRegex(LifecycleV2Error, "activation_refused"):
            lifecycle.activate(
                now_ms=audit_fixtures.NOW,
                expires_at_unix_ms=audit_fixtures.NOW + 10_000,
                audit_authority=audit, readiness_observer=lambda: readiness,
            )

    def test_controller_refuses_unrecovered_audit_and_impossible_activation_ack(self):
        session = audit_fixtures.session()
        audit = audit_fixtures.authority(
            audit_fixtures.Repository(), session, recover=False)
        lifecycle = ControllerLifecycleAuthorityV2(session, plan_identity=DIGESTS[0])
        readiness = {
            "binding_ready": True, "proof_ready": True, "egress_ready": True,
            "sealed_expectations_ready": True, "active_operation_count": 0,
            "drain_status": "drained", **session.config.configured_digests(),
        }
        with self.assertRaisesRegex(LifecycleV2Error, "activation_refused"):
            lifecycle.activate(
                now_ms=audit_fixtures.NOW,
                expires_at_unix_ms=audit_fixtures.NOW + 10_000,
                audit_authority=audit, readiness_observer=lambda: readiness,
            )
        audit.recover_unclosed_pre(now_ms=audit_fixtures.NOW)
        sent = lifecycle.activate(
            now_ms=audit_fixtures.NOW,
            expires_at_unix_ms=audit_fixtures.NOW + 10_000,
            audit_authority=audit, readiness_observer=lambda: readiness,
        )
        impossible = {
            "protocol": audit_fixtures.PROTOCOL, "type": "ACTIVATE_ACK_V2",
            "machine_id": audit_fixtures.MACHINE,
            "broker_epoch": audit_fixtures.BROKER_EPOCH,
            "controller_epoch": audit_fixtures.CONTROLLER_EPOCH,
            "sequence": 2, "reply_to": sent["sequence"],
            "activation_digest": sent["activation_digest"],
            "admission_state": "open", "activate_decision": "activated",
            "active_operation_count": 1,
            "acknowledged_at_unix_ms": audit_fixtures.NOW,
            "activation_expires_at_unix_ms": sent["activation_expires_at_unix_ms"],
            "reason_code": "activated",
        }
        audit_fixtures.accept(audit, impossible)
        with self.assertRaisesRegex(LifecycleV2Error, "activation_ack_invalid"):
            lifecycle.acknowledge_activation(impossible, now_ms=audit_fixtures.NOW)


if __name__ == "__main__":
    unittest.main()
