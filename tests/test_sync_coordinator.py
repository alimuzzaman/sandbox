import subprocess
import threading

from sandbox.sync.capture import capture_manifest
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
