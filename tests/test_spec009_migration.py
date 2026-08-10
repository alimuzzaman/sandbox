"""Focused filesystem safety tests for the spec-009 state relocation."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.commands import migrate


class TestSpec009MigrationSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "legacy" / "runtime"
        self.destination_base = self.root / "base"
        self.destination = self.destination_base / "runtime"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path: Path, value: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_different_destination_never_deletes_source(self):
        source = self._write(self.source / "snapshots" / "one.txt", "legacy")
        destination = self._write(self.destination / "snapshots" / "one.txt", "base")

        with self.assertRaises(migrate.MigrationConflict):
            migrate._transfer(self.source, self.destination, self.source.parent,
                              self.destination_base, [])

        self.assertEqual(source.read_text(), "legacy")
        self.assertEqual(destination.read_text(), "base")

    def test_verified_retry_removes_only_identical_source_copy(self):
        source = self._write(self.source / "seeds" / "demo.xml", "same")
        destination = self._write(self.destination / "seeds" / "demo.xml", "same")

        self.assertEqual(migrate._transfer(self.source, self.destination, self.source.parent,
                                            self.destination_base, []), 1)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(), "same")

    def test_config_only_upgrade_is_not_a_noop_and_preserves_secret_mode(self):
        legacy_secret = self._write(self.root / ".env.local", "TOKEN=not-printed\n")
        legacy_secret.chmod(0o640)
        target_secret = self.destination_base / ".env.local"

        self.assertEqual(migrate._transfer(self.source, self.destination, self.root,
                                            self.destination_base,
                                            [(legacy_secret, target_secret)]), 1)
        self.assertFalse(legacy_secret.exists())
        self.assertEqual(target_secret.read_text(), "TOKEN=not-printed\n")
        self.assertEqual(target_secret.stat().st_mode & 0o777, 0o600)

    def test_lock_rejects_concurrent_relocation(self):
        with migrate._migration_lock(self.destination_base):
            with self.assertRaises(migrate.MigrationConflict):
                with migrate._migration_lock(self.destination_base):
                    pass

    def test_finalize_regenerates_proxy_compose_and_tools(self):
        herd = self.destination / "herd-shims"
        self._write(herd / "old-shim", "stale")
        with patch.object(migrate, "RUNTIME_DIR", self.destination), \
             patch.object(migrate, "write_compose_files") as compose, \
             patch.object(migrate, "regen_caddyfile") as caddy, \
             patch.object(migrate, "ensure_tools_venv") as tools:
            migrate._regenerate_baked_artifacts({"instances": {}})

        compose.assert_called_once()
        caddy.assert_called_once()
        tools.assert_called_once()
        self.assertFalse(herd.exists())

    def test_auto_migration_runs_only_for_an_empty_destination(self):
        legacy_root = self.root / "repo"
        self._write(legacy_root / "runtime" / "registry.json", "{}")
        new_base = self.root / "new-base"
        with patch.object(migrate, "ROOT", legacy_root), \
             patch.object(migrate, "BASE", new_base), \
             patch.object(migrate, "_legacy_config_secrets", return_value=[]), \
             patch.dict(os.environ, {migrate._AUTO_FINALIZE_ENV: ""}, clear=False), \
             patch.object(migrate, "_reexec_finalize") as reexec:
            self.assertTrue(migrate.maybe_auto_migrate())

        self.assertTrue((new_base / "runtime" / "registry.json").exists())
        self.assertFalse((legacy_root / "runtime" / "registry.json").exists())
        reexec.assert_called_once_with(original_command=True)

    def test_auto_migration_refuses_populated_destination_without_merging(self):
        legacy_root = self.root / "repo"
        source = self._write(legacy_root / "runtime" / "registry.json", '{"old": true}')
        new_base = self.root / "new-base"
        target = self._write(new_base / "runtime" / "registry.json", '{"new": true}')
        with patch.object(migrate, "ROOT", legacy_root), \
             patch.object(migrate, "BASE", new_base), \
             patch.object(migrate, "_legacy_config_secrets", return_value=[]), \
             patch.dict(os.environ, {migrate._AUTO_FINALIZE_ENV: ""}, clear=False):
            with self.assertRaises(migrate.MigrationConflict):
                migrate.maybe_auto_migrate()

        self.assertEqual(source.read_text(), '{"old": true}')
        self.assertEqual(target.read_text(), '{"new": true}')

    def test_home_selection_hint_is_owner_only_and_contains_no_state(self):
        home = self.root / "home"
        selected = self.root / "selected-base"
        with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
            migrate._persist_home_selection(selected)

        hint = home / ".config" / "sandbox" / "home"
        self.assertEqual(hint.read_text().strip(), str(selected.resolve()))
        self.assertEqual(hint.stat().st_mode & 0o777, 0o600)

    def test_migration_help_exposes_the_documented_safety_flags(self):
        import subprocess

        result = subprocess.run([str(Path(__file__).parents[1] / "sb"), "migrate", "--help"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--force", result.stdout)
