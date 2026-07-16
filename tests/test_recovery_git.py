import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.git import GitCapture
from sandbox.services.process import ProcessResult


class GitRunner:
    def __init__(self): self.calls = []
    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        command = tuple(argv)
        if command[1:3] == ("rev-parse", "HEAD"): output = "deadbeef\n"
        elif command[1:4] == ("remote", "get-url", "origin"): output = "git@example.test:site.git\n"
        elif command[1] == "status": output = " M README.md\n?? .env\n"
        elif command[1] == "diff": output = "diff --git a/README.md b/README.md\n"
        elif command[1:3] == ("bundle", "create"):
            Path(command[3]).write_bytes(b"bundle"); output = ""
        else: output = ""
        return ProcessResult(command, 0, output, "")


class TestGitCapture(unittest.TestCase):
    def test_records_remote_revision_and_excludes_sensitive_dirty_state(self):
        info = GitCapture(GitRunner()).provenance(".")
        self.assertEqual(info["revision"], "deadbeef")
        self.assertEqual(info["remote"], "git@example.test:site.git")
        self.assertEqual(info["dirty"], (" M README.md",))
        self.assertEqual(info["ignored_sensitive"], ("?? .env",))

    def test_bundle_is_verified_after_creation(self):
        runner = GitRunner()
        with tempfile.TemporaryDirectory() as directory:
            bundle = GitCapture(runner).create_bundle(".", Path(directory) / "changes.bundle", "deadbeef")
        self.assertTrue(any(call[0][1:3] == ("bundle", "verify") for call in runner.calls))
        self.assertEqual(bundle.name, "changes.bundle")

    def test_patch_requires_explicit_non_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = GitCapture(GitRunner())
            patch = capture.create_patch(".", Path(directory) / "changes.patch", ("README.md",))
            self.assertIn("diff --git", patch.read_text())
            with self.assertRaisesRegex(Exception, "sensitive"):
                capture.create_patch(".", Path(directory) / "unsafe.patch", (".env",))

    def test_rejects_option_like_git_tokens_and_unsafe_destinations(self):
        capture = GitCapture(GitRunner())
        with self.assertRaisesRegex(Exception, "remote"):
            capture.provenance(".", remote="--all")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(Exception, "revision"):
                capture.create_bundle(".", Path(directory) / "changes.bundle", "--all")
            with self.assertRaisesRegex(Exception, "destination"):
                capture.create_bundle(".", Path(directory) / "-bundle", "HEAD")

    def test_rejects_symlink_bundle_and_patch_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target"; target.write_bytes(b"keep")
            bundle = root / "bundle"; bundle.symlink_to(target)
            patch = root / "patch"; patch.symlink_to(target)
            capture = GitCapture(GitRunner())
            with self.assertRaisesRegex(Exception, "destination"):
                capture.create_bundle(".", bundle, "HEAD")
            with self.assertRaisesRegex(Exception, "destination"):
                capture.create_patch(".", patch, ("README.md",))
            self.assertEqual(target.read_bytes(), b"keep")
