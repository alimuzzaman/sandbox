import subprocess
import tempfile
import unittest
from pathlib import Path

from sandbox.sync.capture import UnstableCapture, capture_manifest


class SyncCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=self.root, check=True)
        (self.root / "source.txt").write_text("first\n")
        subprocess.run(["git", "add", "source.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_second_view_retries_then_returns_one_coherent_generation(self):
        def change_once(attempt, _entries):
            if attempt == 0:
                (self.root / "source.txt").write_text("second\n")

        manifest = capture_manifest(self.root, retries=1, after_first_view=change_once)

        self.assertEqual(manifest.entries[0].sha256, __import__("hashlib").sha256(b"second\n").hexdigest())
        self.assertIsNotNone(manifest.dirty_digest)

    def test_repeated_second_view_change_fails_closed(self):
        def always_change(attempt, _entries):
            (self.root / "source.txt").write_text(f"changed-{attempt}\n")

        with self.assertRaises(UnstableCapture):
            capture_manifest(self.root, retries=1, after_first_view=always_change)

    def test_new_untracked_file_during_capture_is_detected_as_a_race(self):
        def add_file(_attempt, _entries):
            (self.root / "new-file.txt").write_text("new\n")

        with self.assertRaises(UnstableCapture):
            capture_manifest(self.root, retries=0, after_first_view=add_file)

    def test_nested_project_manifest_paths_remain_git_relative(self):
        nested = self.root / "plugin"
        nested.mkdir()
        (nested / "plugin.php").write_text("<?php\n")
        subprocess.run(["git", "add", "plugin/plugin.php"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "nested"], cwd=self.root, check=True)

        manifest = capture_manifest(nested)

        self.assertEqual([item.path for item in manifest.entries], ["plugin/plugin.php"])
        self.assertEqual(manifest.git_root, self.root.resolve())

    def test_storage_volume_is_excluded_from_host_source_generation(self):
        storage = self.root / "storage"
        storage.mkdir()
        (storage / "runtime.db").write_text("local state\n")
        (self.root / "source.txt").write_text("updated\n")

        manifest = capture_manifest(self.root)

        self.assertEqual([item.path for item in manifest.entries], ["source.txt"])
        self.assertEqual(manifest.excluded_count, 1)


if __name__ == "__main__":
    unittest.main()
