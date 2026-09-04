import unittest
from tests.server_config_fixtures import (
    FIXED_INCARNATION, FIXED_NOW, FakeClock,
    fragment, runtime_observation,
)
from sandbox.server_config.models import ServerType, InstanceConfigAuthority, RuntimeMode
from sandbox.server_config.context import project_mount, project_instance_context

# These imports should fail as the implementation does not exist yet
from sandbox.server_config.lifecycle import get_nginx_mounts, check_instance_attachment, get_nginx_vhost_includes, dispatch_command

class TestServerConfigLifecycle(unittest.TestCase):
    def test_instance_specific_nginx_mount(self):
        """Test that the Compose configuration for an nginx instance includes a read-only mount"""
        mounts = get_nginx_mounts(FIXED_INCARNATION)
        self.assertTrue(any(
            m.source == f"$SANDBOX_HOME/runtime/server-config/{FIXED_INCARNATION}/" and
            m.read_only
            for m in mounts
        ))

    def test_existing_base_vhost_include(self):
        """Test that the nginx base vhost includes an absent-safe glob for the instance's fragment generation"""
        includes = get_nginx_vhost_includes()
        self.assertIn("include /etc/nginx/sandbox-fragments/*.conf;", includes)

    def test_unattached_legacy_refusal(self):
        """Test that a legacy instance is refused before any fragment state mutation"""
        with self.assertRaises(RuntimeError) as ctx:
            check_instance_attachment(incarnation_id=None)
        self.assertIn("sb apply --instance", str(ctx.exception))

    def test_metadata_read_only_pre_dispatch(self):
        """Test that list and show use the pre-dispatch skip policy"""
        res = dispatch_command("list", FIXED_INCARNATION)
        self.assertEqual(res.writes, 0)
        self.assertEqual(res.regenerations, 0)
        self.assertEqual(res.migrations, 0)

if __name__ == '__main__':
    unittest.main()
