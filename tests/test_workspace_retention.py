"""Agent-facing retention verbs: release, ttl, and the reaper."""

import io
import json
import contextlib
import types
import unittest
from unittest import mock

from sandbox.commands import workspaces


def args(**overrides):
    base = {
        "action": "release", "name": None, "remote": None, "local": False,
        "ttl": None, "dry_run": False, "confirm": False, "budget": None,
        "json": True, "project_dir": ".",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


class Recorder:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {
            "schema_version": 1, "ok": True, "action": "release",
            "status": "ok", "target": None, "data": {}, "error": None,
        }

    def release(self, name):
        self.calls.append(("release", name))
        return self.payload

    def set_ttl(self, name, duration):
        self.calls.append(("ttl", name, duration))
        return {**self.payload, "action": "ttl",
                "data": {"expires_at": "2030-01-01T00:00:00Z",
                         "ttl_seconds": 1209600}}

    def reap(self, *, dry_run, ttl, confirm, budget_seconds):
        self.calls.append(("reap", dry_run, ttl, confirm))
        return {**self.payload, "action": "reap",
                "data": {"dry_run": dry_run, "candidates": [], "tier": "all"}}


@contextlib.contextmanager
def run(recorder):
    stream = io.StringIO()
    with mock.patch("sandbox.resources.context.reclaim_service",
                    return_value=recorder):
        with contextlib.redirect_stdout(stream):
            yield stream


class TestRetentionCommands(unittest.TestCase):
    def test_release_routes_to_the_reclaim_service(self):
        recorder = Recorder()
        with run(recorder) as stream:
            workspaces.cmd_workspace(None, args(
                action="release", name="a-workspace-1"))
        self.assertEqual(recorder.calls, [("release", "a-workspace-1")])
        self.assertTrue(json.loads(stream.getvalue())["ok"])

    def test_release_without_a_name_is_refused(self):
        recorder = Recorder()
        with self.assertRaises(SystemExit):
            with run(recorder):
                workspaces.cmd_workspace(None, args(action="release"))
        self.assertEqual(recorder.calls, [])

    def test_ttl_passes_the_duration_through(self):
        recorder = Recorder()
        with run(recorder):
            workspaces.cmd_workspace(None, args(
                action="ttl", name="a-workspace-1", ttl="14d"))
        self.assertEqual(recorder.calls, [("ttl", "a-workspace-1", "14d")])

    def test_ttl_without_a_duration_is_refused(self):
        recorder = Recorder()
        with self.assertRaises(SystemExit):
            with run(recorder):
                workspaces.cmd_workspace(None, args(
                    action="ttl", name="a-workspace-1"))
        self.assertEqual(recorder.calls, [])

    def test_reap_defaults_to_a_dry_run(self):
        recorder = Recorder()
        with run(recorder):
            workspaces.cmd_workspace(None, args(action="reap"))
        self.assertEqual(recorder.calls, [("reap", True, None, False)])

    def test_reap_with_confirmation_is_not_a_dry_run(self):
        recorder = Recorder()
        with run(recorder):
            workspaces.cmd_workspace(None, args(action="reap", confirm=True))
        self.assertEqual(recorder.calls, [("reap", False, None, True)])

    def test_explicit_dry_run_beats_confirmation(self):
        recorder = Recorder()
        with run(recorder):
            workspaces.cmd_workspace(None, args(
                action="reap", confirm=True, dry_run=True))
        self.assertEqual(recorder.calls[0][1], True)

    def test_a_failed_operation_exits_non_zero(self):
        recorder = Recorder({
            "schema_version": 1, "ok": False, "action": "release",
            "status": "failed", "target": None, "data": {},
            "error": {"code": "workspace_identity_invalid",
                      "message": "workspace name is invalid",
                      "retryable": False},
        })
        with self.assertRaises(SystemExit):
            with run(recorder):
                workspaces.cmd_workspace(None, args(
                    action="release", name="a-workspace-1"))

    def test_human_output_names_the_expiry(self):
        recorder = Recorder()
        with run(recorder) as stream:
            workspaces.cmd_workspace(None, args(
                action="ttl", name="a-workspace-1", ttl="14d", json=False))
        self.assertIn("expires: 2030-01-01T00:00:00Z", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
