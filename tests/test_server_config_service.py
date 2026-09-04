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


class TestServerConfigService(unittest.TestCase):
    """T020: Apply/list/show/replace/revert service orchestration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.clock = FakeClock()
        
        self.descriptor = AdapterDescriptor(
            server_type="nginx",
            adapter_id="test_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract"
        )
        self.adapter = FakeAdapter(descriptor=self.descriptor)
        
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_happy_path_apply_produces_active_mutated(self):
        frag = fragment(name="happy-cache")
        result = self.service.apply(frag)
        
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        self.assertTrue(result.mutated)
        self.assertEqual(result.fragment_name, "happy-cache")

    def test_list_after_applying_returns_sorted_metadata(self):
        self.service.apply(fragment(name="z-cache"))
        self.service.apply(fragment(name="a-cache"))
        
        items = self.service.list()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].name, "a-cache")
        self.assertEqual(items[1].name, "z-cache")

    def test_metadata_show_exact_name_returns_bounded_metadata(self):
        self.service.apply(fragment(name="show-cache"))
        
        meta = self.service.show("show-cache")
        self.assertIsNotNone(meta)
        self.assertEqual(meta.name, "show-cache")
        self.assertFalse(hasattr(meta, "content"))

    def test_replace_same_name_different_fragment_leaves_one(self):
        self.service.apply(fragment(name="rep-cache", content=b"old"))
        
        result = self.service.apply(fragment(name="rep-cache", content=b"new-content"))
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        
        items = self.service.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].content_size, len(b"new-content"))

    def test_revert_active_fragment_disappears(self):
        self.service.apply(fragment(name="rev-cache"))
        
        result = self.service.revert("rev-cache")
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        self.assertTrue(result.mutated)
        
        items = self.service.list()
        self.assertEqual(len(items), 0)

    def test_deterministic_ordering_of_fragments(self):
        self.service.apply(fragment(name="b-cache"))
        self.service.apply(fragment(name="a-cache"))
        
        items = self.service.list()
        self.assertEqual([i.name for i in items], ["a-cache", "b-cache"])

    def test_identical_reapply_is_noop_no_calls(self):
        frag = fragment(name="noop-cache", content=b"same")
        self.service.apply(frag)
        
        self.adapter.calls.clear()
        
        result = self.service.apply(frag)
        self.assertEqual(result.outcome, TerminalOutcome.NO_OP)
        self.assertFalse(result.mutated)
        self.assertEqual(len(self.adapter.calls), 0)

    def test_absent_name_healthy_revert_is_noop(self):
        self.service.apply(fragment(name="exist-cache"))
        self.adapter.calls.clear()
        
        result = self.service.revert("missing-cache")
        self.assertEqual(result.outcome, TerminalOutcome.NO_OP)
        self.assertFalse(result.mutated)
        self.assertEqual(len(self.adapter.calls), 0)
