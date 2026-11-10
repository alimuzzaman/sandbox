"""Focused coverage for WordPress bootstrap lifecycle helpers."""
from pathlib import Path
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import types
import unittest
from unittest.mock import patch, call, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.lifecycle as lifecycle  # noqa: E402
import sandbox.core._instances as instances  # noqa: E402
from sandbox.runtimes.base import OperationResult  # noqa: E402
from tests.subprocess_support import run_test_process  # noqa: E402


def _run_install_capture_stdout(args):
    output = io.StringIO()
    with patch("secrets.token_hex", side_effect=[
            "install-login-sentinel", "snapshot-token-sentinel",
    ]), contextlib.redirect_stdout(output):
        lifecycle.cmd_install({}, args)
    return output.getvalue()


class TestWordPressCoreDownload(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_apache_download_waits_for_seed_and_repairs_runtime_ownership(
            self, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]
        compose.return_value.returncode = 0

        lifecycle._download_wordpress_core("preview-demo", args, "apache")

        self.assertEqual(compose.call_args_list, [
            call(
                "exec", "-T", "wp", "sh", "-c",
                "test -f /var/www/html/wp-includes/version.php && "
                "{ test -f /var/www/html/wp-includes/Requests/src/Requests.php "
                "|| test -f /var/www/html/wp-includes/Requests/Requests.php "
                "|| { test -f /var/www/html/wp-includes/class-requests.php "
                "&& grep -Eq \"^[$]wp_version = '([0-5]\\.[^']+|6\\.[01]([.-][^']*)?)';\" /var/www/html/wp-includes/version.php; }; }",
                instance="preview-demo", check=False, capture=True, timeout=5,
            ),
            call(
            "exec", "-T", "wp", "chown", "-R", "33:33", "/var/www/html",
            instance="preview-demo", check=True,
            ),
        ])
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )

    @patch("sandbox.commands.lifecycle.time.sleep")
    @patch("sandbox.commands.lifecycle.time.monotonic",
           side_effect=[0, 1, 31])
    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_download_fails_before_wpcli_when_image_seed_never_completes(
            self, compose, wpcli, _clock, _sleep):
        compose.return_value = SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(RuntimeError, "document root is still incomplete"):
            lifecycle._download_wordpress_core(
                "preview-demo", ["core", "download", "--force"], "nginx"
            )
        wpcli.assert_not_called()

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_litespeed_skips_image_seed_wait_and_repairs_runtime_ownership(
            self, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args, "litespeed")

        compose.assert_called_once_with(
            "exec", "-T", "wp", "chown", "-R", "1000:1000",
            "/var/www/vhosts/localhost/html",
            instance="preview-demo", check=True,
        )
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_download_keeps_herd_on_its_host_path(self, compose, wpcli):
        args = ["core", "download", "--force", "--version=7.0"]

        lifecycle._download_wordpress_core("preview-demo", args, "herd")

        compose.assert_not_called()
        wpcli.assert_called_once_with(
            args, instance="preview-demo", check=True
        )

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_bootstrap_readiness_refuses_an_incomplete_version_or_no_marker(
            self, compose, wpcli):
        compose.return_value = SimpleNamespace(returncode=0)
        lifecycle._download_wordpress_core("preview-demo", ["core", "download"], "apache")
        cmd_template = compose.call_args_list[0].args[5]
        shim = "wp-includes/class-requests.php"
        modern = "wp-includes/Requests/src/Requests.php"
        legacy = "wp-includes/Requests/Requests.php"
        cases = [
            # A version.php still being written: nothing says this core is old yet.
            (shim, "<?php\n$wp_version = '6.1."),
            (shim, "<?php\n$wp_version = '6.1"),
            (shim, "<?php\n$wp_version = '6.1.1'"),
            (shim, "<?php\n$wp_version = '5."),
            (shim, "<?php\n$wp_version = '5.';\n"),
            (shim, "<?php\n$wp_version = '"),
            # No version.php, whatever else is there.
            (modern, None),
            (legacy, None),
            (shim, None),
            # version.php alone: no Requests marker of any layout.
            (None, "<?php\n$wp_version = '6.1.1';\n"),
            (None, "<?php\n$wp_version = '6.4.3';\n"),
        ]
        for marker, content in cases:
            with self.subTest(marker=marker, content=content), \
                    tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "wp-includes").mkdir()
                if marker:
                    (root / marker).parent.mkdir(parents=True, exist_ok=True)
                    (root / marker).touch()
                if content is not None:
                    (root / "wp-includes" / "version.php").write_text(content)
                result = run_test_process(
                    ["sh", "-c", cmd_template.replace("/var/www/html", str(root))],
                    env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)

    @patch("sandbox.commands.lifecycle.wpcli")
    @patch("sandbox.commands.lifecycle.compose")
    def test_bootstrap_readiness_accepts_each_bundled_requests_layout(
            self, compose, wpcli):
        compose.return_value = SimpleNamespace(returncode=0)
        lifecycle._download_wordpress_core("preview-demo", ["core", "download"], "apache")
        cmd_template = compose.call_args_list[0].args[5]

        cases = [
            ("wp-includes/Requests/src/Requests.php", "6.4.3", True),
            ("wp-includes/Requests/Requests.php", "5.9", True),
            ("wp-includes/class-requests.php", "5.9.10", True),
            ("wp-includes/class-requests.php", "6.1.1", True),
            ("wp-includes/class-requests.php", "6.1-RC1", True),
            ("wp-includes/class-requests.php", "4.9", True),
            ("wp-includes/class-requests.php", "6.2", False),
            ("wp-includes/class-requests.php", "6.4.3", False),
            ("wp-includes/class-requests.php", "6.10.1", False),
            ("wp-includes/class-requests.php", "10.0", False),
            ("wp-includes/class-requests.php", "", False),
            ("wp-includes/class-requests.php", None, False),
        ]

        with tempfile.TemporaryDirectory() as td:
            temp_docroot = Path(td)
            cmd = cmd_template.replace("/var/www/html", str(temp_docroot))

            for layout, ver, should_pass in cases:
                with self.subTest(layout=layout, version=ver, should_pass=should_pass):
                    shutil.rmtree(temp_docroot)
                    temp_docroot.mkdir()
                    lfile = temp_docroot / layout
                    lfile.parent.mkdir(parents=True, exist_ok=True)
                    lfile.touch()

                    vfile = temp_docroot / "wp-includes" / "version.php"
                    vfile.parent.mkdir(parents=True, exist_ok=True)
                    if ver is not None:
                        if ver == "":
                            vfile.touch()
                        else:
                            vfile.write_text(f"<?php\n$wp_version = '{ver}';\n")

                    res = run_test_process(
                        ["sh", "-c", cmd],
                        env={"PATH": "/usr/bin:/bin"},
                        capture_output=True,
                    )
                    if should_pass:
                        self.assertEqual(res.returncode, 0, f"Expected {layout} with version {ver} to pass")
                    else:
                        self.assertNotEqual(res.returncode, 0, f"Expected {layout} with version {ver} to fail")

    def test_litespeed_install_downloads_core_then_generates_config(self):
        args = SimpleNamespace(resolved_instance="preview-demo")
        runtime = {
            "server": "litespeed", "domain": "preview-demo.test",
            "wordpress_port": 8188, "wp_version": None,
            "admin": {"user": "admin", "password": "admin",
                      "email": "admin@example.com", "site_title": "Sandbox"},
        }
        result = SimpleNamespace(returncode=1, stdout="")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(lifecycle, "preflight_instance_capability", return_value=None), \
                patch.object(lifecycle, "resolve_instances", return_value={
                    "preview-demo": runtime}), \
                patch.object(lifecycle, "compose") as compose, \
                patch.object(lifecycle, "wpcli", return_value=result) as wpcli, \
                patch.object(lifecycle, "_prepare_mu_plugin_directory"), \
                patch.object(lifecycle, "_write_host_runtime_muplugins"), \
                patch.object(lifecycle, "_ensure_litespeed_htaccess"), \
                patch.object(lifecycle, "_pin_wp_constants_in_config"), \
                patch.object(lifecycle, "_convert_multisite"), \
                patch.object(lifecycle, "wp_dir", return_value=Path(directory)), \
                patch.object(lifecycle, "_autologin_mu_plugin", return_value="<?php"), \
                patch.object(lifecycle, "save_local_autologin_token"), \
                patch.object(lifecycle, "_write_snapshot_muplugin"), \
                patch.object(lifecycle, "save_local_bridge_token"), \
                patch.object(lifecycle, "_write_mail_muplugin"), \
                patch.object(lifecycle, "_write_dl_cache_muplugin"), \
                patch.object(lifecycle, "_write_ondemand_muplugin"), \
                patch.object(lifecycle, "_write_licensing_muplugin"), \
                patch.object(lifecycle, "_remove_obsolete_builder_authoring_assets"):
            output = _run_install_capture_stdout(args)

        self.assertIn("Admin: http://localhost:8188/wp-admin", output)
        self.assertNotIn("install-login-sentinel", output)
        self.assertNotIn("sandbox_autologin=", output)

        compose.assert_called_once_with(
            "exec", "-T", "wp", "chown", "-R", "1000:1000",
            "/var/www/vhosts/localhost/html",
            instance="preview-demo", check=True,
        )
        core_download = call(
            ["core", "download", lifecycle.WORDPRESS_LATEST_DOWNLOAD_URL, "--force"],
            instance="preview-demo", check=True,
        )
        config_create = call([
            "config", "create", "--dbhost=db:3306", "--dbname=wp",
            "--dbuser=wp", "--dbpass=wp", "--skip-check", "--force",
        ], instance="preview-demo", check=False)
        self.assertIn(core_download, wpcli.call_args_list)
        self.assertIn(config_create, wpcli.call_args_list)
        self.assertLess(
            wpcli.call_args_list.index(core_download),
            wpcli.call_args_list.index(config_create),
        )


class TestApplyRollbackLifecycleState(unittest.TestCase):
    def test_rollback_preserves_a_previously_stopped_web_tier(self):
        with tempfile.TemporaryDirectory() as directory:
            compose_path = Path(directory) / "compose.yml"
            snapshot = {
                "local": {},
                "compose_path": compose_path,
                "compose_exists": False,
                "compose_bytes": None,
                "runtime": {"server": "nginx", "wordpress_port": 8188},
                "runtime_running": False,
            }
            stopped = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.object(instances, "_write_local_yaml"), \
                 patch.object(instances, "compose", return_value=stopped) as compose, \
                 patch.object(instances, "_wait_reachable") as wait:
                result = instances._restore_apply_rollback_state(snapshot, "demo")

        self.assertTrue(result["ok"])
        compose.assert_called_once_with(
            "stop", "wp", "nginx", instance="demo", check=False,
            capture=True, timeout=30,
        )
        wait.assert_not_called()


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
            instance="preview-demo", check=True, capture=False,
        )

    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    def test_json_setup_captures_compose_banner(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo", capture=True)

        self.assertTrue(compose.call_args.kwargs["capture"])

    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=True)
    def test_herd_does_not_need_container_permission_repair(self, _is_herd, compose):
        lifecycle._prepare_mu_plugin_directory("preview-demo")

        compose.assert_not_called()


class TestBoundedLogs(unittest.TestCase):
    @patch("sandbox.commands.lifecycle.compose")
    @patch("sandbox.commands.lifecycle._is_herd_instance", return_value=False)
    @patch("sandbox.commands.lifecycle._remote_lifecycle", return_value=None)
    @patch("sandbox.commands.lifecycle._core")
    def test_local_logs_are_bounded_and_follow_is_explicit(
            self, core, _remote_lifecycle, _is_herd, compose):
        core.return_value.registry_find_instance.return_value = None
        args = SimpleNamespace(
            resolved_instance="preview-demo", remote=None, project_dir=None,
            lines=75, since="2026-08-26T00:00:00Z", follow=False,
        )
        lifecycle.cmd_logs({}, args)
        compose.assert_called_once_with(
            "logs", "--no-color", "--tail=75", "--since=2026-08-26T00:00:00Z",
            "wp", "db", instance="preview-demo",
        )


class TestHostRuntimeMuPluginLifecycle(unittest.TestCase):
    def test_loopback_muplugin_prefers_internal_nginx_and_keeps_gateway_fallback(self):
        import sandbox.core._provision as provision

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(provision, "_ensure_muplugins_dir",
                             return_value=Path(directory)):
            provision._write_loopback_muplugin("preview-demo")

            rendered = (Path(directory) / "00-sandbox-loopback.php").read_text()
            self.assertIn("gethostbyname( 'nginx' )", rendered)
            self.assertIn("CURLOPT_URL, 'http://nginx' . $request_target", rendered)
            self.assertIn("CURLOPT_HTTPHEADER, $curl_headers", rendered)
            self.assertIn("'Host: ' . $host_header", rendered)
            self.assertIn("$request_target .= '?' . $dest['query'];", rendered)
            self.assertLess(rendered.index("CURLOPT_URL"),
                            rendered.index("gethostbyname( 'host.docker.internal' )"))
            self.assertIn("CURLOPT_RESOLVE", rendered)
            self.assertIn("gethostbyname( 'host.docker.internal' )", rendered)
            self.assertIn("$home_host !== $dest_host", rendered)
            self.assertNotIn("update_option", rendered)

    def test_loopback_muplugin_routes_clean_tst_self_fetches(self):
        import sandbox.core._provision as provision

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(provision, "_ensure_muplugins_dir",
                             return_value=Path(directory)):
            provision._write_loopback_muplugin("preview-demo")

            rendered = (Path(directory) / "00-sandbox-loopback.php").read_text()
        self.assertIn("preg_match( '/\\.tst$/i'", rendered)
        self.assertIn("$dest_host . ':' . $dest_port", rendered)
        self.assertIn("$home_host !== $dest_host", rendered)
        self.assertIn("$home_port !== $dest_port", rendered)

    def test_loopback_muplugin_intercepts_pre_http_request_and_routes_internally(self):
        import sandbox.core._provision as provision

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(provision, "_ensure_muplugins_dir",
                             return_value=Path(directory)):
            provision._write_loopback_muplugin("preview-demo")

            rendered = (Path(directory) / "00-sandbox-loopback.php").read_text()
        self.assertIn("pre_http_request", rendered)
        self.assertIn("_sandbox_loopback", rendered)
        self.assertIn("X-Forwarded-Proto", rendered)
        self.assertIn("X-Forwarded-Host", rendered)
        self.assertIn("$home_port !== $dest_port", rendered)
        self.assertIn("wp_remote_request", rendered)
        self.assertIn("$redirects_followed < $max_redirects", rendered)

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
        download_args = []
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
                             side_effect=lambda _instance, download, _server: (
                                 download_args.append(download), events.append("download")
                             )[-1]), \
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
        self.assertEqual(download_args, [[
            "core", "download", lifecycle.WORDPRESS_LATEST_DOWNLOAD_URL, "--force"
        ]])
        self.assertEqual(events.count("host-plugins"), 1)
        self.assertLess(events.index("prepare"), events.index("host-plugins"))


class TestUpPhpExtensionImages(unittest.TestCase):
    def test_up_prepares_child_images_before_compose_start(self):
        events = []
        runtime = {
            "server": "nginx", "php_extensions": {"extensions": {"gd": True}},
            "wordpress_port": 8188, "mailpit_port": 8025,
        }
        args = SimpleNamespace(resolved_instance="fixture", json=True)
        fake_core = SimpleNamespace(registry_find_instance=lambda _instance: None)
        # One ExitStack instead of a single 21-manager `with`: the python3.12 on
        # xspeed-dev rejects that as "too many statically nested blocks".
        with contextlib.ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(lifecycle, "_core", return_value=fake_core))
            stack.enter_context(patch.object(
                lifecycle, "resolve_instances", return_value={"fixture": runtime}))
            stack.enter_context(patch.object(
                lifecycle, "_web_services", return_value=("db", "wp", "nginx")))
            stack.enter_context(patch.object(
                lifecycle, "prepare_php_extension_runtime",
                side_effect=lambda config, server: events.append(("prepare", config, server))))
            stack.enter_context(patch.object(
                lifecycle, "_compose_up",
                side_effect=lambda *a, **k: events.append(("compose", a[0], a[1]))))
            compose = stack.enter_context(patch.object(lifecycle, "compose"))
            stack.enter_context(patch.object(
                lifecycle, "php_extension_status", return_value={"drift": {"state": "ready"}}))
            stack.enter_context(patch.object(lifecycle, "wp_dir", return_value=Path(directory)))
            stack.enter_context(patch.object(
                lifecycle, "_is_herd_instance", side_effect=[False, True]))
            for name in ("_write_mail_muplugin", "_write_loopback_muplugin",
                         "_write_dl_cache_muplugin", "_write_ondemand_muplugin",
                         "_write_host_runtime_muplugins", "_write_licensing_muplugin",
                         "_remove_obsolete_builder_authoring_assets"):
                stack.enter_context(patch.object(lifecycle, name))
            stack.enter_context(patch(
                "sandbox.core._provision.read_local_abilities_enabled", return_value=None))
            stack.enter_context(patch("sandbox.commands.jobs.prune_jobs"))
            stack.enter_context(patch.object(
                lifecycle, "site_url", return_value="http://fixture.test"))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            lifecycle.cmd_up({}, args)

        self.assertEqual(events[0], ("prepare", runtime, "nginx"))
        self.assertEqual(events[1], ("compose", "fixture", ("db", "wp", "nginx")))
        self.assertTrue(compose.call_args.kwargs["capture"])
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_up_fails_with_typed_json_before_compose_when_image_preparation_fails(self):
        runtime = {
            "server": "nginx", "php_extensions": {"extensions": {"gd": True}},
            "wordpress_port": 8188, "mailpit_port": 8025,
        }
        args = SimpleNamespace(resolved_instance="fixture", json=True)
        fake_core = SimpleNamespace(registry_find_instance=lambda _instance: None)
        with patch.object(lifecycle, "_core", return_value=fake_core), \
                patch.object(lifecycle, "resolve_instances", return_value={"fixture": runtime}), \
                patch.object(lifecycle, "prepare_php_extension_runtime",
                             side_effect=ValueError("child image unavailable")), \
                patch.object(lifecycle, "_compose_up") as compose_up, \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                self.assertRaises(SystemExit) as raised:
            lifecycle.cmd_up({}, args)

        self.assertEqual(raised.exception.code, 1)
        compose_up.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["mutated"])
        self.assertEqual(payload["runtime"], "wordpress")
        self.assertEqual(payload["error"]["code"], "php_extension_image_prepare_failed")


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

    def test_doctor_report_only_returns_zero_for_completed_failed_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            inst_cfg = {"admin": {}, "wordpress_port": 8188}
            with patch.object(lifecycle, "preflight_instance_capability", return_value=None), \
                    patch.object(lifecycle, "resolve_instances", return_value={"fixture": inst_cfg}), \
                    patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                        registry_find_instance=lambda _name: None)), \
                    patch.object(lifecycle, "compose", return_value=SimpleNamespace(
                        returncode=0, stdout="", stderr="")), \
                    patch.object(lifecycle, "wpcli", return_value=SimpleNamespace(
                        returncode=1, stdout="", stderr="")), \
                    patch.object(lifecycle, "php_extension_status", return_value=None), \
                    patch.object(lifecycle, "_probe_mcp_server", return_value=(False, "missing")), \
                    patch.object(lifecycle, "MCP_VENV", Path(directory) / "venv"), \
                    patch.object(lifecycle, "focus_file", return_value=Path(directory) / "focus"), \
                    patch.object(lifecycle, "plugins_dir", return_value=Path(directory) / "plugins"), \
                    patch.object(lifecycle, "_project_declares_plugin_check", return_value=False), \
                    patch.object(lifecycle, "_local_yaml", return_value={}), \
                    patch.object(lifecycle, "SECRETS_ENV", Path(directory) / "env"), \
                    patch("sandbox.core._domains.proxy_health_checks", return_value=[]), \
                    patch("sandbox.core._remote.list_remotes", return_value={}), \
                    patch.object(lifecycle, "_storage_pressure_doctor_checks", return_value=[]), \
                    contextlib.redirect_stdout(output):
                lifecycle.cmd_doctor(
                    {"instances": {"fixture": {}}},
                    SimpleNamespace(resolved_instance="fixture", json=True, report_only=True),
                )

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exit_code"], 1)

    def test_cmd_up_php_extension_drift_empty_issues(self):
        inst_cfg = {"php_extensions": {"profile": "wordpress@1"}, "server": "nginx"}
        cfg = {"instances": {"fixture": inst_cfg}}
        args = SimpleNamespace(resolved_instance="fixture", quiet=True, json=True)

        with patch.object(lifecycle, "resolve_instances", return_value={"fixture": inst_cfg}), \
                patch.object(lifecycle, "_web_services", return_value=["wp"]), \
                patch.object(lifecycle, "_compose_up"), \
                patch.object(lifecycle, "wp_is_installed", return_value=True), \
                patch.object(lifecycle, "php_extension_status", return_value={"drift": {"state": "drift", "issues": []}}), \
                self.assertRaises(SystemExit) as raised:
            lifecycle.cmd_up(cfg, args)
        self.assertEqual(raised.exception.code, 1)

    def test_cmd_up_php_extension_drift_ignored_when_wp_not_installed(self):
        inst_cfg = {
            "php_extensions": {"profile": "wordpress@1"},
            "server": "nginx",
            "wordpress_port": 8080,
            "mailpit_port": 8025,
        }
        cfg = {"instances": {"fixture": inst_cfg}}
        args = SimpleNamespace(resolved_instance="fixture", quiet=True, json=True)

        with patch.object(lifecycle, "resolve_instances", return_value={"fixture": inst_cfg}), \
                patch.object(lifecycle, "_web_services", return_value=["wp"]), \
                patch.object(lifecycle, "_compose_up"), \
                patch.object(lifecycle, "prepare_php_extension_runtime"), \
                patch.object(lifecycle, "wp_is_installed", return_value=False), \
                patch.object(lifecycle, "wp_dir", return_value=Path("/tmp/nonexistent-wp-dir")), \
                patch.object(lifecycle, "php_extension_status", return_value={"drift": {"state": "drift", "issues": []}}):
            # Must not raise SystemExit
            lifecycle.cmd_up(cfg, args)


if __name__ == "__main__":
    unittest.main()


class TestComposeUpAddressPools(unittest.TestCase):
    _POOL_ERR = "Error response from daemon: all predefined address pools have been fully subnetted"

    def _result(self, code, stderr=""):
        return types.SimpleNamespace(returncode=code, stdout="", stderr=stderr)

    def test_reclaims_unused_networks_and_retries_once(self):
        results = [self._result(1, self._POOL_ERR), self._result(0)]
        with patch.object(lifecycle, "compose", side_effect=lambda *a, **k: results.pop(0)) as compose, \
                patch.object(lifecycle, "_reclaim_empty_sandbox_networks",
                             return_value=["sandbox-old_default"]), \
                patch.object(lifecycle, "info"):
            lifecycle._compose_up("fixture", ["wp"], quiet=True)
        self.assertEqual(compose.call_count, 2)

    def test_reports_typed_error_when_nothing_to_reclaim(self):
        with patch.object(lifecycle, "compose", return_value=self._result(1, self._POOL_ERR)), \
                patch.object(lifecycle, "_reclaim_empty_sandbox_networks", return_value=[]), \
                patch.object(lifecycle, "die", side_effect=SystemExit(1)) as die:
            with self.assertRaises(SystemExit):
                lifecycle._compose_up("fixture", ["wp"])
        self.assertIn("docker_address_pools_exhausted", die.call_args[0][0])

    def test_reclaim_only_removes_sandbox_networks_without_containers(self):
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            if argv[1:3] == ["network", "ls"]:
                return types.SimpleNamespace(returncode=0, stdout="sandbox-a_default\nsandbox-b_default\nother\n")
            if argv[1] == "ps":
                used = "sandbox-b_default" in argv[-1]
                return types.SimpleNamespace(returncode=0, stdout="abc\n" if used else "")
            return types.SimpleNamespace(returncode=0, stdout="")
        with patch.object(lifecycle.subprocess, "run", side_effect=fake_run):
            removed = lifecycle._reclaim_empty_sandbox_networks()
        self.assertEqual(removed, ["sandbox-a_default"])
        self.assertNotIn(["docker", "network", "rm", "sandbox-b_default"], calls)
        self.assertNotIn(["docker", "network", "rm", "other"], calls)


class TestEnsureServerChangePolicy(unittest.TestCase):
    def test_ensure_after_down_undeclared_keeps_litespeed(self):
        import contextlib
        import sandbox_core as sc

        pconf = {
            "root": "/tmp/fake-project",
            "server": "nginx",  # default in pconf
            "server_declared": False,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "status": "stopped",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "litespeed",
        }
        cfg = {
            "instances": {
                "demo": {"server": "litespeed", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(patch.object(sc, "registry_get", return_value=existing))
            stack.enter_context(patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(patch.object(instances, "_secure_at_create", return_value=False))
            stack.enter_context(patch.object(instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(patch.object(instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            stack.enter_context(patch.object(instances, "_docker_preflight"))
            mock_build = stack.enter_context(patch.object(instances, "_build_instance_block", return_value={"server": "litespeed"}))
            stack.enter_context(patch.object(instances, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(patch.object(instances, "_write_local_yaml"))
            stack.enter_context(patch.object(instances, "load_config", return_value=cfg))
            stack.enter_context(patch.object(instances, "write_compose_files"))
            stack.enter_context(patch.object(lifecycle, "cmd_up"))
            stack.enter_context(patch.object(lifecycle, "cmd_install"))
            stack.enter_context(patch.object(instances, "_instance_web_services_running", return_value=(True, "")))
            stack.enter_context(patch.object(instances, "_auto_heal_wp_url"))
            stack.enter_context(patch.object(instances, "_wait_http", return_value=True))
            stack.enter_context(patch.object(instances, "_wait_reachable", return_value=True))
            stack.enter_context(patch.object(instances, "_wire_project_plugins"))
            stack.enter_context(patch.object(instances, "_wire_project_themes"))
            stack.enter_context(patch.object(instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(patch.object(instances, "_refresh_registered_url", return_value=existing))
            stack.enter_context(patch("sandbox.commands.data.capture_install_snapshots"))
            stack.enter_context(patch.object(sc, "registry_put", return_value=existing))
            instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertEqual(mock_build.call_args[0][5], "litespeed")

    def test_ensure_declared_server_difference_refuses_before_any_write(self):
        import contextlib
        import sandbox_core as sc

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        existing = {
            "instance": "demo",
            "label": "default",
            "status": "stopped",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
        }
        cfg = {
            "instances": {
                "demo": {"server": "nginx", "wordpress_port": 8088, "db_port": 3307, "mailpit_port": 8025},
            }
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(patch.object(sc, "registry_get", return_value=existing))
            stack.enter_context(patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(patch.object(instances, "_secure_at_create", return_value=False))
            stack.enter_context(patch.object(instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(patch.object(instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            stack.enter_context(patch.object(instances, "_docker_preflight"))
            mock_compose = stack.enter_context(patch.object(instances, "compose"))
            mock_write_local = stack.enter_context(patch.object(instances, "_write_local_yaml"))
            mock_registry_put = stack.enter_context(patch.object(sc, "registry_put"))
            with self.assertRaises(sc.ConfigError) as ctx:
                instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")

            err = ctx.exception
            self.assertEqual(getattr(err, "code", None), "apply_server_change_refused")
            msg = str(err)
            self.assertIn("apply_server_change_refused", msg)
            self.assertIn("nginx", msg)
            self.assertIn("litespeed", msg)
            self.assertIn("No state was changed", msg)
            mock_compose.assert_not_called()
            mock_write_local.assert_not_called()
            mock_registry_put.assert_not_called()

    def test_ensure_pending_row_and_brand_new_row_are_unchanged(self):
        import contextlib
        import sandbox_core as sc

        pconf = {
            "root": "/tmp/fake-project",
            "server": "litespeed",
            "server_declared": True,
        }
        cfg = {"instances": {"fake-project": {"server": "litespeed"}, "demo": {"server": "litespeed"}}}

        # 1. Brand-new row (existing is None)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(patch.object(sc, "registry_get", return_value=None))
            stack.enter_context(patch.object(sc, "registry_list_for_root", return_value=[]))
            stack.enter_context(patch.object(sc, "registry_all", return_value={}))
            stack.enter_context(patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(instances, "_derive_instance_name", return_value="fake-project"))
            stack.enter_context(patch.object(instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(patch.object(instances, "_secure_at_create", return_value=False))
            stack.enter_context(patch.object(instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(patch.object(instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            stack.enter_context(patch.object(instances, "_docker_preflight"))
            mock_build = stack.enter_context(patch.object(instances, "_build_instance_block", return_value={"server": "litespeed"}))
            stack.enter_context(patch.object(instances, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(patch.object(instances, "_write_local_yaml"))
            stack.enter_context(patch.object(instances, "load_config", return_value=cfg))
            stack.enter_context(patch.object(instances, "write_compose_files"))
            stack.enter_context(patch.object(lifecycle, "cmd_up"))
            stack.enter_context(patch.object(lifecycle, "cmd_install"))
            stack.enter_context(patch.object(instances, "_instance_web_services_running", return_value=(True, "")))
            stack.enter_context(patch.object(instances, "_auto_heal_wp_url"))
            stack.enter_context(patch.object(instances, "_wait_http", return_value=True))
            stack.enter_context(patch.object(instances, "_wait_reachable", return_value=True))
            stack.enter_context(patch.object(instances, "_wire_project_plugins"))
            stack.enter_context(patch.object(instances, "_wire_project_themes"))
            stack.enter_context(patch.object(instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(patch.object(instances, "_refresh_registered_url", return_value={"instance": "fake-project"}))
            stack.enter_context(patch("sandbox.commands.data.capture_install_snapshots"))
            stack.enter_context(patch.object(sc, "registry_put", return_value={"instance": "fake-project"}))
            instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertEqual(mock_build.call_args[0][5], "litespeed")

        # 2. Pending row (existing has status='pending', server='nginx')
        pending_existing = {
            "instance": "demo",
            "label": "default",
            "status": "pending",
            "wordpress_port": 8088,
            "db_port": 3307,
            "mailpit_port": 8025,
            "server": "nginx",
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sc, "load_project_config", return_value=pconf))
            stack.enter_context(patch.object(sc, "registry_get", return_value=pending_existing))
            stack.enter_context(patch.object(sc, "project_lock", return_value=contextlib.nullcontext()))
            stack.enter_context(patch.object(instances, "_derive_instance_name", return_value="demo"))
            stack.enter_context(patch.object(instances, "_proxy_sudoers_installed", return_value=False))
            stack.enter_context(patch.object(instances, "_secure_at_create", return_value=False))
            stack.enter_context(patch.object(instances, "docker_daemon_preflight", return_value={"ok": True}))
            stack.enter_context(patch.object(instances, "_resolve_port_conflicts", side_effect=lambda c, **kw: c))
            stack.enter_context(patch.object(instances, "_docker_preflight"))
            mock_build_pending = stack.enter_context(patch.object(instances, "_build_instance_block", return_value={"server": "litespeed"}))
            stack.enter_context(patch.object(instances, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(patch.object(instances, "_write_local_yaml"))
            stack.enter_context(patch.object(instances, "load_config", return_value=cfg))
            stack.enter_context(patch.object(instances, "write_compose_files"))
            stack.enter_context(patch.object(lifecycle, "cmd_up"))
            stack.enter_context(patch.object(lifecycle, "cmd_install"))
            stack.enter_context(patch.object(instances, "_instance_web_services_running", return_value=(True, "")))
            stack.enter_context(patch.object(instances, "_auto_heal_wp_url"))
            stack.enter_context(patch.object(instances, "_wait_http", return_value=True))
            stack.enter_context(patch.object(instances, "_wait_reachable", return_value=True))
            stack.enter_context(patch.object(instances, "_wire_project_plugins"))
            stack.enter_context(patch.object(instances, "_wire_project_themes"))
            stack.enter_context(patch.object(instances, "site_url", return_value="http://localhost:8088"))
            stack.enter_context(patch.object(instances, "_refresh_registered_url", return_value=pending_existing))
            stack.enter_context(patch("sandbox.commands.data.capture_install_snapshots"))
            stack.enter_context(patch.object(sc, "registry_put", return_value=pending_existing))
            instances._ensure_instance_impl(cfg, "/tmp/fake-project", "default")
            self.assertEqual(mock_build_pending.call_args[0][5], "litespeed")
