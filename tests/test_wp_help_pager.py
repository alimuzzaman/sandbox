import unittest
from pathlib import Path
from unittest import mock

from sandbox.core import _docker


class TestWpHelpPager(unittest.TestCase):
    def test_builtin_wp_cli_exec_sets_noninteractive_pager(self):
        with mock.patch.object(_docker, "_managed_execution_gate", return_value=None), \
                mock.patch.object(_docker, "_is_herd_instance", return_value=False), \
                mock.patch.object(_docker, "_wp_has_builtin_cli", return_value=True), \
                mock.patch.object(_docker, "compose") as compose:
            _docker._wpcli_unleased(["help", "plugin"], instance="fixture")

        self.assertEqual(
            compose.call_args.args,
            ("exec", "-e", "PAGER=cat", "-u", "www-data", "-T",
             "wp", "wp", "help", "plugin"),
        )
        self.assertEqual(compose.call_args.kwargs["instance"], "fixture")

    def test_one_shot_wp_cli_container_sets_noninteractive_pager(self):
        with mock.patch.object(_docker, "_managed_execution_gate", return_value=None), \
                mock.patch.object(_docker, "_is_herd_instance", return_value=False), \
                mock.patch.object(_docker, "_wp_has_builtin_cli", return_value=False), \
                mock.patch.object(_docker, "compose") as compose:
            _docker._wpcli_unleased(["help", "plugin"], instance="fixture")

        self.assertEqual(
            compose.call_args.args,
            ("run", "--rm", "-e", "PAGER=cat", "wpcli", "help", "plugin"),
        )
        self.assertEqual(compose.call_args.kwargs["instance"], "fixture")

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
