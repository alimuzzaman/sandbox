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

    def test_implementation_without_live_evidence_is_not_adoptable(self):
        from sandbox.ingress.manifest import built_in_ingress_registry
        registry = built_in_ingress_registry({"system-caddy": object()})
        self.assertFalse(registry.get("system-caddy").adoptable)

    def test_platform_and_capability_filter_precedes_side_effects(self):
        from sandbox.ingress.manifest import built_in_ingress_registry
        registry = built_in_ingress_registry()
        candidates = registry.candidates(platform="linux", capabilities={"wildcard"})
        self.assertTrue(candidates)
        self.assertTrue(all("wildcard" in item.declaration.capabilities for item in candidates))
        self.assertFalse(any(item.adapter_id == "unidentified" for item in candidates))


if __name__ == "__main__":
    unittest.main()
