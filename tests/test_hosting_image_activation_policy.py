import unittest

from tests.fixtures.hosting_image_activation import activation_policy, activation_request, authority_binding


class ActivationPolicyTests(unittest.TestCase):
    def test_capability_and_target_cannot_be_widened(self):
        from sandbox.hosting.images.activation.policy import admit_activation
        from sandbox.hosting.images.activation.models import ActivationRequest
        policy = activation_policy(); request = activation_request(); binding = authority_binding(policy=policy)
        self.assertFalse(admit_activation(request, policy, binding, capability="rollback").ok)
        changed = ActivationRequest.create(
            request_id=request.request_id, operation=request.operation,
            expected_generation=request.expected_generation,
            policy_digest=request.policy_digest, plan=request.plan, proof=request.proof,
            authority_binding_digest="sha256:" + "f" * 64,
            rollback_grant_digest=request.rollback_grant_digest,
            confirmed=request.confirmed)
        self.assertEqual(admit_activation(changed, policy, binding, capability="activate").code,
                         "authority_mismatch")

    def test_adoption_is_zero_init_only(self):
        from sandbox.hosting.images.activation.policy import admit_activation
        request = activation_request(operation="adopt")
        policy = activation_policy(); binding = authority_binding(policy=policy)
        self.assertEqual(admit_activation(request, policy, binding, capability="adopt").code,
                         "adoption_requires_zero_init")


if __name__ == "__main__": unittest.main()
