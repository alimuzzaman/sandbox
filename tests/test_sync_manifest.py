import subprocess
import tempfile
import unittest
from pathlib import Path

from sandbox.sync.capture import ManifestLimitExceeded, capture_manifest
from sandbox.sync.policy import CredentialDetected


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=self.root, check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content="fixture\n"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit_all(self):
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)


class SyncManifestTests(GitFixture):
    def test_manifest_is_sorted_git_relative_and_content_stable(self):
        self.write("zeta.txt", "z\n")
        executable = self.write("bin/tool", "#!/bin/sh\n")
        executable.chmod(0o755)
        self.commit_all()

        first = capture_manifest(self.root)
        second = capture_manifest(self.root)

        self.assertEqual([item.path for item in first.entries], ["bin/tool", "zeta.txt"])
        self.assertTrue(first.entries[0].executable)
        self.assertEqual(first.manifest_digest, second.manifest_digest)
        self.assertEqual(first.generation_id, second.generation_id)
        self.assertNotIn(str(self.root), str(first.canonical_entries()))

    def test_ordinary_build_runtime_database_log_and_symlink_inputs_are_omitted(self):
        self.write("source.php", "<?php echo 'safe';\n")
        self.write("node_modules/pkg/index.js", "safe module\n")
        self.write("build/app.js", "safe build\n")
        self.write("runtime/state.json", "{}\n")
        self.write("data.sqlite3", "not a database secret\n")
        self.write("debug.log", "safe log\n")
        (self.root / "linked.php").symlink_to(self.root / "source.php")
        self.commit_all()

        manifest = capture_manifest(self.root)

        self.assertEqual([item.path for item in manifest.entries], ["source.php"])
        self.assertEqual(manifest.excluded_count, 6)

    def test_tracked_credential_name_rejects_the_complete_generation(self):
        self.write("safe.txt")
        self.write(".env", "EMPTY=\n")
        self.commit_all()
        with self.assertRaises(CredentialDetected) as raised:
            capture_manifest(self.root)
        self.assertNotIn(".env", str(raised.exception))

    def test_untracked_credential_content_rejects_the_complete_generation(self):
        self.write("safe.txt")
        self.commit_all()
        self.write("local-config.txt", "api_key=synthetic_fixture_value_12345\n")
        with self.assertRaises(CredentialDetected):
            capture_manifest(self.root)

    def test_modified_tracked_credential_content_rejects_the_complete_generation(self):
        self.write("config.txt", "safe fixture\n")
        self.commit_all()
        self.write("config.txt", "password=synthetic_fixture_value\n")
        with self.assertRaises(CredentialDetected):
            capture_manifest(self.root)

    def test_explicit_ignored_credential_is_screened(self):
        self.write("safe.txt")
        self.write(".gitignore", "ignored/\n")
        self.commit_all()
        self.write("ignored/config.txt", "password=synthetic_fixture_value\n")
        with self.assertRaises(CredentialDetected):
            capture_manifest(self.root, explicit_includes=("ignored/config.txt",))

    def test_path_and_byte_limits_are_enforced_before_manifest_return(self):
        self.write("one.txt", "one")
        self.write("two.txt", "two")
        self.commit_all()
        with self.assertRaises(ManifestLimitExceeded):
            capture_manifest(self.root, max_files=1)
        with self.assertRaises(ManifestLimitExceeded):
            capture_manifest(self.root, max_bytes=5)


if __name__ == "__main__":
    unittest.main()
