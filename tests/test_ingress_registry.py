from __future__ import annotations

import unittest


class TestIngressRegistry(unittest.TestCase):
    def test_order_is_deterministic_and_duplicates_fail(self):
        from sandbox.ingress.models import SupportDeclaration
        from sandbox.ingress.registry import IngressAdapterRegistry, IngressAdapterSpec
        declaration = SupportDeclaration(
            "fixture", ("fixture",), ("linux",), "implemented_unproven",
            frozenset({"http"}), None,
        )
        registry = IngressAdapterRegistry()
        registry.register(IngressAdapterSpec(declaration, object(), 20))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            registry.register(IngressAdapterSpec(declaration, object(), 10))

    def test_system_caddy_has_checked_in_exact_http_evidence(self):
        from sandbox.ingress.manifest import BUILTIN_INGRESS, built_in_ingress_registry
        registry = built_in_ingress_registry({"system-caddy": object()})
        self.assertTrue(registry.get("system-caddy").adoptable)
        self.assertEqual(registry.get("system-caddy").declaration.evidence_id,
                         "037-t044-ubuntu-2404")
        self.assertEqual(next(item for item in BUILTIN_INGRESS
                              if item.adapter_id == "system-caddy").evidence_id,
                         "037-t044-ubuntu-2404")
        self.assertEqual(registry.get("system-caddy").declaration.capabilities,
                         frozenset({"http"}))
        for adapter_id in ("sandbox-caddy", "herd-valet", "system-nginx",
                           "system-apache", "traefik"):
            self.assertFalse(registry.get(adapter_id).adoptable)

    def test_runtime_proof_material_is_not_an_input(self):
        from sandbox.ingress.manifest import built_in_ingress_registry

        for value in (
            {"system-caddy": "ubuntu-live-http-exact"},
            "ubuntu-live-http-exact",
            object(),
        ):
            with self.assertRaises(TypeError):
                built_in_ingress_registry(
                    {"system-caddy": object()}, proof_attestation=value,
                )

    def test_platform_and_capability_filter_precedes_side_effects(self):
        from sandbox.ingress.manifest import built_in_ingress_registry
        registry = built_in_ingress_registry()
        candidates = registry.candidates(platform="linux", capabilities={"wildcard"})
        self.assertTrue(candidates)
        self.assertTrue(all("wildcard" in item.declaration.capabilities for item in candidates))
        self.assertFalse(any(item.adapter_id == "unidentified" for item in candidates))


if __name__ == "__main__":
    unittest.main()
