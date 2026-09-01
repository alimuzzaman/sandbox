import unittest

from tests.fixtures.hosting_image_activation import (
    activation_policy, activation_request, authority_binding, staged_proof,
)


class ActivationModelTests(unittest.TestCase):
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

    def test_separately_valid_substituted_proof_refuses_exact_equality(self):
        from sandbox.hosting.images.activation.models import validate_activation_artifacts
        proof = staged_proof().as_mapping(); proof["plan_digest"] = "sha256:" + "9" * 64
        with self.assertRaises(ValueError): validate_activation_artifacts(activation_request().plan, proof)


if __name__ == "__main__": unittest.main()
