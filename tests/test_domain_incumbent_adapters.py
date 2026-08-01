from __future__ import annotations

import unittest


class TestIncumbentDomainAdapters(unittest.TestCase):
    def test_herd_valet_plan_uses_documented_cli_only(self):
        from sandbox.network.adapters.incumbent import IncumbentResolverAdapter

        adapter = IncumbentResolverAdapter("herd", "/usr/local/bin/herd")
        plan = adapter.plan("demo.test", "127.0.0.77")
        self.assertEqual(plan["argv"][0], "/usr/local/bin/herd")
        self.assertNotIn("state", str(plan).lower())

    def test_wsl_and_unknown_are_never_mutation_adapters(self):
        from sandbox.network.adapters.external import ExternalResolverAdapter

        for platform in ("wsl2", "unknown"):
            adapter = ExternalResolverAdapter(platform)
            self.assertFalse(adapter.adoptable)
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                adapter.apply({})


if __name__ == "__main__":
    unittest.main()
