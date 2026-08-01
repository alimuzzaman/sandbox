import unittest


class TestIsolationNamespaces(unittest.TestCase):
    def test_nspawn_descriptor_has_private_identity_network_and_no_privilege(self):
        from sandbox.isolation.models import ManagedIsolationPolicy
        from sandbox.isolation.nspawn import NspawnCompiler
        policy = ManagedIsolationPolicy(
            1, "sb-0123456789ab", {"base": 200000, "count": 65536},
            {"path": "/images/demo.img"}, (), (), {"veth": "ve-sb-demo"},
            {"seccomp": "managed-v1"}, frozenset(), {"memory": 1024}, (),
        )
        result = NspawnCompiler().compile(policy)
        self.assertEqual(result["Exec"]["PrivateUsers"], "pick")
        self.assertEqual(result["Exec"]["DropCapability"], "all")
        self.assertEqual(result["Exec"]["NoNewPrivileges"], "yes")
        self.assertEqual(result["Network"]["Private"], "yes")
        self.assertIn("~@mount", result["Exec"]["SystemCallFilter"])


if __name__ == "__main__": unittest.main()
