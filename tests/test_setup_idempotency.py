import types
import unittest
from unittest.mock import patch

from sandbox.commands import lifecycle


class TestSetupIdempotency(unittest.TestCase):
    def test_wp_is_installed_uses_core_is_installed(self):
        with patch.object(
            lifecycle,
            "wpcli",
            return_value=types.SimpleNamespace(returncode=0),
        ) as wpcli:
            self.assertTrue(lifecycle.wp_is_installed("demo"))
        wpcli.assert_called_once_with(
            ["core", "is-installed"],
            instance="demo",
            check=False,
            capture=True,
        )

    def test_wp_is_installed_is_false_until_database_is_ready(self):
        with patch.object(
            lifecycle,
            "wpcli",
            return_value=types.SimpleNamespace(returncode=1),
        ):
            self.assertFalse(lifecycle.wp_is_installed("demo"))

    def test_up_removes_orphan_services(self):
        cfg = {"instances": {"demo": {"server": "nginx"}}}
        args = types.SimpleNamespace(resolved_instance="demo")
        inst = {"server": "nginx", "wordpress_port": 8188,
                "db_port": 3318, "mailpit_port": 8125}
        with patch.object(lifecycle, "resolve_instances", return_value={"demo": inst}), \
             patch.object(lifecycle, "compose") as compose, \
             patch.object(lifecycle, "_web_services", return_value=("wp", "nginx")), \
             patch.object(lifecycle, "site_url", return_value="http://localhost:8188"), \
             patch.object(lifecycle, "wp_dir", return_value=types.SimpleNamespace(exists=lambda: False)):
            lifecycle.cmd_up(cfg, args)
        compose.assert_called_once_with(
            "up", "-d", "--remove-orphans", "wp", "nginx", instance="demo"
        )


if __name__ == "__main__":
    unittest.main()
