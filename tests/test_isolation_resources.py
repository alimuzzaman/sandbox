import unittest


class TestIsolationResources(unittest.TestCase):
    def test_every_exhaustion_dimension_has_a_finite_limit(self):
        from sandbox.isolation.resources import ResourcePolicyCompiler
        result = ResourcePolicyCompiler().compile({})
        for key in ("cpu_percent", "memory_bytes", "pids", "runtime_seconds",
                    "disk_bytes", "inodes", "fds", "connections", "io_weight"):
            self.assertGreater(result[key], 0)
        self.assertEqual(result["systemd"]["MemorySwapMax"], 0)

    def test_invalid_unbounded_or_boolean_limits_fail(self):
        from sandbox.isolation.resources import ResourcePolicyCompiler
        for value in (0, True, 10**20):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ResourcePolicyCompiler().compile({"memory_bytes": value})


if __name__ == "__main__": unittest.main()
