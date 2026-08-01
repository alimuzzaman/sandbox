import unittest

from tests.test_ingress_service import TestIngressServiceMutation


class TestIngressRecovery(unittest.TestCase):
    def test_residual_can_retry_without_registry_or_instance_identity(self):
        helper = TestIngressServiceMutation(methodName="test_cleanup_preserves_drift_and_removes_unchanged")
        self.addCleanup(helper.doCleanups)
        service, adapter, _runner, repository = helper.service()
        applied = service.apply_route(helper.planned(service), interactive=True)
        expected = repository.route(applied["route_id"]).last_applied
        adapter.current = {"foreign": True}
        self.assertFalse(service.cleanup_owner("/tmp/project::default")["ok"])
        adapter.current = expected
        retried = service.cleanup_owner("/tmp/project::default")
        self.assertTrue(retried["ok"])
        self.assertNotIn(applied["route_id"], repository.snapshot()["recovery"])


if __name__ == "__main__": unittest.main()
