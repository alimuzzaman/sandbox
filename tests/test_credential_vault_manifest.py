"""Acceptance-manifest schema, canonical encoding, and digest stability.

Offline only. Nothing here contacts a host, and no value in this file is a real
identity, revision, or credential.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    catalog as catalog_module, fixtures, manifest as manifest_module,
)


class TestAcceptanceManifest(unittest.TestCase):
    def test_the_fixture_manifest_is_accepted_and_digest_is_stable(self):
        document = manifest_module.validate_manifest(fixtures.manifest())
        first = manifest_module.manifest_digest(document)
        # Key order must not change the digest; canonical encoding decides it.
        shuffled = dict(reversed(list(document.items())))
        self.assertEqual(manifest_module.manifest_digest(shuffled), first)
        self.assertEqual(len(first), 64)
        self.assertEqual(
            manifest_module.canonical_json(document),
            json.dumps(document, sort_keys=True, separators=(",", ":")),
        )

    def test_an_unknown_key_anywhere_is_refused(self):
        for mutate in (
            lambda value: value.update({"extra": 1}),
            lambda value: value["source"].update({"extra": 1}),
            lambda value: value["service"].update({"extra": 1}),
            lambda value: value["checks"][0].update({"extra": 1}),
            lambda value: value["artifacts"][0].update({"extra": 1}),
        ):
            with self.subTest(mutate=mutate):
                document = fixtures.manifest()
                mutate(document)
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertIn(raised.exception.code,
                              {"schema_unknown_key", "section_invalid"})

    def test_a_missing_key_is_refused_rather_than_defaulted(self):
        for section in ("source", "target", "platform", "service", "transport",
                        "kernel", "bounds", "cleanup"):
            with self.subTest(section=section):
                document = fixtures.manifest()
                document[section].pop(next(iter(document[section])))
                with self.assertRaises(manifest_module.ManifestError):
                    manifest_module.validate_manifest(document)

    def test_secret_like_material_anywhere_is_refused(self):
        cases = (
            ("manifest_id", fixtures.SECRET_SHAPED["authorization_header"]),
            ("manifest_id", fixtures.SECRET_SHAPED["aws_access_key"]),
        )
        for field, value in cases:
            with self.subTest(value=value[:16]):
                document = fixtures.manifest()
                document[field] = value
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertIn(raised.exception.code,
                              {"secret_like_material", "field_invalid"})
        forbidden = fixtures.manifest()
        forbidden["target"] = dict(forbidden["target"])
        forbidden["target"][fixtures.SECRET_SHAPED["internal_identifier"]] = "op-1"
        with self.assertRaises(manifest_module.ManifestError):
            manifest_module.validate_manifest(forbidden)

    def test_an_unsupported_platform_is_refused_before_anything_else(self):
        for field, value in (("os_release", "debian-12"),
                             ("architecture", "riscv64"),
                             ("kernel_release", "not a kernel")):
            with self.subTest(field=field):
                document = fixtures.manifest()
                document["platform"][field] = value
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertIn(raised.exception.code,
                              {"platform_unsupported", "field_invalid"})

    def test_identities_must_be_distinct_and_private(self):
        shared_socket = fixtures.manifest()
        shared_socket["transport"]["controller_socket"] = \
            shared_socket["transport"]["lease_socket"]
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(shared_socket)
        self.assertEqual(raised.exception.code, "socket_identity_shared")

        public = fixtures.manifest()
        public["transport"]["guest_address"] = "8.8.8.8"
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(public)
        self.assertEqual(raised.exception.code, "address_not_private")

        shared_uid = fixtures.manifest()
        shared_uid["service"]["controller_uid"] = shared_uid["service"]["service_uid"]
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(shared_uid)
        self.assertEqual(raised.exception.code, "service_uid_shared")

    def test_bounds_and_capabilities_must_not_contradict(self):
        contradiction = fixtures.manifest()
        contradiction["bounds"]["connect_seconds"] = 5
        contradiction["bounds"]["total_seconds"] = 2
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(contradiction)
        self.assertEqual(raised.exception.code, "bounds_contradiction")

        capabilities = fixtures.manifest()
        capabilities["kernel"]["required_capabilities"] = ["CAP_SYS_ADMIN"]
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(capabilities)
        self.assertEqual(raised.exception.code, "capability_contradiction")

        oversize = fixtures.manifest()
        oversize["bounds"]["max_concurrent"] = 64
        with self.assertRaises(manifest_module.ManifestError):
            manifest_module.validate_manifest(oversize)

    def test_checks_and_artifacts_must_be_unique_and_meaningful(self):
        duplicate = fixtures.manifest()
        duplicate["checks"].append(dict(duplicate["checks"][0]))
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(duplicate)
        self.assertEqual(raised.exception.code, "list_duplicate")

        optional_only = fixtures.manifest()
        for item in optional_only["checks"]:
            item["required"] = False
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.validate_manifest(optional_only)
        self.assertEqual(raised.exception.code, "check_requirement_mismatch")

        empty = fixtures.manifest()
        empty["artifacts"] = []
        with self.assertRaises(manifest_module.ManifestError):
            manifest_module.validate_manifest(empty)

    def test_a_revision_mismatch_refuses_before_any_test_action(self):
        document = manifest_module.validate_manifest(fixtures.manifest())
        self.assertTrue(manifest_module.assert_revision(document, {
            "git_sha": fixtures.GIT_SHA,
            "sandbox_revision": fixtures.SANDBOX_REVISION,
        })["ok"])
        for observed in (
            {"git_sha": "1" * 40, "sandbox_revision": fixtures.SANDBOX_REVISION},
            {"git_sha": fixtures.GIT_SHA, "sandbox_revision": "sandbox-9.9.9"},
        ):
            with self.subTest(observed=tuple(observed.values())[0][:8]):
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.assert_revision(document, observed)
                self.assertEqual(raised.exception.code, "revision_mismatch")
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.assert_revision(document, {"git_sha": fixtures.GIT_SHA})
        self.assertEqual(raised.exception.code, "revision_observation_invalid")

    def test_loading_refuses_symlinks_oversize_and_non_canonical_files(self):
        document = manifest_module.validate_manifest(fixtures.manifest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "manifest.json"
            good.write_text(manifest_module.canonical_json(document))
            self.assertEqual(manifest_module.load_manifest(good)["manifest_id"],
                             document["manifest_id"])

            pretty = root / "pretty.json"
            pretty.write_text(json.dumps(document, indent=2))
            with self.assertRaises(manifest_module.ManifestError) as raised:
                manifest_module.load_manifest(pretty)
            self.assertEqual(raised.exception.code, "encoding_not_canonical")

            link = root / "link.json"
            link.symlink_to(good)
            with self.assertRaises(manifest_module.ManifestError) as raised:
                manifest_module.load_manifest(link)
            self.assertEqual(raised.exception.code, "manifest_symlink")

            missing = root / "absent.json"
            with self.assertRaises(manifest_module.ManifestError) as raised:
                manifest_module.load_manifest(missing)
            self.assertEqual(raised.exception.code, "manifest_missing")

            huge = root / "huge.json"
            huge.write_text(" " * (manifest_module.MAX_DOCUMENT_BYTES + 1))
            with self.assertRaises(manifest_module.ManifestError) as raised:
                manifest_module.load_manifest(huge)
            self.assertEqual(raised.exception.code, "document_oversize")

    def test_helpers_expose_only_planned_identities(self):
        document = manifest_module.validate_manifest(fixtures.manifest())
        required = sum(definition.required
                       for definition in catalog_module.CHECKS.values())
        self.assertEqual(len(manifest_module.check_ids(document)), required)
        self.assertEqual(len(manifest_module.required_check_ids(document)), required)
        self.assertEqual(manifest_module.artifact_names(document),
                         ("checks.json", "cleanup.json"))

    def test_catalog_types_and_artifacts_are_not_manifest_choices(self):
        cases = []
        wrong_category = fixtures.manifest()
        wrong_category["checks"][0]["category"] = "cleanup"
        cases.append((wrong_category, "check_category_mismatch"))
        wrong_requirement = fixtures.manifest()
        wrong_requirement["checks"][0]["required"] = False
        cases.append((wrong_requirement, "check_requirement_mismatch"))
        unknown = fixtures.manifest()
        unknown["checks"][0]["check_id"] = "invented_live_proof"
        cases.append((unknown, "check_unsupported"))
        overbound = fixtures.manifest()
        overbound["artifacts"][0]["max_bytes"] = 262145
        cases.append((overbound, "artifact_bound_too_large"))
        missing = fixtures.manifest()
        missing["artifacts"].pop()
        cases.append((missing, "artifact_catalog_incomplete"))
        for document, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertEqual(raised.exception.code, code)

    def test_every_required_catalog_check_is_mandatory_across_every_category(self):
        required = [definition for definition in catalog_module.CHECKS.values()
                    if definition.required]
        self.assertEqual({item.category for item in required},
                         set(manifest_module.CHECK_CATEGORIES))
        for definition in required:
            with self.subTest(check_id=definition.check_id,
                              category=definition.category):
                document = fixtures.manifest()
                document["checks"] = [item for item in document["checks"]
                                      if item["check_id"] != definition.check_id]
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertEqual(raised.exception.code, "required_check_missing")
                self.assertEqual(raised.exception.location,
                                 f"checks.{definition.check_id}")

    def test_optional_catalog_checks_may_be_omitted(self):
        document = manifest_module.validate_manifest(fixtures.manifest())
        optional = {check_id for check_id, definition in catalog_module.CHECKS.items()
                    if not definition.required}
        self.assertTrue(optional)
        self.assertTrue(optional.isdisjoint(manifest_module.check_ids(document)))

    def test_cleanup_must_cover_exact_catalog_derived_identities(self):
        for field, mutation in (
            ("sockets", lambda value: value.pop()),
            ("units", lambda value: value.append("foreign.service")),
            ("interfaces", lambda value: value.__setitem__(0, "other0")),
        ):
            with self.subTest(field=field):
                document = fixtures.manifest()
                mutation(document["cleanup"][field])
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.validate_manifest(document)
                self.assertEqual(raised.exception.code, "cleanup_coverage_mismatch")


if __name__ == "__main__":
    unittest.main()
