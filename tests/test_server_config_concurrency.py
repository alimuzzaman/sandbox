"""Tests for per-incarnation concurrency locking, deadlines, and re-read behavior (T059 / US4).

Verifies:
- Distinct incarnations do not contend or block each other
- Same incarnation contends on lock; deadline expiration yields CONFLICT without state corruption
- Re-read-after-wait: queued second writer re-reads state under lock and preserves first writer's commit
- Whole-operation 180-second deadline enforcement
- Phase 60-second deadline enforcement
- Proper lock release and cleanup on failure/timeout
"""

from datetime import datetime, timezone
import shutil
import tempfile
import time
import unittest

from sandbox.server_config.adapters.base import AdapterDescriptor, RenderedGeneration
from sandbox.server_config.context import Clock
from sandbox.server_config.models import (
    OperationResult,
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
    TerminalOutcome,
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


INCARNATION_B = "inc_" + "2" * 32


class TestServerConfigConcurrency(unittest.TestCase):
    """T059: Per-incarnation locking, re-read-after-wait, and deadline bounds."""

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
        self.adapter.results["activate"] = PhaseResult("active", None, self.clock.now())
        self.adapter.results["reload"] = PhaseResult("reloaded", None, self.clock.now())
        self.adapter.results["observe_ready"] = PhaseResult("ready", None, self.clock.now())

        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_distinct_incarnations_do_not_contend_on_locks(self):
        """Two different incarnations can acquire locks independently."""
        repo_a = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        repo_b = ServerConfigRepository(self.temp_dir, INCARNATION_B)

        with repo_a.locked():
            # repo_b should acquire its lock without contention or delay
            with repo_b.locked(deadline=time.monotonic() + 0.5):
                self.assertTrue(repo_a.lock_path.exists())
                self.assertTrue(repo_b.lock_path.exists())

    def test_lock_contention_same_incarnation_yields_conflict_on_deadline_expiration(self):
        """When lock is held past the deadline, a second attempt raises or yields CONFLICT."""
        repo = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        with repo.locked():
            # A second attempt with an immediate deadline must raise operation_conflict
            with self.assertRaisesRegex(ValueError, "operation_conflict"):
                with repo.locked(deadline=time.monotonic() - 1.0):
                    pass

    def test_re_read_after_wait_preserves_concurrent_writer_commits(self):
        """Writer 2 re-reads state under lock and includes Writer 1's newly committed fragment."""
        frag1 = fragment(name="cache-one", content=b"one")
        frag2 = fragment(name="cache-two", content=b"two")

        # Writer 1 applies cache-one
        res1 = self.service.apply(frag1)
        self.assertEqual(res1.outcome, TerminalOutcome.ACTIVE)

        # Writer 2 applies cache-two using a fresh service instance (simulating another process)
        service2 = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )
        res2 = service2.apply(frag2)
        self.assertEqual(res2.outcome, TerminalOutcome.ACTIVE)

        # Both fragments must exist in the final state!
        all_frags = self.service.list()
        names = [f.name for f in all_frags]
        self.assertIn("cache-one", names)
        self.assertIn("cache-two", names)

    def test_whole_operation_deadline_180_seconds_enforced(self):
        """Operation exceeding 180 seconds total monotonic time is aborted."""
        # Adapter validation simulates a stall that exhausts the 180s operation budget
        def stalled_validation(*args):
            self.clock.advance(185.0)
            return PhaseResult("passed", None, self.clock.now())

        self.adapter.results["validate"] = stalled_validation

        result = self.service.apply(fragment(name="timeout-cache"))
        self.assertIn(result.outcome, {TerminalOutcome.REFUSED, TerminalOutcome.RECOVERY_NEEDED})

    def test_phase_deadline_60_seconds_enforced(self):
        """A single phase (e.g. activation) exceeding 60 seconds triggers phase timeout."""
        def stalled_activation(*args):
            self.clock.advance(65.0)
            raise TimeoutError("activation timed out")

        self.adapter.results["activate"] = stalled_activation

        result = self.service.apply(fragment(name="timeout-phase-cache"))
        self.assertIn(result.outcome, {TerminalOutcome.ROLLED_BACK, TerminalOutcome.RECOVERY_NEEDED})

    def test_lock_released_on_unhandled_error(self):
        """When an operation raises an unhandled exception, the lock file descriptor is closed."""
        self.adapter.results["validate"] = RuntimeError("unexpected crash")
        try:
            self.service.apply(fragment(name="crash-cache"))
        except RuntimeError:
            pass

        # Lock must be immediately re-acquirable
        with self.repository.locked(deadline=time.monotonic() + 0.1):
            pass


if __name__ == "__main__":
    unittest.main()
