"""Unit tests for the per-project/modular sandbox tooling (specs 001 + 002).

Stdlib `unittest` only (no extra deps) — the .cli-venv already has PyYAML.
Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
    # or: ./sb selftest   (wraps this — see cmd_selftest)

These cover the pure/safe-to-test logic of the rewrite. Integration against a
live instance (doctor/wp/bridge over HTTP) is exercised separately.
"""
import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.cli  # noqa: E402  (registers command modules on import)
import sandbox.core as core  # noqa: E402
from sandbox.registry import COMMANDS  # noqa: E402
import sandbox_core  # noqa: E402


class TestPackageStructure(unittest.TestCase):
    def test_every_command_registered(self):
        # 39 commands + the `ui` alias (FR-007/FR-012: registry-driven dispatch).
        self.assertGreaterEqual(len(COMMANDS), 40)
        for name in ("up", "down", "status", "wp", "doctor", "snapshot",
                     "restore", "instances", "instance", "web", "open", "ui"):
            self.assertIn(name, COMMANDS, f"command {name!r} not registered")

    def test_no_default_instance_constant(self):
        # Spec 001: the legacy main/DEFAULT_INSTANCE model is gone.
        self.assertFalse(hasattr(core, "DEFAULT_INSTANCE"),
                         "DEFAULT_INSTANCE must not exist (per-project model)")

    def test_sb_is_thin_entry(self):
        # Constitution III: sb stays a small entry file; logic lives in sandbox/.
        n = len((ROOT / "sb").read_text().splitlines())
        self.assertLessEqual(n, 120, f"sb entry should be thin, got {n} lines")

    def test_entry_points_at_repo_root(self):
        self.assertEqual(core.ROOT, ROOT)
        self.assertEqual(core.ENTRY, ROOT / "sb")


class TestPureHelpers(unittest.TestCase):
    def test_deep_merge(self):
        a = {"x": 1, "n": {"a": 1, "b": 2}}
        b = {"y": 2, "n": {"b": 3, "c": 4}}
        out = core.deep_merge(a, b)
        self.assertEqual(out["x"], 1)
        self.assertEqual(out["y"], 2)
        self.assertEqual(out["n"], {"a": 1, "b": 3, "c": 4})  # nested merged, b wins

    def test_expand(self):
        self.assertEqual(core.expand("${home}/x", {"home": "/h"}), "/h/x")
        self.assertEqual(core.expand(["${a}", "z"], {"a": "1"}), ["1", "z"])
        self.assertEqual(core.expand({"k": "${a}"}, {"a": "v"}), {"k": "v"})

    def test_valid_server_passthrough(self):
        for s in ("apache", "nginx", "litespeed", "herd"):
            self.assertEqual(core._valid_server(s), s)

    def test_next_free_port_skips_used(self):
        # High ephemeral range, unlikely all bound; assert it honors `used`.
        used = {49210, 49211}
        p = core._next_free_port(49210, used)
        self.assertGreaterEqual(p, 49210)
        self.assertNotIn(p, used)


class TestSnapshotNameValidation(unittest.TestCase):
    """Spec 002 FR-010: the bridge must reject path-traversal names."""

    def test_valid_names(self):
        for n in ("t1", "snap-2026", "my.snapshot_1", "A1"):
            self.assertTrue(core._valid_snapshot_name(n), n)

    def test_rejects_traversal_and_junk(self):
        for n in ("", "..", ".", "../x", "../../etc", "a/b", "/abs",
                  ".hidden", "-x", "_x", "a b", "a\nb", None):
            self.assertFalse(core._valid_snapshot_name(n), repr(n))


class TestRegistrySourcedResolution(unittest.TestCase):
    """Spec 001: resolve_instances is registry-sourced; no synthesized `main`."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sb-test-rt-")
        self._old = os.environ.get("SANDBOX_RUNTIME")
        os.environ["SANDBOX_RUNTIME"] = self._tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SANDBOX_RUNTIME", None)
        else:
            os.environ["SANDBOX_RUNTIME"] = self._old
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_empty_registry_yields_no_instances(self):
        # No registry entries → no instances, and definitely no `main`.
        out = core.resolve_instances({})
        self.assertEqual(out, {})
        self.assertNotIn("main", out)

    def test_registry_crud_roundtrip(self):
        root = str(Path(self._tmp) / "proj")
        sandbox_core.registry_put(root, instance="proj-x", wordpress_port=8200)
        self.assertEqual(sandbox_core.registry_get(root)["instance"], "proj-x")
        self.assertEqual(
            sandbox_core.registry_find_instance("proj-x")["wordpress_port"], 8200)
        self.assertTrue(sandbox_core.registry_remove(root))

    def test_core_selftest_registry(self):
        # The shipped registry self-test (CRUD + lock + no lost updates).
        sandbox_core._selftest_registry()


class TestImageResolution(unittest.TestCase):
    """Pure version-pin → container-image mapping (server-aware)."""

    def test_web_image_defaults(self):
        self.assertEqual(core._web_image("apache"), "wordpress:latest")
        self.assertEqual(core._web_image("nginx"), "wordpress:php8.3-fpm")

    def test_web_image_php_pin(self):
        self.assertEqual(core._web_image("apache", "8.1"), "wordpress:php8.1")
        self.assertEqual(core._web_image("nginx", "8.2"), "wordpress:php8.2-fpm")
        self.assertEqual(core._web_image("litespeed", "8.1"),
                         "litespeedtech/openlitespeed:1.8.2-lsphp81")

    def test_web_image_explicit_wins_but_not_default_sentinel(self):
        self.assertEqual(core._web_image("apache", explicit="my/img:1"), "my/img:1")
        self.assertEqual(core._web_image("apache", explicit="wordpress:latest"),
                         "wordpress:latest")

    def test_cli_image_follows_php(self):
        self.assertEqual(core._cli_image(), "wordpress:cli")
        self.assertEqual(core._cli_image("8.1"), "wordpress:cli-php8.1")


class TestTldValidation(unittest.TestCase):
    def test_norm_tld_valid(self):
        self.assertEqual(core._norm_tld("tst"), "tst")
        self.assertEqual(core._norm_tld(".TST"), "tst")
        self.assertEqual(core._norm_tld("  test "), "test")
        self.assertEqual(core._norm_tld(""), "")

    def test_norm_tld_invalid_exits(self):
        import contextlib
        import io
        for bad in ("bad/tld", "a_b", "has space"):
            with self.assertRaises(SystemExit), \
                 contextlib.redirect_stderr(io.StringIO()):
                core._norm_tld(bad)


class TestSiteUrl(unittest.TestCase):
    """site_url's deterministic paths (no proxy/valet probe involved)."""

    def test_no_domain_is_localhost_port(self):
        self.assertEqual(core.site_url({"wordpress_port": 8195}),
                         "http://localhost:8195")
        self.assertEqual(core.site_url({"domain": None, "wordpress_port": 9001}),
                         "http://localhost:9001")

    def test_herd_with_domain_is_https(self):
        self.assertEqual(
            core.site_url({"server": "herd", "domain": "x.test",
                           "wordpress_port": 8080}),
            "https://x.test")


class TestDomainValidation(unittest.TestCase):
    def test_valid_domain_passthrough(self):
        self.assertEqual(core._valid_domain("myapp.tst"), "myapp.tst")
        self.assertEqual(core._valid_domain("MyApp.TST"), "myapp.tst")  # lowercased

    def test_invalid_domain_exits(self):
        import contextlib
        import io
        for bad in ("bad domain", "no_tld", "-leading.tst"):
            with self.assertRaises(SystemExit), \
                 contextlib.redirect_stderr(io.StringIO()):
                core._valid_domain(bad)


class TestNamingAndLiterals(unittest.TestCase):
    def test_derive_instance_name_basic(self):
        self.assertEqual(core._derive_instance_name("/a/templately", set()), "templately")

    def test_derive_instance_name_truncate_strip(self):
        # 24-char truncate must not leave a trailing hyphen.
        out = core._derive_instance_name("/a/templately-nav-menu-url-replace", set())
        self.assertEqual(out, "templately-nav-menu-url")
        self.assertFalse(out.endswith("-"))

    def test_derive_instance_name_dedup(self):
        self.assertEqual(core._derive_instance_name("/a/foo", {"foo"}), "foo-2")
        self.assertEqual(core._derive_instance_name("/a/foo", {"foo", "foo-2"}), "foo-3")

    def test_php_literal(self):
        self.assertEqual(core._php_literal(True), "true")
        self.assertEqual(core._php_literal(False), "false")
        self.assertEqual(core._php_literal(5), "5")
        self.assertEqual(core._php_literal(None), "null")
        self.assertEqual(core._php_literal("x"), "'x'")
        self.assertEqual(core._php_literal("a'b"), "'a\\'b'")  # single-quote escaped

    def test_pkg_slug_zip_url(self):
        self.assertEqual(
            core._pkg_slug("https://x.org/twentytwentyfour.1.5.zip"),
            "twentytwentyfour")

    def test_valid_server_invalid_exits(self):
        import contextlib
        import io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            core._valid_server("bogusserver")


class TestWpConfigRendering(unittest.TestCase):
    def test_merged_wp_config_project_wins_and_drops_wp_debug(self):
        m = core._merged_wp_config({"wp_config": {"WP_DEBUG": True, "MY_CONST": "v"}})
        self.assertEqual(m.get("MY_CONST"), "v")
        # WP_DEBUG is set via the WORDPRESS_DEBUG env, never as a define() here.
        self.assertNotIn("WP_DEBUG", m)

    def test_merged_wp_config_base_has_no_wp_debug(self):
        base = core._merged_wp_config({})
        self.assertIsInstance(base, dict)
        self.assertNotIn("WP_DEBUG", base)

    def test_wp_debug_env(self):
        self.assertEqual(core._wp_debug_env({}), "1")                       # default on
        self.assertEqual(core._wp_debug_env({"wp_config": {"WP_DEBUG": True}}), "1")
        self.assertEqual(core._wp_debug_env({"wp_config": {"WP_DEBUG": False}}), "")  # off

    def test_multisite_mode(self):
        self.assertIsNone(core._multisite_mode({}))
        self.assertIsNone(core._multisite_mode({"multisite": False}))
        self.assertEqual(core._multisite_mode({"multisite": True}), "subdirectory")
        self.assertEqual(core._multisite_mode({"multisite": "subdomain"}), "subdomain")
        self.assertEqual(core._multisite_mode({"multisite": "subdirectory"}),
                         "subdirectory")


class TestConfigExtraPhp(unittest.TestCase):
    def test_single_site_has_guarded_defines_no_multisite(self):
        php = core._config_extra_php({"wp_config": {"MY_CONST": "v"}})
        # every constant is defined()-guarded (never double-defines)
        self.assertIn("defined('MY_CONST') || define('MY_CONST', 'v');", php)
        self.assertNotIn("WP_ALLOW_MULTISITE", php)

    def test_subdirectory_multisite_block(self):
        php = core._config_extra_php({"multisite": True, "wordpress_port": 8195})
        self.assertIn("WP_ALLOW_MULTISITE", php)
        self.assertIn("define('MULTISITE', true)", php)
        self.assertIn("SUBDOMAIN_INSTALL", php)

    def test_subdomain_multisite_block(self):
        php = core._config_extra_php({"multisite": "subdomain", "wordpress_port": 8195})
        self.assertIn("WP_ALLOW_MULTISITE", php)
        self.assertIn("SUBDOMAIN_INSTALL", php)


class TestSiteHost(unittest.TestCase):
    def test_no_domain_includes_port(self):
        self.assertEqual(core._site_host({"wordpress_port": 8195}), "localhost:8195")

    def test_herd_domain_no_port(self):
        self.assertEqual(
            core._site_host({"server": "herd", "domain": "x.test",
                             "wordpress_port": 8080}),
            "x.test")


class TestSmallHelpers(unittest.TestCase):
    def test_server_runtime(self):
        self.assertEqual(core._server_runtime("apache")["docroot"], "/var/www/html")
        self.assertEqual(core._server_runtime("nginx")["docroot"], "/var/www/html")
        ls = core._server_runtime("litespeed")
        self.assertEqual(ls["docroot"], "/var/www/vhosts/localhost/html")
        self.assertEqual(ls["uid"], "1000:1000")

    def test_tld_default_and_override(self):
        self.assertEqual(core._tld({"tld": "foo"}), "foo")
        self.assertEqual(core._tld({}), core.PROXY_TLD)
        self.assertEqual(core._tld(None), core.PROXY_TLD)

    def test_herd_domain_and_db_name(self):
        self.assertEqual(core._herd_domain("myinst"), "myinst.test")
        self.assertEqual(core._herd_db_name("My-Inst"), "sandbox_my_inst")

    def test_extra_vol_lines(self):
        self.assertEqual(core._extra_vol_lines({}), "")
        self.assertEqual(core._extra_vol_lines({"extra_mounts": []}), "")
        self.assertIn("/host/path",
                      core._extra_vol_lines({"extra_mounts": ["/host/path"]}))


class TestDownloadCache(unittest.TestCase):
    """The shared download cache helpers (`./sb cache` + cache_* MCP tools)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sb-dlcache-"))
        # The helpers read DL_CACHE_DIR from the _docker module namespace.
        self._orig = core._docker.DL_CACHE_DIR
        core._docker.DL_CACHE_DIR = self.tmp

    def tearDown(self):
        core._docker.DL_CACHE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cache_command_registered(self):
        self.assertIn("cache", COMMANDS)

    def test_info_empty(self):
        nfo = core.dl_cache_info()
        self.assertEqual(nfo["total_files"], 0)
        self.assertEqual(nfo["total_bytes"], 0)
        self.assertEqual([l["name"] for l in nfo["layers"]], ["wp-cli", "wp-http"])

    def test_info_counts_then_clear(self):
        (self.tmp / "wp-cli" / "plugin").mkdir(parents=True)
        (self.tmp / "wp-cli" / "plugin" / "foo-1.0.zip").write_bytes(b"x" * 100)
        (self.tmp / "wp-http").mkdir(parents=True)
        (self.tmp / "wp-http" / "abc.zip").write_bytes(b"y" * 50)
        nfo = core.dl_cache_info()
        self.assertEqual(nfo["total_files"], 2)
        self.assertEqual(nfo["total_bytes"], 150)
        # Clearing one layer leaves the other intact and recreates the empty dir.
        res = core.dl_cache_clear("wp-http")
        self.assertEqual(res["freed_bytes"], 50)
        self.assertTrue((self.tmp / "wp-http").is_dir())
        self.assertEqual(core.dl_cache_info()["total_files"], 1)
        # Clearing all empties everything.
        core.dl_cache_clear()
        self.assertEqual(core.dl_cache_info()["total_files"], 0)

    def test_clear_rejects_unknown_layer(self):
        with self.assertRaises(ValueError):
            core.dl_cache_clear("nope")


class TestPluginConfigMap(unittest.TestCase):
    """Spec 010: canonical slug-keyed plugin map — normalize + field-merge."""

    def _resolve(self, *layer_docs, opted_layers=None):
        """Run the spec-010 pipeline on raw docs (low->high precedence). By
        default every layer counts as project-declared (opted-in); pass
        opted_layers=[bool,...] to mark which layers are project/override vs
        user-global catalog (matching load_project_config's opted_in logic)."""
        layers = []
        for d in layer_docs:
            m, _legacy, _self = sandbox_core._normalize_plugins(d or {})
            layers.append(m)
        if opted_layers is None:
            opted = set().union(*layers) if layers else set()
        else:
            opted = set()
            for m, is_opted in zip(layers, opted_layers):
                if is_opted:
                    opted |= set(m)
        merged = sandbox_core._merge_plugin_maps(*layers)
        return {s: sandbox_core._resolve_plugin_entry(e, s in opted)
                for s, e in merged.items()}

    def test_shorthand_true_is_active_org(self):
        r = self._resolve({"plugins": {"woo": True}})["woo"]
        self.assertTrue(r["active"]); self.assertFalse(r["on_demand"])
        self.assertEqual(r["source"], {"kind": "org", "value": None})

    def test_shorthand_false_is_inactive_installed(self):
        r = self._resolve({"plugins": {"qm": False}})["qm"]
        self.assertFalse(r["active"]); self.assertFalse(r["on_demand"])

    def test_project_path_defaults_active(self):
        # a bare path in the PROJECT (opted-in) -> source set, state defaults ACTIVE
        r = self._resolve({"plugins": {"my-addon": "."}})["my-addon"]
        self.assertEqual(r["source"]["kind"], "path")
        self.assertTrue(r["active"]); self.assertFalse(r["on_demand"])

    def test_catalog_path_defaults_on_demand(self):
        # the SAME bare path, but ONLY in the user-global catalog -> on-demand
        r = self._resolve({"plugins": {"t": "~/src/t"}},
                          opted_layers=[False])["t"]
        self.assertEqual(r["source"]["kind"], "path")
        self.assertTrue(r["on_demand"]); self.assertFalse(r["active"])

    def test_zip_string_is_zip_source(self):
        r = self._resolve({"plugins": {"t": "https://x/t.zip"}})["t"]
        self.assertEqual(r["source"]["kind"], "zip")

    def test_catalog_path_plus_project_active_keeps_both(self):
        # SC-007: user-global path (source only) + project true (state only)
        user = {"plugins": {"templately": "~/Sites/git/templately"}}
        proj = {"plugins": {"templately": True}}
        r = self._resolve(user, proj, opted_layers=[False, True])["templately"]
        self.assertTrue(r["active"])                       # from project
        self.assertEqual(r["source"]["kind"], "path")      # from catalog
        self.assertEqual(r["source"]["value"], "~/Sites/git/templately")
        self.assertFalse(r["on_demand"])                   # org fallback NOT applied

    def test_catalog_only_defaults_on_demand(self):
        # SC-008: a catalog path the project doesn't mention -> on-demand, not active
        user = {"plugins": {"elementor-pro": "~/pro/elementor-pro"}}
        proj = {"plugins": {"betterdocs": True}}
        r = self._resolve(user, proj, opted_layers=[False, True])
        self.assertTrue(r["elementor-pro"]["on_demand"])
        self.assertFalse(r["elementor-pro"]["active"])
        self.assertTrue(r["betterdocs"]["active"])         # project opted in

    def test_override_resources_one_slug_others_kept(self):
        # SC-002: override changes one source; the other plugin is NOT dropped
        proj = {"plugins": {"my-addon": ".", "woo": True}}
        override = {"plugins": {"woo": "~/src/woo"}}
        r = self._resolve(proj, override)
        self.assertIn("my-addon", r)                       # not dropped
        self.assertEqual(r["woo"]["source"]["value"], "~/src/woo")

    def test_explicit_project_source_beats_catalog(self):
        user = {"plugins": {"t": "~/local/t"}}
        proj = {"plugins": {"t": {"source": "org", "active": True}}}
        r = self._resolve(user, proj, opted_layers=[False, True])["t"]
        self.assertEqual(r["source"]["kind"], "org")       # explicit project wins

    def test_force_active_everywhere_from_user_global(self):
        # explicit active in the catalog -> active even though not project-declared
        user = {"plugins": {"qm": {"active": True}}}
        r = self._resolve(user, {"plugins": {"x": True}}, opted_layers=[False, True])
        self.assertTrue(r["qm"]["active"])

    def test_legacy_list_active_install(self):
        m, legacy, self_e = sandbox_core._normalize_plugins({"plugins": [".", "woo"]})
        self.assertTrue(legacy)
        self.assertIsNotNone(self_e)                       # "." -> self entry
        self.assertTrue(m["woo"]["active"])

    def test_legacy_mappings_fold_in(self):
        m, legacy, _ = sandbox_core._normalize_plugins(
            {"mappings": {"wp-content/plugins/t": "/p"},
             "mappings_inactive": {"wp-content/plugins/pro": "/q"}})
        self.assertTrue(legacy)
        self.assertEqual(sandbox_core._resolve_plugin_entry(m["t"])["active"], True)
        self.assertEqual(sandbox_core._resolve_plugin_entry(m["pro"])["active"], False)

    def test_non_plugin_mapping_not_folded(self):
        m, _, _ = sandbox_core._normalize_plugins(
            {"mappings": {"wp-content/mu-plugins/x": "/p"}})
        self.assertEqual(m, {})                            # left for the old path

    def test_map_wins_over_legacy_same_slug(self):
        m, _, _ = sandbox_core._normalize_plugins(
            {"plugins": {"t": False}, "mappings": {"wp-content/plugins/t": "/p"}})
        # map entry (inactive, no source) wins; mapping's path is NOT applied
        self.assertIs(m["t"]["source"], sandbox_core._UNSET)

    def test_multiple_sources_rejected(self):
        with self.assertRaises(sandbox_core.ConfigError):
            sandbox_core._normalize_plugins(
                {"plugins": {"t": {"path": "/p", "zip": "https://x/t.zip"}}})

    def test_sc001_legacy_equivalence(self):
        # SC-001: the canonical map expresses every legacy case identically.
        def norm(doc):
            m, _, _ = sandbox_core._normalize_plugins(doc)
            return {s: sandbox_core._resolve_plugin_entry(e, True) for s, e in m.items()}
        # active local: legacy mappings  ==  map {path}
        self.assertEqual(
            norm({"mappings": {"wp-content/plugins/x": "/p"}})["x"],
            norm({"plugins": {"x": "/p"}})["x"])
        # inactive local: legacy mappings_inactive == map {path, active:false}
        self.assertEqual(
            norm({"mappings_inactive": {"wp-content/plugins/x": "/p"}})["x"],
            norm({"plugins": {"x": {"path": "/p", "active": False}}})["x"])
        # active org: legacy list slug == map true
        self.assertEqual(
            norm({"plugins": ["x"]})["x"],
            norm({"plugins": {"x": True}})["x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
