import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sandbox.ci.workflow import WorkflowError, preflight
from sandbox.commands.ci import _artifact_blocking_differences
from sandbox.commands import ci


class WorkflowTests(unittest.TestCase):
    def test_preflight_is_contained_and_blocks_unaccepted_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text("jobs:\n  test:\n    runs-on: ubuntu-latest\n    timeout-minutes: 2\n    strategy:\n      matrix:\n        node: [20, 22]\n")
            result = preflight(root, "ci.yml")
            self.assertFalse(result["ok"]); self.assertEqual(result["graph"]["matrix_cells"], 2)
            self.assertTrue(preflight(root, "ci.yml", accepted_differences=["act.job-timeout-ignored"])["ok"])
            with self.assertRaises(WorkflowError): preflight(root, "../outside.yml")

    def test_malformed_yaml_and_unknown_dependency_fail_before_a_run_is_planned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            malformed = root / "malformed.yml"
            malformed.write_text("jobs: [\n")
            with self.assertRaises(WorkflowError):
                preflight(root, malformed.name)
            unknown_need = root / "unknown-need.yml"
            unknown_need.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    needs: missing\n    steps: []\n")
            with self.assertRaisesRegex(WorkflowError, "needs unknown job"):
                preflight(root, unknown_need.name)

    def test_upload_artifact_glob_and_expression_paths_block_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, path_value in (("glob", "reports/**/*.xml"),
                                     ("expression", "reports/${{ matrix.node }}.xml")):
                flow = root / f"{name}.yml"
                flow.write_text(
                    "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - uses: actions/upload-artifact@v4\n"
                    f"        with:\n          path: {path_value}\n          if-no-files-found: error\n"
                )
                result = preflight(root, flow.name)
                self.assertFalse(result["ok"])
                self.assertEqual(result["catalog_version"], "2")
                self.assertIn("sandbox.artifact-pattern-unsupported", result["blocking"])

    def test_upload_artifact_requires_error_missing_semantics_and_rejects_unsupported_options(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing.yml"
            missing.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n        with:\n          path: reports\n"
            )
            result = preflight(root, missing.name)
            self.assertIn("sandbox.artifact-missing-semantics", result["blocking"])
            self.assertEqual(_artifact_blocking_differences(result),
                             ["sandbox.artifact-missing-semantics"])
            accepted = preflight(root, missing.name,
                accepted_differences=["sandbox.artifact-missing-semantics"])
            self.assertEqual(_artifact_blocking_differences(accepted), [])
            supported = root / "supported.yml"
            supported.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n"
                "        with:\n          path: reports\n          if-no-files-found: error\n"
            )
            self.assertTrue(preflight(root, supported.name)["ok"])
            options = root / "options.yml"
            options.write_text(supported.read_text().replace(
                "if-no-files-found: error", "if-no-files-found: error\n          compression-level: 9"))
            result = preflight(root, options.name)
            self.assertIn("sandbox.artifact-options-unsupported", result["blocking"])

    def test_selected_job_ignores_unrelated_artifact_differences_but_includes_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text(
                "jobs:\n"
                "  release:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n"
                "        with:\n          path: 'dist/*.zip'\n"
                "  unit:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo unit\n"
                "  dependent:\n    runs-on: ubuntu-latest\n    needs: release\n"
                "    steps:\n      - run: echo dependent\n"
            )
            unit = preflight(root, flow.name, selected_jobs=["unit"])
            self.assertTrue(unit["ok"])
            self.assertNotIn("sandbox.artifact-pattern-unsupported", unit["blocking"])
            dependent = preflight(root, flow.name, selected_jobs=["dependent"])
            self.assertFalse(dependent["ok"])
            self.assertIn("sandbox.artifact-pattern-unsupported", dependent["blocking"])

    def test_safe_mode_neutralizes_deployment_and_records_difference_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text("jobs:\n  release:\n    steps:\n      - run: ./deploy.sh\n")
            result = preflight(root, "ci.yml", safe_mode=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["safe_mode_actions"][0]["action"], "neutralized")
            self.assertEqual(result["differences"][-1]["id"], "safe-mode:release:0")
            self.assertNotIn("safe-mode:release:0", result["blocking"])

    def test_safe_mode_blocks_unknown_external_mutation_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text(
                "jobs:\n  mutate:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: example/external-mutator@v1\n")
            result = preflight(root, flow.name, safe_mode=True)
            self.assertFalse(result["ok"])
            self.assertIn("safe-mode-unknown-mutation:mutate:0", result["blocking"])
            self.assertEqual(result["safe_mode_actions"], [{
                "id": "safe-mode-unknown-mutation:mutate:0",
                "location": "jobs.mutate.steps[0]", "action": "blocked",
            }])

    def test_environment_ci_secret_requires_explicit_allowlist(self):
        with patch.dict("os.environ", {"SANDBOX_CI_SECRET_TOKEN": "environment-value"}, clear=False):
            self.assertIsNone(ci._resolve_secret("TOKEN", {"ci_secrets": {}}))
            self.assertEqual(ci._resolve_secret("TOKEN", {
                "ci_secrets": {}, "ci_secret_allowlist": ["TOKEN"],
            }), "environment-value")
        self.assertEqual(ci._resolve_secret("TOKEN", {
            "ci_secrets": {"TOKEN": "configured-value"},
        }), "configured-value")
