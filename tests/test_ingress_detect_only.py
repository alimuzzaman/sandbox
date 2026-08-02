"""Public listener evidence may identify, but never mutate, unsupported products."""

import unittest


class Observer:
    def __init__(self, endpoints): self.endpoints = tuple(endpoints)
    def snapshot(self): return self.endpoints


class TestIngressDetectOnly(unittest.TestCase):
    def observe(self, command, *, platform="linux"):
        from sandbox.ingress.detection import IngressDetector
        from sandbox.ingress.models import ListenerEndpoint
        return IngressDetector(
            listener_observer=Observer((ListenerEndpoint(
                "0.0.0.0", 80, process={"command": command},
            ),)),
            platform=platform,
        ).observe()[0]

    def test_named_products_use_public_process_evidence_and_remain_non_adoptable(self):
        from sandbox.ingress.manifest import built_in_ingress_registry
        registry = built_in_ingress_registry()
        for command, adapter_id, platform, tier in (
            ("nginx-proxy-manager", "nginx-proxy-manager", "linux", "credential_pending"),
            ("ddev-router", "ddev", "linux", "detect_only"),
            ("xampp", "xampp", "linux", "detect_only"),
            ("laragon", "laragon", "linux", "outside_platform"),
            ("wamp", "wamp", "darwin", "outside_platform"),
            ("local", "local", "linux", "outside_platform"),
        ):
            with self.subTest(adapter_id=adapter_id, platform=platform):
                observation = self.observe(command, platform=platform)
                self.assertEqual((observation.adapter_id, observation.support_tier),
                                 (adapter_id, tier))
                self.assertFalse(registry.get(adapter_id).adoptable)

    def test_detect_only_adapter_has_no_route_mutation_surface(self):
        from sandbox.ingress.adapters.detect_only import DetectOnlyAdapter
        from sandbox.ingress.models import ListenerEndpoint
        adapter = DetectOnlyAdapter("fixture", products=("fixture",), platforms=("linux",))
        evidence = adapter.detect((ListenerEndpoint("127.0.0.1", 80),), platform="linux")
        self.assertEqual(evidence["mode"], "detect_only")
        self.assertFalse(evidence["route_mutations"])
        for operation in ("plan_route", "validate_current", "stage_candidate", "activate", "cleanup"):
            self.assertFalse(hasattr(adapter, operation))


if __name__ == "__main__": unittest.main()
