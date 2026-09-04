import unittest
from tests.server_config_fixtures import (
    FIXED_INCARNATION, FIXED_NOW, FakeClock,
    fragment, runtime_observation,
)
from sandbox.server_config.models import ServerType, InstanceConfigAuthority, RuntimeMode
from sandbox.server_config.context import project_mount, project_instance_context

# These imports should fail as the implementation does not exist yet
from sandbox.server_config.lifecycle import apply_fragment, get_fragment_root, get_nginx_mounts, read_fragments

class TestServerConfigIsolation(unittest.TestCase):
    def test_zero_other_instance_write(self):
        """Test that applying/reverting fragments on instance A does not touch instance B's fragment root, mount, or runtime"""
        incarnation_a = "inst-A"
        incarnation_b = "inst-B"
        
        apply_fragment(incarnation_a, fragment())
        
        root_a = get_fragment_root(incarnation_a)
        root_b = get_fragment_root(incarnation_b)
        
        self.assertNotEqual(root_a, root_b)
        
    def test_distinct_mount_roots(self):
        """Test that two instances with different incarnation IDs get different source roots and mount IDs"""
        incarnation_a = "inst-A"
        incarnation_b = "inst-B"
        
        mounts_a = get_nginx_mounts(incarnation_a)
        mounts_b = get_nginx_mounts(incarnation_b)
        
        self.assertNotEqual(mounts_a[0].source, mounts_b[0].source)

    def test_no_cross_incarnation_adoption(self):
        """Test that fragments stored under incarnation X cannot be read/applied by incarnation Y"""
        with self.assertRaises(ValueError):
            read_fragments(incarnation_id="inst-Y", storage_path=f"/path/to/inst-X/fragments")

    def test_distinct_mount_roots_identical_guest_targets(self):
        """T070: Different instances get different host source paths but fixed guest target."""
        mounts_a = get_nginx_mounts("inst-A")
        mounts_b = get_nginx_mounts("inst-B")
        self.assertNotEqual(mounts_a[0].source, mounts_b[0].source)
        self.assertEqual(mounts_a[0].target, mounts_b[0].target)
        self.assertEqual(mounts_a[0].target, "/etc/nginx/sandbox-fragments")

    def test_target_only_compose_invocation(self):
        """T070: Mutation on target instance only targets that instance container."""
        from sandbox.server_config.lifecycle import get_target_service_scope

        target_scope = get_target_service_scope("target-inst", "nginx")
        self.assertEqual(target_scope["instance"], "target-inst")
        self.assertEqual(target_scope["service"], "web")
        self.assertNotIn("control-inst", target_scope.values())

    def test_no_host_global_or_caddy_changes(self):
        """T070: Fragment mutations never touch host-global Caddy or proxy configs."""
        from sandbox.server_config.lifecycle import verify_caddy_untouched

        self.assertTrue(verify_caddy_untouched())

    def test_cross_instance_adoption_strictly_refused(self):
        """T070: Instance B cannot adopt or reference fragments owned by Instance A."""
        with self.assertRaises(ValueError):
            read_fragments(
                incarnation_id="inc_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                storage_path="/path/to/runtime/server-config/inc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/fragments",
            )


if __name__ == '__main__':
    unittest.main()
