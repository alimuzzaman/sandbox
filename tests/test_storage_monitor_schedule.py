"""Pure and disposable checks for the storage-monitor schedule contract."""

from __future__ import annotations

import os
import argparse
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from sandbox.resources.schedule import (
    ScheduleError,
    activate,
    build_schedule_plan,
    deactivate,
)


POLICY = {
    "schedule_calendar": "hourly",
    "schedule_randomized_delay": "5min",
    "schedule_timeout": "30min",
}
TARGET = {"kind": "remote", "name": "remote-a"}


class TestStorageMonitorSchedule(unittest.TestCase):
    def test_plan_is_disabled_and_install_free(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            self.assertFalse(plan["enabled"])
            self.assertEqual(
                plan["command"],
                ["sb", "resources", "monitor", "--scheduled", "--json", "--remote", "remote-a"],
            )
            self.assertEqual(set(plan["units"]), set(Path(path).name for path in plan["paths"].values()))
            service_name = next(name for name in plan["units"] if name.endswith(".service"))
            timer_name = next(name for name in plan["units"] if name.endswith(".timer"))
            self.assertIn("flock -n", plan["units"][service_name])
            self.assertIn("OnCalendar=hourly", plan["units"][timer_name])
            self.assertFalse(list(Path(home).rglob("*")))

    def test_launchd_plan_is_a_valid_plist_with_fixed_argv(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, {"kind": "local", "name": "local"}, "launchd")
            plist_name = next(name for name in plan["units"] if name.endswith(".plist"))
            payload = plistlib.loads(plan["units"][plist_name].encode("utf-8"))
            self.assertFalse(plan["enabled"])
            self.assertEqual(payload["ProgramArguments"], ["sb", "resources", "monitor", "--scheduled", "--json"])
            self.assertEqual(payload["StartCalendarInterval"], {"Minute": 0})
            self.assertIn("no native randomized-delay", plan["limitations"][0])

    def test_renderer_rejects_non_monitor_argv(self):
        with self.assertRaises(ScheduleError) as raised:
            build_schedule_plan(POLICY, TARGET, "systemd", command=["rm", "-rf", "/"])
        self.assertEqual(raised.exception.code, "invalid_schedule_command")

    def test_activation_and_deactivation_need_confirmation(self):
        plan = build_schedule_plan(POLICY, TARGET, "systemd")
        with patch("sandbox.resources.schedule.subprocess.run") as run:
            activation = activate(plan, confirm=False)
            deactivation = deactivate(plan, confirm=False)
        self.assertEqual(activation["error"]["code"], "protected_operation")
        self.assertEqual(deactivation["error"]["code"], "protected_operation")
        run.assert_not_called()

    def test_activation_is_idempotent_and_deactivation_removes_only_units(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            completed = type("Completed", (), {"returncode": 0})()
            with patch("sandbox.resources.schedule.subprocess.run", return_value=completed) as run:
                first = activate(plan, confirm=True)
                second = activate(plan, confirm=True)
                self.assertEqual(first["status"], "activated")
                self.assertEqual(second["status"], "unchanged")
                self.assertEqual(run.call_count, 1)
                for path in plan["paths"].values():
                    self.assertTrue(Path(path).is_file())
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o644)
                removed = deactivate(plan, confirm=True)
            self.assertEqual(removed["status"], "deactivated")
            self.assertTrue(all(not Path(path).exists() for path in plan["paths"].values()))

    def test_activation_rejects_a_forged_plan(self):
        plan = build_schedule_plan(POLICY, TARGET, "systemd")
        forged = {**plan, "command": ["sh", "-c", "echo unsafe"]}
        result = activate(forged, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_schedule_command")

    def test_deactivation_does_not_remove_modified_unit(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            first_name = next(iter(plan["paths"]))
            path = Path(plan["paths"][first_name])
            path.parent.mkdir(parents=True)
            path.write_text("operator-owned unit\n", encoding="utf-8")
            result = deactivate(plan, confirm=True)
            self.assertTrue(path.exists())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "schedule_content_mismatch")

    def test_cli_renders_schedule_and_refuses_unconfirmed_activation(self):
        from sandbox.commands import resources

        parser = argparse.ArgumentParser()
        resources.configure_parser(parser)
        with patch.object(resources, "resolve_policy", return_value=POLICY):
            output = io.StringIO()
            with redirect_stdout(output):
                resources.cmd_resources({}, parser.parse_args(["schedule", "--json"]))
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(payload["data"]["enabled"])

            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                resources.cmd_resources({}, parser.parse_args(["schedule", "--activate", "--json"]))
            refused = json.loads(output.getvalue())
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(refused["error"]["code"], "protected_operation")


if __name__ == "__main__":
    unittest.main()
