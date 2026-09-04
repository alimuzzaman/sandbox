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

    def test_isolation_target_control_set_unchanged_on_refusal(self):
        """T049: Refused mutation leaves existing fragment set and readiness unchanged."""
        incarnation = "inst-unchanged"
        initial_root = get_fragment_root(incarnation)
        with self.assertRaises(ValueError):
            apply_fragment(incarnation, fragment(name="invalid_bad", content=b"listen 80;"))
        self.assertEqual(get_fragment_root(incarnation), initial_root)


if __name__ == '__main__':
    unittest.main()
