import unittest


class Detector:
    def __init__(self, observations): self.observations = observations
    def observe(self): return self.observations


class TestIngressNonInteractive(unittest.TestCase):
    def test_credential_pending_pin_never_prompts_or_mutates(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint
        observation = IngressObservation(
            "nginx-proxy-manager", "nginx-proxy-manager",
            (ListenerEndpoint("0.0.0.0", 80),), "credential_pending",
            frozenset({"http", "https", "wildcard"}),
        )
        service = IngressService(
            detector=Detector((observation,)), registry=built_in_ingress_registry(),
        )
        selection = service.select(pin="nginx-proxy-manager", pin_source="project")
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "credential_pending")
        self.assertEqual(selection.pin_source, "project")


if __name__ == "__main__": unittest.main()
