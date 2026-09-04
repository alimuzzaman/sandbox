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

    def test_server_switch_refused_when_active_fragments_exist(self):
        """T071: Server-switch is refused if instance has active fragments."""
        from sandbox.server_config.lifecycle import check_server_switch_allowed

        with self.assertRaises(RuntimeError) as ctx:
            check_server_switch_allowed(
                instance_name="test-inst",
                has_active_fragments=True,
                has_pending_transaction=False,
                is_recovery_needed=False,
            )
        self.assertIn("revert fragments", str(ctx.exception).lower())

    def test_server_switch_refused_when_transaction_unresolved(self):
        """T071: Server-switch is refused if instance has an unresolved transaction."""
        from sandbox.server_config.lifecycle import check_server_switch_allowed

        with self.assertRaises(RuntimeError) as ctx:
            check_server_switch_allowed(
                instance_name="test-inst",
                has_active_fragments=False,
                has_pending_transaction=True,
                is_recovery_needed=False,
            )
        self.assertIn("unresolved", str(ctx.exception).lower())

    def test_server_switch_refused_when_recovery_needed(self):
        """T071: Server-switch is refused if instance is in recovery-needed state."""
        from sandbox.server_config.lifecycle import check_server_switch_allowed

        with self.assertRaises(RuntimeError) as ctx:
            check_server_switch_allowed(
                instance_name="test-inst",
                has_active_fragments=False,
                has_pending_transaction=False,
                is_recovery_needed=True,
            )
        self.assertIn("recovery", str(ctx.exception).lower())

    def test_server_switch_allowed_when_clean(self):
        """T071: Server-switch is allowed when fragment state is empty and healthy."""
        from sandbox.server_config.lifecycle import check_server_switch_allowed

        # Should return cleanly without exception
        check_server_switch_allowed(
            instance_name="test-inst",
            has_active_fragments=False,
            has_pending_transaction=False,
            is_recovery_needed=False,
        )

    def test_instance_deletion_refused_when_active_fragments_without_authorization(self):
        """T071: Deleting an instance with active fragments requires explicit confirmation."""
        from sandbox.server_config.lifecycle import check_instance_deletion_allowed

        with self.assertRaises(RuntimeError) as ctx:
            check_instance_deletion_allowed(
                instance_name="test-inst",
                has_active_fragments=True,
                has_pending_transaction=False,
                is_recovery_needed=False,
                confirm_server_config=False,
            )
        self.assertIn("server-config", str(ctx.exception).lower())

    def test_instance_deletion_allowed_when_confirmed(self):
        """T071: Deleting an instance with active fragments is allowed when explicitly confirmed."""
        from sandbox.server_config.lifecycle import check_instance_deletion_allowed

        check_instance_deletion_allowed(
            instance_name="test-inst",
            has_active_fragments=True,
            has_pending_transaction=False,
            is_recovery_needed=False,
            confirm_server_config=True,
        )


if __name__ == '__main__':
    unittest.main()
