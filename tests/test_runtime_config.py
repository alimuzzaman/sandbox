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
        self.assertEqual(runtime["executionProfiles"]["ci"]["cleanup"], "ephemeral")
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

    def test_execution_policy_precedence_preserves_explicit_false_and_builtin_override(self):
        from sandbox.config.runtime import resolve_execution_policy

        runtime = {
            "executionProfile": "exec",
            "executionProfiles": {
                "exec": {"timeoutSeconds": 111, "stallSeconds": 11,
                         "cancelGraceSeconds": 12, "cancelOnStall": True,
                         "cleanup": "always"},
                "custom": {"timeoutSeconds": 222, "stallSeconds": 22,
                           "cancelGraceSeconds": 23, "cancelOnStall": True,
                           "cleanup": "ephemeral"},
            },
            "workspaces": {"qa": {"executionProfile": "custom"}},
        }
        workspace = resolve_execution_policy(runtime, workspace="qa")
        explicit = resolve_execution_policy(
            runtime, workspace="qa", execution_profile="exec", timeout_seconds=45,
            cancel_on_stall=False,
        )

        self.assertEqual(workspace.deadline_seconds, 222)
        self.assertEqual(workspace.cancel_grace_seconds, 23)
        self.assertEqual(workspace.cleanup_policy, "ephemeral")
        self.assertEqual(workspace.provenance["execution_profile"], "workspace")
        self.assertEqual((explicit.deadline_seconds, explicit.cancel_on_stall), (45, False))
        self.assertEqual(explicit.provenance["execution_profile"], "explicit")
        self.assertEqual(explicit.provenance["cancel_on_stall"], "explicit")

    def test_execution_policy_rejects_zero_before_submission(self):
        from sandbox.config.runtime import resolve_execution_policy

        for key in ("timeout_seconds", "stall_seconds", "cancel_grace_seconds"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_execution_policy(**{key: 0})

    def test_operation_fallback_wins_when_project_profile_is_omitted(self):
        from sandbox.config.runtime import normalize_runtime_policy, resolve_execution_policy

        omitted = resolve_execution_policy(
            normalize_runtime_policy({}), fallback_profile="unit")
        declared = resolve_execution_policy(
            normalize_runtime_policy({"executionProfile": "exec"}), fallback_profile="unit")

        self.assertEqual((omitted.execution_profile, omitted.provenance["execution_profile"]),
                         ("unit", "operation"))
        self.assertEqual((declared.execution_profile, declared.provenance["execution_profile"]),
                         ("exec", "project"))

    def test_execution_profile_declaration_marker_is_not_raw_configuration(self):
        from sandbox.config.runtime import normalize_runtime_policy, resolve_execution_policy

        with self.assertRaises(ValueError):
            normalize_runtime_policy({"_executionProfileDeclared": False})
        with self.assertRaises(ValueError):
            resolve_execution_policy(
                {"executionProfile": "exec", "_executionProfileDeclared": False},
                fallback_profile="unit")

    def test_normalized_execution_profile_declaration_survives_reentry(self):
        from sandbox.config.runtime import normalize_runtime_policy, resolve_execution_policy

        omitted = normalize_runtime_policy(normalize_runtime_policy({}))
        declared = normalize_runtime_policy(normalize_runtime_policy({"executionProfile": "exec"}))

        self.assertEqual(resolve_execution_policy(omitted, fallback_profile="unit").provenance[
                         "execution_profile"], "operation")
        self.assertEqual(resolve_execution_policy(declared, fallback_profile="unit").provenance[
                         "execution_profile"], "project")


if __name__ == "__main__":
    unittest.main()
