import inspect
import unittest


class TestIngressDestroyOrdering(unittest.TestCase):
    def test_ingress_cleanup_precedes_dns_runtime_and_identity_deletion(self):
        from sandbox.commands.instances_cmd import cmd_instance, _cleanup_instance_routes
        helper = inspect.getsource(_cleanup_instance_routes)
        ingress = helper.index("ingress_service(cfg).cleanup_owner")
        dns = helper.index("domain_service(cfg).cleanup")
        self.assertLess(ingress, dns)
        source = inspect.getsource(cmd_instance)
        cleanup = source.rindex("_cleanup_instance_routes(cfg, owner)")
        runtime = source.index('compose("down", "-v"')
        local_identity = source.index('del local["instances"][name]')
        registry_identity = source.index("sc.registry_remove")
        self.assertLess(cleanup, runtime)
        self.assertLess(cleanup, local_identity)
        self.assertLess(cleanup, registry_identity)

    def test_cleanup_owner_uses_durable_owner_string_not_live_registry_lookup(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/application/ingress_service.py").read_text()
        self.assertIn("def cleanup_owner(self, owner", source)
        self.assertNotIn("registry_find_instance", source)


if __name__ == "__main__": unittest.main()
