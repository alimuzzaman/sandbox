"""Offline coverage for managed Compose hosting and Cloudflare intent."""
import json
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._cloudflare as cloudflare  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
import sandbox.core._remote as remote  # noqa: E402
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


def _public_acme_manifest(aliases=None):
    if aliases is None:
        aliases = "        - hostname: www.example.test\n          mode: serve"
    return _manifest(aliases).replace(
        "      proxied: true\n      tls: origin-ca\n      ssl_mode: strict\n",
        "      proxied: false\n      tls: acme\n",
    )


def _manifest_with_environments(names):
    source = _manifest()
    prefix, block = source.split("  production:\n", 1)
    return prefix + "".join(f"  {name}:\n{block}" for name in names)


def _manifest_with_derived_revision():
    return _manifest().replace(
        "      require_clean: true\n",
        "      require_clean: true\n"
        "      derived_environment:\n"
        "        LENZORA_SOURCE_REVISION: pushed_commit_sha\n",
    )


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

    def test_single_environment_can_omit_environment_choice(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        self.assertEqual(result["environment"], "production")

    def test_environment_names_are_deterministic_for_all_validation(self):
        with self._write(_manifest_with_environments(["production", "development"])) as directory:
            self.assertEqual(
                hosting.environment_names(directory),
                ("development", "production"),
            )

    def test_validates_deploy_derived_environment(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            result = hosting.validate_manifest(directory)
        self.assertEqual(
            result["deploy"]["derived_environment"],
            {"LENZORA_SOURCE_REVISION": "pushed_commit_sha"},
        )
        self.assertEqual(result["deploy"]["min_free_disk_mb"], 1024)

    def test_validates_compose_build_timeout(self):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      build_timeout_seconds: 2400\n",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        self.assertEqual(result["compose"]["build_timeout_seconds"], 2400)

        for value in (True, 59, 7201, "2400"):
            token = (
                str(value).lower() if isinstance(value, bool)
                else f'"{value}"' if isinstance(value, str)
                else str(value)
            )
            invalid = _manifest().replace(
                "      service: web\n",
                f"      service: web\n      build_timeout_seconds: {token}\n",
            )
            with self.subTest(value=value), self._write(invalid) as directory:
                with self.assertRaisesRegex(hosting.HostingError, "build_timeout_seconds"):
                    hosting.validate_manifest(directory)

    def test_validates_host_apply_disk_floor(self):
        invalid = (
            _manifest().replace(
                "      require_clean: true\n",
                "      require_clean: true\n      min_free_disk_mb: VALUE\n",
            )
        )
        cases = [(0, "0"), (True, "true"), ("1024", '"1024"'), (1_048_577, "1048577")]
        for value, token in cases:
            with self.subTest(value=value), self._write(invalid.replace("VALUE", token)) as directory:
                with self.assertRaisesRegex(hosting.HostingError, "min_free_disk_mb"):
                    hosting.validate_manifest(directory)

    def test_rejects_invalid_deploy_derived_environment(self):
        cases = {
            "mapping": _manifest().replace(
                "      require_clean: true\n",
                "      require_clean: true\n      derived_environment: value\n",
            ),
            "environment key": _manifest().replace(
                "      require_clean: true\n",
                "      require_clean: true\n"
                "      derived_environment:\n        bad-key: pushed_commit_sha\n",
            ),
            "providers": _manifest().replace(
                "      require_clean: true\n",
                "      require_clean: true\n"
                "      derived_environment:\n        SOURCE_REVISION: local_head\n",
            ),
            "require_clean": _manifest_with_derived_revision().replace(
                "      require_clean: true\n", "      require_clean: false\n",
            ),
        }
        for message, manifest in cases.items():
            with self.subTest(message=message), self._write(manifest) as directory:
                with self.assertRaisesRegex(hosting.HostingError, message):
                    hosting.validate_manifest(directory)

    def test_rejects_derived_environment_secret_collisions(self):
        secret_blocks = (
            "      values:\n        LENZORA_SOURCE_REVISION: static\n",
            "      required:\n        LENZORA_SOURCE_REVISION: REVISION_SECRET\n",
            "      generated:\n        LENZORA_SOURCE_REVISION: REVISION_SECRET\n",
        )
        for secret_block in secret_blocks:
            manifest = _manifest_with_derived_revision().replace(
                "    cloudflare:\n", f"    secrets:\n{secret_block}    cloudflare:\n",
            )
            with self.subTest(secret_block=secret_block), self._write(manifest) as directory:
                with self.assertRaisesRegex(hosting.HostingError, "must not overlap"):
                    hosting.validate_manifest(directory)

    def test_missing_environment_lists_sorted_escaped_choices(self):
        manifest = _manifest_with_environments([
            '"zeta"', '"a\\nb"', '"alpha"',
        ])
        with self._write(manifest) as directory:
            with self.assertRaises(hosting.HostingError) as caught:
                hosting.validate_manifest(directory)
        self.assertEqual(
            str(caught.exception),
            "--environment is required when a manifest has multiple environments; "
            "available: 'a\\nb', 'alpha', 'zeta'",
        )

    def test_unknown_environment_lists_sorted_escaped_choices(self):
        manifest = _manifest_with_environments([
            '"zeta"', '"a\\nb"', '"alpha"',
        ])
        with self._write(manifest) as directory:
            with self.assertRaises(hosting.HostingError) as caught:
                hosting.validate_manifest(directory, "bad\x1b[31m")
        self.assertEqual(
            str(caught.exception),
            "unknown hosting environment 'bad\\x1b[31m'; "
            "available: 'a\\nb', 'alpha', 'zeta'",
        )

    def test_environment_choice_list_is_bounded(self):
        names = [f'"environment-{index:02d}"' for index in range(20)]
        manifest = _manifest_with_environments(names)
        with self._write(manifest) as directory:
            with self.assertRaises(hosting.HostingError) as caught:
                hosting.validate_manifest(directory)
        self.assertEqual(
            str(caught.exception),
            "--environment is required when a manifest has multiple environments; "
            "available: " + ", ".join(
                [*(f"'environment-{index:02d}'" for index in range(16)), "... (4 more)"]
            ),
        )

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

    def test_nested_manifest_uses_its_parent_as_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            nested = outer / "site"
            nested.mkdir()
            (nested / "sandbox.hosting.yml").write_text(_manifest())
            (nested / "compose.yml").write_text("services: {}\n")
            # An outer checkout may contain unrelated Compose files.  Passing
            # the nested project path must never select that outer source.
            (outer / "compose.yml").write_text("services: outer\n")
            result = hosting.validate_manifest(nested)
        self.assertEqual(Path(result["manifest_path"]), (nested / "sandbox.hosting.yml").resolve())
        self.assertEqual(Path(result["source_root"]), nested.resolve())
        self.assertEqual(Path(result["project_root"]), nested.resolve())
        self.assertEqual(result["compose"]["files"], ["compose.yml"])

    def test_nested_manifest_rejects_source_root_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "site"
            nested.mkdir()
            manifest = _manifest().replace("project: example-site", "project: example-site\nsource_root: ..")
            (nested / "sandbox.hosting.yml").write_text(manifest)
            (nested / "compose.yml").write_text("services: {}\n")
            with self.assertRaisesRegex(hosting.HostingError, "source_root"):
                hosting.validate_manifest(nested)

    def test_nested_manifest_declared_source_root_is_the_transfer_root(self):
        """A nested manifest's declared root drives validation and dirty apply.

        This is intentionally a local, live-ish transfer regression: it uses a
        real nested Git checkout and tar archive, while recording the one SSH
        extraction call.  The archive and Compose command must both be rooted
        at ``site``; an outer checkout file must never be included.
        """
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            config = outer / "config"
            source = outer / "site"
            config.mkdir()
            source.mkdir()
            (config / "sandbox.hosting.yml").write_text(
                _manifest().replace("project: example-site", "project: example-site\nsource_root: ../site")
            )
            (source / "compose.yml").write_text("services: {}\n")
            subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
            subprocess.run(["git", "add", "config/sandbox.hosting.yml", "site/compose.yml"],
                           cwd=outer, check=True)
            subprocess.run([
                "git", "-c", "user.email=sandbox@example.test", "-c", "user.name=Sandbox",
                "commit", "-qm", "initial",
            ], cwd=outer, check=True)

            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=outer,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            committed_sha, _ = remote._source_tree_commit(source, source, head)
            committed = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", committed_sha],
                cwd=outer, capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            self.assertEqual(committed, ["compose.yml"])
            self.assertNotIn("unrelated-outer.txt", committed)

            (source / "compose.yml").write_text("services:\n  web: {}\n")
            (source / "local.yml").write_text("services: {}\n")
            (outer / "unrelated-outer.txt").write_text("must not deploy\n")
            validated = hosting.validate_manifest(config)
            self.assertEqual(Path(validated["source_root"]), source.resolve())
            self.assertTrue(validated["source_root_nested"])

            diff_text, untracked = remote.capture_uncommitted(source)
            self.assertIn("a/compose.yml b/compose.yml", diff_text)
            self.assertEqual(untracked, ["local.yml"])
            self.assertNotIn("unrelated-outer.txt", diff_text)
            self.assertNotIn("unrelated-outer.txt", untracked)

            with patch.object(remote, "ssh_run", return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr="")) as ssh:
                applied = remote.apply_uncommitted(
                    {"ssh": "ubuntu@example.test"}, "/srv/deploy/example-site", source,
                    diff_text, untracked,
                )
            self.assertEqual(applied, 2)
            command = ssh.call_args.args[1]
            self.assertIn("tar -xzf - -C /srv/deploy/example-site", command)
            self.assertNotIn("/srv/deploy/example-site/site", command)
            archive = tarfile.open(fileobj=io.BytesIO(ssh.call_args.kwargs["input_data"]), mode="r:gz")
            names = {name for name in archive.getnames() if not name.startswith("._")}
            self.assertEqual(names, {"compose.yml", "local.yml"})

            compose_command = hosting_cmd._compose_prefix(
                validated, "/srv/deploy/example-site", "/runtime/override.yml", "/runtime/env"
            )
            self.assertIn("-f /srv/deploy/example-site/compose.yml", compose_command)
            self.assertNotIn("/srv/deploy/example-site/site/compose.yml", compose_command)

    def test_renders_caddy_serve_and_redirect_routes(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem")
        self.assertIn("reverse_proxy 127.0.0.1:18001", rendered)
        self.assertIn("redir https://xn--94b2eraib0c0bd9i.xn--54b7fta0cc{uri} 308", rendered)
        self.assertIn("tls /cert.pem /key.pem", rendered)

    def test_normalizes_an_idn_redirect_target_before_rendering_or_planning(self):
        aliases = "        - hostname: asb.bd\n          mode: redirect\n          target: https://আমারসোনার.বাংলা"
        with self._write(_manifest(aliases)) as directory:
            result = hosting.validate_manifest(directory)
        redirect = next(route for route in result["routes"] if route["mode"] == "redirect")
        self.assertEqual(redirect["target"], "https://xn--94b2eraib0c0bd9i.xn--54b7fta0cc")
        self.assertIn("xn--94b2eraib0c0bd9i.xn--54b7fta0cc{uri}", hosting.caddyfile(result, 18001))

    def test_rejects_redirect_alias_cycles(self):
        aliases = """        - hostname: one.example.test
          mode: redirect
          target: https://two.example.test
        - hostname: two.example.test
          mode: redirect
          target: https://one.example.test"""
        with self._write(_manifest(aliases)) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "cycle"):
                hosting.validate_manifest(directory)

    def test_rejects_redirect_target_path_that_cannot_preserve_request_uri(self):
        aliases = "        - hostname: asb.bd\n          mode: redirect\n          target: https://example.test/fixed"
        with self._write(_manifest(aliases)) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "path, query, or fragment"):
                hosting.validate_manifest(directory)

    def test_robots_defaults_to_allow_and_leaves_the_render_untouched(self):
        with self._write(_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        self.assertEqual(result["robots"], "allow")
        self.assertNotIn("robots.txt", hosting.caddyfile(result, 18001, "/c.pem", "/k.pem"))

    def test_robots_deny_answers_robots_txt_ahead_of_the_proxy(self):
        manifest = _manifest().replace("    cloudflare:\n", "    robots: deny\n    cloudflare:\n")
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem")
        self.assertIn("handle /robots.txt {", rendered)
        self.assertIn("Disallow: /", rendered)
        # The proxy must sit in its own handle, or /robots.txt has two
        # candidate routes and can fall through to the origin.
        self.assertLess(rendered.index("handle /robots.txt"),
                        rendered.index("reverse_proxy 127.0.0.1:18001"))
        self.assertIn("    handle {\n        reverse_proxy 127.0.0.1:18001", rendered)
        # Redirect-only aliases stay plain redirects.
        self.assertIn("redir https://xn--94b2eraib0c0bd9i.xn--54b7fta0cc{uri} 308", rendered)

    def test_rejects_an_unknown_robots_mode(self):
        manifest = _manifest().replace("    cloudflare:\n", "    robots: sometimes\n    cloudflare:\n")
        with self._write(manifest) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "robots must be allow or deny"):
                hosting.validate_manifest(directory)

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

    def test_renders_basic_auth_bypasses_for_public_paths_and_cloudflare_client_ips(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n      bypass_ips: [103.95.98.15, 2001:4860:4860::8888]\n      bypass_paths: [/auth.md, /.well-known/oauth-protected-resource]\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem", "$2a$hash")
        self.assertEqual(result["basic_auth"]["bypass_ips"], ["103.95.98.15", "2001:4860:4860::8888"])
        self.assertIn("remote_ip 173.245.48.0/20", rendered)
        self.assertIn("header CF-Connecting-IP 103.95.98.15", rendered)
        self.assertIn("header CF-Connecting-IP 2001:4860:4860::8888", rendered)
        self.assertIn("handle @basic_auth_bypass_0", rendered)
        self.assertIn("handle @basic_auth_bypass_1", rendered)
        self.assertIn("method GET", rendered)
        self.assertIn("path /auth.md /.well-known/oauth-protected-resource", rendered)
        self.assertIn("handle @basic_auth_public_paths", rendered)
        self.assertIn("handle {", rendered)
        self.assertIn("lnzr_dev $2a$hash", rendered)

    def test_renders_method_scoped_exact_and_templated_basic_auth_bypass_routes(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            """    basic_auth:
      username: lnzr_dev
      password_secret: BASIC_AUTH_PASSWORD
      bypass_routes:
        - path: /api/oauth2/token
          methods: [POST]
        - path_template: /api/v1/orgs/{orgId}/projects/{projectId}/snapshots
          methods: [GET, POST]
    cloudflare:
""",
        )
        with self._write(manifest) as directory:
            result = hosting.validate_manifest(directory)
        rendered = hosting.caddyfile(result, 18001, "/cert.pem", "/key.pem", "$2a$hash")
        self.assertEqual(
            result["basic_auth"]["bypass_routes"],
            [
                {"path": "/api/oauth2/token", "methods": ["POST"]},
                {
                    "path_template": "/api/v1/orgs/{orgId}/projects/{projectId}/snapshots",
                    "methods": ["GET", "POST"],
                },
            ],
        )
        self.assertIn("@basic_auth_public_route_0 {", rendered)
        self.assertIn("method POST", rendered)
        self.assertIn("path /api/oauth2/token", rendered)
        self.assertIn("@basic_auth_public_route_1 {", rendered)
        self.assertIn("method GET POST", rendered)
        self.assertIn(
            "path_regexp basic_auth_public_route_1 ^/api/v1/orgs/[^/]+/projects/[^/]+/snapshots$",
            rendered,
        )

    def test_rejects_non_public_basic_auth_bypass_ip(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n      bypass_ips: [127.0.0.1]\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "bypass_ips must contain public"):
                hosting.validate_manifest(directory)

    def test_rejects_unsafe_basic_auth_bypass_path(self):
        manifest = _manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n      username: lnzr_dev\n      password_secret: BASIC_AUTH_PASSWORD\n      bypass_paths: [/]\n    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "bypass_paths"):
                hosting.validate_manifest(directory)

    def test_rejects_unsafe_basic_auth_bypass_routes(self):
        invalid_routes = (
            "- path: /\n          methods: [POST]",
            "- path: /api/*\n          methods: [POST]",
            "- path: /api/../admin\n          methods: [POST]",
            "- path_template: /api/{bad-name}\n          methods: [GET]",
            "- path_template: /api/static\n          methods: [GET]",
            "- path: /api/oauth2/token\n          methods: []",
            "- path: /api/oauth2/token\n          methods: [TRACE]",
            "- path: /api/oauth2/token\n          path_template: /api/{resource}\n          methods: [POST]",
        )
        for route in invalid_routes:
            with self.subTest(route=route):
                manifest = _manifest().replace(
                    "    cloudflare:\n",
                    "    basic_auth:\n"
                    "      username: lnzr_dev\n"
                    "      password_secret: BASIC_AUTH_PASSWORD\n"
                    "      bypass_routes:\n"
                    f"        {route}\n"
                    "    cloudflare:\n",
                )
                with self._write(manifest) as directory:
                    with self.assertRaisesRegex(hosting.HostingError, "bypass_routes"):
                        hosting.validate_manifest(directory)

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

    def test_public_acme_plan_uses_dns_only_records_and_automatic_caddy_tls(self):
        with self._write(_public_acme_manifest()) as directory:
            result = hosting.validate_manifest(directory)
        plan = hosting.desired_plan(result, "203.0.113.10")
        rendered = hosting.caddyfile(result, 18001)

        self.assertTrue(all(record["proxied"] is False for record in plan["records"]))
        self.assertIsNone(plan["ssl_mode"])
        self.assertNotIn("    tls ", rendered)
        self.assertIn("reverse_proxy 127.0.0.1:18001", rendered)

    def test_rejects_mixed_cloudflare_tls_policies(self):
        invalid_blocks = (
            "      proxied: false\n      tls: origin-ca\n      ssl_mode: strict\n",
            "      proxied: true\n      tls: acme\n",
            "      proxied: false\n      tls: acme\n      ssl_mode: strict\n",
        )
        for block in invalid_blocks:
            with self.subTest(block=block):
                manifest = _manifest().replace(
                    "      proxied: true\n      tls: origin-ca\n      ssl_mode: strict\n",
                    block,
                )
                with self._write(manifest) as directory:
                    with self.assertRaisesRegex(hosting.HostingError, "Cloudflare"):
                        hosting.validate_manifest(directory)

    def test_public_acme_rejects_cloudflare_header_ip_bypass(self):
        manifest = _public_acme_manifest().replace(
            "    cloudflare:\n",
            "    basic_auth:\n"
            "      username: lnzr_dev\n"
            "      password_secret: BASIC_AUTH_PASSWORD\n"
            "      bypass_ips: [103.95.98.15]\n"
            "    cloudflare:\n",
        )
        with self._write(manifest) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "bypass_ips.*proxied"):
                hosting.validate_manifest(directory)

    def test_public_acme_rejects_wildcard_routes_without_dns_challenge_support(self):
        aliases = "        - hostname: '*.example.test'\n          mode: serve"
        with self._write(_public_acme_manifest(aliases)) as directory:
            with self.assertRaisesRegex(hosting.HostingError, "public ACME.*wildcard"):
                hosting.validate_manifest(directory)

    def test_runtime_is_namespaced_and_uses_loopback_only(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        self.assertEqual(runtime["compose_project"], "sandbox-host-example-site-production")
        self.assertIn('127.0.0.1:', runtime["compose_override"])
        self.assertIn(f"reverse_proxy 127.0.0.1:{runtime['loopback_port']}", runtime["caddyfile"])
        self.assertNotIn("derived_environment", runtime)

    def test_runtime_plan_defers_derived_environment_resolution_to_apply(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        self.assertEqual(runtime["derived_environment"], [{
            "key": "LENZORA_SOURCE_REVISION",
            "provider": "pushed_commit_sha",
            "resolved_at_apply": True,
        }])
        self.assertNotIn("environment", runtime)

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

    def test_compose_command_starts_declared_background_services(self):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080", "container_port: 8080\n      background_services: [worker, scheduler]"
            ))
            validated = hosting.validate_manifest(directory)
        command = hosting.render_compose_command(
            validated, "/srv/example", "/srv/example/.sandbox-hosting.yml"
        )
        self.assertTrue(command.endswith("web worker scheduler"))

    def test_rejects_duplicate_background_service(self):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080", "container_port: 8080\n      background_services: [web]"
            ))
            with self.assertRaisesRegex(hosting.HostingError, "must not be duplicated"):
                hosting.validate_manifest(directory)

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

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_background_services_are_recreated_and_started_with_web(self, remote_checked, _write):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080", "container_port: 8080\n      background_services: [worker]"
            ))
            validated = hosting.validate_manifest(directory)
        runtime = {"compose_override": "services: {}\n", "environment": "EXAMPLE=value\n"}
        hosting_cmd._run_compose({}, validated, "/srv/example", "/srv/runtime", runtime)
        commands = [call.args[1] for call in remote_checked.call_args_list]
        self.assertIn("--force-recreate --renew-anon-volumes --remove-orphans web worker", commands[0])
        self.assertTrue(commands[-1].endswith("up -d web worker"))

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_build_false_deploys_without_rebuilding_any_image(self, remote_checked, _write):
        with self._write(_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080",
                "container_port: 8080\n      build: false\n      init_services: [setup]",
            ))
            validated = hosting.validate_manifest(directory)
        runtime = {"compose_override": "services: {}\n", "environment": "EXAMPLE=value\n"}
        hosting_cmd._run_compose({}, validated, "/srv/example", "/srv/runtime", runtime)
        commands = [call.args[1] for call in remote_checked.call_args_list]
        self.assertNotIn("--build", commands[0])
        self.assertIn("up -d --force-recreate --renew-anon-volumes --remove-orphans web", commands[0])
        self.assertFalse(any(command.endswith("build setup") for command in commands))
        self.assertTrue(any(command.endswith("run --rm setup") for command in commands))

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_default_compose_apply_requests_a_fresh_build(self, remote_checked, _write):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = {"compose_override": "services: {}\n", "environment": "EXAMPLE=value\n"}
        hosting_cmd._run_compose({}, validated, "/srv/example", "/srv/runtime", runtime)
        command = remote_checked.call_args_list[0].args[1]
        self.assertIn("up -d --build", command)

    @patch("sandbox.commands.hosting.remote.ssh_stream")
    def test_logged_remote_commands_use_stream_transport(self, ssh_stream):
        ssh_stream.return_value = subprocess.CompletedProcess(
            [], 0, stdout="remote output\n", stderr="",
        )
        lines = []
        callback = lines.append
        output = hosting_cmd._remote_checked(
            {}, "printf output", timeout=42, progress=callback,
            log_path="/srv/runtime/apply.log",
        )
        self.assertEqual(output, "remote output\n")
        ssh_stream.assert_called_once()
        self.assertIn("tee -a", ssh_stream.call_args.args[1])
        self.assertEqual(ssh_stream.call_args.kwargs["timeout"], 42)
        self.assertEqual(ssh_stream.call_args.kwargs["on_line"], callback)

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_compose_progress_is_phase_visible_and_log_is_forwarded(self, remote_checked, _write):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = {"compose_override": "services: {}\n", "environment": "EXAMPLE=value\n"}
        progress = []
        hosting_cmd._run_compose(
            {}, validated, "/srv/example", "/srv/runtime", runtime,
            progress.append, "/srv/runtime/apply.log",
        )
        self.assertEqual(progress, [
            "Compose build/recreate started (timeout 900s; build=enabled)",
            "Compose build/recreate completed",
        ])
        build_call = next(
            call for call in remote_checked.call_args_list
            if "--build" in call.args[1]
        )
        self.assertEqual(build_call.kwargs["log_path"], "/srv/runtime/apply.log")

    @patch("sandbox.commands.hosting._remote_checked")
    def test_apply_rejects_a_stale_running_service_revision(self, remote_checked):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        remote_checked.return_value = "b" * 40
        with self.assertRaisesRegex(RuntimeError, "source revision check failed"):
            hosting_cmd._verify_remote_derived_environment(
                {}, validated, "/srv/source", "/srv/runtime", "a" * 40,
            )
        command = remote_checked.call_args.args[1]
        self.assertIn("exec -T web", command)
        self.assertIn("LENZORA_SOURCE_REVISION", command)

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_build_timeout_is_used_for_compose_build_steps(self, remote_checked, _write):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      build_timeout_seconds: 2400\n"
            "      init_services: [setup]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = {"compose_override": "services: {}\n", "environment": "EXAMPLE=value\n"}
        hosting_cmd._run_compose({}, validated, "/srv/example", "/srv/runtime", runtime)
        build_calls = [call for call in remote_checked.call_args_list
                       if "--force-recreate" in call.args[1]
                       or " build setup" in call.args[1]]
        self.assertGreaterEqual(len(build_calls), 2)
        self.assertTrue(all(call.kwargs.get("timeout") == 2400 for call in build_calls))

    def test_build_defaults_to_true_and_rejects_a_non_boolean(self):
        with self._write(_manifest()) as directory:
            self.assertTrue(hosting.validate_manifest(directory)["compose"]["build"])
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(manifest.read_text().replace(
                "container_port: 8080", "container_port: 8080\n      build: cached"
            ))
            with self.assertRaisesRegex(hosting.HostingError, "compose.build must be true or false"):
                hosting.validate_manifest(directory)

    @patch("sandbox.commands.hosting.time.sleep")
    @patch("sandbox.commands.hosting.remote.ssh_run")
    def test_remote_health_retries_startup_connection_reset(self, ssh_run, _sleep):
        ssh_run.side_effect = [
            types.SimpleNamespace(returncode=56, stdout="", stderr="curl: (56) Recv failure: Connection reset by peer"),
            types.SimpleNamespace(returncode=0, stdout="200", stderr=""),
        ]
        runtime = {
            "loopback_port": 18001,
            "healthcheck": {"path": "/api/health", "statuses": [200]},
        }

        hosting_cmd._verify_remote_health({}, runtime)

        self.assertEqual(ssh_run.call_count, 2)
        _sleep.assert_called_once_with(2)

    @patch("sandbox.commands.hosting.remote.ssh_run")
    def test_remote_failure_reports_the_cause_not_the_build_progress(self, ssh_run):
        # Compose writes build progress to stderr, so the cause is at the tail.
        stderr = "\n".join(
            [f" Image sandbox-host-example-service-{index} Building " for index in range(400)]
            + ["target worker: failed to solve: could not resolve base image"]
        )
        ssh_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=stderr,
        )
        with self.assertRaises(RuntimeError) as raised:
            hosting_cmd._remote_checked({}, "docker compose up")
        message = str(raised.exception)
        self.assertIn("failed to solve: could not resolve base image", message)
        self.assertNotIn("Building", message)

    def test_remote_failure_keeps_the_tail_when_no_marker_matches(self):
        text = "\n".join(f"line {index}" for index in range(2000))
        message = hosting_cmd._remote_failure_message(text)
        self.assertTrue(message.startswith("... "))
        self.assertTrue(message.endswith("line 1999"))
        self.assertLessEqual(len(message), 2000)

    def test_remote_failure_falls_back_when_output_is_empty(self):
        self.assertEqual(hosting_cmd._remote_failure_message(""), "remote command failed")

    @patch("sandbox.commands.hosting.remote.ssh_run")
    def test_remote_timeout_preserves_bounded_partial_output(self, ssh_run):
        ssh_run.side_effect = subprocess.TimeoutExpired(
            cmd=["ssh", "secret-target"], timeout=45,
            output="step 1\nstep 2\n",
            stderr="target worker: failed to solve: timeout\n",
        )

        with self.assertRaisesRegex(RuntimeError, "remote command timed out after 45 seconds") as raised:
            hosting_cmd._remote_checked({}, "docker compose build", timeout=45)

        self.assertIn("failed to solve: timeout", str(raised.exception))
        self.assertNotIn("secret-target", str(raised.exception))

    def test_host_status_reports_revision_and_bounded_service_health(self):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        state = {"version": 1, "hosts": {
            hosting.state_key("myvps", validated): {"commit": "a" * 40},
        }}
        rows = '{"Service":"web","State":"running","Health":"healthy"}\n' \
            '{"Service":"worker","State":"running","Health":"unknown"}\n'
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=["web\nworker\n", rows]):
            result = hosting_cmd._host_runtime_status(
                validated, {"provisioned": True}, "myvps", state,
            )
        self.assertEqual(result["deployed_revision"], "a" * 40)
        self.assertEqual(result["health"], {"state": "ready"})
        self.assertEqual([item["service"] for item in result["services"]], ["web", "worker"])
        self.assertEqual(result["topology"], {
            "state": "ready",
            "declared_services": ["web", "worker"],
            "compose_services": ["web", "worker"],
            "running_services": ["web", "worker"],
            "missing_from_compose": [],
            "missing_from_runtime": [],
        })

    def test_host_status_includes_profiled_declared_services_without_counting_init_or_db(self):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      init_services: [migrate]\n"
            "      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        rows = '{"Service":"web","State":"running","Health":"healthy"}\n' \
            '{"Service":"worker","State":"running","Health":"healthy"}\n' \
            '{"Service":"db","State":"running","Health":"healthy"}\n'
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=[
                 "web\nworker\nmigrate\ndb\n", rows,
             ]) as checked:
            result = hosting_cmd._host_runtime_status(
                validated, {"provisioned": True}, "myvps", {"version": 1, "hosts": {}},
            )
        self.assertIn("--profile '*' config --services", checked.call_args_list[0].args[1])
        self.assertEqual(result["health"], {"state": "ready"})
        self.assertEqual(result["topology"]["compose_services"], ["web", "worker"])
        self.assertEqual(result["topology"]["running_services"], ["web", "worker"])
        self.assertNotIn("migrate", result["topology"]["compose_services"])
        self.assertNotIn("db", result["topology"]["running_services"])

    def test_host_status_marks_manifest_service_missing_from_compose_as_degraded(self):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        rows = '{"Service":"web","State":"running","Health":"healthy"}\n'
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=["web\n", rows]):
            result = hosting_cmd._host_runtime_status(
                validated, {"provisioned": True}, "myvps", {"version": 1, "hosts": {}},
            )
        self.assertEqual(result["health"]["state"], "degraded")
        self.assertEqual(result["topology"]["state"], "degraded")
        self.assertEqual(result["topology"]["missing_from_compose"], ["worker"])
        self.assertEqual(result["topology"]["missing_from_runtime"], ["worker"])

    def test_host_status_marks_compose_service_without_runtime_row_as_degraded(self):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)
        rows = '{"Service":"web","State":"running","Health":"healthy"}\n'
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=["web\nworker\n", rows]):
            result = hosting_cmd._host_runtime_status(
                validated, {"provisioned": True}, "myvps", {"version": 1, "hosts": {}},
            )
        self.assertEqual(result["health"]["state"], "degraded")
        self.assertEqual(result["topology"]["missing_from_compose"], [])
        self.assertEqual(result["topology"]["missing_from_runtime"], ["worker"])

    def test_host_status_fails_closed_to_unavailable_without_mutation(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        state = {"version": 1, "hosts": {}}
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_checked",
                          side_effect=RuntimeError("ssh unavailable")):
            result = hosting_cmd._host_runtime_status(
                validated, {"provisioned": True}, "myvps", state,
            )
        self.assertEqual(result["health"]["state"], "unavailable")
        self.assertIn("ssh unavailable", result["health"]["reason"])
        self.assertEqual(state["hosts"], {})

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_caddy_apply_is_locked_digest_aware_and_phase_bounded(self, remote_checked, write):
        desired = "example.test { reverse_proxy 127.0.0.1:18001 }\n"
        digest = hashlib.sha256(desired.encode()).hexdigest()
        remote_checked.return_value = (
            f"[Sandbox] caddy phase=digest state=changed digest={digest}\n"
            "[Sandbox] caddy phase=validate state=passed\n"
            "[Sandbox] caddy phase=reload state=passed\n"
            "[Sandbox] caddy phase=observe state=active\n"
        )

        receipt = hosting_cmd._configure_host_caddy(
            {}, "sandbox-host-example-site-production", desired,
            log_path="/srv/runtime/apply.log",
        )

        self.assertEqual(receipt, {"state": "changed", "digest": digest})
        write.assert_called_once()
        command = remote_checked.call_args.args[1]
        self.assertIn("flock -w 30", command)
        self.assertIn("sha256sum", command)
        self.assertIn("timeout 30 caddy validate", command)
        self.assertIn("timeout 30 systemctl reload caddy", command)
        self.assertIn("timeout 30 systemctl is-active --quiet caddy", command)
        self.assertIn("phase noop unchanged", command)
        self.assertEqual(remote_checked.call_args.kwargs["timeout"], 150)
        self.assertEqual(remote_checked.call_args.kwargs["log_path"], "/srv/runtime/apply.log")
        syntax = subprocess.run(
            ["sh", "-n", "-c", command], capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_caddy_digest_noop_returns_unchanged_receipt(self, remote_checked, _write):
        desired = "example.test { reverse_proxy 127.0.0.1:18001 }\n"
        digest = hashlib.sha256(desired.encode()).hexdigest()
        remote_checked.return_value = (
            f"[Sandbox] caddy phase=digest state=unchanged digest={digest}\n"
            f"[Sandbox] caddy phase=noop state=unchanged digest={digest}\n"
            f"[Sandbox] caddy phase=observe state=active digest={digest}\n"
        )
        receipt = hosting_cmd._configure_host_caddy(
            {}, "sandbox-host-example-site-production", desired,
        )
        self.assertEqual(receipt, {"state": "unchanged", "digest": digest})

    @patch("sandbox.commands.hosting._write_remote_text")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_caddy_restore_reports_exact_rollback_state(self, remote_checked, _write):
        previous = "example.test { reverse_proxy 127.0.0.1:18000 }\n"
        digest = hashlib.sha256(previous.encode()).hexdigest()
        remote_checked.return_value = (
            f"[Sandbox] caddy phase=rollback state=rollback_complete digest={digest}\n"
        )
        receipt = hosting_cmd._restore_host_caddy(
            {}, "sandbox-host-example-site-production", previous,
            log_path="/srv/runtime/apply.log",
        )
        self.assertEqual(receipt, {"state": "rollback_complete", "digest": digest})

        remote_checked.side_effect = RuntimeError("reload timed out")
        with self.assertRaisesRegex(hosting.HostingError, "rollback_incomplete.*reload timed out"):
            hosting_cmd._restore_host_caddy(
                {}, "sandbox-host-example-site-production", previous,
                log_path="/srv/runtime/apply.log",
            )

    def test_host_diagnose_combines_disk_images_and_source_revision_evidence(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        revision = "a" * 40
        state = {"version": 1, "hosts": {
            hosting.state_key("myvps", validated): {"commit": revision},
        }}
        status = {
            "project": "example-site", "environment": "production", "remote": "myvps",
            "deployed_revision": revision, "state_record": "present",
            "services": [{"service": "web", "state": "running", "health": "healthy"}],
            "health": {"state": "ready"},
        }
        images = '{"Service":"web","Image":"example:web","ID":"sha256:1","Created":"now"}\n'
        with patch.object(hosting_cmd, "_host_runtime_status", return_value=status), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_remote_disk_free_mb", return_value=4096), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=[images, revision]):
            result = hosting_cmd._host_runtime_diagnose(
                validated, {"provisioned": True}, "myvps", state,
            )
        self.assertEqual(result["disk"], {"state": "ready", "free_mb": 4096})
        self.assertEqual(result["image_state"], {"state": "ready"})
        self.assertEqual(result["source_revision"]["state"], "ready")
        self.assertEqual(result["source_revision"]["checks"][0]["state"], "match")
        self.assertTrue(result["apply_log"].endswith("/apply.log"))

    @patch("sandbox.commands.hosting.info")
    @patch("sandbox.commands.hosting._remote_checked")
    def test_stale_buildkit_snapshot_recovers_with_a_no_cache_rebuild(self, remote_checked, _info):
        stale = RuntimeError(
            "target worker: failed to solve: failed to commit abc to def during finalize: "
            "failed to stat active key during commit: snapshot abc does not exist: not found"
        )
        remote_checked.side_effect = [stale, "", ""]
        hosting_cmd._build_checked({}, "compose", "compose up -d --build web", "web")
        commands = [call.args[1] for call in remote_checked.call_args_list]
        self.assertEqual(commands[1], "compose build --no-cache web")
        self.assertEqual(commands[2], "compose up -d --build web")

    @patch("sandbox.commands.hosting._remote_checked")
    def test_unrelated_build_failure_is_not_retried(self, remote_checked):
        remote_checked.side_effect = RuntimeError("failed to solve: dockerfile parse error")
        with self.assertRaisesRegex(RuntimeError, "dockerfile parse error"):
            hosting_cmd._build_checked({}, "compose", "compose up -d --build web", "web")
        self.assertEqual(remote_checked.call_count, 1)

    @patch("sandbox.commands.hosting.remote.resolve_sandbox_home", return_value="/srv/sandbox")
    @patch("sandbox.commands.hosting._remote_checked",
           side_effect=["web\nworker\n", "web | ready\nworker | polling\n"])
    def test_reads_bounded_logs_for_all_declared_host_services(self, remote_checked, _resolve_home):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)

        output = hosting_cmd._read_host_logs(validated, {}, lines=75)

        self.assertEqual(output, "web | ready\nworker | polling\n")
        command = remote_checked.call_args.args[1]
        self.assertIn("docker compose", command)
        self.assertIn("-p sandbox-host-example-site-production", command)
        self.assertIn("logs --no-color --tail 75 web worker", command)

    @patch("sandbox.commands.hosting.remote.resolve_sandbox_home", return_value="/srv/sandbox")
    @patch("sandbox.commands.hosting._remote_checked",
           side_effect=["web\n", "web | ready\n"])
    def test_reads_present_logs_and_reports_missing_declared_service(self, remote_checked, _resolve_home):
        manifest = _manifest().replace(
            "      service: web\n",
            "      service: web\n      background_services: [worker]\n",
        )
        with self._write(manifest) as directory:
            validated = hosting.validate_manifest(directory)

        output = hosting_cmd._read_host_logs(validated, {}, lines=50)

        self.assertIn("[missing service: worker]", output)
        self.assertIn("web | ready", output)
        commands = [call.args[1] for call in remote_checked.call_args_list]
        self.assertIn("config --services", commands[0])
        self.assertIn("logs --no-color --tail 50 web", commands[1])

    def test_state_round_trip_is_atomic_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosts.json"
            state = {"version": 1, "hosts": {"myvps/example/production": {"loopback_port": 18001}}}
            hosting.save_host_state(state, path)
            self.assertEqual(hosting.load_host_state(path), state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_disk_free_prefers_authenticated_diagnostics(self):
        entry = {"control_url": "https://control.example.test", "bearer_token": "token"}
        with patch.object(hosting_cmd.remote, "remote_diagnostics", return_value={"disk_free_mb": 4096}) as diagnostics, \
             patch.object(hosting_cmd.remote, "ssh_run") as ssh:
            self.assertEqual(hosting_cmd._remote_disk_free_mb(entry, "/srv/sandbox"), 4096)
        diagnostics.assert_called_once_with(entry)
        ssh.assert_not_called()

    def test_remote_disk_free_falls_back_to_registered_ssh(self):
        entry = {"ssh": "alim@example.test"}
        result = subprocess.CompletedProcess([], 0, stdout="2048\n", stderr="")
        with patch.object(hosting_cmd.remote, "ssh_run", return_value=result) as ssh:
            self.assertEqual(hosting_cmd._remote_disk_free_mb(entry, "/srv/sandbox"), 2048)
        self.assertIn("df -Pm /srv/sandbox", ssh.call_args.args[1])

    def test_host_apply_disk_preflight_rejects_before_reservation(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        entry = {"ssh": "alim@example.test"}
        with patch.object(hosting_cmd, "_remote_disk_free_mb", return_value=1055), \
             patch.object(hosting_cmd, "_remote_checked") as checked:
            with self.assertRaisesRegex(hosting.HostingError, "1056 MiB"):
                hosting_cmd._prepare_host_apply(entry, "/srv/sandbox", validated)
        checked.assert_not_called()

    def test_host_apply_disk_preflight_reserves_bounded_rollback_space(self):
        with self._write(_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        entry = {"ssh": "alim@example.test"}
        with patch.object(hosting_cmd, "_remote_disk_free_mb", return_value=1056), \
             patch.object(hosting_cmd, "_remote_checked", return_value="") as checked:
            reservation = hosting_cmd._prepare_host_apply(entry, "/srv/sandbox", validated)
        self.assertEqual(
            reservation,
            "/srv/sandbox/.sandbox/host-apply-example-site-production.rollback.reserve",
        )
        command = checked.call_args.args[1]
        self.assertIn("fallocate -l 32M", command)
        self.assertIn("chmod 0600", command)

    def test_failed_apply_calls_rollback(self):
        events = []

        def apply():
            events.append("apply")
            raise RuntimeError("remote rejected Caddy config")

        with self.assertRaisesRegex(RuntimeError, "remote rejected"):
            hosting.apply_with_rollback(apply, lambda: events.append("rollback"))
        self.assertEqual(events, ["apply", "rollback"])

    def test_failed_apply_continues_all_rollback_steps_and_reports_every_failure(self):
        events = []

        def apply():
            raise RuntimeError("Caddy validation failed")

        def failed_dns_restore():
            events.append("dns")
            raise RuntimeError("DNS API unavailable")

        def failed_caddy_restore():
            events.append("caddy")
            raise RuntimeError("Caddy reload unavailable")

        with self.assertRaisesRegex(hosting.HostingError, "Caddy validation failed.*DNS API unavailable.*Caddy reload unavailable"):
            hosting.apply_with_rollback(apply, [failed_dns_restore, failed_caddy_restore])
        self.assertEqual(events, ["dns", "caddy"])

    def test_apply_rejects_disallowed_branch_before_reading_remote_state(self):
        args = types.SimpleNamespace(
            action="apply", project_dir=None, environment=None,
            remote="not-configured", confirm=True, json=True,
            allow_zone_ssl_change=False,
        )
        validated = {
            "project_root": "/tmp/example-site",
            "project": "example-site",
            "environment": "production",
            "deploy": {"allowed_branches": ["main"], "require_clean": True},
        }
        clean = subprocess.CompletedProcess(
            ["git", "status", "--porcelain"], 0, stdout="", stderr=""
        )

        with patch.object(hosting_cmd.hosting, "validate_manifest", return_value=validated), \
             patch.object(hosting_cmd.remote, "current_branch", return_value="dev"), \
             patch.object(hosting_cmd.remote, "get_remote") as get_remote, \
             patch.object(hosting_cmd.subprocess, "run", return_value=clean), \
             patch.object(hosting_cmd, "die", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                hosting_cmd.cmd_host(None, args)

        get_remote.assert_not_called()

    def test_apply_rejects_dirty_source_before_remote_push_for_derived_environment(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        dirty = subprocess.CompletedProcess(
            ["git", "status", "--porcelain"], 0,
            stdout=" M sandbox.hosting.yml\n", stderr="",
        )
        with patch.object(hosting_cmd.remote, "current_branch", return_value="main"), \
             patch.object(hosting_cmd.subprocess, "run", return_value=dirty), \
             patch.object(hosting_cmd.remote, "push_commits") as push:
            with self.assertRaisesRegex(hosting.HostingError, "requires a clean working tree"):
                hosting_cmd._validate_apply_source(validated)
        push.assert_not_called()

    def test_host_plan_reports_only_unresolved_derived_environment_metadata(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        args = types.SimpleNamespace(
            action="plan", project_dir=directory, environment=None,
            remote="myvps", confirm=False, json=True,
            allow_zone_ssl_change=False,
        )
        entry = {"provisioned": True, "origin_ipv4": "203.0.113.10", "origin_ipv6": None}
        with patch.object(hosting_cmd.hosting, "validate_manifest", return_value=validated), \
             patch.object(hosting_cmd.remote, "get_remote", return_value=entry), \
             patch.object(hosting_cmd.hosting, "load_host_state", return_value={"version": 1, "hosts": {}}), \
             patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_cmd, "_declared_secret_sources", return_value=set()), \
             patch.object(hosting_cmd, "_cloudflare_drift", return_value={"configured": False}), \
             patch("builtins.print") as printed:
            hosting_cmd.cmd_host(None, args)
        plan = json.loads(printed.call_args.args[0])
        self.assertEqual(plan["runtime"]["derived_environment"], [{
            "key": "LENZORA_SOURCE_REVISION",
            "provider": "pushed_commit_sha",
            "resolved_at_apply": True,
        }])
        self.assertNotIn("commit", plan["runtime"])
        self.assertNotIn("value", plan["runtime"]["derived_environment"][0])

    def test_host_validate_all_emits_one_result_per_environment(self):
        with self._write(_manifest_with_environments(["production", "development"])) as directory:
            args = types.SimpleNamespace(
                action="validate", project_dir=directory, environment=None,
                all=True, json=True,
            )
            with patch("builtins.print") as printed:
                hosting_cmd.cmd_host(None, args)

        payload = json.loads(printed.call_args.args[0])
        self.assertTrue(payload["ok"])
        self.assertEqual(
            [item["environment"] for item in payload["environments"]],
            ["development", "production"],
        )
        self.assertTrue(all(item["ok"] for item in payload["environments"]))
    def test_host_apply_json_returns_sanitized_revision_evidence(self):
        revision = "d" * 40
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        args = types.SimpleNamespace(
            action="apply", project_dir=directory, environment=None,
            remote="myvps", confirm=True, json=True,
            allow_zone_ssl_change=False,
        )
        entry = {"provisioned": True, "origin_ipv4": "203.0.113.10", "origin_ipv6": None}
        apply_result = {
            "commit": revision,
            "derived_environment": [{
                "key": "LENZORA_SOURCE_REVISION",
                "provider": "pushed_commit_sha",
                "resolved_at_apply": True,
            }],
        }
        with patch.object(hosting_cmd.hosting, "validate_manifest", return_value=validated), \
             patch.object(hosting_cmd, "_validate_apply_source", return_value="main"), \
             patch.object(hosting_cmd.remote, "get_remote", return_value=entry), \
             patch.object(hosting_cmd.hosting, "load_host_state", return_value={"version": 1, "hosts": {}}), \
             patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_cmd, "_declared_secret_sources", return_value=set()), \
             patch.object(hosting_cmd, "_cloudflare_drift", return_value={"configured": False}), \
             patch.object(hosting_cmd, "_apply_host", return_value=apply_result), \
             patch("builtins.print") as printed:
            hosting_cmd.cmd_host(None, args)
        evidence = json.loads(printed.call_args.args[0])
        self.assertEqual(evidence, {
            "ok": True,
            "project": "example-site",
            "environment": "production",
            "remote": "myvps",
            "remote_selection": "explicit",
            **apply_result,
        })
        self.assertNotIn("runtime", evidence)
        self.assertNotIn("environment.env", json.dumps(evidence))

    def test_apply_derives_revision_from_nested_push_result_before_reset_and_compose(self):
        revision = "c" * 40
        events = []
        with self._write(_public_acme_manifest()) as directory:
            manifest = Path(directory) / "sandbox.hosting.yml"
            manifest.write_text(_public_acme_manifest().replace(
                "      require_clean: true\n",
                "      require_clean: true\n"
                "      derived_environment:\n"
                "        LENZORA_SOURCE_REVISION: pushed_commit_sha\n",
            ))
            validated = hosting.validate_manifest(directory)
        validated["source_root_nested"] = True
        validated["source_root"] = "/checkout/nested-source"
        validated["manifest_root"] = "/checkout"
        runtime = hosting.desired_runtime(validated, "myvps")
        runtime["records"] = hosting.desired_plan(
            validated, "203.0.113.10",
        )["records"]
        state = {"version": 1, "hosts": {}}
        client = MagicMock()
        client.records.return_value = []
        client.upsert_address.return_value = {"id": "record-1"}
        original_render = hosting.render_env_file

        def push(*_args, **_kwargs):
            events.append("push")
            return revision

        def render(*args, **kwargs):
            events.append("render")
            return original_render(*args, **kwargs)

        def resolve_secrets(*_args):
            events.append("secrets")
            return {}, []

        with patch.object(hosting_cmd, "_secret_status", side_effect=resolve_secrets), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_ensure_host_source", return_value="/srv/source"), \
             patch.object(hosting_cmd.remote, "push_commits", side_effect=push) as pushed, \
             patch.object(hosting_cmd.hosting, "render_env_file", side_effect=render), \
             patch.object(hosting_cmd.cloudflare, "Client", return_value=client), \
             patch.object(hosting_cmd.remote, "reset_target_to", side_effect=lambda *_: events.append("reset")), \
             patch.object(hosting_cmd.remote, "capture_uncommitted", side_effect=lambda *_: (events.append("capture") or ("", []))), \
             patch.object(hosting_cmd.remote, "apply_uncommitted") as apply_overlay, \
             patch.object(hosting_cmd, "_read_remote_optional", return_value=None), \
             patch.object(hosting_cmd, "_run_compose", side_effect=lambda *_: events.append("compose")) as compose, \
             patch.object(hosting_cmd, "_verify_remote_health"), \
             patch.object(hosting_cmd, "_verify_remote_derived_environment"), \
             patch.object(hosting_cmd, "_configure_host_caddy"), \
             patch.object(hosting_cmd, "_verify_edge"), \
             patch.object(hosting_cmd.hosting, "save_host_state"):
            result = hosting_cmd._apply_host(
                validated, {}, "myvps", runtime, state, False, "main",
            )

        self.assertEqual(events[:6], ["secrets", "push", "capture", "render", "reset", "compose"])
        apply_overlay.assert_not_called()
        self.assertEqual(
            pushed.call_args.kwargs["source_root"], "/checkout/nested-source",
        )
        self.assertIn(f"LENZORA_SOURCE_REVISION={revision}\n", compose.call_args.args[4]["environment"])
        self.assertEqual(result, {
            "commit": revision,
            "derived_environment": [{
                "key": "LENZORA_SOURCE_REVISION",
                "provider": "pushed_commit_sha",
                "resolved_at_apply": True,
            }],
        })
        self.assertNotIn("environment", result)

    def test_invalid_derived_push_sha_fails_before_reset_compose_or_state(self):
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        state = {"version": 1, "hosts": {}}
        with patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_ensure_host_source", return_value="/srv/source"), \
             patch.object(hosting_cmd.remote, "push_commits", return_value="abc123"), \
             patch.object(hosting_cmd.remote, "capture_uncommitted", return_value=("", [])), \
             patch.object(hosting_cmd.remote, "reset_target_to") as reset, \
             patch.object(hosting_cmd, "_run_compose") as compose, \
             patch.object(hosting_cmd.hosting, "save_host_state") as save_state:
            with self.assertRaisesRegex(hosting.HostingError, "lowercase 40-hex"):
                hosting_cmd._apply_host(
                    validated, {}, "myvps", runtime, state, False, "main",
                )
        reset.assert_not_called()
        compose.assert_not_called()
        save_state.assert_not_called()
        self.assertEqual(state, {"version": 1, "hosts": {}})

    def test_post_push_dirty_clean_source_fails_before_remote_mutation_or_state(self):
        revision = "d" * 40
        with self._write(_manifest_with_derived_revision()) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        state = {"version": 1, "hosts": {}}
        with patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_ensure_host_source", return_value="/srv/source"), \
             patch.object(hosting_cmd.remote, "push_commits", return_value=revision), \
             patch.object(hosting_cmd.remote, "capture_uncommitted", return_value=("diff --git a/x b/x\n", ["new.txt"])), \
             patch.object(hosting_cmd.hosting, "render_env_file") as render, \
             patch.object(hosting_cmd.remote, "reset_target_to") as reset, \
             patch.object(hosting_cmd.remote, "apply_uncommitted") as apply_overlay, \
             patch.object(hosting_cmd, "_run_compose") as compose, \
             patch.object(hosting_cmd.hosting, "save_host_state") as save_state:
            with self.assertRaisesRegex(hosting.HostingError, "changed while the source was being pushed"):
                hosting_cmd._apply_host(
                    validated, {}, "myvps", runtime, state, False, "main",
                )

        render.assert_not_called()
        reset.assert_not_called()
        apply_overlay.assert_not_called()
        compose.assert_not_called()
        save_state.assert_not_called()
        self.assertEqual(state, {"version": 1, "hosts": {}})

    def test_dirty_allowed_source_preserves_post_push_overlay(self):
        revision = "e" * 40
        with self._write(_public_acme_manifest().replace(
            "      require_clean: true\n", "      require_clean: false\n",
        )) as directory:
            validated = hosting.validate_manifest(directory)
        runtime = hosting.desired_runtime(validated, "myvps")
        runtime["records"] = hosting.desired_plan(
            validated, "203.0.113.10",
        )["records"]
        state = {"version": 1, "hosts": {}}
        client = MagicMock()
        client.records.return_value = []
        client.upsert_address.return_value = {"id": "record-1"}
        overlay = ("diff --git a/x b/x\n", ["new.txt"])

        with patch.object(hosting_cmd, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_ensure_host_source", return_value="/srv/source"), \
             patch.object(hosting_cmd.remote, "push_commits", return_value=revision), \
             patch.object(hosting_cmd.remote, "capture_uncommitted", return_value=overlay), \
             patch.object(hosting_cmd.cloudflare, "Client", return_value=client), \
             patch.object(hosting_cmd.remote, "reset_target_to"), \
             patch.object(hosting_cmd.remote, "apply_uncommitted") as apply_overlay, \
             patch.object(hosting_cmd, "_read_remote_optional", return_value=None), \
             patch.object(hosting_cmd, "_run_compose"), \
             patch.object(hosting_cmd, "_verify_remote_health"), \
             patch.object(hosting_cmd, "_configure_host_caddy"), \
             patch.object(hosting_cmd, "_verify_edge"), \
             patch.object(hosting_cmd.hosting, "save_host_state"):
            hosting_cmd._apply_host(
                validated, {}, "myvps", runtime, state, False, "main",
            )

        apply_overlay.assert_called_once_with(
            {}, "/srv/source", validated["project_root"], overlay[0], overlay[1],
        )

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

    @patch("sandbox.commands.hosting.hosting.save_host_state")
    @patch("sandbox.commands.hosting._verify_edge")
    @patch("sandbox.commands.hosting._configure_host_caddy")
    @patch("sandbox.commands.hosting._verify_remote_health")
    @patch("sandbox.commands.hosting._run_compose")
    @patch("sandbox.commands.hosting._read_remote_optional", return_value=None)
    @patch("sandbox.commands.hosting._origin_certificate")
    @patch("sandbox.commands.hosting.remote.apply_uncommitted")
    @patch("sandbox.commands.hosting.remote.capture_uncommitted", return_value=("", []))
    @patch("sandbox.commands.hosting.remote.reset_target_to")
    @patch("sandbox.commands.hosting.remote.push_commits", return_value="abc123")
    @patch("sandbox.commands.hosting.remote.resolve_sandbox_home", return_value="/srv/sandbox")
    @patch("sandbox.commands.hosting._ensure_host_source", return_value="/srv/sandbox/deploy-src/hosts/example-site")
    @patch("sandbox.commands.hosting.cloudflare.Client")
    def test_public_acme_apply_skips_origin_ca_and_creates_dns_only_records(
        self, client_type, _source, _home, _push, _reset, _capture, _uncommitted,
        origin_certificate, _read_caddy, _compose, _health, configure_caddy,
        verify_edge, save_state,
    ):
        client = client_type.return_value
        client.zone.return_value = {"id": "zone-1", "name": "example.test"}
        client.records.return_value = []
        client.upsert_address.side_effect = [
            {"id": "record-1"}, {"id": "record-2"},
        ]
        with TestHostingManifest()._write(_public_acme_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        state = {"version": 1, "hosts": {}}
        runtime = hosting.desired_runtime(validated, "myvps", state)
        runtime["records"] = hosting.desired_plan(validated, "203.0.113.10")["records"]

        hosting_cmd._apply_host(validated, {}, "myvps", runtime, state, False, "main")

        origin_certificate.assert_not_called()
        client.current_ssl_mode.assert_not_called()
        client.ssl_mode.assert_not_called()
        self.assertTrue(client.upsert_address.call_count)
        self.assertTrue(all(call.kwargs["proxied"] is False for call in client.upsert_address.call_args_list))
        self.assertNotIn("    tls ", configure_caddy.call_args.args[2])
        verify_edge.assert_called_once_with(
            validated["routes"],
            healthcheck_path="/",
            basic_auth_enabled=False,
        )
        save_state.assert_called_once()

    @patch("sandbox.commands.hosting.cloudflare.cloudflare_token", return_value="configured")
    @patch("sandbox.commands.hosting.cloudflare.Client")
    def test_public_acme_drift_does_not_accept_redirect_cname_as_origin_route(
        self, client_type, _token,
    ):
        client = client_type.return_value
        client.zone.return_value = {"id": "zone-1", "name": "example.test"}
        client.records.return_value = [{
            "id": "cname-1", "type": "CNAME", "name": "old.example.test",
            "content": "target.example.test", "proxied": False,
        }]
        plan = {
            "records": [{
                "hostname": "old.example.test", "address": "203.0.113.10",
                "proxied": False, "mode": "redirect",
                "target": "https://target.example.test",
            }],
        }

        drift = hosting_cmd._cloudflare_drift(plan)

        self.assertFalse(drift["records"][0]["exists"])
        client.current_ssl_mode.assert_not_called()

    @patch("sandbox.commands.hosting.hosting.save_host_state")
    @patch("sandbox.commands.hosting._restore_host_caddy")
    @patch("sandbox.commands.hosting._verify_edge", side_effect=RuntimeError("certificate pending"))
    @patch("sandbox.commands.hosting._configure_host_caddy")
    @patch("sandbox.commands.hosting._verify_remote_health")
    @patch("sandbox.commands.hosting._run_compose")
    @patch("sandbox.commands.hosting._read_remote_optional", return_value="old caddy")
    @patch("sandbox.commands.hosting._origin_certificate")
    @patch("sandbox.commands.hosting.remote.apply_uncommitted")
    @patch("sandbox.commands.hosting.remote.capture_uncommitted", return_value=("", []))
    @patch("sandbox.commands.hosting.remote.reset_target_to")
    @patch("sandbox.commands.hosting.remote.push_commits", return_value="abc123")
    @patch("sandbox.commands.hosting.remote.resolve_sandbox_home", return_value="/srv/sandbox")
    @patch("sandbox.commands.hosting._ensure_host_source", return_value="/srv/sandbox/deploy-src/hosts/example-site")
    @patch("sandbox.commands.hosting.cloudflare.Client")
    def test_public_acme_apply_restores_dns_and_caddy_when_certificate_verification_fails(
        self, client_type, _source, _home, _push, _reset, _capture, _uncommitted,
        origin_certificate, _read_caddy, _compose, _health, _configure,
        _verify_edge, restore_caddy, save_state,
    ):
        client = client_type.return_value
        client.zone.return_value = {"id": "zone-1", "name": "example.test"}
        previous = [
            {"id": "record-1", "type": "A", "name": "example-1.test",
             "content": "192.0.2.1", "proxied": True, "ttl": 1},
            {"id": "record-2", "type": "A", "name": "example-2.test",
             "content": "192.0.2.1", "proxied": True, "ttl": 1},
        ]
        client.records.side_effect = [[previous[0]], [previous[1]]]
        client.upsert_address.side_effect = [{"id": "record-1"}, {"id": "record-2"}]
        with TestHostingManifest()._write(_public_acme_manifest()) as directory:
            validated = hosting.validate_manifest(directory)
        state = {"version": 1, "hosts": {}}
        runtime = hosting.desired_runtime(validated, "myvps", state)
        runtime["records"] = hosting.desired_plan(validated, "203.0.113.10")["records"]

        with self.assertRaisesRegex(RuntimeError, "certificate pending"):
            hosting_cmd._apply_host(validated, {}, "myvps", runtime, state, False, "main")

        origin_certificate.assert_not_called()
        self.assertEqual(client.restore_record.call_count, 2)
        restored = [call.args[1] for call in client.restore_record.call_args_list]
        self.assertEqual(restored, list(reversed(previous)))
        restore_caddy.assert_called_once_with(
            {}, "sandbox-host-example-site-production", "old caddy",
            log_path="/srv/sandbox/runtime/hosts/example-site/production/apply.log",
        )
        save_state.assert_not_called()

    @patch("sandbox.commands.hosting.urllib.request.build_opener")
    def test_basic_auth_edge_probe_streams_credentials_only_in_memory(self, build_opener):
        opener = build_opener.return_value
        challenge = urllib.error.HTTPError(
            "https://example.test/", 401, "Unauthorized", {}, io.BytesIO(),
        )
        authenticated = MagicMock(status=200)
        authenticated.__enter__.return_value = authenticated
        opener.open.side_effect = [challenge, authenticated]
        secret = "do-not-print-this-secret"
        hosting_cmd._verify_edge(
            [{"hostname": "example.test", "mode": "serve"}],
            basic_auth_enabled=True,
            basic_auth_credentials=("operator", secret),
        )
        authenticated_request = opener.open.call_args_list[1].args[0]
        self.assertTrue(authenticated_request.get_header("Authorization").startswith("Basic "))
        self.assertNotIn(secret, authenticated_request.full_url)
        self.assertNotIn(secret, repr(authenticated_request))


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
    def test_account_scoped_discovery_lists_tunnels_and_access_apps(self, mock_open):
        mock_open.side_effect = [
            _Response({"success": True, "result": [{"id": "tunnel-1"}]}),
            _Response({"success": True, "result": [{"id": "app-1"}]}),
        ]
        client = cloudflare.Client("token")
        self.assertEqual(client.tunnels("account-1"), [{"id": "tunnel-1"}])
        self.assertEqual(client.access_applications("account-1"), [{"id": "app-1"}])
        tunnel_request, access_request = [call.args[0] for call in mock_open.call_args_list]
        self.assertTrue(tunnel_request.full_url.endswith("/accounts/account-1/cfd_tunnel"))
        self.assertTrue(access_request.full_url.endswith("/accounts/account-1/access/apps"))
        self.assertNotIn("token", tunnel_request.full_url)

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
    def test_preview_transfers_ignored_descriptor_before_remote_ensure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sandbox.config.json").write_text(
                '{"slug":"demo","plugins":{"demo":"."}}'
            )
            args = types.SimpleNamespace(
                action="create", json=True, confirm=True, ttl_hours=24,
                remote="preview", project_dir=str(root), name=None,
                base_domain="sandbox.asb.bd",
            )
            config_core = MagicMock()
            config_core.load_project_config.return_value = {
                "root": str(root), "slug": "demo",
            }
            entry = {"provisioned": True, "origin_ipv4": "203.0.113.10"}
            with patch.object(preview, "_load_state",
                              return_value={"version": 1, "previews": {}}), \
                 patch.object(preview.core, "_core", return_value=config_core), \
                 patch.object(preview.remote, "get_remote", return_value=entry), \
                 patch.object(preview, "preflight_project_capability",
                              return_value=None), \
                 patch.object(preview.remote, "current_branch", return_value="latest"), \
                 patch.object(preview, "preview_identity",
                              return_value=("preview-id", "preview-label")), \
                 patch.object(preview.remote, "ensure_deploy_repo",
                              return_value="/srv/demo"), \
                 patch.object(preview.remote, "push_commits", return_value="abc123"), \
                 patch.object(preview.remote, "reset_target_to"), \
                 patch.object(preview.remote, "capture_uncommitted",
                              return_value=("", [])), \
                 patch.object(preview.remote, "apply_uncommitted") as overlay, \
                 patch.object(preview.remote, "ensure_remote_instance",
                              side_effect=RuntimeError("stop after overlay")), \
                 patch.object(preview.remote, "delete_remote_instance_for_label"), \
                 patch("builtins.print"):
                with self.assertRaises(SystemExit):
                    preview.cmd_preview(None, args)
            overlay.assert_called_once_with(
                entry, "/srv/demo", root, "", ["sandbox.config.json"],
            )

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

    def test_preview_failure_json_always_names_label_and_instance(self):
        args = types.SimpleNamespace(
            action="create", json=True, confirm=True, ttl_hours=24,
            remote="preview", project_dir="/tmp/project", name=None,
            base_domain="sandbox.asb.bd",
        )
        config_core = MagicMock()
        config_core.load_project_config.return_value = {
            "root": "/tmp/project", "slug": "demo",
        }
        instance = {"instance": "preview-demo", "wordpress_port": 8188}
        output = io.StringIO()
        with patch.object(preview, "_load_state",
                          return_value={"version": 1, "previews": {}}), \
             patch.object(preview.core, "_core", return_value=config_core), \
             patch.object(preview.remote, "get_remote", return_value={
                 "provisioned": True, "origin_ipv4": "203.0.113.10"}), \
             patch.object(preview, "preflight_project_capability", return_value=None), \
             patch.object(preview.remote, "current_branch", return_value="latest"), \
             patch.object(preview, "preview_identity",
                          return_value=("preview-id", "preview-label")), \
             patch.object(preview.remote, "ensure_deploy_repo",
                          return_value="/srv/demo"), \
             patch.object(preview.remote, "push_commits", return_value="abc123"), \
             patch.object(preview.remote, "reset_target_to"), \
             patch.object(preview.remote, "capture_uncommitted", return_value=("", [])), \
             patch.object(preview.remote, "apply_uncommitted"), \
             patch.object(preview.remote, "ensure_remote_instance",
                          return_value=instance), \
             patch.object(preview.remote, "reconcile_remote_instance",
                          side_effect=RuntimeError("apply failed")), \
             patch.object(preview.remote, "delete_remote_instance"), \
             redirect_stdout(output):
            with self.assertRaises(SystemExit):
                preview.cmd_preview(None, args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["preview"], {
            "label": "preview-label", "instance": "preview-demo",
        })

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
             patch.object(preview.remote, "reconcile_remote_instance",
                          return_value=instance) as reconciled, \
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
        reconciled.assert_called_once_with(
            {"provisioned": True, "origin_ipv4": "203.0.113.10"},
            "/srv/demo", ANY,
        )

    def test_preview_identity_is_stable_and_namespaced(self):
        first = preview.preview_identity("/tmp/example", "fix/login", "login")
        second = preview.preview_identity("/tmp/example", "fix/login", "login")
        self.assertEqual(first, second)
        self.assertTrue(first[0].startswith("login-"))
        self.assertTrue(first[1].startswith("preview-"))

    def test_preview_human_success_names_instance_and_label(self):
        message = preview._ready_message({
            "url": "https://preview.example.test",
            "instance": "preview-demo",
            "expires_at": "2026-08-26T00:00:00+00:00",
        }, "preview-label")
        self.assertIn("instance preview-demo", message)
        self.assertIn("label preview-label", message)

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

    def test_renders_exact_pushed_revision_and_ignores_hostile_process_environment(self):
        revision = "a" * 40
        with TestHostingManifest()._write(_manifest_with_derived_revision()) as project:
            validated = hosting.validate_manifest(project)
        with patch.dict("os.environ", {"LENZORA_SOURCE_REVISION": "b" * 40}):
            rendered = hosting.render_env_file(
                validated, {}, pushed_commit_sha=revision,
            )
        self.assertIn(f"LENZORA_SOURCE_REVISION={revision}\n", rendered)
        self.assertNotIn("b" * 40, rendered)

    def test_rejects_missing_malformed_or_noncanonical_pushed_revision(self):
        with TestHostingManifest()._write(_manifest_with_derived_revision()) as project:
            validated = hosting.validate_manifest(project)
        for revision in (None, "abc123", "A" * 40, "a" * 39, "a" * 41):
            with self.subTest(revision=revision):
                with self.assertRaisesRegex(hosting.HostingError, "lowercase 40-hex"):
                    hosting.render_env_file(
                        validated, {}, pushed_commit_sha=revision,
                    )

    def test_manifest_without_derived_environment_keeps_rendering_behavior(self):
        with TestHostingManifest()._write(_manifest()) as project:
            validated = hosting.validate_manifest(project)
        self.assertEqual(
            hosting.render_env_file(validated, {}, pushed_commit_sha="not-a-sha"),
            "",
        )

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
