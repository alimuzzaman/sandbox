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


class TestIncumbentReloadDoesNotOrphanOwnedRoutes(unittest.TestCase):
    """Activating a route reloads the incumbent, which changes its pid. Judging
    "is this still the same incumbent" on the full fingerprint therefore
    orphaned every route this feature had just created."""

    @staticmethod
    def _observation(pid):
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint

        return IngressObservation(
            "system-caddy", "Caddy",
            (ListenerEndpoint("::", 80, socket_id="1", owner_confidence="proven",
                              process={"pid": pid, "start": str(pid),
                                       "executable": "/usr/bin/caddy"}),),
            "implemented_unproven", frozenset({"http"}))

    def test_reload_changes_the_fingerprint_but_not_ownership(self):
        before, after = self._observation(100), self._observation(200)
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(before.ownership_fingerprint, after.ownership_fingerprint)

    def test_a_different_endpoint_set_does_change_ownership(self):
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint

        moved = IngressObservation(
            "system-caddy", "Caddy",
            (ListenerEndpoint("127.0.0.1", 8080, socket_id="1"),),
            "implemented_unproven", frozenset({"http"}))
        self.assertNotEqual(self._observation(100).ownership_fingerprint,
                            moved.ownership_fingerprint)


class TestAbsentArtifactIsNotAnEternalResidual(unittest.TestCase):
    """A route whose fragment is already gone has no foreign state to preserve;
    keeping the record reports a residual that nothing can ever clear."""

    def test_absent_artifact_drops_the_record(self):
        helper, (service, adapter, _runner, repository) = TestIngressCleanup.fixture(self)
        applied = service.apply_route(helper.planned(service), interactive=True)
        adapter.current = {"route_id": applied["route_id"], "content_digest": ""}

        result = service.cleanup_owner("/tmp/project::default")

        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanup"]["residual"], [])
        self.assertIsNone(repository.route(applied["route_id"]))
        self.assertNotIn(applied["route_id"], repository.snapshot()["recovery"])

    def test_repeating_it_is_safe(self):
        helper, (service, adapter, _runner, repository) = TestIngressCleanup.fixture(self)
        service.apply_route(helper.planned(service), interactive=True)
        adapter.current = {"route_id": "gone", "content_digest": ""}
        service.cleanup_owner("/tmp/project::default")
        again = service.cleanup_owner("/tmp/project::default")
        self.assertTrue(again["ok"])
        self.assertEqual(again["reason"]["code"], "already_absent")
