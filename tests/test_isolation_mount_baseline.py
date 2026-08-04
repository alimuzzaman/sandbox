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


class TestImageInodeGeometry(unittest.TestCase):
    """mke2fs rounds an inode request up to a whole block group."""

    def setUp(self):
        self.helper = _helper()

    def source(self):
        path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py").read_text()
        return path.split("def resource_limits_match", 1)[1].split("\ndef ", 1)[0]

    def test_the_inode_count_is_a_floor_not_an_equality(self):
        # Asking for 500000 produced 500736 on the proof host, so an exact
        # comparison could never pass and every machine failed the gate.
        body = self.source()
        self.assertNotIn('fields["Inode count"] != expected["inodes"]', body)
        self.assertIn("Inodes per group", body)
        self.assertIn("surplus", body)

    def test_the_surplus_is_bounded_by_one_block_group(self):
        # A floor alone would accept any oversized filesystem; the bound is what
        # keeps this an assertion about the geometry that was asked for.
        body = self.source()
        self.assertIn('surplus >= fields["Inodes per group"]', body)
        self.assertIn("surplus < 0", body)


class TestResourceGateBeforeServicesExist(unittest.TestCase):
    """Isolation is proven before any service is activated, on purpose."""

    def source(self):
        path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                / "native-helper.py").read_text()
        return path.split("def resource_limits_match", 1)[1].split("\ndef ", 1)[0]

    def test_service_level_ceilings_are_not_demanded_before_services_run(self):
        # Web, PHP, database and cron stay masked until the boundary is proven,
        # so querying mariadb or php-fpm at that point can only fail -- and did,
        # on a correctly built stack.
        body = self.source()
        gate = body.split("    units = (", 1)[0]
        for probe in ("max_connections", "php-fpm8.3", "nginx -T", "apache2ctl"):
            self.assertNotIn(probe, gate)

    def test_the_machine_ceilings_are_still_checked_before_services_run(self):
        # The unit ceilings and image geometry are the isolation-relevant half
        # and must hold at that point.
        gate = self.source().split("    units = (", 1)[0]
        for name in ("MemoryMax", "TasksMax", "LimitNOFILE", "CPUQuotaPerSecUSec",
                     "Block count", "Inode count"):
            self.assertIn(name, gate)

    def test_the_ceilings_are_still_checked_once_services_are_running(self):
        # Skipping them before activation must not mean never checking them.
        body = self.source()
        after = body.split("guest_unit_load_states", 1)[1]
        for probe in ("max_connections", "php-fpm8.3", "worker_connections"):
            self.assertIn(probe, after)
