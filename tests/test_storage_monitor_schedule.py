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
    deactivate_installed,
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
            self.assertNotIn("flock", plan["units"][service_name])
            self.assertIn(
                "ExecStart=/usr/bin/env sb resources monitor --scheduled --json --remote remote-a",
                plan["units"][service_name],
            )
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
            self.assertFalse(plan["activation_supported"])
            self.assertFalse(plan["timeout_enforced"])

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
                self.assertEqual(run.call_count, 2)
                for path in plan["paths"].values():
                    self.assertTrue(Path(path).is_file())
                    self.assertEqual(Path(path).stat().st_mode & 0o777, 0o644)
                removed = deactivate(plan, confirm=True)
            self.assertEqual(removed["status"], "deactivated")
            self.assertTrue(all(not Path(path).exists() for path in plan["paths"].values()))

    def test_matching_files_retry_transition_after_prior_failure(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            failed = ScheduleError("transition unavailable", "schedule_transition_failed")
            with patch("sandbox.resources.schedule._run_bounded", side_effect=[failed, None]) as run:
                first = activate(plan, confirm=True)
                second = activate(plan, confirm=True)
            self.assertFalse(first["ok"])
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(run.call_count, 2)

    def test_deactivation_uses_installed_receipt_after_policy_drift(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            with patch("sandbox.resources.schedule._run_bounded") as run:
                self.assertTrue(activate(plan, confirm=True)["ok"])
                removed = deactivate_installed(TARGET, "systemd", confirm=True)
            self.assertTrue(removed["ok"])
            self.assertEqual(removed["status"], "deactivated")
            self.assertEqual(run.call_count, 2)
            self.assertTrue(all(not Path(path).exists() for path in plan["paths"].values()))

    def test_activation_write_failures_restore_prior_installation_at_every_boundary(self):
        from sandbox.resources import schedule

        changed_policy = {**POLICY, "schedule_calendar": "daily"}
        for failure_boundary in (1, 2, 3):
            with self.subTest(failure_boundary=failure_boundary), \
                    tempfile.TemporaryDirectory() as home, \
                    patch.dict(os.environ, {"HOME": home}):
                prior_plan = build_schedule_plan(POLICY, TARGET, "systemd")
                changed_plan = build_schedule_plan(changed_policy, TARGET, "systemd")
                with patch("sandbox.resources.schedule._run_bounded"):
                    self.assertTrue(activate(prior_plan, confirm=True)["ok"])

                receipt_path = next(Path(home).rglob("*.installed.json"))
                installed_paths = [*(Path(value) for value in prior_plan["paths"].values()), receipt_path]
                prior = {
                    path: (path.read_bytes(), path.stat().st_mode & 0o777)
                    for path in installed_paths
                }
                real_write = schedule._write_unit
                calls = 0

                def fail_at_boundary(path, content, mode):
                    nonlocal calls
                    calls += 1
                    real_write(path, content, mode)
                    if calls == failure_boundary:
                        raise ScheduleError(
                            "injected write failure",
                            "schedule_write_failed",
                            retryable=True,
                        )

                with patch("sandbox.resources.schedule._write_unit", side_effect=fail_at_boundary), \
                        patch("sandbox.resources.schedule._run_bounded") as transition:
                    result = activate(changed_plan, confirm=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "schedule_write_failed")
                transition.assert_not_called()
                self.assertEqual(
                    {
                        path: (path.read_bytes(), path.stat().st_mode & 0o777)
                        for path in installed_paths
                    },
                    prior,
                )

                # Deactivation remains possible from the restored receipt even
                # if the current policy or remote definition has disappeared.
                with patch("sandbox.resources.schedule._run_bounded"):
                    removed = deactivate_installed(TARGET, "systemd", confirm=True)
                self.assertTrue(removed["ok"])
                self.assertTrue(all(not path.exists() for path in installed_paths))

    def test_activation_refuses_every_symlinked_scheduler_ancestor(self):
        components = (".config", "systemd", "user")
        for symlink_index in range(len(components)):
            with self.subTest(component=components[symlink_index]), \
                    tempfile.TemporaryDirectory() as home, \
                    tempfile.TemporaryDirectory() as outside, \
                    patch.dict(os.environ, {"HOME": home}):
                plan = build_schedule_plan(POLICY, TARGET, "systemd")
                parent = Path(home)
                for component in components[:symlink_index]:
                    parent = parent / component
                    parent.mkdir(mode=0o700)
                link = parent / components[symlink_index]
                link.symlink_to(outside, target_is_directory=True)
                outside_mode = Path(outside).stat().st_mode & 0o777

                with patch("sandbox.resources.schedule._run_bounded") as transition:
                    result = activate(plan, confirm=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "unsafe_schedule_path")
                transition.assert_not_called()
                self.assertEqual(list(Path(outside).iterdir()), [])
                self.assertEqual(Path(outside).stat().st_mode & 0o777, outside_mode)

    def test_activation_refuses_symlinked_home_and_unsafe_ancestor_mode(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as outside:
            linked_home = Path(base) / "linked-home"
            linked_home.symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {"HOME": str(linked_home)}):
                plan = build_schedule_plan(POLICY, TARGET, "systemd")
                result = activate(plan, confirm=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "unsafe_schedule_path")
            self.assertEqual(list(Path(outside).iterdir()), [])

        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            config = Path(home) / ".config"
            config.mkdir(mode=0o755)
            mode_before = config.stat().st_mode & 0o777
            result = activate(plan, confirm=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "unsafe_schedule_path")
            self.assertEqual(config.stat().st_mode & 0o777, mode_before)
            self.assertEqual(list(config.iterdir()), [])

    def test_receipt_loading_refuses_symlinked_ancestor(self):
        from sandbox.resources import schedule

        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as outside, \
                patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            with patch("sandbox.resources.schedule._run_bounded"):
                self.assertTrue(activate(plan, confirm=True)["ok"])
            user_root = Path(home) / ".config" / "systemd" / "user"
            saved = Path(home) / "saved-user"
            user_root.rename(saved)
            user_root.symlink_to(outside, target_is_directory=True)
            outside_mode = Path(outside).stat().st_mode & 0o777

            with self.assertRaises(ScheduleError) as raised:
                schedule._load_receipt(TARGET, "systemd")

            self.assertEqual(raised.exception.code, "unsafe_schedule_path")
            self.assertEqual(list(Path(outside).iterdir()), [])
            self.assertEqual(Path(outside).stat().st_mode & 0o777, outside_mode)

    def test_rollback_refuses_symlinked_ancestor_without_writing_through_it(self):
        from sandbox.resources import schedule

        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as outside, \
                patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            changed = build_schedule_plan(
                {**POLICY, "schedule_calendar": "daily"}, TARGET, "systemd",
            )
            with patch("sandbox.resources.schedule._run_bounded"):
                self.assertTrue(activate(plan, confirm=True)["ok"])
            real_write = schedule._write_unit
            user_root = Path(home) / ".config" / "systemd" / "user"
            saved = Path(home) / "saved-user"
            outside_mode = Path(outside).stat().st_mode & 0o777

            def sabotage_after_write(path, content, mode):
                real_write(path, content, mode)
                user_root.rename(saved)
                user_root.symlink_to(outside, target_is_directory=True)
                raise ScheduleError("injected failure", "schedule_write_failed")

            with patch("sandbox.resources.schedule._write_unit", side_effect=sabotage_after_write), \
                    patch("sandbox.resources.schedule._run_bounded") as transition:
                result = activate(changed, confirm=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "schedule_rollback_failed")
            transition.assert_not_called()
            self.assertEqual(list(Path(outside).iterdir()), [])
            self.assertEqual(Path(outside).stat().st_mode & 0o777, outside_mode)

    def test_deactivation_refuses_symlinked_ancestor_without_removal(self):
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as outside, \
                patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            with patch("sandbox.resources.schedule._run_bounded"):
                self.assertTrue(activate(plan, confirm=True)["ok"])
            user_root = Path(home) / ".config" / "systemd" / "user"
            saved = Path(home) / "saved-user"
            user_root.rename(saved)
            user_root.symlink_to(outside, target_is_directory=True)

            with patch("sandbox.resources.schedule._run_bounded") as transition:
                result = deactivate_installed(TARGET, "systemd", confirm=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "unsafe_schedule_path")
            transition.assert_not_called()
            self.assertTrue(all((saved / Path(path).name).is_file() for path in plan["paths"].values()))
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_deactivation_without_installed_receipt_is_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            result = deactivate_installed(TARGET, "systemd", confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "schedule_evidence_unknown")

    def test_direct_unconfirmed_deactivation_precedes_all_validation(self):
        with patch("sandbox.resources.schedule._target", side_effect=AssertionError), \
                patch("sandbox.resources.schedule.normalize_platform", side_effect=AssertionError):
            result = deactivate_installed(object(), object(), confirm=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "protected_operation")

    def test_confirmed_malformed_deactivation_returns_bounded_envelope(self):
        class BadPlatform:
            def __str__(self):
                raise RuntimeError("hostile platform")

        cases = (
            (object(), "systemd", "invalid_target"),
            ({"kind": [], "name": "remote-a"}, "systemd", "invalid_target"),
            (TARGET, "plan9", "unsupported_platform"),
            (TARGET, BadPlatform(), "unsupported_platform"),
        )
        for target, platform, code in cases:
            with self.subTest(code=code):
                result = deactivate_installed(target, platform, confirm=True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], code)

    def test_launchd_activation_refuses_unenforced_timeout(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "launchd")
            with patch("sandbox.resources.schedule.subprocess.run") as run:
                result = activate(plan, confirm=True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "schedule_timeout_unenforced")
            self.assertFalse(list(Path(home).rglob("*.plist")))
            run.assert_not_called()

    def test_activation_rejects_a_forged_plan(self):
        plan = build_schedule_plan(POLICY, TARGET, "systemd")
        forged = {**plan, "command": ["sh", "-c", "echo unsafe"]}
        result = activate(forged, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_schedule_command")

    def test_activation_refuses_invalid_utf8_and_oversized_units_without_transition(self):
        mutations = (
            ("invalid_utf8", b"\xff\xfe\xfd"),
            ("oversized", b"x" * ((256 * 1024) + 1)),
        )
        for name, content in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as home, \
                    patch.dict(os.environ, {"HOME": home}):
                plan = build_schedule_plan(POLICY, TARGET, "systemd")
                path = Path(next(iter(plan["paths"].values())))
                path.parent.mkdir(parents=True, mode=0o700)
                # mkdir(parents=True) applies the requested mode only to the
                # leaf; make every scheduler ancestor match the lifecycle gate.
                for ancestor in (
                    Path(home) / ".config",
                    Path(home) / ".config" / "systemd",
                    path.parent,
                ):
                    ancestor.chmod(0o700)
                path.write_bytes(content)
                path.chmod(0o644)

                with patch("sandbox.resources.schedule._run_bounded") as transition:
                    result = activate(plan, confirm=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "schedule_content_mismatch")
                transition.assert_not_called()

    def test_deactivation_refuses_invalid_utf8_and_oversized_units_without_transition(self):
        mutations = (
            ("invalid_utf8", b"\xff\xfe\xfd"),
            ("oversized", b"x" * ((256 * 1024) + 1)),
        )
        for name, content in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as home, \
                    patch.dict(os.environ, {"HOME": home}):
                plan = build_schedule_plan(POLICY, TARGET, "systemd")
                with patch("sandbox.resources.schedule._run_bounded"):
                    self.assertTrue(activate(plan, confirm=True)["ok"])
                path = Path(next(iter(plan["paths"].values())))
                path.write_bytes(content)
                path.chmod(0o644)

                with patch("sandbox.resources.schedule._run_bounded") as transition:
                    result = deactivate_installed(TARGET, "systemd", confirm=True)

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "schedule_content_mismatch")
                transition.assert_not_called()

    def test_deactivation_does_not_remove_modified_unit(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            with patch("sandbox.resources.schedule._run_bounded"):
                self.assertTrue(activate(plan, confirm=True)["ok"])
            first_name = next(iter(plan["paths"]))
            path = Path(plan["paths"][first_name])
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

    def test_cli_confirmation_gate_precedes_policy_and_remote_resolution(self):
        from sandbox.commands import resources

        parser = argparse.ArgumentParser()
        resources.configure_parser(parser)
        for operation in ("--activate", "--deactivate"):
            output = io.StringIO()
            with patch.object(resources, "resolve_policy", side_effect=AssertionError), \
                    redirect_stdout(output), self.assertRaises(SystemExit):
                resources.cmd_resources(
                    {}, parser.parse_args(["schedule", operation, "--remote", "removed", "--json"]),
                )
            self.assertEqual(
                json.loads(output.getvalue())["error"]["code"],
                "protected_operation",
            )

    def test_cli_confirmed_deactivation_does_not_resolve_removed_remote(self):
        from sandbox.commands import resources

        parser = argparse.ArgumentParser()
        resources.configure_parser(parser)
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            plan = build_schedule_plan(POLICY, TARGET, "systemd")
            with patch("sandbox.resources.schedule._run_bounded"):
                self.assertTrue(activate(plan, confirm=True)["ok"])
                output = io.StringIO()
                with patch.object(resources, "resolve_policy", side_effect=AssertionError), \
                        patch("sandbox.resources.schedule.normalize_platform", return_value="systemd"), \
                        redirect_stdout(output):
                    resources.cmd_resources(
                        {}, parser.parse_args([
                            "schedule", "--deactivate", "--confirm", "--remote", "remote-a", "--json",
                        ]),
                    )
            self.assertEqual(json.loads(output.getvalue())["status"], "deactivated")


if __name__ == "__main__":
    unittest.main()
