import types
import unittest
from unittest.mock import Mock, patch

from sandbox.commands import lifecycle
from sandbox.core import _instances


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

    def test_up_never_mutates_unreceipted_legacy_proxy_state(self):
        cfg = {"instances": {"demo": {"server": "nginx"}}}
        args = types.SimpleNamespace(resolved_instance="demo")
        inst = {"server": "nginx", "domain": "demo.tst", "tld": "tst",
                "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125}
        proxy = Mock()
        proxy.plan.return_value = {"hostname": "demo.tst", "port": 8188}
        dependencies = types.SimpleNamespace(proxy=proxy)
        with patch.object(lifecycle, "resolve_instances", return_value={"demo": inst}), \
             patch.object(lifecycle, "proxy_available", return_value=True), \
             patch.object(lifecycle, "wordpress_runtime_dependencies", return_value=dependencies), \
             patch.object(lifecycle, "_ensure_proxy_up") as legacy_ensure, \
             patch.object(lifecycle, "compose"), \
             patch.object(lifecycle, "_web_services", return_value=("wp", "nginx")), \
             patch.object(lifecycle, "site_url", return_value="http://demo.tst"), \
             patch.object(lifecycle, "wp_dir", return_value=types.SimpleNamespace(exists=lambda: False)):
            lifecycle.cmd_up(cfg, args)
        proxy.plan.assert_not_called()
        proxy.apply.assert_not_called()
        legacy_ensure.assert_not_called()

    def test_port_conflicts_reassign_all_instance_ports(self):
        cfg = {"instances": {"demo": {}}}
        local = {"instances": {"demo": {}}}
        resolved = {"demo": {
            "wordpress_port": 8188,
            "db_port": 3318,
            "mailpit_port": 8125,
        }}
        with patch.object(_instances, "resolve_instances", return_value=resolved), \
             patch.object(_instances, "_local_yaml", return_value=local), \
             patch.object(_instances, "_port_busy_by_other",
                          side_effect=[False, False, True]), \
             patch.object(_instances, "_next_free_port",
                          side_effect=[8190, 3320, 8127]), \
             patch.object(_instances, "_write_local_yaml") as write_local, \
             patch.object(_instances, "load_config", return_value=cfg), \
             patch.object(_instances, "write_compose_files"):
            _instances._resolve_port_conflicts(cfg)

        self.assertEqual(local["instances"]["demo"], {
            "wordpress_port": 8190,
            "db_port": 3320,
            "mailpit_port": 8127,
        })
        write_local.assert_called_once_with(local)


if __name__ == "__main__":
    unittest.main()
