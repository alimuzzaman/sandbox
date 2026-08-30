"""Focused filesystem safety tests for the spec-009 state relocation."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
from tests.subprocess_support import synthetic_environment
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import migrate
from sandbox.workspaces.models import WorkspaceRecord
from sandbox.workspaces.repository import WorkspaceIndexError, WorkspaceRepository
from sandbox.workspaces.maintenance import base_maintenance_lock


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

    @staticmethod
    def _locator_digest(path: Path) -> str:
        return "sha256:" + hashlib.sha256(str(path).encode()).hexdigest()

    def _real_relocation_fixture(self):
        """Create source-owned SQLite metadata plus protected byte sentinels."""
        old_base = self.root / "relocation-old"
        new_base = self.root / "relocation-new"
        old_runtime = old_base / "runtime"
        legacy_root = old_runtime / "jobs" / "workspaces"
        workspace_json = legacy_root / "local-abc" / "default" / "workspace.json"
        checkout = old_runtime / "deploy" / "checkout"
        source_checkout = old_runtime / "deploy" / "source"
        external_locator = self.root / "external-deployment" / "checkout"
        workspace_json.parent.mkdir(parents=True, exist_ok=True)
        checkout.mkdir(parents=True, exist_ok=True)
        source_checkout.mkdir(parents=True, exist_ok=True)
        external_locator.mkdir(parents=True, exist_ok=True)
        workspace_bytes = b'{"label":"default","namespace":"local:abc"}\n'
        workspace_json.write_bytes(workspace_bytes)
        (checkout / "manifest.json").write_bytes(b"managed checkout\n")
        (source_checkout / "manifest.json").write_bytes(b"managed source checkout\n")

        protected = {
            "legacy_workspace": workspace_json,
            "project": self._write(old_runtime / "wp-fixture" / "project.php", "project-bytes"),
            "uploads": self._write(
                old_runtime / "wp-fixture" / "wp-content" / "uploads" / "one.txt",
                "upload-bytes",
            ),
            "snapshot": self._write(
                old_runtime / "snapshots" / "fixture" / "one.sql", "snapshot-bytes",
            ),
            "database_volume": self._write(
                old_runtime / "volumes" / "db-fixture" / "volume.dat", "database-bytes",
            ),
            "registry": self._write(old_runtime / "registry.json", "registry-bytes"),
            "job_metadata": self._write(
                old_runtime / "jobs" / "job-001.json", "job-metadata-bytes",
            ),
        }

        repository = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3", legacy_root,
        )
        record = repository.register(
            "project:relocation", "default", namespace="local-abc",
            path=str(workspace_json), aliases=("stable",),
            metadata={
                "checkout_locator": str(checkout),
                "checkout_locator_digest": self._locator_digest(checkout),
                "source_checkout_locator": str(source_checkout),
                "source_checkout_locator_digest": self._locator_digest(source_checkout),
                "external_locator": str(external_locator),
                "source_identity": "git:relocation-source",
                "source_generation": "source-generation-17",
            },
        )
        repository.bind_resource(record.workspace_id, "compose_project", "sandbox-relocation")
        generation = repository.schema_generation()
        return {
            "old_base": old_base,
            "new_base": new_base,
            "old_runtime": old_runtime,
            "new_runtime": new_base / "runtime",
            "legacy_root": legacy_root,
            "workspace_json": workspace_json,
            "checkout": checkout,
            "source_checkout": source_checkout,
            "external_locator": external_locator,
            "protected": protected,
            "repository": repository,
            "record": record,
            "generation": generation,
        }

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

    def test_migration_lock_preserves_body_io_errors(self):
        with self.assertRaises(OSError) as caught:
            with migrate._migration_lock(self.destination_base):
                raise OSError("simulated migration copy failure")
        self.assertEqual(str(caught.exception), "simulated migration copy failure")

    def test_migration_lock_maps_flock_setup_failure_to_conflict(self):
        with patch("fcntl.flock", side_effect=OSError("flock unavailable")):
            with self.assertRaises(migrate.MigrationConflict):
                with migrate._migration_lock(self.destination_base):
                    self.fail("lock setup failure must not enter the body")

    def test_transfer_journal_rebases_a_real_checkpointed_workspace_index(self):
        old_base = self.root / "old-home"
        new_base = self.root / "new-home"
        old_runtime = old_base / "runtime"
        legacy = old_runtime / "jobs" / "workspaces" / "local-abc" / "default" / "workspace.json"
        legacy.parent.mkdir(parents=True)
        legacy_bytes = b'{"label":"default","namespace":"local:abc"}\n'
        legacy.write_bytes(legacy_bytes)
        checkout = old_runtime / "deploy" / "checkout"
        checkout.mkdir(parents=True)
        digest = lambda value: "sha256:" + __import__("hashlib").sha256(
            str(value).encode()).hexdigest()
        repository = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3",
            old_runtime / "jobs" / "workspaces",
        )
        record = repository.register(
            "project:move", "default", namespace="local-abc", path=str(legacy),
            aliases=("stable-alias",),
            metadata={
                "checkout_locator": str(checkout),
                "checkout_locator_digest": digest(checkout),
                "external_locator": str(self.root / "outside"),
            },
        )
        repository.bind_resource(record.workspace_id, "compose_project", "sandbox-move")
        generation = repository.schema_generation()

        with migrate._migration_lock(old_base, new_base):
            self.assertGreater(migrate._transfer(
                old_runtime, new_base / "runtime", old_base, new_base, []), 0)

        self.assertIsNotNone(migrate._load_journal(new_base))
        self.assertFalse((new_base / "runtime" / "workspaces" / "index.sqlite3-wal").exists())
        with patch.object(migrate, "RUNTIME_DIR", new_base / "runtime"):
            result = migrate._rebase_workspace_index_from_journal(new_base)
        self.assertEqual(result["rows_rebased"], 1)
        moved = WorkspaceRepository(
            new_base / "runtime" / "workspaces" / "index.sqlite3",
            new_base / "runtime" / "jobs" / "workspaces",
        ).get(record.workspace_id)
        self.assertIsInstance(moved, WorkspaceRecord)
        self.assertEqual(moved.workspace_id, record.workspace_id)
        self.assertEqual(moved.aliases, ("stable-alias",))
        self.assertEqual(moved.bindings[0]["resource_id"], "sandbox-move")
        self.assertEqual(moved.path, str(new_base / legacy.relative_to(old_base)))
        self.assertEqual(moved.metadata["checkout_locator"], str(new_base / checkout.relative_to(old_base)))
        self.assertEqual(moved.metadata["external_locator"], str(self.root / "outside"))
        self.assertEqual(
            WorkspaceRepository(
                new_base / "runtime" / "workspaces" / "index.sqlite3",
                new_base / "runtime" / "jobs" / "workspaces",
            ).schema_generation(), generation,
        )
        self.assertEqual(
            (new_base / legacy.relative_to(old_base)).read_bytes(), legacy_bytes)

    def test_real_transfer_and_finalize_rebases_metadata_and_protected_state(self):
        """The journal drives a real SQLite rebase; protected state is source-only."""
        fixture = self._real_relocation_fixture()
        old_base = fixture["old_base"]
        new_base = fixture["new_base"]
        old_runtime = fixture["old_runtime"]
        new_runtime = fixture["new_runtime"]
        before_bytes = {
            name: path.read_bytes() for name, path in fixture["protected"].items()
        }

        with migrate._migration_lock(old_base, new_base):
            moved = migrate._transfer(
                old_runtime, new_runtime, old_base, new_base, [],
            )
        self.assertGreater(moved, 0)
        journal = migrate._load_journal(new_base)
        self.assertIsNotNone(journal)
        self.assertEqual(journal["source"], str(old_base.resolve()))

        with patch.object(migrate, "BASE", new_base), \
             patch.object(migrate, "RUNTIME_DIR", new_runtime), \
             patch.object(migrate, "_regenerate_baked_artifacts") as regenerate, \
             patch.object(migrate, "resolve_instances", return_value={}) as resolve, \
             patch.object(migrate, "_instance_running") as running, \
             patch.object(migrate, "_wait_reachable") as reachable, \
             patch.object(migrate, "compose") as compose:
            migrate._finalize({})

        regenerate.assert_called_once_with({})
        resolve.assert_called_once_with({})
        running.assert_not_called()
        reachable.assert_not_called()
        compose.assert_not_called()
        self.assertFalse(migrate._journal_path(new_base).exists(),
                         "journal clears only after real rebase and finalization succeed")

        for name, source in fixture["protected"].items():
            destination = new_base / source.relative_to(old_base)
            self.assertEqual(destination.read_bytes(), before_bytes[name])
            self.assertFalse(source.exists())
        moved_workspace = WorkspaceRepository(
            new_runtime / "workspaces" / "index.sqlite3",
            new_runtime / "jobs" / "workspaces",
        ).get(fixture["record"].workspace_id)
        self.assertEqual(moved_workspace.workspace_id, fixture["record"].workspace_id)
        self.assertEqual(moved_workspace.project_identity, "project:relocation")
        self.assertEqual(moved_workspace.namespace, "local-abc")
        self.assertEqual(moved_workspace.aliases, ("stable",))
        self.assertEqual(moved_workspace.bindings[0]["resource_id"], "sandbox-relocation")
        self.assertEqual(moved_workspace.path,
                         str(new_base / fixture["workspace_json"].relative_to(old_base)))
        moved_checkout = new_base / fixture["checkout"].relative_to(old_base)
        moved_source_checkout = new_base / fixture["source_checkout"].relative_to(old_base)
        self.assertEqual(moved_workspace.metadata["checkout_locator"], str(moved_checkout))
        self.assertEqual(moved_workspace.metadata["source_checkout_locator"],
                         str(moved_source_checkout))
        self.assertEqual(moved_workspace.metadata["checkout_locator_digest"],
                         self._locator_digest(moved_checkout))
        self.assertEqual(moved_workspace.metadata["source_checkout_locator_digest"],
                         self._locator_digest(moved_source_checkout))
        self.assertEqual(moved_workspace.metadata["external_locator"],
                         str(fixture["external_locator"]))
        self.assertEqual(moved_workspace.metadata["source_identity"],
                         "git:relocation-source")
        self.assertEqual(moved_workspace.metadata["source_generation"],
                         "source-generation-17")
        self.assertEqual(
            WorkspaceRepository(
                new_runtime / "workspaces" / "index.sqlite3",
                new_runtime / "jobs" / "workspaces",
            ).schema_generation(), fixture["generation"],
        )

    def test_real_finalize_rebase_failure_retains_journal_before_generation_orchestration(self):
        """A typed rebase failure remains retryable and never reaches runtime seams."""
        fixture = self._real_relocation_fixture()
        old_base = fixture["old_base"]
        new_base = fixture["new_base"]
        with migrate._migration_lock(old_base, new_base):
            migrate._transfer(
                fixture["old_runtime"], fixture["new_runtime"],
                old_base, new_base, [],
            )
        journal_path = migrate._journal_path(new_base)
        journal_bytes = journal_path.read_bytes()
        with patch.object(migrate, "BASE", new_base), \
             patch.object(migrate, "RUNTIME_DIR", fixture["new_runtime"]), \
             patch.object(
                 WorkspaceRepository,
                 "rebase_home_locators",
                 side_effect=WorkspaceIndexError(
                     "workspace_index_unavailable", "simulated source-only rebase failure",
                 ),
             ) as rebase, \
             patch.object(migrate, "_regenerate_baked_artifacts") as regenerate, \
             patch.object(migrate, "resolve_instances") as resolve, \
             patch.object(migrate, "_instance_running") as running, \
             patch.object(migrate, "_wait_reachable") as reachable, \
             patch.object(migrate, "compose") as compose, \
             patch.object(migrate, "die", side_effect=SystemExit(1)) as die:
            with self.assertRaises(SystemExit) as raised:
                migrate._finalize({})

        self.assertEqual(raised.exception.code, 1)
        rebase.assert_called_once()
        regenerate.assert_not_called()
        resolve.assert_not_called()
        running.assert_not_called()
        reachable.assert_not_called()
        compose.assert_not_called()
        die.assert_called_once()
        self.assertEqual(journal_path.read_bytes(), journal_bytes)
        self.assertTrue(journal_path.exists())

    def test_competing_repository_writer_blocks_transfer_without_lost_update(self):
        old_base = self.root / "old-home"
        new_base = self.root / "new-home"
        old_runtime = old_base / "runtime"
        repository = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3",
            old_runtime / "jobs" / "workspaces",
        )
        first = repository.register("project:one", "first")
        entered = threading.Event()
        release = threading.Event()

        def writer():
            with base_maintenance_lock(old_base, exclusive=False):
                entered.set()
                self.assertTrue(release.wait(5))
                return repository.register("project:two", "second")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            self.assertTrue(entered.wait(5))
            with self.assertRaises(migrate.MigrationConflict):
                with migrate._migration_lock(old_base, new_base):
                    migrate._transfer(
                        old_runtime, new_base / "runtime", old_base, new_base, [])
            self.assertTrue((old_runtime / "workspaces" / "index.sqlite3").exists())
            self.assertFalse((new_base / "runtime" / "workspaces" / "index.sqlite3").exists())
            release.set()
            second = future.result(timeout=5)

        self.assertEqual(
            {item.workspace_id for item in repository.list(include_legacy=False)},
            {first.workspace_id, second.workspace_id},
        )

    def test_legacy_and_automatic_entrypoints_refuse_a_busy_source_base(self):
        old_base = self.root / "legacy-home"
        new_base = self.root / "new-home"
        old_runtime = old_base / "runtime"
        repository = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3",
            old_runtime / "jobs" / "workspaces",
        )
        first = repository.register("project:one", "first")
        entered = threading.Event()
        release = threading.Event()

        def writer():
            with base_maintenance_lock(old_base, exclusive=False):
                entered.set()
                self.assertTrue(release.wait(5))
                return repository.register("project:two", "second")

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            self.assertTrue(entered.wait(5))
            for automatic in (False, True):
                with self.subTest(automatic=automatic), \
                        patch.object(migrate, "ROOT", old_base), \
                        patch.object(migrate, "BASE", new_base), \
                        patch.object(migrate, "_legacy_config_secrets", return_value=[]), \
                        patch.object(migrate, "die", side_effect=migrate.MigrationConflict):
                    if automatic:
                        with self.assertRaises(migrate.MigrationConflict):
                            migrate.maybe_auto_migrate()
                    else:
                        with self.assertRaises(migrate.MigrationConflict):
                            migrate.cmd_migrate(
                                {}, SimpleNamespace(
                                    finalize=False, force=False, apply=True, dry_run=False,
                                ),
                            )
                self.assertTrue((old_runtime / "workspaces" / "index.sqlite3").exists())
                self.assertFalse((new_base / migrate._JOURNAL).exists())
                self.assertFalse((new_base / "runtime" / "workspaces" / "index.sqlite3").exists())
            release.set()
            second = future.result(timeout=5)

        self.assertEqual(
            {item.workspace_id for item in repository.list(include_legacy=False)},
            {first.workspace_id, second.workspace_id},
        )

    def test_subprocess_repository_writer_blocks_transfer_before_journal_or_copy(self):
        old_base = self.root / "legacy-home"
        new_base = self.root / "new-home"
        old_runtime = old_base / "runtime"
        repository = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3",
            old_runtime / "jobs" / "workspaces",
        )
        first = repository.register("project:one", "first")
        project_root = Path(__file__).resolve().parents[1]
        script = """
from pathlib import Path
import sys
from sandbox.workspaces.maintenance import base_maintenance_lock
from sandbox.workspaces.repository import WorkspaceRepository
base = Path(sys.argv[1])
repository = WorkspaceRepository(base / 'runtime' / 'workspaces' / 'index.sqlite3',
                                 base / 'runtime' / 'jobs' / 'workspaces')
with base_maintenance_lock(base, exclusive=False):
    print('locked', flush=True)
    sys.stdin.readline()
    record = repository.register('project:two', 'second')
print(record.workspace_id, flush=True)
"""
        env = synthetic_environment()
        env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(old_base)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(migrate.MigrationConflict):
                with migrate._migration_lock(old_base, new_base):
                    migrate._transfer(
                        old_runtime, new_base / "runtime", old_base, new_base, [])
            self.assertTrue((old_runtime / "workspaces" / "index.sqlite3").exists())
            self.assertFalse((new_base / migrate._JOURNAL).exists())
            self.assertFalse((new_base / "runtime" / "workspaces" / "index.sqlite3").exists())
            process.stdin.write("release\n")
            process.stdin.close()
            self.assertTrue(process.stdout.readline().strip().startswith("ws_"))
            self.assertEqual(process.wait(timeout=5), 0, process.stderr.read())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        records = WorkspaceRepository(
            old_runtime / "workspaces" / "index.sqlite3",
            old_runtime / "jobs" / "workspaces",
        ).list(include_legacy=False)
        self.assertEqual(len(records), 2)
        self.assertIn(first.workspace_id, {item.workspace_id for item in records})
        self.assertEqual({item.label for item in records}, {"first", "second"})

    def test_busy_or_malformed_workspace_checkpoint_retains_source_before_journal(self):
        for case in ("busy", "malformed"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source_base = root / "old-home"
                destination_base = root / "new-home"
                source_runtime = source_base / "runtime"
                index = source_runtime / "workspaces" / "index.sqlite3"
                index.parent.mkdir(parents=True)
                held_connection = None
                if case == "busy":
                    repository = WorkspaceRepository(
                        index, source_runtime / "jobs" / "workspaces")
                    repository.register("project:one", "first")
                    held_connection = sqlite3.connect(index, timeout=0.0, isolation_level=None)
                    held_connection.execute("BEGIN IMMEDIATE")
                else:
                    index.write_bytes(b"SQLite format 3\x00malformed-index")
                try:
                    with self.assertRaises(migrate.MigrationConflict):
                        migrate._transfer(
                            source_runtime, destination_base / "runtime",
                            source_base, destination_base, [],
                        )
                finally:
                    if held_connection is not None:
                        held_connection.execute("ROLLBACK")
                        held_connection.close()
                self.assertTrue(index.exists())
                self.assertFalse((destination_base / migrate._JOURNAL).exists())
                self.assertFalse((destination_base / "runtime" / "workspaces" / "index.sqlite3").exists())

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

    def test_finalize_retains_invalid_journal_before_any_generation_orchestration(self):
        """Corrupt or lexically relative journals must stop finalization safely."""
        journal_path = self.destination_base / migrate._JOURNAL
        cases = (
            ("malformed", b"{not-json\n"),
            ("non-object", b"[]\n"),
            (
                "relative-source",
                json.dumps({
                    "source": "relative/legacy-base",
                    "moves": [str(self.destination / "registry.json")],
                }).encode() + b"\n",
            ),
        )
        for label, raw in cases:
            with self.subTest(label=label):
                journal_path.parent.mkdir(parents=True, exist_ok=True)
                journal_path.write_bytes(raw)
                with patch.object(migrate, "BASE", self.destination_base), \
                     patch.object(migrate, "RUNTIME_DIR", self.destination), \
                     patch.object(migrate, "_regenerate_baked_artifacts") as baked, \
                     patch.object(migrate, "write_compose_files") as compose, \
                     patch.object(migrate, "regen_caddyfile") as caddy, \
                     patch.object(migrate, "ensure_tools_venv") as tools, \
                     patch.object(migrate, "resolve_instances") as resolve, \
                     patch.object(migrate, "_instance_running") as running, \
                     patch.object(migrate, "_wait_reachable") as wait, \
                     patch.object(migrate, "compose") as recreate, \
                     patch.object(migrate, "die", side_effect=migrate.MigrationConflict) as die:
                    with self.assertRaises(migrate.MigrationConflict):
                        migrate._finalize({})

                self.assertEqual(journal_path.read_bytes(), raw)
                die.assert_called_once()
                for operation in (baked, compose, caddy, tools, resolve,
                                  running, wait, recreate):
                    operation.assert_not_called()

    def test_finalize_retains_unreadable_journal_before_any_generation_orchestration(self):
        """An unreadable authorization journal is a bounded, retryable failure."""
        journal_path = self.destination_base / migrate._JOURNAL
        raw = b'{"source":"/old-base","moves":[]}'
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_bytes(raw)
        with patch.object(migrate, "BASE", self.destination_base), \
             patch.object(migrate, "RUNTIME_DIR", self.destination), \
             patch.object(migrate, "_regenerate_baked_artifacts") as baked, \
             patch.object(migrate, "write_compose_files") as compose, \
             patch.object(migrate, "regen_caddyfile") as caddy, \
             patch.object(migrate, "ensure_tools_venv") as tools, \
             patch.object(migrate, "resolve_instances") as resolve, \
             patch.object(migrate, "_instance_running") as running, \
             patch.object(migrate, "_wait_reachable") as wait, \
             patch.object(migrate, "compose") as recreate, \
             patch.object(migrate, "die", side_effect=migrate.MigrationConflict) as die, \
             patch("pathlib.Path.read_text", side_effect=PermissionError("permission denied")):
            with self.assertRaises(migrate.MigrationConflict):
                migrate._finalize({})

        self.assertEqual(journal_path.read_bytes(), raw)
        die.assert_called_once()
        for operation in (baked, compose, caddy, tools, resolve,
                          running, wait, recreate):
            operation.assert_not_called()

    def test_finalize_rebases_from_a_valid_verified_journal(self):
        source_base = self.root / "legacy-base"
        source_base.mkdir()
        journal_path = self.destination_base / migrate._JOURNAL
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(json.dumps({
            "source": str(source_base),
            "moves": [str(self.destination / "registry.json")],
        }) + "\n")
        rebased = {
            "ok": True,
            "metadata_only": True,
            "index_present": False,
            "rows_rebased": 0,
            "locators_rebased": 0,
            "already_rebased": True,
            "index_generation": None,
        }
        with patch.object(migrate, "BASE", self.destination_base), \
             patch.object(migrate, "RUNTIME_DIR", self.destination), \
             patch.object(migrate, "resolve_instances", return_value={}), \
             patch.object(migrate, "_regenerate_baked_artifacts") as baked, \
             patch(
                 "sandbox.workspaces.repository.WorkspaceRepository.rebase_home_locators",
                 return_value=rebased,
             ) as rebase:
            migrate._finalize({})

        rebase.assert_called_once_with(
            self.destination / "workspaces" / "index.sqlite3",
            source_base.resolve(),
            self.destination_base,
        )
        baked.assert_called_once_with({})
        self.assertFalse(journal_path.exists())

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
             patch.object(resources.time, "monotonic", return_value=100.0), \
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

    def _sandbox_base_probe(self, env, *, cwd=None):
        """Resolve sandbox_core.sandbox_base in a fresh interpreter.

        A subprocess matters here: the CLI and MCP are separately launched
        processes, so an import-time path decision must not depend on this
        test process having previously imported either composition root.
        """
        root = Path(__file__).resolve().parents[1]
        probe = "import sandbox_core; print(sandbox_core.sandbox_base())"
        child_env = dict(env)
        child_env["PYTHONPATH"] = str(root)
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=str(cwd or root), env=child_env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip())

    def test_sandbox_core_honours_persisted_selector_after_env_removed(self):
        home = self.root / "process-home"
        selected = self.root / "selected-base"
        hint = home / ".config" / "sandbox" / "home"
        hint.parent.mkdir(parents=True)
        hint.write_text(f"  {selected}  \n")
        env = synthetic_environment()
        env["HOME"] = str(home)
        env.pop("SANDBOX_HOME", None)
        env.pop("SANDBOX_RUNTIME", None)
        self.assertEqual(self._sandbox_base_probe(env), selected.resolve())

    def test_sandbox_home_env_wins_over_persisted_selector(self):
        home = self.root / "process-home"
        selected = self.root / "selected-base"
        explicit = self.root / "explicit-base"
        hint = home / ".config" / "sandbox" / "home"
        hint.parent.mkdir(parents=True)
        hint.write_text(str(selected) + "\n")
        env = synthetic_environment()
        env["HOME"] = str(home)
        env["SANDBOX_HOME"] = str(explicit)
        env.pop("SANDBOX_RUNTIME", None)
        self.assertEqual(self._sandbox_base_probe(env), explicit.resolve())

    def test_blank_missing_and_unreadable_selector_fall_back_to_default(self):
        home = self.root / "process-home"
        default = (home / "sandbox").resolve()
        hint = home / ".config" / "sandbox" / "home"
        env = synthetic_environment()
        env["HOME"] = str(home)
        env.pop("SANDBOX_HOME", None)
        env.pop("SANDBOX_RUNTIME", None)

        for contents in (None, "\n  \n", "relative-state"):
            with self.subTest(selector=contents):
                if contents is None:
                    if hint.exists():
                        hint.unlink()
                else:
                    hint.parent.mkdir(parents=True, exist_ok=True)
                    hint.write_text(contents)
                self.assertEqual(self._sandbox_base_probe(env), default)

        # A directory at the selector path is unreadable as a text file in a
        # fresh process (IsADirectoryError), and exercises the same safe
        # fallback without relying on uid-dependent chmod behavior.
        if hint.exists():
            hint.unlink()
        hint.mkdir(parents=True)
        try:
            self.assertEqual(self._sandbox_base_probe(env), default)
        finally:
            shutil.rmtree(hint)

    def test_relative_selector_is_ignored_independent_of_process_cwd(self):
        home = self.root / "process-home"
        hint = home / ".config" / "sandbox" / "home"
        hint.parent.mkdir(parents=True)
        hint.write_text("relative-state\n")
        cwd_a = self.root / "cwd-a"
        cwd_b = self.root / "cwd-b"
        cwd_a.mkdir()
        cwd_b.mkdir()
        env = synthetic_environment()
        env["HOME"] = str(home)
        env.pop("SANDBOX_HOME", None)
        env.pop("SANDBOX_RUNTIME", None)
        expected = (home / "sandbox").resolve()
        self.assertEqual(self._sandbox_base_probe(env, cwd=cwd_a), expected)
        self.assertEqual(self._sandbox_base_probe(env, cwd=cwd_b), expected)

    def test_registry_uses_only_the_selected_base(self):
        """A selector change does not search or merge another registry."""
        import sandbox_core

        home = self.root / "process-home"
        selected = self.root / "selected-base"
        fallback = home / "sandbox"
        hint = home / ".config" / "sandbox" / "home"
        hint.parent.mkdir(parents=True)
        hint.write_text(str(selected) + "\n")
        old_home = os.environ.get("HOME")
        old_sandbox_home = os.environ.get("SANDBOX_HOME")
        old_runtime = os.environ.get("SANDBOX_RUNTIME")
        try:
            os.environ["HOME"] = str(home)
            os.environ.pop("SANDBOX_HOME", None)
            os.environ.pop("SANDBOX_RUNTIME", None)
            sandbox_core.registry_put(
                str(self.root / "project"), instance="selected-instance",
            )
            self.assertTrue((selected / "runtime" / "registry.json").is_file())
            self.assertFalse((fallback / "runtime" / "registry.json").exists())
            self.assertEqual(
                sandbox_core.registry_find_instance("selected-instance")["instance"],
                "selected-instance",
            )
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_sandbox_home is None:
                os.environ.pop("SANDBOX_HOME", None)
            else:
                os.environ["SANDBOX_HOME"] = old_sandbox_home
            if old_runtime is None:
                os.environ.pop("SANDBOX_RUNTIME", None)
            else:
                os.environ["SANDBOX_RUNTIME"] = old_runtime

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
