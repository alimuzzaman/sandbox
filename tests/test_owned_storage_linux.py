"""Unit tests for Linux filesystem adapter and synthetic fallback mechanics."""

import os
import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage.adapters.linux import (
    FileSystemAdapterError,
    LinuxFilesystemAdapter,
    OpenBeneathError,
    RenameNoReplaceError,
)


class TestLinuxFilesystemAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name) / "root"
        self.root.mkdir(parents=True, exist_ok=True)
        self.adapter = LinuxFilesystemAdapter(self.root)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_owner_only_permissions(self):
        dir_path = self.root / "sub"
        self.adapter.ensure_directory(dir_path)
        self.assertTrue(dir_path.exists())
        mode = os.stat(dir_path).st_mode & 0o777
        self.assertEqual(mode, 0o700)

        file_path = dir_path / "test.txt"
        self.adapter.write_file_bytes(file_path, b"hello")
        self.assertTrue(file_path.exists())
        file_mode = os.stat(file_path).st_mode & 0o777
        self.assertEqual(file_mode, 0o600)

    def test_open_beneath_rejects_parent_escape(self):
        with self.assertRaises(OpenBeneathError):
            self.adapter.open_beneath("../outside.txt")

    def test_open_beneath_rejects_symlink_escape(self):
        target = self.root.parent / "escape_target"
        target.mkdir(parents=True, exist_ok=True)
        link = self.root / "link_out"
        try:
            os.symlink(target, link)
        except OSError:
            self.skipTest("Symlinks not supported in environment")

        with self.assertRaises(OpenBeneathError):
            self.adapter.open_beneath("link_out")

    def test_rename_noreplace_succeeds_for_new_dest(self):
        src = self.root / "src"
        dst = self.root / "dst"
        self.adapter.ensure_directory(src)
        self.adapter.write_file_bytes(src / "data.bin", b"payload")

        self.adapter.rename_noreplace(src, dst)
        self.assertFalse(src.exists())
        self.assertTrue(dst.exists())
        self.assertTrue((dst / "data.bin").exists())

    def test_rename_noreplace_refuses_when_dest_exists(self):
        src = self.root / "src"
        dst = self.root / "dst"
        self.adapter.ensure_directory(src)
        self.adapter.ensure_directory(dst)

        with self.assertRaises(RenameNoReplaceError):
            self.adapter.rename_noreplace(src, dst)

    def test_stat_identity(self):
        dir_path = self.root / "ident_dir"
        self.adapter.ensure_directory(dir_path)
        ident = self.adapter.stat_identity(dir_path)
        self.assertIn("inode", ident)
        self.assertIn("device", ident)
        self.assertIsInstance(ident["inode"], int)

    def test_remove_tree_beneath(self):
        tree = self.root / "tree"
        self.adapter.ensure_directory(tree)
        self.adapter.ensure_directory(tree / "nested")
        self.adapter.write_file_bytes(tree / "nested" / "file.txt", b"abc")

        self.adapter.remove_tree_beneath(tree)
        self.assertFalse((tree / "nested").exists())
        # The tree root itself remains or is cleaned up as required
        self.assertEqual(list(tree.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
