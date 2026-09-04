import unittest
import tempfile
from dataclasses import dataclass

from tests.server_config_fixtures import (
    FakeAdapter, FakeClock, FIXED_NOW, fragment, runtime_observation,
)
from sandbox.server_config.models import (
    TerminalOutcome, ServerType, Readiness, InstanceConfigAuthority, RuntimeMode,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from sandbox.server_config.adapters.base import AdapterDescriptor


@dataclass(frozen=True)
class ControlSnapshot:
    fragments: tuple
    active_generation: str | None
    known_good_receipt: Any
    readiness: Readiness


class TestServerConfigControlInstance(unittest.TestCase):
    """T073: Target vs Control before/after evidence comparison matrix."""

    def setUp(self):
        self.clock = FakeClock()
        self.target_dir = tempfile.mkdtemp()
        self.control_dir = tempfile.mkdtemp()

        self.target_incarnation = "inc_" + "1" * 32
        self.control_incarnation = "inc_" + "2" * 32

        self.target_repo = ServerConfigRepository(self.target_dir, self.target_incarnation)
        self.control_repo = ServerConfigRepository(self.control_dir, self.control_incarnation)

        self.target_desc = AdapterDescriptor(
            server_type="nginx",
            adapter_id="target_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )
        self.control_desc = AdapterDescriptor(
            server_type="nginx",
            adapter_id="control_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )

        self.target_adapter = FakeAdapter(descriptor=self.target_desc)
        self.control_adapter = FakeAdapter(descriptor=self.control_desc)

        self.target_service = ServerConfigService(
            repository=self.target_repo,
            adapter=self.target_adapter,
            clock=self.clock,
        )
        self.control_service = ServerConfigService(
            repository=self.control_repo,
            adapter=self.control_adapter,
            clock=self.clock,
        )

        # Baseline fragment on control instance so it has non-empty initial state
        self.control_service.apply(fragment(name="control-base", content=b"control-content"))
        self.control_adapter.calls.clear()

    def snapshot_control(self) -> dict:
        fragments = self.control_service.list()
        receipt = self.control_repo.read_receipt()
        gen_id = getattr(receipt, "generation_id", None) if receipt else None
        return {
            "fragments": tuple((f.name, f.content_size) for f in fragments),
            "generation_id": gen_id,
            "adapter_calls_count": len(self.control_adapter.calls),
        }

    def test_target_apply_leaves_control_untouched(self):
        """T073: Apply on target leaves control completely unchanged."""
        before = self.snapshot_control()
        res = self.target_service.apply(fragment(name="target-frag", content=b"target-data"))
        self.assertEqual(res.outcome, TerminalOutcome.ACTIVE)
        after = self.snapshot_control()
        self.assertEqual(before, after)

    def test_target_replace_leaves_control_untouched(self):
        """T073: Replace on target leaves control completely unchanged."""
        self.target_service.apply(fragment(name="target-frag", content=b"initial"))
        before = self.snapshot_control()
        res = self.target_service.apply(fragment(name="target-frag", content=b"replaced"))
        self.assertEqual(res.outcome, TerminalOutcome.ACTIVE)
        after = self.snapshot_control()
        self.assertEqual(before, after)

    def test_target_revert_leaves_control_untouched(self):
        """T073: Revert on target leaves control completely unchanged."""
        self.target_service.apply(fragment(name="target-frag", content=b"initial"))
        before = self.snapshot_control()
        res = self.target_service.revert("target-frag")
        self.assertEqual(res.outcome, TerminalOutcome.ACTIVE)
        after = self.snapshot_control()
        self.assertEqual(before, after)

    def test_target_refusal_leaves_control_untouched(self):
        """T073: Refused mutation on target leaves control completely unchanged."""
        before = self.snapshot_control()
        with self.assertRaises(ValueError):
            self.target_service.apply(fragment(name="bad_name_forbidden", content=b""))
        after = self.snapshot_control()
        self.assertEqual(before, after)

    def test_target_rollback_leaves_control_untouched(self):
        """T073: Rollback on target leaves control completely unchanged."""
        # Prime target with known-good state
        self.target_service.apply(fragment(name="good-frag", content=b"data"))

        # Inject activation failure on target adapter
        self.target_adapter.results["activate"] = RuntimeError("target_fault")
        before = self.snapshot_control()
        res = self.target_service.apply(fragment(name="faulty-frag", content=b"data"))
        self.assertEqual(res.outcome, TerminalOutcome.ROLLED_BACK)
        after = self.snapshot_control()
        self.assertEqual(before, after)

    def test_target_recovery_needed_leaves_control_untouched(self):
        """T073: Recovery-needed on target leaves control completely unchanged."""
        # Inject catastrophic activation + rollback failure on target
        self.target_adapter.results["activate"] = RuntimeError("target_fault")
        self.target_adapter.results["restore"] = RuntimeError("restore_fault")
        before = self.snapshot_control()
        res = self.target_service.apply(fragment(name="fatal-frag", content=b"data"))
        self.assertEqual(res.outcome, TerminalOutcome.RECOVERY_NEEDED)
        after = self.snapshot_control()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
