import unittest

from tests.hosting_image_fixtures import stage_request, staging_policy


class TestImageStagingPolicy(unittest.TestCase):
    def test_exact_plan_policy_and_target_admit(self):
        from sandbox.hosting.images.staging_policy import admit_stage_request
        policy = staging_policy(); result = admit_stage_request(stage_request(policy=policy), policy)
        self.assertTrue(result.ok)
        self.assertEqual(policy.broker_recipient,
            "ghcr-repository-read:acme/widget@sha256:" + "1" * 64)

    def test_policy_plan_target_and_generation_are_immutable_request_authority(self):
        from sandbox.hosting.images.staging_policy import admit_stage_request
        from sandbox.hosting.images.staging_models import StageRequest
        policy = staging_policy(); request = stage_request(policy=policy)
        changed = StageRequest.create(request_id=request.request_id,
            expected_generation=request.expected_generation, plan=request.plan,
            staging_policy_digest="sha256:" + "e" * 64,
            target=request.target, confirmed=True)
        self.assertEqual(admit_stage_request(changed, policy).code, "policy_mismatch")

    def test_fixed_recipient_registry_path_digest_and_operation_cannot_be_substituted(self):
        from sandbox.hosting.images.staging_models import (
            StagingContractError, StagingPolicy, staging_digest,
        )
        policy = staging_policy()
        substitutions = (
            {"broker_recipient": "docker-repository-read:acme/widget@sha256:" + "1" * 64},
            {"broker_recipient": "ghcr-repository-read:ghcr.io/acme/widget@sha256:" + "1" * 64},
            {"broker_recipient": "ghcr-repository-read:acme/other@sha256:" + "1" * 64},
            {"broker_recipient": "ghcr-repository-read:acme/widget:latest"},
            {"broker_recipient": "ghcr-repository-read:acme/widget@sha256:" + "2" * 64},
            {"operation": "registry.repository.read"},
        )
        for change in substitutions:
            with self.subTest(change=change):
                raw = policy.as_mapping(); raw.update(change)
                identity = dict(raw); identity.pop("policy_digest")
                raw["policy_digest"] = staging_digest(
                    "sandbox.hosting.images.staging-policy.v1", identity)
                with self.assertRaises(StagingContractError):
                    StagingPolicy.from_mapping(raw)

    def test_request_requires_confirmation_and_closed_verified_plan(self):
        from sandbox.hosting.images.staging_models import StageRequest, StagingContractError
        policy = staging_policy()
        with self.assertRaises(StagingContractError) as raised:
            StageRequest.create(request_id="request", expected_generation=0, plan={},
                                staging_policy_digest=policy.policy_digest,
                                target=policy.target, confirmed=False)
        self.assertEqual(raised.exception.code, "plan_invalid")


if __name__ == "__main__": unittest.main()
