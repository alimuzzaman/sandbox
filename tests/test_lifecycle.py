"""Focused coverage for WordPress bootstrap lifecycle helpers."""
from pathlib import Path
import contextlib
import io
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.lifecycle as lifecycle  # noqa: E402
from sandbox.runtimes.base import OperationResult  # noqa: E402


class TestWordPressCoreDownload(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    def test_download_repairs_docroot_ownership_first(self, _is_herd, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args)

        compose.assert_called_once_with(
            "exec", "-T", "wp", "chown", "-R", "www-data:www-data",
            "/var/www/html", instance="preview-demo", check=True,
        )
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=True)
    def test_download_keeps_herd_on_its_host_path(self, _is_herd, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args)

        compose.assert_not_called()
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )


class TestMuPluginDirectoryPreparation(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    def test_docker_prepares_only_the_mu_plugin_directory(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo")

        compose.assert_called_once_with(
            "exec", "-T", "wp", "sh", "-c",
            "mkdir -p /var/www/html/wp-content/mu-plugins && "
            "chown -R www-data:www-data /var/www/html/wp-content/mu-plugins && "
            "chmod -R a+rwX /var/www/html/wp-content/mu-plugins",
            instance="preview-demo", check=True,
        )

    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=True)
    def test_herd_does_not_need_container_permission_repair(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo")

        compose.assert_not_called()


class TestHostRuntimeMuPluginLifecycle(unittest.TestCase):
    def test_shared_reconciler_writes_abilities_and_debug_plugins(self):
        import sandbox.core._provision as provision

        with patch.object(provision, "_write_abilities_muplugin") as abilities, \
                patch.object(provision, "_write_debug_muplugins") as debug:
            provision._write_host_runtime_muplugins("preview-demo")

        abilities.assert_called_once_with("preview-demo")
        debug.assert_called_once_with("preview-demo")

    def test_herd_up_reconciles_only_host_runtime_muplugins(self):
        args = SimpleNamespace(resolved_instance="preview-demo")
        runtime = {
            "server": "herd", "domain": "preview-demo.test",
            "wordpress_port": 8188,
        }
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                    registry_find_instance=lambda _instance: None)), \
                patch.object(lifecycle, "resolve_instances", return_value={
                    "preview-demo": runtime}), \
                patch.object(lifecycle, "wp_dir", return_value=Path(directory)), \
                patch.object(lifecycle, "_write_host_runtime_muplugins") as host_plugins, \
                patch.object(lifecycle, "_remove_obsolete_builder_authoring_assets"), \
                patch.object(lifecycle, "_write_mail_muplugin") as mail_plugin, \
                patch.object(lifecycle, "_write_dl_cache_muplugin") as cache_plugin, \
                patch.object(lifecycle, "_write_ondemand_muplugin") as ondemand_plugin, \
                patch.object(lifecycle, "_write_licensing_muplugin") as licensing_plugin, \
                patch.object(lifecycle, "compose") as compose:
            lifecycle.cmd_up({}, args)

        host_plugins.assert_called_once_with("preview-demo")
        compose.assert_not_called()
        mail_plugin.assert_not_called()
        cache_plugin.assert_not_called()
        ondemand_plugin.assert_not_called()
        licensing_plugin.assert_not_called()

    def test_install_reconciles_host_runtime_muplugins_after_core_download(self):
        events = []
        args = SimpleNamespace(resolved_instance="preview-demo")
        runtime = {
            "server": "herd", "domain": "preview-demo.test",
            "wordpress_port": 8188, "wp_version": None,
            "admin": {"user": "admin", "password": "admin",
                      "email": "admin@example.com", "site_title": "Sandbox"},
        }
        result = SimpleNamespace(returncode=1, stdout="")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(lifecycle, "preflight_instance_capability", return_value=None), \
                patch.object(lifecycle, "resolve_instances", return_value={
                    "preview-demo": runtime}), \
                patch.object(lifecycle, "_download_wordpress_core",
                             side_effect=lambda *_args: events.append("download")), \
                patch.object(lifecycle, "wpcli", return_value=result), \
                patch.object(lifecycle, "_prepare_mu_plugin_directory",
                             side_effect=lambda *_args: events.append("prepare")), \
                patch.object(lifecycle, "_write_host_runtime_muplugins",
                             side_effect=lambda *_args: events.append("host-plugins")), \
                patch.object(lifecycle, "_pin_wp_constants_in_config"), \
                patch.object(lifecycle, "_convert_multisite"), \
                patch.object(lifecycle, "wp_dir", return_value=Path(directory)), \
                patch.object(lifecycle, "_autologin_mu_plugin", return_value="<?php"), \
                patch.object(lifecycle, "save_local_autologin_token"), \
                patch.object(lifecycle, "_remove_obsolete_builder_authoring_assets"):
            lifecycle.cmd_install({}, args)

        self.assertLess(events.index("download"), events.index("prepare"))
        self.assertEqual(events.count("host-plugins"), 1)
        self.assertLess(events.index("prepare"), events.index("host-plugins"))


class TestDoctorJson(unittest.TestCase):
    def test_doctor_json_redacts_provider_token_in_preflight_error(self):
        from tests.redaction_corpus import GITHUB

        output = io.StringIO()
        error = SimpleNamespace(message=f"preflight failed {GITHUB}")
        with patch.object(lifecycle, "preflight_instance_capability",
                          return_value=error), \
                contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as raised:
            lifecycle.cmd_doctor({}, SimpleNamespace(
                resolved_instance="fixture", json=True))
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertNotIn(GITHUB, output.getvalue())
        self.assertIn("[REDACTED]", payload["checks"][0]["label"])

    def test_shared_extension_text_renderer_uses_canonical_report_fields(self):
        report = {
            "desired": {"profile": "wordpress@1",
                        "catalog": {"revision": 1, "digest": "sha256:" + "a" * 64},
                        "resolution_digest": "sha256:" + "b" * 64},
            "readiness": {"state": "blocked"},
            "staleness": {"state": "fresh"},
            "drift": {"state": "drift"},
            "observed": {plane: {"state": "ready", "php_version": "8.3.10"}
                         for plane in ("web", "cli", "exec", "phpunit")},
            "issues": [{"code": "version_mismatch",
                        "message": "PHP extension version does not match the requirement",
                        "extension": "gd"}],
        }
        output = lifecycle._render_php_extension_text(report)
        self.assertIn("Profile: wordpress@1", output)
        for plane in ("web", "cli", "exec", "phpunit"):
            self.assertIn(f"{plane}: ready", output)
        self.assertIn("version_mismatch [gd]", output)

    def test_doctor_json_is_one_document_and_emits_before_nonzero_exit(self):
        report = {
            "ok": False, "exit_code": 1,
            "desired": {"profile": "wordpress@1",
                        "catalog": {"revision": 1, "digest": "sha256:" + "a" * 64},
                        "requirements": [{"name": "gd", "state": "enabled", "version": None}],
                        "resolution_digest": "sha256:" + "b" * 64},
            "provenance": {"state": "unavailable"},
            "observed": {plane: {"state": "unavailable"} for plane in
                         ("web", "cli", "exec", "phpunit")},
            "readiness": {"state": "unavailable"},
            "staleness": {"state": "stale", "reason": "one_or_more_planes_unavailable"},
            "drift": {"state": "unknown"},
            "issues": [{"code": "plane_drift", "message": "safe message"}],
        }
        owner = {"root": "/tmp/project", "label": "default"}
        service = SimpleNamespace(invoke=lambda _request: OperationResult(
            True, "status", owner["root"], "wordpress", {"status": "ready"}))
        cfg = {"instances": {"fixture": {"app_password": ""}}}
        inst_cfg = {"admin": {}, "wordpress_port": 8188,
                    "php_extensions": {"extensions": {"gd": True}}}
        process = SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(row) for row in (
            {"Service": "wp", "State": "running"},
            {"Service": "db", "State": "running"},
            {"Service": "mailpit", "State": "running"},
        )), stderr="")
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / ".env.local"
            secret.write_text("fixture")
            secret.chmod(0o600)
            output = io.StringIO()
            with patch.object(lifecycle, "preflight_instance_capability", return_value=None), \
                    patch.object(lifecycle, "resolve_instances", return_value={"fixture": inst_cfg}), \
                    patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                        registry_find_instance=lambda _name: owner)), \
                    patch.object(lifecycle, "runtime_service", return_value=service), \
                    patch.object(lifecycle, "runtime_health_lines", return_value=[]), \
                    patch.object(lifecycle, "php_extension_status", return_value=report), \
                    patch.object(lifecycle, "compose", return_value=process), \
                    patch.object(lifecycle, "wpcli", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(lifecycle, "_probe_mcp_server", return_value=(True, "")), \
                    patch.object(lifecycle, "MCP_VENV", Path(directory)), \
                    patch.object(lifecycle, "focus_file", return_value=Path(directory) / "focus"), \
                    patch.object(lifecycle, "plugins_dir", return_value=Path(directory) / "plugins"), \
                    patch.object(lifecycle, "_project_declares_plugin_check", return_value=False), \
                    patch.object(lifecycle, "_local_yaml", return_value={
                        "defaults": {"github_org": "WPDevelopers"}}), \
                    patch.object(lifecycle, "SECRETS_ENV", secret), \
                    patch("sandbox.core._domains.proxy_health_checks", return_value=[]), \
                    patch("sandbox.core._remote.list_remotes", return_value={}), \
                    contextlib.redirect_stdout(output), \
                    self.assertRaises(SystemExit) as raised:
                lifecycle.cmd_doctor(cfg, SimpleNamespace(
                    resolved_instance="fixture", json=True))
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["php_extensions"], report)
        self.assertTrue(any(row["section"] == "PHP extensions" and not row["ok"]
                            for row in payload["checks"]))
