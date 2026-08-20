from __future__ import annotations

import json
import unittest

from sandbox.php_extensions.catalog import (
    DEFAULT_CATALOG,
    PhpExtensionCatalogError,
    normalize_requirements,
    normalize_version_constraint,
)


class PhpExtensionCatalogTests(unittest.TestCase):
    def test_wordpress_profile_is_immutable_and_explicit(self):
        profile = DEFAULT_CATALOG.profile("wordpress@1")
        self.assertIn("gd", {name for group in profile.capability_alternatives for name in group})
        self.assertIn("curl", profile.required)
        self.assertIn("zip", profile.recommended)
        self.assertEqual(DEFAULT_CATALOG.digest, DEFAULT_CATALOG.digest)
        self.assertNotIn("pecl", json.dumps(DEFAULT_CATALOG.to_dict()).lower())
        self.assertNotIn("http://", json.dumps(DEFAULT_CATALOG.to_dict()).lower())
        self.assertNotIn("https://", json.dumps(DEFAULT_CATALOG.to_dict()).lower())

    def test_model_like_and_public_mapping_preserve_state_and_version(self):
        class Requirement:
            name = "gd"
            state = "enabled"
            version = "2.3.*"

        class Config:
            profile = "wordpress@1"
            requirements = (Requirement(),)

        self.assertEqual(
            normalize_requirements(Config()),
            ({"name": "gd", "state": "enabled", "version": "2.3.*"},),
        )
        self.assertEqual(
            normalize_requirements({
                "profile": "wordpress@1",
                "extensions": {"gd": {"state": "enabled", "version": "2.3.*"}},
            }),
            ({"name": "gd", "state": "enabled", "version": "2.3.*"},),
        )

    def test_catalog_rejects_unknown_and_reports_provisioning_boundary(self):
        validation = DEFAULT_CATALOG.validate({"does_not_exist": True})
        self.assertFalse(validation.ok)
        self.assertEqual(validation.issues[0].code, "unknown_extension")
        validation = DEFAULT_CATALOG.validate({"xdebug": True}, require_provisioning=True)
        self.assertFalse(validation.ok)
        self.assertEqual(validation.issues[0].code, "unsupported_provisioning")

    def test_profile_requires_core_and_image_capability(self):
        validation = DEFAULT_CATALOG.validate({"gd": True}, profile="wordpress@1")
        self.assertFalse(validation.ok)
        self.assertIn("profile_required_missing", {issue.code for issue in validation.issues})
        required = {item["name"]: True
                    for item in DEFAULT_CATALOG.profile_requirements("wordpress@1")}
        valid = DEFAULT_CATALOG.validate({**required, "gd": True}, profile="wordpress@1")
        self.assertTrue(valid.ok)

    def test_catalog_rejects_arbitrary_recipe_metadata(self):
        with self.assertRaises(ValueError):
            DEFAULT_CATALOG.recipe("php://filter")
        with self.assertRaises(PhpExtensionCatalogError):
            normalize_requirements({"gd": {"state": "enabled", "url": "https://bad"}})

    def test_version_constraint_is_small_and_explicit(self):
        self.assertEqual(normalize_version_constraint("2.3.*"), "2.3.*")
        self.assertEqual(normalize_version_constraint("php"), "php")
        with self.assertRaises(PhpExtensionCatalogError):
            normalize_version_constraint(">=2.3")
        with self.assertRaises(PhpExtensionCatalogError):
            normalize_version_constraint("2.3.4.5.6")


if __name__ == "__main__":
    unittest.main()
