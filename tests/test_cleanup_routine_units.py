"""Host timer render, install and remove for the cleanup routine (spec 057)."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sandbox.resources.cleanup_routine import units
from sandbox.resources.cleanup_routine.contract import RoutineError

SB = "/home/sandbox/sandbox/sb-src/sb"
HOME = "/home/sandbox/sandbox"
CONFIG = {
    "cadence": "daily", "timeout": "30min", "randomized_delay": "5min",
}
ANALYZE_OK = (
    "  Original form: daily\n"
    "Normalized form: *-*-* 00:00:00\n"
    "    Next elapse: Fri 2026-10-09 00:00:00 UTC\n"
    "       (in UTC): Fri 2026-10-09 00:00:00 UTC\n"
    "       From now: 10h left\n"
)


class FakeSystem:
    """Record bounded host commands and answer like a lingering systemd host."""

    def __init__(self, *, linger="yes", analyze=(0, ANALYZE_OK), fail=None):
        self.calls = []
        self.linger = linger
        self.analyze = analyze
        self.fail = set(fail or ())

    def __call__(self, argv, timeout=15):
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        for needle in self.fail:
            if needle in joined:
                return 1, ""
        if argv[:2] == ["systemd-analyze", "calendar"]:
            return self.analyze
        if argv[0] == "loginctl":
            return 0, self.linger + "\n"
        if "show" in argv:
            return 0, "Fri 2026-10-09 00:03:12 UTC\n"
        return 0, ""


class UnitsCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        os.chmod(self.home.name, 0o700)
        patcher = patch.dict(os.environ, {"HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        which = patch.object(units.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}")
        self.which = which.start()
        self.addCleanup(which.stop)
        self.unit_dir = Path(self.home.name) / ".config" / "systemd" / "user"

    def install(self, system, config=CONFIG):
        with patch.object(units, "_run", system):
            return units.install(config, sb_path=SB, sandbox_home=HOME)


class RenderTests(UnitsCase):
    def test_service_and_timer_text(self):
        rendered = units.render(CONFIG, sb_path=SB, sandbox_home=HOME)
        service = rendered[units.SERVICE]
        timer = rendered[units.TIMER]
        self.assertIn("Type=oneshot", service)
        self.assertIn("UMask=0077", service)
        self.assertIn("TimeoutStartSec=1860s", service)
        self.assertIn(f"ExecStart={SB} resources routine --routine-run --json", service)
        self.assertIn(f"Environment=SANDBOX_HOME={HOME}", service)
        self.assertIn("OnCalendar=daily", timer)
        self.assertIn("RandomizedDelaySec=5min", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn(f"Unit={units.SERVICE}", timer)

    def test_render_refuses_unsafe_paths(self):
        for path in ("relative/sb", "/tmp/a b/sb", "/tmp/x;id/sb", "/tmp/x\n/sb"):
            with self.assertRaises(RoutineError, msg=path) as raised:
                units.render(CONFIG, sb_path=path, sandbox_home=HOME)
            self.assertEqual(raised.exception.code, "routine_install_failed")


class CadenceTests(UnitsCase):
    def test_valid_cadence_returns_next_elapse(self):
        with patch.object(units, "_run", FakeSystem()):
            self.assertEqual(units.validate_cadence("daily"), "2026-10-09T00:00:00Z")

    def test_invalid_cadence_writes_nothing(self):
        system = FakeSystem(analyze=(1, ""))
        with self.assertRaises(RoutineError) as raised:
            self.install(system, {**CONFIG, "cadence": "every tuesday-ish"})
        self.assertEqual(raised.exception.code, "invalid_cadence")
        self.assertFalse(self.unit_dir.exists() and any(self.unit_dir.iterdir()))
        self.assertFalse(any(call[:2] == ["systemctl", "--user"] for call in system.calls))


class HostCheckTests(UnitsCase):
    def test_missing_systemctl_is_systemd_unavailable(self):
        self.which.side_effect = lambda name: None if name == "systemctl" else f"/usr/bin/{name}"
        with self.assertRaises(RoutineError) as raised:
            self.install(FakeSystem())
        self.assertEqual(raised.exception.code, "systemd_unavailable")
        self.assertFalse(self.unit_dir.exists())

    def test_linger_off_is_linger_disabled(self):
        with self.assertRaises(RoutineError) as raised:
            self.install(FakeSystem(linger="no"))
        self.assertEqual(raised.exception.code, "linger_disabled")
        self.assertFalse(self.unit_dir.exists())


class InstallTests(UnitsCase):
    def test_install_writes_units_then_reloads_and_enables(self):
        system = FakeSystem()
        result = self.install(system)
        self.assertEqual(result["next_run"], "2026-10-09T00:00:00Z")
        self.assertTrue((self.unit_dir / units.SERVICE).is_file())
        self.assertTrue((self.unit_dir / units.TIMER).is_file())
        lifecycle = [call for call in system.calls if call[0] == "systemctl"]
        self.assertEqual(lifecycle[0], ["systemctl", "--user", "daemon-reload"])
        self.assertEqual(lifecycle[1], ["systemctl", "--user", "enable", "--now", units.TIMER])

    def test_re_enable_leaves_exactly_one_timer(self):
        self.install(FakeSystem())
        self.install(FakeSystem(), {**CONFIG, "cadence": "weekly"})
        timers = list(self.unit_dir.glob("*.timer"))
        self.assertEqual([item.name for item in timers], [units.TIMER])
        self.assertIn("OnCalendar=weekly", timers[0].read_text())

    def test_install_failure_restores_prior_units(self):
        self.install(FakeSystem())
        before = (self.unit_dir / units.TIMER).read_text()
        with self.assertRaises(RoutineError) as raised:
            self.install(FakeSystem(fail={"enable --now"}), {**CONFIG, "cadence": "weekly"})
        self.assertEqual(raised.exception.code, "routine_install_failed")
        self.assertEqual((self.unit_dir / units.TIMER).read_text(), before)

    def test_first_install_failure_leaves_no_units(self):
        with self.assertRaises(RoutineError) as raised:
            self.install(FakeSystem(fail={"daemon-reload"}))
        self.assertEqual(raised.exception.code, "routine_install_failed")
        self.assertFalse((self.unit_dir / units.TIMER).exists())
        self.assertFalse((self.unit_dir / units.SERVICE).exists())

    def test_next_run_reads_the_installed_timer(self):
        with patch.object(units, "_run", FakeSystem()):
            self.assertEqual(units.next_run(), "2026-10-09T00:03:12Z")
        with patch.object(units, "_run", FakeSystem(fail={"show"})):
            self.assertIsNone(units.next_run())


class HostEnableTests(UnitsCase):
    REV = "f" * 24

    def setUp(self):
        super().setUp()
        from sandbox.resources.cleanup_routine import host
        from sandbox.resources.cleanup_routine.store import RoutineStore

        self.host = host
        self.store = RoutineStore(Path(self.home.name) / "runtime" / "resources" / "cleanup-routine")

    def handle(self, payload, system=None):
        with patch.object(units, "_run", system or FakeSystem()):
            return self.host.handle(
                payload, live_revision=self.REV, store=self.store,
                sb_path=SB, sandbox_home=HOME,
                host_config={"resources": {"reclaim_exclude": ["keep-*"]}},
            )

    def enable(self, **overrides):
        payload = {
            "action": "cleanup_routine_enable", "expected_runtime_revision": self.REV,
            "cadence": "daily", "timeout": "30min", "randomized_delay": "5min",
            "exclusions": ["lenzora*"],
        }
        payload.update(overrides)
        return payload

    def test_enable_installs_records_and_reports(self):
        envelope = self.handle(self.enable())
        self.assertEqual(envelope["resource_schema"], 1)
        self.assertEqual(envelope["service"], {"runtime_revision": self.REV})
        result = envelope["result"]
        self.assertTrue(result["ok"], result)
        routine = result["routine"]
        self.assertTrue(routine["enabled"])
        self.assertEqual(routine["cadence"], "daily")
        self.assertEqual(routine["timeout"], "30min")
        self.assertEqual(routine["exclusions"], ["lenzora*"])
        self.assertEqual(routine["effective_exclusions"], ["lenzora*", "keep-*"])
        self.assertEqual(routine["enabled_revision"], self.REV)
        self.assertEqual(routine["next_run"], "2026-10-09T00:00:00Z")
        self.assertEqual(result["runs"], [])
        self.assertTrue((self.unit_dir / units.TIMER).exists())
        stored = self.store.read_config()
        self.assertTrue(stored["enabled"])
        self.assertEqual(stored["unit"], units.UNIT)

    def test_revision_mismatch_refuses_before_any_write(self):
        system = FakeSystem()
        envelope = self.handle(self.enable(expected_runtime_revision="0" * 24), system)
        self.assertEqual(envelope["result"]["error"]["code"], "runtime_revision_mismatch")
        self.assertEqual(system.calls, [])
        self.assertIsNone(self.store.read_config())
        self.assertFalse(self.unit_dir.exists())

    def test_invalid_request_is_a_typed_error(self):
        envelope = self.handle(self.enable(exclusions=["a/b"]))
        self.assertEqual(envelope["result"]["error"]["code"], "invalid_exclusion")
        envelope = self.handle({"action": "cleanup_routine_enable", "x": 1})
        self.assertEqual(envelope["result"]["error"]["code"], "invalid_request")

    def test_install_failure_keeps_the_prior_config(self):
        self.handle(self.enable())
        envelope = self.handle(self.enable(cadence="weekly"),
                               FakeSystem(fail={"enable --now"}))
        self.assertEqual(envelope["result"]["error"]["code"], "routine_install_failed")
        self.assertEqual(self.store.read_config()["cadence"], "daily")

    def test_first_install_failure_leaves_no_config(self):
        envelope = self.handle(self.enable(), FakeSystem(linger="no"))
        self.assertEqual(envelope["result"]["error"]["code"], "linger_disabled")
        self.assertIsNone(self.store.read_config())

    def test_re_enable_replaces_settings(self):
        self.handle(self.enable())
        envelope = self.handle(self.enable(cadence="weekly", exclusions=[]))
        self.assertEqual(envelope["result"]["routine"]["cadence"], "weekly")
        self.assertEqual(envelope["result"]["routine"]["exclusions"], [])
        self.assertEqual(len(list(self.unit_dir.glob("*.timer"))), 1)


if __name__ == "__main__":
    unittest.main()
