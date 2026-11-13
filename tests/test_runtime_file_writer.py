"""Nonsecret writer subprocess checks, not retained-owner acceptance."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from sandbox.hosting.runtime_file_writer import PROGRAM
from tests.subprocess_support import run_test_process


class RuntimeFileWriterTest(unittest.TestCase):
    def invoke(self, path, payload=b"public fixture\n"):
        frame = len(payload).to_bytes(8, "big") + payload
        return run_test_process([sys.executable, "-c", PROGRAM, str(path), "0600"],
            input=frame, capture_output=True, timeout=10,
            env={"HOME": str(path.parent), "SANDBOX_AUTO_UPDATE": "0"})

    def test_publish_and_idempotent_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "new-parent" / "sample"
            first = self.invoke(path)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(json.loads(first.stdout), {"ok": True, "status": "published"})
            identity = path.stat().st_ino
            second = self.invoke(path)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(json.loads(second.stdout), {"ok": True, "status": "already_current"})
            self.assertEqual(path.read_bytes(), b"public fixture\n")
            self.assertEqual(path.stat().st_ino, identity)

    @unittest.skipUnless(hasattr(os, "O_PATH") and os.geteuid() != 0,
                         "requires non-root Linux search-only directory semantics")
    def test_search_only_ancestor_does_not_require_listing_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            ancestor = Path(temporary).resolve() / "search-only"
            parent = ancestor / "private"
            parent.mkdir(parents=True, mode=0o700)
            ancestor.chmod(0o111)
            try:
                with self.assertRaises(PermissionError):
                    os.open(ancestor, os.O_RDONLY | os.O_DIRECTORY)
                result = self.invoke(parent / "sample")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"ok": True, "status": "published"})
            finally:
                ancestor.chmod(0o700)

    def test_symlink_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            actual = root / "actual"
            actual.mkdir(mode=0o700)
            link = root / "alias"
            link.symlink_to(actual, target_is_directory=True)
            result = self.invoke(link / "sample")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {"ok": False, "status": "not_published"})
            self.assertFalse((actual / "sample").exists())

    def test_group_writable_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve() / "unsafe"
            parent.mkdir()
            parent.chmod(0o770)
            result = self.invoke(parent / "sample")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout), {"ok": False, "status": "not_published"})
            self.assertFalse((parent / "sample").exists())


if __name__ == "__main__":
    unittest.main()
