import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TestWordPressRuntimeConfig(unittest.TestCase):
    def test_compose_is_the_only_default(self):
        from sandbox.config.wordpress_runtime import normalize_wordpress_runtime
        result = normalize_wordpress_runtime({"_wordpress_runtime_raw": {
            "project": {}, "machine_override": {},
        }})
        self.assertEqual((result["mode"], result["adapter"], result["source"]),
                         ("compose", "compose", "default"))

    def test_committed_native_requirement_cannot_activate_machine(self):
        from sandbox.config.wordpress_runtime import normalize_wordpress_runtime
        result = normalize_wordpress_runtime({"_wordpress_runtime_raw": {
            "project": {"mode": "managed-native", "adapter": "ubuntu-nspawn"},
            "machine_override": {},
        }})
        self.assertEqual(result["mode"], "compose")
        self.assertEqual(result["reason"], "explicit_selection_required")
        self.assertFalse(result["explicit"])

    def test_gitignored_machine_override_explicitly_selects_native(self):
        from sandbox.config.wordpress_runtime import normalize_wordpress_runtime
        result = normalize_wordpress_runtime({"_wordpress_runtime_raw": {
            "project": {"php": "8.3"},
            "machine_override": {"mode": "managed-native", "adapter": "ubuntu-nspawn",
                                 "webServer": "nginx"},
        }})
        self.assertEqual(result["mode"], "managed_native")
        self.assertEqual(result["source"], "machine_override")
        self.assertEqual(result["php"], "8.3")

    def test_unknown_keys_and_implicit_native_adapter_fail(self):
        from sandbox.config.wordpress_runtime import normalize_wordpress_runtime
        for machine in ({"mode": "managed-native"}, {"surprise": True}):
            with self.subTest(machine=machine), self.assertRaises(ValueError):
                normalize_wordpress_runtime({"_wordpress_runtime_raw": {
                    "project": {}, "machine_override": machine,
                }})

    def test_wordpress_loader_preserves_machine_provenance(self):
        from sandbox.config.facade import resolve_project_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sandbox.config.json").write_text(json.dumps({
                "wordpressRuntime": {"php": "8.3"},
            }))
            (root / "sandbox.config.override.json").write_text(json.dumps({
                "wordpressRuntime": {"mode": "managed-native",
                                     "adapter": "ubuntu-nspawn"},
            }))
            result = resolve_project_config(
                root, legacy_loader=mock.Mock(return_value={}),
            )
        self.assertEqual(result["wordpressRuntime"]["mode"], "managed_native")
        self.assertEqual(result["wordpressRuntime"]["source"], "machine_override")


if __name__ == "__main__": unittest.main()
