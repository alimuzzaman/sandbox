from __future__ import annotations

import unittest


class TestIngressModels(unittest.TestCase):
    def test_exact_loopbacks_do_not_overlap_but_wildcard_does(self):
        from sandbox.ingress.models import ListenerEndpoint
        one = ListenerEndpoint("127.0.0.1", 80)
        dedicated = ListenerEndpoint("127.0.0.77", 80)
        wildcard = ListenerEndpoint("0.0.0.0", 80)
        self.assertFalse(one.overlaps(dedicated))
        self.assertTrue(wildcard.overlaps(one))
        self.assertTrue(wildcard.overlaps(dedicated))

    def test_protocol_and_port_are_part_of_overlap(self):
        from sandbox.ingress.models import ListenerEndpoint
        requested = ListenerEndpoint("127.0.0.77", 80)
        self.assertFalse(requested.overlaps(ListenerEndpoint("0.0.0.0", 443)))
        self.assertFalse(requested.overlaps(ListenerEndpoint("0.0.0.0", 80, "udp")))

    def test_ipv6_wildcard_overlaps_ipv4_only_when_dual_stack(self):
        from sandbox.ingress.models import ListenerEndpoint
        requested = ListenerEndpoint("127.0.0.77", 80)
        self.assertFalse(requested.overlaps(ListenerEndpoint("::", 80)))
        self.assertTrue(requested.overlaps(ListenerEndpoint("::", 80, dual_stack=True)))

    def test_observation_fingerprint_changes_with_listener_scope(self):
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint
        exact = IngressObservation("caddy", "Caddy", (ListenerEndpoint("127.0.0.1", 80),),
                                   "implemented_unproven")
        wildcard = IngressObservation("caddy", "Caddy", (ListenerEndpoint("0.0.0.0", 80),),
                                      "implemented_unproven")
        self.assertNotEqual(exact.fingerprint, wildcard.fingerprint)

    def test_route_transitions_to_healthy_only_for_matching_observation(self):
        from sandbox.ingress.models import RouteRecord
        route = RouteRecord.create(
            owner="/tmp/project::default", hostname="demo.test",
            backend={"address": "127.0.0.1", "port": 8123},
            adapter_id="caddy", protocols={"http"}, desired={"route": "demo"},
        ).with_applied({"route": "demo"})
        self.assertEqual(route.with_observed({"route": "demo"}).lifecycle, "healthy")
        self.assertEqual(route.with_observed({"route": "other"}).lifecycle, "drifted")

    def test_support_is_not_adoptable_without_live_evidence(self):
        from sandbox.ingress.models import SupportDeclaration
        declaration = SupportDeclaration(
            "nginx", ("nginx",), ("linux",), "implemented_unproven",
            frozenset({"http"}), None,
        )
        self.assertFalse(declaration.adoptable)


if __name__ == "__main__":
    unittest.main()
