from __future__ import annotations

import unittest


class ServerConfigAdapterManifestTests(unittest.TestCase):
    def test_manifest_contains_only_nginx_and_litespeed(self):
        from sandbox.server_config.adapters.manifest import default_adapter_registry

        registry = default_adapter_registry()
        self.assertEqual(registry.server_types(), ("litespeed", "nginx"))
        self.assertEqual(registry.require("nginx").adapter_id, "wordpress-cache/nginx/1")
        self.assertEqual(registry.require("nginx").web_service, "nginx")
        self.assertEqual(
            registry.require("litespeed").adapter_id,
            "wordpress-cache/openlitespeed/1",
        )
        for server in ("apache", "herd"):
            with self.subTest(server=server):
                with self.assertRaisesRegex(ValueError, "server_unsupported"):
                    registry.require(server)

    def test_duplicate_server_or_adapter_id_is_refused(self):
        from sandbox.server_config.adapters.base import AdapterDescriptor, AdapterRegistry

        nginx = AdapterDescriptor(
            server_type="nginx", adapter_id="wordpress-cache/nginx/1",
            authority_versions=("wordpress-cache-v1",), renderer_revision="nginx/1",
            active_image_families=("nginx",), web_service="nginx",
            mount_layout="server-config-mount-v1/nginx",
            readiness_contract="target-origin-effective-generation/v1",
        )
        with self.assertRaisesRegex(ValueError, "adapter_duplicate"):
            AdapterRegistry((nginx, nginx))
        duplicate_id = AdapterDescriptor(
            server_type="litespeed", adapter_id="wordpress-cache/nginx/1",
            authority_versions=("wordpress-cache-v1",), renderer_revision="ols/1",
            active_image_families=("litespeedtech/openlitespeed",), web_service="wp",
            mount_layout="server-config-mount-v1/ols",
            readiness_contract="target-origin-effective-vhost/v1",
        )
        with self.assertRaisesRegex(ValueError, "adapter_duplicate"):
            AdapterRegistry((nginx, duplicate_id))

    def test_adapter_protocol_declares_every_bounded_phase(self):
        from sandbox.server_config.adapters.base import ServerConfigAdapter

        required = {
            "policy", "render", "observe_runtime", "validate", "activate", "reload",
            "observe_ready", "restore",
        }
        self.assertTrue(required.issubset(set(dir(ServerConfigAdapter))))

    def test_rendered_generation_refuses_paths_writable_modes_and_duplicates(self):
        from sandbox.server_config.adapters.base import RenderedFile, RenderedGeneration

        safe = RenderedFile("combined.conf", b"safe\n")
        with self.assertRaisesRegex(ValueError, "name"):
            RenderedFile("../outside", b"unsafe\n")
        with self.assertRaisesRegex(ValueError, "mode"):
            RenderedFile("combined.conf", b"unsafe\n", mode=0o600)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            RenderedGeneration(
                generation_id="sha256:" + "a" * 64,
                files=(safe, safe),
                manifest_digest="sha256:" + "b" * 64,
            )


if __name__ == "__main__":
    unittest.main()
