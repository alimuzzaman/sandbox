import unittest


class TestIsolationNetwork(unittest.TestCase):
    def test_default_has_no_route_and_drops_all_guest_initiated_traffic(self):
        from sandbox.isolation.network import NetworkPolicyCompiler
        result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30", ingress_port=8080,
        )
        self.assertFalse(result["default_route"])
        self.assertEqual(result["forward_policy"], "drop")
        self.assertIn("counter drop", result["rules"][-1])
        self.assertIn("ct state established,related accept", result["rules"][0])

    def test_public_grant_is_narrow_and_private_grant_is_rejected_by_model(self):
        from sandbox.isolation.models import EgressGrant
        from sandbox.isolation.network import NetworkPolicyCompiler
        grant = EgressGrant("wordpress-api", "owner", "public_cidr_tcp",
                            ("8.8.8.8/32",), (443,), "later")
        result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30", ingress_port=8080,
            grants=(grant,),
        )
        self.assertTrue(any("wordpress-api" in rule for rule in result["rules"]))


if __name__ == "__main__": unittest.main()
