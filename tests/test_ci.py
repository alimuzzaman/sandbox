"""Unit tests for the CI workflow interpreter (docs/ci-e2e-runner-spec.md §3).

Stdlib `unittest` only, no docker — pure parsing/classification/interpolation
logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.ci as ci  # noqa: E402


class TestExpandMatrix(unittest.TestCase):
    def test_include_only_shape_is_a_literal_cell_list(self):
        # Query Monitor's real shape: matrix.include with no other axes.
        strategy = {"matrix": {"include": [
            {"wp": "6.8", "php": "8.4"}, {"wp": "6.2", "php": "7.4"},
        ]}}
        cells = ci._expand_matrix(strategy)
        self.assertEqual(cells, [{"wp": "6.8", "php": "8.4"},
                                 {"wp": "6.2", "php": "7.4"}])

    def test_cartesian_axes(self):
        strategy = {"matrix": {"os": ["a", "b"], "node": [10, 12]}}
        cells = ci._expand_matrix(strategy)
        self.assertEqual(len(cells), 4)
        self.assertIn({"os": "a", "node": 10}, cells)
        self.assertIn({"os": "b", "node": 12}, cells)

    def test_exclude_drops_matching_cells(self):
        strategy = {"matrix": {"os": ["a", "b"], "node": [10, 12]},
                   }
        strategy["matrix"]["exclude"] = [{"os": "a", "node": 12}]
        cells = ci._expand_matrix(strategy)
        self.assertEqual(len(cells), 3)
        self.assertNotIn({"os": "a", "node": 12}, cells)

    def test_include_merges_extra_keys_into_matching_cells(self):
        # GitHub semantics: an include entry matching existing axis keys adds
        # its OTHER keys onto that cell; a non-matching entry becomes new.
        strategy = {"matrix": {"animal": ["cat", "dog"]}}
        strategy["matrix"]["include"] = [{"animal": "cat", "color": "pink"},
                                         {"animal": "bird", "color": "blue"}]
        cells = ci._expand_matrix(strategy)
        by_animal = {c["animal"]: c for c in cells}
        self.assertEqual(by_animal["cat"]["color"], "pink")
        self.assertNotIn("color", by_animal["dog"])
        self.assertEqual(by_animal["bird"]["color"], "blue")

    def test_no_matrix_is_one_cell(self):
        self.assertEqual(ci._expand_matrix(None), [{}])
        self.assertEqual(ci._expand_matrix({}), [{}])


class TestClassifyStep(unittest.TestCase):
    def test_checkout_is_known_noop(self):
        s = ci._classify_step({"uses": "actions/checkout@v4"})
        self.assertEqual(s["kind"], "known")

    def test_10up_deploy_is_deploy_class(self):
        s = ci._classify_step({"name": "Deploy",
                               "uses": "10up/action-wordpress-plugin-deploy@stable",
                               "env": {"SVN_PASSWORD": "${{ secrets.SVN_PASSWORD }}"}})
        self.assertEqual(s["kind"], "deploy")
        self.assertIn("SVN_PASSWORD", s["secrets_needed"])

    def test_unrecognized_action_is_unknown_never_guessed(self):
        s = ci._classify_step({"uses": "anthropics/claude-code-action@beta"})
        self.assertEqual(s["kind"], "unknown")

    def test_run_step_is_run_kind(self):
        s = ci._classify_step({"run": "composer install"})
        self.assertEqual(s["kind"], "run")

    def test_secrets_extracted_from_run_script(self):
        s = ci._classify_step({"run": 'echo "${{ secrets.MUKUL_PAT }}"'})
        self.assertEqual(s["secrets_needed"], ["MUKUL_PAT"])


class TestMergeEnvPrecedence(unittest.TestCase):
    def test_step_overrides_job_overrides_workflow(self):
        # docs.github.com/actions/learn-github-actions/variables
        merged = ci._merge_env({"X": "workflow"}, {"X": "job", "Y": "job"},
                               {"X": "step"})
        self.assertEqual(merged["X"], "step")
        self.assertEqual(merged["Y"], "job")

    def test_none_layers_are_skipped(self):
        merged = ci._merge_env(None, {"A": "1"}, None)
        self.assertEqual(merged, {"A": "1"})


class TestInterpolate(unittest.TestCase):
    def test_matrix_expression(self):
        out = ci._interpolate("wp ${{ matrix.wp }}", {"wp": "6.8"}, {})
        self.assertEqual(out, "wp 6.8")

    def test_env_expression_reads_declared_env_not_host_env(self):
        out = ci._interpolate("${{ env.FOO }}", {}, {}, declared_env={"FOO": "bar"})
        self.assertEqual(out, "bar")

    def test_unresolved_secret_raises_keyerror(self):
        with self.assertRaises(KeyError):
            ci._interpolate("${{ secrets.MISSING }}", {}, {})

    def test_resolved_secret_substitutes(self):
        out = ci._interpolate("${{ secrets.X }}", {}, {"X": "shh"})
        self.assertEqual(out, "shh")


class TestResolveCellVersions(unittest.TestCase):
    def test_matrix_keys_used_when_no_setup_php_step(self):
        php, wp = ci._resolve_cell_versions({"php": "8.1", "wp": "6.4"}, [])
        self.assertEqual((php, wp), ("8.1", "6.4"))

    def test_setup_php_with_php_version_overrides_matrix_key(self):
        # A project may reference the version only inside setup-php's `with:`
        # rather than naming its matrix axis literally `php` — that's the
        # more explicit "what will actually run" signal, so it wins.
        steps = [{"kind": "known", "uses": "shivammathur/setup-php@v2",
                 "with": {"php-version": "${{ matrix.phpVersion }}"}}]
        php, wp = ci._resolve_cell_versions({"phpVersion": "8.3"}, steps)
        self.assertEqual(php, "8.3")

    def test_no_version_info_returns_none(self):
        php, wp = ci._resolve_cell_versions({"variant": "alpha"}, [])
        self.assertIsNone(php)
        self.assertIsNone(wp)


class TestGuardDangerousCommands(unittest.TestCase):
    def test_git_push_is_blocked_by_default(self):
        script, warnings = ci._guard_dangerous_commands("git push origin main",
                                                         allow_deploy=False)
        self.assertIn("blocked", warnings[0])
        self.assertIn("# [sandbox-ci: blocked", script)

    def test_allow_deploy_lets_it_through(self):
        script, warnings = ci._guard_dangerous_commands("git push origin main",
                                                         allow_deploy=True)
        self.assertEqual(warnings, [])
        self.assertEqual(script, "git push origin main")

    def test_safe_commands_are_untouched(self):
        script, warnings = ci._guard_dangerous_commands("npm run build",
                                                         allow_deploy=False)
        self.assertEqual(warnings, [])
        self.assertEqual(script, "npm run build")


class TestIsDeployClass(unittest.TestCase):
    def test_known_10up_deploy_actions(self):
        self.assertTrue(ci._is_deploy_class("10up/action-wordpress-plugin-deploy@stable"))
        self.assertTrue(ci._is_deploy_class("10up/action-wordpress-plugin-asset-update@stable"))

    def test_keyword_heuristic_catches_unknown_deploy_actions(self):
        self.assertTrue(ci._is_deploy_class("some-org/publish-to-registry@v1"))
        self.assertTrue(ci._is_deploy_class("peaceiris/actions-gh-pages@v3"))
        self.assertTrue(ci._is_deploy_class("docker/build-push-action@v5"))

    def test_ordinary_actions_are_not_deploy_class(self):
        self.assertFalse(ci._is_deploy_class("actions/checkout@v4"))
        self.assertFalse(ci._is_deploy_class("actions/upload-artifact@v4"))
        self.assertFalse(ci._is_deploy_class("anthropics/claude-code-action@beta"))
        self.assertFalse(ci._is_deploy_class("shivammathur/setup-php@v2"))

    def test_upload_artifact_uses_sandbox_collection(self):
        self.assertTrue(ci._is_upload_artifact("actions/upload-artifact@v4"))
        self.assertFalse(ci._is_upload_artifact("actions/download-artifact@v4"))


class TestNeutralizeWorkflowForSafety(unittest.TestCase):
    def _workflow(self):
        return {
            "name": "Deploy",
            "jobs": {"build": {"steps": [
                {"name": "Checkout", "uses": "actions/checkout@v4"},
                {"name": "Build", "run": "npm run build"},
                {"name": "Deploy", "uses": "10up/action-wordpress-plugin-deploy@stable",
                 "env": {"SVN_PASSWORD": "${{ secrets.SVN_PASSWORD }}"}},
                {"name": "Push tag", "run": "git push origin main"},
            ]}},
        }

    def test_deploy_step_neutralized_by_default(self):
        patched, notes = ci._neutralize_workflow_for_safety(self._workflow(), allow_deploy=False)
        deploy_step = patched["jobs"]["build"]["steps"][2]
        self.assertNotIn("uses", deploy_step)
        self.assertIn("run", deploy_step)
        self.assertIn("skipped", deploy_step["run"])
        self.assertTrue(any("deploy-class" in n for n in notes))

    def test_allow_deploy_leaves_deploy_step_untouched(self):
        patched, notes = ci._neutralize_workflow_for_safety(self._workflow(), allow_deploy=True)
        deploy_step = patched["jobs"]["build"]["steps"][2]
        self.assertEqual(deploy_step["uses"], "10up/action-wordpress-plugin-deploy@stable")
        self.assertEqual(notes, [])

    def test_dangerous_run_command_neutralized_by_default(self):
        patched, notes = ci._neutralize_workflow_for_safety(self._workflow(), allow_deploy=False)
        push_step = patched["jobs"]["build"]["steps"][3]
        self.assertIn("blocked", push_step["run"])
        self.assertTrue(any("Push tag" in n for n in notes))

    def test_safe_steps_are_untouched(self):
        patched, _ = ci._neutralize_workflow_for_safety(self._workflow(), allow_deploy=False)
        self.assertEqual(patched["jobs"]["build"]["steps"][0]["uses"], "actions/checkout@v4")
        self.assertEqual(patched["jobs"]["build"]["steps"][1]["run"], "npm run build")

    def test_upload_artifact_is_replaced_with_local_collection_marker(self):
        workflow = {"jobs": {"build": {"steps": [
            {"uses": "actions/upload-artifact@v4", "with": {"path": "report.txt"}},
        ]}}}
        patched, notes = ci._neutralize_workflow_for_safety(workflow, allow_deploy=False)
        step = patched["jobs"]["build"]["steps"][0]
        self.assertNotIn("uses", step)
        self.assertIn("collected after job", step["run"])
        self.assertTrue(any("Sandbox job-artifact collection" in note for note in notes))

    def test_original_workflow_dict_is_not_mutated(self):
        original = self._workflow()
        ci._neutralize_workflow_for_safety(original, allow_deploy=False)
        self.assertEqual(original["jobs"]["build"]["steps"][2]["uses"],
                         "10up/action-wordpress-plugin-deploy@stable")


class TestRuntimeSecrets(unittest.TestCase):
    def test_skipped_deploy_secrets_do_not_block_safe_run(self):
        jobs = [{"steps": [
            {"uses": "10up/action-wordpress-plugin-deploy@stable",
             "secrets_needed": ["SVN_PASSWORD"]},
            {"run": "npm test", "secrets_needed": []},
        ]}]
        self.assertEqual(ci._runtime_secrets_needed(jobs, allow_deploy=False), [])

    def test_deploy_secrets_are_required_when_allowed(self):
        jobs = [{"steps": [{
            "uses": "10up/action-wordpress-plugin-deploy@stable",
            "secrets_needed": ["SVN_PASSWORD"],
        }]}]
        self.assertEqual(ci._runtime_secrets_needed(jobs, allow_deploy=True),
                         ["SVN_PASSWORD"])


class TestWriteTempFiles(unittest.TestCase):
    def test_write_patched_workflow_roundtrips(self):
        import yaml
        path = ci._write_patched_workflow({"name": "x", "jobs": {"a": {"steps": []}}})
        try:
            data = yaml.safe_load(path.read_text())
            self.assertEqual(data["name"], "x")
        finally:
            path.unlink()

    def test_write_secrets_file_shape_and_perms(self):
        import stat
        path = ci._write_secrets_file({"FOO": "bar", "BAZ": "qux"})
        try:
            lines = path.read_text().splitlines()
            self.assertIn("FOO=bar", lines)
            self.assertIn("BAZ=qux", lines)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
        finally:
            path.unlink()


class TestEventMatches(unittest.TestCase):
    def test_string_trigger(self):
        self.assertTrue(ci._event_matches("push", "push"))
        self.assertFalse(ci._event_matches("push", "pull_request"))

    def test_list_trigger(self):
        self.assertTrue(ci._event_matches(["push", "pull_request"], "pull_request"))
        self.assertFalse(ci._event_matches(["push"], "workflow_dispatch"))

    def test_dict_trigger(self):
        self.assertTrue(ci._event_matches({"push": {"branches": ["main"]},
                                          "pull_request": None}, "push"))
        self.assertFalse(ci._event_matches({"push": None}, "release"))

    def test_none_trigger(self):
        self.assertFalse(ci._event_matches(None, "push"))


_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,20}$")


class TestCellSlugAndLabel(unittest.TestCase):
    def test_slug_is_stable_across_runs(self):
        # Same cell -> same slug regardless of run_id.
        cell = {"wp": "6.8", "php": "8.4"}
        self.assertEqual(ci._cell_slug(cell), ci._cell_slug(dict(cell)))

    def test_empty_cell_slug_is_none(self):
        self.assertIsNone(ci._cell_slug({}))

    def test_label_is_a_valid_instance_label(self):
        label = ci._cell_label("ci", "abcd", "test", {"php": "8.3"})
        self.assertRegex(label, _LABEL_RE)

    def test_label_stable_for_same_job_and_cell(self):
        # Same (run_id, job_id, cell) -> same label, every time (not random).
        a = ci._cell_label("ci", "abcd", "test", {"php": "8.3"})
        b = ci._cell_label("ci", "abcd", "test", {"php": "8.3"})
        self.assertEqual(a, b)

    def test_different_jobs_same_empty_cell_never_collide(self):
        # Real bug found live: a workflow with two matrix-less jobs (e.g. a
        # reusable-workflow caller job + a plain job) both produce ONE cell
        # with an empty {} matrix. The old _cell_label(run_id, cell) — no
        # job_id — handed both jobs the IDENTICAL label, so two concurrent
        # ensure_instance(..., create=True) calls raced the same instance
        # name (one thread's fresh wp core install stomped the other's).
        label_a = ci._cell_label("ci", "abcd", "call-it", {})
        label_b = ci._cell_label("ci", "abcd", "direct", {})
        self.assertNotEqual(label_a, label_b)
        self.assertRegex(label_a, _LABEL_RE)
        self.assertRegex(label_b, _LABEL_RE)

    def test_different_cells_same_job_never_collide(self):
        label_a = ci._cell_label("ci", "abcd", "test", {"php": "8.3"})
        label_b = ci._cell_label("ci", "abcd", "test", {"php": "8.4"})
        self.assertNotEqual(label_a, label_b)

    def test_long_job_id_still_produces_valid_label(self):
        # Labels are capped at 21 chars (^[a-z0-9][a-z0-9_-]{0,20}$) — a
        # realistic long job id must not overflow that.
        label = ci._cell_label("ci", "abcd", "integration-tests-full-matrix",
                               {"php": "8.3", "wp": "6.8"})
        self.assertRegex(label, _LABEL_RE)
        self.assertLessEqual(len(label), 21)


if __name__ == "__main__":
    unittest.main()
