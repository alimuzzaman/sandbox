from __future__ import annotations

import subprocess
import unittest


class TestIncumbentDomainAdapters(unittest.TestCase):
    def test_herd_valet_plan_uses_documented_cli_only(self):
        from sandbox.network.adapters.incumbent import IncumbentResolverAdapter

        adapter = IncumbentResolverAdapter("herd", "/usr/local/bin/herd")
        plan = adapter.plan("demo.test", "127.0.0.77")
        self.assertEqual(plan["argv"][0], "/usr/local/bin/herd")
        self.assertNotIn("state", str(plan).lower())

    def test_incumbent_dns_integration_is_read_only_and_ingress_owns_mutation(self):
        from sandbox.network.adapters.incumbent import IncumbentResolverAdapter

        class Process:
            def run(self, argv, *, timeout):
                self.argv = tuple(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")

        process = Process()
        adapter = IncumbentResolverAdapter("valet", "/usr/bin/valet", process)
        plan = adapter.plan("demo.test", "127.0.0.77")
        result = adapter.apply(plan)
        self.assertTrue(result["ok"])
        self.assertFalse(result["mutated"])
        self.assertEqual(plan["mutation_owner"], "ingress")
        self.assertEqual(process.argv, ("/usr/bin/valet", "status"))

    def test_wsl_and_unknown_are_never_mutation_adapters(self):
        from sandbox.network.adapters.external import ExternalResolverAdapter

        for platform in ("wsl2", "unknown"):
            adapter = ExternalResolverAdapter(platform)
            self.assertFalse(adapter.adoptable)
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                adapter.apply({})


if __name__ == "__main__":
    unittest.main()
