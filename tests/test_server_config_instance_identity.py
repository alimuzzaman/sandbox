from dataclasses import FrozenInstanceError
import re
import unittest


class ServerConfigInstanceIdentityTests(unittest.TestCase):
    def test_new_instance_mints_fixed_format_opaque_incarnation(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        projection = InstanceIdentityProjection.for_new_instance(
            random_bytes=lambda size: b"\x12" * size
        )
        self.assertEqual(projection.instance_incarnation_id, "inc_" + "12" * 16)
        self.assertRegex(projection.instance_incarnation_id, re.compile(r"^inc_[0-9a-f]{32}$"))
        self.assertIsNone(projection.server_config_mount_id)
        self.assertTrue(projection.is_attached is False)

    def test_existing_identity_is_preserved_across_projection(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        current = InstanceIdentityProjection(
            instance_incarnation_id="inc_" + "1" * 32,
            server_config_mount_id="sha256:" + "a" * 64,
        )
        projected = current.preserve_for_update()
        self.assertEqual(projected, current)
        self.assertIsNot(projected, current)
        with self.assertRaises(FrozenInstanceError):
            projected.instance_incarnation_id = "inc_" + "2" * 32

    def test_legacy_record_is_visible_but_never_silently_adopted(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        legacy = InstanceIdentityProjection.from_existing_record({})
        self.assertTrue(legacy.is_legacy)
        self.assertFalse(legacy.can_mutate)
        with self.assertRaisesRegex(ValueError, "legacy instance cannot adopt"):
            legacy.stage_mount("sha256:" + "a" * 64)

    def test_staged_mount_rolls_back_to_exact_prior_projection(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        current = InstanceIdentityProjection(
            instance_incarnation_id="inc_" + "1" * 32,
            server_config_mount_id="sha256:" + "a" * 64,
        )
        staged = current.stage_mount("sha256:" + "b" * 64)
        self.assertEqual(staged.server_config_mount_id, "sha256:" + "b" * 64)
        self.assertEqual(staged.prior_server_config_mount_id, "sha256:" + "a" * 64)
        rolled_back = staged.rollback_mount()
        self.assertEqual(rolled_back, current)

    def test_failed_first_mount_attachment_rolls_back_to_unattached(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        current = InstanceIdentityProjection(
            instance_incarnation_id="inc_" + "1" * 32,
            server_config_mount_id=None,
        )
        staged = current.stage_mount("sha256:" + "b" * 64)
        self.assertEqual(staged.rollback_mount(), current)

    def test_name_reuse_requires_a_new_incarnation_and_cannot_reuse_mount(self):
        from sandbox.server_config.models import InstanceIdentityProjection

        deleted = InstanceIdentityProjection(
            instance_incarnation_id="inc_" + "1" * 32,
            server_config_mount_id="sha256:" + "a" * 64,
        )
        recreated = deleted.for_recreated_instance(random_bytes=lambda size: b"\x22" * size)
        self.assertEqual(recreated.instance_incarnation_id, "inc_" + "22" * 16)
        self.assertNotEqual(recreated.instance_incarnation_id, deleted.instance_incarnation_id)
        self.assertIsNone(recreated.server_config_mount_id)


if __name__ == "__main__":
    unittest.main()
