"""`sb resources routine` client behaviour (spec 057)."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import resources as command
from sandbox.resources.remote import RemoteResourceAdapter

REV = "a" * 24
ENTRY = {
    "name": "r1", "provisioned": True, "ssh_target": "root@203.0.113.9",
    "control_url": "https://control.example.test", "bearer_token": "secret-token",
}
POLICY = {"schedule_timeout": "45min", "schedule_randomized_delay": "2min",
          "schedule_calendar": "hourly"}


def parse(*argv):
    parser = argparse.ArgumentParser()
    command.configure_parser(parser)
    return parser.parse_args(["routine", *argv])


def ok_result(enabled=True, runs=()):
    return {
        "ok": True,
        "routine": {
            "enabled": enabled, "cadence": "daily", "timeout": "45min",
            "randomized_delay": "2min", "exclusions": ["lenzora*"],
            "effective_exclusions": ["lenzora*", "keep-*"],
            "enabled_revision": REV, "next_run": "2026-10-09T00:03:00Z" if enabled else None,
            "enabled_at": "2026-10-08T00:00:00Z", "disabled_at": None,
        },
        "last_run_revision": REV if runs else None,
        "runs": list(runs),
    }


class Transport:
    def __init__(self, result=None, entry=ENTRY):
        self.requests = []
        self.result = result or ok_result()
        self.entry = entry

    def lookup(self, name):
        return self.entry if self.entry and name == self.entry["name"] else None

    def __call__(self, entry, route, *, input_data, timeout):
        self.requests.append((route, json.loads(input_data)))
        envelope = {"resource_schema": 1, "transport": "control",
                    "service": {"runtime_revision": REV}, "result": self.result}
        return SimpleNamespace(args=("control-http",), returncode=0,
                               stdout=json.dumps(envelope), stderr="")

    def adapter(self, remote):
        return RemoteResourceAdapter(remote, remote_lookup=self.lookup,
                                     service_request=self)


class CliCase(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        patches = (
            patch.object(command, "_routine_adapter", side_effect=self.transport.adapter),
            patch.object(command, "resolve_policy", return_value=dict(POLICY)),
            patch.object(command, "_routine_revision", return_value=REV),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def run_cli(self, *argv):
        return command._run_routine(parse(*argv))


class EnableTests(CliCase):
    def test_enable_without_confirm_is_protected_and_sends_nothing(self):
        payload = self.run_cli("--remote", "r1", "--enable")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "protected_operation")
        self.assertEqual(self.transport.requests, [])

    def test_unknown_remote_is_refused_before_any_request(self):
        payload = self.run_cli("--remote", "nope", "--enable", "--confirm")
        self.assertEqual(payload["error"]["code"], "remote_unavailable")
        self.assertEqual(self.transport.requests, [])

    def test_unprovisioned_remote_is_refused_before_any_request(self):
        self.transport.entry = {**ENTRY, "provisioned": False}
        payload = self.run_cli("--remote", "r1", "--status")
        self.assertEqual(payload["error"]["code"], "remote_unavailable")
        self.assertEqual(self.transport.requests, [])

    def test_enable_sends_revision_and_resolved_policy(self):
        payload = self.run_cli("--remote", "r1", "--enable", "--confirm")
        self.assertTrue(payload["ok"], payload)
        route, request = self.transport.requests[0]
        self.assertEqual(route, "POST /resources")
        self.assertEqual(request, {
            "action": "cleanup_routine_enable",
            "expected_runtime_revision": REV,
            "cadence": "daily", "timeout": "45min", "randomized_delay": "2min",
            "exclusions": [],
        })
        command.resolve_policy.assert_called_with("r1")

    def test_cadence_timeout_and_repeated_exclude_are_forwarded(self):
        self.run_cli("--remote", "r1", "--enable", "--confirm",
                     "--cadence", "*-*-* 03:00:00", "--timeout", "20min",
                     "--exclude", "lenzora*", "--exclude", "keep-*")
        request = self.transport.requests[0][1]
        self.assertEqual(request["cadence"], "*-*-* 03:00:00")
        self.assertEqual(request["timeout"], "20min")
        self.assertEqual(request["randomized_delay"], "2min")
        self.assertEqual(request["exclusions"], ["lenzora*", "keep-*"])

    def test_invalid_exclusion_is_refused_locally(self):
        payload = self.run_cli("--remote", "r1", "--enable", "--confirm",
                               "--exclude", "a/b")
        self.assertEqual(payload["error"]["code"], "invalid_exclusion")
        self.assertEqual(self.transport.requests, [])

    def test_host_refusal_is_reported(self):
        self.transport.result = {"ok": False, "error": {
            "code": "runtime_revision_mismatch", "message": "differs"}}
        payload = self.run_cli("--remote", "r1", "--enable", "--confirm")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "runtime_revision_mismatch")

    def test_json_output_has_no_ssh_target_or_token(self):
        args = parse("--remote", "r1", "--enable", "--confirm", "--json")
        out = io.StringIO()
        with redirect_stdout(out):
            command.cmd_resources(None, args)
        rendered = out.getvalue()
        payload = json.loads(rendered)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["target"], {"kind": "remote", "name": "r1"})
        for forbidden in ("203.0.113.9", "ssh_target", "secret-token", "bearer"):
            self.assertNotIn(forbidden, rendered)


class ModeTests(CliCase):
    def test_exactly_one_mode(self):
        payload = self.run_cli("--remote", "r1", "--status", "--enable", "--confirm")
        self.assertEqual(payload["error"]["code"], "invalid_mode")
        payload = self.run_cli("--remote", "r1")
        self.assertEqual(payload["error"]["code"], "invalid_mode")

    def test_remote_is_required_except_for_the_run(self):
        payload = self.run_cli("--status")
        self.assertEqual(payload["error"]["code"], "remote_required")

    def test_routine_run_refuses_a_remote(self):
        payload = self.run_cli("--routine-run", "--remote", "r1")
        self.assertEqual(payload["error"]["code"], "invalid_mode")

    def test_routine_run_dispatches_to_the_host_run(self):
        with patch("sandbox.resources.cleanup_routine.run.run_routine",
                   return_value={"ok": True, "run": {"outcome": "nothing_to_do"}}) as run:
            payload = self.run_cli("--routine-run", "--json")
        run.assert_called_once_with()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["run"]["outcome"], "nothing_to_do")
        self.assertIsNone(payload["target"])

    def test_routine_flags_are_refused_on_other_actions(self):
        parser = argparse.ArgumentParser()
        command.configure_parser(parser)
        args = parser.parse_args(["status", "--cadence", "daily", "--json"])
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            command.cmd_resources(None, args)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "invalid_mode")


class StatusTests(CliCase):
    RUN = {
        "schema": 1, "run_id": "b" * 32, "started_at": "2026-10-08T00:03:00Z",
        "ended_at": "2026-10-08T00:04:00Z", "outcome": "reclaimed", "reason": None,
        "bytes_reclaimed": 3 * 1024 * 1024, "removed": 4, "skipped": 2,
        "skipped_reasons": {"excluded_by_request": 2}, "runtime_revision": REV,
        "manifest": "deletions/" + "b" * 32 + ".jsonl",
    }

    def test_status_needs_no_confirm_and_requests_full_history(self):
        self.transport.result = ok_result(runs=[self.RUN])
        payload = self.run_cli("--remote", "r1", "--status")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["status"], "enabled")
        self.assertEqual(self.transport.requests,
                         [("POST /resources", {"action": "cleanup_routine_status",
                                               "history": 30})])
        self.assertEqual(payload["data"]["runs"][0]["run_id"], "b" * 32)
        self.assertEqual(payload["data"]["last_run_revision"], REV)
        self.assertEqual(payload["data"]["routine"]["effective_exclusions"],
                         ["lenzora*", "keep-*"])

    def test_status_refuses_enable_only_flags(self):
        payload = self.run_cli("--remote", "r1", "--status", "--cadence", "daily")
        self.assertEqual(payload["error"]["code"], "invalid_mode")
        self.assertEqual(self.transport.requests, [])

    def test_human_status_shows_config_and_runs(self):
        self.transport.result = ok_result(runs=[self.RUN])
        out = io.StringIO()
        with redirect_stdout(out):
            command.cmd_resources(None, parse("--remote", "r1", "--status"))
        text = out.getvalue()
        for expected in ("enabled", "cadence daily", "timeout 45min",
                         "lenzora*, keep-*", "2026-10-09T00:03:00Z",
                         "b" * 32, "reclaimed", "3.0 MiB",
                         "excluded_by_request=2", "deletions/"):
            self.assertIn(expected, text)
        self.assertNotIn("203.0.113.9", text)

    def test_human_status_without_runs_says_so(self):
        self.transport.result = ok_result(enabled=False)
        out = io.StringIO()
        with redirect_stdout(out):
            command.cmd_resources(None, parse("--remote", "r1", "--status"))
        self.assertIn("disabled", out.getvalue())
        self.assertIn("no runs recorded", out.getvalue())


class DisableTests(CliCase):
    def test_disable_without_confirm_is_protected_and_sends_nothing(self):
        payload = self.run_cli("--remote", "r1", "--disable")
        self.assertEqual(payload["error"]["code"], "protected_operation")
        self.assertEqual(self.transport.requests, [])

    def test_disable_sends_only_the_action(self):
        self.transport.result = ok_result(enabled=False)
        payload = self.run_cli("--remote", "r1", "--disable", "--confirm")
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(self.transport.requests,
                         [("POST /resources", {"action": "cleanup_routine_disable"})])

    def test_disable_refuses_enable_only_flags(self):
        payload = self.run_cli("--remote", "r1", "--disable", "--confirm",
                               "--exclude", "x")
        self.assertEqual(payload["error"]["code"], "invalid_mode")
        self.assertEqual(self.transport.requests, [])

    def test_remove_failure_is_reported(self):
        self.transport.result = {"ok": False, "error": {
            "code": "routine_remove_failed", "message": "systemctl failed"}}
        payload = self.run_cli("--remote", "r1", "--disable", "--confirm")
        self.assertEqual(payload["error"]["code"], "routine_remove_failed")


if __name__ == "__main__":
    unittest.main()
