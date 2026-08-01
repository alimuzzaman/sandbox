"""End-to-end CLI tests for the per-project resolution gate (spec 001).

These run the real `sb` entry as a subprocess (no Docker — the gate + registry
read happen before any container work), so they exercise the actual bootstrap,
package import, registry dispatch, and the no-`main` resolution behavior.
"""
import os
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "sb"


def run_sb(*args, cwd="/tmp"):
    return subprocess.run(
        [str(SB), *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "SANDBOX_INSTANCE": ""}, timeout=90)


class TestResolutionGate(unittest.TestCase):
    def test_wordpress_only_legacy_commands_have_capability_gates(self):
        import sandbox.cli as cli
        self.assertEqual(cli.CLI_CAPABILITIES["wp"], "wordpress.cli")
        self.assertEqual(cli.CLI_CAPABILITIES["snapshot"], "wordpress.snapshot")
        self.assertEqual(cli.CLI_CAPABILITIES["shell"], "wordpress.exec")
        self.assertNotIn("up", cli.CLI_CAPABILITIES)
        self.assertNotIn("open", cli.CLI_CAPABILITIES)

    def test_generic_init_types_are_parser_visible(self):
        r = run_sb("init", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("astro", r.stdout)
        self.assertIn("compose", r.stdout)

    def test_final_public_command_inventory_matches_the_owned_manifest(self):
        from sandbox.commands.manifest import load_builtin_commands, validate_builtin_command_coverage
        from sandbox.registry import COMMANDS, COMMAND_SPECS

        load_builtin_commands()
        self.assertEqual(len(COMMANDS), 86)
        self.assertEqual(tuple(sorted(COMMANDS)), tuple(sorted(COMMAND_SPECS.names())))
        self.assertEqual(validate_builtin_command_coverage(), ())

    def test_instance_scoped_command_errors_outside_project(self):
        # `status` is instance-scoped; from a non-registered dir it must abort
        # with guidance and a non-zero exit — never silently target `main`.
        r = run_sb("status")
        self.assertNotEqual(r.returncode, 0)
        out = (r.stderr + r.stdout).lower()
        self.assertIn("no sandbox instance", out)
        self.assertNotIn("instance: main", out)

    def test_registry_wide_command_runs_anywhere(self):
        # `instances` is registry-wide → works from any directory.
        r = run_sb("instances")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unknown_instance_is_rejected(self):
        r = run_sb("status", "--instance", "definitely-not-a-real-instance")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown instance", (r.stderr + r.stdout).lower())

    def test_help_lists_selftest(self):
        r = run_sb("--help")
        self.assertIn("selftest", r.stdout + r.stderr)

    def test_cli_first_runtime_commands_are_visible(self):
        r = run_sb("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exec", r.stdout)
        self.assertIn("guide", r.stdout)

    def test_guide_is_available_without_an_instance(self):
        r = run_sb("guide", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        guide = json.loads(r.stdout)
        self.assertEqual(guide["mode"], "cli-first")
        self.assertEqual(guide["project_kind"], "compose")
        self.assertIn("sandbox-cli", guide["skill"])

    def test_exec_requires_an_instance_outside_a_project(self):
        r = run_sb("exec", "--", "echo", "hello")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no sandbox instance", r.stderr + r.stdout)

    def test_test_command_lists_explicit_modes(self):
        r = run_sb("test", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto", r.stdout)
        self.assertIn("unit", r.stdout)
        self.assertIn("integration", r.stdout)

    def test_help_lists_hermes_control_plane(self):
        r = run_sb("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hermes", r.stdout + r.stderr)

    def test_hermes_requires_explicit_remote(self):
        r = run_sb("hermes", "status")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--remote", r.stderr + r.stdout)

    def test_hermes_v2_actions_are_listed_in_help(self):
        r = run_sb("hermes", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backup", r.stdout)
        self.assertIn("update", r.stdout)
        self.assertIn("policy", r.stdout)
        self.assertIn("acceptance", r.stdout)
        self.assertIn("--confirm", r.stdout)
        self.assertIn("--force-replace", r.stdout)
        self.assertIn("worktree", r.stdout)

    def test_scheduler_mutations_fail_closed_before_remote_lookup(self):
        remote = "missing-scheduler-remote"
        for command, expected in (
            (("cron", "reconcile", "--force-replace"), "unknown_remote"),
            (("cron", "verify", "deadbeef1234"), "confirmation_required"),
            (("repo", "sync", "--repo", "sandbox"), "confirmation_required"),
        ):
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads(r.stdout)["error"]["code"], expected)

    def test_hermes_dashboard_options_are_listed_in_help(self):
        r = run_sb("hermes", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--port", r.stdout)
        self.assertIn("--fqdn", r.stdout)
        self.assertIn("--plan", r.stdout)
        self.assertNotIn("--insecure", r.stdout)
        self.assertIn("--basic-auth-secret", r.stdout)

    def test_dashboard_public_subcommands_are_parser_safe(self):
        remote = "missing-public-dashboard-remote"
        for command, expected in (
            (("dashboard", "exposure-status"), "unknown_remote"),
            (("dashboard", "expose", "--fqdn", "other.asb.bd", "--plan"), "unknown_remote"),
            (("dashboard", "basic-auth", "set"), "unknown_remote"),
        ):
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads(r.stdout)["error"]["code"], expected)

    def test_dashboard_insecure_option_is_not_accepted_by_the_parser(self):
        r = run_sb("hermes", "dashboard", "status", "--remote", "test", "--insecure")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unrecognized arguments: --insecure", r.stderr)

    def test_dashboard_refuses_before_v2_without_remote_mutation(self):
        r = run_sb("hermes", "dashboard", "install", "--remote", "missing-dashboard-remote", "--json")
        self.assertNotEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["error"]["code"], "unknown_remote")

    def test_hermes_protected_v2_actions_refuse_before_remote_lookup(self):
        remote = "missing-remote-for-confirmation-test"
        cases = [
            ("backup", "restore", "--backup-id", "20260711T000000Z-deadbeef"),
            ("update", "apply", "--version", "v2026.7.7.2"),
        ]
        for action, subaction, option, value in cases:
            with self.subTest(action=action):
                r = run_sb("hermes", action, subaction, "--remote", remote, option, value, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_hermes_v2_read_commands_keep_the_json_envelope(self):
        remote = "missing-remote-for-v2-read-contract"
        cases = [
            ("update", "plan", "--version", "v2026.7.7.2"),
            ("backup", "list"),
            ("cleanup",),
            ("health",),
            ("acceptance", "v2"),
        ]
        for case in cases:
            with self.subTest(case=case):
                r = run_sb("hermes", *case, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], "unknown_remote")

    def test_hermes_gateway_and_async_parser_failures_are_json_safe_before_remote_access(self):
        remote = "missing-remote-for-parser-contract"
        cases = [
            ("gateway",),
            ("job", "status"),
            ("run", "--async"),
        ]
        for case in cases:
            with self.subTest(case=case):
                r = run_sb("hermes", *case, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertIn(payload["error"]["code"], {"missing_gateway_action", "missing_job_id", "missing_run_input"})

    def test_hermes_repository_subcommands_have_stable_json_failures(self):
        remote = "missing-remote-for-repository-contract"
        cases = [
            (("repo", "auth", "gitlab"), "unsupported_provider"),
            (("repo", "clone"), "missing_repo_url"),
            (("repo", "list"), "unknown_remote"),
        ]
        for command, expected_code in cases:
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], expected_code)

    def test_hermes_repository_auth_rejects_broad_oauth_and_advertises_token_stdin(self):
        remote = "missing-remote-for-least-privilege-auth"
        r = run_sb("hermes", "repo", "auth", "github", "--remote", remote, "--json")
        self.assertNotEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["error"]["code"], "fine_grained_token_required")
        help_output = run_sb("hermes", "--help")
        self.assertEqual(help_output.returncode, 0, help_output.stderr)
        self.assertIn("--token-stdin", help_output.stdout)

    def test_hermes_v1_command_contracts_are_json_safe(self):
        remote = "missing-remote-for-v1-contract"
        cases = [
            (("install", "--version", "main"), "invalid_release"),
            (("setup",), "unknown_remote"),
            (("doctor",), "unknown_remote"),
            (("status",), "unknown_remote"),
            (("chat",), "missing_repo"),
            (("run",), "missing_run_input"),
        ]
        for command, expected_code in cases:
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], expected_code)

    def test_no_main_in_help_command_list(self):
        # The phantom `main` instance is gone; it must not appear as guidance.
        r = run_sb("instances")
        self.assertNotIn(" main ", (r.stdout + r.stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
