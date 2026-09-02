import unittest

from tests.fixtures.hosting_image_activation import (
    activation_policy, activation_request, authority_binding, staged_proof,
)


class ActivationModelTests(unittest.TestCase):
    def test_forward_request_digest_binds_rollback_subject_and_grant(self):
        from sandbox.hosting.images.activation.models import ActivationRequest
        request = activation_request()
        changed = ActivationRequest.create(
            request_id=request.request_id, operation=request.operation,
            expected_generation=request.expected_generation,
            policy_digest=request.policy_digest, plan=request.plan, proof=request.proof,
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest="sha256:" + "9" * 64,
            rollback_grant_digest=request.rollback_grant_digest,
            confirmed=True)
        self.assertNotEqual(request.request_digest, changed.request_digest)

    def test_generation_bound_edge_receipt_is_closed_and_digest_validated(self):
        from sandbox.hosting.images.activation.models import (
            ActivationTransaction, activation_digest,
        )
        request = activation_request()
        edge_body = {"request_id": "activation-a/edge",
            "request_digest": "sha256:" + "1" * 64, "terminal": True,
            "route_digest": "sha256:" + "2" * 64,
            "observation_digest": "sha256:" + "3" * 64,
            "target_identity": request.proof.target.target_identity,
            "generation": 1, "deployment_identity": "sha256:" + "4" * 64}
        edge = {**edge_body, "receipt_digest": activation_digest(
            "sandbox.hosting.images.activation-edge-receipt.v1", edge_body)}
        transaction = {"schema_version": 1, "transaction_digest": "sha256:" + "5" * 64,
            "request_id": request.request_id, "request_digest": request.request_digest,
            "operation": request.operation, "holder": "activation-owner/activation-a",
            "starting_generation": 0, "phase": "edge_pending", "effect_entered": True,
            "authority_binding_digest": request.authority_binding_digest,
            "proof_pin": {"lease_id": "activation-lease/" + "a" * 48,
                "holder": "activation-owner/activation-a", "phase": "accepted",
                "proof_digest": request.proof.proof_digest,
                "host_acceptance_receipt": "host-acceptance/" + "b" * 64},
            "rollback_subject_digest": request.rollback_subject_digest,
            "rollback_grant_digest": request.rollback_grant_digest,
            "init_receipts": (), "init_steps": (), "edge_required": True,
            "recovery_context": {"target": request.proof.target.as_mapping(),
                "compose_project": "widget", "selected_services": ["web", "worker"]},
            "running_observation": None, "edge_result": edge,
            "candidate_generation": None, "result": None}
        self.assertEqual(ActivationTransaction(**transaction).edge_result, edge)
        with self.assertRaisesRegex(ValueError, "edge_uncertain"):
            ActivationTransaction(**{**transaction, "edge_result": {
                **edge, "receipt_digest": "sha256:" + "f" * 64}})

    def test_exact_plan_proof_projection_and_machine_binding_are_required(self):
        from sandbox.hosting.images.activation.policy import admit_activation
        request = activation_request(); policy = activation_policy(); binding = authority_binding(policy=policy)
        admitted = admit_activation(request, policy, binding, capability="activate")
        self.assertTrue(admitted.ok)
        changed = binding.as_mapping(); changed["proof_digest"] = "sha256:" + "e" * 64
        changed["binding_digest"] = "sha256:" + "f" * 64
        with self.assertRaises(ValueError):
            type(binding).from_mapping(changed)

    def test_unknown_and_credential_fields_are_not_part_of_closed_request(self):
        raw = activation_request().as_mapping(); raw["credential"] = "synthetic-canary"
        from sandbox.hosting.images.activation.models import ActivationRequest
        with self.assertRaises(TypeError):
            ActivationRequest(**raw)

    def test_persisted_target_mappings_are_closed(self):
        from sandbox.hosting.images.activation.models import (
            ForwardRollbackSubject, activation_digest,
        )
        from tests.fixtures.hosting_image_activation import rollback_subject
        subject = rollback_subject(); body = subject.body_mapping()
        body["target"] = {**body["target"], "extra_authority": "forbidden"}
        with self.assertRaises(ValueError):
            ForwardRollbackSubject(**body, subject_digest=activation_digest(
                "sandbox.hosting.images.forward-rollback-subject.v1", body))

    def test_separately_valid_substituted_proof_refuses_exact_equality(self):
        from sandbox.hosting.images.activation.models import validate_activation_artifacts
        proof = staged_proof().as_mapping(); proof["plan_digest"] = "sha256:" + "9" * 64
        with self.assertRaises(ValueError): validate_activation_artifacts(activation_request().plan, proof)


if __name__ == "__main__": unittest.main()
