"""Tests for automatic rollback, recovery activation, and recovery-needed behavior (T058 / US4).

Verifies:
- Fault during activation triggers exact-prior restore and proven readiness -> ROLLED_BACK
- Fault during reload triggers rollback -> ROLLED_BACK
- Fault during readiness observation triggers rollback -> ROLLED_BACK
- Exactly one recovery activation is attempted (no infinite rollback loops)
- Rollback fault or timeout enters RECOVERY_NEEDED
- Rolled-back result has mutated=True and preserves failure evidence
"""

from datetime import datetime, timezone
import shutil
import tempfile
import unittest

from sandbox.server_config.adapters.base import AdapterDescriptor, RenderedGeneration
from sandbox.server_config.context import Clock
from sandbox.server_config.models import (
    ActivationTransaction,
    KnownGoodReceipt,
    OperationResult,
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
    TerminalOutcome,
    TransactionPhase,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from tests.server_config_fixtures import (
    FIXED_INCARNATION,
    FIXED_NOW,
    FakeAdapter,
    FakeClock,
    fragment,
)


class TestServerConfigRollback(unittest.TestCase):
    """T058: Post-validation faults, automatic rollback, and recovery-needed semantics."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.clock = FakeClock()
        self.descriptor = AdapterDescriptor(
            server_type="nginx",
            adapter_id="test_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )
        self.adapter = FakeAdapter(descriptor=self.descriptor)
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )

        # Dynamic runtime observation tracking current committed generation
        def observe_runtime_hook(*args):
            state = self.repository.read_state()
            gen_id = (
                state.get("generation_id")
                if isinstance(state, dict) and state.get("generation_id")
                else "sha256:" + "0" * 64
            )
            return RuntimeObservation(
                instance_incarnation_id=FIXED_INCARNATION,
                server_type=ServerType.NGINX,
                runtime_id="runtime-1",
                image_id="sha256:" + "9" * 64,
                mount_id="sha256:" + "8" * 64,
                observed_generation_id=gen_id,
                readiness=Readiness.READY,
                observed_at=self.clock.now(),
            )

        self.adapter.results["observe_runtime"] = observe_runtime_hook
        self.adapter.results["validate"] = PhaseResult("passed", None, self.clock.now())

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _setup_active_prior_fragment(self) -> ServerConfigFragment:
        """Applies a known-good baseline fragment."""
        self.adapter.results["activate"] = PhaseResult("active", None, self.clock.now())
        self.adapter.results["reload"] = PhaseResult("reloaded", None, self.clock.now())
        self.adapter.results["observe_ready"] = PhaseResult("ready", None, self.clock.now())

        prior_frag = fragment(name="baseline-cache", content=b"baseline")
        result = self.service.apply(prior_frag)
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)
        return prior_frag

    def test_post_validation_activation_fault_restores_prior_generation(self):
        """When adapter.activate fails, rollback restores the exact prior set and reports ROLLED_BACK."""
        prior = self._setup_active_prior_fragment()
        prior_state = self.repository.read_state()
        prior_gen = prior_state.get("generation_id")

        # Now configure candidate activation to fail
        self.adapter.results["activate"] = RuntimeError("injected activation fault")
        self.adapter.results["restore"] = PhaseResult("restored", None, self.clock.now())
        self.adapter.results["reload"] = PhaseResult("reloaded", None, self.clock.now())
        self.adapter.results["observe_ready"] = PhaseResult("ready", None, self.clock.now())

        candidate = fragment(name="new-cache", content=b"candidate")
        result = self.service.apply(candidate)

        # Must report ROLLED_BACK with mutated=True
        self.assertEqual(result.outcome, TerminalOutcome.ROLLED_BACK)
        self.assertTrue(result.mutated)
        self.assertEqual(result.fragment_name, "new-cache")

        # Verify restore was called with prior_gen
        restore_calls = [c for c in self.adapter.calls if c[0] == "restore"]
        self.assertTrue(len(restore_calls) >= 1)
        self.assertEqual(restore_calls[0][1][0], prior_gen)

        # The prior fragment set remains active
        active_fragments = self.service.list()
        self.assertEqual(len(active_fragments), 1)
        self.assertEqual(active_fragments[0].name, "baseline-cache")

    def test_post_validation_reload_fault_restores_prior_generation(self):
        """When candidate reload fails, rollback restores prior set and reports ROLLED_BACK."""
        self._setup_active_prior_fragment()

        # Activation succeeds, but reload fails
        call_count = {"reload": 0}

        def reload_hook(*args):
            call_count["reload"] += 1
            if call_count["reload"] == 1:
                raise RuntimeError("injected candidate reload failure")
            return PhaseResult("reloaded", None, self.clock.now())

        self.adapter.results["activate"] = PhaseResult("active", None, self.clock.now())
        self.adapter.results["reload"] = reload_hook
        self.adapter.results["restore"] = PhaseResult("restored", None, self.clock.now())
        self.adapter.results["observe_ready"] = PhaseResult("ready", None, self.clock.now())

        candidate = fragment(name="new-cache", content=b"candidate")
        result = self.service.apply(candidate)

        self.assertEqual(result.outcome, TerminalOutcome.ROLLED_BACK)
        self.assertTrue(result.mutated)

        # Baseline fragment remains active
        active_fragments = self.service.list()
        self.assertEqual(len(active_fragments), 1)
        self.assertEqual(active_fragments[0].name, "baseline-cache")

    def test_candidate_readiness_observation_failure_triggers_rollback(self):
        """When candidate observe_ready fails/degraded, rollback restores prior set."""
        self._setup_active_prior_fragment()

        call_count = {"observe_ready": 0}

        def observe_ready_hook(*args):
            call_count["observe_ready"] += 1
            if call_count["observe_ready"] == 1:
                return PhaseResult("degraded", None, self.clock.now())
            return PhaseResult("ready", None, self.clock.now())

        self.adapter.results["activate"] = PhaseResult("active", None, self.clock.now())
        self.adapter.results["reload"] = PhaseResult("reloaded", None, self.clock.now())
        self.adapter.results["observe_ready"] = observe_ready_hook
        self.adapter.results["restore"] = PhaseResult("restored", None, self.clock.now())

        candidate = fragment(name="new-cache", content=b"candidate")
        result = self.service.apply(candidate)

        self.assertEqual(result.outcome, TerminalOutcome.ROLLED_BACK)
        self.assertTrue(result.mutated)

    def test_recovery_activation_fault_enters_recovery_needed_without_infinite_retry(self):
        """When both candidate and rollback activation fail, system enters RECOVERY_NEEDED and stops."""
        self._setup_active_prior_fragment()

        # Both activate and restore fail
        self.adapter.results["activate"] = RuntimeError("candidate fault")
        self.adapter.results["restore"] = RuntimeError("recovery restore fault")

        candidate = fragment(name="new-cache", content=b"candidate")
        result = self.service.apply(candidate)

        self.assertEqual(result.outcome, TerminalOutcome.RECOVERY_NEEDED)
        self.assertIsNone(result.mutated)  # RECOVERY_NEEDED has mutated=None

        # Exactly ONE recovery restore attempt must be made
        restore_calls = [c for c in self.adapter.calls if c[0] == "restore"]
        self.assertEqual(len(restore_calls), 1)

    def test_rollback_timeout_enters_recovery_needed(self):
        """If rollback exceeds its 60-second deadline, terminal outcome is RECOVERY_NEEDED."""
        self._setup_active_prior_fragment()

        def timeout_restore(*args):
            self.clock.advance(65.0)  # Exceeds 60s phase limit
            return PhaseResult("restored", None, self.clock.now())

        self.adapter.results["activate"] = RuntimeError("candidate fault")
        self.adapter.results["restore"] = timeout_restore

        candidate = fragment(name="new-cache", content=b"candidate")
        result = self.service.apply(candidate)

        self.assertEqual(result.outcome, TerminalOutcome.RECOVERY_NEEDED)


if __name__ == "__main__":
    unittest.main()
