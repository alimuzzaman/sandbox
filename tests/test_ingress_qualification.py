from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest import mock


class Detector:
    def __init__(self, observations):
        self.observations = tuple(observations)
        self.calls = 0

    def observe(self):
        self.calls += 1
        return self.observations


class Adapter:
    def __init__(self, ready=True):
        self._ready = ready
        self.ready_calls = 0

    def ready(self, authority=None):
        self.ready_calls += 1
        self.authority = authority
        return self._ready


def caddy_observation(*, address="::", confidence="proven", command="caddy",
                      pid=4242, start="77", executable=None,
                      executable_digest="e" * 64, socket_id="424280"):
    from sandbox.ingress.models import IngressObservation, ListenerEndpoint

    return IngressObservation(
        "system-caddy", "Caddy",
        (ListenerEndpoint(
            address, 80, socket_id=socket_id,
            process={
                "pid": pid,
                "start": start,
                "executable": executable or f"/usr/bin/{command}",
                "executable_digest": executable_digest,
                "command": command,
            },
            service={"unit": "caddy.service"},
            owner_confidence=confidence,
        ),),
        "adoptable", frozenset({"http"}),
        product_identity={"evidence": "process-best-effort"},
    )


def selection(*, observations=None, adapter=None, platform="linux",
              protocols=("http",), capabilities=()):
    from sandbox.application.ingress_service import IngressService
    from sandbox.ingress.manifest import built_in_ingress_registry

    detector = Detector(observations or (caddy_observation(),))
    implementation = adapter or Adapter()
    service = IngressService(
        detector=detector,
        registry=built_in_ingress_registry({"system-caddy": implementation}),
        platform=platform,
    )
    return service.select(
        required_protocols=protocols,
        required_capabilities=capabilities,
        pin="system-caddy",
        pin_source="project",
    ), detector, implementation


class TestProductionIngressQualification(unittest.TestCase):
    def test_registry_has_one_checked_in_exact_http_qualification(self):
        from sandbox.ingress.manifest import built_in_ingress_registry
        from sandbox.ingress.qualification import SYSTEM_CADDY_QUALIFICATION

        registry = built_in_ingress_registry({"system-caddy": Adapter()})
        caddy = registry.get("system-caddy")

        self.assertTrue(caddy.adoptable)
        self.assertEqual(caddy.declaration.evidence_id,
                         "037-t044-ubuntu-2404")
        self.assertEqual(caddy.declaration.platforms, ("linux",))
        self.assertEqual(caddy.declaration.capabilities, frozenset({"http"}))
        self.assertEqual(SYSTEM_CADDY_QUALIFICATION.evidence_id,
                         "037-t044-ubuntu-2404")
        for adapter_id in (
            "sandbox-caddy", "herd-valet", "system-nginx",
            "system-apache", "traefik",
        ):
            self.assertFalse(registry.get(adapter_id).adoptable)

    def test_registry_has_no_runtime_proof_input(self):
        from sandbox.ingress.manifest import built_in_ingress_registry

        parameters = inspect.signature(built_in_ingress_registry).parameters
        self.assertNotIn("proof_attestation", parameters)
        for value in (
            "037-t044-ubuntu-2404",
            {"system-caddy": "037-t044-ubuntu-2404"},
            object(),
        ):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                built_in_ingress_registry(
                    {"system-caddy": Adapter()}, proof_attestation=value,
                )

    def test_linux_observed_system_caddy_exact_http_is_selected(self):
        observed, detector, adapter = selection()

        self.assertEqual(observed.adapter_id, "system-caddy")
        self.assertEqual(observed.reason_code, "selected")
        self.assertEqual(observed.required_protocols, frozenset({"http"}))
        self.assertEqual(detector.calls, 1)
        self.assertEqual(adapter.ready_calls, 1)
        self.assertEqual(adapter.authority, {
            "pid": 4242,
            "start": "77",
            "executable_digest": "e" * 64,
            "socket_ids": ("424280",),
            "listen_address": "::",
            "listen_port": 80,
        })

    def test_unqualified_platform_protocol_and_capability_are_refused(self):
        cases = (
            ({"platform": "darwin"}, "ingress_control_unavailable"),
            ({"protocols": ("https",)}, "detected_not_adoptable"),
            ({"capabilities": ("wildcard",)}, "detected_not_adoptable"),
        )
        for arguments, reason in cases:
            with self.subTest(arguments=arguments):
                observed, _detector, _adapter = selection(**arguments)
                self.assertIsNone(observed.adapter_id)
                self.assertEqual(observed.reason_code, reason)

    def test_changed_unidentified_and_unproven_owners_are_refused(self):
        for observation in (
            caddy_observation(command="nginx"),
            caddy_observation(confidence="probable"),
            caddy_observation(pid="not-a-pid"),
            caddy_observation(start="not-a-start"),
            caddy_observation(executable="caddy"),
            caddy_observation(executable="/tmp/caddy"),
            caddy_observation(executable="/home/operator/bin/caddy"),
            caddy_observation(executable_digest="not-a-digest"),
            caddy_observation(socket_id="not-an-inode"),
        ):
            with self.subTest(process=observation.endpoints[0].process):
                observed, _detector, _adapter = selection(
                    observations=(observation,),
                )
                self.assertIsNone(observed.adapter_id)
                self.assertEqual(observed.reason_code,
                                 "ingress_control_unavailable")

    def test_two_caddy_processes_cannot_share_one_qualified_selection(self):
        first = caddy_observation(address="0.0.0.0", pid=4242, start="77",
                                  socket_id="424280")
        second_endpoint = caddy_observation(
            address="::", pid=5252, start="88", socket_id="525280",
        ).endpoints[0]
        from sandbox.ingress.models import IngressObservation
        conflicting = IngressObservation(
            first.adapter_id, first.product,
            first.endpoints + (second_endpoint,), first.support_tier,
            first.capabilities, first.product_identity,
        )

        observed, _detector, adapter = selection(observations=(conflicting,))

        self.assertIsNone(observed.adapter_id)
        self.assertEqual(observed.reason_code, "ingress_control_unavailable")
        self.assertEqual(adapter.ready_calls, 0)

    def test_service_listener_mismatch_fails_before_dns_mutation(self):
        from sandbox.application.clean_url_service import CleanUrlService

        events = []

        class MismatchedAdapter(Adapter):
            def ready(self, authority=None):
                self.ready_calls += 1
                self.authority = authority
                return False

        class Domains:
            def ingress_policy(self, *_args, **_kwargs):
                return {"pin": "system-caddy", "pin_source": "project"}

            def apply(self, *_args, **_kwargs):
                events.append("dns-mutation")
                raise AssertionError("DNS must not run before exact listener qualification")

        ingress_selection, detector, adapter = selection(adapter=MismatchedAdapter())
        self.assertIsNone(ingress_selection.adapter_id)
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry
        ingress = IngressService(
            detector=detector,
            registry=built_in_ingress_registry({"system-caddy": adapter}),
            platform="linux",
        )
        result = CleanUrlService(ingress=ingress, domains=Domains()).apply(
            "/tmp/project", backend={"address": "127.0.0.1", "port": 8123},
            fallback_url="http://localhost:8123",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "ingress_control_unavailable")
        self.assertEqual(events, [])

    def test_missing_helper_import_readiness_is_refused(self):
        observed, _detector, adapter = selection(adapter=Adapter(ready=False))

        self.assertIsNone(observed.adapter_id)
        self.assertEqual(observed.reason_code, "ingress_control_unavailable")
        self.assertEqual(adapter.ready_calls, 1)

    def test_helper_observation_failure_is_fail_closed(self):
        class FailedAdapter(Adapter):
            def ready(self, authority=None):
                self.ready_calls += 1
                raise RuntimeError("synthetic helper failure")

        observed, _detector, adapter = selection(adapter=FailedAdapter())

        self.assertIsNone(observed.adapter_id)
        self.assertEqual(observed.reason_code, "ingress_control_unavailable")
        self.assertEqual(adapter.ready_calls, 1)

    def test_foreign_listener_collision_is_refused_before_adapter_control(self):
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint

        foreign = IngressObservation(
            "system-nginx", "nginx",
            (ListenerEndpoint(
                "0.0.0.0", 80, socket_id="foreign",
                process={"command": "nginx"}, owner_confidence="proven",
            ),),
            "implemented_unproven", frozenset({"http"}),
        )
        observed, _detector, adapter = selection(
            observations=(caddy_observation(), foreign),
        )

        self.assertIsNone(observed.adapter_id)
        self.assertEqual(observed.reason_code, "foreign_endpoint_owner")
        self.assertEqual(adapter.ready_calls, 0)

    def test_domains_use_reaches_source_qualified_provider_without_identity_change(self):
        from sandbox.commands.domains import _use_provider
        from sandbox.core import _config, _domains

        local = {"unrelated": {"kept": True}}
        policy = type("Policy", (), {"ingress_policy": lambda *_args, **_kwargs: {}})()
        args = SimpleNamespace(
            tld="system-caddy", resolver=None, project_dir="/tmp/project",
            label="default",
        )
        with mock.patch.object(_config, "_local_yaml", return_value=local), \
             mock.patch.object(_config, "_write_local_yaml") as write, \
             mock.patch.object(_domains, "clean_url_mode", return_value="system-caddy"), \
             mock.patch("sandbox.application.context.domain_service", return_value=policy):
            result = _use_provider(args)

        self.assertEqual(result["provider"], "system-caddy")
        self.assertEqual(result["reason"]["code"], "provider_selected")
        write.assert_called_once_with({
            "unrelated": {"kept": True},
            "domains": {"ingress": "system-caddy"},
        })
        self.assertNotIn("hostname", write.call_args.args[0]["domains"])


if __name__ == "__main__":
    unittest.main()
