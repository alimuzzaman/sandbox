from __future__ import annotations

import unittest


class Detector:
    def __init__(self, observations): self.observations = observations
    def observe(self): return tuple(self.observations)


class TestIngressSelection(unittest.TestCase):
    def test_split_http_https_owners_are_refused(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint
        observations = (
            IngressObservation("system-nginx", "nginx",
                               (ListenerEndpoint("0.0.0.0", 80),),
                               "implemented_unproven", frozenset({"http"})),
            IngressObservation("system-caddy", "caddy",
                               (ListenerEndpoint("0.0.0.0", 443),),
                               "implemented_unproven", frozenset({"https"})),
        )
        selection = IngressService(
            detector=Detector(observations), registry=built_in_ingress_registry(),
        ).select(required_protocols=("http", "https"))
        self.assertEqual(selection.reason_code, "split_ingress_owners")
        self.assertIsNone(selection.adapter_id)

    def test_unproven_implementation_is_not_selected(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry
        selection = IngressService(
            detector=Detector(()),
            registry=built_in_ingress_registry({"sandbox-caddy": object()}),
        ).select(required_protocols=("http",))
        self.assertEqual(selection.reason_code, "no_live_proven_ingress")


if __name__ == "__main__": unittest.main()
