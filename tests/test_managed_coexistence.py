"""Contracts that prevent managed-native installation from changing host services."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest


class SystemctlProcess:
    """Deterministic foreign-service observer with no host interaction."""

    def run(self, argv, **_kwargs):
        unit = argv[2]
        state = {
            "nginx.service": "LoadState=loaded\nActiveState=active\n"
                             "UnitFileState=enabled\nFragmentPath=/etc/nginx/nginx.service\n",
            "apache2.service": "LoadState=not-found\nActiveState=inactive\n"
                               "UnitFileState=disabled\nFragmentPath=\n",
            "mariadb.service": "LoadState=loaded\nActiveState=active\n"
                               "UnitFileState=enabled\nFragmentPath=/etc/systemd/system/mariadb.service\n",
            "mysql.service": "LoadState=not-found\nActiveState=inactive\n"
                             "UnitFileState=disabled\nFragmentPath=\n",
            "php8.3-fpm.service": "LoadState=loaded\nActiveState=active\n"
                                  "UnitFileState=enabled\nFragmentPath=/etc/systemd/system/php8.3-fpm.service\n",
        }[unit]
        return type("Result", (), {"stdout": state})()


class TestManagedCoexistence(unittest.TestCase):
    def test_baseline_observes_active_enabled_config_and_data_exactly(self):
        from sandbox.runtimes.managed.packages import HostServiceBaseline

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            data = root / "data"
            config.mkdir()
            data.mkdir()
            config_file = config / "foreign.conf"
            data_file = data / "ibdata1"
            config_file.write_text("worker_processes 2\n")
            data_file.write_bytes(b"original-data")
            config_original = config_file.stat()
            original = data_file.stat()
            observer = HostServiceBaseline(
                process=SystemctlProcess(), config_roots=(config,), data_roots=(data,),
            )

            before = observer.observe()
            self.assertEqual(before["units"]["nginx.service"]["ActiveState"], "active")
            self.assertEqual(before["units"]["nginx.service"]["UnitFileState"], "enabled")
            self.assertEqual(before, observer.observe())

            config_file.write_text("worker_processes 3\n")
            self.assertNotEqual(before, observer.observe())
            config_file.write_text("worker_processes 2\n")
            os.utime(config_file, ns=(config_original.st_atime_ns, config_original.st_mtime_ns))
            self.assertEqual(before, observer.observe())

            # A same-size write with restored timestamps must still be observed.
            # Metadata-only data digests cannot prove a foreign database stayed unchanged.
            data_file.write_bytes(b"changed-data!")
            os.utime(data_file, ns=(original.st_atime_ns, original.st_mtime_ns))
            self.assertNotEqual(before, observer.observe())

    def test_host_baseline_drift_is_blocked_and_reports_persistent_apply_mutation(self):
        from sandbox.runtimes.managed.packages import ManagedPackageService
        from tests.test_managed_package_plan import TestManagedPackagePlan

        plan = TestManagedPackagePlan().planner().plan()
        baseline = iter(({"units": {"nginx.service": {"ActiveState": "active"}}},
                         {"units": {"nginx.service": {"ActiveState": "inactive"}}}))
        applied = []
        service = ManagedPackageService(
            replanner=lambda: plan,
            apply_transaction=lambda current: applied.append(current) or {
                "ok": True, "state": "ready", "mutated": True,
            },
            baseline_observer=lambda: next(baseline),
            confirmation=lambda _plan: True,
        )

        result = service.apply(plan, interactive=True)
        self.assertEqual(result["reason"]["code"], "host_service_baseline_changed")
        self.assertTrue(result["mutated"])
        self.assertEqual(applied, [plan])


if __name__ == "__main__":
    unittest.main()
