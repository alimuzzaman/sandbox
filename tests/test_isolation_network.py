import unittest
from datetime import datetime, timezone


class TestIsolationNetwork(unittest.TestCase):
    def test_default_has_no_route_and_drops_all_guest_initiated_traffic(self):
        from sandbox.isolation.network import NetworkPolicyCompiler
        result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30", ingress_port=8080,
        )
        self.assertIn('iifname "ve-sb-demo" counter drop', result["rules"][-1])
        self.assertFalse(result["default_route"])
        self.assertEqual(result["forward_policy"], "drop")
        self.assertIn("counter drop", result["rules"][-1])
        self.assertIn("ct state established,related accept", result["rules"][0])
        self.assertTrue(any('oifname "ve-sb-demo" counter drop' in rule
                            for rule in result["rules"]))
        self.assertEqual(result["chain_policies"], {
            "input": "accept", "output": "accept", "forward": "accept"})

    def test_active_public_grant_compiles_only_the_exact_broker_endpoint(self):
        from sandbox.isolation.models import EgressGrant
        from sandbox.isolation.network import NetworkPolicyCompiler
        grant = EgressGrant("wordpress-api", "sb-0123456789ab", "public_cidr_tcp",
                            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30",
            ingress_port=8080, grants=(grant,),
        )
        self.assertEqual(result["routes"], ())
        self.assertFalse(result["default_route"])
        self.assertEqual(result["broker"], {
            "address": "10.77.0.1", "port": 18443,
            "grant_ids": ("wordpress-api",),
        })
        self.assertTrue(any("10.77.0.2 ip daddr 10.77.0.1" in rule and
                            "tcp dport 18443" in rule for rule in result["rules"]))
        self.assertFalse(any("8.8.8.8" in rule for rule in result["rules"]))

    def test_grants_reject_broad_special_expired_foreign_and_unsafe_identity(self):
        from sandbox.isolation.models import EgressGrant
        from sandbox.isolation.network import NetworkPolicyCompiler
        compiler = NetworkPolicyCompiler(clock=lambda: datetime(2026, 1, 1,
                                                                  tzinfo=timezone.utc))
        for destination in ("0.0.0.0/0", "10.0.0.0/8", "127.0.0.0/8",
                            "169.254.169.254/32", "100.64.0.0/10"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                EgressGrant("api", "sb-0123456789ab", "public_cidr_tcp",
                            (destination,), (443,), "2999-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            EgressGrant('bad\nrule', "sb-0123456789ab", "public_cidr_tcp",
                        ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        expired = EgressGrant("expired", "sb-0123456789ab", "public_cidr_tcp",
                              ("8.8.8.8/32",), (443,), "2025-01-01T00:00:00Z")
        foreign = EgressGrant("foreign", "sb-fedcba987654", "public_cidr_tcp",
                              ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        for grant in (expired, foreign):
            with self.subTest(grant=grant.grant_id), self.assertRaises(ValueError):
                compiler.compile(machine_id="sb-0123456789ab", veth="ve-sb-demo",
                    host_address="10.77.0.1/30", guest_address="10.77.0.2/30",
                    ingress_port=8080, grants=(grant,))

    def test_hostname_grant_is_resolved_to_public_routes_and_revocation_removes_it(self):
        from sandbox.isolation.models import EgressGrant
        from sandbox.isolation.network import NetworkPolicyCompiler
        active = EgressGrant("api", "sb-0123456789ab", "hostname_https",
                             ("api.wordpress.org",), (443,), "2999-01-01T00:00:00Z")
        revoked = EgressGrant("old", "sb-0123456789ab", "hostname_https",
                              ("downloads.wordpress.org",), (443,),
                              "2999-01-01T00:00:00Z", revoked=True)
        active_result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30",
            ingress_port=8080, grants=(active, revoked),
        )
        self.assertEqual(active_result["grant_ids"], ("api",))
        self.assertEqual(active_result["routes"], ())
        result = NetworkPolicyCompiler().compile(
            machine_id="sb-0123456789ab", veth="ve-sb-demo",
            host_address="10.77.0.1/30", guest_address="10.77.0.2/30", ingress_port=8080,
            grants=(revoked,),
        )
        self.assertEqual(result["grant_ids"], ())
        self.assertEqual(result["routes"], ())
        self.assertIsNone(result["broker"])

    def test_allocator_gives_each_instance_a_unique_point_to_point_subnet(self):
        from sandbox.isolation.network import SubnetAllocator
        allocator = SubnetAllocator(); first = allocator.allocate("sb-0123456789ab")
        second = allocator.allocate("sb-fedcba987654", used=(first["subnet"],))
        self.assertNotEqual(first["subnet"], second["subnet"])
        self.assertLessEqual(len(first["veth"]), 15)


if __name__ == "__main__": unittest.main()
