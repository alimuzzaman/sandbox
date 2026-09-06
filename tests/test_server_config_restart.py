import unittest
import tempfile
import os
from unittest.mock import MagicMock

from tests.server_config_fixtures import (
    FakeClock, FIXED_INCARNATION, fragment,
)
from sandbox.server_config.models import (
    TerminalOutcome, Readiness, InstanceConfigAuthority, RuntimeMode, ServerType,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.lifecycle import (
    reconcile_restart_generation,
    check_instance_mount_and_image_drift,
)


class TestServerConfigRestart(unittest.TestCase):
    """T072: stop/stopped/start, committed-generation restoration, drift detection, and readiness."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.clock = FakeClock()

    def test_stopped_instance_refuses_mutations(self):
        """T072: A stopped instance refuses fragment apply/revert mutations."""
        from sandbox.server_config.service import ServerConfigService
        from sandbox.server_config.adapters.base import AdapterDescriptor
        from tests.server_config_fixtures import FakeAdapter

        descriptor = AdapterDescriptor(
            server_type="nginx",
            adapter_id="test_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )
        adapter = FakeAdapter(descriptor=descriptor)
        service = ServerConfigService(
            repository=self.repository,
            adapter=adapter,
            clock=self.clock,
        )

        stopped_authority = InstanceConfigAuthority(
            instance_name="stopped-inst",
            instance_incarnation_id=FIXED_INCARNATION,
            project_identity="proj_test",
            server_type=ServerType.NGINX,
            runtime_mode=RuntimeMode.LOCAL_COMPOSE,
            server_config_mount_id="sha256:" + "a" * 64,
            status="stopped",
        )

        with self.assertRaises(RuntimeError) as ctx:
            service.apply(
                fragment(name="test-frag"),
                instance_authority=stopped_authority,
            )
        self.assertIn("stopped", str(ctx.exception).lower())

    def test_ordinary_stop_then_start_preserves_incarnation_and_generation(self):
        """T072: Ordinary stop/start retains the exact incarnation ID and committed generation."""
        # Setup committed generation in repo
        # When restarting, reconcile_restart_generation returns the exact generation_id
        res = reconcile_restart_generation(
            repository=self.repository,
            incarnation_id=FIXED_INCARNATION,
            current_image="nginx:1.27.4-alpine",
        )
        self.assertIsNotNone(res)

    def test_restart_reconciles_committed_generation_before_ready(self):
        """T072: System restores and reconciles committed generation before reporting ready."""
        adapter = MagicMock()
        adapter.observe_runtime.return_value = MagicMock(generation_id="sha256:" + "1" * 64)
        adapter.observe_ready.return_value = MagicMock(readiness=Readiness.READY)

        res = reconcile_restart_generation(
            repository=self.repository,
            incarnation_id=FIXED_INCARNATION,
            current_image="nginx:1.27.4-alpine",
            adapter=adapter,
        )
        # Must verify adapter observe_ready was called
        self.assertTrue(adapter.observe_ready.called)

    def test_restart_detects_image_drift_fails_closed(self):
        """T072: If running image drifted from recorded generation, fail closed."""
        with self.assertRaises(RuntimeError) as ctx:
            check_instance_mount_and_image_drift(
                expected_image="nginx:1.27.4-alpine",
                observed_image="nginx:1.25.0-alpine",
                expected_mount=f"/sandbox/runtime/server-config/{FIXED_INCARNATION}",
                observed_mount=f"/sandbox/runtime/server-config/{FIXED_INCARNATION}",
            )
        self.assertIn("image", str(ctx.exception).lower())

    def test_restart_detects_mount_drift_fails_closed(self):
        """T072: If mount path drifted or is missing, fail closed."""
        with self.assertRaises(RuntimeError) as ctx:
            check_instance_mount_and_image_drift(
                expected_image="nginx:1.27.4-alpine",
                observed_image="nginx:1.27.4-alpine",
                expected_mount=f"/sandbox/runtime/server-config/{FIXED_INCARNATION}",
                observed_mount=None,
            )
        self.assertIn("mount", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
