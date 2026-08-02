from __future__ import annotations

import unittest


class Detector:
    def __init__(self, observations=()): self.observations = observations
    def observe(self): return self.observations


class TestIngressPins(unittest.TestCase):
    def service(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry
        return IngressService(
            detector=Detector(), registry=built_in_ingress_registry(),
        )

    def test_machine_override_beats_committed_project_pin_and_reports_source(self):
        service = self.service()
        selection = service.select(
            project_pin="system-caddy", machine_override="herd-valet",
        )
        self.assertEqual(selection.pin, "herd-valet")
        self.assertEqual(selection.pin_source, "machine_override")
        self.assertEqual(selection.reason_code, "pin_unavailable")

    def test_unavailable_explicit_pin_never_falls_back_to_a_different_ingress(self):
        service = self.service()
        selection = service.select(project_pin="missing-incumbent")
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "pin_unavailable")
        self.assertEqual(selection.pin_source, "project")

    def test_disabled_machine_pin_suppresses_detection(self):
        service = self.service()
        selection = service.select(project_pin="system-caddy", machine_override="disabled")
        self.assertIsNone(selection.adapter_id)
        self.assertEqual(selection.reason_code, "ingress_disabled")
        self.assertEqual(selection.pin_source, "machine_override")


if __name__ == "__main__": unittest.main()
