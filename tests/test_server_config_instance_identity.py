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

    def test_incarnation_preserved_on_apply_and_reconcile(self):
        """T069: Existing incarnation must be preserved across apply/reconcile."""
        from sandbox.core._instances import _server_config_registry_identity_fields

        existing = {
            "instance_incarnation_id": "inc_" + "a" * 32,
            "server_config_mount_id": "sha256:" + "b" * 64,
        }
        reconciled = _server_config_registry_identity_fields(existing)
        self.assertEqual(reconciled["instance_incarnation_id"], "inc_" + "a" * 32)
        self.assertEqual(reconciled["server_config_mount_id"], "sha256:" + "b" * 64)

    def test_incarnation_preserved_across_relocation(self):
        """T069: Relocating an instance does not change its incarnation ID."""
        from sandbox.server_config.lifecycle import relocate_instance_server_config

        record = {
            "instance_incarnation_id": "inc_" + "f" * 32,
            "server_config_mount_id": "sha256:" + "c" * 64,
        }
        relocated = relocate_instance_server_config(record, "/new/sandbox/home")
        self.assertEqual(relocated["instance_incarnation_id"], "inc_" + "f" * 32)
        self.assertEqual(relocated["server_config_mount_id"], "sha256:" + "c" * 64)

    def test_deletion_disassociates_incarnation_and_cleans_fragments(self):
        """T069: Instance deletion cleans up fragment storage and disassociates incarnation."""
        from sandbox.server_config.lifecycle import disassociate_instance_server_config
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as temp_dir:
            incarnation_id = "inc_" + "d" * 32
            frag_dir = os.path.join(temp_dir, incarnation_id)
            os.makedirs(frag_dir, exist_ok=True)
            test_file = os.path.join(frag_dir, "test.fragment")
            with open(test_file, "w") as f:
                f.write("test")

            disassociate_instance_server_config(
                incarnation_id=incarnation_id,
                storage_root=temp_dir,
            )
            self.assertFalse(os.path.exists(frag_dir))

    def test_legacy_record_refuses_fragment_mutation(self):
        """T069: Legacy instance records without incarnation ID fail closed on fragment mutation."""
        from sandbox.server_config.lifecycle import check_instance_attachment

        with self.assertRaises(RuntimeError) as ctx:
            check_instance_attachment(incarnation_id=None)
        self.assertIn("sb apply --instance", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
