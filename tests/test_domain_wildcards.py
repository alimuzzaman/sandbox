from __future__ import annotations

import unittest


class TestDomainWildcardPolicy(unittest.TestCase):
    def test_local_suffix_classification_does_not_treat_public_names_as_local(self):
        from sandbox.config.domains import suffix_class

        self.assertEqual(suffix_class("demo.test", "test"), "test")
        self.assertEqual(suffix_class("legacy.tst", "tst"), "legacy_private")
        self.assertEqual(suffix_class("demo.example.com", "com"), "public")

    def test_exact_binding_is_distinct_from_declared_wildcard_zone(self):
        from sandbox.network.models import ResolutionBinding

        exact = ResolutionBinding.create(
            kind="exact", name="site.test", target="127.0.0.77",
            adapter_id="resolved", owners=("owner",), desired={},
        )
        zone = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77",
            adapter_id="resolved", owners=("owner",), desired={},
        )
        self.assertNotEqual(exact.binding_id, zone.binding_id)

    def test_authority_wildcard_is_bounded_to_declared_zone(self):
        from sandbox.network.authority import DnsmasqAuthority
        from sandbox.network.models import ResolutionBinding

        zone = ResolutionBinding.create(
            kind="zone", name="*.site.test", target="127.0.0.77",
            adapter_id="resolved", owners=("owner",), desired={},
        )
        text = DnsmasqAuthority.render_config(
            address="127.0.0.54", port=5300, bindings=(zone,),
            pid_file="/tmp/pid", log_file="/tmp/log",
        )
        self.assertIn("address=/site.test/127.0.0.77", text)
        self.assertNotIn("address=/test/", text)
        self.assertNotIn("address=/com/", text)


if __name__ == "__main__":
    unittest.main()
