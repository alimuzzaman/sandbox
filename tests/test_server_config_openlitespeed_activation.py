import unittest
from unittest.mock import Mock
import dataclasses

from sandbox.server_config.models import (
    ServerType, Readiness, RuntimeObservation,
    OperationResult, FragmentSet
)
from sandbox.server_config.adapters.base import RenderedGeneration
from tests.server_config_fixtures import (
    FakeClock, FIXED_INCARNATION, FIXED_NOW,
    runtime_observation, fragment, fragment_set,
)

try:
    from sandbox.server_config.adapters.openlitespeed import OpenLiteSpeedAdapter, ReadinessResult
except ImportError:
    OpenLiteSpeedAdapter = None
    ReadinessResult = None


class TestOpenLiteSpeedActivation(unittest.TestCase):
    """T037: Target-only OLS generation selection/restart, recheck, effective-vhost identity, readiness, rollback."""

    def setUp(self):
        if OpenLiteSpeedAdapter is None:
            self.fail("sandbox.server_config.adapters.openlitespeed not implemented yet")
        self.clock = FakeClock()
        self.mock_gateway = Mock()
        self.adapter = OpenLiteSpeedAdapter(gateway=self.mock_gateway)
        self.generation = RenderedGeneration(
            generation_id="sha256:2222222222222222222222222222222222222222222222222222222222222222",
            files=(),
            manifest_digest="sha256:2222222222222222222222222222222222222222222222222222222222222222",
        )

    def test_target_only_restart(self):
        """Test that activation restarts only the selected OLS instance."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        self.adapter.activate(generation_id=self.generation.generation_id, observation=obs)
        self.mock_gateway.restart_target_service.assert_called_once_with(obs.runtime_id)

    def test_runtime_recheck_before_activation(self):
        """Test that activation proves runtime facts match preconditions before restarting."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        # Gateway returns a changed observation (e.g. image changed)
        changed_obs = dataclasses.replace(obs, image_id="sha256:" + "e" * 64)
        self.mock_gateway.get_current_observation.return_value = changed_obs
        
        result = self.adapter.activate(generation_id=self.generation.generation_id, observation=obs)
        self.assertFalse(result.ok)

    def test_effective_vhost_identity_readiness(self):
        """Test that readiness checks observe_ready returning active generation and live process proof."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        self.mock_gateway.probe_readiness.return_value = ReadinessResult(
            code="ready",
            evidence_id=None,
            observed_at=FIXED_NOW,
            effective_generation=self.generation.generation_id,
        )
        
        readiness = self.adapter.observe_ready(
            expected_generation=self.generation.generation_id,
            observation=obs,
        )
        self.assertEqual(readiness.state, "ready")
        self.assertEqual(readiness.effective_generation, self.generation.generation_id)

    def test_rollback_operation(self):
        """Test that restore restores prior generation and reloads target."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        prior_gen = "sha256:" + "3" * 64
        
        self.adapter.restore(prior_generation=prior_gen, observation=obs)
        self.mock_gateway.restore.assert_called_once_with(prior_gen)
        self.mock_gateway.restart_target_service.assert_called_once_with(obs.runtime_id)


if __name__ == "__main__":
    unittest.main()
