import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

    def test_reconcile_aggregates_residuals_without_live_instance_lookup(self):
        helper = TestIngressServiceMutation(methodName="test_cleanup_preserves_drift_and_removes_unchanged")
        self.addCleanup(helper.doCleanups)
        service, adapter, _runner, repository = helper.service()
        applied = service.apply_route(helper.planned(service), interactive=True)
        adapter.current = {"foreign": True}
        result = service.reconcile_owner("/tmp/project::default")
        self.assertEqual(result["operation"], "ingress_reconcile")
        self.assertEqual(result["state"], "cleanup_incomplete")
        self.assertEqual(result["recovery"]["residual"][0]["route_id"], applied["route_id"])
        self.assertIsNotNone(repository.route(applied["route_id"]))

    def test_instance_cleanup_aggregates_resolver_cleanup_after_ingress_residual(self):
        from sandbox.commands.instances_cmd import _cleanup_instance_routes

        ingress = mock.Mock()
        ingress.cleanup_owner.return_value = {
            "ok": False, "state": "cleanup_incomplete", "mutated": False,
        }
        domains = mock.Mock()
        domains.cleanup.return_value = SimpleNamespace(
            ok=True, state="ready", mutated=False,
        )
        with mock.patch("sandbox.commands.instances_cmd.ingress_service", return_value=ingress), \
                mock.patch("sandbox.commands.instances_cmd.domain_service", return_value=domains):
            _cleanup_instance_routes({}, {"root": "/tmp/project", "label": "default"})
        ingress.cleanup_owner.assert_called_once_with(f"{Path('/tmp/project').resolve()}::default")
        domains.cleanup.assert_called_once_with("/tmp/project", label="default", interactive=False)


if __name__ == "__main__": unittest.main()
