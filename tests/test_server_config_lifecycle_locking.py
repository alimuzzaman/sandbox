import unittest
from unittest.mock import MagicMock, call
import tempfile
import os

from sandbox.server_config.lifecycle import (
    LifecycleMutationCoordinator,
    LockOrderingError,
)


class TestServerConfigLifecycleLocking(unittest.TestCase):
    """T071: Lifecycle-lock then fragment-lock ordering, re-read-under-lock, crash rollback."""

    def test_lifecycle_lock_then_fragment_lock_ordering(self):
        """T071: Must acquire lifecycle lock FIRST, then fragment lock SECOND."""
        lock_log = []

        class MockLifecycleLock:
            def acquire(self):
                lock_log.append("lifecycle_acquire")

            def release(self):
                lock_log.append("lifecycle_release")

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()

        class MockFragmentLock:
            def acquire(self):
                lock_log.append("fragment_acquire")

            def release(self):
                lock_log.append("fragment_release")

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *args):
                self.release()

        coordinator = LifecycleMutationCoordinator(
            lifecycle_lock=MockLifecycleLock(),
            fragment_lock=MockFragmentLock(),
        )

        with coordinator.acquire():
            lock_log.append("inside_effect")

        self.assertEqual(
            lock_log,
            [
                "lifecycle_acquire",
                "fragment_acquire",
                "inside_effect",
                "fragment_release",
                "lifecycle_release",
            ],
        )

    def test_re_read_under_lock(self):
        """T071: Re-reading state occurs under lock before effect evaluation."""
        events = []
        state_reader = MagicMock(side_effect=lambda: events.append("state_read") or {"state": "clean"})

        coordinator = LifecycleMutationCoordinator(
            lifecycle_lock=MagicMock(),
            fragment_lock=MagicMock(),
            state_reader=state_reader,
        )

        with coordinator.acquire() as state:
            events.append("effect_run")

        self.assertEqual(events, ["state_read", "effect_run"])

    def test_lock_held_across_effect(self):
        """T071: Locks remain held during mutation execution."""
        lifecycle_lock = MagicMock()
        fragment_lock = MagicMock()

        coordinator = LifecycleMutationCoordinator(
            lifecycle_lock=lifecycle_lock,
            fragment_lock=fragment_lock,
        )

        with coordinator.acquire():
            # In the effect block, both locks must be held (release not called yet)
            self.assertEqual(lifecycle_lock.__enter__.call_count, 1)
            self.assertEqual(fragment_lock.__enter__.call_count, 1)
            self.assertEqual(lifecycle_lock.__exit__.call_count, 0)
            self.assertEqual(fragment_lock.__exit__.call_count, 0)

        # After exiting, both locks released
        self.assertEqual(lifecycle_lock.__exit__.call_count, 1)
        self.assertEqual(fragment_lock.__exit__.call_count, 1)

    def test_crash_rollback_releases_both_locks(self):
        """T071: Crash during effect cleanly releases both locks."""
        lifecycle_lock = MagicMock()
        fragment_lock = MagicMock()

        coordinator = LifecycleMutationCoordinator(
            lifecycle_lock=lifecycle_lock,
            fragment_lock=fragment_lock,
        )

        with self.assertRaises(RuntimeError):
            with coordinator.acquire():
                raise RuntimeError("crash_during_mutation")

        self.assertEqual(fragment_lock.__exit__.call_count, 1)
        self.assertEqual(lifecycle_lock.__exit__.call_count, 1)

    def test_toctou_loser_no_effect(self):
        """T071: If state became invalid while waiting for lock, coordinator refuses without running effect."""
        effect_ran = False
        # State reader reports degraded state that arrived while waiting for lock
        state_reader = MagicMock(return_value={"is_recovery_needed": True})

        coordinator = LifecycleMutationCoordinator(
            lifecycle_lock=MagicMock(),
            fragment_lock=MagicMock(),
            state_reader=state_reader,
        )

        with self.assertRaises(RuntimeError) as ctx:
            with coordinator.acquire_gated(require_clean=True):
                effect_ran = True

        self.assertFalse(effect_ran)
        self.assertIn("recovery", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
