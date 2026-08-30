from __future__ import annotations

import unittest


class TestDomainRegistry(unittest.TestCase):
    def test_registry_is_deterministic_and_duplicates_fail(self):
        from sandbox.network.registry import ResolverAdapterRegistry, ResolverAdapterSpec

        registry = ResolverAdapterRegistry()
        registry.register(ResolverAdapterSpec(
            "zeta", object(), ("zeta",), ("linux",), "detect_only", frozenset(), None, 20,
        ))
        registry.register(ResolverAdapterSpec(
            "alpha", object(), ("alpha",), ("linux",), "detect_only", frozenset(), None, 10,
        ))
        self.assertEqual([item.adapter_id for item in registry.items()], ["alpha", "zeta"])
        with self.assertRaisesRegex(ValueError, "duplicate resolver adapter"):
            registry.register(ResolverAdapterSpec(
                "alpha", object(), ("other",), ("linux",), "detect_only", frozenset(), None, 30,
            ))

    def test_proof_gate_prevents_unproven_adoption(self):
        from sandbox.network.registry import ResolverAdapterSpec

        unproven = ResolverAdapterSpec(
            "resolved", object(), ("resolved",), ("linux",),
            "implemented_unproven", frozenset({"exact"}), None, 10,
        )
        proven = ResolverAdapterSpec(
            "resolved", object(), ("resolved",), ("linux",),
            "adoptable", frozenset({"exact"}), "evidence-038-resolved", 10,
        )
        self.assertFalse(unproven.adoptable)
        self.assertTrue(proven.adoptable)

    def test_builtin_manifest_has_exact_declared_order_and_no_false_advertising(self):
        from sandbox.network.manifest import BUILTIN_RESOLVER_ADAPTERS

        ids = tuple(item.adapter_id for item in BUILTIN_RESOLVER_ADAPTERS)
        self.assertEqual(ids, (
            "systemd-resolved", "networkmanager", "macos", "dnsmasq",
            "herd-valet", "hosts", "external", "unknown",
        ))
        self.assertTrue(all(not item.adoptable for item in BUILTIN_RESOLVER_ADAPTERS
                            if item.support_tier != "external"))

    def test_systemd_resolved_uses_fixed_source_evidence(self):
        from sandbox.network.manifest import BUILTIN_RESOLVER_ADAPTERS, built_in_resolver_registry

        ordinary = built_in_resolver_registry({"systemd-resolved": object()})
        self.assertTrue(ordinary.get("systemd-resolved").adoptable)
        self.assertEqual(ordinary.get("systemd-resolved").evidence_id,
                         "038-t034-ubuntu-2404")
        self.assertEqual(BUILTIN_RESOLVER_ADAPTERS[0].support_tier, "adoptable")
        self.assertEqual(BUILTIN_RESOLVER_ADAPTERS[0].capabilities,
                         frozenset({"exact"}))


if __name__ == "__main__":
    unittest.main()
