"""Physical anchors must meet the exact recovery permission contract."""
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from sandbox.hosting.target_lease import SCRIPT
from tests.subprocess_support import run_test_process

# The in-place repair needs the anchor's flock and its /proc fd (Linux remotes).
TIGHTENS = bool(shutil.which("flock", path="/usr/bin:/bin")) and Path("/proc/self/fd").is_dir()


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

    def legacy_anchor(self, directory, mode=0o664):
        lease = Path(directory) / "example.lease"
        lock = Path(str(lease) + ".lock")
        lock.touch(mode=0o600)
        lock.chmod(mode)
        return lease, lock

    @unittest.skipUnless(TIGHTENS, "requires real flock and /proc")
    def test_legacy_group_writable_anchor_is_tightened_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            lease, lock = self.legacy_anchor(directory)
            before = lock.stat()
            result = self.run_lease(lease)
            self.assertEqual(result.returncode, 0, result.stderr)
            after = lock.stat()
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)

    def test_existing_anchor_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            lease, lock = self.legacy_anchor(directory)
            before = lock.stat()
            result = self.run_lease(lease)
            # Without flock the script stops before any repair; the inode stays either way.
            self.assertIn(result.returncode, (0, 46), result.stderr)
            after = lock.stat()
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            if result.returncode == 46:
                self.assertEqual(stat.S_IMODE(after.st_mode), 0o664)

    @unittest.skipUnless(TIGHTENS, "requires real flock and /proc")
    def test_anchor_with_a_lease_body_is_left_as_it_is(self):
        with tempfile.TemporaryDirectory() as directory:
            lease, lock = self.legacy_anchor(directory)
            lease.write_text(f"{'c' * 32} {'d' * 32} 1\n")
            lease.chmod(0o600)
            result = self.run_lease(lease)
            self.assertEqual(result.returncode, 45, result.stderr)  # held by another owner
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o664)
            self.assertEqual(lease.read_text().split()[1], "d" * 32)

    @unittest.skipUnless(TIGHTENS, "requires real flock and /proc")
    def test_hard_linked_anchor_is_left_as_it_is(self):
        with tempfile.TemporaryDirectory() as directory:
            lease, lock = self.legacy_anchor(directory)
            os.link(lock, Path(directory) / "other-name")
            result = self.run_lease(lease)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o664)

    @unittest.skipUnless(TIGHTENS, "requires real flock and /proc")
    def test_symlinked_anchor_target_is_left_as_it_is(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "example.lease"
            target = Path(directory) / "elsewhere"
            target.touch(mode=0o600)
            target.chmod(0o664)
            Path(str(lease) + ".lock").symlink_to(target)
            result = self.run_lease(lease)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o664)

    @unittest.skipUnless(shutil.which("flock", path="/usr/bin:/bin"), "requires real flock")
    def test_acquire_and_release_preserve_private_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = Path(directory) / "example.lease"
            self.assertEqual(self.run_lease(lease).returncode, 0)
            self.assertEqual(stat.S_IMODE(lease.stat().st_mode), 0o600)
            self.assertEqual(self.run_lease(lease, "release").returncode, 0)
            self.assertFalse(lease.exists())
            self.assertEqual(stat.S_IMODE(Path(str(lease) + ".lock").stat().st_mode), 0o600)
