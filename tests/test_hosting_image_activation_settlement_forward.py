import copy
import unittest

from sandbox.hosting.images.activation.models import activation_digest
from sandbox.hosting.images.activation.repository import ActivationRepository
from sandbox.hosting.images.activation.settlement_forward import ForwardSettlementApproval
from sandbox.hosting.images.activation.settlement_models import SettlementDataAssessment
from sandbox.hosting.images.activation.v2_models import ActivationRequestV2
from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
from tests.test_hosting_image_activation_settlement_models import ssh_signature
from tests.test_hosting_image_activation_settlement_repository import _state, _plan, settle_candidate
from tests.test_hosting_image_activation_v2 import (
    artifacts, grant_for, request_for, FakeEdgeV2, FakeGrantVerifier, FakeHostStatePort,
    FakeRuntimeV2, FakeStageRepositoryPort, FakeTargetMutationPort,
)


def successor_fixture():
    _, state, head = settle_candidate(_state(), _plan(), "sha256:" + "9" * 64)
    plan, proof, snapshot = artifacts(graph=True)
    grant = grant_for(plan, proof, snapshot=snapshot)
    request = request_for(plan, proof, snapshot, grant, request_id="approved-successor")
    assessment = SettlementDataAssessment.create(target=proof.target,
        transaction_digest=head["transaction_digest"], application_revision=plan.receipt.source_sha,
        backup_receipt_digests=("sha256:" + "b" * 64,))
    approval = ForwardSettlementApproval.create(authority_id="operator", authority_revision="1",
        operation="activate", target=proof.target, request_id=request.request_id,
        expected_generation=0, predecessor_digest=head["terminal_receipt"]["terminal_digest"],
        transaction_digest=head["transaction_digest"], application_revision=plan.receipt.source_sha,
        plan_set_digest=plan.plan_set_digest, proof_set_digest=proof.proof_digest,
        compose_snapshot_digest=snapshot.snapshot_digest, policy_digest=plan.policy.policy_digest,
        rollback_grant_digest=grant.grant_digest, data_assessment=assessment,
        issued_at=50, expires_at=200, signature=ssh_signature())
    return state, request, grant, approval


def signed_request(request, approval):
    return ActivationRequestV2.create(request_id=request.request_id, operation=request.operation,
        expected_generation=request.expected_generation, policy_digest=request.policy_digest,
        plan_set=request.plan_set, proof_set=request.proof_set,
        compose_snapshot=request.compose_snapshot, rollback_grant_digest=request.rollback_grant_digest,
        confirmed=True, settlement_forward=approval.as_mapping())


class ForwardVerifier:
    def __init__(self, allowed): self.allowed = allowed
    def verify_forward(self, approval, *, now):
        return self.allowed and approval.issued_at <= now < approval.expires_at


class SettlementForwardTests(unittest.TestCase):
    def test_recomputed_inner_transaction_cannot_cross_link_another_settlement(self):
        from sandbox.hosting.images.activation.repository import decode_activation_state, ActivationRepositoryError
        from sandbox.hosting.images.activation.v2_repository import transaction_v2, validate_transaction_v2
        state, request, grant, approval = successor_fixture()
        fields = {name: getattr(approval, name) for name in approval.__dataclass_fields__
                  if name not in {"schema_version", "approval_digest"}}
        fields["predecessor_digest"] = "sha256:" + "f" * 64
        changed = ForwardSettlementApproval.create(**fields)
        request = signed_request(request, changed)
        pin = {"lease_id": "activation-lease/" + "7" * 48, "holder": "activation-owner/" + request.request_id,
            "phase": "accepted", "proof_digest": request.proof_set["proof_digest"],
            "host_acceptance_receipt": "host-acceptance/" + "8" * 64}
        active = transaction_v2(request, holder=pin["holder"], proof_pin=pin,
            recovery_context={"target": request.proof_set["target"], "compose_project": "lenzora",
                "selected_services": list(request.compose_snapshot.selected_services),
                "compose_snapshot": request.compose_snapshot.as_mapping(), "compatibility_grant": grant.as_mapping(),
                "settlement_forward": changed.as_mapping()}, prior_generation_digest=grant.prior_generation_digest)
        self.assertEqual(validate_transaction_v2(active), active)
        state["active"] = active; state["reserved_terminal_bytes"] = 16384
        with self.assertRaises(ActivationRepositoryError): decode_activation_state(state)

    def test_second_incident_binds_original_forward_to_the_previous_settlement(self):
        from sandbox.hosting.images.activation.settlement_models import SettlementObservation, SettlementPlan
        from sandbox.hosting.images.activation.settlement_repository import settle_candidate as commit_settlement, validate_settlements
        state, request, grant, approval = successor_fixture()
        request = signed_request(request, approval)
        result, host, _stage, _runtime = self._execute(state, request, grant, allowed=True, fail_graph=True)
        self.assertEqual(result["result_class"], "uncertain", result)
        active = host.state["active"]
        observation = SettlementObservation.create(target=approval.target,
            transaction_digest=active["transaction_digest"], generation=0, runtime_epoch=approval.target.daemon_identity,
            container_identities=(), preserved_identities=(), inventory_digest="sha256:" + "b" * 64)
        assessment = SettlementDataAssessment.create(target=approval.target,
            transaction_digest=active["transaction_digest"], application_revision=approval.application_revision,
            backup_receipt_digests=("sha256:" + "c" * 64,))
        plan = SettlementPlan.create(request_id="settlement-second", active_request_id=request.request_id,
            active_request_digest=request.request_digest, transaction_digest=active["transaction_digest"],
            target=approval.target, generation=0, current_generation_digest=None,
            observation=observation, data_assessment=assessment)
        from sandbox.hosting.images.activation.settlement_models import SettlementApproval
        operator = SettlementApproval.create(authority_id="operator", authority_revision="1",
            plan_digest=plan.plan_digest, issued_at=50, expires_at=200, signature=ssh_signature())
        status, settled, record = commit_settlement(host.state, plan, operator, authorized_at=100)
        self.assertEqual(status, "settled")
        self.assertEqual(record["sequence"], 2)
        self.assertEqual(validate_settlements(settled), settled)
        self.assertEqual(record["original_transaction"]["recovery_context"]["settlement_forward"], approval.as_mapping())

    def test_missing_or_empty_settlement_history_cannot_keep_a_forward_subject(self):
        from sandbox.hosting.images.activation.repository import decode_activation_state, ActivationRepositoryError
        for failed in (True, False):
            state, request, grant, approval = successor_fixture()
            result, host, _stage, _runtime = self._execute(
                state, signed_request(request, approval), grant, allowed=True, fail_graph=failed)
            self.assertEqual(result["result_class"], "uncertain" if failed else "success", result)
            for empty in (True, False):
                corrupt = copy.deepcopy(host.state)
                if empty: corrupt["settlements"] = {}
                else: corrupt.pop("settlements")
                with self.assertRaises(ActivationRepositoryError): decode_activation_state(corrupt)

    def test_additive_request_preserves_legacy_bytes_and_round_trips_new_authority(self):
        _state_value, request, _grant, approval = successor_fixture()
        raw = request.as_mapping()
        self.assertNotIn("settlement_forward", raw)
        self.assertEqual(ActivationRequestV2.from_mapping(raw).as_mapping(), raw)
        signed = signed_request(request, approval)
        self.assertNotEqual(signed.request_digest, request.request_digest)
        self.assertEqual(ActivationRequestV2.from_mapping(signed.as_mapping()), signed)
        altered = copy.deepcopy(signed.as_mapping())
        altered["settlement_forward"]["request_id"] = "substitution"
        with self.assertRaises(ValueError): ActivationRequestV2.from_mapping(altered)

    def test_recomputed_boolean_schema_and_wrong_operation_are_rejected(self):
        _state_value, _request, _grant, approval = successor_fixture()
        for key, value in (("schema_version", True), ("operation", "rollback")):
            body = approval.body_mapping(); body[key] = value
            raw = {**body, "approval_digest": activation_digest(
                "sandbox.hosting.images.settlement-forward-approval.v1", body)}
            with self.assertRaises(ValueError): ForwardSettlementApproval.from_mapping(raw)

    def test_missing_or_uninstalled_forward_approval_refuses_before_custody_and_runtime(self):
        for supplied in (False, True):
            state, request, grant, approval = successor_fixture()
            if supplied: request = signed_request(request, approval)
            result, host, stage, runtime = self._execute(state, request, grant, allowed=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "authority_mismatch")
            self.assertIsNone(stage.custody.lease)
            self.assertFalse(runtime.graph_effects)
            self.assertEqual(host.state, state)

    def test_approved_successor_commits_and_retains_separate_approval_in_graph_evidence(self):
        state, request, grant, approval = successor_fixture()
        request = signed_request(request, approval)
        result, host, _stage, _runtime = self._execute(state, request, grant, allowed=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(host.state["generation"], 1)
        self.assertEqual(host.state["current"]["execution_evidence"]["settlement_forward"], approval.as_mapping())
        self.assertEqual(host.state["settlements"], state["settlements"])
        self.assertEqual(host.state["results"]["activation-a"], state["results"]["activation-a"])

    def _execute(self, state, request, grant, *, allowed, fail_graph=False):
        host = FakeHostStatePort(); host.state = copy.deepcopy(state)
        stage = FakeStageRepositoryPort(); runtime = FakeRuntimeV2()
        if fail_graph:
            runtime.execute_graph_step_v2 = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic lost graph ack"))
        repository = ActivationRepository(host_state_port=host, stage_repository=stage,
            target_mutation_port=FakeTargetMutationPort())
        service = ActivationServiceV2(repository=repository, runtime_adapter=runtime,
            edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
            settlement_approval_verifier=ForwardVerifier(allowed), clock=lambda: 100)
        result = service.execute(request, rollback_grant=grant, compose_files=("compose.yml",),
            compose_project="lenzora", edge_route_digest="sha256:" + "a" * 64,
            admission_deadline="2999-01-01T00:00:00Z",
            stage_ledger_authority="feature-050-stage-ledger-v2", stage_ledger_revision=1)
        return result, host, stage, runtime
