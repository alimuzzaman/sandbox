from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class ServerConfigContextTests(unittest.TestCase):
    def test_mount_projection_is_typed_owner_bound_and_relocation_stable(self):
        from sandbox.server_config.context import project_mount

        incarnation = "inc_" + "1" * 32
        first = project_mount(Path("/first/home/runtime/server-config"), incarnation)
        relocated = project_mount(Path("/second/home/runtime/server-config"), incarnation)

        self.assertEqual(first.instance_incarnation_id, incarnation)
        self.assertEqual(first.mount_id, relocated.mount_id)
        self.assertNotEqual(first.source_root, relocated.source_root)
        self.assertTrue(first.read_only)
        self.assertRegex(first.mount_id, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaisesRegex(ValueError, "mount root"):
            type(first)(
                instance_incarnation_id=incarnation,
                mount_id=first.mount_id,
                source_root=Path("relative") / incarnation,
            )

    def test_legacy_record_projects_as_unattached_without_minting(self):
        from sandbox.server_config.context import project_instance_context

        projected = project_instance_context(
            record={
                "instance": "demo", "root": "/projects/demo", "server": "nginx",
                "status": "ready",
            },
            project_identity="project:demo",
            server_config_root=Path("/sandbox/runtime/server-config"),
        )

        self.assertTrue(projected.identity.is_legacy)
        self.assertIsNone(projected.mount)
        self.assertFalse(projected.authority.supports_mutation)

    def test_attached_record_projects_typed_authority_and_mount(self):
        from sandbox.server_config.context import project_instance_context
        from sandbox.server_config.models import RuntimeMode, ServerType

        incarnation = "inc_" + "2" * 32
        record = {
            "instance": "demo", "root": "/projects/demo", "server": "nginx",
            "status": "ready", "instance_incarnation_id": incarnation,
        }
        expected = project_instance_context(
            record=record, project_identity="project:demo",
            server_config_root=Path("/sandbox/runtime/server-config"),
        ).expected_mount
        record["server_config_mount_id"] = expected.mount_id

        projected = project_instance_context(
            record=record, project_identity="project:demo",
            server_config_root=Path("/sandbox/runtime/server-config"),
        )

        self.assertEqual(projected.authority.server_type, ServerType.NGINX)
        self.assertEqual(projected.authority.runtime_mode, RuntimeMode.LOCAL_COMPOSE)
        self.assertEqual(projected.mount, expected)
        self.assertTrue(projected.authority.supports_mutation)

    def test_application_composition_requires_dependencies_and_does_not_read_state(self):
        from sandbox.application.context import server_config_dependencies
        from tests.server_config_fixtures import FakeClock

        calls = []

        class Registry:
            def registry_get(self, root, label=None):
                calls.append((root, label))
                return None

        with tempfile.TemporaryDirectory() as tmp:
            dependencies = server_config_dependencies(
                registry=Registry(),
                server_config_root=Path(tmp),
                project_identity_resolver=lambda root, label=None: "project:test",
                clock=FakeClock(),
            )

        self.assertEqual(calls, [])
        self.assertEqual(dependencies.project_identity_resolver("/p"), "project:test")
        self.assertIsNotNone(dependencies.repository_factory)
        self.assertEqual(dependencies.adapters.server_types(), ("litespeed", "nginx"))
        self.assertEqual(dependencies.clock.monotonic(), 1_000.0)

    def test_public_package_exports_only_typed_foundation_contract(self):
        import sandbox.server_config as server_config

        expected = {
            "AdapterDescriptor", "AdapterRegistry", "InstanceConfigAuthority",
            "InstanceIdentityProjection", "ServerConfigDependencies",
            "ServerConfigInstanceContext", "ServerConfigMountProjection",
            "ServerType", "project_instance_context", "project_mount",
        }
        self.assertTrue(expected.issubset(set(server_config.__all__)))
        for name in expected:
            self.assertTrue(hasattr(server_config, name))


if __name__ == "__main__":
    unittest.main()
