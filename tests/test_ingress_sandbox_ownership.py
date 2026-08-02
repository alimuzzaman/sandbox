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


def _service(*, owner):
    from sandbox.application.ingress_service import IngressService
    from sandbox.ingress.manifest import built_in_ingress_registry

    return IngressService(
        detector=Detector(_runtime_publisher()),
        registry=built_in_ingress_registry(),
        bind_probe=Probe("conflict"),
        sandbox_owner=owner,
    )


class TestSandboxOwnedEndpoints(unittest.TestCase):
    def test_owned_endpoints_are_not_reported_as_conflicts(self):
        detected = _service(owner=lambda _endpoint: True).detect()
        states = {item["port"]: item["state"] for item in detected["requested_endpoints"]}
        self.assertEqual(states, {80: "sandbox_owned", 443: "sandbox_owned"})

    def test_foreign_endpoints_are_still_reported_as_conflicts(self):
        detected = _service(owner=lambda _endpoint: False).detect()
        states = {item["port"]: item["state"] for item in detected["requested_endpoints"]}
        self.assertEqual(states, {80: "conflict", 443: "conflict"})

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
        self.assertEqual(states, {80: "conflict", 443: "conflict"})


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
