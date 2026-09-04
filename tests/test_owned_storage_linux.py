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

    def test_prepare_ci_materialization_and_confinement(self):
        proj_id = "proj_test_ci"
        ws_id = "ws_test_ci"
        obj_id = "obj_test_ci"

        source_dir = self.root / "sources" / "src_1"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "read_only_seed.txt").write_text("immutable seed")

        bundle = self.adapter.prepare_ci_materialization(
            project_identity=proj_id,
            workspace_id=ws_id,
            object_id=obj_id,
            source_path=source_dir,
        )

        self.assertIn("root_fd", bundle)
        self.assertIn("work_fd", bundle)
        self.assertIn("source_fd", bundle)
        self.assertEqual(bundle["object_id"], obj_id)

        # File descriptors must be valid open descriptors
        self.assertIsInstance(os.fstat(bundle["root_fd"]).st_ino, int)
        self.assertIsInstance(os.fstat(bundle["work_fd"]).st_ino, int)
        self.assertIsInstance(os.fstat(bundle["source_fd"]).st_ino, int)

        # Writable interior verification
        obj_root = bundle["object_root"]
        work_path = bundle["work_path"]
        self.assertTrue(self.adapter.verify_interior_confinement(obj_root, work_path / "new_file.txt"))

        # Writing outside work/ must be rejected
        self.assertFalse(self.adapter.verify_interior_confinement(obj_root, obj_root / "escaped.txt"))
        self.assertFalse(self.adapter.verify_interior_confinement(obj_root, obj_root / "meta" / "evil.txt"))
        self.assertFalse(self.adapter.verify_interior_confinement(obj_root, self.root / "other_project" / "file.txt"))

        # Clean up descriptors
        os.close(bundle["root_fd"])
        os.close(bundle["work_fd"])
        os.close(bundle["source_fd"])

    def test_mount_controller_descriptor_handoff(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import importlib
        mc = importlib.import_module("tools.owned-storage-mount-controller")

        controller = mc.MountController(runtime_root=self.root / "run")

        bundle = self.adapter.prepare_ci_materialization(
            project_identity="proj_mc",
            workspace_id="ws_mc",
            object_id="obj_mc",
        )

        res = controller.mount_materialization(
            work_fd=bundle["work_fd"],
            source_fd=bundle["source_fd"],
        )

        self.assertTrue(res["ok"])
        self.assertIn("mount_identity_digest", res)
        self.assertTrue(res["mount_identity_digest"].startswith("sha256:"))
        self.assertEqual(res["work_access"], "read-write")
        self.assertEqual(res["root_access"], "read-only")

        # Invalid descriptor rejection
        with self.assertRaises(mc.MountControllerError):
            controller.mount_materialization(work_fd=99999)

        # Release mount
        unm = controller.unmount_materialization(res["mount_identity_digest"])
        self.assertTrue(unm["ok"])

        os.close(bundle["root_fd"])
        os.close(bundle["work_fd"])


if __name__ == "__main__":
    unittest.main()


