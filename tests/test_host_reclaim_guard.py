"""Shared host reclaim guard and host-apply lock probe (spec 057, FR-018)."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from sandbox.resources import host_guard


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(tempfile.mkdtemp()) / "runtime"
        self.addCleanup(shutil.rmtree, self.runtime.parent, ignore_errors=True)

    def test_guard_is_created_owner_only_under_runtime_resources(self):
        with host_guard.try_reclaim_guard(runtime=self.runtime) as acquired:
            self.assertTrue(acquired)
        path = host_guard.reclaim_guard_path(self.runtime)
        self.assertEqual(path, self.runtime / "resources" / "reclaim-host.lock")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_second_holder_is_busy_and_release_frees_it(self):
        with host_guard.try_reclaim_guard(runtime=self.runtime) as first:
            self.assertTrue(first)
            with host_guard.try_reclaim_guard(runtime=self.runtime) as second:
                self.assertFalse(second)
        with host_guard.try_reclaim_guard(runtime=self.runtime) as again:
            self.assertTrue(again)

    def test_guard_refuses_a_symlinked_lock_file(self):
        target = self.runtime.parent / "elsewhere"
        target.write_text("")
        (self.runtime / "resources").mkdir(parents=True)
        host_guard.reclaim_guard_path(self.runtime).symlink_to(target)
        with host_guard.try_reclaim_guard(runtime=self.runtime) as acquired:
            self.assertFalse(acquired)


class ApplyLockProbeCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.lock = self.root / "apply.lock"

    def test_missing_lock_file_is_not_held(self):
        self.assertFalse(host_guard.apply_transaction_active((self.lock,)))
        self.assertFalse(self.lock.exists())

    def test_free_lock_file_is_not_held(self):
        self.lock.write_text("")
        self.assertFalse(host_guard.apply_transaction_active((self.lock,)))

    def test_exclusively_held_lock_reports_busy(self):
        self.lock.write_text("")
        descriptor = os.open(str(self.lock), os.O_RDWR)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.assertTrue(host_guard.apply_transaction_active((self.lock,)))
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        self.assertFalse(host_guard.apply_transaction_active((self.lock,)))


class LockPathParityCase(unittest.TestCase):
    """The guard duplicates three host-apply lock literals; keep them equal."""

    def test_caddy_hosting_lock_matches(self):
        from sandbox.commands import hosting

        self.assertIn(hosting._CADDY_LOCK_PATH, host_guard.APPLY_TRANSACTION_LOCKS)

    def test_nginx_edge_lock_matches(self):
        from sandbox.hosting.front_door import nginx

        self.assertIn(nginx.LOCK_PATH, host_guard.APPLY_TRANSACTION_LOCKS)

    def test_docker_pool_lock_matches(self):
        from sandbox.core import _remote

        program = _remote._remote_docker_pool_program(confirm=False)
        literal = next(
            line.split('"')[1] for line in program.splitlines()
            if line.startswith("LOCK = pathlib.Path(")
        )
        self.assertIn(literal, host_guard.APPLY_TRANSACTION_LOCKS)

    def test_exactly_three_apply_locks(self):
        self.assertEqual(len(host_guard.APPLY_TRANSACTION_LOCKS), 3)

    def test_probe_program_uses_the_same_literals(self):
        from sandbox.resources.remote import _REMOTE_PROGRAM

        for path in host_guard.APPLY_TRANSACTION_LOCKS:
            self.assertTrue(f'"{path}"' in _REMOTE_PROGRAM, path)
        self.assertTrue(f'"{host_guard.GUARD_NAME}"' in _REMOTE_PROGRAM)


if __name__ == "__main__":
    unittest.main()
