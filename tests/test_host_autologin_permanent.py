"""Compatibility and refusal coverage for opt-in hosted WordPress links."""
import base64
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from sandbox.commands import hosting as command
from sandbox.core import _hosting as hosting
from sandbox.services.redaction import redact_text, redact_structure
from tests.subprocess_support import run_test_process


TOKEN = "A" * 43
DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()
ORIGIN = "https://fixture.example.test"


def target():
    return {
        "project": "fixture", "environment": "development",
        "compose": {"service": "gateway", "files": ["compose.yml"]},
        "routes": [{"hostname": "fixture.example.test", "primary": True}],
        "autologin": {"user": "fixture-admin", "service": "wordpress",
                      "request_path": "/wp-login.php", "ttl_seconds": 600,
                      "container_path": "/var/www/html/wp-content/mu-plugins/99-autologin.php"},
    }


class PermanentIssuanceTests(unittest.TestCase):
    def test_cli_exposes_option_and_refuses_conflicting_or_wrong_action(self):
        root = Path(__file__).resolve().parent.parent
        cases = [(('--help',), 0, '--permanent'),
                 (('login-url', '--permanent', '--ttl-seconds', '600'), 2, 'not allowed with argument'),
                 (('plan', '--permanent'), 1, 'only available with host login-url')]
        with tempfile.TemporaryDirectory() as home:
            for arguments, code, text in cases:
                with self.subTest(arguments=arguments):
                    result = run_test_process([sys.executable, str(root / 'sb'), 'host', *arguments],
                                              env={'SANDBOX_HOME': home}, cwd=root,
                                              capture_output=True, text=True)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertIn(text, result.stdout + result.stderr)

    def issue(self, *, permanent=False, ttl=None, now=100000):
        state = {"hosts": {"remote/fixture/development": {}}}
        save, checked = Mock(), Mock()
        with patch.object(command.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
                patch.object(command, "_compose_prefix", return_value="fixture-compose"), \
                patch.object(command, "_remote_checked", checked), \
                patch.object(command.secrets, "token_urlsafe", return_value=TOKEN), \
                patch.object(command.time, "time", return_value=now):
            result = command._issue_host_autologin(target(), {}, "remote", state, ttl,
                                                   save_state=save, permanent=permanent)
        install = shlex.split(checked.call_args_list[0].args[1])
        plugin = base64.b64decode(install[2]).decode()
        save.assert_called_once_with(state)
        self.assertIn("exec -T wordpress", checked.call_args_list[0].args[1])
        self.assertEqual(checked.call_count, 2)
        return result, state, plugin

    def test_default_lifetime_output_and_renderer_are_preserved(self):
        for ttl, expected in ((None, 100600), (900, 100900)):
            with self.subTest(ttl=ttl):
                result, state, plugin = self.issue(ttl=ttl)
                self.assertEqual(result, {"url": f"{ORIGIN}/wp-login.php?sandbox_autologin={TOKEN}&expires={expected}",
                                          "expires_at": expected, "one_time": True, "user": "fixture-admin"})
                with patch.object(hosting.time, "time", return_value=100000):
                    self.assertEqual(plugin, hosting.render_autologin_mu_plugin(DIGEST, "fixture-admin", expected))
                self.assertEqual(state["hosts"]["remote/fixture/development"]["autologin"],
                                 {"user": "fixture-admin", "expires_at": expected})

    def test_permanent_ignores_default_ttl_without_retaining_key(self):
        for now in (100000, 100000 + 30 * 86400):
            with self.subTest(now=now):
                result, state, plugin = self.issue(permanent=True, now=now)
                self.assertEqual(result, {"url": f"{ORIGIN}/wp-login.php?sandbox_autologin=permanent#sandbox_autologin={TOKEN}",
                                          "expires_at": 0, "one_time": False, "user": "fixture-admin"})
                self.assertEqual(state["hosts"]["remote/fixture/development"]["autologin"],
                                 {"user": "fixture-admin", "expires_at": 0, "one_time": False})
                self.assertNotIn(TOKEN, json.dumps(state))
                self.assertNotIn(TOKEN, plugin)
                self.assertIn(DIGEST, plugin)
                parsed = urlsplit(result["url"])
                self.assertNotIn(TOKEN, parsed.path + "?" + parsed.query)
                self.assertNotIn(TOKEN, redact_text(result["url"]))
                self.assertNotIn(TOKEN, json.dumps(redact_structure(result)))

    def test_explicit_ttl_conflict_refuses_before_randomness_or_install(self):
        state = {"hosts": {"remote/fixture/development": {}}}
        with patch.object(command, "_remote_checked") as checked, \
                patch.object(command.secrets, "token_urlsafe") as random, \
                self.assertRaisesRegex(hosting.HostingError, "cannot be combined"):
            command._issue_host_autologin(target(), {}, "remote", state, 600, permanent=True)
        checked.assert_not_called()
        random.assert_not_called()
        self.assertEqual(state, {"hosts": {"remote/fixture/development": {}}})

    def test_missing_integration_or_deployment_has_no_effect(self):
        for config, state in (({}, {}), (target(), {"hosts": {}})):
            with self.subTest(config=bool(config)), patch.object(command, "_remote_checked") as checked, \
                    patch.object(command.secrets, "token_urlsafe") as random, \
                    self.assertRaises(hosting.HostingError):
                command._issue_host_autologin(config, {}, "remote", state, None, permanent=True)
            checked.assert_not_called()
            random.assert_not_called()

    def test_unsupported_or_unconfirmed_mode_never_acquires_writer(self):
        cases = [{"confirm": False}, {"ttl_seconds": 600}, {"service": "entry"},
                 {"email": "fixture@example.test"}, {"save_email": True}, {"list_links": True},
                 {"revoke": "fixture"}, {"uses": 0}, {"generic": True}]
        for case in cases:
            with self.subTest(case=case), ExitStack() as stack:
                config = target()
                if case.get("generic"):
                    config["login_service"] = {"entries": {"fixture": {}}}
                args = SimpleNamespace(action="login-url", permanent=True, confirm=True,
                                       remote="fixture-remote", project_dir=".", environment="development",
                                       image_action=None, json=True, ttl_seconds=None)
                for name, value in case.items():
                    setattr(args, name, value)
                stack.enter_context(patch.object(hosting, "validate_manifest", return_value=config))
                stack.enter_context(patch.object(command.remote, "get_remote", return_value={"provisioned": True}))
                stack.enter_context(patch.object(hosting, "load_host_state", return_value={}))
                writer = stack.enter_context(patch.object(command, "_with_host_writer_lock"))
                generic = stack.enter_context(patch.object(command, "_cmd_host_login_link"))
                stack.enter_context(redirect_stdout(io.StringIO()))
                stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit):
                    command.cmd_host({}, args)
                writer.assert_not_called()
                generic.assert_not_called()

    def test_other_actions_refuse_before_resolution(self):
        for action in ("apply", "plan", "share", "exec", "destroy"):
            with self.subTest(action=action), patch.object(hosting, "validate_manifest") as validate, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_host({}, SimpleNamespace(action=action, permanent=True))
            validate.assert_not_called()

    def test_confirmed_native_request_uses_existing_writer_and_output(self):
        args = SimpleNamespace(action="login-url", permanent=True, confirm=True,
                               remote="fixture-remote", project_dir=".", environment="development",
                               image_action=None, json=True, ttl_seconds=None)
        result = {"url": ORIGIN + "/wp-login.php?sandbox_autologin=permanent#sandbox_autologin=" + TOKEN,
                  "expires_at": 0, "one_time": False, "user": "fixture-admin"}
        output = io.StringIO()
        with patch.object(hosting, "validate_manifest", return_value=target()), \
                patch.object(command.remote, "get_remote", return_value={"provisioned": True}), \
                patch.object(hosting, "load_host_state", return_value={}), \
                patch.object(command, "_with_host_writer_lock", return_value=result) as writer, \
                redirect_stdout(output):
            command.cmd_host({}, args)
        writer.assert_called_once()
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, **result})

    def test_invalid_origin_and_digest_are_rejected(self):
        for origin in ("http://fixture.example.test", "https://fixture.example.test/", "https://evil@fixture.example.test", "null"):
            with self.subTest(origin=origin), self.assertRaises(hosting.HostingError):
                hosting.render_permanent_autologin_mu_plugin(DIGEST, "admin", origin)
        with self.assertRaises(hosting.HostingError):
            hosting.render_permanent_autologin_mu_plugin("bad", "admin", ORIGIN)


PHP_FIXTURE = r'''<?php
define('ABSPATH', '/fixture/');
$input = json_decode($argv[2], true);
$result = ['status' => 200, 'auth' => null, 'cookie' => null, 'redirect' => null, 'resolved_user' => null];
register_shutdown_function(function() { echo "\nFIXTURE_STATE:" . json_encode($GLOBALS['result']); });
function add_action($hook, $callback, $priority) { $GLOBALS['callback'] = $callback; }
function nocache_headers() {}
function status_header($code) { $GLOBALS['result']['status'] = $code; }
function wp_die($message) { exit; }
function is_ssl() { return $GLOBALS['input']['tls'] ?? true; }
function wp_unslash($value) { return $value; }
function esc_attr($value) { return htmlspecialchars($value, ENT_QUOTES); }
function get_user_by($field, $login) { $GLOBALS['result']['resolved_user'] = [$field, $login]; return ($GLOBALS['input']['user_exists'] ?? true) ? (object)['ID' => 42] : false; }
function add_site_option($key, $value) { return !($GLOBALS['input']['already_used'] ?? false); }
function delete_site_option($key) {}
function wp_set_current_user($id) { $GLOBALS['result']['auth'] = $id; }
function wp_set_auth_cookie($id, $remember, $secure) { $GLOBALS['result']['cookie'] = [$id, $remember, $secure]; }
function is_multisite() { return $GLOBALS['input']['multisite'] ?? false; }
function admin_url() { return 'https://fixture.example.test/wp-admin/'; }
function network_admin_url() { return 'https://fixture.example.test/wp-admin/network/'; }
function wp_safe_redirect($url) { $GLOBALS['result']['redirect'] = $url; }
$_GET = $input['get'] ?? ['sandbox_autologin' => 'permanent'];
$_POST = ['sandbox_autologin_token' => $input['token'] ?? str_repeat('A', 43)];
$_SERVER = ['REQUEST_METHOD' => $input['method'] ?? 'POST',
            'HTTP_HOST' => $input['host'] ?? 'fixture.example.test',
            'HTTP_ORIGIN' => $input['origin'] ?? 'https://fixture.example.test'];
if (file_exists($argv[1])) { require $argv[1]; ($GLOBALS['callback'])(); }
'''


@unittest.skipUnless(shutil.which("php"), "PHP is required for generated handler execution")
class PermanentPHPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plugin = Path(self.directory.name) / "plugin.php"
        self.runner = Path(self.directory.name) / "runner.php"
        self.runner.write_text(PHP_FIXTURE)
        self.plugin.write_text(hosting.render_permanent_autologin_mu_plugin(DIGEST, "fixture-admin", ORIGIN))

    def request(self, **values):
        proc = run_test_process(["php", str(self.runner), str(self.plugin), json.dumps(values)],
                                capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        body, result = proc.stdout.rsplit("\nFIXTURE_STATE:", 1)
        return body, json.loads(result)

    def test_get_and_head_never_authenticate(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                body, result = self.request(method=method)
                self.assertIsNone(result["auth"])
                self.assertIsNone(result["cookie"])
                if method == "GET":
                    self.assertIn("history.replaceState", body)
                    self.assertIn("form.method='POST'", body)
                    self.assertNotIn(TOKEN, body)
                else:
                    self.assertEqual(body, "")

    def test_same_key_authenticates_repeatedly_with_secure_normal_session(self):
        for _ in range(2):
            _, result = self.request()
            self.assertEqual(result["auth"], 42)
            self.assertEqual(result["resolved_user"], ["login", "fixture-admin"])
            self.assertEqual(result["cookie"], [42, False, True])
            self.assertEqual(result["redirect"], ORIGIN + "/wp-admin/")

    def test_multisite_uses_network_admin(self):
        _, result = self.request(multisite=True)
        self.assertEqual(result["redirect"], ORIGIN + "/wp-admin/network/")

    def test_invalid_requests_cannot_authenticate(self):
        cases = [dict(origin="null"), dict(origin=""), dict(origin="https://evil.test"),
                 dict(tls=False), dict(host="evil.test"), dict(user_exists=False),
                 dict(token="B" * 43), dict(token="short"), dict(token=[TOKEN]),
                 dict(method="PUT"), dict(get={"sandbox_autologin": ["permanent"]}),
                 dict(get={"sandbox_autologin": "other"})]
        for case in cases:
            with self.subTest(case=case):
                _, result = self.request(**case)
                self.assertEqual(result["status"], 403)
                self.assertIsNone(result["auth"])
                self.assertIsNone(result["cookie"])

    def test_replacement_and_explicit_removal_revoke_old_key(self):
        replacement = "B" * 43
        self.plugin.write_text(hosting.render_permanent_autologin_mu_plugin(
            hashlib.sha256(replacement.encode()).hexdigest(), "fixture-admin", ORIGIN))
        _, stale = self.request()
        self.assertEqual(stale["status"], 403)
        _, fresh = self.request(token=replacement)
        self.assertEqual(fresh["auth"], 42)
        self.plugin.unlink()
        _, removed = self.request(token=replacement)
        self.assertIsNone(removed["auth"])

    def test_unrelated_requests_return_without_auth(self):
        _, result = self.request(get={})
        self.assertIsNone(result["auth"])

    def test_default_renderer_still_authenticates_once_and_refuses_expiry(self):
        self.plugin.write_text(hosting.render_autologin_mu_plugin(DIGEST, "fixture-admin", int(time.time()) + 600))
        query = {"sandbox_autologin": TOKEN}
        _, first = self.request(get=query)
        self.assertEqual(first["auth"], 42)
        _, used = self.request(get=query, already_used=True)
        self.assertEqual(used["status"], 410)
        self.assertIsNone(used["auth"])
        with patch.object(hosting.time, "time", return_value=100):
            self.plugin.write_text(hosting.render_autologin_mu_plugin(DIGEST, "fixture-admin", 101))
        _, expired = self.request(get=query)
        self.assertEqual(expired["status"], 403)
        self.assertIsNone(expired["auth"])


if __name__ == "__main__":
    unittest.main()
