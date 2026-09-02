from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ServerConfigCoreIdentityTests(unittest.TestCase):
    def test_new_record_mints_once_while_existing_and_legacy_records_are_preserved(self):
        from sandbox.core import _instances

        minted = _instances._server_config_registry_identity_fields(
            None, random_bytes=lambda size: b"\x12" * size,
        )
        self.assertEqual(minted, {
            "instance_incarnation_id": "inc_" + "12" * 16,
            "server_config_mount_id": None,
        })
        self.assertEqual(
            _instances._server_config_registry_identity_fields(minted), minted,
        )
        self.assertEqual(
            _instances._server_config_registry_identity_fields({"instance": "legacy"}),
            {},
        )

    def test_failed_apply_restores_exact_prior_identity_projection(self):
        from sandbox.core import _instances

        incarnation = "inc_" + "1" * 32
        prior_mount = "sha256:" + "a" * 64
        staged_mount = "sha256:" + "b" * 64
        writes = []

        class Core:
            @staticmethod
            def registry_get(root, label=None):
                return {
                    "root": root, "label": label,
                    "instance_incarnation_id": incarnation,
                    "server_config_mount_id": staged_mount,
                }

            @staticmethod
            def registry_put(root, label="default", **fields):
                writes.append((root, label, fields))
                return {"root": root, "label": label, **fields}

        with tempfile.TemporaryDirectory() as tmp:
            compose_path = Path(tmp) / "compose.yml"
            snapshot = {
                "local": {}, "compose_path": compose_path,
                "compose_exists": False, "compose_bytes": None,
                "runtime": {}, "runtime_running": False,
                "registry": {
                    "root": "/projects/demo", "label": "default",
                    "instance_incarnation_id": incarnation,
                    "server_config_mount_id": prior_mount,
                },
                "server_config_identity": {
                    "instance_incarnation_id": incarnation,
                    "server_config_mount_id": prior_mount,
                },
            }
            with mock.patch.object(_instances, "_core", return_value=Core()), \
                    mock.patch.object(_instances, "_write_local_yaml"):
                restored = _instances._restore_apply_rollback_state(
                    snapshot, "demo", runtime_touched=False,
                )

        self.assertTrue(restored["ok"])
        self.assertEqual(writes, [(
            "/projects/demo", "default", {
                "instance_incarnation_id": incarnation,
                "server_config_mount_id": prior_mount,
            },
        )])

    def test_rollback_does_not_write_registry_when_identity_is_unchanged(self):
        from sandbox.core import _instances

        identity = {
            "instance_incarnation_id": "inc_" + "1" * 32,
            "server_config_mount_id": "sha256:" + "a" * 64,
        }

        class Core:
            @staticmethod
            def registry_get(root, label=None):
                return {"root": root, "label": label, **identity}

            @staticmethod
            def registry_put(*args, **kwargs):
                raise AssertionError("unchanged identity must not be rewritten")

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = {
                "local": {}, "compose_path": Path(tmp) / "compose.yml",
                "compose_exists": False, "compose_bytes": None,
                "runtime": {}, "runtime_running": False,
                "registry": {"root": "/projects/demo", "label": "default", **identity},
                "server_config_identity": identity,
            }
            with mock.patch.object(_instances, "_core", return_value=Core()), \
                    mock.patch.object(_instances, "_write_local_yaml"):
                restored = _instances._restore_apply_rollback_state(
                    snapshot, "demo", runtime_touched=False,
                )

        self.assertTrue(restored["ok"])


if __name__ == "__main__":
    unittest.main()
