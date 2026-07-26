import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class RuntimeConfigTests(unittest.TestCase):
    def test_builtin_profiles_are_finite_and_remote_default_is_preserved(self):
        from sandbox.config.runtime import normalize_runtime_policy

        runtime = normalize_runtime_policy({
            "default": "remote", "remote": "scaleway-sandbox", "workspace": "node-unit"
        })
        self.assertEqual(runtime["default"], "remote")
        self.assertEqual(runtime["remote"], "scaleway-sandbox")
        self.assertEqual(runtime["workspace"], "node-unit")
        self.assertEqual(runtime["executionProfiles"]["unit"]["timeoutSeconds"], 1800)
        self.assertLessEqual(runtime["executionProfiles"]["overnight"]["timeoutSeconds"], 604800)

    def test_custom_execution_and_output_profiles_are_declarative(self):
        from sandbox.config.runtime import normalize_runtime_policy

        runtime = normalize_runtime_policy({
            "executionProfiles": {"long": {"timeoutSeconds": 7200, "stallSeconds": 600}},
            "outputProfiles": {"agent": {"mode": "sampled", "everyLines": 20,
                                                "include": ["FAIL"], "maxBytes": 4096}},
        })
        self.assertEqual(runtime["executionProfiles"]["long"]["timeoutSeconds"], 7200)
        self.assertEqual(runtime["outputProfiles"]["agent"]["everyLines"], 20)

    def test_invalid_remote_deadline_profile_filter_and_unknown_keys_fail(self):
        from sandbox.config.runtime import normalize_runtime_policy

        cases = (
            {"default": "remote"},
            {"default": "cloud"},
            {"executionProfiles": {"bad": {"timeoutSeconds": 0}}},
            {"outputProfiles": {"bad": {"mode": "shell", "command": "grep FAIL"}}},
            {"unknown": True},
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_runtime_policy(value)

    def test_declared_test_plans_are_validated_against_explicit_profiles(self):
        from sandbox.config.runtime import normalize_runtime_policy

        runtime = normalize_runtime_policy({
            "executionProfiles": {"verify": {"timeoutSeconds": 90}},
            "testPlans": {"checks": {
                "executionProfile": "verify", "maxParallel": 2,
                "steps": [{"id": "lint", "argv": ["npm", "run", "lint"], "parallelSafe": True}],
            }},
        })
        self.assertEqual(runtime["testPlans"]["checks"]["executionProfile"], "verify")
        for invalid in (
            {"testPlans": {"bad": {"steps": [{"id": "bad id", "argv": ["echo"]}]}}},
            {"testPlans": {"bad": {"executionProfile": "missing",
                                       "steps": [{"id": "ok", "argv": ["echo"]}]}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_runtime_policy(invalid)

    def test_wordpress_and_compose_facades_expose_common_runtime_policy(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sandbox.config.json").write_text(json.dumps({
                "runtime": {"default": "remote", "remote": "vps", "workspace": "unit"}
            }))
            legacy = mock.Mock(return_value={
                "tests": {"suite": "auto"},
                "runtime": {"default": "remote", "remote": "vps", "workspace": "unit"},
            })
            result = resolve_project_config(root, legacy_loader=legacy)
            self.assertEqual(result["runtime"]["remote"], "vps")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {"file": "compose.yaml", "service": "web",
                            "internal_port": 80, "health_path": "/"},
                "runtime": {"default": "remote", "remote": "vps"},
            }))
            result = resolve_project_config(root, legacy_loader=mock.Mock())
            self.assertEqual(result["runtime"]["default"], "remote")


if __name__ == "__main__":
    unittest.main()
