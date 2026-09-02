from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import sandbox.hosting.images as public_image_contract
from sandbox.config.compose import ComposeSchemaProvider
from sandbox.config.hosting_images import (
    HostingImageConfigError, normalize_machine_image_policies,
    normalize_project_image_intent, normalize_release_receipt,
)
from sandbox.config.manifest import (
    COMMON_CONFIG_PROVIDERS, MACHINE_CONFIG_PROVIDERS,
    apply_common_config, apply_machine_config,
)
from sandbox.config.wordpress import WordPressSchemaProvider
from sandbox.hosting.images import (
    ImageContractError,
    validate_verified_image_plan, verify_image_plan,
)
from tests.hosting_image_fixtures import (
    channel_objects, policy_mapping, project_intent_mapping, receipt_mapping,
    valid_channel_mappings, valid_channels, verified_plan_mapping,
)


def _dict_nodes(value, path=()):
    if type(value) is dict:
        yield path, value
        for key, item in value.items():
            yield from _dict_nodes(item, path + (key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from _dict_nodes(item, path + (index,))


def _leaf_paths(value, path=()):
    if type(value) is dict:
        for key, item in value.items():
            yield from _leaf_paths(item, path + (key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from _leaf_paths(item, path + (index,))
    else:
        yield path, value


def _replace(value, path, replacement):
    result = deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return result


def _mutation(value):
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if value.startswith("sha256:"):
        return "sha256:" + ("8" if value[7] != "8" else "9") * 64
    if len(value) in {40, 64} and set(value) <= set("0123456789abcdef"):
        return ("8" if value[0] != "8" else "9") * len(value)
    return value + "-changed"


class TestOwningInputBoundaries(unittest.TestCase):
    def test_outer_receipt_digest_is_required_but_payload_cannot_claim_one(self):
        raw = receipt_mapping()
        receipt = normalize_release_receipt(raw)
        self.assertEqual(receipt.claimed_payload_digest, raw["payload_digest"])

        raw["payload"]["payload_digest"] = raw["payload_digest"]
        with self.assertRaises(HostingImageConfigError) as raised:
            normalize_release_receipt(raw)
        self.assertEqual(raised.exception.code, "authority_substitution")

    def test_each_raw_channel_object_is_closed_recursively(self):
        policy, project, receipt = valid_channel_mappings()
        cases = (
            (lambda value: normalize_machine_image_policies(
                {"production-widget": value}), policy),
            (normalize_project_image_intent, project),
            (normalize_release_receipt, receipt),
        )
        for boundary, raw in cases:
            for path, node in tuple(_dict_nodes(raw)):
                for key in tuple(node):
                    missing = deepcopy(raw)
                    target = missing
                    for part in path:
                        target = target[part]
                    target.pop(key)
                    with self.subTest(boundary=boundary, path=path, missing=key):
                        with self.assertRaises(HostingImageConfigError):
                            boundary(missing)
                unknown = deepcopy(raw)
                target = unknown
                for part in path:
                    target = target[part]
                target["unknown_field"] = "bounded"
                with self.subTest(boundary=boundary, path=path, unknown=True):
                    with self.assertRaises(HostingImageConfigError):
                        boundary(unknown)

    def test_tag_index_registry_digest_platform_and_signature_forms_refuse(self):
        mutations = (
            {"repository": "acme/widget:latest"},
            {"manifest_media_type": "application/vnd.oci.image.index.v1+json"},
            {"manifest_digest": "sha256:ABC"},
            {"platform": {"os": "linux", "architecture": "x86_64"}},
            {"signature_mode": "cosign"},
        )
        for change in mutations:
            with self.subTest(change=change):
                with self.assertRaises(HostingImageConfigError):
                    normalize_release_receipt(receipt_mapping(payload_changes=change))

        policy = policy_mapping()
        policy["image"]["registry"] = "registry.example.invalid"
        with self.assertRaises(HostingImageConfigError):
            normalize_machine_image_policies({"production-widget": policy})

    def test_closed_provenance_allows_only_four_opaque_digests(self):
        valid = receipt_mapping()["payload"]["provenance"]
        invalid = ({"builder": "sha256:" + "6" * 64},)
        for hostile in (
            "/private/path", "../private", "token-like-bare-secret",
            "Authorization: Bearer secret", "api_key=secret",
        ):
            invalid += ({**valid, "builder_id": hostile},)
        for provenance in invalid:
            with self.subTest(provenance=provenance):
                with self.assertRaises(HostingImageConfigError):
                    normalize_release_receipt(receipt_mapping(
                        payload_changes={"provenance": provenance}))

    def test_source_and_build_privacy_fields_accept_only_canonical_names_or_digests(self):
        cases = (
            {"build_identity": "/private/path"},
            {"build_identity": "../private"},
            {"build_identity": "token-like-bare-secret"},
            {"build_identity": "Authorization: Bearer secret"},
            {"build_identity": "api_key=secret"},
            {"source_repository": "/private/path"},
            {"source_repository": "../private"},
            {"source_repository": "owner/../private"},
            {"source_repository": "Authorization/Bearer"},
            {"source_repository": "api_key=secret/repository"},
            {"source_revision": "token-like-bare-secret"},
            {"source_revision": "A" * 40},
        )
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaises(HostingImageConfigError):
                    normalize_release_receipt(receipt_mapping(payload_changes=change))

    def test_relationship_mismatches_refuse_only_after_exact_channels_exist(self):
        policy_raw, project_raw, _ = valid_channel_mappings()
        receipt_raw = receipt_mapping(payload_changes={"config_digest": "sha256:" + "8" * 64})
        trusted, project, receipt = channel_objects(
            policy_mapping(receipt=receipt_raw, image=policy_raw["image"]),
            project_raw, receipt_raw)
        result = verify_image_plan(trusted, project, receipt)
        self.assertFalse(result.ok)
        self.assertEqual(result.result_class, "receipt_mismatch")

    def test_machine_topology_is_maximum_and_project_can_only_narrow(self):
        trusted, _, receipt = valid_channels()
        narrow = normalize_project_image_intent(project_intent_mapping(
            declared_services=["web"], persistent_services=["web"], one_shot_services=[]))
        self.assertTrue(verify_image_plan(trusted, narrow, receipt).ok)
        for raw in (
            project_intent_mapping(persistent_services=["worker"]),
            project_intent_mapping(persistent_services=["web", "migrate"], one_shot_services=[]),
        ):
            with self.subTest(raw=raw):
                project = normalize_project_image_intent(raw)
                self.assertFalse(verify_image_plan(trusted, project, receipt).ok)

        undeclared = project_intent_mapping(one_shot_services=["seed", "unknown"])
        with self.assertRaises(HostingImageConfigError):
            normalize_project_image_intent(undeclared)


class TestPlanConsumerContract(unittest.TestCase):
    def test_independent_fixed_plan_vector_validates(self):
        raw = verified_plan_mapping()
        self.assertEqual(validate_verified_image_plan(raw).as_mapping(), raw)

    def test_every_recursive_leaf_mutation_refuses(self):
        raw = verified_plan_mapping()
        for path, value in _leaf_paths(raw):
            with self.subTest(path=path):
                with self.assertRaises(ImageContractError):
                    validate_verified_image_plan(_replace(raw, path, _mutation(value)))

    def test_every_recursive_object_refuses_missing_and_unknown_fields(self):
        raw = verified_plan_mapping()
        for path, node in tuple(_dict_nodes(raw)):
            for key in tuple(node):
                missing = deepcopy(raw)
                target = missing
                for part in path:
                    target = target[part]
                target.pop(key)
                with self.subTest(path=path, missing=key):
                    with self.assertRaises(ImageContractError):
                        validate_verified_image_plan(missing)
            unknown = deepcopy(raw)
            target = unknown
            for part in path:
                target = target[part]
            target["unknown_field"] = "bounded"
            with self.subTest(path=path, unknown=True):
                with self.assertRaises(ImageContractError):
                    validate_verified_image_plan(unknown)

    def test_noncanonical_arrays_and_legacy_partial_envelopes_refuse(self):
        raw = verified_plan_mapping()
        raw["topology"]["persistent_services"].reverse()
        for value in (
            raw, {"schema_version": 2}, {"image_plane": {"schema_version": 2}},
            {"recovery_receipt": {"schema_version": 1}},
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImageContractError):
                    validate_verified_image_plan(value)


class TestHostingImageConfigProviders(unittest.TestCase):
    def test_manifest_registers_distinct_trusted_machine_provider(self):
        common = {item[0]: item[2] for item in COMMON_CONFIG_PROVIDERS}
        machine = {item[0]: item[2] for item in MACHINE_CONFIG_PROVIDERS}
        self.assertEqual(common["hostingImages"], "sandbox.config.hosting_images")
        self.assertEqual(machine["hosting.images.policies"], "sandbox.config.hosting_images")
        policies = normalize_machine_image_policies({"production-widget": policy_mapping()})
        token = policies["production-widget"]
        self.assertNotIn(type(token).__name__, public_image_contract.__all__)
        self.assertFalse(hasattr(public_image_contract, "TrustedMachinePolicy"))
        self.assertFalse(hasattr(public_image_contract, "_TrustedMachinePolicy"))
        self.assertFalse(hasattr(public_image_contract, "_issue_trusted_machine_policy"))
        with self.assertRaises(ImageContractError):
            type(token)(token.policy)

    def _compose(self, root, *, primary=None, override=None, label=None, global_intent=None):
        descriptor = {
            "kind": "compose",
            "compose": {"file": "compose.yaml", "service": "web",
                        "internal_port": 8080, "health_path": "/healthz"},
        }
        if primary is not None:
            descriptor["hostingImages"] = primary
        (root / "sandbox.config.json").write_text(json.dumps(descriptor))
        (root / "compose.yaml").write_text("services: {web: {image: example}}\n")
        if override is not None:
            (root / "sandbox.config.override.json").write_text(json.dumps(
                {"hostingImages": override}))
        if label is not None:
            (root / "sandbox.config.qa.json").write_text(json.dumps(
                {"hostingImages": label}))
        raw = ComposeSchemaProvider().resolve(root, label="qa")
        if global_intent is not None:
            raw["hostingImages"] = global_intent
        return apply_common_config(raw)

    def _wordpress(self, root, *, primary=None, override=None, label=None, global_intent=None):
        descriptor = {"kind": "wordpress"}
        if primary is not None:
            descriptor["hostingImages"] = primary
        (root / "sandbox.config.json").write_text(json.dumps(descriptor))
        if override is not None:
            (root / "sandbox.config.override.json").write_text(json.dumps(
                {"hostingImages": override}))
        if label is not None:
            (root / "sandbox.config.qa.json").write_text(json.dumps(
                {"hostingImages": label}))
        def legacy(_root, label=None):
            result = {"root": str(root), "label": label or "default"}
            if global_intent is not None:
                result["hostingImages"] = global_intent
            return result
        return apply_common_config(WordPressSchemaProvider(legacy).resolve(root, label="qa"))

    def test_primary_project_layer_is_equivalent_for_both_config_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            compose_root, wordpress_root = base / "compose", base / "wordpress"
            compose_root.mkdir(); wordpress_root.mkdir()
            intent = project_intent_mapping()
            compose = self._compose(compose_root, primary=intent)
            wordpress = self._wordpress(wordpress_root, primary=intent)
            self.assertEqual(compose["hostingImages"], wordpress["hostingImages"])

    def test_override_and_label_layers_cannot_supply_or_replace_project_intent(self):
        foreign = project_intent_mapping(policy_selector="foreign-policy")
        for kind in ("compose", "wordpress"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                resolver = self._compose if kind == "compose" else self._wordpress
                resolved = resolver(root, primary=project_intent_mapping(),
                                    override=foreign, label=foreign)
                self.assertEqual(resolved["hostingImages"].policy_selector,
                                 "production-widget")

    def test_absent_primary_is_identical_even_when_other_layers_declare_intent(self):
        foreign = project_intent_mapping(policy_selector="foreign-policy")
        for kind in ("compose", "wordpress"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                resolver = self._compose if kind == "compose" else self._wordpress
                clean = resolver(root)
                layered = resolver(root, override=foreign, label=foreign)
                self.assertEqual(layered, clean)
                self.assertNotIn("hostingImages", layered)

    def test_global_layer_cannot_supply_intent_for_either_config_kind(self):
        foreign = project_intent_mapping(policy_selector="foreign-policy")
        for kind in ("compose", "wordpress"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                resolver = self._compose if kind == "compose" else self._wordpress
                root = Path(directory)
                clean = resolver(root)
                inherited = resolver(root, global_intent=foreign)
                self.assertEqual(inherited, clean)
                self.assertNotIn("hostingImages", inherited)

    def test_machine_provider_preserves_siblings_and_issues_trusted_type(self):
        raw = {"hosting": {"otherOwner": {"enabled": True}, "images": {
            "policies": {"production-widget": policy_mapping()}}}}
        before = deepcopy(raw)
        resolved = apply_machine_config(raw)
        self.assertEqual(raw, before)
        self.assertEqual(resolved["hosting"]["otherOwner"], {"enabled": True})
        token = resolved["hosting"]["images"]["policies"]["production-widget"]
        self.assertTrue(type(token).__name__.startswith("_"))
        with self.assertRaises(ImageContractError):
            type(token)(token.policy)


if __name__ == "__main__":
    unittest.main()
