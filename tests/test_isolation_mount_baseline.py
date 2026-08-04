"""nspawn's own hardening mounts are not the host leaking in (039 T047)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _helper():
    path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
            / "native-helper.py")
    spec = importlib.util.spec_from_file_location("native_helper_mounts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNspawnBaselineMounts(unittest.TestCase):
    def setUp(self):
        self.helper = _helper()

    def observed(self, filesystem, *, read_only):
        return {"filesystem": filesystem, "source": filesystem, "root": "/",
                "options": {"ro"} if read_only else {"rw"}}

    def test_a_read_only_mask_is_accepted_as_nspawn_made_it(self):
        for target in ("/proc/sys", "/proc/acpi", "/sys/kernel", "/sys/block"):
            with self.subTest(target=target):
                filesystem = "proc" if target.startswith("/proc") else "sysfs"
                self.assertTrue(self.helper._nspawn_baseline_mount(
                    target, self.observed(filesystem, read_only=True)))

    def test_a_mask_that_is_no_longer_read_only_is_still_reported(self):
        # The whole value of these mounts is that they are read-only. One that
        # is writable is drift, not baseline.
        self.assertFalse(self.helper._nspawn_baseline_mount(
            "/proc/sys", self.observed("proc", read_only=False)))
        self.assertFalse(self.helper._nspawn_baseline_mount(
            "/run/host", self.observed("tmpfs", read_only=False)))

    def test_a_baseline_target_carrying_another_filesystem_is_reported(self):
        # Same path, different filesystem, is someone else's mount.
        self.assertFalse(self.helper._nspawn_baseline_mount(
            "/proc/sys", self.observed("ext4", read_only=True)))

    def test_a_path_outside_the_baseline_is_never_accepted(self):
        for target in ("/etc/shadow", "/var/lib/docker", "/home", "/run/secrets"):
            with self.subTest(target=target):
                self.assertFalse(self.helper._nspawn_baseline_mount(
                    target, self.observed("tmpfs", read_only=True)))

    def test_the_writable_entries_are_namespaced_or_machine_owned(self):
        # Each writable entry is deliberate; assert the set rather than letting
        # one be added silently.
        writable = {target for target, (_fs, read_only)
                    in self.helper.NSPAWN_BASELINE_MOUNTS.items() if not read_only}
        self.assertEqual(writable, {
            "/proc/sys/net", "/dev/net/tun", "/tmp", "/run/lock",
            "/sys/fs/cgroup", "/proc/kmsg", "/proc/sys/kernel/random/boot_id",
        })


if __name__ == "__main__":
    unittest.main()
