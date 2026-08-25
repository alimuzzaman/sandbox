"""Unit tests for remote VPS hosting (specs/014-remote-vps-hosting/).

Stdlib `unittest` only, no docker, no real SSH/VPS -- pure config-read/write,
SSH/git command-construction, and deploy-mechanism logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v

Per Constitution Principle IV, this unit coverage is NOT proof of done on its
own -- see specs/014-remote-vps-hosting/quickstart.md for the required
live-verification pass against a real VPS.
"""
import json
import importlib.util
import io
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
import sandbox.core._config as _cfgmod  # noqa: E402
import sandbox.commands.remote as remote_cmd  # noqa: E402
import sandbox.commands.deploy as deploy_cmd  # noqa: E402
import sandbox.commands.integ as integ_cmd  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                        stdout=stdout, stderr=stderr)


class _patched_config_local:
    """sandbox/core/__init__.py back-fills CONFIG_LOCAL into every submodule's
    OWN namespace (see its module docstring) -- _remote.py's write path and
    _config.py's _local_yaml() read path each resolve their OWN separate
    binding, even though both started out pointing at the same object. A
    single patch.object on just one module leaves the other reading/writing
    the REAL sandbox.local.yml. Patch both together so reads and writes in a
    test agree on the same temp path."""
    def __init__(self, path):
        self._patches = [
            patch.object(sr, "CONFIG_LOCAL", path),
            patch.object(_cfgmod, "CONFIG_LOCAL", path),
        ]

    def __enter__(self):
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


class TestRemoteBlockConfig(unittest.TestCase):
    def test_round_trip_preserves_unrelated_config(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            local_yml.write_text("licensing:\n  elementor_pro_key: keep-me\n")
            with _patched_config_local(local_yml):
                sr._write_remote_block({"myvps": {"ssh": "ubuntu@1.2.3.4"}})
                block = sr._remote_block()
                self.assertEqual(block, {"myvps": {"ssh": "ubuntu@1.2.3.4"}})
                # unrelated section untouched
                import yaml
                raw = yaml.safe_load(local_yml.read_text())
                self.assertEqual(raw["licensing"]["elementor_pro_key"], "keep-me")

    def test_empty_block_removes_the_key_entirely(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr._write_remote_block({"myvps": {"ssh": "x"}})
                sr._write_remote_block({})
                import yaml
                raw = yaml.safe_load(local_yml.read_text()) or {}
                self.assertNotIn("remotes", raw)

    def test_put_remote_is_idempotent_on_reregister(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                self.assertEqual(len(sr.list_remotes()), 1)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4")

    def test_put_remote_updates_only_given_fields(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                sr.put_remote("myvps", provisioned=True)
                entry = sr.get_remote("myvps")
                self.assertEqual(entry["ssh"], "ubuntu@1.2.3.4")
                self.assertTrue(entry["provisioned"])

    def test_remove_remote_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            local_yml = Path(d) / "sandbox.local.yml"
            with _patched_config_local(local_yml):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                existed = sr.remove_remote("myvps")
                self.assertTrue(existed)
                self.assertIsNone(sr.get_remote("myvps"))
                # removing again is a no-op, not an error
                self.assertFalse(sr.remove_remote("myvps"))


class TestFeature022FinalRemoteRegression(unittest.TestCase):
    def test_machine_scoped_list_keeps_exact_json_envelope_without_secret_fields(self):
        remotes = {
            "zeta": {"ssh": "user@private.example", "provisioned": True},
            "alpha": {"ssh": "user@other.example", "provisioned": False},
        }
        output = StringIO()
        with patch.object(sr, "list_remotes", return_value=remotes), \
             patch.object(sr, "check_reachable", side_effect=(True, False)), \
             redirect_stdout(output):
            remote_cmd._cmd_list(types.SimpleNamespace(), True)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload, {
            "ok": True,
            "remotes": [
                {"name": "alpha", "ssh_configured": True, "reachable": True,
                 "provisioned": False, "provider": "unknown"},
                {"name": "zeta", "ssh_configured": True, "reachable": False,
                 "provisioned": True, "provider": "unknown"},
            ],
            "error": None,
        })
        self.assertNotIn("private.example", output.getvalue())
        self.assertNotIn("other.example", output.getvalue())

    def test_machine_scoped_list_exposes_only_safe_provider_labels(self):
        remotes = {
            "safe": {"ssh": "user@safe.example", "provider": "hetzner"},
            "missing": {"ssh": "user@missing.example"},
            "blank": {"ssh": "user@blank.example", "provider": " \t "},
            "nonstring": {"ssh": "user@number.example", "provider": 42},
            "uppercase": {"ssh": "user@uppercase.example", "provider": "DigitalOcean"},
            "space": {"ssh": "user@space.example", "provider": "digital ocean"},
            "control": {"ssh": "user@control.example", "provider": "trusted\nprovider"},
            "secret": {"ssh": "user@secret.example", "provider": "vultr",
                       "bearer_token": "remote-list-secret"},
        }
        output = StringIO()
        with patch.object(sr, "list_remotes", return_value=remotes), \
             patch.object(sr, "check_reachable", return_value=False), \
             redirect_stdout(output):
            remote_cmd._cmd_list(types.SimpleNamespace(), True)

        payload = json.loads(output.getvalue())
        providers = {row["name"]: row.get("provider") for row in payload["remotes"]}
        self.assertEqual(providers, {
            "blank": "unknown",
            "control": "unknown",
            "missing": "unknown",
            "nonstring": "unknown",
            "safe": "hetzner",
            "secret": "vultr",
            "space": "unknown",
            "uppercase": "unknown",
        })
        for value in ("safe.example", "missing.example", "blank.example", "number.example",
                      "uppercase.example", "space.example", "control.example", "secret.example",
                      "remote-list-secret"):
            self.assertNotIn(value, output.getvalue())

    def test_machine_scoped_list_human_output_includes_provider_without_secrets(self):
        remotes = {
            "myvps": {"ssh": "user@private.example", "provider": "digitalocean",
                      "bearer_token": "remote-list-secret"},
        }
        output = StringIO()
        with patch.object(sr, "list_remotes", return_value=remotes), \
             patch.object(sr, "check_reachable", return_value=True), \
             redirect_stdout(output):
            remote_cmd._cmd_list(types.SimpleNamespace(), False)

        self.assertIn("digitalocean", output.getvalue())
        self.assertNotIn("private.example", output.getvalue())
        self.assertNotIn("remote-list-secret", output.getvalue())


class TestValidateRemoteName(unittest.TestCase):
    def test_valid_names_pass(self):
        for name in ["myvps", "my-vps", "my_vps", "vps1"]:
            self.assertEqual(sr.validate_remote_name(name), name)

    def test_invalid_names_raise(self):
        for name in ["My VPS", "vps!", "", "  ", "VPS"]:
            with self.assertRaises(ValueError):
                sr.validate_remote_name(name)


class TestSshRun(unittest.TestCase):
    def test_raises_when_no_ssh_configured(self):
        with self.assertRaises(ValueError):
            sr.ssh_run({}, "true")

    @patch("subprocess.run")
    def test_builds_expected_ssh_command(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                sr.ssh_run({"ssh": "ubuntu@1.2.3.4"}, "true", timeout=10)
                args = mock_run.call_args[0][0]
                self.assertEqual(args[0], "ssh")
                self.assertIn("ubuntu@1.2.3.4", args)
                self.assertIn("true", args)
                self.assertIn("ControlMaster=auto", args)
                self.assertIn("ControlPersist=600", args)
                self.assertIn("ServerAliveInterval=30", args)
                self.assertIn("ServerAliveCountMax=3", args)
                control_path = next(arg for arg in args if arg.startswith("ControlPath="))
                self.assertIn("%C", control_path)
                self.assertNotIn("ubuntu", control_path)
                self.assertNotIn("1.2.3.4", control_path)

    def test_control_directory_is_owner_only(self):
        with tempfile.TemporaryDirectory() as runtime:
            control_dir = Path(runtime) / "s"
            control_dir.mkdir(mode=0o755)
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                self.assertEqual(sr._ensure_ssh_control_dir(), control_dir)
            self.assertEqual(control_dir.stat().st_mode & 0o777, 0o700)

    def test_control_path_never_contains_connection_details(self):
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                args = sr.ssh_command_args(
                    {"ssh": "deploy-token@secret-host.internal:2222"}, "true"
                )
        control_path = next(arg for arg in args if arg.startswith("ControlPath="))
        self.assertIn("%C", control_path)
        self.assertNotIn("deploy-token", control_path)
        self.assertNotIn("secret-host.internal", control_path)

    @patch("subprocess.run")
    def test_builds_expected_ssh_command_with_custom_port(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                sr.ssh_run({"ssh": "ubuntu@1.2.3.4:2222"}, "true", timeout=10)
                args = mock_run.call_args[0][0]
                self.assertIn("-p", args)
                self.assertIn("2222", args)
                self.assertIn("ubuntu@1.2.3.4", args)

    @patch("subprocess.run")
    def test_any_ssh_or_scp_result_is_returned_without_retry(self, mock_run):
        calls = [
            ("ssh", lambda: sr.ssh_run(
                {"ssh": "ubuntu@1.2.3.4"}, "mutating-command"
            )),
            ("scp", lambda: sr.scp_run(
                {"ssh": "ubuntu@1.2.3.4"}, "local.php", "/remote/local.php"
            )),
        ]
        results = [
            (1, "remote command failed"),
            (255, "ControlPath too long"),
            (255, "mux_client_request_session: read from master failed"),
        ]
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                for transport, call in calls:
                    for returncode, stderr in results:
                        with self.subTest(transport=transport, returncode=returncode, stderr=stderr):
                            mock_run.reset_mock()
                            mock_run.return_value = _completed(
                                returncode=returncode, stderr=stderr
                            )
                            result = call()
                            self.assertEqual(result.returncode, returncode)
                            mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_ssh_timeout_is_not_replayed(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=10)
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    sr.ssh_run({"ssh": "ubuntu@1.2.3.4"}, "true", timeout=10)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_scp_uses_control_options_with_custom_port(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                result = sr.scp_run(
                    {"ssh": "ubuntu@1.2.3.4:2222"}, "local.php", "/remote/local.php"
                )
        self.assertEqual(result.returncode, 0)
        multiplexed_args = mock_run.call_args[0][0]
        self.assertIn("ControlMaster=auto", multiplexed_args)
        self.assertIn("ControlPersist=600", multiplexed_args)
        self.assertIn("-P", multiplexed_args)
        self.assertIn("2222", multiplexed_args)
        control_path = next(arg for arg in multiplexed_args if arg.startswith("ControlPath="))
        self.assertIn("%C", control_path)
        self.assertNotIn("ubuntu", control_path)

    @patch("subprocess.run")
    @patch("sandbox.core._remote._ensure_ssh_control_dir", side_effect=PermissionError("denied"))
    def test_ssh_uses_direct_mode_when_control_directory_preparation_fails(
            self, mock_ensure, mock_run):
        mock_run.return_value = _completed(returncode=0)
        sr.ssh_run({"ssh": "ubuntu@1.2.3.4"}, "true")
        mock_ensure.assert_called_once()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("BatchMode=yes", args)
        self.assertIn("ConnectTimeout=10", args)
        self.assertNotIn("ControlMaster=auto", args)
        self.assertNotIn("ControlPersist=600", args)

    @patch("subprocess.run")
    @patch("sandbox.core._remote._ensure_ssh_control_dir", side_effect=OSError("readonly"))
    def test_scp_uses_direct_mode_when_control_directory_preparation_fails(
            self, mock_ensure, mock_run):
        mock_run.return_value = _completed(returncode=0)
        sr.scp_run({"ssh": "ubuntu@1.2.3.4:2222"}, "local.php", "/remote/local.php")
        mock_ensure.assert_called_once()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("-P", args)
        self.assertIn("2222", args)
        self.assertNotIn("ControlMaster=auto", args)
        self.assertNotIn("ControlPersist=60", args)

    def test_parses_ssh_url_with_custom_port(self):
        parts = sr.remote_ssh_parts("ssh://ubuntu@1.2.3.4:2222")
        self.assertEqual(parts["target"], "ubuntu@1.2.3.4")
        self.assertEqual(parts["host"], "1.2.3.4")
        self.assertEqual(parts["port"], 2222)

    def test_redacts_ssh_target_from_user_visible_error(self):
        error = sr.redact_ssh_connection(
            "ssh://ubuntu@1.2.3.4:2222: connection refused",
            {"ssh": "ubuntu@1.2.3.4:2222"},
        )
        self.assertNotIn("ubuntu@1.2.3.4", error)
        self.assertIn("[redacted SSH target]", error)


class TestRemoteDiagnostics(unittest.TestCase):
    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_resource_and_inventory_use_authenticated_control_http(self, urlopen):
        response = MagicMock(status=200)
        response.read.side_effect = [
            b'{"resource_schema":1,"result":{"identity":"host-a"}}',
            b'{"ok":true,"inventory_schema":1,"transport":"control"}',
            b'{"ok":true,"inventory_schema":1,"transport":"control"}',
        ]
        urlopen.return_value.__enter__.return_value = response
        remote = {"control_url": "https://control.example.test",
                  "bearer_token": "secret-token"}
        result = sr.remote_resource_request(remote, {"action": "observe"}, timeout=10)
        self.assertEqual(result["result"]["identity"], "host-a")
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.full_url, "https://control.example.test/resources")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"action": "observe"})
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        inventory = sr.remote_inventory(remote)
        self.assertEqual(inventory["inventory_schema"], 1)
        self.assertEqual(urlopen.call_args_list[1].args[0].full_url,
                         "https://control.example.test/inventory")
        sr.remote_inventory(remote, mode="deep")
        self.assertEqual(urlopen.call_args_list[2].args[0].full_url,
                         "https://control.example.test/inventory?deep=1")

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_diagnostics_use_authenticated_https_without_ssh(self, urlopen):
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok":true,"memory_available_mb":1024}'
        urlopen.return_value.__enter__.return_value = response
        result = sr.remote_diagnostics({
            "control_url": "https://control.example.test", "bearer_token": "secret-token",
        })
        self.assertEqual(result["memory_available_mb"], 1024)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://control.example.test/diagnostics")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_process_diagnostics_require_versioned_control_capability(self, urlopen):
        response = MagicMock(status=200)
        response.read.return_value = json.dumps({
            "ok": True,
            "diagnostics_schema": 2,
            "transport": "control",
            "capabilities": ["process_view", "container_view"],
            "process_view": {"status": "complete"},
            "containers": {"status": "unavailable"},
        }).encode()
        urlopen.return_value.__enter__.return_value = response
        result = sr.remote_diagnostics({
            "control_url": "https://control.example.test", "bearer_token": "secret-token",
        }, include_processes=True)
        self.assertEqual(result["diagnostics_schema"], 2)
        request = urlopen.call_args[0][0]
        self.assertEqual(
            request.full_url, "https://control.example.test/diagnostics?processes=1"
        )

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_process_diagnostics_reject_old_service_without_capability(self, urlopen):
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok":true,"memory_available_mb":1024}'
        urlopen.return_value.__enter__.return_value = response
        with self.assertRaisesRegex(RuntimeError, "does not support process diagnostics"):
            sr.remote_diagnostics({
                "control_url": "https://control.example.test", "bearer_token": "secret-token",
            }, include_processes=True)

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_diagnostics_allow_only_matching_registered_tailscale_http(self, urlopen):
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok":true}'
        urlopen.return_value.__enter__.return_value = response
        result = sr.remote_diagnostics({
            "control_url": "http://100.64.1.2:9174",
            "control_transport": "tailscale",
            "tailscale_host": "100.64.1.2",
            "bearer_token": "secret-token",
        })
        self.assertTrue(result["ok"])
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            sr.remote_diagnostics({
                "control_url": "http://100.64.1.3:9174",
                "control_transport": "tailscale",
                "tailscale_host": "100.64.1.2",
                "bearer_token": "secret-token",
            })

    def test_diagnostics_require_https_control_and_token(self):
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            sr.remote_diagnostics({"control_url": "http://control.example.test", "bearer_token": "x"})
        with self.assertRaisesRegex(RuntimeError, "bearer token"):
            sr.remote_diagnostics({"control_url": "https://control.example.test"})

    def test_ssh_diagnostics_are_disabled_before_any_probe(self):
        with self.assertRaisesRegex(RuntimeError, "no longer supported"):
            sr.remote_ssh_diagnostics({"ssh": "registered-target"})

    def test_process_view_is_bounded_grouped_and_privacy_safe(self):
        process = "\n".join((
            "__SANDBOX_PS_BEGIN__",
            "20 1 1.5 2.0 100 worker",
            "10 1 3.0 2.0 200 /private/token",
            "30 1 2.0 2.0 300 worker",
            "__SANDBOX_PS_END__",
            "__SANDBOX_DOCKER_BEGIN__",
            "__SANDBOX_DOCKER_AVAILABLE__",
            '{"Name":"web","CPUPerc":"4.5%","MemUsage":"2MiB / 4MiB","MemPerc":"50%","PIDs":"3"}',
            "__SANDBOX_DOCKER_END__",
        ))
        process_view, containers = sr._parse_ssh_process_view(process)
        self.assertEqual(process_view["status"], "complete")
        self.assertEqual(process_view["apps"][0]["name"], "worker")
        self.assertEqual(process_view["apps"][0]["process_count"], 2)
        self.assertEqual(process_view["processes"][0]["name"], "redacted")
        self.assertEqual(containers["rows"][0]["memory_used_bytes"], 2 * 1024 * 1024)

    def test_process_parser_marks_bounds_and_optional_docker_fallback(self):
        rows = [f"{pid} 1 0.1 0.1 1 worker" for pid in range(1, 102)]
        payload = "\n".join([
            "__SANDBOX_PS_BEGIN__", *rows, "__SANDBOX_PS_END__",
            "__SANDBOX_DOCKER_BEGIN__", "__SANDBOX_DOCKER_END__",
        ])
        process_view, containers = sr._parse_ssh_process_view(payload)
        self.assertEqual(process_view["status"], "partial")
        self.assertEqual(process_view["observed_count"], 101)
        self.assertTrue(process_view["truncated"])
        self.assertEqual(len(process_view["processes"]), 100)
        self.assertEqual(containers["status"], "unavailable")

        invalid = payload.replace("1 1 0.1 0.1 1 worker", "1 1 nan 0.1 1 worker", 1)
        invalid_view, _ = sr._parse_ssh_process_view(invalid)
        self.assertEqual(invalid_view["status"], "partial")

    def test_empty_ps_section_is_unavailable_not_an_empty_complete_host(self):
        process_view, _ = sr._parse_ssh_process_view(
            "__SANDBOX_PS_BEGIN__\n__SANDBOX_PS_END__\n"
            "__SANDBOX_DOCKER_BEGIN__\n__SANDBOX_DOCKER_END__\n"
        )
        self.assertEqual(process_view["status"], "unavailable")

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_verify_remote_returns_safe_authenticated_envelope(self, urlopen):
        response = MagicMock(status=400)
        response.__enter__.return_value = response
        urlopen.return_value = response
        token = "super-secret-token"
        result = sr.verify_remote({
            "control_url": "https://control.example.test",
            "bearer_token": token,
            "mcp_service": {"runtime_revision": "abc123"},
        }, name="myvps")
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["endpoint"], {"scheme": "https", "host": "control.example.test"})
        self.assertEqual(result["revision"], "abc123")
        self.assertNotIn(token, json.dumps(result))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), f"Bearer {token}")

    @patch("sandbox.core._remote.urllib.request.urlopen")
    def test_verify_remote_redacts_auth_failure_and_userinfo(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://control.example.test/mcp", 401, "Unauthorized", {}, None,
        )
        result = sr.verify_remote({
            "control_url": "https://control.example.test",
            "bearer_token": "secret-token",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "remote_auth_failed")
        with self.assertRaisesRegex(RuntimeError, "auth_verification_unavailable"):
            sr.verify_remote({
                "control_url": "https://user:password@control.example.test",
                "bearer_token": "secret-token",
            })


class TestCheckReachable(unittest.TestCase):
    @patch("subprocess.run")
    def test_true_on_zero_exit(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        self.assertTrue(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))
        mock_run.assert_called_once_with(
            [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "ConnectionAttempts=1",
                "-o", "ControlMaster=no",
                "ubuntu@1.2.3.4",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    @patch("subprocess.run")
    def test_custom_port_keeps_probe_single_and_non_multiplexed(self, mock_run):
        mock_run.return_value = _completed(returncode=0)
        self.assertTrue(sr.check_reachable({"ssh": "ubuntu@1.2.3.4:2222"}))
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[-2:], ["ubuntu@1.2.3.4", "true"])
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertIn("ControlMaster=no", argv)
        self.assertNotIn("ControlPath", " ".join(argv))
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _completed(returncode=255, stderr="Connection refused")
        self.assertFalse(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10))
    def test_false_on_timeout(self, mock_run):
        self.assertFalse(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))

    @patch("subprocess.run", side_effect=OSError("ssh unavailable"))
    def test_false_on_ssh_os_error(self, mock_run):
        self.assertFalse(sr.check_reachable({"ssh": "ubuntu@1.2.3.4"}))
        self.assertEqual(mock_run.call_count, 1)

    def test_false_on_missing_ssh_config(self):
        self.assertFalse(sr.check_reachable({}))


class TestDeployTargetPath(unittest.TestCase):
    @patch("sandbox.core._remote.deploy_target_path", return_value="/srv/deploy/project")
    def test_workspace_path_matches_durable_job_slug(self, _target_path):
        path = sr.remote_workspace_path({}, "/local/path/project", "node-unit")
        self.assertTrue(path.startswith("/srv/deploy/project-workspace-"))
        self.assertNotIn(".workspace-", path)

    @patch("sandbox.core._remote.remote_workspace_path",
           return_value="/srv/deploy/project-workspace-label")
    @patch("sandbox.core._remote.ssh_run", return_value=_completed())
    def test_workspace_prepare_preserves_reusable_bind_mount_directories(
            self, run, _workspace):
        path = sr.prepare_remote_workspace(
            {}, "/local/path/project", "label",
            deployed_path="/srv/deploy/project")

        self.assertEqual(path, "/srv/deploy/project-workspace-label")
        command = run.call_args.args[1]
        self.assertIn('find "$item" -mindepth 1 -maxdepth 1', command)
        self.assertIn('rmdir -- "$item"', command)
        self.assertNotIn("rm -rf /srv/deploy/project-workspace-label", command)

    @patch("subprocess.run")
    def test_resolves_using_project_slug_and_remote_sandbox_home(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="/home/ubuntu/sandbox\n")
        path = sr.deploy_target_path({"ssh": "ubuntu@1.2.3.4"}, "/local/path/my-plugin")
        self.assertEqual(path, "/home/ubuntu/sandbox/deploy-src/my-plugin")

    @patch("subprocess.run")
    def test_raises_when_sandbox_home_unresolvable(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="connection refused")
        with self.assertRaises(RuntimeError):
            sr.deploy_target_path({"ssh": "ubuntu@1.2.3.4"}, "/local/path/my-plugin")


class TestPushCommits(unittest.TestCase):
    def test_nested_source_root_pushes_subtree_without_outer_files(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            source = outer / "site"
            source.mkdir()
            (source / "compose.yml").write_text("services: {}\n")
            (outer / "outer-secret.txt").write_text("must stay outside\n")
            subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
            subprocess.run(["git", "add", "site/compose.yml", "outer-secret.txt"],
                           cwd=outer, check=True)
            subprocess.run([
                "git", "-c", "user.email=sandbox@example.test", "-c", "user.name=Sandbox",
                "commit", "-qm", "initial",
            ], cwd=outer, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=outer,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            (source / "dirty.yml").write_text("services: {}\n")
            push_calls = []
            real_run = sr.subprocess.run

            def run(*args, **kwargs):
                argv = args[0]
                if argv[:2] == ["git", "push"]:
                    push_calls.append(argv)
                    return _completed(returncode=0)
                return real_run(*args, **kwargs)

            with tempfile.TemporaryDirectory() as runtime, \
                 patch.object(sr, "RUNTIME_DIR", Path(runtime)), \
                 patch.object(sr.subprocess, "run", side_effect=run):
                pushed = sr.push_commits(
                    {"ssh": "ubuntu@1.2.3.4"}, source, "/srv/deploy/example-site", "main",
                    source_root=source,
                )

            self.assertEqual(len(push_calls), 1)
            source_spec = push_calls[0][-1]
            self.assertTrue(source_spec.startswith(
                f"{pushed}:refs/heads/sandbox-source-{pushed}"
            ))
            committed = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", pushed],
                cwd=outer, capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertEqual(committed, ["compose.yml"])
            self.assertNotIn("outer-secret.txt", committed)

    def test_nested_source_ref_keeps_immutable_ref_and_subtree_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            source = outer / "site"
            source.mkdir()
            (source / "compose.yml").write_text("services: {}\n")
            (outer / "outer-only.txt").write_text("outside\n")
            subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
            subprocess.run(["git", "add", "."], cwd=outer, check=True)
            subprocess.run([
                "git", "-c", "user.email=sandbox@example.test", "-c", "user.name=Sandbox",
                "commit", "-qm", "initial",
            ], cwd=outer, check=True)
            source_ref = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=outer,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            push_calls = []
            real_run = sr.subprocess.run

            def run(*args, **kwargs):
                argv = args[0]
                if argv[:2] == ["git", "push"]:
                    push_calls.append(argv)
                    return _completed(returncode=0)
                return real_run(*args, **kwargs)

            with tempfile.TemporaryDirectory() as runtime, \
                 patch.object(sr, "RUNTIME_DIR", Path(runtime)), \
                 patch.object(sr.subprocess, "run", side_effect=run):
                pushed = sr.push_commits(
                    {"ssh": "ubuntu@1.2.3.4"}, source, "/srv/deploy/example-site", None,
                    source_ref="HEAD", resolved_sha=source_ref, source_root=source,
                )

            self.assertEqual(len(push_calls), 1)
            self.assertIn(f"refs/heads/sandbox-source-{pushed}", push_calls[0][-1])
            self.assertNotEqual(pushed, source_ref)
            committed = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", pushed],
                cwd=outer, capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertEqual(committed, ["compose.yml"])

    def test_nested_source_root_avoids_preseeded_full_tree_branch(self):
        """A legacy full-tree branch must survive nested subtree deployment."""
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            source = outer / "site"
            source.mkdir()
            (source / "compose.yml").write_text("services: {}\n")
            (outer / "outer-only.txt").write_text("legacy outer file\n")
            subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
            subprocess.run(["git", "add", "."], cwd=outer, check=True)
            subprocess.run([
                "git", "-c", "user.email=sandbox@example.test", "-c", "user.name=Sandbox",
                "commit", "-qm", "legacy full tree",
            ], cwd=outer, check=True)
            full_tree_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=outer,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            bare = outer / "target.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
            subprocess.run([
                "git", "push", str(bare), f"{full_tree_sha}:refs/heads/main",
            ], cwd=outer, check=True, capture_output=True, text=True)

            with tempfile.TemporaryDirectory() as runtime, \
                 patch.object(sr, "RUNTIME_DIR", Path(runtime)), \
                 patch.object(sr, "git_ssh_url", return_value=str(bare)):
                pushed = sr.push_commits(
                    {"ssh": "ubuntu@1.2.3.4"}, source, "/srv/deploy/example-site", "main",
                    source_root=source,
                )

            main_names = subprocess.run(
                ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "refs/heads/main"],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            staging_ref = f"refs/heads/sandbox-source-{pushed}"
            staged_names = subprocess.run(
                ["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", staging_ref],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertEqual(main_names, ["outer-only.txt", "site/compose.yml"])
            self.assertEqual(staged_names, ["compose.yml"])

    @patch("subprocess.run")
    def test_pushes_head_to_the_correct_branch_and_url(self, mock_run):
        revision = "a" * 40
        mock_run.side_effect = [
            _completed(returncode=0, stdout=revision + "\n"),
            _completed(returncode=0),
        ]
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)), \
                 patch.dict(os.environ, {"PRESERVE_ME": "value"}):
                sha = sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                                       "/home/ubuntu/sandbox/deploy-src/proj", "main")
        self.assertEqual(sha, revision)
        push_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(push_args[0], "git")
        self.assertEqual(push_args[1], "push")
        self.assertIn("ssh://ubuntu@1.2.3.4/home/ubuntu/sandbox/deploy-src/proj", push_args)
        self.assertIn(f"{revision}:refs/heads/main", push_args)
        push_env = mock_run.call_args_list[1][1]["env"]
        git_ssh = push_env["GIT_SSH_COMMAND"]
        self.assertEqual(push_env["PRESERVE_ME"], "value")
        self.assertIn("BatchMode=yes", git_ssh)
        self.assertIn("ConnectTimeout=10", git_ssh)
        self.assertIn("ControlMaster=auto", git_ssh)
        self.assertIn("ControlPersist=600", git_ssh)
        self.assertIn("%C", git_ssh)
        self.assertNotIn("ubuntu", git_ssh)
        self.assertNotIn("1.2.3.4", git_ssh)

    @patch("subprocess.run")
    def test_push_url_preserves_custom_ssh_port(self, mock_run):
        revision = "a" * 40
        mock_run.side_effect = [
            _completed(returncode=0, stdout=revision + "\n"),
            _completed(returncode=0),
        ]
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                sr.push_commits({"ssh": "ubuntu@1.2.3.4:2222"}, "/local/proj",
                                 "/home/ubuntu/sandbox/deploy-src/proj", "main")
        push_args = mock_run.call_args_list[1][0][0]
        self.assertIn("ssh://ubuntu@1.2.3.4:2222/home/ubuntu/sandbox/deploy-src/proj",
                      push_args)
        self.assertEqual(
            shlex.split(mock_run.call_args_list[1][1]["env"]["GIT_SSH_COMMAND"])[-2:],
            ["-p", "2222"],
        )

    @patch("subprocess.run")
    def test_push_failure_is_not_replayed(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="a" * 40 + "\n"),
            _completed(returncode=255, stderr="ControlPath too long"),
        ]
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                with self.assertRaises(RuntimeError):
                    sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                                     "/home/ubuntu/sandbox/deploy-src/proj", "main")
        self.assertEqual(mock_run.call_count, 2)

    @patch("subprocess.run")
    @patch("sandbox.core._remote._ensure_ssh_control_dir", side_effect=PermissionError("denied"))
    def test_push_uses_direct_ssh_when_control_directory_preparation_fails(
        self, mock_ensure, mock_run):
        revision = "a" * 40
        mock_run.side_effect = [
            _completed(returncode=0, stdout=revision + "\n"),
            _completed(returncode=0),
        ]
        sr.push_commits({"ssh": "ubuntu@1.2.3.4:2222"}, "/local/proj",
                        "/home/ubuntu/sandbox/deploy-src/proj", "main")
        mock_ensure.assert_called_once()
        self.assertEqual(mock_run.call_count, 2)  # resolve once, then push pinned SHA
        git_ssh = mock_run.call_args_list[1][1]["env"]["GIT_SSH_COMMAND"]
        self.assertIn("BatchMode=yes", git_ssh)
        self.assertIn("ConnectTimeout=10", git_ssh)
        self.assertEqual(shlex.split(git_ssh)[-2:], ["-p", "2222"])
        self.assertNotIn("ControlMaster=auto", git_ssh)
        self.assertNotIn("ControlPersist=600", git_ssh)

    @patch("subprocess.run")
    def test_never_references_origin_or_any_other_remote(self, mock_run):
        # Spec FR-008: deploy must succeed even for a branch never pushed to
        # GitHub/origin. Guards against a future change accidentally routing
        # through the project's OWN git remotes instead of pushing straight
        # to the VPS's deploy-target path.
        revision = "a" * 40
        mock_run.side_effect = [
            _completed(returncode=0, stdout=revision + "\n"),
            _completed(returncode=0),
        ]
        sr.push_commits({"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                         "/home/ubuntu/sandbox/deploy-src/proj", "wip-branch")
        push_args = mock_run.call_args_list[1][0][0]
        self.assertNotIn("origin", push_args)

    @patch("subprocess.run")
    def test_head_movement_after_resolution_cannot_change_pushed_or_returned_sha(self, mock_run):
        selected = "a" * 40
        moved = "b" * 40
        rev_parse_count = 0

        def run(args, **_kwargs):
            nonlocal rev_parse_count
            if args[:3] == ["git", "rev-parse", "HEAD"]:
                rev_parse_count += 1
                revision = selected if rev_parse_count == 1 else moved
                return _completed(returncode=0, stdout=revision + "\n")
            return _completed(returncode=0)

        mock_run.side_effect = run
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                pushed = sr.push_commits(
                    {"ssh": "ubuntu@1.2.3.4"}, "/local/proj",
                    "/home/ubuntu/sandbox/deploy-src/proj", "main",
                )

        self.assertEqual(pushed, selected)
        self.assertEqual(rev_parse_count, 1)
        push_args = mock_run.call_args_list[1].args[0]
        self.assertIn(f"{selected}:refs/heads/main", push_args)
        self.assertNotIn(moved, push_args)

    @patch("subprocess.run")
    def test_source_ref_resolves_full_sha_and_rejects_dirty_tree(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="A" * 40 + "\n"),
            _completed(returncode=0, stdout="diff --git a/x b/x\n"),
            _completed(returncode=0, stdout=""),
        ]
        with self.assertRaisesRegex(ValueError, "clean working tree"):
            sr.resolve_source_ref("/local/proj", "refs/tags/v1")
        self.assertEqual(mock_run.call_count, 3)
        self.assertTrue(all("commit" not in " ".join(map(str, call.args[0]))
                            for call in mock_run.call_args_list[1:]))

    @patch("subprocess.run")
    def test_source_ref_push_uses_sha_derived_immutable_destination(self, mock_run):
        sha = "b" * 40
        mock_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as runtime:
            with patch.object(sr, "RUNTIME_DIR", Path(runtime)):
                pushed = sr.push_commits(
                    {"ssh": "ubuntu@1.2.3.4"}, "/local/proj", "/srv/deploy/proj",
                    None, source_ref="refs/tags/v1", resolved_sha=sha,
                )
        self.assertEqual(pushed, sha)
        push_args = mock_run.call_args_list[0].args[0]
        self.assertIn(f"{sha}:refs/heads/sandbox-source-{sha}", push_args)
        self.assertNotIn("git commit", " ".join(push_args))
        self.assertNotIn("git stash", " ".join(push_args))


class TestCaptureAndApplyUncommitted(unittest.TestCase):
    def test_appledouble_filter_is_basename_only(self):
        kept, skipped = sr.filter_appledouble_paths([
            "._root-sidecar", "nested/._nested-sidecar", ".env",
            "nested/.env", "notes._suffix",
        ])
        self.assertEqual(skipped, 2)
        self.assertEqual(kept, [".env", "nested/.env", "notes._suffix"])

    def test_deploy_descriptor_includes_primary_but_not_machine_override(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sandbox.config.json").write_text('{"slug":"demo"}')
            (root / "sandbox.config.override.json").write_text(
                '{"plugins":{"private":"/machine/path"}}'
            )

            self.assertEqual(
                sr.deploy_project_descriptor_files(root),
                ["sandbox.config.json"],
            )

    @patch("subprocess.run")
    def test_capture_returns_diff_and_untracked_files(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="diff --git a/f.php b/f.php\n+x\n"),
            _completed(returncode=0, stdout="M  f.php\n?? new-file.txt\n?? sub/new2.txt\n"),
        ]
        diff_text, untracked = sr.capture_uncommitted("/local/proj")
        self.assertIn("diff --git", diff_text)
        self.assertEqual(untracked, ["new-file.txt", "sub/new2.txt"])

    @patch("subprocess.run")
    def test_capture_excludes_appledouble_untracked_but_keeps_dotfiles(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout=""),
            _completed(
                returncode=0,
                stdout=(
                    "?? ._root-sidecar\n"
                    "?? .env\n"
                    "?? nested/._nested-sidecar\n"
                    "?? nested/.env\n"
                ),
            ),
        ]
        diagnostic = StringIO()
        with redirect_stderr(diagnostic):
            diff_text, untracked = sr.capture_uncommitted("/local/proj")
        self.assertEqual(diff_text, "")
        self.assertEqual(untracked, [".env", "nested/.env"])
        self.assertIn("skipped 2", diagnostic.getvalue())
        self.assertNotIn("._root-sidecar", diagnostic.getvalue())
        self.assertNotIn("nested/._nested-sidecar", diagnostic.getvalue())

    @patch("subprocess.run")
    def test_capture_ignores_tracked_appledouble_only_diff(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="diff --git a/._tracked b/._tracked\n"),
            _completed(returncode=0, stdout=" M ._tracked\n"),
        ]
        with redirect_stderr(StringIO()):
            diff_text, untracked = sr.capture_uncommitted("/local/proj")
        self.assertEqual(diff_text, "")
        self.assertEqual(untracked, [])

    @patch("subprocess.run")
    def test_uses_untracked_files_all_so_nested_new_files_are_not_collapsed(self, mock_run):
        # Real bug caught only by live-verifying against an actual remote (a
        # mocked porcelain string can't reveal this -- the mock has to assume
        # the shape it's testing): plain `git status --porcelain` collapses a
        # brand-new untracked DIRECTORY to just its directory name (`subdir/`)
        # rather than listing files inside it. Without `--untracked-files=all`,
        # apply_uncommitted's `local_path.is_file()` check would silently skip
        # every file inside a new untracked directory (a directory is never
        # a file), and it would never transfer.
        mock_run.side_effect = [
            _completed(returncode=0, stdout=""),
            _completed(returncode=0, stdout="?? subdir/nested.txt\n"),
        ]
        sr.capture_uncommitted("/local/proj")
        status_call_args = mock_run.call_args_list[1][0][0]
        self.assertIn("--untracked-files=all", status_call_args)

    @patch("subprocess.run")
    def test_replace_not_stack_resets_before_applying(self, mock_run):
        # Verifies the ORDER: reset_target_to must run (and succeed) before
        # apply_uncommitted's diff-apply step -- this is what makes a second
        # deploy replace rather than stack (spec FR-007).
        mock_run.return_value = _completed(returncode=0)
        calls = []

        def record(*args, **kwargs):
            calls.append(args[0])
            return _completed(returncode=0)

        mock_run.side_effect = record
        entry = {"ssh": "ubuntu@1.2.3.4"}
        sr.reset_target_to(entry, "/home/ubuntu/sandbox/deploy-src/proj", "abc1234")
        with patch("sandbox.core._remote.ssh_run", return_value=_completed(returncode=0)):
            sr.apply_uncommitted(entry, "/home/ubuntu/sandbox/deploy-src/proj",
                                  "/local/proj", "diff --git a/f b/f\n+x\n", [])
        # reset happened via ssh_run (subprocess.run under the hood) before apply
        reset_call = [c for c in calls if "git reset --hard abc1234" in " ".join(c)]
        self.assertTrue(reset_call, "expected a git reset --hard call")

    @patch("subprocess.run")
    def test_reset_also_removes_untracked_files_left_by_a_prior_deploy(self, mock_run):
        # Real bug caught by /speckit-analyze: `git reset --hard` alone only
        # rewinds TRACKED files -- it does nothing about an untracked file a
        # PREVIOUS deploy transferred. Without also cleaning those, a file
        # added in deploy #1 and later deleted locally would survive on the
        # VPS forever, breaking the "replace, not stack" guarantee (FR-007)
        # for exactly that class of file.
        mock_run.return_value = _completed(returncode=0)
        sr.reset_target_to({"ssh": "ubuntu@1.2.3.4"},
                            "/home/ubuntu/sandbox/deploy-src/proj", "abc1234")
        cmd_arg = mock_run.call_args[0][0]
        joined = " ".join(cmd_arg)
        self.assertIn("git reset --hard abc1234", joined)
        self.assertIn("git clean -fd", joined)
        # order matters: reset must come before clean
        self.assertLess(joined.index("git reset --hard"), joined.index("git clean -fd"))

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_apply_counts_tracked_and_untracked_files(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        # subprocess.run is used for: git diff --name-only, git diff deleted,
        # and one local tar archive; the archive is streamed over one SSH
        # session.
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "a.php").write_text("x")
            (proj / "b.php").write_text("y")
            mock_run.side_effect = [
                _completed(returncode=0, stdout="a.php\n"),  # git diff --name-only
                _completed(returncode=0, stdout=""),           # git diff deleted
                _completed(returncode=0, stdout=b"archive"),   # tar archive
            ]
            applied = sr.apply_uncommitted(
                {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
                str(proj), "diff --git a/x b/x\n+y\n", ["b.php"],
            )
            self.assertEqual(applied, 2)  # 1 tracked-diff file + 1 untracked file
            self.assertEqual(mock_run.call_args_list[2][0][0][:3], ["tar", "-czf", "-"])
            self.assertIn("a.php", mock_run.call_args_list[2][0][0])
            self.assertIn("b.php", mock_run.call_args_list[2][0][0])
            mock_ssh_run.assert_called_once()
            self.assertEqual(mock_ssh_run.call_args.kwargs["input_data"], b"archive")

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_streamed_dirty_files_use_one_remote_session(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "b.php").write_text("y")
            mock_run.return_value = _completed(returncode=0, stdout=b"archive")
            sr.apply_uncommitted(
                {"ssh": "ubuntu@1.2.3.4:2222"},
                "/home/ubuntu/sandbox/deploy-src/proj",
                str(proj), "", ["b.php"],
            )
            tar_args = mock_run.call_args[0][0]
            self.assertEqual(tar_args[:3], ["tar", "-czf", "-"])
            self.assertIn("b.php", tar_args)
            mock_ssh_run.assert_called_once()
            self.assertIn("/home/ubuntu/sandbox/deploy-src/proj", mock_ssh_run.call_args[0][1])

    @patch("sandbox.core._remote.ssh_run")
    def test_dirty_archive_excludes_sidecars_and_preserves_dotfiles_bytes(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            nested = project / "nested"
            nested.mkdir()
            (project / "._root-sidecar").write_bytes(b"sidecar-root")
            (nested / "._nested-sidecar").write_bytes(b"sidecar-nested")
            (project / ".env").write_bytes(b"SECRET=keep-this-byte-sequence\x00\xff")
            (nested / ".env").write_bytes(b"NESTED=keep")
            (project / "ordinary.bin").write_bytes(b"\x00\x01\xfe\xff")
            untracked = [
                "._root-sidecar", "nested/._nested-sidecar", ".env",
                "nested/.env", "ordinary.bin",
            ]
            diagnostic = StringIO()
            with redirect_stderr(diagnostic):
                applied = sr.apply_uncommitted(
                    {"ssh": "ubuntu@1.2.3.4"}, "/remote/project", project,
                    "", untracked,
                )
            self.assertEqual(applied, 3)
            self.assertIn("skipped 2", diagnostic.getvalue())
            self.assertNotIn("._root-sidecar", diagnostic.getvalue())
            archive_bytes = mock_ssh_run.call_args.kwargs["input_data"]
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
                members = {
                    member.name[2:] if member.name.startswith("./") else member.name
                    for member in archive.getmembers()
                }
                self.assertEqual(
                    members,
                    {".env", "nested/.env", "ordinary.bin"},
                )
                files = {
                    (member.name[2:] if member.name.startswith("./") else member.name):
                    archive.extractfile(member).read()
                    for member in archive.getmembers() if member.isfile()
                }
                self.assertEqual(files[".env"], b"SECRET=keep-this-byte-sequence\x00\xff")
                self.assertEqual(files["nested/.env"], b"NESTED=keep")
                self.assertEqual(files["ordinary.bin"], b"\x00\x01\xfe\xff")

    @patch("sandbox.core._remote.ssh_run_batch")
    @patch("subprocess.run")
    def test_apply_removes_deleted_tracked_files(self, mock_run, mock_ssh_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout="gone.php\n"),  # git diff --name-only
            _completed(returncode=0, stdout="gone.php\n"),  # git diff deleted
        ]
        mock_ssh_run.return_value = _completed(returncode=0)
        applied = sr.apply_uncommitted(
            {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
            "/local/proj", "diff --git a/gone.php b/gone.php\n", [],
        )
        self.assertEqual(applied, 1)
        rm_cmd = mock_ssh_run.call_args[0][1][0]
        self.assertIn("rm -f --", rm_cmd)
        self.assertIn("gone.php", rm_cmd)

    @patch("sandbox.core._remote.ssh_run")
    @patch("subprocess.run")
    def test_missing_untracked_file_is_skipped_not_erroring(self, mock_run, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        mock_run.return_value = _completed(returncode=0)
        applied = sr.apply_uncommitted(
            {"ssh": "ubuntu@1.2.3.4"}, "/home/ubuntu/sandbox/deploy-src/proj",
            "/nonexistent/local/proj", "", ["does-not-exist.txt"],
        )
        self.assertEqual(applied, 0)


class TestCurrentBranch(unittest.TestCase):
    @patch("subprocess.run")
    def test_returns_branch_name(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="main\n")
        self.assertEqual(sr.current_branch("/local/proj"), "main")

    @patch("subprocess.run")
    def test_raises_on_detached_head(self, mock_run):
        mock_run.return_value = _completed(returncode=0, stdout="HEAD\n")
        with self.assertRaises(RuntimeError):
            sr.current_branch("/local/proj")


class TestCmdRemoteAdd(unittest.TestCase):
    def test_add_requires_ssh_url(self):
        args = MagicMock(name="myvps", ssh_url=None, json=False)
        args.name = "myvps"
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                with self.assertRaises(SystemExit):
                    remote_cmd._cmd_add(args, as_json=False)

    def test_add_registers_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.name = "myvps"
                args.ssh_url = "ssh://ubuntu@1.2.3.4"
                remote_cmd._cmd_add(args, as_json=False)
                remote_cmd._cmd_add(args, as_json=False)
                self.assertEqual(len(sr.list_remotes()), 1)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4")

    def test_add_preserves_custom_ssh_port_in_normalized_form(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.name = "myvps"
                args.ssh_url = "ssh://ubuntu@1.2.3.4:2222"
                remote_cmd._cmd_add(args, as_json=False)
                self.assertEqual(sr.get_remote("myvps")["ssh"], "ubuntu@1.2.3.4:2222")

    def test_add_json_does_not_return_ssh_target(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock(ssh_url="ssh://ubuntu@1.2.3.4", json=True)
                args.name = "myvps"
                with patch("builtins.print") as mock_print:
                    remote_cmd._cmd_add(args, as_json=True)
                output = mock_print.call_args[0][0]
                self.assertNotIn("ubuntu@1.2.3.4", output)
                self.assertTrue(json.loads(output)["ssh_configured"])

    def test_list_never_returns_ssh_target(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                with patch.object(sr, "check_reachable", return_value=True), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_list(args, as_json=True)
                output = mock_print.call_args[0][0]
                self.assertNotIn("ubuntu@1.2.3.4", output)
                self.assertTrue(json.loads(output)["remotes"][0]["ssh_configured"])


class TestCmdRemoteRemove(unittest.TestCase):
    def test_remove_never_calls_ssh(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                with patch("subprocess.run") as mock_run:
                    remote_cmd._cmd_remove(args, as_json=False)
                    mock_run.assert_not_called()
                self.assertIsNone(sr.get_remote("myvps"))


class TestUploadRuntimeSource(unittest.TestCase):
    @patch("subprocess.run")
    def test_streams_this_checkout_to_remote_sandbox_home(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout=b"tarball", stderr=b""),
            _completed(returncode=0, stdout=b"", stderr=b""),
        ]
        remote_cmd._upload_runtime_source("ubuntu@1.2.3.4")

        tar_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(tar_args[0], "tar")
        self.assertIn("--exclude", tar_args)
        self.assertIn(".git", tar_args)
        self.assertIn(".cli-venv", tar_args)
        self.assertIn("mcp/wp-server/.venv", tar_args)
        self.assertIn("runtime", tar_args)
        self.assertIn("node_modules", tar_args)
        self.assertIn("src/desktop/release", tar_args)
        self.assertIn("src/desktop/build", tar_args)
        self.assertIn("src/desktop/dist", tar_args)
        self.assertIn(".cache", tar_args)
        self.assertIn("._*", tar_args)
        self.assertIn("*/._*", tar_args)
        self.assertEqual(mock_run.call_args_list[0][1]["cwd"], str(ROOT))

        ssh_args = mock_run.call_args_list[1][0][0]
        self.assertEqual(ssh_args[0], "ssh")
        self.assertIn("ubuntu@1.2.3.4", ssh_args)
        self.assertIn("sb-src", ssh_args[-1])
        self.assertNotIn("rm -rf", ssh_args[-1])
        self.assertEqual(mock_run.call_args_list[1][1]["input"], b"tarball")
        self.assertFalse(mock_run.call_args_list[1][1]["text"])

    def test_upload_runtime_source_archive_excludes_sidecars_and_preserves_dotfiles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sandbox").mkdir()
            (root / "nested").mkdir()
            (root / "node_modules" / "large-tree").mkdir(parents=True)
            (root / "src" / "desktop" / "release").mkdir(parents=True)
            (root / ".cache").mkdir()
            (root / "._runtime-sidecar").write_bytes(b"sidecar")
            (root / "nested" / "._runtime-nested-sidecar").write_bytes(b"sidecar")
            (root / ".env").write_bytes(b"RUNTIME=keep\x00\xff")
            (root / "nested" / ".gitignore").write_bytes(b"*.tmp\n")
            (root / "ordinary.bin").write_bytes(b"\x00\x01\xfe\xff")
            (root / "node_modules" / "large-tree" / "dependency.js").write_bytes(b"generated")
            (root / "src" / "desktop" / "release" / "Sandbox.app").write_bytes(b"generated")
            (root / ".cache" / "artifact").write_bytes(b"generated")
            ssh_result = _completed(returncode=0, stdout=b"", stderr=b"")
            with patch.object(remote_cmd, "ROOT", root), \
                 patch.object(sr, "ssh_process", return_value=ssh_result) as ssh, \
                 redirect_stderr(StringIO()) as diagnostic:
                remote_cmd._upload_runtime_source("ubuntu@1.2.3.4")
            payload = ssh.call_args.kwargs["input_data"]
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                members = {
                    member.name[2:] if member.name.startswith("./") else member.name
                    for member in archive.getmembers()
                }
                self.assertEqual(
                    members,
                    {".", "sandbox", "nested", ".env", "nested/.gitignore", "ordinary.bin",
                     "src", "src/desktop"},
                )
                files = {
                    (member.name[2:] if member.name.startswith("./") else member.name):
                    archive.extractfile(member).read()
                    for member in archive.getmembers() if member.isfile()
                }
                self.assertEqual(files[".env"], b"RUNTIME=keep\x00\xff")
                self.assertEqual(files["nested/.gitignore"], b"*.tmp\n")
                self.assertEqual(files["ordinary.bin"], b"\x00\x01\xfe\xff")
            self.assertNotIn("._runtime-sidecar", diagnostic.getvalue())

    @patch("subprocess.run")
    def test_upload_runtime_source_uses_custom_ssh_port(self, mock_run):
        mock_run.side_effect = [
            _completed(returncode=0, stdout=b"tarball", stderr=b""),
            _completed(returncode=0, stdout=b"", stderr=b""),
        ]
        remote_cmd._upload_runtime_source("ubuntu@1.2.3.4:2222")
        ssh_args = mock_run.call_args_list[1][0][0]
        self.assertIn("-p", ssh_args)
        self.assertIn("2222", ssh_args)

    @patch("subprocess.run")
    def test_raises_when_tar_fails_before_ssh(self, mock_run):
        mock_run.return_value = _completed(
            returncode=2, stdout=b"", stderr=b"tar failed"
        )
        with self.assertRaisesRegex(RuntimeError, "could not package"):
            remote_cmd._upload_runtime_source("ubuntu@1.2.3.4")
        self.assertEqual(mock_run.call_count, 1)


class TestCmdRemoteProvisionKeepsTokenSecret(unittest.TestCase):
    def test_provision_result_omits_the_minted_token(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "https"
                args.control_host = "sandbox.example.com"
                args.confirm = True
                with patch("subprocess.run", return_value=_completed(returncode=0)), \
                     patch.object(remote_cmd, "RUNTIME_DIR", Path(d) / "runtime"), \
                     patch.object(sr, "configure_https_proxy"), \
                     patch.object(sr, "start_remote_mcp_server"), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_provision(args, as_json=True)
                printed = mock_print.call_args[0][0]
                result = json.loads(printed)
                self.assertNotIn("bearer_token", result)
                self.assertNotIn("token", json.dumps(result).lower())
                self.assertEqual(result["control_transport"], "https")
                self.assertEqual(result["control_url"], "https://sandbox.example.com")
                self.assertEqual(result["provision_log"]["status"], "complete")
                journal = next((Path(d) / "runtime" / "remote-provision" / "myvps").glob("*.json"))
                self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
                events = json.loads(journal.read_text())["events"]
                self.assertEqual([event["stage"] for event in events], [
                    "started", "runtime_staging", "runtime_staged", "bootstrap_running",
                    "bootstrap_complete", "control_service_starting", "complete",
                ])

    def test_provision_can_explicitly_use_tailscale(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "tailscale"
                args.control_host = None
                args.confirm = True
                with patch("subprocess.run", return_value=_completed(returncode=0)) as mock_run, \
                     patch.object(remote_cmd, "RUNTIME_DIR", Path(d) / "runtime"), \
                     patch.object(sr, "resolve_tailscale_ip", return_value="100.64.1.2"), \
                     patch.object(sr, "start_remote_mcp_server"), \
                     patch("builtins.print") as mock_print:
                    remote_cmd._cmd_provision(args, as_json=True)
                provision_ssh_cmd = mock_run.call_args_list[2][0][0][-1]
                self.assertIn("SANDBOX_CONTROL_TRANSPORT=tailscale", provision_ssh_cmd)
                result = json.loads(mock_print.call_args[0][0])
                self.assertEqual(result["control_transport"], "tailscale")
                self.assertEqual(result["control_url"], "http://100.64.1.2:9174")

    def test_https_json_provision_requires_control_host(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "https"
                args.control_host = None
                args.confirm = False
                with self.assertRaises(SystemExit):
                    remote_cmd._cmd_provision(args, as_json=True)

    def test_provision_without_confirmation_is_plan_only(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = MagicMock()
                args.name = "myvps"
                args.control = "https"
                args.control_host = "sandbox.example.com"
                args.confirm = False
                with patch.object(remote_cmd, "_upload_runtime_source") as upload, \
                     patch("subprocess.run") as run, patch("builtins.print") as printed:
                    remote_cmd._cmd_provision(args, as_json=True)
                upload.assert_not_called()
                run.assert_not_called()
                payload = json.loads(printed.call_args.args[0])
                self.assertEqual(payload["status"], "planned")
                self.assertFalse(payload["provisioned"])
                self.assertTrue(payload["data"]["requires_confirm"])

    def test_failed_staging_leaves_a_redacted_owner_only_journal(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                args = types.SimpleNamespace(name="myvps", control="https",
                                             control_host="sandbox.example.com", confirm=True)
                with patch.object(remote_cmd, "RUNTIME_DIR", Path(d) / "runtime"), \
                     patch.object(remote_cmd, "_upload_runtime_source",
                                  side_effect=RuntimeError("token=private-value staging failed")):
                    with self.assertRaises(SystemExit):
                        remote_cmd._cmd_provision(args, as_json=True)
                journal = next((Path(d) / "runtime" / "remote-provision" / "myvps").glob("*.json"))
                payload = json.loads(journal.read_text())
                self.assertEqual(payload["status"], "failed")
                self.assertEqual(payload["events"][-1]["stage"], "runtime_staging_failed")
                self.assertNotIn("private-value", journal.read_text())
                self.assertEqual(journal.stat().st_mode & 0o777, 0o600)

    def test_next_plan_surfaces_an_incomplete_previous_provision_log(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4")
                with patch.object(remote_cmd, "RUNTIME_DIR", Path(d) / "runtime"):
                    journal = remote_cmd._new_provision_log("myvps", "https")
                    remote_cmd._record_provision_event(journal, "runtime_staged")
                    args = types.SimpleNamespace(name="myvps", control="https",
                                                 control_host="sandbox.example.com", confirm=False)
                    output = StringIO()
                    with redirect_stdout(output):
                        remote_cmd._cmd_provision(args, as_json=True)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["previous_provision_log"], {
                    "log_id": journal["log_id"], "status": "in_progress",
                    "updated_at": journal["updated_at"],
                })


class TestStartRemoteMcpServer(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_defaults_sandbox_home(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        entry = {"ssh": "ubuntu@1.2.3.4"}
        token = "a" * 64
        sr.start_remote_mcp_server(entry, "100.64.1.2", 9174, token)
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("sandbox-mcp-remote.service", cmd)
        self.assertIn("EnvironmentFile=%h/.sandbox/mcp-remote.env", cmd)
        self.assertIn("SANDBOX_REMOTE_MCP_TOKEN", cmd)
        self.assertIn("rollback()", cmd)
        self.assertIn("mcp-remote-backup", cmd)
        self.assertNotIn(token, cmd)
        self.assertEqual(mock_ssh_run.call_args.kwargs["input_data"], token + "\n")

    @patch("sandbox.core._remote.ssh_run")
    def test_migration_handoff_proves_only_the_legacy_pidfile_process(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.migrate_remote_mcp_service({"ssh": "ubuntu@1.2.3.4"}, "127.0.0.1", 9174, "c" * 64,
                                      "https://sandbox.example.test", confirm=True, legacy_pidfile=True)
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("/proc/$legacy_pid/cmdline", command)
        self.assertIn("/proc/$legacy_pid/cwd", command)
        self.assertIn("sb-src (deleted)", command)
        self.assertIn("kill \"$legacy_pid\"", command)
        self.assertNotIn("pathlib.Path('/proc')", command)
        self.assertIn("SANDBOX_REMOTE_MCP_TOKEN=\"$sandbox_remote_mcp_token\"", command)
        self.assertIn("./sb mcp-install", command)
        self.assertLess(command.index("./sb mcp-install"), command.index("kill \"$legacy_pid\""))
        self.assertEqual(mock_ssh_run.call_args.kwargs["timeout"], 300)

    @patch("sandbox.core._remote.ssh_run")
    def test_migration_refuses_to_replace_an_unproven_existing_unit(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=43)
        with self.assertRaisesRegex(RuntimeError, "ownership_unknown"):
            sr.migrate_remote_mcp_service({"ssh": "ubuntu@1.2.3.4"}, "127.0.0.1", 9174, "d" * 64,
                                          "https://sandbox.example.test", confirm=True)

    @patch("sandbox.core._remote.ssh_run")
    def test_migration_unit_preflight_requires_sandbox_marker_and_runtime_path(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        record = sr.remote_mcp_service_record("127.0.0.1", 9174, "https://sandbox.example.test")
        sr.migrate_remote_mcp_service({"ssh": "ubuntu@1.2.3.4"}, "127.0.0.1", 9174, "e" * 64,
                                      "https://sandbox.example.test", confirm=True)
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("WorkingDirectory=%h/sandbox/sb-src", command)
        self.assertIn(record["ownership_marker"], command)
        self.assertIn("exit 43", command)

    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_passes_public_url(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        entry = {"ssh": "ubuntu@1.2.3.4"}
        sr.start_remote_mcp_server(
            entry, "127.0.0.1", 9174, "b" * 64,
            public_url="https://sandbox.example.com",
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("--bind 127.0.0.1", cmd)
        self.assertIn("--public-url https://sandbox.example.com", cmd)
        self.assertIn("Restart=on-failure", cmd)
        self.assertIn("StartLimitBurst=5", cmd)
        self.assertIn("systemctl --user reset-failed sandbox-mcp-remote.service", cmd)
        self.assertIn("systemctl --user enable sandbox-mcp-remote.service", cmd)
        self.assertIn("systemctl --user restart sandbox-mcp-remote.service", cmd)

    @patch("sandbox.core._remote.ssh_run")
    def test_start_remote_mcp_server_timeout_is_redacted(self, mock_ssh_run):
        mock_ssh_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=30)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            sr.start_remote_mcp_server(
                {"ssh": "ubuntu@1.2.3.4"}, "127.0.0.1", 9174,
                "s" * 64, public_url="https://sandbox.example.com",
            )


class TestRemoteDoctorChecks(unittest.TestCase):
    def test_incomplete_remote_stops_before_network_access(self):
        checks = sr.remote_doctor_checks({})
        self.assertEqual(checks, [{
            "label": "SSH configured", "ok": False,
            "hint": "register it with `./sb remote add <name> <ssh-url>`",
        }])

    def test_authenticated_mcp_route_is_checked_without_exposing_token(self):
        class Response:
            status = 405
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False

        remote = {
            "ssh": "ubuntu@example.test", "provisioned": True,
            "control_transport": "https", "control_url": "https://control.example.test",
            "bearer_token": "never-print-this",
        }
        with patch.object(sr, "check_reachable", return_value=True), \
             patch("urllib.request.OpenerDirector.open", return_value=Response()) as urlopen:
            checks = sr.remote_doctor_checks(remote)
        endpoint = next(check for check in checks if check["label"] == "MCP endpoint reachable")
        self.assertTrue(endpoint["ok"])
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://control.example.test/mcp")
        self.assertNotIn("never-print-this", repr(checks))


class TestMcpHttpsTransportArguments(unittest.TestCase):
    def test_cmd_mcp_forwards_public_url_to_the_server(self):
        args = types.SimpleNamespace(
            transport="streamable-http", bind="127.0.0.1", port=9174,
            token="test-token", public_url="https://sandbox.example.com",
        )
        with patch("os.execv") as execv:
            integ_cmd.cmd_mcp(None, args)
        argv = execv.call_args.args[1]
        self.assertEqual(argv[-2:], ["--public-url", "https://sandbox.example.com"])

    def test_cmd_mcp_allows_remote_environment_token_without_argv(self):
        args = types.SimpleNamespace(
            transport="streamable-http", bind="127.0.0.1", port=9174,
            token=None, public_url=None, project_dir=None,
        )
        with patch.dict(os.environ, {"SANDBOX_REMOTE_MCP_TOKEN": "environment-secret"}), \
             patch("os.execv") as execv:
            integ_cmd.cmd_mcp(None, args)
        argv = execv.call_args.args[1]
        self.assertNotIn("--token", argv)
        self.assertNotIn("environment-secret", argv)


class TestConfigureHttpsProxy(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_configures_caddy_virtual_host(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.configure_https_proxy({"ssh": "ubuntu@1.2.3.4"}, "sandbox.example.com", 9174)
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("apt-get install -y caddy", cmd)
        self.assertIn("reverse_proxy 127.0.0.1:9174", cmd)
        self.assertIn("/etc/caddy/conf.d/sandbox-mcp-sandbox.example.com.caddy", cmd)
        self.assertIn("import /etc/caddy/conf.d/*.caddy", cmd)

    def test_rejects_non_hostname(self):
        with self.assertRaises(ValueError):
            sr.configure_https_proxy({"ssh": "ubuntu@1.2.3.4"}, "bad/host", 9174)

    @patch("sandbox.core._remote.ssh_run")
    def test_instance_route_bootstraps_caddy_like_control_proxy(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.configure_instance_https_route(
            {"ssh": "ubuntu@1.2.3.4"}, "default-demo.sandbox.asb.bd", 8188
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("apt-get install -y caddy", cmd)
        self.assertIn("import /etc/caddy/conf.d/*.caddy", cmd)
        self.assertIn("systemctl enable --now caddy", cmd)
        self.assertIn("reverse_proxy 127.0.0.1:8188", cmd)
        self.assertIn(
            "/etc/caddy/conf.d/sandbox-instance-default-demo.sandbox.asb.bd.caddy",
            cmd,
        )
        self.assertIn("/etc/caddy/conf.d/sandbox-host-*.caddy", cmd)
        self.assertIn("use sb host apply instead", cmd)
        self.assertLess(
            cmd.index("sandbox-host-*.caddy"),
            cmd.index("sandbox-instance-default-demo.sandbox.asb.bd.caddy"),
        )

    @patch("sandbox.core._remote.ssh_run")
    def test_public_routes_deny_crawlers_by_default(self, mock_ssh_run):
        # A preview host is a real public DNS record, usually handed out with an
        # autologin token in the URL — it must never reach a search index.
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.configure_instance_https_route(
            {"ssh": "ubuntu@1.2.3.4"}, "default-demo.sandbox.asb.bd", 8188
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("handle /robots.txt {", cmd)
        self.assertIn("Disallow: /", cmd)
        # The proxy sits inside its own handle so /robots.txt cannot also match it.
        self.assertLess(cmd.index("handle /robots.txt"),
                        cmd.index("reverse_proxy 127.0.0.1:8188"))

    def test_robots_allow_renders_a_bare_proxy(self):
        cmd = sr._caddy_proxy_command("demo.example.com", 8188, "sandbox-instance",
                                      robots="allow")
        self.assertNotIn("robots.txt", cmd)
        self.assertIn("reverse_proxy 127.0.0.1:8188", cmd)

    @patch("sandbox.core._remote.ssh_run")
    def test_instance_route_reports_permanent_host_ownership(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(
            returncode=65,
            stderr="hostname is managed by permanent Sandbox hosting; use sb host apply instead",
        )
        with self.assertRaisesRegex(RuntimeError, "managed by permanent Sandbox hosting"):
            sr.configure_instance_https_route(
                {"ssh": "ubuntu@1.2.3.4"}, "lenzora.dev", 8188
            )

    @patch("sandbox.core._remote.ssh_run")
    def test_removes_only_the_named_instance_route(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(returncode=0)
        sr.remove_instance_https_route(
            {"ssh": "ubuntu@1.2.3.4"}, "preview-demo.sandbox.asb.bd"
        )
        cmd = mock_ssh_run.call_args[0][1]
        self.assertIn("rm -f /etc/caddy/conf.d/sandbox-instance-preview-demo.sandbox.asb.bd.caddy", cmd)
        self.assertIn("caddy validate", cmd)


class TestRemotePreviewInstances(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_ensure_remote_instance_uses_new_label(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout='{"instance":"preview-a","wordpress_port":8123}\n')
        result = sr.ensure_remote_instance({"ssh": "ubuntu@1.2.3.4"}, "/srv/project", "preview-a")
        self.assertEqual(result["instance"], "preview-a")
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("ensure --local --project-dir /srv/project", command)
        self.assertIn("--label preview-a --create", command)
        self.assertIn("timeout --signal=TERM --kill-after=30s 300s", command)
        self.assertEqual(mock_ssh_run.call_args.kwargs["timeout"], 345)

    @patch("sandbox.core._remote.ssh_run")
    def test_reconcile_remote_instance_applies_deployed_config(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(
            stdout='progress\n{"instance":"preview-a","wordpress_port":8123}\n'
        )

        result = sr.reconcile_remote_instance(
            {"ssh": "ubuntu@1.2.3.4"}, "/srv/project", "preview-a"
        )

        self.assertEqual(result["instance"], "preview-a")
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("apply --project-dir /srv/project", command)
        self.assertIn("--label preview-a --json", command)
        self.assertEqual(mock_ssh_run.call_args.kwargs["timeout"], 345)

    @patch("sandbox.core._remote.ssh_run")
    def test_ensure_remote_instance_reports_timeout_once(self, mock_ssh_run):
        mock_ssh_run.side_effect = [
            _completed(stdout="/home/ubuntu/sandbox/sb\n"),
            _completed(returncode=124),
        ]
        with self.assertRaisesRegex(RuntimeError, "timed out after 300s"):
            sr.ensure_remote_instance({"ssh": "ubuntu@1.2.3.4"}, "/srv/project", "preview-a")
        self.assertEqual(mock_ssh_run.call_count, 2)

    @patch("sandbox.core._remote.delete_remote_instance")
    @patch("sandbox.core._remote.remote_sb_path", return_value="/srv/sandbox/sb")
    @patch("sandbox.core._remote.ssh_run")
    def test_partial_preview_cleanup_resolves_the_instance_by_label(self, ssh_run, _sb_path, delete):
        ssh_run.return_value = _completed(stdout=json.dumps({"ok": True, "instances": [
            {"name": "preview-a", "label": "preview-a"},
        ]}) + "\n")

        removed = sr.delete_remote_instance_for_label(
            {"ssh": "ubuntu@1.2.3.4"}, "/srv/project", "preview-a"
        )

        self.assertTrue(removed)
        self.assertIn("instances --project-dir /srv/project --json", ssh_run.call_args.args[1])
        delete.assert_called_once_with({"ssh": "ubuntu@1.2.3.4"}, "preview-a")

    @patch("sandbox.core._remote.ssh_run")
    def test_delete_remote_instance_is_scoped_to_name(self, mock_ssh_run):
        mock_ssh_run.side_effect = [_completed(stdout="/home/ubuntu/sandbox\n"), _completed(returncode=0)]
        sr.delete_remote_instance({"ssh": "ubuntu@1.2.3.4"}, "preview-a")
        self.assertIn("instance delete preview-a --yes", mock_ssh_run.call_args[0][1])


class TestStopRemoteMcpServer(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_stop_targets_only_the_proven_service_unit(self, mock_ssh_run):
        mock_ssh_run.side_effect = [
            _completed(stdout="enabled=enabled\nactive=active\npid=123\nlinger=yes\nownership=proven\npid_ownership=proven\nlistener=expected\nauth=ok\n"),
            _completed(returncode=0),
        ]
        remote = {"ssh": "ubuntu@1.2.3.4", "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174)}
        sr.stop_remote_mcp_server(remote)
        cmd = mock_ssh_run.call_args_list[1].args[1]
        self.assertEqual(cmd, "systemctl --user stop sandbox-mcp-remote.service")
        self.assertNotIn("/proc", cmd)
        self.assertNotIn("--token", cmd)

    @patch("sandbox.core._remote.ssh_run")
    def test_stop_refuses_unproven_ownership(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout="enabled=not-found\nactive=inactive\npid=0\nlinger=no\n")
        with self.assertRaisesRegex(RuntimeError, "ownership_unknown"):
            sr.stop_remote_mcp_server({"ssh": "ubuntu@1.2.3.4"})
        self.assertEqual(mock_ssh_run.call_count, 1)


class TestRemoteMcpServiceStatus(unittest.TestCase):
    def test_runtime_revision_sources_ignore_appledouble_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "VERSION").write_text("1.0\n")
            (root / "sb").write_text("#!/bin/sh\n")
            (root / "sandbox").mkdir()
            (root / "sandbox" / "good.py").write_text("value = 1\n")
            (root / "sandbox" / "._sidecar.py").write_bytes(b"resource-fork")
            relative = {
                source.relative_to(root).as_posix()
                for source in sr._remote_mcp_revision_sources(root)
            }
            self.assertIn("sandbox/good.py", relative)
            self.assertNotIn("sandbox/._sidecar.py", relative)

    def test_runtime_revision_covers_the_shipped_cli_and_mcp_surface(self):
        root = Path(sr.__file__).resolve().parents[2]
        relative = {
            source.relative_to(root).as_posix()
            for source in sr._remote_mcp_revision_sources(root)
        }
        self.assertIn("VERSION", relative)
        self.assertIn("sb", relative)
        self.assertIn("sandbox/commands/jobs_runtime.py", relative)
        self.assertIn("sandbox/workspaces/repository.py", relative)
        self.assertIn("mcp/wp-server/server.py", relative)
        self.assertFalse(any(".venv" in source for source in relative))

    @patch("sandbox.core._remote.ssh_run")
    def test_status_proves_unit_metadata_listener_and_authenticated_route(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(
            stdout="enabled=enabled\nactive=active\npid=7\nlinger=yes\nownership=proven\npid_ownership=proven\nlistener=expected\nauth=ok\nlegacy_pidfile=present\n")
        record = sr.remote_mcp_service_record("127.0.0.1", 9174, "https://sandbox.example.test")
        status = sr.remote_mcp_service_status({"ssh": "ubuntu@1.2.3.4", "mcp_service": record})
        self.assertEqual(status["ownership"], "proven")
        self.assertEqual(status["pid_ownership"], "proven")
        self.assertTrue(status["listener_expected"])
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["legacy_pidfile"], "present")
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("SANDBOX_REMOTE_MCP_RUNTIME_REVISION", command)
        self.assertEqual(len(record["runtime_revision"]), 24)
        self.assertIn("grep -Fq -- '--bind 127.0.0.1 --port 9174'", command)
        self.assertIn("ss -H -ltn", command)
        self.assertIn("ControlGroup", command)
        self.assertIn("/proc/$pid/cgroup", command)
        self.assertIn("urllib.request", command)
        self.assertIn("response.status in (200,204,400,405,406)", command)
        self.assertIn("exc.code in (200,204,400,405,406)", command)
        self.assertNotIn(". $HOME/.sandbox/mcp-remote.env", command)
        self.assertNotIn("bearer_token", command)

    @patch("sandbox.core._remote.ssh_run")
    def test_status_reports_matching_local_and_installed_runtime_revisions(self, mock_ssh_run):
        local_revision = sr._remote_mcp_runtime_revision()
        mock_ssh_run.return_value = _completed(
            stdout=f"enabled=enabled\nactive=active\nremote_revision={local_revision}\n"
        )
        status = sr.remote_mcp_service_status({
            "ssh": "registered-target",
            "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174),
        })
        self.assertEqual(status["local_runtime_revision"], local_revision)
        self.assertEqual(status["installed_runtime_revision"], local_revision)
        self.assertEqual(status["runtime_revision_state"], "match")
        self.assertNotIn("registered-target", json.dumps(status))

    @patch("sandbox.core._remote.ssh_run")
    def test_status_embedded_probe_extracts_hash_after_full_environment_prefix(self, mock_ssh_run):
        local_revision = sr._remote_mcp_runtime_revision()
        remote = {
            "ssh": "registered-target",
            "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174),
        }
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary) / "sandbox-mcp-remote.service"
            unit.write_text(
                "[Service]\n"
                f"Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION={local_revision}\n"
            )
            # Capture the exact embedded parser from the generated SSH command,
            # execute it against a realistic unit file, then feed its bounded
            # output through the normal status parser.
            mock_ssh_run.return_value = _completed()
            sr.remote_mcp_service_status(remote)
            command = mock_ssh_run.call_args.args[1]
            parser = shlex.split(command)[shlex.split(command).index("-c") + 1]
            probe = subprocess.run(
                [sys.executable, "-c", parser, str(unit)],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(probe.stdout.strip(), f"remote_revision={local_revision}")
            mock_ssh_run.return_value = _completed(
                stdout=f"enabled=enabled\nactive=active\n{probe.stdout}"
            )
            status = sr.remote_mcp_service_status(remote)
        self.assertEqual(status["installed_runtime_revision"], local_revision)
        self.assertEqual(status["runtime_revision_state"], "match")

    @patch("sandbox.core._remote.ssh_run")
    def test_status_reports_mismatching_runtime_revisions_without_reclassifying_ownership(self, mock_ssh_run):
        local_revision = "a" * 24
        installed_revision = "b" * 24
        mock_ssh_run.return_value = _completed(
            stdout=f"enabled=enabled\nactive=inactive\nownership=proven\nremote_revision={installed_revision}\n"
        )
        with patch.object(sr, "_remote_mcp_runtime_revision", return_value=local_revision):
            record = sr.remote_mcp_service_record("127.0.0.1", 9174)
            status = sr.remote_mcp_service_status({"ssh": "registered-target", "mcp_service": record})
        self.assertEqual(status["ownership"], "proven")
        self.assertEqual(status["local_runtime_revision"], local_revision)
        self.assertEqual(status["installed_runtime_revision"], installed_revision)
        self.assertEqual(status["runtime_revision_state"], "mismatch")

    @patch("sandbox.core._remote.ssh_run")
    def test_status_distinguishes_unavailable_and_malformed_runtime_revision_evidence(self, mock_ssh_run):
        remote = {"ssh": "registered-target", "mcp_service": {}}
        mock_ssh_run.return_value = _completed(stdout="enabled=not-found\nremote_revision=unavailable\n")
        unavailable = sr.remote_mcp_service_status(remote)
        self.assertIsNone(unavailable["installed_runtime_revision"])
        self.assertEqual(unavailable["runtime_revision_state"], "unavailable")

        mock_ssh_run.return_value = _completed(stdout="enabled=enabled\nremote_revision=unknown\n")
        unknown = sr.remote_mcp_service_status(remote)
        self.assertIsNone(unknown["installed_runtime_revision"])
        self.assertEqual(unknown["runtime_revision_state"], "unknown")

    @patch("sandbox.core._remote.ssh_run")
    def test_status_rejects_non_hash_remote_revision_values(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(
            stdout="enabled=enabled\nremote_revision=not-a-revision\n"
        )
        status = sr.remote_mcp_service_status({"ssh": "registered-target", "mcp_service": {}})
        self.assertIsNone(status["installed_runtime_revision"])
        self.assertEqual(status["runtime_revision_state"], "unknown")
        self.assertNotIn("not-a-revision", json.dumps(status))


class TestRemoteDockerPool(unittest.TestCase):
    def _run_transaction(self, root: Path, *, systemctl_ok: bool = True):
        binary = root / "bin"
        binary.mkdir()
        scripts = {
            "docker": '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[:2] == ["ps", "-q"]: print("container-one")
elif args[:2] == ["network", "ls"]: print("network-one")
elif args[:2] == ["network", "inspect"]:
    print(json.dumps([{"Id":"a" * 64,"Options":{"com.docker.network.bridge.name":"docker-test"}}]))
elif args and args[0] == "inspect": print("no")
raise SystemExit(0)
''',
            "ip": '''#!/usr/bin/env python3
print("[]")
''',
            "dockerd": '''#!/usr/bin/env python3
import json, sys
path = sys.argv[sys.argv.index("--config-file") + 1]
json.load(open(path))
''',
            "systemctl": "#!/usr/bin/env python3\nraise SystemExit(%d)\n" % (
                0 if systemctl_ok else 1),
        }
        for name, source in scripts.items():
            target = binary / name
            target.write_text(source)
            target.chmod(0o755)
        config = root / "daemon.json"
        config.write_text('{"log-driver":"json-file"}\n')
        config.chmod(0o600)
        source = sr._remote_docker_pool_program(confirm=True)
        source = source.replace(
            'pathlib.Path("/etc/docker/daemon.json")', f'pathlib.Path({str(config)!r})')
        source = source.replace(
            'pathlib.Path("/run/lock/sandbox-docker-pool.lock")',
            f'pathlib.Path({str(root / "transaction.lock")!r})')
        env = dict(os.environ)
        env["PATH"] = str(binary) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            [sys.executable, "-c", source], text=True, capture_output=True,
            timeout=15, env=env, check=False)
        return result, config

    def test_fixed_transaction_program_compiles_and_has_rollback_recovery(self):
        source = sr._remote_docker_pool_program(confirm=True)
        compile(source, "<remote-docker-pool>", "exec")
        self.assertIn('"172.16.0.0/12"', source)
        self.assertIn('"10.201.0.0/16"', source)
        self.assertIn('"10.202.0.0/16"', source)
        self.assertIn('"size": 24', source)
        self.assertIn("dockerd", source)
        self.assertIn("rollback_attempted", source)
        self.assertIn("docker\", \"start", source)

    @patch("sandbox.core._remote.ssh_run")
    def test_plan_uses_registered_remote_and_returns_bounded_counts(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "planned", "requires_confirm": True,
            "network_count": 31, "running_container_count": 74,
            "restart_policy_none_count": 3,
            "current_pools_configured": False, "current_pool_count": 0,
            "current_pools_digest": "sha256:" + "a" * 64,
            "desired_pools": list(sr.REMOTE_DOCKER_ADDRESS_POOLS),
            "subnet_capacity": 4608, "restart_required": True,
            "route_overlap_count": 0, "apply_safe": True,
        }))
        result = sr.remote_docker_pool(
            {"ssh": "registered-target"}, confirm=False)
        self.assertEqual(result["network_count"], 31)
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("sudo -n python3", command)
        self.assertNotIn("registered-target", command)

    @patch("sandbox.core._remote.ssh_run")
    def test_structured_apply_failure_is_preserved(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(
            returncode=3,
            stdout=json.dumps({
                "ok": False, "code": "docker_pool_apply_failed",
                "status": "failed", "message": "unsafe remote detail",
                "rollback_attempted": True, "rollback_succeeded": True,
                "containers_missing": 0,
            }),
        )
        result = sr.remote_docker_pool(
            {"ssh": "registered-target"}, confirm=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["rollback_attempted"])
        self.assertEqual(result["message"], "Docker pool update failed")

    @patch("sandbox.core._remote.ssh_run")
    def test_remote_response_rejects_unexpected_fields(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "planned", "secret": "must-not-pass",
        }))
        with self.assertRaisesRegex(RuntimeError, "unexpected fields"):
            sr.remote_docker_pool({"ssh": "registered-target"})

    @patch("sandbox.core._remote.ssh_run")
    def test_remote_response_rejects_false_complete_receipt(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "complete",
        }))
        with self.assertRaisesRegex(RuntimeError, "incomplete evidence"):
            sr.remote_docker_pool({"ssh": "registered-target"}, confirm=True)

    @patch("sandbox.core._remote.ssh_run")
    def test_recovery_plan_requires_exact_bounded_evidence(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "status": "recovery_planned", "requires_confirm": True,
            "recovery_candidate_count": 20, "recovery_window_seconds": 180,
            "recovery_expected_count": 72, "recovery_evidence_count": 72,
            "recovery_removed_count": 0,
        }))
        result = sr.remote_docker_pool(
            {"ssh": "registered-target"}, recover_interrupted=True,
            expected_running=72)
        self.assertEqual(result["recovery_candidate_count"], 20)
        decoded_command = mock_ssh_run.call_args.args[1]
        self.assertIn("sudo -n python3", decoded_command)

    def test_transaction_validates_then_applies_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, config = self._run_transaction(Path(temporary))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                json.loads(config.read_text())["default-address-pools"],
                list(sr.REMOTE_DOCKER_ADDRESS_POOLS),
            )
            self.assertNotIn("log-driver", payload)
            self.assertEqual(len(list(config.parent.glob("daemon.json.bak-*"))), 1)

    def test_failed_restart_atomically_restores_original_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, config = self._run_transaction(
                Path(temporary), systemctl_ok=False)
            self.assertEqual(result.returncode, 3, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["code"], "docker_pool_apply_failed")
            self.assertEqual(json.loads(config.read_text()), {"log-driver": "json-file"})
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)


class TestRemoteDomainInventory(unittest.TestCase):
    @patch("sandbox.core._remote.ssh_run")
    def test_inventory_is_bounded_and_secret_free(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "count": 1, "domains": [{
                "domain": "example.com", "owners": ["site-production"],
                "sources": ["instance_registry", "caddy_route"],
                "statuses": ["ready"],
            }],
        }))
        result = sr.remote_domain_inventory({
            "ssh": "registered-target", "sb_path": "/home/alim/sandbox/sb-src/sb",
        })
        self.assertEqual(result["domains"][0]["domain"], "example.com")
        command = mock_ssh_run.call_args.args[1]
        self.assertIn("sudo -n python3", command)
        self.assertNotIn("registered-target", command)

    @patch("sandbox.core._remote.ssh_run")
    def test_inventory_rejects_unknown_fields(self, mock_ssh_run):
        mock_ssh_run.return_value = _completed(stdout=json.dumps({
            "ok": True, "count": 0, "domains": [], "secret": "no",
        }))
        with self.assertRaisesRegex(RuntimeError, "invalid envelope"):
            sr.remote_domain_inventory({
                "ssh": "registered-target", "sb_path": "/home/alim/sandbox/sb-src/sb",
            })

    @patch("sandbox.core._remote.remote_mcp_service_status")
    @patch("sandbox.core._remote.check_reachable", return_value=True)
    @patch("urllib.request.build_opener")
    def test_doctor_includes_owned_service_recovery_evidence(self, opener, reachable, status):
        class Response:
            status = 405
            def __enter__(self): return self
            def __exit__(self, *_): return False
        opener.return_value.open.return_value = Response()
        status.return_value = {"ownership": "proven", "enabled": True, "active": True,
                               "linger": True, "listener_expected": True, "authenticated": True}
        checks = sr.remote_doctor_checks({
            "ssh": "ubuntu@example.test", "provisioned": True,
            "control_transport": "https", "control_url": "https://control.example.test",
            "bearer_token": "a" * 64,
            "mcp_service": sr.remote_mcp_service_record("127.0.0.1", 9174, "https://control.example.test"),
        })
        self.assertTrue(all(item["ok"] for item in checks))
        self.assertIn("MCP reboot recovery", [item["label"] for item in checks])


class TestRemoteServiceCommand(unittest.TestCase):
    def test_status_json_passes_through_secret_safe_revision_evidence(self):
        args = types.SimpleNamespace(name="status", ssh_url="myvps", confirm=False)
        status = {
            "local_runtime_revision": "a" * 24,
            "installed_runtime_revision": "a" * 24,
            "runtime_revision_state": "match",
        }
        with patch.object(remote_cmd.sr, "get_remote", return_value={
            "ssh": "ubuntu@1.2.3.4", "bearer_token": "secret-token",
        }), patch.object(remote_cmd.sr, "remote_mcp_service_status", return_value=status), \
                redirect_stdout(StringIO()) as output:
            remote_cmd._cmd_service(args, as_json=True)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["data"], status)
        self.assertNotIn("secret-token", output.getvalue())

    def test_diagnostics_rejects_ssh_before_remote_lookup(self):
        args = types.SimpleNamespace(name="diagnostics", ssh_url="myvps", confirm=False, ssh=True)
        with patch.object(remote_cmd.sr, "get_remote") as lookup, \
                redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit):
            remote_cmd._cmd_service(args, as_json=True)
        lookup.assert_not_called()
        self.assertIn("no longer supported", error.getvalue())

    def test_diagnostics_processes_dispatches_to_control_service(self):
        args = types.SimpleNamespace(name="diagnostics", ssh_url="myvps", confirm=False,
                                     ssh=False, processes=True)
        remote = {"control_url": "https://control.example.test"}
        with patch.object(remote_cmd.sr, "get_remote", return_value=remote), \
                patch.object(remote_cmd.sr, "remote_diagnostics", return_value={}) as probe, \
                redirect_stdout(StringIO()):
            remote_cmd._cmd_service(args, as_json=True)
        probe.assert_called_once_with(remote, include_processes=True)

    def test_tailscale_service_migration_omits_control_url_from_unit_identity(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True,
                              control_transport="tailscale", tailscale_host="100.64.1.2",
                              control_url="http://100.64.1.2:9174", bearer_token="a" * 64)
                args = types.SimpleNamespace(name="migrate", ssh_url="myvps", confirm=False, plan=False)
                with patch.object(remote_cmd.sr, "remote_mcp_service_status", return_value={"legacy_pidfile": "absent"}), \
                     patch.object(remote_cmd.sr, "migrate_remote_mcp_service", return_value={"status": "planned", "service": {}}) as migrate, \
                     patch("builtins.print"):
                    remote_cmd._cmd_service(args, as_json=True)
                self.assertIsNone(migrate.call_args.args[4])

    def test_service_migration_plan_records_read_only_legacy_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True,
                              control_transport="https", control_url="https://sandbox.example.test",
                              mcp_port=9174, bearer_token="a" * 64)
                args = MagicMock()
                args.name = "migrate"
                args.ssh_url = "myvps"
                args.confirm = False
                with patch.object(sr, "remote_mcp_service_status", return_value={"legacy_pidfile": "present"}) as status, \
                     patch.object(remote_cmd, "_upload_runtime_source") as upload, \
                     patch.object(sr, "ssh_run") as ssh_run, patch("builtins.print") as printed:
                    remote_cmd._cmd_service(args, as_json=True)
                ssh_run.assert_not_called()
                upload.assert_not_called()
                payload = json.loads(printed.call_args.args[0])
                self.assertEqual(payload["status"], "planned")
                self.assertTrue(payload["data"]["legacy_pidfile_detected"])
                self.assertNotIn("a" * 64, json.dumps(payload))

    def test_confirmed_service_migration_stages_runtime_before_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True,
                              control_transport="https", control_url="https://sandbox.example.test",
                              mcp_port=9174, bearer_token="a" * 64)
                args = MagicMock()
                args.name = "migrate"
                args.ssh_url = "myvps"
                args.confirm = True
                service = sr.remote_mcp_service_record("127.0.0.1", 9174, "https://sandbox.example.test")
                with patch.object(sr, "remote_mcp_service_status", return_value={"legacy_pidfile": "present"}), \
                     patch.object(remote_cmd, "_upload_runtime_source") as upload, \
                     patch.object(sr, "migrate_remote_mcp_service", return_value={"status": "applied", "service": service}) as migrate, \
                     patch("builtins.print"):
                    remote_cmd._cmd_service(args, as_json=True)
                upload.assert_called_once_with("ubuntu@1.2.3.4")
                self.assertTrue(migrate.call_args.kwargs["legacy_pidfile"])

    def test_down_without_confirmation_is_plan_only(self):
        args = MagicMock()
        args.name = "myvps"
        args.confirm = False
        with patch.object(sr, "get_remote", return_value={"ssh": "ubuntu@1.2.3.4"}), \
             patch.object(sr, "stop_remote_mcp_server") as stop, patch("builtins.print") as printed:
            remote_cmd._cmd_down(args, as_json=True)
        stop.assert_not_called()
        self.assertEqual(json.loads(printed.call_args.args[0])["status"], "planned")

    def test_confirmed_up_uses_the_verified_migration_path(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True,
                              control_transport="https", control_host="sandbox.example.test",
                              control_url="https://sandbox.example.test", mcp_port=9174,
                              bearer_token="a" * 64)
                args = MagicMock()
                args.name = "myvps"
                args.confirm = True
                service = sr.remote_mcp_service_record("127.0.0.1", 9174, "https://sandbox.example.test")
                with patch.object(remote_cmd, "_upload_runtime_source") as upload, \
                     patch.object(sr, "remote_mcp_service_status", return_value={"legacy_pidfile": "present"}), \
                     patch.object(sr, "configure_https_proxy") as proxy, \
                     patch.object(sr, "migrate_remote_mcp_service", return_value={"status": "applied", "service": service}) as migrate, \
                     patch("builtins.print"):
                    remote_cmd._cmd_up(args, as_json=True)
                upload.assert_called_once_with("ubuntu@1.2.3.4")
                proxy.assert_called_once()
                self.assertTrue(migrate.call_args.kwargs["confirm"])
                self.assertTrue(migrate.call_args.kwargs["legacy_pidfile"])


class TestDeployRequiresProvisionedRemote(unittest.TestCase):
    def test_deploy_to_unprovisioned_remote_dies(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=False)
                args = MagicMock()
                args.project_dir = d
                args.remote = "myvps"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                   return_value={"root": d, "slug": "proj"}):
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)

    def test_deploy_to_unregistered_remote_dies(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.project_dir = d
                args.remote = "does-not-exist"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                   return_value={"root": d, "slug": "proj"}):
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)

    def test_json_deploy_to_unregistered_remote_returns_json(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock()
                args.project_dir = d
                args.remote = "does-not-exist"
                args.json = True
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": d, "slug": "proj"}), \
                     patch("builtins.print") as mock_print:
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertFalse(result["ok"])
                self.assertEqual(result["remote"], "does-not-exist")
                self.assertIn("no remote named", result["error"])


class TestRejectHerdProjects(unittest.TestCase):
    def test_herd_configured_project_raises(self):
        with self.assertRaises(ValueError):
            sr.reject_herd_projects({"server": "herd"})

    def test_non_herd_project_is_fine(self):
        sr.reject_herd_projects({"server": "nginx"})  # does not raise
        sr.reject_herd_projects({})  # missing server key does not raise

    def test_deploy_to_herd_project_dies_before_touching_the_remote(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = d
                args.remote = "myvps"
                args.json = False
                sc = deploy_cmd._core()
                with patch.object(
                    sc, "load_project_config",
                    return_value={"root": d, "slug": "proj", "server": "herd"},
                ):
                    with patch("subprocess.run") as mock_run:
                        with self.assertRaises(SystemExit):
                            deploy_cmd.cmd_deploy(None, args)
                        mock_run.assert_not_called()


class TestDeployEnsureExpose(unittest.TestCase):
    def test_deploy_rejects_global_instance_selector_before_remote_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            args = types.SimpleNamespace(
                project_dir=d,
                remote="myvps",
                instance="wrong-instance",
                source_ref=None,
                json=True,
            )
            with patch.object(sr, "get_remote") as get_remote, \
                 patch.object(sr, "ensure_deploy_repo") as ensure_repo, \
                 patch("builtins.print") as mock_print, \
                 self.assertRaises(SystemExit) as raised:
                deploy_cmd.cmd_deploy(None, args)

            self.assertEqual(raised.exception.code, 1)
            payload = json.loads(mock_print.call_args.args[0])
            self.assertFalse(payload["ok"])
            self.assertIn("cannot target --instance", payload["error"])
            get_remote.assert_not_called()
            ensure_repo.assert_not_called()

    def test_remote_plugin_activation_targets_returned_instance_explicitly(self):
        remote = {"ssh": "ubuntu@example.test"}
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch.object(sr, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(sr, "remote_sb_path", return_value="/usr/local/bin/sb"), \
             patch.object(sr, "ssh_run", return_value=result) as run:
            sr.activate_remote_plugin(
                remote, "/srv/deploy/demo", "demo-default", "demo"
            )

        command = run.call_args.args[1]
        self.assertIn(
            "/usr/local/bin/sb --instance demo-default wp plugin activate demo",
            command,
        )
        self.assertIn("rm -f", command)
        self.assertNotIn("rm -rf", command)

    def test_remote_plugin_activation_refuses_an_unmounted_source(self):
        remote = {"ssh": "ubuntu@example.test"}
        missing = subprocess.CompletedProcess([], 42, stdout="", stderr="")
        with patch.object(sr, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(sr, "remote_sb_path", return_value="/usr/local/bin/sb"), \
             patch.object(sr, "ssh_run", return_value=missing) as run:
            with self.assertRaisesRegex(
                    RuntimeError, "instance=demo-default, target=/srv/deploy/demo"):
                sr.activate_remote_plugin(
                    remote, "/srv/deploy/demo", "demo-default", "demo"
                )
        self.assertEqual(run.call_count, 1)

    def test_default_instance_domain_uses_hyphenated_label_and_slug(self):
        self.assertEqual(
            sr.default_instance_domain("default", "templately.ai.builder"),
            "default-templately-ai-builder.sandbox.asb.bd",
        )
        self.assertEqual(
            sr.default_instance_domain("!!!", "!!!"),
            "default-project.sandbox.asb.bd",
        )

    def test_rewrite_instance_url_preserves_autologin_query(self):
        self.assertEqual(
            sr.rewrite_instance_url(
                "http://localhost:8188/?sandbox_autologin=abc123",
                "https://default-demo.sandbox.asb.bd",
            ),
            "https://default-demo.sandbox.asb.bd/?sandbox_autologin=abc123",
        )

    def test_deploy_can_ensure_activate_and_expose_remote_instance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            (root / "sandbox.config.json").write_text(
                '{"slug":"demo","plugins":{"demo":"."}}'
            )
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                inst = {
                    "instance": "demo",
                    "label": "default",
                    "wordpress_port": 8188,
                    "url": "http://localhost:8188",
                    "login_url": "http://localhost:8188/?sandbox_autologin=abc123",
                }
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": str(root), "slug": "demo"}), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0) as mock_overlay, \
                     patch.object(sr, "ensure_remote_instance", return_value=inst) as mock_ensure, \
                     patch.object(sr, "reconcile_remote_instance", return_value=inst) as mock_apply, \
                     patch.object(sr, "activate_remote_plugin") as mock_activate, \
                     patch.object(sr, "configure_instance_https_route") as mock_route, \
                     patch.object(sr, "instance_route_hosts", return_value=[]), \
                     patch.object(sr, "set_remote_instance_url") as mock_url, \
                     patch("builtins.print") as mock_print:
                    deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertTrue(result["ok"])
                self.assertEqual(result["url"], "https://default-demo.sandbox.asb.bd")
                self.assertEqual(result["instance"]["admin_url"],
                                 "https://default-demo.sandbox.asb.bd/wp-admin/")
                self.assertEqual(
                    result["instance"]["login_url"],
                    "https://default-demo.sandbox.asb.bd/?sandbox_autologin=abc123",
                )
                mock_ensure.assert_called_once_with(sr.get_remote("myvps"), "/remote/demo")
                mock_overlay.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo", root, "",
                    ["sandbox.config.json"],
                )
                mock_apply.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo"
                )
                mock_activate.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo", "demo", "demo"
                )
                mock_route.assert_called_once_with(
                    sr.get_remote("myvps"), "default-demo.sandbox.asb.bd", 8188
                )
                mock_url.assert_called_once_with(
                    sr.get_remote("myvps"), "/remote/demo",
                    "https://default-demo.sandbox.asb.bd"
                )

    def test_source_ref_still_transfers_ignored_primary_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            (root / "sandbox.config.json").write_text(
                '{"slug":"demo","plugins":{"demo":"."}}'
            )
            args = MagicMock(
                project_dir=str(root), remote="myvps", source_ref="v1.0.0",
                json=True, ensure=False, expose=False, pro_plugins=False,
            )
            sc = deploy_cmd._core()
            entry = {"ssh": "ubuntu@1.2.3.4", "provisioned": True}
            with patch.object(sc, "load_project_config", return_value={
                    "root": str(root), "slug": "demo", "kind": "wordpress"}), \
                 patch.object(sr, "get_remote", return_value=entry), \
                 patch.object(sr, "resolve_source_ref", return_value="a" * 40), \
                 patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                 patch.object(sr, "push_commits", return_value="a" * 40), \
                 patch.object(sr, "reset_target_to"), \
                 patch.object(sr, "apply_uncommitted", return_value=1) as overlay, \
                 patch("builtins.print"):
                deploy_cmd.cmd_deploy(None, args)
            overlay.assert_called_once_with(
                entry, "/remote/demo", root, "", ["sandbox.config.json"],
            )

    def _expose_with_aliases(self, *, alias_arg, project_aliases=None,
                             existing_routes=(), prune=False):
        """Run one `deploy --ensure --expose` and report what it routed."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            (root / "sandbox.config.json").write_text(
                '{"slug":"demo","plugins":{"demo":"."}}'
            )
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                args.alias = alias_arg
                args.prune_routes = prune
                args.pro_plugins = False
                inst = {"instance": "demo", "label": "default",
                        "wordpress_port": 8188, "url": "http://localhost:8188"}
                pconf = {"root": str(root), "slug": "demo"}
                if project_aliases is not None:
                    pconf["aliases"] = project_aliases
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config", return_value=pconf), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value=inst), \
                     patch.object(sr, "reconcile_remote_instance", return_value=inst), \
                     patch.object(sr, "activate_remote_plugin"), \
                     patch.object(sr, "configure_instance_https_route") as route, \
                     patch.object(sr, "instance_route_hosts",
                                  return_value=list(existing_routes)), \
                     patch.object(sr, "remove_instance_https_route") as remove, \
                     patch.object(sr, "set_remote_instance_url"), \
                     patch("builtins.print") as mock_print:
                    deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                routed = [c.args[1] for c in route.call_args_list]
                removed = [c.args[1] for c in remove.call_args_list]
                return result, routed, removed

    def test_expose_routes_each_declared_alias_to_the_same_port(self):
        result, routed, _ = self._expose_with_aliases(
            alias_arg=["cdn.example.com", "assets.example.com"])
        # The primary hostname is configured first: a failing alias must never
        # leave the instance unreachable on its own domain.
        self.assertEqual(routed, ["default-demo.sandbox.asb.bd",
                                  "cdn.example.com", "assets.example.com"])
        self.assertEqual(result["instance"]["alias_urls"],
                         ["https://cdn.example.com", "https://assets.example.com"])

    def test_expose_falls_back_to_the_project_declaration(self):
        _, routed, _ = self._expose_with_aliases(
            alias_arg=None, project_aliases=["cdn.example.com"])
        self.assertIn("cdn.example.com", routed)

    def test_an_explicit_alias_flag_overrides_the_project_declaration(self):
        _, routed, _ = self._expose_with_aliases(
            alias_arg=["only.example.com"], project_aliases=["cdn.example.com"])
        self.assertIn("only.example.com", routed)
        self.assertNotIn("cdn.example.com", routed)

    def test_stale_routes_are_reported_but_not_deleted_by_default(self):
        result, _, removed = self._expose_with_aliases(
            alias_arg=["cdn.example.com"],
            existing_routes=["default-demo.sandbox.asb.bd", "cdn.example.com",
                             "old-name.sandbox.asb.bd"])
        self.assertEqual(removed, [])
        self.assertEqual(result["instance"]["stale_routes"],
                         ["old-name.sandbox.asb.bd"])

    def test_prune_routes_deletes_only_the_undeclared_hostnames(self):
        result, _, removed = self._expose_with_aliases(
            alias_arg=["cdn.example.com"],
            existing_routes=["default-demo.sandbox.asb.bd", "cdn.example.com",
                             "old-name.sandbox.asb.bd"],
            prune=True)
        self.assertEqual(removed, ["old-name.sandbox.asb.bd"])
        self.assertEqual(result["instance"]["pruned_routes"],
                         ["old-name.sandbox.asb.bd"])
        self.assertEqual(result["instance"]["stale_routes"], [])

    def test_an_unreadable_route_inventory_does_not_fail_the_deploy(self):
        # The instance is already exposed and serving by then; losing the
        # inventory read is a reporting gap, not a deploy failure.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                args.alias = []
                args.prune_routes = False
                args.pro_plugins = False
                inst = {"instance": "demo", "label": "default",
                        "wordpress_port": 8188, "url": "http://localhost:8188"}
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": str(root), "slug": "demo"}), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value=inst), \
                     patch.object(sr, "reconcile_remote_instance", return_value=inst), \
                     patch.object(sr, "activate_remote_plugin"), \
                     patch.object(sr, "configure_instance_https_route"), \
                     patch.object(sr, "instance_route_hosts",
                                  side_effect=RuntimeError("ssh died")), \
                     patch.object(sr, "set_remote_instance_url"), \
                     patch("builtins.print") as mock_print:
                    deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://default-demo.sandbox.asb.bd")
        self.assertEqual(result["instance"]["stale_routes"], [])

    def test_malformed_ensure_result_returns_actionable_json_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock()
                args.project_dir = str(root)
                args.remote = "myvps"
                args.json = True
                args.ensure = True
                args.expose = True
                args.domain = "default-demo.sandbox.asb.bd"
                args.plugin_slug = "demo"
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config",
                                  return_value={"root": str(root), "slug": "demo"}), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value={"status": "ready"}), \
                     patch("builtins.print") as mock_print:
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)
                result = json.loads(mock_print.call_args[0][0])
                self.assertFalse(result["ok"])
                self.assertIn("remote ensure returned no 'instance'", result["error"])

    def test_generic_deploy_can_ensure_and_expose_without_wordpress_calls(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with _patched_config_local(root / "sandbox.local.yml"):
                sr.put_remote("myvps", ssh="ubuntu@1.2.3.4", provisioned=True)
                args = MagicMock(project_dir=str(root), remote="myvps", json=True,
                                 ensure=True, expose=True,
                                 domain="app.example.com", plugin_slug=None)
                instance = {"instance": "demo", "label": "default", "kind": "compose",
                            "http_port": 4321, "url": "http://127.0.0.1:4321"}
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config", return_value={
                    "root": str(root), "kind": "compose"}), \
                     patch("sandbox.commands.deploy.preflight_project_capability", return_value=None), \
                     patch.object(sr, "ensure_deploy_repo", return_value="/remote/demo"), \
                     patch.object(sr, "current_branch", return_value="main"), \
                     patch.object(sr, "push_commits", return_value="abc123"), \
                     patch.object(sr, "reset_target_to"), \
                     patch.object(sr, "capture_uncommitted", return_value=("", [])), \
                     patch.object(sr, "apply_uncommitted", return_value=0), \
                     patch.object(sr, "ensure_remote_instance", return_value=instance), \
                     patch.object(sr, "activate_remote_plugin") as activate, \
                     patch.object(sr, "set_remote_instance_url") as set_url, \
                     patch.object(sr, "configure_instance_https_route") as route, \
                     patch.object(sr, "instance_route_hosts", return_value=[]), \
                     patch("builtins.print") as printed:
                    deploy_cmd.cmd_deploy(None, args)
                result = json.loads(printed.call_args[0][0])
                self.assertTrue(result["ok"])
                self.assertEqual(result["url"], "https://app.example.com")
                self.assertEqual(result["instance"]["url"], "https://app.example.com")
                route.assert_called_once_with(sr.get_remote("myvps"), "app.example.com", 4321)
                activate.assert_not_called()
                set_url.assert_not_called()

    def test_generic_plugin_slug_is_rejected_before_remote_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            with _patched_config_local(Path(d) / "sandbox.local.yml"):
                args = MagicMock(project_dir=d, remote="myvps", json=True,
                                 ensure=True, expose=False, plugin_slug="nope")
                sc = deploy_cmd._core()
                with patch.object(sc, "load_project_config", return_value={
                    "root": d, "kind": "compose"}), \
                     patch("sandbox.commands.deploy.preflight_project_capability", return_value=None), \
                     patch.object(sr, "get_remote") as get_remote, \
                     patch("builtins.print") as printed:
                    with self.assertRaises(SystemExit):
                        deploy_cmd.cmd_deploy(None, args)
                result = json.loads(printed.call_args[0][0])
                self.assertIn("--plugin-slug", result["error"])
                get_remote.assert_not_called()


class TestRemoteDeployMcpWrapper(unittest.TestCase):
    def _load_tool_module(self):
        class _Mcp:
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator

        fake_app = types.ModuleType("app")
        fake_app.mcp = _Mcp()
        fake_app.SANDBOX_ROOT = ROOT
        fake_app._safe_json = json.loads
        fake_app._run_sandbox_json = lambda *_args, **_kwargs: None
        old_app = sys.modules.get("app")
        sys.modules["app"] = fake_app
        try:
            path = ROOT / "mcp" / "wp-server" / "tools" / "remote.py"
            spec = importlib.util.spec_from_file_location("remote_tool_under_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        finally:
            if old_app is None:
                sys.modules.pop("app", None)
            else:
                sys.modules["app"] = old_app

    def test_remote_deploy_defaults_to_ensure_and_expose(self):
        module = self._load_tool_module()
        payload = {
            "ok": True,
            "remote": "myvps",
            "pushed_commit": "abc123",
            "uncommitted_files_applied": 0,
            "instance": {"instance": "demo"},
            "url": "https://default-demo.sandbox.asb.bd",
            "error": None,
        }
        with patch.object(module, "_run_sandbox_json", return_value={
            "timed_out": False, "returncode": 0, "stdout": json.dumps(payload),
            "stderr": "", "payload": payload,
        }) as mock_run:
            result = module.remote_deploy("/tmp/project", "myvps")
        self.assertTrue(result["ok"])
        cmd = mock_run.call_args[0][0]
        self.assertIn("--ensure", cmd)
        self.assertIn("--expose", cmd)

    def test_remote_deploy_forwards_domain_and_plugin_slug(self):
        module = self._load_tool_module()
        with patch.object(module, "_run_sandbox_json", return_value={
            "timed_out": False, "returncode": 0, "stdout": "",
            "stderr": "", "payload": {"ok": True, "remote": "myvps"},
        }) as mock_run:
            module.remote_deploy(
                "/tmp/project", "myvps",
                domain="default-demo.sandbox.asb.bd",
                plugin_slug="demo",
            )
        cmd = mock_run.call_args[0][0]
        self.assertIn("--domain", cmd)
        self.assertIn("default-demo.sandbox.asb.bd", cmd)
        self.assertIn("--plugin-slug", cmd)
        self.assertIn("demo", cmd)

    def test_remote_deploy_uses_runtime_aware_mcp_preflight_when_available(self):
        module = self._load_tool_module()
        calls = []
        with patch.object(module, "_require_project_deployment_capability",
                          side_effect=lambda project_dir: calls.append(project_dir)), \
             patch.object(module, "_run_sandbox_json", return_value={
                 "timed_out": False, "returncode": 0, "stdout": "",
                 "stderr": "", "payload": {"ok": True, "remote": "myvps"},
             }):
            result = module.remote_deploy("/tmp/generic-project", "myvps")
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["/tmp/generic-project"])

    def test_remote_deploy_redacts_ssh_target_in_error(self):
        module = self._load_tool_module()
        with patch.object(module, "_run_sandbox_json", return_value={
            "timed_out": False, "returncode": 1, "stdout": "",
            "stderr": "ssh: ubuntu@1.2.3.4 refused", "payload": None,
        }):
            result = module.remote_deploy("/tmp/project", "myvps")
        self.assertNotIn("ubuntu@1.2.3.4", result["error"])
        self.assertIn("[redacted SSH target]", result["error"])

    def test_remote_deploy_timeout_keeps_contract_shape(self):
        module = self._load_tool_module()
        with patch.object(module, "_run_sandbox_json", return_value={
            "timed_out": True, "returncode": None, "stdout": "",
            "stderr": "", "payload": None,
        }):
            result = module.remote_deploy("/tmp/project", "myvps")
        self.assertFalse(result["ok"])
        self.assertEqual(result["remote"], "myvps")
        self.assertIn("timed out", result["error"])


if __name__ == "__main__":
    unittest.main()
