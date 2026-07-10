"""Focused unit coverage for the remote Hermes control plane (spec 016).

These tests intentionally mock SSH: they validate the command/state contract
without installing Hermes, authenticating a Git provider, or changing a VPS.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._hermes as hermes  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestValidation(unittest.TestCase):
    def test_managed_repository_names_are_not_paths(self):
        self.assertEqual(hermes.validate_repo_name("my.repo_2"), "my.repo_2")
        for value in ("../escape", "/tmp/repo", "", "two words", ".hidden"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_repo_name(value)

    def test_repository_url_rejects_userinfo_and_sanitizes(self):
        self.assertEqual(
            hermes.validate_repo_url("https://github.com/acme/example.git"),
            "https://github.com/acme/example.git",
        )
        with self.assertRaises(hermes.HermesError):
            hermes.validate_repo_url("https://user:token@github.com/acme/example.git")

    def test_release_requires_immutable_tag_and_full_commit(self):
        self.assertEqual(hermes.validate_release("v2026.7.7.2", "a" * 40),
                         ("v2026.7.7.2", "a" * 40))
        for tag, commit in (("main", "a" * 40), ("v1", "short")):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_release(tag, commit)

    def test_result_redacts_sensitive_values(self):
        data = hermes.result(False, "status", "test", error=hermes.HermesError(
            "failed with token=secret ssh://user@host", "remote_failed"))
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("user@host", json.dumps(data))


class TestProfileRendering(unittest.TestCase):
    def test_profile_has_full_sequential_sandbox_mcp_access(self):
        rendered = hermes.render_profile("/home/u/sandbox", "/home/u/sandbox/sb-src/sb")
        self.assertEqual(rendered["mcp_servers"]["sandbox"]["command"],
                         "/home/u/sandbox/sb-src/sb")
        self.assertFalse(rendered["mcp_servers"]["sandbox"]["supports_parallel_tool_calls"])
        self.assertNotIn("include", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertNotIn("exclude", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertEqual(rendered["approvals"]["mode"], "manual")
        self.assertEqual(rendered["approvals"]["cron_mode"], "deny")

    def test_gateway_allowlist_fails_closed(self):
        for value in ([], ["*"], ["all"]):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_gateway_allowlist(value)
        self.assertEqual(hermes.validate_gateway_allowlist(["123", "team"]), ["123", "team"])


class TestRemoteCommands(unittest.TestCase):
    def setUp(self):
        self.entry = {"ssh": "ubuntu@example.test", "provisioned": True}

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_uses_pinned_noninteractive_installer(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="a" * 40 + "\n"),
            _completed(),
            _completed(stdout="hermes 0.18.2\n"),
            _completed(),
        ]
        out = hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertTrue(out["ok"])
        command = ssh_run.call_args_list[2].args[1]
        self.assertIn("--branch v2026.7.7.2", command)
        self.assertIn("--commit " + "a" * 40, command)
        self.assertIn("--non-interactive", command)
        self.assertIn("--skip-setup", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_rejects_path_escape_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        with self.assertRaises(hermes.HermesError):
            hermes.clone_repo("test", "https://github.com/acme/repo.git", "../escape")
        ssh_run.assert_not_called()

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_async_run_uses_worktree_by_default(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="0123456789abcdef\n"),
        ]
        out = hermes.run("test", "repo", "inspect", async_=True)
        self.assertTrue(out["data"]["worktree"])
        self.assertEqual(out["job_id"], "0123456789abcdef")
        self.assertIn("git worktree add", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_dashboard_refuses_without_v2_gate_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.dashboard_action("test", "install")
        self.assertEqual(caught.exception.code, "v2_gate_required")
        ssh_run.assert_not_called()

    @patch("sandbox.core._hermes.subprocess.run")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_chat_opens_tty_in_a_worktree(self, get_remote, ssh_run, process):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="/home/ubuntu/sandbox/hermes-repos/repo/.worktrees/123\n"),
        ]
        process.return_value = _completed()
        out = hermes.chat("test", "repo")
        self.assertTrue(out["data"]["worktree"])
        self.assertIn("-tt", process.call_args.args[0])
        self.assertIn("git worktree add", ssh_run.call_args_list[1].args[1])


class TestLocalState(unittest.TestCase):
    def test_state_round_trip_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hermes.json"
            hermes.write_state(path, {"schema_version": 1, "repositories": {}})
            self.assertEqual(hermes.read_state(path)["repositories"], {})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
