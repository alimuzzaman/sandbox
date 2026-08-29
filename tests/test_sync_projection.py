import subprocess
import unittest

from sandbox.sync.capture import capture_manifest
from sandbox.sync.projection import ManagedSourceProjection, SourceWriteRefused


def test_projection_uses_git_relative_sorted_paths(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "app.py").write_text("print('safe')\n")
    manifest = capture_manifest(tmp_path)
    assert [entry.path for entry in manifest.entries] == ["nested/app.py"]
    assert str(tmp_path) not in str(manifest.canonical_entries())


class ManagedSourceProjectionTests(unittest.TestCase):
    def test_shared_source_write_is_refused_before_execution(self):
        policy = ManagedSourceProjection()
        with self.assertRaises(SourceWriteRefused):
            policy.prepare(
                relationship_id="rel", generation_id="gen",
                source_access="managed_read_only", requests_source_write=True,
            )

    def test_isolated_copy_keeps_writes_in_artifact_boundary(self):
        policy = ManagedSourceProjection()
        result = policy.prepare(
            relationship_id="rel", generation_id="gen",
            source_access="isolated_copy", requests_source_write=True,
        )
        self.assertEqual(result, {
            "relationship_id": "rel", "generation_id": "gen",
            "source_access": "isolated_copy", "managed_source_writable": False,
            "output_boundary": "artifacts",
        })

    def test_out_of_band_digest_is_bounded_divergence_not_adopted(self):
        policy = ManagedSourceProjection()
        divergence = policy.detect_divergence(
            relationship_id="rel", generation_id="gen",
            expected_digest="a" * 64, observed_digest="b" * 64,
            affected_count=3, detected_at="2026-08-26T00:00:01Z",
        )
        self.assertEqual(divergence.affected_count, 3)
        self.assertNotIn("digest", repr(divergence).lower())
