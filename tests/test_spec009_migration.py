"""Focused filesystem safety tests for the spec-009 state relocation."""
from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
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

    def test_automatic_migration_drops_old_extension_context_after_verified_move(self):
        legacy_root = self.root / "repo"
        source = self._write(legacy_root / "runtime" / "registry.json", "{}")
        legacy_config = self._write(
            legacy_root / "sandbox.local.yml",
            "instances: {fixture: {php_extension_digest: sha256:aaaa}}\n",
        )
        old_context = self._write(
            legacy_root / "runtime" / "build" / "php-extensions"
            / ("sha256:" + "a" * 64) / "Dockerfile.web",
            "FROM old-base\n",
        )
        new_base = self.root / "new-base"
        with patch.object(migrate, "ROOT", legacy_root), \
             patch.object(migrate, "BASE", new_base), \
             patch.object(migrate, "_legacy_config_secrets", return_value=[
                 (legacy_config, new_base / "sandbox.local.yml"),
             ]), \
             patch.dict(os.environ, {migrate._AUTO_FINALIZE_ENV: ""}, clear=False), \
             patch.object(migrate, "_reexec_finalize") as reexec:
            self.assertTrue(migrate.maybe_auto_migrate())

        self.assertFalse(source.exists())
        self.assertFalse(legacy_config.exists())
        self.assertEqual(
            (new_base / "sandbox.local.yml").read_text(),
            "instances: {fixture: {php_extension_digest: sha256:aaaa}}\n",
        )
        self.assertFalse(old_context.exists())
        self.assertFalse((new_base / "runtime" / "build" / "php-extensions").exists())
        reexec.assert_called_once_with(original_command=True)

    def test_extension_finalizer_omission_is_zero_work(self):
        import sandbox.php_extensions.compose_builder as builder

        with patch.object(migrate, "resolve_instances", return_value={
            "legacy": {"server": "nginx"},
        }), patch.object(builder, "materialize_compose_extension_context") as materialize:
            self.assertEqual(migrate._regenerate_extension_contexts({}), 0)
        materialize.assert_not_called()

    def test_extension_finalizer_rejects_inconsistent_persisted_identity(self):
        import sandbox.php_extensions.compose_builder as builder

        instance = {
            "server": "nginx",
            "php_extensions": {"extensions": {"gd": True}},
            "php_extension_digest": "not-a-digest",
            "php_extension_parent_digests": {
                "web": "sha256:" + "a" * 64,
                "wpcli": "sha256:" + "b" * 64,
            },
        }
        with patch.object(migrate, "resolve_instances", return_value={"legacy": instance}), \
             patch.object(builder, "materialize_compose_extension_context") as materialize:
            with self.assertRaises(migrate.MigrationConflict):
                migrate._regenerate_extension_contexts({})
        materialize.assert_not_called()

    def test_extension_finalizer_plans_all_instances_before_materializing(self):
        """A stale later plan cannot leave an earlier context on disk."""
        import sandbox.php_extensions.compose_builder as builder
        from sandbox.core._docker import _extension_plan_requirements
        from sandbox.php_extensions.compose_builder import plan_compose_extension_images

        digest_a = "sha256:" + "a" * 64
        digest_b = "sha256:" + "b" * 64
        requirements = _extension_plan_requirements(
            {"profile": "wordpress@1", "extensions": {"gd": True}},
        )
        plan = plan_compose_extension_images(
            requirements,
            parent_image="wordpress:php8.3-fpm",
            wpcli_image="wordpress:cli-php8.3",
            parent_digest=digest_a,
            wpcli_parent_digest=digest_b,
            server="nginx", php_version="8.3", platform="linux", architecture="amd64",
        )
        valid = {
            "server": "nginx",
            "php_version": "8.3",
            "php_extensions": requirements,
            "php_extension_parent_digests": {"web": digest_a, "wpcli": digest_b},
            "php_extension_digest": plan.digest,
            "platform": "linux",
            "architecture": "amd64",
        }
        stale = dict(valid)
        stale["php_extension_digest"] = "sha256:" + "c" * 64
        with patch.dict(os.environ, {"SANDBOX_HOME": str(self.destination_base)}, clear=False), \
             patch.object(migrate, "resolve_instances", return_value={
                 "a-valid": valid, "z-stale": stale,
             }), \
             patch.object(builder, "materialize_compose_extension_context") as materialize:
            with self.assertRaises(migrate.MigrationConflict):
                migrate._regenerate_extension_contexts({})

        materialize.assert_not_called()
        self.assertFalse(
            self.destination_base.joinpath("runtime", "build", "php-extensions").exists()
        )

    def test_cli_auto_reexec_rejects_stale_extension_identity_before_compose_write(self):
        """The real re-exec boundary validates identity before normal CLI writes."""
        from sandbox import cli

        stale = {
            "server": "nginx",
            "php_extensions": {"extensions": {"gd": True}},
            "php_extension_digest": "not-a-digest",
            "php_extension_parent_digests": {
                "web": "sha256:" + "a" * 64,
                "wpcli": "sha256:" + "b" * 64,
            },
        }
        compose_writes: list[str] = []
        argv = ["sb", "resources", "plan", "--scope", "cache", "--json"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(migrate, "BASE", self.destination_base), \
             patch.object(migrate, "RUNTIME_DIR", self.destination), \
             patch.object(migrate, "resolve_instances", return_value={"fixture": stale}), \
             patch.object(cli, "write_compose_files",
                          side_effect=lambda *_a, **_k: compose_writes.append("cli")), \
             patch.object(cli, "write_env_for_compose",
                          side_effect=lambda *_a, **_k: compose_writes.append("env")), \
             patch.object(migrate, "write_compose_files",
                          side_effect=lambda *_a, **_k: compose_writes.append("migration")), \
             patch.dict(os.environ, {migrate._AUTO_FINALIZE_ENV: "1"}, clear=False), \
             self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                cli.main(invocation_started_monotonic=100.0)

        self.assertEqual(compose_writes, [])

    def test_cli_auto_reexec_finalizes_once_and_preserves_valid_dispatch(self):
        """A valid automatic re-exec regenerates Compose once, then dispatches."""
        from sandbox import cli
        from sandbox.commands import resources

        payload = {
            "ok": True,
            "action": "plan",
            "status": "planned",
            "data": {
                "plan_id": "p" * 32,
                "expires_at": "2099-01-01T00:00:00Z",
                "candidates": [],
                "estimated_reclaimable_bytes": 0,
            },
        }
        compose_writes: list[str] = []
        plan_calls = []
        service = SimpleNamespace(
            plan=lambda *args, **kwargs: plan_calls.append((args, kwargs)) or payload,
        )
        argv = ["sb", "resources", "plan", "--scope", "cache", "--json"]
        with patch.object(sys, "argv", argv), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(migrate, "BASE", self.destination_base), \
             patch.object(migrate, "RUNTIME_DIR", self.destination), \
             patch.object(migrate, "resolve_instances", return_value={}), \
             patch.object(migrate, "write_compose_files",
                          side_effect=lambda *_a, **_k: compose_writes.append("migration")), \
             patch.object(migrate, "regen_caddyfile"), \
             patch.object(migrate, "ensure_tools_venv"), \
             patch.object(cli, "write_compose_files",
                          side_effect=lambda *_a, **_k: compose_writes.append("cli")), \
             patch.object(cli, "write_env_for_compose",
                          side_effect=lambda *_a, **_k: compose_writes.append("env")), \
             patch.object(resources, "resource_service", return_value=service), \
             patch.dict(os.environ, {migrate._AUTO_FINALIZE_ENV: "1"}, clear=False), \
             redirect_stdout(io.StringIO()):
            cli.main(invocation_started_monotonic=100.0)

        self.assertEqual(compose_writes, ["migration", "env"])
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(plan_calls[0][0], ("cache",))

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

    def test_extension_context_and_workspace_metadata_relocate_without_private_paths(self):
        """A base move carries pure metadata while generated locators are rebuilt.

        This is intentionally a source-only proof: protected runtime bytes are
        preserved while no Docker, database, network, or job lifecycle operation
        is invoked by the pure transfer path.
        """
        from sandbox.php_extensions.compose_builder import (
            extension_cache_status,
            materialize_compose_extension_context,
            plan_compose_extension_images,
        )
        from sandbox.core._docker import _extension_plan_requirements

        old_base = self.root / "old-base"
        new_base = self.root / "new-base"
        old_runtime = old_base / "runtime"
        old_home = os.environ.get("SANDBOX_HOME")
        digest_a = "sha256:" + "a" * 64
        digest_b = "sha256:" + "b" * 64
        try:
            os.environ["SANDBOX_HOME"] = str(old_base)
            plan = plan_compose_extension_images(
                _extension_plan_requirements(
                    {"profile": "wordpress@1", "extensions": {"gd": True}},
                ),
                parent_image="wordpress:php8.3-fpm",
                wpcli_image="wordpress:cli-php8.3",
                parent_digest=digest_a,
                wpcli_parent_digest=digest_b,
                server="nginx", php_version="8.3", platform="linux", architecture="amd64",
            )
            materialize_compose_extension_context(plan)

            protected = {
                "project": self._write(old_runtime / "wp-fixture" / "project.php", "project"),
                "uploads": self._write(old_runtime / "wp-fixture" / "wp-content" / "uploads" / "one.txt", "upload"),
                "snapshot": self._write(old_runtime / "snapshots" / "fixture" / "one.sql", "snapshot"),
                "workspace": self._write(
                    old_runtime / "jobs" / "workspaces" / "local-abc" / "default" / "workspace.json",
                    '{"label":"default","namespace":"local:abc"}\n',
                ),
                "index": self._write(old_runtime / "workspaces" / "index.sqlite3", "index-bytes"),
            }
            unrelated_build = self._write(
                old_runtime / "build" / "other-cache" / "metadata.json", "pure-build-data",
            )
            extension_context = old_runtime / "build" / "php-extensions" / plan.digest
            before = {name: path.read_bytes() for name, path in protected.items()}
            with patch.object(migrate, "compose") as compose, \
                 patch.object(migrate, "write_compose_files") as write_compose_files, \
                 patch.object(migrate, "regen_caddyfile") as regen_caddyfile, \
                 patch.object(migrate, "ensure_tools_venv") as ensure_tools_venv:
                moved = migrate._transfer(
                    old_runtime, new_base / "runtime", old_base, new_base, [],
                )
            # The transfer is counted by top-level runtime artifact (the
            # protected WP tree and workspace tree each move as one entry),
            # plus the unrelated build child. The generated extension context
            # is excluded until finalization.
            self.assertEqual(moved, 5)
            for name, source in protected.items():
                destination = new_base / "runtime" / source.relative_to(old_runtime)
                self.assertEqual(destination.read_bytes(), before[name])
                self.assertFalse(source.exists())
            self.assertEqual(
                (new_base / "runtime" / "build" / "other-cache" / "metadata.json").read_text(),
                "pure-build-data",
            )
            self.assertFalse(unrelated_build.exists())
            self.assertFalse(extension_context.exists())
            self.assertFalse((new_base / "runtime" / "build" / "php-extensions").exists())
            for operation in (compose, write_compose_files, regen_caddyfile,
                              ensure_tools_venv):
                operation.assert_not_called()

            os.environ["SANDBOX_HOME"] = str(new_base)
            instance = {
                "server": "nginx",
                "php_version": "8.3",
                "php_extensions": {
                    "profile": "wordpress@1", "extensions": {"gd": True},
                },
                "php_extension_parent_digests": {
                    "web": digest_a, "wpcli": digest_b,
                },
                "php_extension_digest": plan.digest,
                "platform": "linux",
                "architecture": "amd64",
            }
            with patch.object(migrate, "resolve_instances", return_value={"fixture": instance}):
                self.assertEqual(migrate._regenerate_extension_contexts({}), 1)
            status = extension_cache_status(plan.digest)
            self.assertEqual(status["state"], "ready")
            rendered = json.dumps(status, sort_keys=True)
            self.assertNotIn(str(old_base), rendered)
            self.assertNotIn("Dockerfile.web", rendered)
            self.assertNotIn("project.php", rendered)
        finally:
            if old_home is None:
                os.environ.pop("SANDBOX_HOME", None)
            else:
                os.environ["SANDBOX_HOME"] = old_home
