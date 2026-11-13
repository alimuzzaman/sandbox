"""Physical anchors must meet the exact recovery permission contract."""
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from sandbox.hosting.target_lease import SCRIPT
from tests.subprocess_support import run_test_process


class TargetLeasePermissionsTests(unittest.TestCase):
    def run_lease(self, path, action="acquire"):
        return run_test_process(
            ["/bin/sh", "-c", "umask 002\n" + SCRIPT, "sandbox-lease",
             str(path), "a" * 32, "b" * 32, action],
            env={"PATH": "/usr/bin:/bin", "HOME": str(path.parent)},
            capture_output=True, timeout=5,
        )

    def test_new_anchor_is_private_even_with_group_writable_caller_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "example.lease"
            result = self.run_lease(lease)
            # The anchor is created before flock. On platforms without flock,
            # its real permission effect is still observable, not acquisition.
            self.assertIn(result.returncode, (0, 46), result.stderr)
            self.assertEqual(stat.S_IMODE(Path(str(lease) + ".lock").stat().st_mode), 0o600)

    def test_existing_anchor_is_not_replaced_or_permission_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "example.lease"
            lock = Path(str(lease) + ".lock")
            lock.touch(mode=0o600)
            lock.chmod(0o664)
            before = lock.stat()
            result = self.run_lease(lease)
            self.assertIn(result.returncode, (0, 46), result.stderr)
            after = lock.stat()
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o664)

    @unittest.skipUnless(shutil.which("flock", path="/usr/bin:/bin"), "requires real flock")
    def test_acquire_and_release_preserve_private_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "example.lease"
            self.assertEqual(self.run_lease(lease).returncode, 0)
            self.assertEqual(stat.S_IMODE(lease.stat().st_mode), 0o600)
            self.assertEqual(self.run_lease(lease, "release").returncode, 0)
            self.assertFalse(lease.exists())
            self.assertEqual(stat.S_IMODE(Path(str(lease) + ".lock").stat().st_mode), 0o600)
