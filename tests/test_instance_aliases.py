"""Instance aliases: extra hostnames one WordPress instance answers on.

Covers the four surfaces an alias has to reach to actually work — the
Caddyfile route, the certificate SANs, the wp-config constants, and the
instance block that carries the declaration across an apply.

    .cli-venv/bin/python -m unittest tests.test_instance_aliases -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.cli  # noqa: E402  (registers command modules on import)
import sandbox.core as core  # noqa: E402
import sandbox.core._domains as domains_core  # noqa: E402
import sandbox_core  # noqa: E402


class TestNormalizeAliases(unittest.TestCase):
    def test_normalizes_case_whitespace_and_trailing_dot(self):
        self.assertEqual(
            core.normalize_aliases(["  CDN.Example.COM.  "]),
            ["cdn.example.com"],
        )

    def test_accepts_a_bare_string(self):
        self.assertEqual(core.normalize_aliases("cdn.example.com"),
                         ["cdn.example.com"])

    def test_drops_the_primary_domain_and_duplicates(self):
        self.assertEqual(
            core.normalize_aliases(["a.tst", "p.tst", "a.tst"], primary="p.tst"),
            ["a.tst"],
        )

    def test_lenient_mode_skips_unusable_entries(self):
        # A scheme, a port, a path, or a wildcard is not a hostname Caddy can
        # match or mkcert can put in a SAN.
        self.assertEqual(
            core.normalize_aliases(
                ["https://a.tst", "b.tst:8080", "c.tst/x", "*.d.tst", "", 7,
                 "good.tst"]),
            ["good.tst"],
        )

    def test_strict_mode_dies_on_a_bad_entry(self):
        with self.assertRaises(SystemExit):
            core.normalize_aliases(["*.d.tst"], strict=True)

    def test_none_is_empty(self):
        self.assertEqual(core.normalize_aliases(None), [])

    def test_a_blank_entry_means_declared_and_empty(self):
        # `--alias ""` / MCP `aliases=[]` must override an inherited project
        # declaration, not fail as a typo.
        self.assertEqual(core.normalize_aliases([""], strict=True), [])
        self.assertEqual(core.normalize_aliases(["  "], strict=True), [])


class TestInstanceAliases(unittest.TestCase):
    def test_reads_the_block_and_excludes_the_primary(self):
        self.assertEqual(
            core.instance_aliases({"domain": "p.tst",
                                   "aliases": ["cdn.tst", "p.tst"]}),
            ["cdn.tst"],
        )

    def test_multisite_has_no_aliases(self):
        # wp_site.domain already maps hostnames to sites; a second name for
        # site 1 would fight that mapping.
        self.assertEqual(
            core.instance_aliases({"multisite": True, "aliases": ["cdn.tst"]}),
            [],
        )
        self.assertEqual(
            core.instance_aliases({"multisite": "subdomain",
                                   "aliases": ["cdn.tst"]}),
            [],
        )

    def test_herd_has_no_aliases(self):
        self.assertEqual(
            core.instance_aliases({"server": "herd", "aliases": ["cdn.tst"]}),
            [],
        )


class TestAliasCaddyRoutes(unittest.TestCase):
    def _regen(self, instances):
        with tempfile.TemporaryDirectory() as td:
            proxy_dir = Path(td) / "proxy"
            caddyfile = proxy_dir / "Caddyfile"
            with mock.patch.object(domains_core, "PROXY_DIR", proxy_dir), \
                 mock.patch.object(domains_core, "PROXY_CADDYFILE", caddyfile), \
                 mock.patch.object(domains_core, "resolve_instances",
                                   return_value=instances), \
                 mock.patch.object(sandbox_core, "registry_all", return_value={}):
                domains_core.regen_caddyfile({})
            return caddyfile.read_text()

    def test_alias_gets_its_own_block_on_the_same_port(self):
        rendered = self._regen({"demo": {
            "domain": "demo.tst", "tld": "tst", "wordpress_port": 8123,
            "aliases": ["cdn.tst"],
        }})
        self.assertIn("http://demo.tst {", rendered)
        self.assertIn("http://cdn.tst {", rendered)
        self.assertEqual(rendered.count("reverse_proxy host.docker.internal:8123"), 2)

    def test_alias_is_routed_even_without_a_routed_primary_domain(self):
        rendered = self._regen({"demo": {
            "tld": "tst", "wordpress_port": 8123, "aliases": ["cdn.example.com"],
        }})
        self.assertIn("http://cdn.example.com {", rendered)
        self.assertIn("reverse_proxy host.docker.internal:8123", rendered)

    def test_alias_serves_https_under_the_primary_certificate(self):
        # One cert per instance, keyed by its primary domain, with the alias as
        # a SAN — so the alias block must read the PRIMARY's cert files or it
        # silently stays on http after `sb secure`.
        with tempfile.TemporaryDirectory() as td:
            cert = Path(td) / "demo.tst.pem"
            key = Path(td) / "demo.tst-key.pem"
            cert.write_text("cert")
            key.write_text("key")

            def cert_paths(domain):
                if domain == "demo.tst":
                    return cert, key
                return Path(td) / "missing.pem", Path(td) / "missing-key.pem"

            with mock.patch.object(domains_core, "_cert_paths", cert_paths):
                rendered = core._caddy_block("cdn.tst", 8123,
                                             cert_domain="demo.tst")
        self.assertIn("\ncdn.tst {\n", rendered)
        self.assertIn("tls /certs/demo.tst.pem /certs/demo.tst-key.pem", rendered)
        self.assertIn("redir https://{host}{uri} 308", rendered)


class TestAliasWpConfig(unittest.TestCase):
    def _php(self, **inst):
        inst.setdefault("wordpress_port", 8188)
        return core._config_extra_php(inst)

    def test_no_aliases_emits_no_host_logic(self):
        self.assertNotIn("sandbox_alias_hosts", self._php(domain="p.tst"))

    def test_alias_defines_wp_home_and_siteurl_from_the_request_host(self):
        php = self._php(domain="p.tst", aliases=["cdn.example.com"])
        self.assertIn("$sandbox_alias_hosts = array('cdn.example.com');", php)
        self.assertIn("in_array($sandbox_alias_host, $sandbox_alias_hosts, true)", php)
        self.assertIn("defined('WP_HOME') || define('WP_HOME', $sandbox_alias_url);", php)
        self.assertIn("defined('WP_SITEURL') || define('WP_SITEURL', $sandbox_alias_url);", php)

    def test_the_host_is_matched_against_the_allowlist_not_trusted(self):
        # HTTP_HOST is attacker-controlled: an unlisted host must leave both
        # constants undefined so WP falls back to the home/siteurl options.
        php = self._php(domain="p.tst", aliases=["cdn.example.com"])
        self.assertNotIn("define('WP_HOME', $sandbox_alias_scheme", php)
        home_at = php.index("define('WP_HOME'")
        guard_at = php.index("if (in_array($sandbox_alias_host")
        self.assertLess(guard_at, home_at)

    def test_scheme_falls_back_to_the_forwarded_proto_header(self):
        php = self._php(domain="p.tst", aliases=["cdn.example.com"])
        self.assertIn("HTTP_X_FORWARDED_PROTO", php)

    def test_multisite_never_gets_alias_constants(self):
        self.assertNotIn("sandbox_alias_hosts",
                         self._php(multisite=True, aliases=["cdn.example.com"]))

    def test_compose_env_escapes_the_php_variables(self):
        # Compose interpolates $var inside YAML; an unescaped $_SERVER would
        # land as an empty string.
        env = core._env_config_lines({"domain": "p.tst", "wordpress_port": 8188,
                                      "aliases": ["cdn.example.com"]})
        self.assertIn("$$_SERVER['HTTP_HOST']", env)
        self.assertNotIn(" $_SERVER", env)


class TestAliasInstanceBlock(unittest.TestCase):
    """The declaration has to survive the block rebuild that every apply does."""

    PORTS = {"wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125}

    def _block(self, pconf, previous=None):
        import sandbox.core._instances as instances
        local = {"instances": {"project": previous}} if previous else {}
        with mock.patch.object(instances, "_local_yaml", return_value=local):
            return instances._build_instance_block(
                {}, "project", "/tmp/project", pconf, dict(self.PORTS), "nginx")

    def test_declared_aliases_land_in_the_block(self):
        block = self._block({"root": "/tmp/project",
                             "aliases": ["CDN.Example.com"]})
        self.assertEqual(block["aliases"], ["cdn.example.com"])

    def test_omission_preserves_the_previous_aliases(self):
        block = self._block({"root": "/tmp/project"},
                            previous={"aliases": ["cdn.example.com"]})
        self.assertEqual(block["aliases"], ["cdn.example.com"])

    def test_an_explicit_empty_list_removes_them(self):
        block = self._block({"root": "/tmp/project", "aliases": []},
                            previous={"aliases": ["cdn.example.com"]})
        self.assertNotIn("aliases", block)

    def test_no_aliases_anywhere_leaves_the_block_unchanged(self):
        self.assertNotIn("aliases", self._block({"root": "/tmp/project"}))

    def test_a_typo_fails_the_apply_that_introduced_it(self):
        with self.assertRaises(SystemExit):
            self._block({"root": "/tmp/project", "aliases": ["*.cdn.tst"]})

    def test_the_primary_domain_is_not_kept_as_its_own_alias(self):
        block = self._block(
            {"root": "/tmp/project", "aliases": ["demo.tst", "cdn.tst"]},
            previous={"domain": "demo.tst", "tld": "tst"})
        self.assertEqual(block["aliases"], ["cdn.tst"])


class TestAliasCertificateSans(unittest.TestCase):
    def setUp(self):
        import shutil
        if shutil.which("mkcert") is None:
            self.skipTest("mkcert binary unavailable")

    def test_secure_at_create_mints_the_alias_as_a_san(self):
        instances = {"demo": {"domain": "demo.tst", "tld": "tst",
                              "wordpress_port": 8123,
                              "aliases": ["cdn.tst"]}}
        with mock.patch.object(domains_core, "_ensure_url_proxy",
                               return_value=(True, None)), \
             mock.patch.object(domains_core, "_valid_domain",
                               side_effect=lambda d: d), \
             mock.patch.object(domains_core, "resolve_instances",
                               return_value=instances), \
             mock.patch.object(domains_core, "load_config", return_value={}), \
             mock.patch.object(domains_core, "_local_yaml",
                               return_value={"instances": {"demo": {}}}), \
             mock.patch.object(domains_core, "_write_local_yaml"), \
             mock.patch.object(domains_core, "_assign_domains_to_all",
                               side_effect=lambda cfg, tld=None: cfg), \
             mock.patch.object(domains_core, "regen_caddyfile"), \
             mock.patch.object(domains_core, "reload_proxy", return_value=True), \
             mock.patch.object(domains_core, "_mint_cert",
                               return_value=True) as mint:
            domains_core._secure_at_create({}, "demo")
        self.assertTrue(mint.called, "no certificate was minted")
        self.assertIn("cdn.tst", mint.call_args.kwargs.get("extra_sans") or [])


if __name__ == "__main__":
    unittest.main()
