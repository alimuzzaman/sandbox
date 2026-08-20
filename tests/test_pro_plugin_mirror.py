import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.core import _proplugins as pro


def _plugin(store: Path, slug: str, *, header: bool = True) -> Path:
    directory = store / slug
    directory.mkdir(parents=True)
    body = "<?php\n"
    if header:
        body += "/**\n * Plugin Name: Fixture\n */\n"
    (directory / f"{slug}.php").write_text(body)
    return directory


class LocalStoreTests(unittest.TestCase):
    def test_configured_store_that_is_missing_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SANDBOX_PRO_PLUGINS", None)
                with self.assertRaises(ValueError):
                    pro.local_store({"defaults": {"pro_plugins_home": missing}})

    def test_absent_default_store_resolves_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SANDBOX_PRO_PLUGINS", None)
                with patch.object(pro, "DEFAULT_STORE", str(Path(tmp) / "absent")):
                    self.assertIsNone(pro.local_store({}))

    def test_environment_overrides_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            with patch.dict(os.environ, {"SANDBOX_PRO_PLUGINS": str(store)}):
                self.assertEqual(pro.local_store({"defaults": {"pro_plugins_home": "/x"}}),
                                 store.resolve())


class StoreInventoryTests(unittest.TestCase):
    def test_only_plugin_directories_are_advertised(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            _plugin(store, "elementor-pro")
            _plugin(store, "not-a-plugin", header=False)
            (store / "wp-rocket_3.20.3.zip").write_text("zip")
            (store / "Bad Slug").mkdir()
            found = pro.store_plugins(store)
            self.assertEqual(sorted(found), ["elementor-pro"])
            self.assertEqual(found["elementor-pro"]["files"], 1)

    def test_fingerprint_tracks_content_and_ignores_excluded_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            directory = _plugin(store, "betterdocs-pro")
            first = pro.fingerprint(store)
            (directory / ".git").mkdir()
            (directory / ".git" / "HEAD").write_text("ref: refs/heads/main")
            self.assertEqual(pro.fingerprint(store), first)
            (directory / "betterdocs-pro.php").write_text(
                "<?php\n/**\n * Plugin Name: Fixture 2\n */\n")
            self.assertNotEqual(pro.fingerprint(store), first)


class RsyncCommandTests(unittest.TestCase):
    def test_transfer_mirrors_with_delete_and_excludes(self):
        remote = {"ssh": "alim@203.0.113.10"}
        with tempfile.TemporaryDirectory() as tmp:
            argv = pro._rsync_argv(remote, Path(tmp), "/home/alim/sandbox/plugins-pro")
        self.assertEqual(argv[0], "rsync")
        self.assertIn("--delete", argv)
        self.assertIn("node_modules/", argv)
        self.assertEqual(argv[-1], "alim@203.0.113.10:/home/alim/sandbox/plugins-pro/")
        self.assertTrue(argv[-2].endswith("/"))


class CatalogProgramTests(unittest.TestCase):
    """The catalog merge runs on the remote; exercise the real program here."""

    def _run(self, home: Path, store: str, plugins: dict) -> dict:
        payload = json.dumps({"home": str(home), "store": store, "plugins": plugins})
        result = subprocess.run(
            ["python3", "-c", pro._CATALOG_PROGRAM],
            input=payload, capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_mirrored_slugs_are_added_and_stale_ones_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = f"{home}/plugins-pro"
            (home / "config.json").write_text(json.dumps({"plugins": {
                "gone-pro": f"{store}/gone-pro",
                "local-checkout": "/srv/checkouts/local-checkout",
                "query-monitor": {"active": True},
            }}))
            report = self._run(home, store, {"elementor-pro": f"{store}/elementor-pro"})
            self.assertEqual(report["added"], ["elementor-pro"])
            self.assertEqual(report["removed"], ["gone-pro"])
            catalog = json.loads((home / "config.json").read_text())
            # A bare path is source-only: available on demand, never auto-enabled.
            self.assertEqual(catalog["plugins"]["elementor-pro"], f"{store}/elementor-pro")
            self.assertEqual(catalog["plugins"]["local-checkout"],
                             "/srv/checkouts/local-checkout")
            self.assertEqual(catalog["plugins"]["query-monitor"], {"active": True})

    def test_host_configured_entry_is_never_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = f"{home}/plugins-pro"
            (home / "config.json").write_text(json.dumps({"plugins": {
                "elementor-pro": {"path": "/srv/elementor-pro", "active": True},
            }}))
            report = self._run(home, store, {"elementor-pro": f"{store}/elementor-pro"})
            self.assertEqual(report["conflicts"], ["elementor-pro"])
            catalog = json.loads((home / "config.json").read_text())
            self.assertEqual(catalog["plugins"]["elementor-pro"],
                             {"path": "/srv/elementor-pro", "active": True})

    def test_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = f"{home}/plugins-pro"
            entries = {"betterdocs-pro": f"{store}/betterdocs-pro"}
            self._run(home, store, entries)
            report = self._run(home, store, entries)
            self.assertEqual(report["kept"], ["betterdocs-pro"])
            self.assertEqual(report["added"], [])


class SyncFlowTests(unittest.TestCase):
    def test_unchanged_store_skips_the_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "store"
            store.mkdir()
            _plugin(store, "embedpress-pro")
            remote = {"ssh": "alim@203.0.113.10"}
            receipt = {"fingerprint": pro.fingerprint(store),
                       "remote_store": "/home/alim/sandbox/plugins-pro",
                       "catalog": "/home/alim/sandbox/config.json"}
            with patch.object(pro, "local_store", return_value=store), \
                 patch.object(pro, "remote_store",
                              return_value="/home/alim/sandbox/plugins-pro"), \
                 patch.object(pro, "read_receipt", return_value=receipt), \
                 patch("subprocess.run") as run:
                summary = pro.sync(remote, "myvps")
            run.assert_not_called()
            self.assertEqual(summary["skipped"], "unchanged")
            self.assertEqual(summary["slugs"], ["embedpress-pro"])

    def test_machine_without_a_store_is_skipped_not_failed(self):
        with patch.object(pro, "local_store", return_value=None):
            summary = pro.sync({"ssh": "alim@203.0.113.10"}, "myvps")
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["skipped"], "no_local_store")

    def test_catalog_payload_is_passed_on_stdin_only(self):
        program = f"python3 -c {shlex.quote(pro._CATALOG_PROGRAM)}"
        self.assertNotIn("plugins-pro", program)


if __name__ == "__main__":
    unittest.main()
