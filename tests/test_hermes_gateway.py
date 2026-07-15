import unittest

from sandbox.hermes.gateway import GatewayPlan, HermesGatewayService
from tests.fakes.hermes import RecordingGatewayBackend


class TestHermesGateway(unittest.TestCase):
    def test_plan_is_validated_and_has_no_backend_side_effect(self):
        backend = RecordingGatewayBackend()
        service = HermesGatewayService(backend)

        plan = service.plan("hermes.example.test", "http://127.0.0.1:9119", basic_auth=True)

        self.assertTrue(plan.access_first)
        self.assertTrue(plan.basic_auth)
        self.assertEqual(backend.calls, [])
        with self.assertRaisesRegex(ValueError, "loopback"):
            service.plan("hermes.example.test", "https://public.example.test")
        with self.assertRaisesRegex(ValueError, "hostname"):
            service.plan("hermes.example.test;route", "http://127.0.0.1:9119")
        for origin in (
            "http://127.0.0.1:9119@public.example.test",
            "http://127.0.0.1:9119/path",
            "http://127.0.0.1:9119?redirect=public.example.test",
            "http://127.0.0.1:not-a-port",
        ):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "loopback"):
                service.plan("hermes.example.test", origin)

    def test_apply_revalidates_hand_built_plan_before_backend_side_effects(self):
        backend = RecordingGatewayBackend()
        service = HermesGatewayService(backend)
        unsafe = GatewayPlan(
            "hermes.example.test",
            "http://127.0.0.1:9119@public.example.test",
            basic_auth=True,
        )

        with self.assertRaisesRegex(ValueError, "loopback"):
            service.apply(unsafe)

        self.assertEqual(backend.calls, [])

    def test_apply_authenticates_before_route_and_rolls_back_in_reverse_order(self):
        """US7 seam: public route exposure is reversible and access comes first."""
        backend = RecordingGatewayBackend(fail_route=True)
        service = HermesGatewayService(backend)
        plan = service.plan("hermes.example.test", "http://127.0.0.1:9119", basic_auth=True)

        with self.assertRaisesRegex(RuntimeError, "route failed"):
            service.apply(plan)

        self.assertEqual([name for name, _ in backend.calls], [
            "apply_access", "apply_route", "remove_route", "remove_access",
        ])

    def test_remove_is_an_explicit_idempotent_service_operation(self):
        backend = RecordingGatewayBackend()
        service = HermesGatewayService(backend)
        plan = service.plan("hermes.example.test", "http://127.0.0.1:9119")
        remove = getattr(service, "remove", None)
        self.assertTrue(callable(remove), "gateway must expose remove(plan) for reversible teardown")

        remove(plan) if callable(remove) else None

        self.assertEqual([name for name, _ in backend.calls], ["remove_route", "remove_access"])

    def test_remove_revalidates_hand_built_plan_before_backend_side_effects(self):
        backend = RecordingGatewayBackend()
        service = HermesGatewayService(backend)
        unsafe = GatewayPlan(
            "hermes.example.test;route",
            "http://127.0.0.1:9119",
        )

        with self.assertRaisesRegex(ValueError, "hostname"):
            service.remove(unsafe)

        self.assertEqual(backend.calls, [])


if __name__ == "__main__": unittest.main()
