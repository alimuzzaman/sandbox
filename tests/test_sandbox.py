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


if __name__ == "__main__":
    unittest.main(verbosity=2)
