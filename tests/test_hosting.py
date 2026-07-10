"""Offline coverage for managed Compose hosting and Cloudflare intent."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._cloudflare as cloudflare  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402


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
