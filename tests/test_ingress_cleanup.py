import unittest

from tests.test_ingress_service import TestIngressServiceMutation


class TestIngressCleanup(unittest.TestCase):
    def fixture(self):
        helper = TestIngressServiceMutation(methodName="test_cleanup_preserves_drift_and_removes_unchanged")
        self.addCleanup(helper.doCleanups)
        return helper, helper.service()

    def test_unchanged_cleanup_is_repeat_safe(self):
        helper, (service, _adapter, _runner, repository) = self.fixture()
        applied = service.apply_route(helper.planned(service), interactive=True)
        first = service.cleanup_owner("/tmp/project::default")
        second = service.cleanup_owner("/tmp/project::default")
        self.assertTrue(first["ok"]); self.assertTrue(first["mutated"])
        self.assertTrue(second["ok"]); self.assertFalse(second["mutated"])
        self.assertIsNone(repository.route(applied["route_id"]))

    def test_target_or_property_drift_is_preserved_for_recovery(self):
        helper, (service, adapter, _runner, repository) = self.fixture()
        applied = service.apply_route(helper.planned(service), interactive=True)
        adapter.current = {"route": "ok", "backend": {"address": "127.0.0.1", "port": 9999}}
        result = service.cleanup_owner("/tmp/project::default")
        self.assertEqual(result["state"], "cleanup_incomplete")
        self.assertIsNotNone(repository.route(applied["route_id"]))
        self.assertEqual(repository.snapshot()["recovery"][applied["route_id"]]["reason_code"],
                         "route_drifted")

    def test_replaced_incumbent_is_never_mutated(self):
        helper, (service, _adapter, _runner, repository) = self.fixture()
        applied = service.apply_route(helper.planned(service), interactive=True)
        service.detector.observation = type(service.detector.observation)(
            "fixture", "replacement",
            service.detector.observation.endpoints, "adoptable",
            service.detector.observation.capabilities,
            {"service": "replacement"},
        )
        result = service.cleanup_owner("/tmp/project::default")
        self.assertEqual(result["state"], "cleanup_incomplete")
        recovery = repository.snapshot()["recovery"][applied["route_id"]]
        self.assertEqual(recovery["reason_code"], "incumbent_replaced")

    def test_foreign_marker_or_property_change_is_never_removed(self):
        helper, (service, adapter, _runner, repository) = self.fixture()
        applied = service.apply_route(helper.planned(service), interactive=True)
        adapter.current = {**repository.route(applied["route_id"]).last_applied,
                           "ownership_marker": "foreign"}
        result = service.cleanup_owner("/tmp/project::default")
        self.assertFalse(result["ok"])
        self.assertIsNotNone(repository.route(applied["route_id"]))
        self.assertEqual(repository.snapshot()["recovery"][applied["route_id"]]["status"],
                         "drifted")

    def test_unavailable_adapter_retains_a_non_secret_residual(self):
        helper, (service, _adapter, _runner, repository) = self.fixture()
        applied = service.apply_route(helper.planned(service), interactive=True)
        service.registry._items.pop("fixture")
        result = service.cleanup_owner("/tmp/project::default")
        recovery = repository.snapshot()["recovery"][applied["route_id"]]
        self.assertEqual(result["state"], "cleanup_incomplete")
        self.assertEqual(recovery["reason_code"], "incumbent_unavailable")
        self.assertNotIn("credential", repr(recovery).lower())


if __name__ == "__main__": unittest.main()
