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
        self.assertEqual(result["Exec"]["PrivateUsers"], "200000:65536")
        self.assertIn("CAP_SYS_ADMIN", result["Exec"]["DropCapability"])
        self.assertIn("CAP_NET_RAW", result["Exec"]["DropCapability"])
        self.assertNotIn("CAP_NET_BIND_SERVICE", result["Exec"]["DropCapability"])
        self.assertEqual(result["Exec"]["AmbientCapability"], ())
        self.assertEqual(result["Exec"]["NoNewPrivileges"], "yes")
        self.assertEqual(result["Network"]["Private"], "yes")
        self.assertIn("~@mount", result["Exec"]["SystemCallFilter"])
        self.assertEqual(result["Exec"]["PrivateUsersDelegate"], "0")
        self.assertEqual(result["Network"]["VirtualEthernetExtra"], "ve-sb-demo:host0")
        self.assertEqual(result["Files"]["ReadOnly"], "no")
        self.assertEqual(result["Service"]["DevicePolicy"], "closed")
        self.assertNotIn("/dev/kmsg rw", result["Service"]["DeviceAllow"])
        self.assertEqual(result["Security"]["AppArmorProfile"],
                         "sandbox-native-sb-0123456789ab")


if __name__ == "__main__": unittest.main()
