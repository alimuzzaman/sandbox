from __future__ import annotations

import unittest


class Detector:
    def __init__(self, observations): self.observations = observations
    def observe(self): return tuple(self.observations)


class Adapter:
    def __init__(self, ready=True):
        self._ready = ready

    def ready(self):
        return self._ready


def registry(*declarations):
    from sandbox.ingress.registry import IngressAdapterRegistry, IngressAdapterSpec
    value = IngressAdapterRegistry()
    for order, declaration in enumerate(declarations):
        value.register(IngressAdapterSpec(declaration, Adapter(), order))
    return value


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

    def test_foreign_overlap_blocks_sandbox_caddy_even_when_it_is_proven(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        declaration = SupportDeclaration(
            "sandbox-caddy", ("sandbox-caddy",), ("linux",), "sandbox_owned",
            frozenset({"http"}), "proof",
        )
        observation = IngressObservation(
            "system-nginx", "nginx", (ListenerEndpoint("0.0.0.0", 80),),
            "implemented_unproven", frozenset({"http"}),
        )
        selection = IngressService(
            detector=Detector((observation,)), registry=registry(declaration),
        ).select(required_protocols=("http",))
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "foreign_endpoint_owner")

    def test_accepted_addresses_are_concrete_and_match_every_required_port(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        declaration = SupportDeclaration(
            "fixture", ("fixture",), ("linux",), "adoptable",
            frozenset({"http", "https"}), "proof",
        )
        observation = IngressObservation(
            "fixture", "fixture", (
                ListenerEndpoint("0.0.0.0", 80),
                ListenerEndpoint("127.0.0.1", 443),
            ), "adoptable", frozenset({"http", "https"}),
        )
        selection = IngressService(
            detector=Detector((observation,)), registry=registry(declaration),
        ).select(required_protocols=("http", "https"))
        self.assertEqual(selection.accepted_addresses, ("127.0.0.1",))
        self.assertNotIn("0.0.0.0", selection.accepted_addresses)

    def test_unavailable_adapter_control_surface_is_not_selected(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        from sandbox.ingress.registry import IngressAdapterRegistry, IngressAdapterSpec
        declaration = SupportDeclaration(
            "fixture", ("fixture",), ("linux",), "adoptable",
            frozenset({"http"}), "proof",
        )
        value = IngressAdapterRegistry()
        value.register(IngressAdapterSpec(declaration, Adapter(ready=False), 1))
        observation = IngressObservation(
            "fixture", "fixture", (ListenerEndpoint("127.0.0.1", 80),),
            "adoptable", frozenset({"http"}),
        )
        selection = IngressService(
            detector=Detector((observation,)), registry=value,
        ).select(required_protocols=("http",))
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "ingress_control_unavailable")

    def test_system_caddy_requires_proven_process_ownership(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        declaration = SupportDeclaration(
            "system-caddy", ("caddy",), ("linux",), "adoptable",
            frozenset({"http"}), "proof",
        )
        observation = IngressObservation(
            "system-caddy", "Caddy",
            (ListenerEndpoint("127.0.0.1", 80, owner_confidence="probable"),),
            "adoptable", frozenset({"http"}),
        )
        selection = IngressService(
            detector=Detector((observation,)), registry=registry(declaration),
        ).select(required_protocols=("http",))
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "ingress_control_unavailable")

    def test_system_caddy_public_or_wildcard_listener_is_never_adopted(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        declaration = SupportDeclaration(
            "system-caddy", ("caddy",), ("linux",), "adoptable",
            frozenset({"http"}), "proof",
        )
        for address in ("0.0.0.0", "10.0.0.4"):
            observation = IngressObservation(
                "system-caddy", "Caddy",
                (ListenerEndpoint(address, 80, owner_confidence="proven"),),
                "adoptable", frozenset({"http"}),
            )
            selection = IngressService(
                detector=Detector((observation,)), registry=registry(declaration),
            ).select(required_protocols=("http",))
            self.assertIsNone(selection.adapter_id)
            self.assertEqual(selection.reason_code, "ingress_control_unavailable")


if __name__ == "__main__": unittest.main()
