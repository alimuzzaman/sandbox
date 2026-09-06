import unittest
from unittest.mock import Mock, call
import dataclasses

from sandbox.server_config.models import (
    ServerType, Readiness, RuntimeObservation,
    OperationResult, FragmentSet
)
from sandbox.server_config.adapters.base import RenderedGeneration
from sandbox.server_config.adapters.nginx import NginxAdapter
from tests.server_config_fixtures import (
    FakeClock, FIXED_INCARNATION, FIXED_NOW,
    runtime_observation, fragment, fragment_set,
)


class TestNginxRuntime(unittest.TestCase):
    """T022: nginx exact-image validation, reload, readiness."""

    def setUp(self):
        self.clock = FakeClock()
        self.mock_gateway = Mock()
        self.adapter = NginxAdapter(gateway=self.mock_gateway)
        self.generation = RenderedGeneration(
            generation_id="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            files=(),
            manifest_digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        )

    def test_exact_active_image_validation_1(self):
        """Test that the nginx adapter creates a disposable validation container using the exact active image ID (content-addressed sha256), not a tag"""
        image_id = "sha256:" + "d" * 64
        obs = dataclasses.replace(runtime_observation(), image_id=image_id)
        
        # Should call validate using the observed image ID, not tag
        result = self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        self.mock_gateway.create_validation_container.assert_called_once()
        kwargs = self.mock_gateway.create_validation_container.call_args.kwargs
        self.assertEqual(kwargs.get("image_id"), image_id)
        self.assertNotIn("latest", kwargs.get("image_id", ""))

    def test_network_none_synthetic_validator_2(self):
        """Test that the validation container is created with --network none, read-only root, no live volumes/environment/secrets, and bounded tmpfs"""
        obs = runtime_observation()
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        kwargs = self.mock_gateway.create_validation_container.call_args.kwargs
        self.assertEqual(kwargs.get("network_mode"), "none")
        self.assertTrue(kwargs.get("read_only_root"))
        self.assertFalse(kwargs.get("mount_live_volumes"))
        self.assertFalse(kwargs.get("pass_environment"))
        self.assertIsNotNone(kwargs.get("tmpfs"))

    def test_fixed_argv_nginx_t_3(self):
        """Test that validation runs the native `nginx -t` command via fixed argv (no shell interpretation)"""
        obs = runtime_observation()
        
        self.adapter.validate_generation(
            generation=self.generation,
            observation=obs
        )
        
        kwargs = self.mock_gateway.create_validation_container.call_args.kwargs
        self.assertEqual(kwargs.get("command"), ["nginx", "-t", "-c", "/etc/nginx/nginx.conf"])
        self.assertFalse(kwargs.get("shell", False))

    def test_target_only_nginx_test_reload_4(self):
        """Test that reload targets only the selected Compose nginx service, not other services or instances"""
        obs = dataclasses.replace(runtime_observation(), runtime_id="runtime-123")
        
        self.adapter.reload_service(
            generation=self.generation,
            observation=obs
        )
        
        self.mock_gateway.reload_service.assert_called_once()
        kwargs = self.mock_gateway.reload_service.call_args.kwargs
        self.assertEqual(kwargs.get("target_instance"), "runtime-123")

    def test_pre_activation_identity_recheck_5(self):
        """Test that immediately before activation, the adapter rechecks runtime_id, image_id, mount_id, and incarnation_id match the pre-validation observation"""
        obs = runtime_observation()
        
        # Make the gateway return a different observation before activation
        changed_obs = dataclasses.replace(obs, image_id="sha256:" + "e" * 64)
        self.mock_gateway.get_current_observation.return_value = changed_obs
        
        with self.assertRaises(Exception) as context:
            self.adapter.activate_generation(
                generation=self.generation,
                observation=obs
            )
            
        self.assertIn("Identity mismatch", str(context.exception))

    def test_effective_generation_observation_6(self):
        """Test that after reload, the adapter observes the effective generation matches the activated candidate"""
        obs = runtime_observation()
        self.mock_gateway.get_current_observation.return_value = dataclasses.replace(
            obs, observed_generation_id="sha256:0000000000000000000000000000000000000000000000000000000000000000"
        )
        
        result = self.adapter.check_readiness(
            generation=self.generation,
            observation=obs
        )
        
        self.assertEqual(result, Readiness.READY)

    def test_unknown_not_ready_7(self):
        """Test that a container that is stopped, unreachable, or returns no generation evidence is reported as NOT ready"""
        obs = runtime_observation()
        
        # Test stopped
        self.mock_gateway.get_current_observation.return_value = dataclasses.replace(
            obs, readiness=Readiness.STOPPED
        )
        self.assertEqual(
            self.adapter.check_readiness(self.generation, obs),
            Readiness.STOPPED
        )
        
        # Test unknown
        self.mock_gateway.get_current_observation.return_value = dataclasses.replace(
            obs, readiness=Readiness.UNKNOWN
        )
        self.assertEqual(
            self.adapter.check_readiness(self.generation, obs),
            Readiness.UNKNOWN
        )
