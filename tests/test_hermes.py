"""Focused unit coverage for the remote Hermes control plane (spec 016).

These tests intentionally mock SSH: they validate the command/state contract
without installing Hermes, authenticating a Git provider, or changing a VPS.
"""
from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._hermes as hermes  # noqa: E402
from sandbox.commands.hermes import _job_payload, _repo_action  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestValidation(unittest.TestCase):
    def test_public_dashboard_is_exact_and_caddy_stays_loopback(self):
        fragment = hermes._public_caddy_fragment("hermes.asb.bd", False)
        self.assertIn("http://:9120", fragment)
        self.assertIn("bind 127.0.0.1", fragment)
        self.assertIn("reverse_proxy 127.0.0.1:9119", fragment)
        self.assertIn("header_up Host {upstream_hostport}", fragment)
        self.assertIn("header_up Origin http://127.0.0.1:9119", fragment)
        self.assertIn("handle {\n        respond 404", fragment)
        self.assertNotIn("0.0.0.0", fragment)
        with self.assertRaises(hermes.HermesError):
            hermes._public_plan({}, {}, {}, "other.asb.bd")

    def test_public_plan_reports_missing_configuration_without_network_access(self):
        with patch("sandbox.core._hermes._public_config", return_value={}):
            out = hermes._public_validate_cloudflare({}, "hermes.asb.bd")
        self.assertFalse(out["configured"])
        self.assertIn("account_id", out["missing"])
        self.assertIn("dns_record_id", out["missing"])

    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._public_config")
    def test_public_plan_is_attach_only_and_sanitized(self, config, status, listeners):
        config.return_value = {}
        status.return_value = {"active": True}
        listeners.return_value = {"expected_loopback": True, "public_listener": False}
        plan = hermes._public_plan({}, {}, {"public_exposure": {}}, "hermes.asb.bd")
        self.assertTrue(plan["attach_only"])
        self.assertTrue(plan["ready"] is False)
        self.assertNotIn("eyj", json.dumps(plan).lower())


class TestPublicExposureLifecycle(unittest.TestCase):
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._public_install_connector")
    @patch("sandbox.core._hermes._public_caddy_apply")
    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._public_require_ready", return_value="connector-secret")
    @patch("sandbox.core._hermes._public_config", return_value={})
    @patch("sandbox.core._hermes._public_plan")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate", return_value={"commit": "a" * 40})
    @patch("sandbox.core._hermes._paths", return_value={"state": "/tmp/hermes.json"})
    @patch("sandbox.core._hermes._require_remote", return_value={"ssh": "u@example.test"})
    def test_confirmed_exposure_uses_local_proxy_and_redacts_connector(
            self, require_remote, paths, gate, read_state, public_plan, config, require_ready,
            ssh, caddy, connector, write_state):
        state = {"dashboard": {}, "public_exposure": {"basic_auth": {"enabled": False}}}
        read_state.return_value = state
        public_plan.return_value = {"ready": True, "fqdn": "hermes.asb.bd"}
        ssh.side_effect = [_completed(stdout=""), _completed()]
        out = hermes.dashboard_action("test", "expose", fqdn="hermes.asb.bd", confirm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "public")
        self.assertNotIn("connector-secret", json.dumps(out))
        caddy.assert_called_once()
        connector.assert_called_once_with(require_remote.return_value, "connector-secret")
        write_state.assert_called_once()

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._public_caddy_remove")
    @patch("sandbox.core._hermes._public_stop_connector")
    @patch("sandbox.core._hermes._remote_state_read", return_value={"dashboard": {}, "public_exposure": {"fqdn": "hermes.asb.bd", "mode": "public"}})
    @patch("sandbox.core._hermes._dashboard_gate", return_value={"commit": "a" * 40})
    @patch("sandbox.core._hermes._paths", return_value={"state": "/tmp/hermes.json"})
    @patch("sandbox.core._hermes._require_remote", return_value={"ssh": "u@example.test"})
    def test_unexpose_only_removes_local_resources(self, require_remote, paths, gate, read_state,
                                                    stop_connector, caddy_remove, write_state):
        out = hermes.dashboard_action("test", "unexpose", confirm=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ssh-only")
        stop_connector.assert_called_once()
        caddy_remove.assert_called_once()
        write_state.assert_called_once()
    def test_managed_repository_names_are_not_paths(self):
        self.assertEqual(hermes.validate_repo_name("my.repo_2"), "my.repo_2")
        for value in ("../escape", "/tmp/repo", "", "two words", ".hidden"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_repo_name(value)

    def test_backup_source_policy_rejects_nested_runtime_credentials(self):
        forbidden = (
            "config/auth.json", "secrets/.env.local", "app/credentials.json",
            "nested/cookies.txt", "tls/private.key", "tls/cert.pem",
        )
        for path in forbidden:
            self.assertTrue(hermes._backup_forbidden_source_path(path), path)
        for path in (".env.example", "hermes_cli/dashboard_auth/cookies.py", "docs/credentials.md"):
            self.assertFalse(hermes._backup_forbidden_source_path(path), path)

    def test_repository_url_rejects_userinfo_and_sanitizes(self):
        self.assertEqual(
            hermes.validate_repo_url("https://github.com/acme/example.git"),
            "https://github.com/acme/example.git",
        )
        with self.assertRaises(hermes.HermesError):
            hermes.validate_repo_url("https://user:token@github.com/acme/example.git")

    def test_state_repository_is_credential_free_github_url(self):
        self.assertEqual(
            hermes.validate_state_repo("https://github.com/alimuzzaman/hermes-agent-state.git"),
            "https://github.com/alimuzzaman/hermes-agent-state.git",
        )
        for value in (
            "git@github.com:alimuzzaman/hermes-agent-state.git",
            "https://user:token@github.com/alimuzzaman/hermes-agent-state.git",
            "https://gitlab.com/alimuzzaman/hermes-agent-state.git",
        ):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_state_repo(value)

    def test_drive_destination_is_bounded_rclone_path(self):
        self.assertEqual(hermes.validate_drive_destination("gdrive:hermes-backups"), "gdrive:hermes-backups")
        self.assertEqual(hermes.validate_drive_destination("gdrive:"), "gdrive:")
        for value in ("https://drive.google.com/x", "gdrive:../escape", "gdrive:folder;rm", "gdrive:folder space"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_drive_destination(value)

    def test_release_requires_immutable_tag_and_full_commit(self):
        self.assertEqual(hermes.validate_release("v2026.7.7.2", "a" * 40),
                         ("v2026.7.7.2", "a" * 40))
        for tag, commit in (("main", "a" * 40), ("v1", "short")):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_release(tag, commit)

    def test_default_supported_release_has_an_audited_full_commit(self):
        self.assertEqual(len(hermes.SUPPORTED_COMMIT), 40)
        self.assertEqual(hermes._expected_commit(hermes.SUPPORTED_TAG, None), hermes.SUPPORTED_COMMIT)
        self.assertEqual(hermes._expected_commit("v999.0.0", None), None)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_update_rejects_moving_branch_before_release_resolution(self, get_remote, ssh_run):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.update_plan("test", "main")
        self.assertEqual(caught.exception.code, "invalid_release")
        ssh_run.assert_not_called()

    def test_result_redacts_sensitive_values(self):
        data = hermes.result(False, "status", "test", error=hermes.HermesError(
            "failed with token=secret Authorization: Bearer secret-bearer ssh://user@host", "remote_failed"))
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("secret-bearer", json.dumps(data))
        self.assertNotIn("user@host", json.dumps(data))

    def test_result_recursively_redacts_bare_provider_and_cookie_values(self):
        github = "github_pat_" + "a" * 30
        openai = "sk-proj-" + "b" * 30
        slack = "xoxb-" + "c" * 30
        payload = hermes.result(True, "logs", "test", data={
            "output": f"{github} {openai} {slack} cookie=session-value",
            "nested": ["ya29." + "d" * 30],
        })
        rendered = json.dumps(payload)
        for secret in (github, openai, slack, "session-value", "ya29." + "d" * 30):
            self.assertNotIn(secret, rendered)
        self.assertIn("[redacted]", rendered)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_remote_timeout_becomes_a_retryable_sanitized_error(self, ssh_run):
        ssh_run.side_effect = subprocess.TimeoutExpired(["ssh"], 30)
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._ssh({"ssh": "ubuntu@example.test"}, "true", timeout=30)
        self.assertEqual(caught.exception.code, "remote_unavailable")
        self.assertTrue(caught.exception.retryable)

    @patch("sandbox.core._hermes.remote.resolve_sandbox_home")
    def test_sandbox_home_timeout_becomes_a_retryable_sanitized_error(self, resolve_home):
        resolve_home.side_effect = subprocess.TimeoutExpired(["ssh"], 15)
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._sandbox_home({"ssh": "ubuntu@example.test"})
        self.assertEqual(caught.exception.code, "sandbox_home_unavailable")
        self.assertTrue(caught.exception.retryable)

    def test_job_payload_keeps_the_public_result_envelope(self):
        payload = _job_payload("test", "status", {
            "job_id": "0123456789abcdef", "status": "running", "stdout": "safe",
        })
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action"], "job_status")
        self.assertEqual(payload["job_id"], "0123456789abcdef")
        missing = _job_payload("test", "status", {
            "job_id": "0123456789abcdef", "status": "not_found",
        })
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "job_not_found")

    @patch("sandbox.commands.hermes.sys.stdin")
    @patch("sandbox.commands.hermes.subprocess.run")
    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_reports_missing_github_cli(self, get_remote, run, stdin):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        stdin.isatty.return_value = False
        stdin.buffer = io.BytesIO(b"github_pat_repository_scoped_token")
        run.return_value = _completed(returncode=127)
        with self.assertRaises(hermes.HermesError) as caught:
            _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=True))
        self.assertEqual(caught.exception.code, "github_cli_missing")
        self.assertIn("command -v gh", run.call_args.args[0][-1])

    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_rejects_broad_browser_oauth_before_remote_lookup(self, get_remote):
        with self.assertRaises(hermes.HermesError) as caught:
            _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=False))
        self.assertEqual(caught.exception.code, "fine_grained_token_required")
        get_remote.assert_not_called()

    @patch("sandbox.commands.hermes.sys.stdin")
    @patch("sandbox.commands.hermes.subprocess.run")
    @patch("sandbox.commands.hermes.remote.get_remote")
    def test_provider_auth_accepts_fine_grained_token_only_on_stdin(self, get_remote, run, stdin):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        stdin.isatty.return_value = False
        stdin.buffer = io.BytesIO(b"github_pat_repository_scoped_token")
        run.side_effect = [_completed(), _completed(), _completed()]
        out = _repo_action(SimpleNamespace(subaction="auth", target="github", remote="test", token_stdin=True))
        self.assertFalse(out["data"]["existing"])
        self.assertEqual(run.call_count, 3)
        login = run.call_args_list[1]
        self.assertIn("gh auth login --hostname github.com --git-protocol https --with-token", login.args[0][-1])
        self.assertNotIn("github_pat_repository_scoped_token", " ".join(login.args[0]))
        self.assertEqual(login.kwargs["input"], b"github_pat_repository_scoped_token")


class TestProfileRendering(unittest.TestCase):
    def test_routing_profile_declares_coordinator_and_specialist_workers(self):
        routing = hermes.render_routing_profile()
        self.assertEqual(routing["delegation"], {
            "provider": "openai-codex",
            "model": "gpt-5.6-terra",
            "max_concurrent_children": 1,
            "max_spawn_depth": 1,
            "orchestrator_enabled": False,
        })
        self.assertEqual(routing["kanban"]["default_assignee"], "terra")
        self.assertEqual(routing["auxiliary"]["kanban_decomposer"]["model"], "gpt-5.3-codex-spark")
        self.assertEqual(routing["auxiliary"]["triage_specifier"]["model"], "gpt-5.6-sol")
        self.assertEqual(
            {worker["name"]: worker["model"] for worker in routing["workers"]},
            {"luna": "gpt-5.6-luna", "terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol"},
        )
        luna = next(worker for worker in routing["workers"] if worker["name"] == "luna")
        self.assertEqual(luna["toolsets"], ["safe", "file"])
        self.assertIn("Never call write, patch, or rename", luna["soul"])
        self.assertIn("SANDBOX_ROUTING_BEGIN", routing["coordinator_soul"])

    def test_routing_setup_expands_the_remote_hermes_launcher(self):
        command = hermes._routing_setup_command({"launcher": "$HOME/.local/bin/hermes"})
        self.assertIn("$HOME/.local/bin/hermes config set delegation.provider", command)
        self.assertNotIn("'$HOME/.local/bin/hermes'", command)
        self.assertLess(command.index("kanban init"), command.index('root_soul = root / "SOUL.md"'))
        self.assertIn('existing.rstrip() + "\\n\\n" + block + "\\n"', command)
        self.assertIn('worker["soul"] + "\\n"', command)
        embedded_python = command.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        compile(embedded_python, "routing_setup.py", "exec")

    def test_profile_has_full_sequential_sandbox_mcp_access(self):
        rendered = hermes.render_profile("/home/u/sandbox", "/home/u/sandbox/sb-src/sb")
        self.assertEqual(rendered["mcp_servers"]["sandbox"]["command"],
                         "/home/u/sandbox/sb-src/sb")
        self.assertFalse(rendered["mcp_servers"]["sandbox"]["supports_parallel_tool_calls"])
        self.assertNotIn("include", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertNotIn("exclude", rendered["mcp_servers"]["sandbox"]["tools"])
        self.assertEqual(rendered["approvals"]["mode"], "manual")
        self.assertEqual(rendered["approvals"]["cron_mode"], "deny")

    def test_profile_snapshot_has_expected_non_secret_owned_settings(self):
        rendered = hermes.render_profile("/home/u/sandbox", "/home/u/sandbox/sb-src/sb")
        self.assertEqual(rendered, {
            "model": {"default": "gpt-5.3-codex-spark", "provider": "openai-codex"},
            "terminal": {"backend": "local", "home_mode": "real", "cwd": "/home/u/sandbox/hermes-repos"},
            "approvals": {"mode": "manual", "cron_mode": "deny", "mcp_reload_confirm": True,
                          "destructive_slash_confirm": True},
            "checkpoints": {"enabled": True},
            "mcp_servers": {"sandbox": {
                "command": "/home/u/sandbox/sb-src/sb", "args": ["mcp"],
                "env": {"SANDBOX_HOME": "/home/u/sandbox"}, "enabled": True,
                "connect_timeout": 60, "timeout": 1200,
                "supports_parallel_tool_calls": False, "tools": {"resources": True, "prompts": True},
            }},
        })
        self.assertNotRegex(json.dumps(rendered).lower(), r"token|password|secret")

    def test_gateway_allowlist_fails_closed(self):
        for value in ([], ["*"], ["all"]):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_gateway_allowlist(value)
        self.assertEqual(hermes.validate_gateway_allowlist(["123", "team"]), ["123", "team"])

    def test_gateway_user_unit_uses_systemd_home_specifier(self):
        body = hermes._gateway_unit({"repo_root": "/home/ubuntu/sandbox/hermes-repos"})
        self.assertIn("Environment=HERMES_HOME=%h/.hermes", body)
        self.assertIn("ExecStart=%h/.local/bin/hermes gateway run", body)
        self.assertNotIn("$HOME", body)

    def test_gateway_install_command_restores_prior_unit_on_failure(self):
        command = hermes._gateway_install_command("hermes-gateway-sandbox.service", "[Service]\nExecStart=/bin/true\n")
        self.assertIn("rollback()", command)
        self.assertIn("loginctl enable-linger", command)
        self.assertIn("mv \"$backup\" \"$target\"", command)

    def test_dashboard_validators_and_loopback_unit(self):
        self.assertEqual(hermes.validate_dashboard_port(None), 9119)
        self.assertEqual(hermes.validate_dashboard_fqdn("Hermes.Example.com."), "hermes.example.com")
        for port in (80, 65536, "not-a-port"):
            with self.assertRaises(hermes.HermesError):
                hermes.validate_dashboard_port(port)
        with self.assertRaises(hermes.HermesError):
            hermes.validate_dashboard_fqdn("https://hermes.example.com")
        unit = hermes._dashboard_unit(9120)
        self.assertIn("--host 127.0.0.1 --port 9120 --no-open --tui", unit)
        self.assertNotIn("--insecure", unit)
        self.assertIn("NoNewPrivileges=true", unit)

    def test_dashboard_install_command_has_rollback(self):
        command = hermes._dashboard_install_command(hermes.DASHBOARD_UNIT, hermes._dashboard_unit(9119))
        self.assertIn("rollback()", command)
        self.assertIn("systemctl --user enable", command)
        self.assertIn("loginctl enable-linger", command)

    @patch("sandbox.core._hermes._ssh")
    def test_dashboard_listener_probe_distinguishes_loopback_and_public(self, ssh):
        ssh.return_value = _completed(stdout=(
            "LISTEN 0 2048 127.0.0.1:9119 0.0.0.0:*\n"
            "LISTEN 0 2048 0.0.0.0:9222 0.0.0.0:*\n"
        ))
        observed = hermes._dashboard_listeners({"ssh": "ubuntu@example.test"}, 9119)
        self.assertTrue(observed["expected_loopback"])
        self.assertFalse(observed["public_listener"])
        ssh.return_value = _completed(stdout="LISTEN 0 2048 0.0.0.0:9119 0.0.0.0:*\n")
        observed = hermes._dashboard_listeners({"ssh": "ubuntu@example.test"}, 9119)
        self.assertFalse(observed["expected_loopback"])
        self.assertTrue(observed["public_listener"])

    def test_dashboard_lifecycle_waits_for_loopback_and_stops_on_failure(self):
        command = hermes._dashboard_lifecycle_command("start", 9119)
        self.assertIn("seq 1 30", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("systemctl --user stop", command)
        self.assertNotIn("public_listener=1 }}", command)

    def test_drive_backup_command_is_full_and_passphrase_stdin_only(self):
        command = hermes._drive_backup_command(
            {"sandbox_home": "/home/u/sandbox", "sb": "/home/u/sandbox/sb-src/sb", "state": "/home/u/sandbox/runtime/hermes.json"},
            "gdrive:hermes-backups", "20260711T000000Z-deadbeef",
        )
        self.assertIn("$HOME/.hermes", command)
        self.assertIn("$HOME/.config/gh", command)
        self.assertIn("$HOME/.config/rclone", command)
        self.assertIn("--exclude=\"$HOME/.hermes/hermes-agent\"", command)
        self.assertIn("gpg --batch", command)
        self.assertIn("rclone copyto", command)
        self.assertIn("entries=", command)
        self.assertIn("instance", command)
        self.assertIn("docker cp", command)
        self.assertIn("drive-volume-fallbacks", command)
        self.assertIn("snapshotting WordPress instances", command)
        self.assertIn("uploading encrypted archive", command)
        self.assertNotIn("passphrase=", command)

    def test_drive_restore_reinstates_github_auth_and_services(self):
        command = hermes._drive_restore_command(
            {"sandbox_home": "/home/u/sandbox", "sb": "/home/u/sandbox/sb-src/sb", "state": "/home/u/sandbox/runtime/hermes.json"},
            "gdrive:hermes-full-recovery", "20260711T000000Z-deadbeef",
        )
        self.assertIn(".config/gh", command)
        self.assertIn(".config/rclone", command)
        self.assertIn("gpg --batch", command)
        self.assertIn("drive-volume-fallbacks", command)
        self.assertIn("docker run --rm", command)
        self.assertIn("systemctl --user start hermes-gateway-sandbox.service", command)

    @patch("sandbox.core._hermes._ssh")
    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._dashboard_port_preflight")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_start_rolls_back_when_service_is_not_healthy(
            self, require_remote, paths, gate, read_state, status, preflight, checked, listeners, ssh):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        gate.return_value = {"commit": hermes.SUPPORTED_COMMIT}
        read_state.return_value = {"dashboard": {"installed": True}}
        status.side_effect = [
            {"active": False, "port": 9119},
            {"active": False, "port": 9119},
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.dashboard_action("test", "start")
        self.assertEqual(caught.exception.code, "dashboard_start_failed")
        preflight.assert_called_once()
        self.assertIn("seq 1 30", checked.call_args.args[1])
        self.assertIn("systemctl --user stop", ssh.call_args.args[1])

    @patch("sandbox.core._hermes._dashboard_listeners")
    @patch("sandbox.core._hermes._dashboard_status")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._dashboard_gate")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_doctor_rejects_public_listener(
            self, require_remote, paths, gate, read_state, status, listeners):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        gate.return_value = {"commit": hermes.SUPPORTED_COMMIT}
        read_state.return_value = {"dashboard": {"installed": True, "auth_mode": "upstream"}}
        status.return_value = {"active": True, "port": 9119}
        listeners.return_value = {"expected_loopback": True, "public_listener": True, "listeners": ["0.0.0.0:9119"]}
        out = hermes.dashboard_action("test", "doctor")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "dashboard_health_failed")

    @patch("sandbox.core._hermes._checked")
    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes._paths")
    @patch("sandbox.core._hermes._require_remote")
    def test_dashboard_install_expands_remote_home(self, require_remote, paths, read_state, write_state, checked):
        require_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        paths.return_value = {"state": "/tmp/hermes.json"}
        read_state.return_value = {
            "schema_version": 1,
            "installation": {"commit": hermes.SUPPORTED_COMMIT},
            "gates": {"v2_operations": {"status": "passed", "commit": hermes.SUPPORTED_COMMIT,
                "integration_schema": hermes.STATE_SCHEMA,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS}}},
        }
        hermes.dashboard_action("test", "install")
        command = checked.call_args.args[1]
        self.assertIn('cd "$HOME/.hermes/hermes-agent"', command)
        self.assertIn("python3 -m venv .venv", command)
        self.assertIn(".venv/bin/pip install", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_dashboard_status_uses_current_v2_gate(self, get_remote, ssh_run):
        get_remote.return_value = {"ssh": "ubuntu@example.test", "provisioned": True}
        state = {"schema_version": 1, "installation": {"commit": hermes.SUPPORTED_COMMIT},
                 "gates": {"v2_operations": {"status": "passed", "commit": hermes.SUPPORTED_COMMIT,
                    "integration_schema": hermes.STATE_SCHEMA,
                    "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS}}},
                 "dashboard": {"installed": True, "auth_mode": "upstream"}}
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout=json.dumps(state)),
                               _completed(stdout=json.dumps(state)),
                               _completed(stdout="active=active\nenabled=enabled\npid=123\nport=9119\n")]
        out = hermes.dashboard_action("test", "status")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["host"], "127.0.0.1")
        self.assertIn("<configured-test-ssh-target>", out["data"]["ssh_forward"])

    def test_dashboard_exposure_plan_requires_feature_015(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.validate_dashboard_fqdn("bad host")
        self.assertEqual(caught.exception.code, "invalid_dashboard_fqdn")


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
            _completed(),
        ]
        out = hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertTrue(out["ok"])
        command = ssh_run.call_args_list[2].args[1]
        self.assertIn("--branch v2026.7.7.2", command)
        self.assertIn("--commit " + "a" * 40, command)
        self.assertIn("--non-interactive", command)
        self.assertIn("--skip-setup", command)
        self.assertIn("verify-tag", command)
        self.assertIn("allowed_signers", command)
        self.assertNotIn("curl -fsSL", command)
        self.assertIn("rev-parse HEAD", command)
        self.assertIn("venv/bin/hermes", command)
        self.assertIn("launcher_tmp", command)
        self.assertIn("$HOME/.local/bin/hermes", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_rejects_failed_release_provenance_before_launcher_check(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="a" * 40 + "\n"),
            _completed(returncode=42, stderr="HERMES_RELEASE_PROVENANCE_FAILED\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertEqual(caught.exception.code, "release_provenance_failed")
        self.assertEqual(ssh_run.call_count, 3)

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_install_reconciles_partial_state_to_the_pinned_release(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"),
            _completed(), _completed(stdout="hermes 0.18.2\n"),
        ]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {},
                                   "installation": {"status": "partial"}}
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        persisted = write_state.call_args.args[2]
        self.assertEqual(persisted["installation"], {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "installed"})

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_identical_reinstall_uses_the_same_pinned_installer_invocation(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"), _completed(), _completed(stdout="hermes 0.18.2\n"),
            _completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="a" * 40 + "\n"), _completed(), _completed(stdout="hermes 0.18.2\n"),
        ]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        hermes.install("test", "v2026.7.7.2", "a" * 40)
        self.assertEqual(ssh_run.call_args_list[2].args[1], ssh_run.call_args_list[6].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_status_distinguishes_configured_and_running_lifecycle(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="hermes 0.18.2\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {},
                                          "profile": {"sandbox_home": "/home/ubuntu/sandbox"}, "sessions": {}})),
        ]
        out = hermes.status("test")
        self.assertEqual(out["status"], "configured")
        self.assertEqual(out["data"]["running_sessions"], 0)

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_preserves_installed_revision_metadata(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {},
                                   "installation": {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "installed"}}
        hermes.setup("test")
        persisted = write_state.call_args.args[2]
        self.assertEqual(persisted["installation"], {"release_tag": "v2026.7.7.2", "commit": "a" * 40, "status": "configured"})
        self.assertIn("sandbox-integration.json.backup", ssh_run.call_args_list[1].args[1])
        self.assertIn("config set model.default gpt-5.3-codex-spark", ssh_run.call_args_list[1].args[1])
        self.assertIn("config set model.provider openai-codex", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_persists_effective_unfiltered_sandbox_mcp_config(self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}

        hermes.setup("test")

        command = ssh_run.call_args_list[1].args[1]
        self.assertIn("mcp add sandbox --command /home/ubuntu/sandbox/sb-src/sb --args mcp", command)
        self.assertIn("mcp_servers.sandbox.env.SANDBOX_HOME /home/ubuntu/sandbox", command)
        self.assertIn("mcp_servers.sandbox.enabled true", command)
        self.assertIn("mcp_servers.sandbox.connect_timeout 60", command)
        self.assertIn("mcp_servers.sandbox.timeout 1200", command)
        self.assertIn("mcp_servers.sandbox.supports_parallel_tool_calls false", command)
        self.assertIn("mcp_servers.sandbox.tools.resources true", command)
        self.assertIn("mcp_servers.sandbox.tools.prompts true", command)
        self.assertNotIn("mcp_servers.sandbox.tools.include", command)
        self.assertNotIn("mcp_servers.sandbox.tools.exclude", command)

    @patch("sandbox.core._hermes._remote_state_write")
    @patch("sandbox.core._hermes._remote_state_read")
    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_setup_converges_worker_routing_without_auth_or_gateway_activation(
            self, get_remote, ssh_run, read_state, write_state):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed()]
        read_state.return_value = {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}}

        hermes.setup("test")

        command = ssh_run.call_args_list[1].args[1]
        for expected in (
            "config set delegation.provider openai-codex",
            "config set delegation.model gpt-5.6-terra",
            "config set delegation.max_concurrent_children 1",
            "config set delegation.max_spawn_depth 1",
            "config set delegation.orchestrator_enabled false",
            "config set kanban.default_assignee terra",
            "config set auxiliary.kanban_decomposer.model gpt-5.3-codex-spark",
            "config set auxiliary.triage_specifier.model gpt-5.6-sol",
            "profile create luna",
            "profile create terra",
            "profile create sol",
            "-p luna config set model.default gpt-5.6-luna",
            "-p terra config set model.default gpt-5.6-terra",
            "-p sol config set model.default gpt-5.6-sol",
        ):
            self.assertIn(expected, command)
        self.assertNotIn("gateway install", command)
        self.assertNotIn("gateway start", command)
        self.assertNotIn(" auth add ", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_rejects_path_escape_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        with self.assertRaises(hermes.HermesError):
            hermes.clone_repo("test", "https://github.com/acme/repo.git", "../escape")
        ssh_run.assert_not_called()

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_places_ref_before_repository_and_destination(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(),
            _completed(),
        ]
        hermes.clone_repo("test", "https://github.com/acme/repo.git", "repo", "v1.2.3")
        command = ssh_run.call_args_list[1].args[1]
        clone = command[command.rindex("git clone"):]
        self.assertLess(clone.index("--branch v1.2.3"), clone.index("https://github.com/acme/repo.git"))
        self.assertIn("submodule update --init --recursive", command)
        self.assertIn("lfs pull", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_matching_existing_origin_is_idempotent(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="EXISTS_MATCH\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.clone_repo("test", "https://github.com/acme/repo.git", "repo")
        self.assertTrue(out["data"]["existing"])
        self.assertIn("EXISTS_MATCH", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_derives_canonical_name_and_uses_a_temporary_destination(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
            _completed(),
        ]
        out = hermes.clone_repo("test", "https://github.com/acme/example.git")
        self.assertEqual(out["repo"], "example")
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn(".example.clone-", command)
        self.assertIn("mv", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_clone_provider_failure_is_sanitized(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(returncode=1, stderr="token=not-for-output"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.clone_repo("test", "https://github.com/acme/example.git", "example")
        self.assertEqual(caught.exception.code, "clone_failed")
        self.assertNotIn("not-for-output", str(caught.exception))

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_async_run_uses_worktree_by_default(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=""),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=0\nworktrees=0\n"),
            _completed(stdout="0123456789abcdef\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/abcd\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.run("test", "repo", "inspect", async_=True)
        self.assertTrue(out["data"]["worktree"])
        self.assertEqual(out["job_id"], "0123456789abcdef")
        self.assertIn("git worktree add", ssh_run.call_args_list[3].args[1])
        self.assertIn("flock -w 30", ssh_run.call_args_list[3].args[1])
        self.assertIn("attempt=$((attempt + 1))", ssh_run.call_args_list[3].args[1])
        self.assertIn("setsid sh -c", ssh_run.call_args_list[3].args[1])

    def test_no_worktree_command_never_creates_a_worktree(self):
        command = hermes._worktree_command({
            "repo_root": "/home/ubuntu/sandbox/hermes-repos", "locks": "/home/ubuntu/sandbox/runtime/hermes-locks",
            "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "hermes_home": "$HOME/.hermes",
            "launcher": "$HOME/.local/bin/hermes",
        }, "repo", "inspect", worktree=False, async_=True)
        self.assertIn("worktree=false", command)
        self.assertNotIn("git worktree add", command)
        self.assertNotIn("ensure_instance", command)

    def test_worktree_setup_uses_integration_owned_root(self):
        command = hermes._worktree_setup({
            "repo_root": "/home/ubuntu/sandbox/hermes-repos",
            "locks": "/home/ubuntu/sandbox/runtime/hermes-locks",
            "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees",
        }, "repo")
        self.assertIn("/home/ubuntu/sandbox/runtime/hermes-worktrees/repo", command)
        self.assertNotIn("mkdir -p .worktrees", command)
        self.assertNotIn('cwd="$PWD/.worktrees/', command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_dashboard_refuses_without_v2_gate_before_ssh(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.dashboard_action("test", "install")
        self.assertEqual(caught.exception.code, "v2_gate_required")
        self.assertEqual(ssh_run.call_count, 2)

    def test_v2_gate_requires_complete_current_revision_evidence(self):
        state = {
            "schema_version": 1,
            "installation": {"commit": "a" * 40},
            "gates": {"v2_operations": {
                "status": "passed", "commit": "a" * 40,
                "integration_schema": hermes.STATE_SCHEMA,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS},
            }},
        }
        self.assertEqual(hermes._v2_gate(state)["status"], "passed")
        state["installation"]["commit"] = "b" * 40
        gate = hermes._v2_gate(state)
        self.assertEqual(gate["status"], "pending")
        self.assertFalse(gate["revision_matches"])

    def test_v2_gate_requires_current_integration_schema(self):
        state = {
            "schema_version": 1,
            "installation": {"commit": "a" * 40},
            "gates": {"v2_operations": {
                "status": "passed", "commit": "a" * 40,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS},
            }},
        }
        gate = hermes._v2_gate(state)
        self.assertEqual(gate["status"], "pending")
        self.assertIn("integration_schema", gate["missing_checks"])
        self.assertFalse(gate["integration_schema_matches"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_v2_acceptance_never_fabricates_a_passing_record(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}})),
        ]
        out = hermes.acceptance_v2("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "v2_gate_incomplete")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_update_plan_is_read_only_and_reports_immutable_target(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="b" * 40 + "\n"),
            _completed(stdout="a" * 40 + "\n"),
        ]
        out = hermes.update_plan("test", "v2026.7.7.2", "b" * 40)
        self.assertEqual(out["status"], "update_available")
        self.assertEqual(out["data"]["current_commit"], "a" * 40)
        self.assertEqual(out["data"]["backup"], "create verified backup before apply")

    def test_update_apply_quiesces_and_resumes_an_active_gateway(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        installed = {"version": "v2026.7.7.2", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", return_value=installed), \
             patch.object(hermes, "health", return_value={"ok": True}), \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", side_effect=[_completed(stdout="active\n"), _completed()] ) as ssh_run:
            out = hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertTrue(out["data"]["gateway_resumed"])
        self.assertIn("systemctl --user stop", ssh_run.call_args_list[0].args[1])
        self.assertIn("systemctl --user start", ssh_run.call_args_list[1].args[1])

    def test_update_apply_attempts_restore_after_install_failure(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", side_effect=hermes.HermesError("broken", "install_failed")), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="inactive\n")):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)

    def test_update_rollback_resumes_a_previously_active_gateway(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", side_effect=hermes.HermesError("broken", "install_failed")), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="active\n")) as ssh_run:
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)
        self.assertIn("systemctl --user start", ssh_run.call_args_list[1].args[1])

    def test_update_apply_attempts_restore_after_health_failure(self):
        plan = {"status": "update_available", "commit": "b" * 40}
        with patch.object(hermes, "update_plan", return_value=plan), \
             patch.object(hermes, "backup_create", return_value={"data": {"backup_id": "20260711T000000Z-deadbeef"}}), \
             patch.object(hermes, "install", return_value={"version": "v2026.7.7.2", "commit": "b" * 40}), \
             patch.object(hermes, "health", return_value={"ok": False}), \
             patch.object(hermes, "backup_restore") as restore, \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch("sandbox.core._hermes.remote.ssh_run", return_value=_completed(stdout="inactive\n")):
            with self.assertRaises(hermes.HermesError) as caught:
                hermes.update_apply("test", "v2026.7.7.2", "b" * 40, True)
        self.assertEqual(caught.exception.code, "update_rolled_back")
        restore.assert_called_once_with("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_list_discovers_archives_without_state_metadata(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="20260711T000000Z-deadbeef.tar.gz\t123\n"),
        ]
        out = hermes.backup_list("test")
        backup = out["data"]["backups"]["20260711T000000Z-deadbeef"]
        self.assertEqual(backup["size_bytes"], 123)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_create_returns_archive_digest_without_state_round_trip(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="2048\n"),
            _completed(stdout="a" * 64 + "  archive.tar.gz\n"),
        ]
        out = hermes.backup_create("test")
        self.assertEqual(out["data"]["sha256"], "a" * 64)
        self.assertEqual(out["data"]["free_mb"], 2048)
        self.assertEqual(ssh_run.call_count, 3)
        command = ssh_run.call_args_list[2].args[1]
        self.assertIn(".sha256", command)
        self.assertIn("tail -n +11", command)
        self.assertIn("runtime/hermes.json", command)
        self.assertIn("git -C \"$repo\" pack-objects --stdout --revs", command)
        self.assertIn("hermes-agent.pack", command)
        self.assertIn("hermes-agent.tag", command)
        self.assertIn("hermes-agent.commit", command)
        self.assertIn("tar -C \"$repo\" -cf - venv", command)
        self.assertIn("tar -C \"$repo\" -cf - .venv", command)
        self.assertIn("$stage/launcher/hermes", command)
        self.assertIn("home runtime units launcher", command)
        self.assertIn("_backup_forbidden_source_path", command)
        self.assertIn("PYTHONPATH=", command)
        self.assertNotIn("tar -C \"$HOME\"", command)
        self.assertIn("auth\\.json", command)
        self.assertIn("credentials?", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_backup_create_refuses_insufficient_disk_before_archive_creation(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="511\n")]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.backup_create("test")
        self.assertEqual(caught.exception.code, "backup_insufficient_space")
        self.assertEqual(ssh_run.call_count, 2)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_restore_creates_pre_restore_backup_and_verifies_digest(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="2048\n"),
            _completed(stdout="a" * 64 + "  archive.tar.gz\n"),
            _completed(),
        ]
        with patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True)
        setup.assert_called_once_with("test")
        self.assertIn("pre_restore_backup_id", out["data"])
        command = ssh_run.call_args_list[5].args[1]
        self.assertIn(".sha256", command)
        self.assertIn("sha256sum", command)
        self.assertIn("tar -tzf", command)
        self.assertIn("$stage/home/.hermes", command)
        self.assertIn("hermes-agent.pack", command)
        self.assertIn("index-pack --stdin --fix-thin", command)
        self.assertIn("remote add origin https://github.com/NousResearch/hermes-agent.git", command)
        self.assertIn("refs/tags/", command)
        self.assertIn("$restore/.git/shallow", command)
        self.assertIn("checkout -q --detach", command)
        self.assertIn("tar -C \"$source/.hermes/hermes-agent\" -cf - venv", command)
        self.assertIn("hermes-agent.previous", command)
        self.assertIn("launcher_previous", command)
        self.assertIn("if test -f \"$stage/launcher/hermes\"", command)
        self.assertIn("exec \"$HOME/.hermes/hermes-agent/venv/bin/hermes\"", command)
        self.assertIn("dashboard_active", command)
        self.assertIn("$restore/venv/bin/hermes", command)
        self.assertNotIn("pip install", command)
        self.assertIn("runtime/hermes.json", command)

    def test_restore_requires_confirmation_before_remote_access(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.backup_restore("test", "20260711T000000Z-deadbeef", False)
        self.assertEqual(caught.exception.code, "confirmation_required")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_rollback_restore_skips_pre_restore_backup_when_runtime_is_missing(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
        ]
        with patch.object(hermes, "backup_create") as create, \
             patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True,
                                        create_pre_restore_backup=False)
        create.assert_not_called()
        setup.assert_called_once_with("test")
        self.assertIsNone(out["data"]["pre_restore_backup_id"])
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn("had_previous=0", command)
        self.assertIn("had_launcher=0", command)
        self.assertNotIn('test -d "$HOME/.hermes/hermes-agent"; test -f', command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_public_restore_skips_pre_restore_backup_when_runtime_is_missing(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(returncode=1),
            _completed(),
        ]
        with patch.object(hermes, "backup_create") as create, \
             patch.object(hermes, "_record_v2_evidence"), \
             patch.object(hermes, "setup") as setup:
            out = hermes.backup_restore("test", "20260711T000000Z-deadbeef", True)
        create.assert_not_called()
        setup.assert_called_once_with("test")
        self.assertIsNone(out["data"]["pre_restore_backup_id"])
        self.assertIn("hermes-agent.restore", ssh_run.call_args_list[2].args[1])

    def test_update_apply_requires_confirmation_before_remote_access(self):
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.update_apply("test", "v2026.7.7.2", "a" * 40, False)
        self.assertEqual(caught.exception.code, "confirmation_required")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_setup_persists_explicit_allowlist(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(),
        ]
        out = hermes.gateway("test", "setup", ["operator-1"])
        self.assertEqual(out["data"]["allowlist_entries"], 1)
        self.assertIn("sandbox-gateway-allowlist.json", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_start_rejects_missing_recorded_allowlist(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=""),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes.gateway("test", "start")
        self.assertEqual(caught.exception.code, "unsafe_gateway_allowlist")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_install_enables_user_lingering_for_reboot_recovery(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"allowlist": ["operator-1"]})),
            _completed(),
        ]
        out = hermes.gateway("test", "install")
        self.assertTrue(out["ok"])
        self.assertIn("loginctl enable-linger", ssh_run.call_args_list[2].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_status_reports_inactive_without_treating_it_as_a_command_error(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="inactive\n")]
        out = hermes.gateway("test", "status")
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "inactive")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_gateway_logs_are_bounded_and_report_truncation(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(stdout="x" * 5000)]
        out = hermes.gateway("test", "logs", lines=1000)
        self.assertTrue(out["data"]["truncated"])
        self.assertEqual(len(out["data"]["output"]), 4000)
        self.assertIn("journalctl", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_cleanup_defaults_to_dry_run_and_retains_dirty_worktrees(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running", "worktree_path": "/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a"},
            }})),
            _completed(stdout="running\t0123456789abcdef\n"),
            _completed(stdout="clean\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a\n"
                              "dirty\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/hermes-repos/repo/.worktrees/b\n"),
        ]
        out = hermes.cleanup("test", confirm=False)
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(len(out["data"]["clean_candidates"]), 0)
        self.assertEqual(len(out["data"]["dirty_retained"]), 1)
        self.assertEqual(len(out["data"]["active_retained"]), 1)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_reconciliation_marks_only_provably_dead_jobs_stale(self, ssh_run):
        entry = self.entry
        paths = {"jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "state": "/home/ubuntu/sandbox/runtime/hermes.json",
                 "sandbox_home": "/home/ubuntu/sandbox"}
        state = {"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
            "0123456789abcdef": {"state": "running", "worktree_path": "/worktree/a"},
        }}
        ssh_run.side_effect = [_completed(stdout="stale\t0123456789abcdef\n"), _completed()]
        reconciled, stale = hermes._reconcile_sessions(entry, paths, state)
        self.assertEqual(stale, ["0123456789abcdef"])
        self.assertEqual(reconciled["sessions"]["0123456789abcdef"]["state"], "stale")
        self.assertTrue(reconciled["sessions"]["0123456789abcdef"]["requires_manual_review"])

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_refuses_concurrent_job_limit(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout=json.dumps({"max_jobs": 1, "max_worktrees": 8, "min_free_disk_mb": 1024, "min_free_memory_mb": 512})),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=1\nworktrees=0\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._resource_preflight(self.entry, paths)
        self.assertEqual(caught.exception.code, "resource_limit")

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_counts_only_worktree_roots(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout="{}"),
            _completed(stdout="disk_mb=4096\nmemory_mb=4096\njobs=0\nworktrees=1\n"),
        ]
        preflight = hermes._resource_preflight(self.entry, paths)
        self.assertEqual(preflight["metrics"]["worktrees"], 1)
        probe = ssh_run.call_args_list[1].args[1]
        self.assertIn("/home/ubuntu/sandbox/runtime/hermes-worktrees -mindepth 2 -maxdepth 2", probe)
        self.assertIn("-path '*/.worktrees/*' -prune -print", probe)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_resource_preflight_refuses_disk_and_memory_thresholds(self, ssh_run):
        paths = {"policy": "/home/ubuntu/.hermes/policy.json", "sandbox_home": "/home/ubuntu/sandbox",
                 "jobs": "/home/ubuntu/sandbox/runtime/hermes-jobs", "repo_root": "/home/ubuntu/sandbox/hermes-repos",
                 "worktrees": "/home/ubuntu/sandbox/runtime/hermes-worktrees"}
        ssh_run.side_effect = [
            _completed(stdout="{}"),
            _completed(stdout="disk_mb=100\nmemory_mb=100\njobs=0\nworktrees=0\n"),
        ]
        with self.assertRaises(hermes.HermesError) as caught:
            hermes._resource_preflight(self.entry, paths)
        self.assertEqual(caught.exception.code, "resource_limit")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_confirmed_cleanup_prunes_only_completed_job_artifacts(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {}})),
            _completed(stdout="clean\t/home/ubuntu/sandbox/hermes-repos/repo\t/home/ubuntu/sandbox/runtime/hermes-worktrees/repo/a\n"),
            _completed(),
            _completed(),
        ]
        out = hermes.cleanup("test", confirm=True)
        self.assertEqual(out["data"]["completed_job_retention_days"], 7)
        self.assertIn("-name '*.status'", ssh_run.call_args_list[4].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_job_status_reads_bounded_incremental_output(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="completed\n0\nfinished\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running"},
            }})),
            _completed(),
        ]
        out = hermes.job_status("test", "0123456789abcdef", 3)
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["stdout"], "finished")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_job_kill_marks_running_job_cancelled(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout="killed\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "running"},
            }})),
            _completed(),
        ]
        out = hermes.job_kill("test", "0123456789abcdef")
        self.assertTrue(out["killed"])
        self.assertIn("kill -- -", ssh_run.call_args_list[1].args[1])

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_uses_stable_check_names_for_home_relative_paths(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["direct_sb"])
        self.assertTrue(out["data"]["mcp_configured"])
        self.assertTrue(out["data"]["mcp_contract_complete"])
        self.assertTrue(out["data"]["mcp_catalog_complete"])
        command = ssh_run.call_args_list[1].args[1]
        self.assertIn("sandbox_mcp_contract", command)
        self.assertIn("$HOME/.hermes/config.yaml", command)
        self.assertIn("mcp_servers", command)
        self.assertIn("supports_parallel_tool_calls", command)
        self.assertIn("include|exclude", command)

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_refuses_an_incomplete_mcp_catalog(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=0\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "doctor_failed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_doctor_refuses_an_invalid_effective_mcp_contract(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=0\n"
                               "sandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
        ]
        out = hermes.doctor("test")
        self.assertFalse(out["ok"])
        self.assertFalse(out["data"]["mcp_contract_complete"])
        self.assertEqual(out["error"]["code"], "doctor_failed")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_status_reports_absent_when_direct_cli_check_fails(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(returncode=1)]
        out = hermes.status("test")
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "absent")
        self.assertEqual(out["error"]["code"], "not_ready")

    @patch("sandbox.core._hermes.remote.ssh_run")
    @patch("sandbox.core._hermes.remote.get_remote")
    def test_health_aggregates_gateway_sessions_and_gate_without_repair(self, get_remote, ssh_run):
        get_remote.return_value = self.entry
        ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=("git=1\ndocker=1\npython3=1\nsystemctl=1\nflock=1\nsetsid=1\n"
                               "hermes=1\nsandbox_sb=1\nsandbox_mcp_config=1\nsandbox_mcp_contract=1\nsandbox_mcp=1\nsandbox_profile=1\nfree_kb=1\nmem_kb=1\n")),
            _completed(stdout="/home/ubuntu/sandbox\n"),
            _completed(stdout=json.dumps({"schema_version": 1, "repositories": {}, "gates": {}, "sessions": {
                "0123456789abcdef": {"state": "stale"},
            }})),
            _completed(stdout="active\nyes\n"),
        ]
        out = hermes.health("test")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["gateway"], {"state": "active", "linger": "yes"})
        self.assertEqual(out["data"]["sessions"]["stale"], 1)

    def test_health_persists_reboot_gate_evidence_with_boot_marker(self):
        before = {
            "schema_version": 1,
            "last_boot_id": "11111111-1111-1111-1111-111111111111",
            "installation": {"commit": hermes.SUPPORTED_COMMIT},
            "sessions": {},
            "gates": {"v2_operations": {"commit": hermes.SUPPORTED_COMMIT,
                "evidence": {name: "passed" for name in hermes._V2_ACCEPTANCE_CHECKS if name != "reboot_recovery"}}},
        }
        persisted = {"schema_version": 1, "installation": {"commit": hermes.SUPPORTED_COMMIT},
                     "sessions": {}, "gates": {}}
        diagnostic = {"ok": True, "data": {"checks": {}}, "error": None}
        with patch.object(hermes, "doctor", return_value=diagnostic), \
             patch.object(hermes, "_require_remote", return_value=self.entry), \
             patch.object(hermes, "_paths", return_value={"state": "/tmp/hermes.json"}), \
             patch.object(hermes, "_remote_state_read", side_effect=[before, persisted]), \
             patch.object(hermes, "_reconcile_sessions", return_value=(before, [])), \
             patch.object(hermes, "_remote_state_write") as write_state, \
             patch.object(hermes, "_ssh", return_value=_completed(stdout="inactive\nno\n22222222-2222-2222-2222-222222222222\n")):
            out = hermes.health("test")
        written = write_state.call_args.args[2]
        self.assertEqual(written["last_boot_id"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(written["gates"]["v2_operations"]["evidence"]["reboot_recovery"], "passed")
        self.assertEqual(out["data"]["v2_gate"]["status"], "passed")

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
    def test_unversioned_legacy_state_migrates_in_memory(self):
        state = hermes._normalize_state({"repositories": {"repo": {}}})
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["sessions"], {})

    def test_state_round_trip_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "hermes.json"
            hermes.write_state(path, {"schema_version": 1, "repositories": {}})
            self.assertEqual(hermes.read_state(path)["repositories"], {})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.with_name("hermes.json.lock").stat().st_mode & 0o777, 0o600)

    @patch("sandbox.core._hermes.remote.ssh_run")
    def test_remote_state_writes_hold_a_bounded_lock(self, ssh_run):
        ssh_run.return_value = _completed()
        hermes._remote_state_write(
            {"ssh": "ubuntu@example.test"},
            {"sandbox_home": "/home/ubuntu/sandbox", "state": "/home/ubuntu/sandbox/runtime/hermes.json"},
            {"schema_version": 1, "repositories": {}, "sessions": {}, "gates": {}},
        )
        self.assertIn("flock -w 30", ssh_run.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
