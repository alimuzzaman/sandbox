import unittest
import tempfile
import shutil
from pathlib import Path

from tests.server_config_fixtures import (
    FakeAdapter, FakeClock, FIXED_INCARNATION, FIXED_NOW,
    fragment, runtime_observation
)
from sandbox.server_config.models import (
    TerminalOutcome, ServerType, PhaseResult, ValidationEvidence,
    OperationResult, Readiness
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.adapters.base import AdapterDescriptor, RenderedGeneration
from sandbox.server_config.service import ServerConfigService

try:
    from sandbox.server_config.adapters.openlitespeed import OpenLiteSpeedAdapter
except ImportError:
    OpenLiteSpeedAdapter = None


class TestServerConfigServiceOpenLiteSpeed(unittest.TestCase):
    """T038: OpenLiteSpeed apply/list/show/revert and complete-set service integration."""

    def setUp(self):
        if OpenLiteSpeedAdapter is None:
            self.fail("sandbox.server_config.adapters.openlitespeed not implemented yet")
        self.temp_dir = tempfile.mkdtemp()
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.clock = FakeClock()
        
        self.descriptor = AdapterDescriptor(
            server_type="litespeed",
            adapter_id="wordpress-cache/openlitespeed/1",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="wordpress-cache-v1/openlitespeed/1",
            active_image_families=("litespeedtech/openlitespeed",),
            web_service="wp",
            mount_layout="server-config-mount-v1/openlitespeed-capability-gated",
            readiness_contract="target-origin-effective-vhost/v1",
        )
        self.adapter = OpenLiteSpeedAdapter()
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_ols_apply_success(self):
        """Test applying an OLS rewrite fragment successfully produces ACTIVE outcome."""
        frag = fragment(name="ols-cache", content=b"rewrite {\n  enable 1\n}\n")
        object.__setattr__(frag, "content", b"rewrite {\n  enable 1\n}\n")
        result = self.service.apply(frag)
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        self.assertTrue(result.mutated)

    def test_ols_list_fragments(self):
        """Test listing active OLS fragments."""
        frag = fragment(name="ols-list-test", content=b"rewrite {\n  enable 1\n}\n")
        object.__setattr__(frag, "content", b"rewrite {\n  enable 1\n}\n")
        self.service.apply(frag)
        frags = self.service.list()
        self.assertEqual(len(frags), 1)
        self.assertEqual(frags[0].name, "ols-list-test")

    def test_ols_revert_fragment(self):
        """Test reverting a fragment restores baseline."""
        frag = fragment(name="ols-revert-test", content=b"rewrite {\n  enable 1\n}\n")
        object.__setattr__(frag, "content", b"rewrite {\n  enable 1\n}\n")
        self.service.apply(frag)
        result = self.service.revert("ols-revert-test")
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        self.assertTrue(result.mutated)
        self.assertEqual(len(self.service.list()), 0)


if __name__ == "__main__":
    unittest.main()
