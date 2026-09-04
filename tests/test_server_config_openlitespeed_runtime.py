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
    from sandbox.server_config.adapters.openlitespeed import OpenLiteSpeedAdapter
except ImportError:
    OpenLiteSpeedAdapter = None


class TestOpenLiteSpeedRuntime(unittest.TestCase):
    """T036: OpenLiteSpeed exact-image validation, network-none, read-only root, bounded tmpfs, loopback canary, cleanup."""

    def setUp(self):
        if OpenLiteSpeedAdapter is None:
            self.fail("sandbox.server_config.adapters.openlitespeed not implemented yet")
        self.clock = FakeClock()
        self.mock_gateway = Mock()
        self.adapter = OpenLiteSpeedAdapter(gateway=self.mock_gateway)
        self.generation = RenderedGeneration(
            generation_id="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            files=(),
            manifest_digest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        )

    def test_exact_active_image_validation(self):
        """Test that the OLS adapter creates a disposable validation container using the exact active image ID (content-addressed sha256)."""
        image_id = "sha256:" + "c" * 64
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED, image_id=image_id)
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        self.mock_gateway.create_validation_container.assert_called_once()
        kwargs = self.mock_gateway.create_validation_container.call_args.kwargs
        self.assertEqual(kwargs.get("image_id"), image_id)

    def test_network_none_isolated_validator(self):
        """Test that validation container has --network none, read-only root, no live volumes, and bounded tmpfs."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        kwargs = self.mock_gateway.create_validation_container.call_args.kwargs
        self.assertEqual(kwargs.get("network_mode"), "none")
        self.assertTrue(kwargs.get("read_only_root"))
        self.assertFalse(kwargs.get("mount_live_volumes"))
        self.assertFalse(kwargs.get("pass_environment"))
        tmpfs = kwargs.get("tmpfs", {})
        self.assertIn("/tmp", tmpfs)
        self.assertIn("/usr/local/lsws/logs", tmpfs)

    def test_loopback_canary_probed(self):
        """Test that validation executes loopback probe to check canary headers/behavior."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        self.mock_gateway.execute_loopback_probe.assert_called_once()

    def test_cleanup_proven(self):
        """Test that validation stops and removes the validator container."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        self.mock_gateway.cleanup_validation_container.assert_called_once()

    def test_capability_unavailable_refusal(self):
        """Test that validation fails closed if image or runtime cannot support OLS vhost inclusion."""
        obs = dataclasses.replace(runtime_observation(), server_type=ServerType.LITESPEED)
        self.mock_gateway.is_capability_supported.return_value = False
        
        result = self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
