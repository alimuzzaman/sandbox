"""Offline coverage for managed Compose hosting and Cloudflare intent."""
import json
import hashlib
import io
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._cloudflare as cloudflare  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
import sandbox.core._secrets as personal_secrets  # noqa: E402
import sandbox.commands.preview as preview  # noqa: E402
import sandbox.commands.hosting as hosting_cmd  # noqa: E402


def _manifest(aliases=None):
    return """version: 1
project: example-site
environments:
  production:
    compose:
      files: [compose.yml]
      service: web
      container_port: 8080
    healthcheck:
      path: /
      statuses: [200, 399]
    deploy:
      allowed_branches: [main]
      require_clean: true
    host:
      primary: আমারসোনার.বাংলা
      aliases:
%s
    cloudflare:
      proxied: true
      tls: origin-ca
      ssl_mode: strict
""" % (aliases if aliases is not None else "        - hostname: '*.আমারসোনার.বাংলা'\n          mode: serve\n        - hostname: asb.bd\n          mode: redirect\n          target: https://আমারসোনার.বাংলা")


class TestHostingManifest(unittest.TestCase):
    def _write(self, content):
        directory = tempfile.TemporaryDirectory()
        Path(directory.name, "sandbox.hosting.yml").write_text(content)
        Path(directory.name, "compose.yml").write_text("services: {}\n")
        return directory

    def test_normalizes_idn_and_wildcard_routes(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        hosts = [route["hostname"] for route in result["routes"]]
        self.assertEqual(hosts[0], "xn--94b2eraib0c0bd9i.xn--54b7fta0cc")
        self.assertIn("*.xn--94b2eraib0c0bd9i.xn--54b7fta0cc", hosts)

    def test_rejects_duplicate_alias(self):
        aliases = "        - hostname: আমারসোনার.বাংলা\n          mode: serve"
        with self._write(_manifest(aliases)) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "duplicate"):
                hosting.validate_manifest(directory)

    def test_requires_https_redirect_target(self):
        aliases = "        - hostname: asb.bd\n          mode: redirect\n          target: http://example.test"
        with self._write(_manifest(aliases)) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "https target"):
                hosting.validate_manifest(directory)

    def test_rejects_missing_compose_file(self):
        with self._write(_manifest()) as directory:
            Path(directory, "compose.yml").unlink()
            with self.assertRaisesRegex(hosting.HostingError, "does not exist"):
                hosting.validate_manifest(directory)

    def test_renders_caddy_serve_and_redirect_routes(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem")
        self.assertIn("reverse_proxy 127.0.0.1:18001", rendered)
        self.assertIn("redir https://আমারসোনার.বাংলা{uri} 308", rendered)
        self.assertIn("tls /cert.pem /key.pem", rendered)

    def test_renders_declared_basic_auth_without_plaintext_password(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem", "$2a$hash")
        self.assertIn("basicauth {", rendered)
        self.assertIn("lnzr_dev $2a$hash", rendered)
        self.assertNotIn("BASIC_AUTH_PASSWORD", rendered)
        self.assertNotIn("plain-password", rendered)

    def test_requires_hash_when_rendering_declared_basic_auth(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        with self.assertRaisesRegex(hosting.HostingError, "requires a generated Caddy password hash"):
            hosting.caddyfile(result, 18001)

    def test_validates_basic_auth_secret_reference(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        self.assertEqual(result["basic_auth"]["password_secret"], "BASIC_AUTH_PASSWORD")

    def test_rejects_basic_auth_username_with_shell_syntax(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: 'bad user'\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "basic_auth.username"):
                hosting.validate_manifest(directory)

    def test_plan_never_adds_undeclared_hosts(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        plan = hosting.desired_plan(result, "203.0.113.10")
        self.assertEqual(len(plan["records"]), len(result["routes"]))
        self.assertTrue(all(r["address"] == "203.0.113.10" for r in plan["records"]))

    def test_runtime_is_namespaced_and_uses_loopback_only(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        self.assertEqual(runtime["compose_project"], "sandbox-host-example-site-production")
        self.assertIn('127.0.0.1:', runtime["compose_override"])
        self.assertIn(f"reverse_proxy 127.0.0.1:{runtime['loopback_port']}", runtime["caddyfile"])

    def test_runtime_plan_reports_basic_auth_without_a_hash(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        self.assertTrue(runtime["basic_auth_enabled"])
        self.assertNotIn("BASIC_AUTH_PASSWORD", runtime["caddyfile"])
        self.assertNotIn("basicauth", runtime["caddyfile"])

    def test_accepts_opt_in_hosted_wordpress_autologin(self):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "    cloudflare:\n", "    autologin:\n      user: admin\n      container_path: /var/www/html/wp-content/mu-plugins/99-autologin.php\n      ttl_seconds: 600\n    cloudflare:\n"
            ))
            validated = hosting.validate_manifest(directory)
        self.assertEqual(validated["autologin"]["user"], "admin")
        self.assertEqual(validated["autologin"]["ttl_seconds"], 600)

    def test_rejects_unsafe_autologin_container_path(self):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "    cloudflare:\n", "    autologin:\n      user: admin\n      container_path: ../wp-config.php\n    cloudflare:\n"
            ))
            with self.assertRaisesRegex(hosting.HostingError, "container_path"):
                hosting.validate_manifest(directory)

    def test_autologin_plugin_has_hash_expiry_and_single_use_guard(self):
        token = "not-written-to-the-server"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        plugin = hosting.render_autologin_mu_plugin(token_hash, "admin", int(time.time()) + 600)
        self.assertIn(token_hash, plugin)
        self.assertNotIn(token, plugin)
        self.assertIn("add_site_option", plugin)
        self.assertIn("SANDBOX_HOST_AUTOLOGIN_EXPIRES_AT", plugin)

    def test_existing_host_keeps_its_allocated_loopback_port(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        key = hosting.state_key("myvps", validated)
        state = {"version": 1, "hosts": {key: {"loopback_port": 18765}}}
        self.assertEqual(hosting.desired_runtime(validated, "myvps", state)["loopback_port"], 18765)

    def test_compose_command_is_namespaced_and_only_starts_declared_services(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        command = hosting.render_compose_command(
            validated, "/srv/example", "/srv/example/.sandbox-hosting.yml"
        )
        self.assertIn("docker compose -p sandbox-host-example-site-production", command)
        self.assertIn("--remove-orphans", command)
        self.assertTrue(command.endswith("web"))

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_init_service_is_built_before_its_one_shot_run(self, remote_checked, _write):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080", "container_port: 8080\n      init_services: [setup]"
            ))
            validated = hosting.validate_manifest(directory)
        runtime = {
            "compose_override": "services: {}\n",
            "environment": "EXAMPLE=value\n",
        }
        hosting_cmd._run_compose({}, validated, "/srv/example", "/srv/runtime", runtime)
        commands = [call.args[1] for call in remote_checked.call_args_list]
        self.assertIn("--force-recreate", commands[0])
        self.assertIn("--renew-anon-volumes", commands[0])
        build_index = next(i for i, command in enumerate(commands) if command.endswith("build setup"))
        run_index = next(i for i, command in enumerate(commands) if command.endswith("run --rm setup"))
        self.assertLess(build_index, run_index)

    def test_state_round_trip_is_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosts.json"
            state = {"version": 1, "hosts": {"myvps/example/production": {"loopback_port": 18001}}}
            hosting.save_host_state(state, path)
            self.assertEqual(hosting.load_host_state(path), state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_apply_calls_rollback(self):
        events = []

        def apply():
            events.append("apply")
            raise RuntimeError("remote rejected Caddy config")

        with self.assertRaisesRegex(RuntimeError, "remote rejected"):
            hosting.apply_with_rollback(apply, lambda: events.append("rollback"))
        self.assertEqual(events, ["apply", "rollback"])

    @patch("sandbox.commands.hosting.time.sleep")
    @patch("sandbox.commands.hosting.urllib.request.build_opener")
    def test_edge_verification_accepts_basic_auth_challenge_for_served_route(self, build_opener, _sleep):
        response = urllib.error.HTTPError(
            "https://example.test/", 401, "Unauthorized", {}, io.BytesIO(),
        )
        build_opener.return_value.open.side_effect = response

        try:
            hosting_cmd._verify_edge(
                [{"hostname": "example.test", "mode": "serve"}],
                basic_auth_enabled=True,
            )
        finally:
            response.close()

        self.assertEqual(build_opener.return_value.open.call_count, 1)


class _Response:
    def __init__(self, data):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestCloudflareClient(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_upsert_uses_existing_matching_record_only(self, mock_open):
        mock_open.side_effect = [
            _Response({"success": True, "result": [{"id": "rec-1", "type": "A"}]}),
            _Response({"success": True, "result": {"id": "rec-1"}}),
        ]
        client = cloudflare.Client("token")
        record = client.upsert_address("zone", "example.com", "203.0.113.10")
        self.assertEqual(record["id"], "rec-1")
        request = mock_open.call_args_list[1].args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertIn("rec-1", request.full_url)
        self.assertNotIn("token", request.full_url)

    @patch("urllib.request.urlopen")
    def test_api_errors_do_not_echo_token(self, mock_open):
        mock_open.return_value = _Response({"success": False, "errors": [{"message": "denied"}]})
        with self.assertRaisesRegex(cloudflare.CloudflareError, "denied") as raised:
            cloudflare.Client("sensitive-token").zone("example.com")
        self.assertNotIn("sensitive-token", str(raised.exception))

    @patch("urllib.request.urlopen")
    def test_delete_addresses_one_record_only(self, mock_open):
        mock_open.return_value = _Response({"success": True, "result": {"id": "rec-1"}})
        cloudflare.Client("token").delete_record("zone-1", "rec-1")
        request = mock_open.call_args.args[0]
        self.assertEqual(request.get_method(), "DELETE")
        self.assertTrue(request.full_url.endswith("/zones/zone-1/dns_records/rec-1"))

    @patch("urllib.request.urlopen")
    def test_restore_updates_only_the_captured_record(self, mock_open):
        mock_open.return_value = _Response({"success": True, "result": {"id": "rec-1"}})
        cloudflare.Client("token").restore_record("zone-1", {
            "id": "rec-1", "type": "A", "name": "example.com", "content": "192.0.2.1",
            "proxied": True, "ttl": 1,
        })
        request = mock_open.call_args.args[0]
        self.assertEqual(request.get_method(), "PUT")
        self.assertTrue(request.full_url.endswith("/zones/zone-1/dns_records/rec-1"))

    @patch("urllib.request.urlopen")
    def test_update_cname_keeps_target_and_enables_proxy(self, mock_open):
        mock_open.return_value = _Response({"success": True, "result": {"id": "rec-1"}})
        cloudflare.Client("token").update_record("zone-1", {
            "id": "rec-1", "type": "CNAME", "name": "www.example.com", "content": "example.com", "ttl": 1,
        }, proxied=True)
        body = json.loads(mock_open.call_args.args[0].data.decode())
        self.assertEqual(body["content"], "example.com")
        self.assertTrue(body["proxied"])


class TestRemotePreviewIdentity(unittest.TestCase):
    def test_preview_rolls_back_a_partial_instance_when_ensure_fails(self):
        args = types.SimpleNamespace(
            action="create", json=True, confirm=True, ttl_hours=24,
            remote="preview", project_dir="/tmp/project", name=None,
            base_domain="sandbox.asb.bd",
        )
        config_core = MagicMock()
        config_core.load_project_config.return_value = {"root": "/tmp/project", "slug": "demo"}
        entry = {"provisioned": True, "origin_ipv4": "203.0.113.10"}

        with patch.object(preview, "_load_state", return_value={"version": 1, "previews": {}}), \
             patch.object(preview.core, "_core", return_value=config_core), \
             patch.object(preview.remote, "get_remote", return_value=entry), \
             patch.object(preview, "preflight_project_capability", return_value=None), \
             patch.object(preview.remote, "current_branch", return_value="latest"), \
             patch.object(preview, "preview_identity", return_value=("preview-id", "preview-label")), \
             patch.object(preview.remote, "ensure_deploy_repo", return_value="/srv/demo"), \
             patch.object(preview.remote, "push_commits", return_value="abc123"), \
             patch.object(preview.remote, "reset_target_to"), \
             patch.object(preview.remote, "capture_uncommitted", return_value=("", [])), \
             patch.object(preview.remote, "apply_uncommitted"), \
             patch.object(preview.remote, "ensure_remote_instance", side_effect=RuntimeError("bootstrap failed")), \
             patch.object(preview.remote, "delete_remote_instance_for_label") as cleanup, \
             patch.object(preview, "die", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                preview.cmd_preview(None, args)

        cleanup.assert_called_once_with(entry, "/srv/demo", "preview-label")

    def test_preview_create_loads_project_config_from_core_facade(self):
        args = types.SimpleNamespace(
            action="create", json=True, confirm=True, ttl_hours=24,
            remote="preview", project_dir="/tmp/project", name=None,
            base_domain="sandbox.asb.bd",
        )
        config_core = MagicMock()
        config_core.load_project_config.return_value = {"root": "/tmp/project", "slug": "demo"}
        client = MagicMock()
        client.zone.return_value = {"id": "zone-1"}
        client.records.return_value = []
        client.upsert_address.return_value = {"id": "record-1"}
        instance = {
            "instance": "preview-demo",
            "wordpress_port": 8188,
            "login_url": "http://127.0.0.1:8188/wp-login.php?sandbox_autologin=token",
        }
        state = {"version": 1, "previews": {}}

        with patch.object(preview, "_load_state", return_value=state), \
             patch.object(preview, "_save_state"), \
             patch.object(preview.core, "_core", return_value=config_core), \
             patch.object(preview.remote, "get_remote", return_value={
                 "provisioned": True, "origin_ipv4": "203.0.113.10",
             }), \
             patch.object(preview, "preflight_project_capability", return_value=None), \
             patch.object(preview.remote, "current_branch", return_value="latest"), \
             patch.object(preview.remote, "ensure_deploy_repo", return_value="/srv/demo"), \
             patch.object(preview.remote, "push_commits", return_value="abc123"), \
             patch.object(preview.remote, "reset_target_to"), \
             patch.object(preview.remote, "capture_uncommitted", return_value=("", [])), \
             patch.object(preview.remote, "apply_uncommitted"), \
             patch.object(preview.remote, "ensure_remote_instance", return_value=instance), \
             patch.object(preview.remote, "activate_remote_plugin"), \
             patch.object(preview.remote, "configure_instance_https_route"), \
             patch.object(preview.remote, "set_remote_instance_url"), \
             patch.object(preview.remote, "rewrite_instance_url", return_value="https://preview.example.test/wp-login.php?sandbox_autologin=token"), \
             patch.object(preview.cloudflare, "Client", return_value=client), \
             patch("builtins.print") as printed:
            preview.cmd_preview(None, args)
        response = json.loads(printed.call_args.args[0])
        self.assertEqual(
            response["preview"]["login_url"],
            "https://preview.example.test/wp-login.php?sandbox_autologin=token",
        )

        config_core.load_project_config.assert_called_once_with("/tmp/project")

    def test_preview_identity_is_stable_and_namespaced(self):
        first = preview.preview_identity("/tmp/example", "fix/login", "login")
        second = preview.preview_identity("/tmp/example", "fix/login", "login")
        self.assertEqual(first, second)
        self.assertTrue(first[0].startswith("login-"))
        self.assertTrue(first[1].startswith("preview-"))

    def test_preview_domain_is_dns_safe(self):
        domain = preview.preview_domain("login-1234abcd", "My Plugin", "sandbox.asb.bd")
        self.assertEqual(domain, "login-1234abcd-my-plugin.sandbox.asb.bd")

    def test_preview_domain_rejects_invalid_base(self):
        with self.assertRaisesRegex(ValueError, "base domain"):
            preview.preview_domain("login-1234abcd", "plugin", "bad/domain")


class TestPersonalSecretFile(unittest.TestCase):
    def test_reads_quoted_export_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".zshrc.secrets"
            path.write_text("export TOKEN='value with spaces'\nOTHER=two\n")
            self.assertEqual(personal_secrets.read_secret_file(path)["TOKEN"], "value with spaces")
            with patch.dict("os.environ", {"TOKEN": "from-environment"}):
                self.assertEqual(personal_secrets.resolve_secret("TOKEN", path), "from-environment")

    def test_rejects_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".zshrc.secrets"
            path.write_text("export TOKEN=$OTHER\n")
            with self.assertRaisesRegex(personal_secrets.SecretError, "expansion"):
                personal_secrets.read_secret_file(path)

    def test_allows_dollar_in_single_quoted_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".zshrc.secrets"
            path.write_text("export TOKEN='dollar$literal'\n")
            self.assertEqual(personal_secrets.read_secret_file(path)["TOKEN"], "dollar$literal")

    def test_write_secret_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".zshrc.secrets"
            personal_secrets.write_secret("TOKEN", "value", path)
            self.assertEqual(personal_secrets.read_secret_file(path)["TOKEN"], "value")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_migrates_only_known_zshrc_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            zshrc = directory / ".zshrc"
            target = directory / ".zshrc.secrets"
            zshrc.write_text("export OPENAI_API_KEY='key'\nexport PATH=/bin\n")
            self.assertEqual(personal_secrets.migrate_zshrc(target, zshrc), ["OPENAI_API_KEY"])
            self.assertEqual(personal_secrets.read_secret_file(target)["OPENAI_API_KEY"], "key")
            self.assertNotIn("OPENAI_API_KEY", zshrc.read_text())
            self.assertIn("PATH=/bin", zshrc.read_text())


class TestHostingSecrets(unittest.TestCase):
    @patch("sandbox.commands.hosting.remote.ssh_run")
    def test_basic_auth_password_is_streamed_to_remote_hash_command(self, ssh_run):
        ssh_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="$2a$14$generated", stderr="",
        )
        hashed = hosting_cmd._remote_basic_auth_hash({"ssh": "alim@example.test"}, "secret-value")
        self.assertEqual(hashed, "$2a$14$generated")
        self.assertEqual(ssh_run.call_args.args[1], "caddy hash-password")
        self.assertEqual(ssh_run.call_args.kwargs["input_data"], "secret-value\n")

    def test_renders_declared_public_and_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = _manifest().replace(
                "    cloudflare:\n",
                "    secrets:\n      values:\n        PUBLIC_VALUE: fixed\n      required:\n        PRIVATE_VALUE: TEST_SECRET\n    cloudflare:\n",
            )
            with TestHostingManifest()._write(manifest) as project:
                validated = hosting.validate_manifest(project)
            rendered = hosting.render_env_file(validated, {"TEST_SECRET": "hidden value"})
            self.assertIn("PUBLIC_VALUE=fixed", rendered)
            self.assertIn("PRIVATE_VALUE='hidden value'", rendered)

    def test_basic_auth_secret_is_not_rendered_into_compose_environment(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n    cloudflare:\n",
        )
        with TestHostingManifest()._write(manifest) as project:
            validated = hosting.validate_manifest(project)
        rendered = hosting.render_env_file(validated, {"BASIC_AUTH_PASSWORD": "secret-value"})
        self.assertNotIn("BASIC_AUTH_PASSWORD", rendered)
        self.assertNotIn("secret-value", rendered)

    def test_missing_declared_secret_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = _manifest().replace(
                "    cloudflare:\n",
                "    secrets:\n      required:\n        PRIVATE_VALUE: TEST_SECRET\n    cloudflare:\n",
            )
            with TestHostingManifest()._write(manifest) as project:
                validated = hosting.validate_manifest(project)
            with self.assertRaisesRegex(hosting.HostingError, "TEST_SECRET"):
                hosting.render_env_file(validated, {})

    def test_compose_prefix_selects_the_generated_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestHostingManifest()._write(_manifest()) as project:
                validated = hosting.validate_manifest(project)
            command = hosting_cmd._compose_prefix(validated, "/srv/site", "/runtime/override.yml", "/runtime/env")
            self.assertIn("SANDBOX_HOST_ENV_FILE=/runtime/env", command)
            self.assertIn("--env-file /runtime/env", command)

    @patch("sandbox.commands.hosting._remote_checked")
    def test_host_source_uses_manifest_project_not_wordpress_slug(self, mocked):
        target = hosting_cmd._ensure_host_source({"ssh": "ubuntu@example.test"}, "/srv/sandbox", "alimuzzaman-me")
        self.assertEqual(target, "/srv/sandbox/deploy-src/hosts/alimuzzaman-me")
        self.assertIn("deploy-src/hosts/alimuzzaman-me", mocked.call_args.args[1])
