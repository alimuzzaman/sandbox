from __future__ import annotations

import unittest

from sandbox.config.hosting_images import HostingImageConfigError, normalize_project_image_intent, normalize_release_receipt
from sandbox.hosting.images import verify_image_plan
from sandbox.hosting.images.models import canonical_json
from tests.hosting_image_fixtures import (
    CONFIG_DIGEST,
    EXPECTED_PLAN_CANONICAL_BYTES,
    EXPECTED_PLAN_DIGEST,
    EXPECTED_POLICY_DIGEST,
    EXPECTED_RECEIPT_DIGEST,
    MANIFEST_DIGEST,
    channel_objects,
    policy_mapping,
    project_intent_mapping,
    receipt_mapping,
    reverse_objects,
    valid_channels,
    valid_channel_mappings,
)


class TestVerifiedImagePlan(unittest.TestCase):
    def test_input_digest_vectors_are_fixed_external_expectations(self):
        policy, _, receipt = valid_channel_mappings()
        self.assertEqual(receipt["payload_digest"], EXPECTED_RECEIPT_DIGEST)
        self.assertEqual(policy["policy_digest"], EXPECTED_POLICY_DIGEST)

    def test_matching_channels_produce_complete_canonical_plan(self):
        result = verify_image_plan(*valid_channels())

        self.assertTrue(result.ok)
        self.assertEqual(result.result_class, "verified")
        self.assertEqual(result.locations, ())
        plan = result.plan
        self.assertIsNotNone(plan)
        self.assertEqual(plan.image.manifest_digest, MANIFEST_DIGEST)
        self.assertEqual(plan.image.config_digest, CONFIG_DIGEST)
        self.assertEqual(plan.topology.persistent_services, ("web", "worker"))
        self.assertEqual(plan.topology.one_shot_services, ("migrate",))
        projection = plan.delivery_identity_projection
        self.assertEqual(projection.image, plan.image)
        self.assertEqual(projection.topology, plan.topology)
        self.assertEqual(
            projection.image.repository_qualified_digest,
            f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
        )
        self.assertEqual(projection.intended_visibility, "private")
        self.assertEqual(dict(projection.service_image_bindings), {
            "migrate": f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
            "web": f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
            "worker": f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
        })
        self.assertNotIn("visibility_observed", result.as_mapping()["plan"])
        self.assertNotIn("signature_verified", result.as_mapping()["plan"])
        self.assertEqual(plan.plan_digest, EXPECTED_PLAN_DIGEST)
        self.assertEqual(plan.canonical_bytes(), EXPECTED_PLAN_CANONICAL_BYTES)

    def test_object_key_order_does_not_change_plan_bytes_or_digest(self):
        policy, project, receipt = valid_channel_mappings()
        first = verify_image_plan(*channel_objects(policy, project, receipt))
        second = verify_image_plan(*channel_objects(
            reverse_objects(policy), reverse_objects(project), reverse_objects(receipt)))

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(first.plan.plan_digest, second.plan.plan_digest)
        self.assertEqual(first.plan.canonical_bytes(), second.plan.canonical_bytes())
        self.assertEqual(canonical_json(first.as_mapping()), canonical_json(second.as_mapping()))

        policy, project, receipt = valid_channel_mappings()
        project["declared_services"].reverse()
        project["persistent_services"].reverse()
        reordered = verify_image_plan(*channel_objects(policy, project, receipt))
        self.assertEqual(first.plan.plan_digest, reordered.plan.plan_digest)

    def test_authority_fields_are_bound_into_plan_identity(self):
        policy, project, receipt = valid_channel_mappings()
        baseline = verify_image_plan(*channel_objects(policy, project, receipt)).plan
        for change in (
            {"authority_id": "machine-policy/controller-b"},
            {"policy_revision": 8},
            {"target_scope": {
                "remote": "production-b", "project": "widget", "environment": "production",
            }},
        ):
            changed = verify_image_plan(*channel_objects(
                policy_mapping(receipt=receipt, **change), project, receipt))
            self.assertTrue(changed.ok)
            self.assertNotEqual(changed.plan.plan_digest, baseline.plan_digest)

    def test_policy_selector_cannot_be_substituted(self):
        policy, project, receipt = valid_channel_mappings()
        project["policy_selector"] = "another-policy"
        result = verify_image_plan(*channel_objects(policy, project, receipt))
        self.assertFalse(result.ok)
        self.assertEqual(result.result_class, "policy_mismatch")
        self.assertIsNone(result.plan)

    def test_project_and_receipt_authority_substitution_refuse(self):
        policy, project, receipt = valid_channel_mappings()
        project["policy_digest"] = policy["policy_digest"]
        with self.assertRaises(HostingImageConfigError) as raised:
            normalize_project_image_intent(project)
        self.assertEqual(raised.exception.code, "authority_substitution")

        policy, project, receipt = valid_channel_mappings()
        receipt["authority_id"] = policy["authority_id"]
        with self.assertRaises(HostingImageConfigError) as raised:
            normalize_release_receipt(receipt)
        self.assertEqual(raised.exception.code, "authority_substitution")

    def test_claimed_and_machine_approved_receipt_digests_are_separate_checks(self):
        policy, project, receipt = valid_channel_mappings()
        receipt["payload_digest"] = "sha256:" + "9" * 64
        self.assertEqual(
            verify_image_plan(*channel_objects(policy, project, receipt)).result_class,
            "receipt_mismatch")

        policy, project, receipt = valid_channel_mappings()
        policy = policy_mapping(receipt=receipt,
                                approved_receipt_payload_digest="sha256:" + "9" * 64)
        self.assertEqual(
            verify_image_plan(*channel_objects(policy, project, receipt)).result_class,
            "policy_mismatch")

    def test_each_provenance_identity_mismatch_refuses(self):
        mutations = {
            "source_repository": "acme/other-source",
            "source_revision": "4" * 40,
            "build_identity": "sha256:" + "9" * 64,
            "provenance": {
                "builder_id": "sha256:" + "9" * 64,
                "workflow_id": "sha256:" + "7" * 64,
                "invocation_id": "sha256:" + "8" * 64,
                "materials_digest": "sha256:" + "4" * 64,
            },
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                receipt = receipt_mapping(payload_changes={field: value})
                baseline = receipt_mapping()["payload"][field]
                # Approve the changed receipt identity while retaining the
                # baseline provenance constraint, so provenance owns refusal.
                policy = policy_mapping(receipt=receipt, **{field: baseline})
                result = verify_image_plan(*channel_objects(
                    policy, project_intent_mapping(), receipt))
                self.assertFalse(result.ok)
                self.assertEqual(result.result_class, "provenance_mismatch")


if __name__ == "__main__":
    unittest.main()
