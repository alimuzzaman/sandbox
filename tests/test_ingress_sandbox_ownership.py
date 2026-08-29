"""Sandbox must recognize its OWN published ingress (037 US1-3, FR-002).

Docker publishes the proxy's ports, so listener evidence attributes them to the
container runtime's helper process. Read literally, that made the service call
its own proxy a foreign conflict and refuse to reuse it.
"""

from __future__ import annotations

import unittest


class Detector:
    def __init__(self, observations):
        self.observations = observations

    def observe(self):
        return tuple(self.observations)


class Probe:
    def __init__(self, verdict="conflict"):
        self.verdict = verdict

    def check(self, _endpoint):
        return self.verdict


def _runtime_publisher():
    """What the kernel shows when a container runtime publishes the proxy."""
    from sandbox.ingress.models import IngressObservation, ListenerEndpoint

    return (IngressObservation(
        "unidentified", "Unidentified listener",
        (ListenerEndpoint("0.0.0.0", 80, "tcp",
                          process={"command": "OrbStack Helper", "pid": "4864"},
                          owner_confidence="probable"),
         ListenerEndpoint("0.0.0.0", 443, "tcp",
                          process={"command": "OrbStack Helper", "pid": "4864"},
                          owner_confidence="probable")),
        "unidentified", frozenset()),)


def _service(*, owner, observations=None, probe="conflict", health=None):
    from sandbox.application.ingress_service import IngressService
    from sandbox.ingress.manifest import built_in_ingress_registry

    return IngressService(
        detector=Detector(_runtime_publisher() if observations is None else observations),
        registry=built_in_ingress_registry(),
        bind_probe=Probe(probe),
        sandbox_owner=owner,
        caddy_health=(lambda: health) if health is not None else None,
    )


class TestSandboxOwnedEndpoints(unittest.TestCase):
    def test_failed_project_route_context_never_widens_to_machine_routes(self):
        from pathlib import Path
        from unittest import mock
        import sandbox_core as sc
        from sandbox.application.context import ingress_service
        from sandbox.ingress.manifest import built_in_ingress_registry

        failed = {"ok": False, "domains": (), "mutated": False,
                  "reason": {"code": "project_route_context_unavailable"}}
        with mock.patch.object(sc, "sandbox_base", return_value=Path("/tmp/sandbox-test")), \
             mock.patch("sandbox.core._domains.sandbox_caddy_health") as health:
            service = ingress_service(
                {}, detector=Detector(_runtime_publisher()),
                registry=built_in_ingress_registry(), bind_probe=Probe("conflict"),
                sandbox_owner=lambda _endpoint: True,
                caddy_health_context=failed,
            )
            detected = service.detect()
        health.assert_not_called()
        self.assertEqual(detected["state"], "degraded")
        self.assertEqual(detected["reason"]["code"],
                         "project_route_context_unavailable")

    def test_owned_endpoints_are_not_reported_as_conflicts(self):
        detected = _service(owner=lambda _endpoint: True).detect()
        states = {item["port"]: item["state"] for item in detected["requested_endpoints"]}
        self.assertEqual(states, {80: "sandbox_owned", 443: "sandbox_owned"})

    def test_foreign_endpoints_are_still_reported_as_conflicts(self):
        detected = _service(owner=lambda _endpoint: False).detect()
        states = {item["port"]: item["state"] for item in detected["requested_endpoints"]}
        self.assertEqual(states, {80: "overlapping", 443: "overlapping"})

    def test_sandbox_owned_route_failure_degrades_and_recovery_is_ready(self):
        degraded = _service(
            owner=lambda _endpoint: True,
            health={"ok": False, "state": "degraded", "mutated": False,
                    "routes": ({"hostname": "demo.tst", "secure": False,
                                "configured": True, "serving": False},),
                    "reason": {"code": "sandbox_caddy_route_unreachable",
                               "message": "Sandbox Caddy route probe failed for demo.tst."}},
        ).detect()
        self.assertFalse(degraded["ok"])
        self.assertEqual(degraded["state"], "degraded")
        self.assertEqual(degraded["reason"]["code"],
                         "sandbox_caddy_route_unreachable")
        recovered = _service(
            owner=lambda _endpoint: True,
            health={"ok": True, "state": "ready", "mutated": False,
                    "routes": ({"hostname": "demo.tst", "secure": False,
                                "configured": True, "serving": True},),
                    "reason": {"code": "sandbox_caddy_ready"}},
        ).detect()
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["state"], "ready")

    def test_caddy_health_exception_is_sanitized_and_bounded(self):
        secret = "token=" + "x" * 5000
        service = _service(owner=lambda _endpoint: True)
        service.caddy_health = lambda: (_ for _ in ()).throw(RuntimeError(secret))
        detected = service.detect()
        self.assertEqual(detected["state"], "degraded")
        self.assertEqual(detected["reason"]["code"],
                         "sandbox_caddy_health_unavailable")
        self.assertNotIn("token=", repr(detected))
        self.assertLess(len(detected["reason"]["message"]), 100)

    def test_invalid_caddy_health_return_shape_is_sanitized(self):
        service = _service(owner=lambda _endpoint: True)
        service.caddy_health = lambda: "bad"
        detected = service.detect()
        self.assertEqual(detected["state"], "degraded")
        self.assertEqual(detected["reason"]["code"],
                         "sandbox_caddy_health_unavailable")

        service.caddy_health = lambda: {
            "ok": False, "state": "degraded", "mutated": False,
            "reason": {"code": "sandbox_caddy_route_unreachable",
                       "message": "token=must-not-escape"},
        }
        missing_fields = service.detect()
        self.assertEqual(missing_fields["reason"]["code"],
                         "sandbox_caddy_health_unavailable")
        self.assertNotIn("token=", repr(missing_fields))

    def test_exact_proven_owner_is_a_true_listener_conflict(self):
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint

        exact = (IngressObservation(
            "unidentified", "Unidentified listener",
            (ListenerEndpoint("127.0.0.77", 80, process={"pid": "42"},
                              owner_confidence="proven"),),
            "unidentified", frozenset()),)
        detected = _service(owner=lambda _endpoint: False,
                            observations=exact).detect()
        endpoint = next(item for item in detected["requested_endpoints"]
                        if item["port"] == 80)
        self.assertEqual(endpoint["state"], "conflict")
        self.assertEqual(endpoint["reason"]["code"], "listener_conflict")

    def test_unavailable_kernel_evidence_keeps_wildcard_as_overlap(self):
        detected = _service(owner=lambda _endpoint: False,
                            probe="unavailable").detect()
        self.assertEqual(
            {item["state"] for item in detected["requested_endpoints"]},
            {"overlapping"},
        )
        self.assertEqual(detected["reason"]["code"], "listener_overlap")

    def test_detection_stays_read_only(self):
        service = _service(owner=lambda _endpoint: True)
        self.assertFalse(service.detect()["mutated"])

    def test_default_probe_claims_nothing(self):
        """Without an injected probe the service must not assume ownership."""
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry

        service = IngressService(
            detector=Detector(_runtime_publisher()),
            registry=built_in_ingress_registry(), bind_probe=Probe("conflict"),
        )
        states = {item["port"]: item["state"]
                  for item in service.detect()["requested_endpoints"]}
        self.assertEqual(states, {80: "overlapping", 443: "overlapping"})


class TestOwnershipProbe(unittest.TestCase):
    def test_probe_only_answers_for_the_proxy_bind_address_and_ports(self):
        from sandbox.core import _domains

        self.assertFalse(_domains.proxy_endpoint_owned("127.0.0.1", 80))
        self.assertFalse(_domains.proxy_endpoint_owned(_domains.PROXY_BIND_IP, 8188))

    def test_probe_is_false_when_the_proxy_is_not_running(self):
        from unittest import mock

        from sandbox.core import _domains

        with mock.patch.object(_domains, "_proxy_container_running", return_value=False), \
             mock.patch.object(_domains.subprocess, "run") as run:
            self.assertFalse(
                _domains.proxy_endpoint_owned(_domains.PROXY_BIND_IP, 80))
        run.assert_not_called()

    def test_probe_reads_the_proxy_project_port_mapping(self):
        from unittest import mock

        from sandbox.core import _domains

        mapping = mock.Mock(returncode=0, stdout="127.0.0.77:80\n", stderr="")
        with mock.patch.object(_domains, "_proxy_container_running", return_value=True), \
             mock.patch.object(_domains.subprocess, "run", return_value=mapping):
            self.assertTrue(
                _domains.proxy_endpoint_owned(_domains.PROXY_BIND_IP, 80))

    def test_probe_is_false_when_the_project_publishes_nothing(self):
        from unittest import mock

        from sandbox.core import _domains

        mapping = mock.Mock(returncode=1, stdout="", stderr="no container")
        with mock.patch.object(_domains, "_proxy_container_running", return_value=True), \
             mock.patch.object(_domains.subprocess, "run", return_value=mapping):
            self.assertFalse(
                _domains.proxy_endpoint_owned(_domains.PROXY_BIND_IP, 443))


if __name__ == "__main__":
    unittest.main()
