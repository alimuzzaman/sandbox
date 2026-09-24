from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch


class TestDomainDestroyOrdering(unittest.TestCase):
    def test_dns_cleanup_precedes_runtime_and_identity_deletion(self):
        from sandbox.commands.instances_cmd import cmd_instance, _cleanup_instance_routes

        helper = inspect.getsource(_cleanup_instance_routes)
        self.assertIn("domain_service(cfg).cleanup", helper)
        source = inspect.getsource(cmd_instance)
        cleanup = source.rindex("_cleanup_instance_routes(cfg, owner)")
        runtime = source.index('compose("down", "-v"')
        local_identity = source.index("_write_local_yaml(local)")
        registry_identity = source.index("sc.registry_remove")
        self.assertLess(cleanup, runtime)
        self.assertLess(cleanup, local_identity)
        self.assertLess(cleanup, registry_identity)

    def test_post_instance_retry_is_covered_by_cleanup_contract(self):
        from tests.test_domain_cleanup import TestDomainCleanup

        source = inspect.getsource(
            TestDomainCleanup.test_cleanup_retries_from_retained_binding_after_registry_deletion
        )
        self.assertIn("DeletedRegistry", source)
        self.assertIn("service.cleanup", source)

    def test_instance_delete_refreshes_caddy_after_registry_removal(self):
        from sandbox.commands.instances_cmd import cmd_instance

        source = inspect.getsource(cmd_instance)
        registry_removal = source.rindex("sc.registry_remove(owner[\"root\"]")
        route_refresh = source.index(
            "_refresh_caddy_routes_after_instance_delete()", registry_removal,
        )
        success = source.index("ok(f\"Instance '{name}' deleted.\")")
        self.assertLess(registry_removal, route_refresh)
        self.assertLess(route_refresh, success)
        self.assertNotIn("preserved unreceipted legacy domain artifacts", source)

    def test_caddy_refresh_uses_current_config_without_starting_stopped_proxy(self):
        from sandbox.commands import instances_cmd

        current_config = {"instances": {"other": {"domain": "other.tst"}}}
        with patch.object(instances_cmd, "load_config", return_value=current_config), \
                patch.object(instances_cmd, "regen_caddyfile") as regenerate, \
                patch.object(instances_cmd, "_proxy_container_running",
                             return_value=False) as running, \
                patch.object(instances_cmd, "reload_proxy") as reload_proxy:
            instances_cmd._refresh_caddy_routes_after_instance_delete()

        regenerate.assert_called_once_with(current_config)
        running.assert_called_once_with()
        reload_proxy.assert_not_called()

    def test_caddy_refresh_reports_live_reload_failure_with_recovery_command(self):
        from sandbox.commands import instances_cmd

        with patch.object(instances_cmd, "load_config", return_value={}), \
                patch.object(instances_cmd, "regen_caddyfile"), \
                patch.object(instances_cmd, "_proxy_container_running",
                             return_value=True), \
                patch.object(instances_cmd, "reload_proxy", return_value=False), \
                patch.object(instances_cmd, "info") as info:
            instances_cmd._refresh_caddy_routes_after_instance_delete()

        info.assert_called_once()
        self.assertIn("./sb domains up", info.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
