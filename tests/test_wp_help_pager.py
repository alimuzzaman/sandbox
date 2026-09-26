import unittest
from pathlib import Path
from unittest import mock

from sandbox.commands import wp
from sandbox.core import _docker


class TestWpHelpPager(unittest.TestCase):
    def test_help_gets_no_pager_switch(self):
        self.assertEqual(
            wp._disable_help_pager(["help", "w3-total-cache", "option", "set"]),
            ["help", "w3-total-cache", "option", "set", "--no-pager"],
        )

    def test_global_option_before_help_is_supported(self):
        self.assertEqual(
            wp._disable_help_pager(["--require=fixture.php", "help", "plugin"])[-1],
            "--no-pager",
        )

    def test_explicit_pager_choice_is_preserved(self):
        self.assertEqual(
            wp._disable_help_pager(["help", "plugin", "--pager"]),
            ["help", "plugin", "--pager"],
        )
        self.assertEqual(
            wp._disable_help_pager(["help", "plugin", "--no-pager"]),
            ["help", "plugin", "--no-pager"],
        )

    def test_unrelated_commands_are_unchanged(self):
        self.assertEqual(wp._disable_help_pager(["plugin", "list"]), ["plugin", "list"])

    def test_builtin_wp_cli_runs_with_cat_pager(self):
        with mock.patch.object(_docker, "_managed_execution_gate", return_value=None), \
                mock.patch.object(_docker, "_is_herd_instance", return_value=False), \
                mock.patch.object(_docker, "_wp_has_builtin_cli", return_value=True), \
                mock.patch.object(_docker, "compose", return_value="result") as compose:
            result = _docker._wpcli_unleased(["help", "core"], "demo")

        self.assertEqual(result, "result")
        self.assertEqual(
            compose.call_args.args[:8],
            ("exec", "-e", "PAGER=cat", "-u", "www-data", "-T", "wp", "wp"),
        )

    def test_wpcli_fallback_runs_with_cat_pager(self):
        with mock.patch.object(_docker, "_managed_execution_gate", return_value=None), \
                mock.patch.object(_docker, "_is_herd_instance", return_value=False), \
                mock.patch.object(_docker, "_wp_has_builtin_cli", return_value=False), \
                mock.patch.object(_docker, "compose", return_value="result") as compose:
            _docker._wpcli_unleased(["help", "core"], "demo")

        self.assertEqual(
            compose.call_args.args[:5], ("run", "-e", "PAGER=cat", "--rm", "wpcli"),
        )

    def test_wpcli_service_sets_cat_pager_for_fallback(self):
        with mock.patch.object(_docker, "_instance_wpcli_image", return_value="wpcli:test"), \
                mock.patch.object(_docker, "_server_runtime", return_value={
                    "docroot": "/var/www/html", "uid": "33:33",
                }), \
                mock.patch.object(_docker, "_env_config_lines", return_value=""):
            rendered = _docker._wpcli_service(
                "demo", {"server": "nginx"}, Path("/plugins"),
            )
        self.assertIn("      PAGER: cat", rendered)


if __name__ == "__main__":
    unittest.main()
