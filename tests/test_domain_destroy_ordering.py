from __future__ import annotations

import inspect
import unittest


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


if __name__ == "__main__":
    unittest.main()
