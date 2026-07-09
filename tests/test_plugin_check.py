"""Unit tests for first-class Plugin Check support (specs/013-plugin-check/).

Stdlib `unittest` only, no docker — pure parsing/baseline-diff/report-rendering
logic (spec FR-017). Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.plugin_check as pc  # noqa: E402
from sandbox.core._plugin_check_report import render_report  # noqa: E402


# A realistic `wp plugin check --format=json` output sample: one `FILE:` line
# followed by a JSON array line, repeated per file — not a single JSON document.
SAMPLE_OUTPUT = (
    "FILE: /var/www/html/wp-content/plugins/demo/includes/foo.php\n"
    '[{"line":10,"column":5,"type":"ERROR","code":"wp_deprecated_function",'
    '"message":"str_contains() called"},'
    '{"line":20,"column":1,"type":"WARNING","code":"nonce_check",'
    '"message":"missing nonce check on read-only GET"}]\n'
    "FILE: /var/www/html/wp-content/plugins/demo/includes/bar.php\n"
    '[{"line":3,"column":2,"type":"ERROR","code":"wp_deprecated_function",'
    '"message":"str_contains() called again"}]\n'
)


class TestParseFindings(unittest.TestCase):
    def test_parses_multi_file_output(self):
        findings = pc._parse_findings(SAMPLE_OUTPUT)
        self.assertEqual(len(findings), 3)
        self.assertEqual(findings[0]["file"],
                         "/var/www/html/wp-content/plugins/demo/includes/foo.php")
        self.assertEqual(findings[0]["type"], "ERROR")
        self.assertEqual(findings[1]["type"], "WARNING")
        self.assertEqual(findings[2]["file"],
                         "/var/www/html/wp-content/plugins/demo/includes/bar.php")

    def test_ignores_stray_non_json_lines(self):
        noisy = "some docker-compose debug line\n" + SAMPLE_OUTPUT + "\ntrailing noise\n"
        findings = pc._parse_findings(noisy)
        self.assertEqual(len(findings), 3)

    def test_empty_output_yields_no_findings(self):
        self.assertEqual(pc._parse_findings(""), [])

    def test_root_converts_host_absolute_paths_to_relative(self):
        # Real bug found via live testing: sandbox bind-mounts plugin source at
        # the SAME absolute host path inside the container, so `wp plugin
        # check` reports real host-absolute paths (e.g.
        # /Users/you/project/includes/Foo.php), not container-namespaced ones.
        # Without converting to project-relative, these never match a
        # baseline keyed on relative paths -- every genuine finding looks new.
        output = (
            "FILE: /Users/dev/my-project/includes/Foo.php\n"
            '[{"line":1,"column":1,"type":"ERROR","code":"some_rule","message":"m"}]\n'
        )
        findings = pc._parse_findings(output, root="/Users/dev/my-project")
        self.assertEqual(findings[0]["file"], "includes/Foo.php")

    def test_root_none_leaves_paths_as_reported(self):
        output = (
            "FILE: /Users/dev/my-project/includes/Foo.php\n"
            '[{"line":1,"column":1,"type":"ERROR","code":"some_rule","message":"m"}]\n'
        )
        findings = pc._parse_findings(output, root=None)
        self.assertEqual(findings[0]["file"], "/Users/dev/my-project/includes/Foo.php")

    def test_root_relpath_never_raises_for_unrelated_paths(self):
        # os.path.relpath (not Path.relative_to) matches JS's path.relative --
        # it computes a relative path with ../ segments as needed rather than
        # raising, even when the reported path isn't actually under root.
        output = (
            "FILE: /somewhere/else/Foo.php\n"
            '[{"line":1,"column":1,"type":"ERROR","code":"some_rule","message":"m"}]\n'
        )
        findings = pc._parse_findings(output, root="/Users/dev/my-project")
        self.assertTrue(findings[0]["file"])  # doesn't raise; some relative path is produced


class TestCountByKey(unittest.TestCase):
    def test_counts_error_findings_by_file_and_code(self):
        findings = pc._parse_findings(SAMPLE_OUTPUT)
        counts = pc._count_by_key(findings)
        self.assertEqual(
            counts,
            {"/var/www/html/wp-content/plugins/demo/includes/foo.php::wp_deprecated_function": 1,
             "/var/www/html/wp-content/plugins/demo/includes/bar.php::wp_deprecated_function": 1})

    def test_warnings_are_excluded_from_baseline_counts(self):
        findings = [{"file": "a.php", "type": "WARNING", "code": "x", "line": 1}]
        self.assertEqual(pc._count_by_key(findings), {})

    def test_line_and_column_never_affect_the_key(self):
        # Same file+code, different line/column -> same key, count 2 (FR-007).
        findings = [
            {"file": "a.php", "type": "ERROR", "code": "x", "line": 10, "column": 1},
            {"file": "a.php", "type": "ERROR", "code": "x", "line": 999, "column": 50},
        ]
        self.assertEqual(pc._count_by_key(findings), {"a.php::x": 2})


class TestBaselineIO(unittest.TestCase):
    def test_missing_baseline_file_is_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pc._load_baseline(Path(d) / "nope.json"), {})

    def test_write_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "baseline.json"
            pc._write_baseline(path, {"a.php::x": 3, "b.php::y": 1})
            self.assertEqual(pc._load_baseline(path), {"a.php::x": 3, "b.php::y": 1})

    def test_corrupt_baseline_file_is_treated_as_empty_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "baseline.json"
            path.write_text("{not valid json")
            self.assertEqual(pc._load_baseline(path), {})


class TestDiffAgainstBaseline(unittest.TestCase):
    def test_no_baseline_means_everything_is_a_violation(self):
        current = {"a.php::x": 2}
        violations = pc._diff_against_baseline(current, {})
        self.assertEqual(violations, [{"key": "a.php::x", "current": 2, "baseline": 0, "delta": 2}])

    def test_current_matching_baseline_is_not_a_violation(self):
        current = {"a.php::x": 2}
        baseline = {"a.php::x": 2}
        self.assertEqual(pc._diff_against_baseline(current, baseline), [])

    def test_current_below_baseline_is_not_a_violation(self):
        # Fixed some findings -- fewer than baselined is fine, never gates.
        current = {"a.php::x": 1}
        baseline = {"a.php::x": 5}
        self.assertEqual(pc._diff_against_baseline(current, baseline), [])

    def test_current_above_baseline_is_a_violation_with_correct_delta(self):
        current = {"a.php::x": 5}
        baseline = {"a.php::x": 2}
        violations = pc._diff_against_baseline(current, baseline)
        self.assertEqual(violations, [{"key": "a.php::x", "current": 5, "baseline": 2, "delta": 3}])

    def test_baselined_findings_not_present_now_are_not_violations(self):
        # A key that only exists in the baseline (fixed entirely) produces no
        # violation -- only CURRENT keys are checked against their baseline.
        current = {}
        baseline = {"a.php::x": 2}
        self.assertEqual(pc._diff_against_baseline(current, baseline), [])


class TestResolvePluginCheckConfig(unittest.TestCase):
    # There is no `pluginCheck.slug` override key -- Plugin Check always
    # checks the project's OWN resolved plugin slug (self-check only,
    # matching the reference implementation, which hardcodes its own
    # plugin's name as a literal rather than reading it from ANY config
    # key). "slug" always comes from the top-level `slug` / project dir
    # name via _project_slug, same as legacy plugins:["."] self-entries.

    def test_uses_the_root_level_slug_when_present(self):
        # Real example: templately-modular-rewrite/sandbox.config.json
        # already has "slug": "templately" at the root for other reasons
        # (spec 010's plugin map) -- Plugin Check reuses that directly.
        resolved = pc._resolve_plugin_check_config(
            {"slug": "templately", "root": "/some/project", "pluginCheck": {}})
        self.assertEqual(resolved["slug"], "templately")

    def test_no_root_level_slug_falls_back_to_directory_name(self):
        resolved = pc._resolve_plugin_check_config(
            {"root": "/some/project/my-plugin-dir", "pluginCheck": {}})
        self.assertEqual(resolved["slug"], "my-plugin-dir")

    def test_missing_pluginCheck_key_entirely_still_resolves(self):
        resolved = pc._resolve_plugin_check_config(
            {"slug": "templately", "root": "/some/project"})
        self.assertEqual(resolved["slug"], "templately")

    def test_a_pluginCheck_slug_key_is_ignored_if_present(self):
        # No override key exists -- an old/misremembered config setting it
        # has zero effect, the root-level slug still wins.
        resolved = pc._resolve_plugin_check_config({
            "slug": "templately", "root": "/some/project",
            "pluginCheck": {"slug": "something-else"}})
        self.assertEqual(resolved["slug"], "templately")

    def test_invalid_slug_dies_with_an_actionable_message(self):
        # A directory name (or root slug) that doesn't look like a valid WP
        # plugin slug -- _project_slug's own validation.
        with self.assertRaises(SystemExit):
            pc._resolve_plugin_check_config(
                {"root": "/some/Not A Valid Slug!", "pluginCheck": {}})

    def test_defaults_applied_with_only_root_slug(self):
        resolved = pc._resolve_plugin_check_config(
            {"slug": "my-plugin", "root": "/some/project", "pluginCheck": {}})
        self.assertEqual(resolved, {
            "slug": "my-plugin",
            "exclude_directories": [],
            "version_file": "my-plugin.php",
            "baseline_file": "plugin-check-baseline.json",
        })

    def test_explicit_pluginCheck_overrides_are_honored(self):
        resolved = pc._resolve_plugin_check_config({
            "slug": "my-plugin", "root": "/some/project",
            "pluginCheck": {
                "excludeDirectories": ["tests", "docs"],
                "versionFile": "custom-main-file.php",
                "baselineFile": "custom-baseline.json",
            }})
        self.assertEqual(resolved["exclude_directories"], ["tests", "docs"])
        self.assertEqual(resolved["version_file"], "custom-main-file.php")
        self.assertEqual(resolved["baseline_file"], "custom-baseline.json")

    def test_falls_back_to_distignore_when_excludeDirectories_unset(self):
        # Real gap found via live testing against templately-modular-rewrite:
        # with no excludeDirectories configured, the tool scanned the WHOLE
        # repo (tests/, .claude/, .specify/, scripts/, etc.) producing mostly
        # noise findings that never ship. A project's own .distignore (the
        # WordPress.org SVN-deploy ignore file) already lists exactly this.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".distignore").write_text(
                "# comment\ntests/\n.claude/\nscripts\n\ncomposer.json\n")
            resolved = pc._resolve_plugin_check_config(
                {"slug": "my-plugin", "root": str(root), "pluginCheck": {}})
            self.assertEqual(resolved["exclude_directories"],
                             ["tests", ".claude", "scripts", "composer.json"])

    def test_explicit_excludeDirectories_takes_priority_over_distignore(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".distignore").write_text("tests/\n.claude/\n")
            resolved = pc._resolve_plugin_check_config({
                "slug": "my-plugin", "root": str(root),
                "pluginCheck": {"excludeDirectories": ["only-this"]}})
            self.assertEqual(resolved["exclude_directories"], ["only-this"])

    def test_no_distignore_and_no_excludeDirectories_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            resolved = pc._resolve_plugin_check_config(
                {"slug": "my-plugin", "root": d, "pluginCheck": {}})
            self.assertEqual(resolved["exclude_directories"], [])


class TestReadDistignoreDirectories(unittest.TestCase):
    def test_missing_distignore_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pc._read_distignore_directories(Path(d)), [])

    def test_parses_real_shaped_distignore(self):
        # Modeled on a real .distignore's shape: comments, blank lines,
        # trailing-slash directories, bare directories, files, and globs.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".distignore").write_text(
                "# A set of files you probably don't want in your distribution\n"
                ".git\n"
                "node_modules\n"
                "*.sql\n"
                "\n"
                "docs/\n"
                "tests/\n"
                "modules/developer-tools\n"
                "# a comment line\n"
                "dist-types/\n"
            )
            result = pc._read_distignore_directories(root)
            self.assertEqual(result, [
                ".git", "node_modules", "*.sql", "docs", "tests",
                "modules/developer-tools", "dist-types",
            ])

    def test_dedupes_repeated_entries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".distignore").write_text("tests/\ntests\n.claude/\n.claude\n")
            result = pc._read_distignore_directories(root)
            self.assertEqual(result, ["tests", ".claude"])

    def test_blank_and_comment_only_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".distignore").write_text("# just a comment\n\n\n")
            self.assertEqual(pc._read_distignore_directories(root), [])


class TestReadVersionHeader(unittest.TestCase):
    def test_reads_version_from_plugin_header(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "my-plugin.php"
            path.write_text("<?php\n/**\n * Plugin Name: My Plugin\n"
                            " * Version: 1.4.2\n */\n")
            self.assertEqual(pc._read_version_header(path), "1.4.2")

    def test_missing_file_returns_unknown(self):
        self.assertEqual(pc._read_version_header(Path("/nonexistent/plugin.php")), "unknown")

    def test_file_without_version_header_returns_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "my-plugin.php"
            path.write_text("<?php\n// no header here\n")
            self.assertEqual(pc._read_version_header(path), "unknown")


class TestCheckerVersion(unittest.TestCase):
    def test_parses_version_from_pinned_zip_url(self):
        pconf = {"plugins_resolved": {"plugin-check": {
            "source": "https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip"}}}
        self.assertEqual(pc._checker_version(pconf), "2.0.0")

    def test_missing_entry_returns_unknown(self):
        self.assertEqual(pc._checker_version({"plugins_resolved": {}}), "unknown")


class TestRunWpPluginCheck(unittest.TestCase):
    def test_no_captured_output_is_an_infrastructure_failure(self):
        fake_result = type("R", (), {"stdout": "", "stderr": "connection refused"})()
        with patch.object(pc, "wpcli", return_value=fake_result):
            with self.assertRaises(SystemExit):
                pc._run_wp_plugin_check("some-instance", "my-plugin", [])

    def test_captured_output_is_returned_even_with_nonzero_style_findings(self):
        fake_result = type("R", (), {"stdout": SAMPLE_OUTPUT, "stderr": ""})()
        with patch.object(pc, "wpcli", return_value=fake_result) as mock_wpcli:
            out = pc._run_wp_plugin_check("some-instance", "my-plugin", ["tests", "docs"])
            self.assertEqual(out, SAMPLE_OUTPUT)
            call_args = mock_wpcli.call_args
            passed_args = call_args[0][0]
            self.assertIn("--exclude-directories=tests,docs", passed_args)
            self.assertEqual(call_args[1].get("check"), False)


class TestRenderReport(unittest.TestCase):
    BASE_META = {
        "plugin_slug": "my-test-plugin", "plugin_version": "1.2.3",
        "checker_version": "2.0.0", "wp_version": "6.8", "php_version": "8.3",
        "exclude_directories": [], "baseline_total": 1, "new_count": 0,
        "baseline_file": "plugin-check-baseline.json",
    }

    def test_report_contains_the_checked_plugin_slug(self):
        findings = [{"file": "a.php", "type": "ERROR", "code": "x", "line": 1,
                    "column": 1, "message": "msg"}]
        html = render_report(findings, self.BASE_META)
        self.assertIn("my-test-plugin", html)

    def test_report_never_hardcodes_a_specific_plugin_name(self):
        # Regression guard for spec FR-013 -- the reference implementation this
        # was ported from hardcoded "Templately" in its masthead/title.
        findings = []
        html = render_report(findings, self.BASE_META)
        self.assertNotIn("Templately", html)

    def test_two_different_projects_produce_differently_branded_reports(self):
        findings = [{"file": "a.php", "type": "ERROR", "code": "x", "line": 1,
                    "column": 1, "message": "msg"}]
        meta_a = {**self.BASE_META, "plugin_slug": "plugin-alpha"}
        meta_b = {**self.BASE_META, "plugin_slug": "plugin-beta"}
        html_a = render_report(findings, meta_a)
        html_b = render_report(findings, meta_b)
        self.assertIn("plugin-alpha", html_a)
        self.assertNotIn("plugin-beta", html_a)
        self.assertIn("plugin-beta", html_b)
        self.assertNotIn("plugin-alpha", html_b)

    def test_warnings_are_present_in_report_even_though_never_gating(self):
        findings = [{"file": "a.php", "type": "WARNING", "code": "nonce_check",
                    "line": 1, "column": 1, "message": "missing nonce check"}]
        html = render_report(findings, {**self.BASE_META, "new_count": 0})
        self.assertIn("nonce_check", html)
        self.assertIn("PASS", html)  # gate still passes; warnings never gate

    def test_gate_fail_state_reflected_in_report(self):
        html = render_report([], {**self.BASE_META, "new_count": 3})
        self.assertIn("FAIL", html)

    def test_exclude_directories_reflected_in_footer_not_hardcoded(self):
        html = render_report([], {**self.BASE_META, "exclude_directories": ["vendor", "node_modules"]})
        self.assertIn("vendor", html)
        self.assertIn("node_modules", html)

    def test_report_is_valid_enough_html_with_no_external_requests(self):
        html = render_report([], self.BASE_META)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<script src=", html)  # no external script tags
        self.assertNotIn('<link ', html)  # no external stylesheet links


if __name__ == "__main__":
    unittest.main()
