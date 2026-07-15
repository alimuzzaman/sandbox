import tarfile
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.filesystem import FilesystemCapture, archive_paths, validate_archive


class TestFilesystemCapture(unittest.TestCase):
    def test_archives_explicit_members_with_relative_names_and_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"; root.mkdir()
            (root / "uploads").mkdir(); (root / "uploads" / "a.txt").write_text("value")
            archive = archive_paths(root, (root / "uploads",), Path(directory) / "set.tar")
            self.assertEqual(validate_archive(archive), ("uploads", "uploads/a.txt"))
            with tarfile.open(archive) as opened:
                member = opened.getmember("uploads/a.txt")
                self.assertTrue(member.isfile())
                self.assertIsInstance(member.uid, int)

    def test_reports_acl_xattr_fallback_and_detects_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"; root.mkdir(); source = root / "file"; source.write_text("before")
            receipt = FilesystemCapture().capture(root, (source,), Path(directory) / "set.tar")
            self.assertIn("ACL/xattr", receipt["warnings"][0])
            with mock.patch.object(tarfile.TarFile, "add", side_effect=lambda *args, **kwargs: source.write_text("after")):
                with self.assertRaisesRegex(RecoveryError, "changed"):
                    archive_paths(root, (source,), Path(directory) / "changed.tar")

    def test_preserves_in_root_symlinks_without_following_the_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"; root.mkdir()
            target = root / "target.txt"; target.write_text("value")
            link = root / "link.txt"; link.symlink_to("target.txt")
            archive = archive_paths(root, (link,), Path(directory) / "link.tar")
            with tarfile.open(archive) as opened:
                member = opened.getmember("link.txt")
                self.assertTrue(member.issym())
                self.assertEqual(member.linkname, "target.txt")

    def test_rejects_member_traversal_and_escaping_link(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.tar"
            with tarfile.open(archive, "w") as opened:
                item = tarfile.TarInfo("../outside"); item.size = 0; opened.addfile(item)
            with self.assertRaisesRegex(RecoveryError, "unsafe"):
                validate_archive(archive)
            archive = Path(directory) / "link.tar"
            with tarfile.open(archive, "w") as opened:
                item = tarfile.TarInfo("ok-link"); item.type = tarfile.SYMTYPE; item.linkname = "../../outside"; opened.addfile(item)
            with self.assertRaisesRegex(RecoveryError, "escapes"):
                validate_archive(archive)

    def test_rejects_duplicate_and_special_members(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.tar"
            with tarfile.open(duplicate, "w") as opened:
                first = tarfile.TarInfo("same"); first.size = 0; opened.addfile(first)
                second = tarfile.TarInfo("same"); second.size = 0; opened.addfile(second)
            with self.assertRaisesRegex(RecoveryError, "duplicate"):
                validate_archive(duplicate)
            special = Path(directory) / "special.tar"
            with tarfile.open(special, "w") as opened:
                item = tarfile.TarInfo("device"); item.type = tarfile.CHRTYPE; item.devmajor = 1; item.devminor = 3
                opened.addfile(item)
            with self.assertRaisesRegex(RecoveryError, "special"):
                validate_archive(special)

    def test_rejects_dot_segments_and_normalized_duplicate_members(self):
        with tempfile.TemporaryDirectory() as directory:
            traversal = Path(directory) / "dot.tar"
            with tarfile.open(traversal, "w") as opened:
                item = tarfile.TarInfo("a/.."); item.size = 0; opened.addfile(item)
            with self.assertRaisesRegex(RecoveryError, "unsafe"):
                validate_archive(traversal)

            duplicate = Path(directory) / "normalized-duplicate.tar"
            with tarfile.open(duplicate, "w") as opened:
                first = tarfile.TarInfo("a/b"); first.size = 0; opened.addfile(first)
                second = tarfile.TarInfo("a/./b"); second.size = 0; opened.addfile(second)
            with self.assertRaisesRegex(RecoveryError, "unsafe"):
                validate_archive(duplicate)
