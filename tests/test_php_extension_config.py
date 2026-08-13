import json
import tempfile
import unittest
from pathlib import Path


class TestPhpExtensionConfig(unittest.TestCase):
    def test_omitted_field_is_a_noop(self):
        import sandbox_core
        from sandbox.config.php_extensions import normalize_php_extensions

        self.assertIsNone(normalize_php_extensions(None))
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "fixture"}))
            result = sandbox_core.load_project_config(root)
        self.assertNotIn("phpExtensions", result)

    def test_shorthand_and_object_requirements_are_canonical_and_immutable(self):
        from sandbox.config.php_extensions import normalize_php_extensions
        from sandbox.php_extensions.models import PhpExtensionsConfig

        config = normalize_php_extensions({
            "extensions": {
                "GD": True,
                "imagick": {"state": "enabled", "version": "3.7.*"},
                "xdebug": "php",
                "zip": False,
            },
        })
        self.assertIsInstance(config, PhpExtensionsConfig)
        self.assertEqual(config.by_name["gd"].state, "enabled")
        self.assertEqual(config.by_name["imagick"].version, "3.7.*")
        self.assertEqual(config.by_name["xdebug"].version, "php")
        self.assertEqual(config["extensions"]["zip"]["state"], "disabled")
        with self.assertRaises(TypeError):
            config["extensions"]["gd"]["state"] = "disabled"
        with self.assertRaises(TypeError):
            config["extensions"]["new"] = {"state": "enabled"}
        self.assertEqual(config.to_dict()["extensions"]["gd"], {"state": "enabled"})

    def test_wordpress_profile_adds_required_members_and_image_capability(self):
        from sandbox.config.php_extensions import normalize_php_extensions

        config = normalize_php_extensions({
            "profile": "wordpress@1",
            "extensions": {"gd": True, "imagick": False},
        })
        self.assertEqual(config.profile, "wordpress@1")
        self.assertTrue(set(config.profile_required).issubset(config.by_name))
        self.assertEqual(config["capabilities"]["image"], ("gd", "imagick"))
        self.assertEqual(config.by_name["gd"].state, "enabled")

    def test_profile_required_members_cannot_be_disabled(self):
        from sandbox.config.php_extensions import normalize_php_extensions

        with self.assertRaisesRegex(ValueError, "required by profile"):
            normalize_php_extensions({
                "profile": "wordpress@1",
                "extensions": {"mysqli": False},
            })
        with self.assertRaisesRegex(ValueError, "at least one"):
            normalize_php_extensions({
                "profile": "wordpress@1",
                "extensions": {"gd": False, "imagick": False},
            })

    def test_unknown_profiles_extensions_and_versions_fail_closed(self):
        from sandbox.config.php_extensions import normalize_php_extensions

        invalid = (
            {"profile": "wordpress@2", "extensions": {}},
            {"extensions": {"not-a-real-extension": True}},
            {"extensions": {"gd": {"state": "enabled", "version": "latest"}}},
            {"extensions": {"gd": {"state": "enabled", "version": "3"}}},
            {"extensions": {"gd": {"state": "disabled", "version": "1.0"}}},
            {"extensions": {"gd": {"state": "enabled", "package": "gd"}}},
            {"extensions": {"GD": True, "gd": False}},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_php_extensions(value)

    def test_root_and_conventional_labeled_config_homes_normalize(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({
                "slug": "fixture",
                "phpExtensions": {"extensions": {"gd": True}},
            }))
            (root / "sandbox.config.qa.json").write_text(json.dumps({
                "phpExtensions": {"extensions": {"gd": "2.3.*", "xdebug": False}},
            }))
            config = sandbox_core.load_project_config(root, label="qa")
        self.assertEqual(config["phpExtensions"].by_name["gd"].version, "2.3.*")
        self.assertEqual(config["phpExtensions"].by_name["xdebug"].state, "disabled")

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            home = root / ".config" / "sandbox"
            home.mkdir(parents=True)
            (home / "sandbox.config.json").write_text(json.dumps({
                "slug": "fixture",
                "phpExtensions": {"extensions": {"gd": True}},
            }))
            (home / "sandbox.config.qa.json").write_text(json.dumps({
                "phpExtensions": {"extensions": {"gd": "2.3.*", "xdebug": False}},
            }))
            config = sandbox_core.load_project_config(root, label="qa")
        self.assertEqual(config["phpExtensions"].by_name["gd"].version, "2.3.*")
        self.assertEqual(config["phpExtensions"].by_name["xdebug"].state, "disabled")

    def test_generic_compose_rejects_php_extensions_in_primary_or_label(self):
        from sandbox.config.facade import resolve_project_config

        for where in ("primary", "label"):
            with self.subTest(where=where), tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
                root = Path(tmp)
                (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
                descriptor = {
                    "kind": "compose",
                    "compose": {
                        "file": "compose.yaml", "service": "web",
                        "internal_port": 80, "health_path": "/",
                    },
                }
                if where == "primary":
                    descriptor["phpExtensions"] = {"extensions": {"gd": True}}
                    (root / "sandbox.config.json").write_text(json.dumps(descriptor))
                    label = None
                else:
                    (root / "sandbox.config.json").write_text(json.dumps(descriptor))
                    (root / "sandbox.config.qa.json").write_text(json.dumps({
                        "phpExtensions": {"extensions": {"gd": True}},
                    }))
                    label = "qa"
                with self.assertRaisesRegex(ValueError, "generic Compose.*phpExtensions"):
                    resolve_project_config(root, label=label, legacy_loader=lambda *_args, **_kwargs: {})


if __name__ == "__main__":
    unittest.main()
