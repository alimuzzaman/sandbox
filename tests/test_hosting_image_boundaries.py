from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
from unittest import mock

from sandbox.config.hosting_images import (
    HostingImageConfigError, normalize_machine_image_policies,
    normalize_project_image_intent, normalize_release_receipt,
)
from sandbox.config.manifest import apply_machine_config
from sandbox.hosting.images import (
    ImageContractError, reject_legacy_image_authority,
    validate_verified_image_plan, verify_image_plan,
)
from sandbox.hosting.images.models import canonical_digest, canonical_json
from tests.hosting_image_fixtures import (
    channel_objects, policy_mapping, project_intent_mapping, receipt_mapping,
    valid_channel_mappings, valid_channels, verified_plan_mapping,
)


class _ExplosiveDict(dict):
    def __iter__(self):
        raise AssertionError("adversarial mapping iterated")

    def items(self):
        raise AssertionError("adversarial mapping items read")

    def get(self, *_args, **_kwargs):
        raise AssertionError("adversarial mapping get called")

    def __len__(self):
        raise AssertionError("adversarial mapping sized")


class TestExactRawBoundaries(unittest.TestCase):
    def test_mapping_and_list_subclasses_refuse_before_iteration(self):
        raw_policy, raw_project, raw_receipt = valid_channel_mappings()
        for boundary, value in (
            (normalize_machine_image_policies,
             _ExplosiveDict({"production-widget": raw_policy})),
            (normalize_project_image_intent, _ExplosiveDict(raw_project)),
            (normalize_release_receipt, _ExplosiveDict(raw_receipt)),
            (validate_verified_image_plan, _ExplosiveDict(verified_plan_mapping())),
            (apply_machine_config, {"hosting": _ExplosiveDict({
                "images": {"policies": {"production-widget": raw_policy}},
            })}),
        ):
            with self.subTest(boundary=boundary):
                with self.assertRaises((HostingImageConfigError, ImageContractError)):
                    boundary(value)

        project = project_intent_mapping()
        project["persistent_services"] = type("HostileList", (list,), {})(["web"])
        with self.assertRaises(HostingImageConfigError):
            normalize_project_image_intent(project)

        project = project_intent_mapping()
        project["policy_selector"] = type("HostileText", (str,), {})("production-widget")
        with self.assertRaises(HostingImageConfigError):
            normalize_project_image_intent(project)

        project = project_intent_mapping()
        project["schema_version"] = type("HostileInteger", (int,), {})(1)
        with self.assertRaises(HostingImageConfigError):
            normalize_project_image_intent(project)

    def test_pure_verifier_rejects_raw_channels_and_interchanged_exact_types(self):
        raw = valid_channel_mappings()
        result = verify_image_plan(*raw)
        self.assertFalse(result.ok)
        self.assertEqual(result.result_class, "input_invalid")
        trusted, project, receipt = valid_channels()
        for values in (
            (project, trusted, receipt), (trusted, receipt, project),
            (receipt, project, trusted),
        ):
            with self.subTest(values=tuple(type(item).__name__ for item in values)):
                result = verify_image_plan(*values)
                self.assertFalse(result.ok)
                self.assertEqual(result.result_class, "input_invalid")

    def test_unknown_versions_duplicates_and_channel_substitution_refuse_at_owner(self):
        raw_policy, raw_project, raw_receipt = valid_channel_mappings()
        cases = (
            (normalize_machine_image_policies,
             {"production-widget": {**raw_policy, "schema_version": 2}}),
            (normalize_project_image_intent, {**raw_project, "schema_version": 2}),
            (normalize_release_receipt,
             {**raw_receipt, "payload": {**raw_receipt["payload"], "schema_version": 2}}),
            (normalize_project_image_intent,
             {**raw_project, "declared_services": ["web", "web"]}),
            (normalize_project_image_intent,
             {**raw_project, "policy_digest": raw_policy["policy_digest"]}),
            (normalize_release_receipt,
             {**raw_receipt, "authority_id": raw_policy["authority_id"]}),
        )
        for boundary, value in cases:
            with self.subTest(boundary=boundary):
                with self.assertRaises(HostingImageConfigError):
                    boundary(value)


class TestRunningCanonicalBudgets(unittest.TestCase):
    def test_total_node_budget_refuses_many_small_nested_values(self):
        value = [[index for index in range(64)] for _ in range(64)]
        with self.assertRaises(ImageContractError) as raised:
            canonical_json(value)
        self.assertEqual(raised.exception.code, "input_too_large")

    def test_running_byte_budget_refuses_before_full_serialization(self):
        value = [["x" * 512 for _ in range(64)] for _ in range(64)]
        with self.assertRaises(ImageContractError) as raised:
            canonical_json(value)
        self.assertEqual(raised.exception.code, "input_too_large")

    def test_integer_budget_refuses_before_decimal_serialization(self):
        with self.assertRaises(ImageContractError) as raised:
            canonical_json(2**64)
        self.assertEqual(raised.exception.code, "input_too_large")

    def test_string_collection_nesting_and_diagnostic_bounds_are_safe(self):
        with self.assertRaises(ImageContractError):
            canonical_json({"value": "x" * 513})
        with self.assertRaises(ImageContractError):
            canonical_json([0] * 65)
        with self.assertRaises(ImageContractError):
            canonical_json({"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}})

        marker = "untrusted-private-diagnostic-marker"
        raw_policy, raw_project, raw_receipt = valid_channel_mappings()
        raw_receipt["payload"]["repository"] = marker
        with self.assertRaises(HostingImageConfigError) as raised:
            normalize_release_receipt(raw_receipt)
        self.assertNotIn(marker, str(raised.exception))

    def test_canonical_domains_are_not_interchangeable(self):
        value = {"schema_version": 1, "identity": "same-bytes"}
        values = {
            canonical_digest("sandbox.hosting.images.release-receipt.v1", value),
            canonical_digest("sandbox.hosting.images.machine-policy.v1", value),
            canonical_digest("sandbox.hosting.images.verified-plan.v1", value),
        }
        self.assertEqual(len(values), 3)


class TestEffectDenial(unittest.TestCase):
    def test_verifier_exposes_no_effect_dependency_parameter(self):
        with self.assertRaises(TypeError):
            verify_image_plan(*valid_channels(), effect=lambda: None)

    def test_exact_success_relationship_refusal_and_boundary_rejection_reach_no_effect(self):
        success = valid_channels()
        raw_policy, raw_project, raw_receipt = valid_channel_mappings()
        mismatch_receipt = receipt_mapping(payload_changes={"config_digest": "sha256:" + "8" * 64})
        refusal = channel_objects(
            policy_mapping(receipt=mismatch_receipt, image=raw_policy["image"]),
            raw_project, mismatch_receipt)
        explosive = _ExplosiveDict(raw_receipt)

        denied = AssertionError("effect reached")
        with mock.patch.object(builtins, "open", side_effect=denied), \
                mock.patch.object(Path, "read_text", side_effect=denied), \
                mock.patch.object(Path, "write_text", side_effect=denied), \
                mock.patch.object(socket, "create_connection", side_effect=denied), \
                mock.patch.object(subprocess, "run", side_effect=denied), \
                mock.patch.object(time, "time", side_effect=denied), \
                mock.patch.object(os, "urandom", side_effect=denied):
            allowed = verify_image_plan(*success)
            refused = verify_image_plan(*refusal)
            raw_refused = verify_image_plan(raw_policy, raw_project, raw_receipt)
            with self.assertRaises(HostingImageConfigError):
                normalize_release_receipt(explosive)

        self.assertTrue(allowed.ok)
        self.assertFalse(refused.ok)
        self.assertFalse(raw_refused.ok)

    def test_unexpected_exception_is_safe_projected_without_text(self):
        trusted, project, receipt = valid_channels()
        marker = "raw-exception-marker"
        with mock.patch(
                "sandbox.hosting.images.trust._verify_image_plan",
                side_effect=RuntimeError(marker)):
            result = verify_image_plan(trusted, project, receipt)
        rendered = json.dumps(result.as_mapping(), sort_keys=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.result_class, "input_invalid")
        self.assertNotIn(marker, rendered)


class _OpaqueLegacyState:
    def __iter__(self):
        raise AssertionError("legacy value traversed")

    def __getattribute__(self, name):
        if name.startswith("__"):
            return object.__getattribute__(self, name)
        raise AssertionError("legacy value inspected")


class TestLegacyNonAuthority(unittest.TestCase):
    def test_feature_047_and_048_values_remain_unchanged_and_non_authorizing(self):
        fixtures = (
            {"schema_version": 2, "image_planes": {"current": {"authorizing": True}}},
            {"schema_version": 1, "recovery_receipt": {"state": "ready"}},
        )
        for value in fixtures:
            before = canonical_json(value)
            result = reject_legacy_image_authority(value)
            self.assertFalse(result.ok)
            self.assertEqual(canonical_json(value), before)
        self.assertFalse(reject_legacy_image_authority(_OpaqueLegacyState()).ok)


if __name__ == "__main__":
    unittest.main()
