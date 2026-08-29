import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sandbox.sync.capture import capture_manifest
from sandbox.sync.coordinator import RelationshipCoordinator
from sandbox.sync.models import SynchronizationRelationship
from sandbox.sync.repository import SyncRepository


def test_generation_transfer_has_one_concurrent_owner(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    (source / "app.py").write_text("print('safe')\n")
    manifest = capture_manifest(source)
    repository = SyncRepository(tmp_path / "sync.json")
    repository.put_relationship(SynchronizationRelationship(
        relationship_id="relationship", project_identity="project",
        remote_name="remote", workspace_id="workspace",
        updated_at="2026-08-28T00:00:00Z",
    ))
    generation, _ = repository.reserve_generation(
        relationship_id="relationship", request_id="request",
        request_digest="a" * 64, manifest_digest=manifest.manifest_digest,
        file_count=manifest.file_count, byte_count=manifest.byte_count,
    )
    claims = []
    barrier = threading.Barrier(8)

    def claim():
        barrier.wait()
        claims.append(repository.claim_generation_transfer(generation.generation_id)[1])

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert claims.count(True) == 1


class RelationshipCoordinatorTests(unittest.TestCase):
    def test_newest_distinct_trigger_is_coalesced_and_runs_after_inflight(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = SyncRepository(Path(temp) / "sync.json")
            coordinator = RelationshipCoordinator(repository, debounce_seconds=0)
            first_started = threading.Event()
            release_first = threading.Event()
            finished = threading.Event()
            calls = []

            def first():
                calls.append("first")
                first_started.set()
                release_first.wait(2)

            def stale():
                calls.append("stale")

            def newest():
                calls.append("newest")
                finished.set()

            before = time.monotonic()
            self.assertTrue(coordinator.submit("relationship", "trigger_a", first))
            self.assertTrue(first_started.wait(1))
            self.assertFalse(coordinator.submit("relationship", "trigger_b", stale))
            self.assertFalse(coordinator.submit("relationship", "trigger_c", newest))
            self.assertLess(time.monotonic() - before, 1)
            release_first.set()
            self.assertTrue(finished.wait(2))
            self.assertEqual(calls, ["first", "newest"])

    def test_duplicate_inflight_trigger_does_not_queue_an_extra_run(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = SyncRepository(Path(temp) / "sync.json")
            coordinator = RelationshipCoordinator(repository, debounce_seconds=1)
            started = threading.Event()
            release = threading.Event()
            calls = []

            def operation():
                calls.append("run")
                started.set()
                release.wait(2)

            self.assertTrue(coordinator.submit("relationship", "same", operation))
            self.assertTrue(started.wait(1))
            self.assertFalse(coordinator.submit("relationship", "same", operation))
            release.set()
            deadline = time.monotonic() + 2
            while coordinator._inflight and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(calls, ["run"])
