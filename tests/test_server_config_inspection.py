"""Tests for read-only inspection projections and zero persistent writes (T060 / US4).

Verifies:
- Stopped instance reports stopped, not ready
- Degraded / unknown runtime reports degraded / unknown
- Unresolved / corrupt state reports recovery-needed
- Absent repository reports absent without creating directories
- Zero persistent writes: inspecting state never creates files, writes timestamps, or repairs state
"""

import os
from pathlib import Path
import shutil
import tempfile
import unittest

from sandbox.server_config.adapters.base import AdapterDescriptor
from sandbox.server_config.models import (
    InspectionState,
    Readiness,
    RuntimeObservation,
    ServerType,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from tests.server_config_fixtures import (
    FIXED_INCARNATION,
    FakeAdapter,
    FakeClock,
    fragment,
)


def _directory_snapshot(path: Path) -> dict[str, tuple[int, int, int]]:
    """Captures relative path -> (st_size, st_mtime_ns, st_ino)."""
    if not path.exists():
        return {}
    snapshot = {}
    for root, dirs, files in os.walk(path):
        for name in files:
            file_path = Path(root) / name
            rel = str(file_path.relative_to(path))
            stat = file_path.stat()
            snapshot[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    return snapshot


class TestServerConfigInspection(unittest.TestCase):
    """T060: Inspection projections and zero-persistent-write guarantees."""

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

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_inspect_absent_repository_reports_absent_with_zero_writes(self):
        """When repository does not exist, inspect returns ABSENT and creates no directories or files."""
        # Repository root does not exist yet
        self.assertFalse(self.repository.root.exists())

        before_files = list(Path(self.temp_dir).rglob("*"))

        state = self.service.inspect()
        self.assertEqual(state, InspectionState.ABSENT)

        # Still no files or directories created!
        after_files = list(Path(self.temp_dir).rglob("*"))
        self.assertEqual(before_files, after_files)
        self.assertFalse(self.repository.root.exists())

    def test_inspect_stopped_runtime_reports_stopped(self):
        """Stopped runtime observation yields STOPPED inspection state, never READY."""
        # Setup initial state with an applied fragment
        self.service.apply(fragment(name="test-cache"))
        if self.repository.lock_path.exists():
            self.repository.lock_path.unlink()

        self.adapter.results["observe_runtime"] = RuntimeObservation(
            instance_incarnation_id=FIXED_INCARNATION,
            server_type=ServerType.NGINX,
            runtime_id="runtime-1",
            image_id="sha256:" + "9" * 64,
            mount_id="sha256:" + "8" * 64,
            observed_generation_id="sha256:" + "0" * 64,
            readiness=Readiness.STOPPED,
            observed_at=self.clock.now(),
        )

        before = _directory_snapshot(self.repository.root)
        state = self.service.inspect()
        after = _directory_snapshot(self.repository.root)

        self.assertEqual(state, InspectionState.STOPPED)
        # Verify zero persistent writes
        self.assertEqual(before, after)
        self.assertFalse(self.repository.lock_path.exists())

    def test_inspect_degraded_runtime_reports_degraded(self):
        """Degraded runtime yields DEGRADED inspection state."""
        self.service.apply(fragment(name="test-cache"))

        self.adapter.results["observe_runtime"] = RuntimeObservation(
            instance_incarnation_id=FIXED_INCARNATION,
            server_type=ServerType.NGINX,
            runtime_id="runtime-1",
            image_id="sha256:" + "9" * 64,
            mount_id="sha256:" + "8" * 64,
            observed_generation_id="sha256:" + "0" * 64,
            readiness=Readiness.DEGRADED,
            observed_at=self.clock.now(),
        )

        before = _directory_snapshot(self.repository.root)
        state = self.service.inspect()
        after = _directory_snapshot(self.repository.root)

        self.assertEqual(state, InspectionState.DEGRADED)
        self.assertEqual(before, after)

    def test_inspect_corrupt_state_reports_recovery_needed_without_repairing(self):
        """Corrupt state file yields RECOVERY_NEEDED without deleting or modifying corrupt file."""
        self.repository.initialize()
        state_file = self.repository.state_path
        corrupt_bytes = b"corrupt-non-json-content\n"
        state_file.write_bytes(corrupt_bytes)
        state_file.chmod(0o600)

        before = _directory_snapshot(self.repository.root)
        state = self.service.inspect()
        after = _directory_snapshot(self.repository.root)

        self.assertEqual(state, InspectionState.RECOVERY_NEEDED)
        # Corrupt file must NOT be repaired, deleted, or altered
        self.assertEqual(state_file.read_bytes(), corrupt_bytes)
        self.assertEqual(before, after)

    def test_list_and_show_perform_zero_persistent_writes(self):
        """list() and show() never write files, update timestamps, or create locks."""
        self.service.apply(fragment(name="alpha-cache"))
        if self.repository.lock_path.exists():
            self.repository.lock_path.unlink()

        before = _directory_snapshot(self.repository.root)

        # Call list and show multiple times
        frags = self.service.list()
        self.assertEqual(len(frags), 1)
        shown = self.service.show("alpha-cache")
        self.assertIsNotNone(shown)
        missing = self.service.show("nonexistent")
        self.assertIsNone(missing)

        after = _directory_snapshot(self.repository.root)

        # Absolute zero change to disk
        self.assertEqual(before, after)
        self.assertFalse(self.repository.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
