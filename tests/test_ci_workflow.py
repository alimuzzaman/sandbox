import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sandbox.ci.workflow import WorkflowError, preflight
from sandbox.commands.ci import _artifact_blocking_differences
from sandbox.commands import ci


_HSSB_FALSE_POSITIVE_STEPS = {
    "js:14": "pnpm run release:check",
    "quality:0": "echo 'Release gate probe intentionally fails the required quality job.'\nexit 1\n",
    "wordpress:2": "if [[ \"$WP_CLI_VERSION\" == 'latest' ]]; then\n  url='https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar'\nelse\n  url=\"https://github.com/wp-cli/wp-cli/releases/download/v${WP_CLI_VERSION}/wp-cli-${WP_CLI_VERSION}.phar\"\nfi\ncurl -fsSL \"$url\" -o \"$RUNNER_TEMP/wp\"\nchmod +x \"$RUNNER_TEMP/wp\"\nsudo mv \"$RUNNER_TEMP/wp\" /usr/local/bin/wp\nwp --info\n",
    "wordpress:4": "python3 scripts/release-candidate.py verify \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\"",
    "wordpress:6": "wp core download --path=\"$WP_ROOT\" --version=\"$WP_VERSION\" --force\nwp config create --path=\"$WP_ROOT\" --dbname=wordpress --dbuser=wordpress --dbpass=wordpress --dbhost=127.0.0.1 --skip-check\nwp core install --path=\"$WP_ROOT\" --url=http://localhost --title='Compatibility test' --admin_user=admin --admin_password=admin --admin_email=admin@example.com --skip-email\nwp plugin install \"$RUNNER_TEMP/candidate/candidate.zip\" --path=\"$WP_ROOT\" --activate\nwp core version --path=\"$WP_ROOT\"\npython3 scripts/release-candidate.py installed \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\" \"$WP_ROOT/wp-content/plugins/html-social-share-buttons\"\n",
    "distribution:6": "mkdir -p \"$RUNNER_TEMP/candidate\"\nHSSB_ARCHIVE_PATH=\"$RUNNER_TEMP/candidate/candidate.zip\" pnpm run zip\nHSSB_ARCHIVE_PATH=\"$RUNNER_TEMP/reproducibility.zip\" pnpm run zip\ncmp \"$RUNNER_TEMP/candidate/candidate.zip\" \"$RUNNER_TEMP/reproducibility.zip\"\npython3 scripts/release-candidate.py create \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\"\n",
    "archive-runtime:5": "python3 scripts/release-candidate.py verify \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\"",
    "archive-runtime:8": "curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o \"$RUNNER_TEMP/wp\"\nchmod +x \"$RUNNER_TEMP/wp\"\nsudo mv \"$RUNNER_TEMP/wp\" /usr/local/bin/wp\nwp core download --path=\"$WP_ROOT\" --force\nwp config create --path=\"$WP_ROOT\" --dbname=wordpress --dbuser=wordpress --dbpass=wordpress --dbhost=127.0.0.1 --skip-check\nwp core install --path=\"$WP_ROOT\" --url=http://127.0.0.1:8221 --title='Candidate browser test' --admin_user=admin --admin_password=admin --admin_email=admin@example.com --skip-email\nwp core version --path=\"$WP_ROOT\"\nwp plugin install \"$RUNNER_TEMP/candidate/candidate.zip\" --path=\"$WP_ROOT\" --activate\npython3 scripts/release-candidate.py installed \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\" \"$WP_ROOT/wp-content/plugins/html-social-share-buttons\"\nwp rewrite structure '/%postname%/' --path=\"$WP_ROOT\"\nwp plugin install plugin-check --version=2.1.0 --activate --path=\"$WP_ROOT\"\n",
    "archive-runtime:9": "php -S 127.0.0.1:8221 -t \"$WP_ROOT\" scripts/ci-wordpress-router.php > \"$RUNNER_TEMP/wordpress-server.log\" 2>&1 &\nserver_pid=$!\ntrap 'kill \"$server_pid\"' EXIT\nfor _ in {1..30}; do\n  if curl -fsS \"$WP_BASE_URL/wp-login.php\" > /dev/null; then break; fi\n  sleep 1\ndone\ncurl -fsS \"$WP_BASE_URL/wp-login.php\" > /dev/null\nwp plugin check html-social-share-buttons --path=\"$WP_ROOT\" --require=\"$WP_ROOT/wp-content/plugins/plugin-check/cli.php\" --format=strict-json --fields=file,line,column,type,code,message > \"$RUNNER_TEMP/plugin-check.json\"\nnode scripts/verify-plugin-check.js \"$RUNNER_TEMP/plugin-check.json\"\npnpm exec playwright test --config tests/release.playwright.config.js\npython3 scripts/release-candidate.py installed \"$RUNNER_TEMP/candidate\" \"$GITHUB_SHA\" \"$WP_ROOT/wp-content/plugins/html-social-share-buttons\"\n"
}


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
                self.assertEqual(result["catalog_version"], "3")
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
            retention = root / "retention.yml"
            retention.write_text(supported.read_text().replace(
                "if-no-files-found: error", "if-no-files-found: error\n          retention-days: 3"))
            result = preflight(root, retention.name)
            self.assertTrue(result["ok"])
            self.assertIn("sandbox.artifact-retention-ignored",
                          [item["id"] for item in result["differences"]])

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

    def test_unknown_mutation_markers_still_fail_closed(self):
        from sandbox.ci.workflow import _has_marker, _UNKNOWN_MUTATION_MARKERS
        self.assertFalse(_has_marker("docker build immutable-image", _UNKNOWN_MUTATION_MARKERS))
        self.assertTrue(_has_marker("node mutate.js", _UNKNOWN_MUTATION_MARKERS))

    def test_validation_steps_are_not_classified_as_mutations(self):
        """The nine HSSB compatibility.yml steps neutralized by substring matching."""
        from sandbox.ci.workflow import step_mutation
        for location, run in _HSSB_FALSE_POSITIVE_STEPS.items():
            with self.subTest(location=location):
                self.assertIsNone(step_mutation({"run": run}))
        for run in ("cargo package --no-check-publish",
                    "composer validate --no-check-publish",
                    "pnpm run release:check",
                    "node tests/release.playwright.config.js",
                    "echo 'git push is disabled here'",
                    "npx playwright test --config tests/release.playwright.config.js"):
            with self.subTest(run=run):
                self.assertIsNone(step_mutation({"run": run}))
        for uses in ("actions/upload-artifact@v4", "actions/checkout@v4",
                     "shivammathur/setup-php@v2"):
            with self.subTest(uses=uses):
                self.assertIsNone(step_mutation({"uses": uses}))

    def test_real_publishing_commands_and_actions_are_still_classified(self):
        from sandbox.ci.workflow import step_mutation
        cases = {
            "git push origin main": "git push",
            "git -C build push --tags": "git push",
            "cd dist && svn commit -m release": "svn commit",
            "svn ci -m 'Tagging 1.2.3'": "svn ci",
            "npm publish --access public": "npm publish",
            "pnpm -r publish --no-git-checks": "pnpm publish",
            "yarn npm publish": "yarn npm publish",
            "gh release create v1.0.0 dist/*.zip": "gh release create",
            "gh release upload v1.0.0 plugin.zip": "gh release upload",
            "twine upload dist/*": "twine upload",
            "npm run deploy": "npm run deploy",
            "pnpm run release:publish": "pnpm run release:publish",
            "yarn publish": "yarn publish",
            "make deploy": "make deploy",
            "./scripts/deploy.sh production": "./scripts/deploy.sh",
            "bash bin/publish.sh": "bash bin/publish.sh",
            "if true; then\n  sudo -E git push origin HEAD\nfi": "git push",
            "npx semantic-release": "semantic-release",
        }
        for run, expected in cases.items():
            with self.subTest(run=run):
                self.assertEqual(step_mutation({"run": run}), expected)
        for uses in ("10up/action-wordpress-plugin-deploy@stable",
                     "softprops/action-gh-release@v2",
                     "peaceiris/actions-gh-pages@v4",
                     "pypa/gh-action-pypi-publish@release/v1"):
            with self.subTest(uses=uses):
                self.assertIsNotNone(step_mutation({"uses": uses}))

    def test_runner_temp_artifact_path_blocks_with_workspace_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = root / "ci.yml"
            flow.write_text(
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/upload-artifact@v4\n"
                "        with:\n          path: ${{ runner.temp }}/candidate/\n"
                "          if-no-files-found: error\n")
            result = preflight(root, flow.name)
            self.assertIn("sandbox.artifact-runner-temp-unsupported", result["blocking"])
            self.assertNotIn("sandbox.artifact-pattern-unsupported", result["blocking"])
            message = next(item["message"] for item in result["differences"]
                           if item["id"] == "sandbox.artifact-runner-temp-unsupported")
            self.assertIn(".ci-artifacts/<name>", message)

    def test_remote_artifacts_drop_expression_paths_as_uncollectable(self):
        job = {"steps": [{"uses": "actions/upload-artifact@v4", "with": {
            "path": "${{ runner.temp }}/plugin-check.json\ntest-results/\n../escape\n"}}]}
        paths, uncollectable = ci._remote_ci_artifacts(job)
        self.assertEqual(paths, ["test-results/"])
        self.assertEqual(uncollectable, ["${{ runner.temp }}/plugin-check.json", "../escape"])
        only_temp = {"steps": [{"uses": "actions/upload-artifact@v4", "with": {
            "path": "${{ runner.temp }}/candidate/"}}]}
        self.assertEqual(ci._remote_ci_artifacts(only_temp)[0], [])

