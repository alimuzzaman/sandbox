from __future__ import annotations

import unittest


class Observer:
    def __init__(self, endpoints): self.endpoints = endpoints
    def snapshot(self): return tuple(self.endpoints)


class TestIngressDetection(unittest.TestCase):
    def test_kernel_listener_survives_missing_process_evidence(self):
        from sandbox.ingress.detection import IngressDetector
        from sandbox.ingress.models import ListenerEndpoint
        endpoint = ListenerEndpoint("0.0.0.0", 80, socket_id="42")
        result = IngressDetector(listener_observer=Observer((endpoint,))).observe()
        self.assertEqual(result[0].adapter_id, "unidentified")
        self.assertEqual(result[0].endpoints[0].socket_id, "42")

    def test_product_evidence_classifies_but_does_not_create_listener_truth(self):
        from sandbox.ingress.detection import IngressDetector
        from sandbox.ingress.models import ListenerEndpoint
        nginx = ListenerEndpoint("0.0.0.0", 80, process={"command": "nginx"})
        result = IngressDetector(listener_observer=Observer((nginx,))).observe()
        self.assertEqual(result[0].adapter_id, "system-nginx")
        self.assertEqual(result[0].endpoints, (nginx,))
        self.assertEqual(IngressDetector(listener_observer=Observer(())).observe(), ())

    def test_non_ingress_ports_are_not_reported_as_ingress_products(self):
        from sandbox.ingress.detection import IngressDetector
        from sandbox.ingress.models import ListenerEndpoint
        result = IngressDetector(listener_observer=Observer((
            ListenerEndpoint("0.0.0.0", 5432, process={"command": "postgres"}),
        ))).observe()
        self.assertEqual(result, ())

    def test_sandbox_owner_requires_container_and_process_evidence(self):
        from sandbox.ingress.detection import IngressDetector
        from sandbox.ingress.models import ListenerEndpoint
        endpoint = ListenerEndpoint(
            "127.0.0.77", 80, process={"command": "caddy"},
            service={"container": "sandbox-proxy"}, owner_confidence="proven",
        )
        result = IngressDetector(listener_observer=Observer((endpoint,))).observe()
        self.assertEqual(result[0].adapter_id, "sandbox-caddy")


if __name__ == "__main__": unittest.main()
