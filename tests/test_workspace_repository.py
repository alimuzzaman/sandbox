"""Focused durable workspace-index contract tests."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from sandbox.workspaces.models import JobEvidence
from sandbox.workspaces.repository import (
    AliasCollisionError,
    WorkspaceIndexError,
    WorkspaceRepository,
    read_only_projection,
)


class WorkspaceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.legacy = self.root / "runtime" / "jobs" / "workspaces"
        self.index = self.root / "runtime" / "workspaces" / "index.sqlite3"
        self.repo = WorkspaceRepository(self.index, self.legacy)

    def tearDown(self):
        self.temp.cleanup()

    def _legacy(self, namespace="local:abc", label="default", raw=b'{"label":"default"}\n'):
        path = self.legacy / namespace / label
        path.mkdir(parents=True, exist_ok=True)
        metadata = path / "workspace.json"
        metadata.write_bytes(raw)
        return metadata

    def test_initialization_is_owner_only_wal_full_and_foreign_keys(self):
        self.assertEqual(self.repo.schema_generation(), 0)
        # PRAGMA foreign_keys is connection-local; inspect the repository's
        # configured connection rather than a fresh unconfigured sqlite handle.
        connection = self.repo._connect()
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        connection.close()
        self.assertEqual(self.index.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.index.parent.stat().st_mode & 0o777, 0o700)

    def test_register_find_get_alias_collision_and_idempotent_binding(self):
        record = self.repo.register("project:one", "default", aliases=("old",))
        self.assertEqual(self.repo.find("project:one", "default").workspace_id, record.workspace_id)
        self.assertEqual(self.repo.get(record.workspace_id).label, "default")
        with self.assertRaises(AliasCollisionError) as ctx:
            self.repo.register("project:two", "default", aliases=("old",))
        self.assertEqual(ctx.exception.code, "workspace_alias_collision")
        self.assertEqual(self.repo.register_alias(record.workspace_id, "old").workspace_id, record.workspace_id)
        self.repo.bind_resource(record.workspace_id, "compose_project", "sandbox-demo")
        other = self.repo.register("project:two", "other")
        with self.assertRaises(WorkspaceIndexError) as binding_error:
            self.repo.bind_resource(other.workspace_id, "compose_project", "sandbox-demo")
        self.assertEqual(binding_error.exception.code, "workspace_alias_collision")

    def test_tombstone_is_indexed_with_reason(self):
        record = self.repo.register("project:tombstone", "default")
        tombstone = self.repo.tombstone(record.workspace_id, reason="operator-request")
        self.assertEqual(tombstone.status, "destroyed")
        self.assertEqual(tombstone.lifecycle, "destroyed")
        self.assertEqual(tombstone.metadata["tombstone_reason"], "operator-request")
        with self.assertRaises(WorkspaceIndexError) as revive:
            self.repo.mark_lifecycle(record.workspace_id, "ready", status="ready")
        self.assertEqual(revive.exception.code, "workspace_lifecycle_invalid")

    def test_pre_release_lifecycle_values_normalize_on_open(self):
        record = self.repo.register("project:legacy-state", "default")
        connection = sqlite3.connect(self.index)
        connection.execute(
            "UPDATE workspaces SET lifecycle='active',status='adoptable' "
            "WHERE workspace_id=?", (record.workspace_id,))
        connection.commit()
        connection.close()
        reopened = WorkspaceRepository(self.index, self.legacy)
        normalized = reopened.get(record.workspace_id)
        self.assertEqual((normalized.lifecycle, normalized.status), ("ready", "ready"))

    def test_register_raw_job_row_derives_target_namespace(self):
        project_root = "/tmp/example"
        short = hashlib.sha256(project_root.encode()).hexdigest()[:12]
        self._legacy(f"remote-vps-{short}", "ci", b'{"label":"ci"}\n')
        plan = self.repo.migration_plan(job_inputs=[{
            "project_root": project_root, "project_identity": "project:one",
            "target_kind": "remote", "remote_name": "vps", "workspace_label": "ci",
        }])
        self.assertEqual(plan.summary, {"adoptable": 1})

    def test_direct_and_combined_job_project_evidence_are_not_dropped(self):
        self._legacy()
        plan = self.repo.migration_plan(
            evidence={
                "jobs": [{"project_identity": "project:one", "namespace": "local:abc", "label": "default"}],
                "projects": [{"project_identity": "project:two", "namespace": "local:def", "label": "other"}],
            }
        )
        self.assertEqual(plan.summary, {"adoptable": 1})
        direct = self.repo.migration_plan(
            evidence={"project_identity": "project:one", "namespace": "local:abc", "label": "default"}
        )
        self.assertEqual(direct.summary, {"adoptable": 1})

    def test_legacy_unresolved_is_visible_not_false_empty(self):
        metadata = self._legacy()
        rows = self.repo.list("project:missing")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "unresolved")
        self.assertEqual(metadata.read_bytes(), b'{"label":"default"}\n')

    def test_migration_adopts_once_and_preserves_bytes(self):
        metadata = self._legacy()
        before = metadata.read_bytes()
        plan = self.repo.migration_plan(evidence=[JobEvidence("project:one", "local:abc", "default")])
        result = self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(metadata.read_bytes(), before)
        replay = self.repo.migration_apply(plan.plan_id, confirm=True)
        self.assertTrue(replay["already_applied"])
        adopted = self.repo.list("project:one", include_legacy=False)
        self.assertEqual(len(adopted), 1)
        self.assertEqual((adopted[0].lifecycle, adopted[0].status), ("ready", "ready"))
        self.assertEqual(adopted[0].aliases, ("legacy:local:abc:default",))

    def test_scoped_plan_is_bound_to_complete_legacy_inventory(self):
        self._legacy("local:a", "default", b'{"label":"default"}\n')
        unrelated = self._legacy("local:b", "other", b'{"label":"other"}\n')
        evidence = [
            JobEvidence("project:a", "local:a", "default"),
            JobEvidence("project:b", "local:b", "other"),
        ]
        plan = self.repo.migration_plan("project:a", evidence=evidence)
        self.assertEqual(plan.summary, {"adoptable": 1})
        unrelated.write_bytes(b'{"label":"other","changed":true}\n')
        with self.assertRaises(WorkspaceIndexError) as stale:
            self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(stale.exception.code, "workspace_migration_plan_stale")

    def test_legacy_alias_collision_rolls_back_workspace_adoption(self):
        self._legacy()
        owner = self.repo.register("project:owner", "owner")
        self.repo.register_alias(owner.workspace_id, "legacy:local:abc:default")
        plan = self.repo.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        with self.assertRaises(WorkspaceIndexError) as caught:
            self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(caught.exception.code, "workspace_alias_collision")
        self.assertIsNone(self.repo.find("project:one", "default"))

    def test_evidence_order_is_canonical_for_digest_bound_apply(self):
        self._legacy()
        first = JobEvidence("project:one", "local:abc", "default")
        second = JobEvidence("project:two", "local:def", "other")
        plan = self.repo.migration_plan(evidence=[first, second])
        result = self.repo.migration_apply(
            plan,
            confirm=True,
            evidence=[second, first],
        )
        self.assertEqual(result["inserted"], 1)

    def test_migration_plan_is_deeply_immutable(self):
        plan = self.repo.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        with self.assertRaises(TypeError):
            plan.summary["unexpected"] = 1
        with self.assertRaises(TypeError):
            plan.evidence[0]["project_identity"] = "project:changed"

    def test_unresolved_plan_records_audit_but_does_not_claim_workspace(self):
        metadata = self._legacy()
        before = metadata.read_bytes()
        plan = self.repo.migration_plan()
        result = self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(self.repo.list(include_legacy=False), [])
        self.assertEqual(self.repo.list()[0].status, "unresolved")
        self.assertEqual(metadata.read_bytes(), before)

    def test_concurrent_apply_commits_one_adoption_and_replays_the_receipt(self):
        self._legacy()
        plan = self.repo.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _value: self.repo.migration_apply(plan.plan_id, confirm=True),
                range(2),
            ))
        self.assertEqual(sorted(item["inserted"] for item in results), [1, 1])
        self.assertEqual(sum(bool(item.get("already_applied")) for item in results), 1)
        self.assertEqual(len(self.repo.list(include_legacy=False)), 1)

    def test_migration_collision_rolls_back_every_candidate(self):
        self._legacy("local:abc", "default", b'{"label":"default"}\n')
        self._legacy("local:def", "other", b'{"label":"other"}\n')
        existing = self.repo.register("project:one", "default")
        plan = self.repo.migration_plan(evidence=[
            JobEvidence("project:one", "local:abc", "default"),
            JobEvidence("project:two", "local:def", "other"),
        ])
        with self.assertRaises(WorkspaceIndexError) as caught:
            self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(caught.exception.code, "migration_collision")
        indexed = self.repo.list(include_legacy=False)
        self.assertEqual([item.workspace_id for item in indexed], [existing.workspace_id])

    def test_index_relocation_preserves_ids_aliases_and_bindings(self):
        old = self.root / "old-home"
        repository = WorkspaceRepository(
            old / "runtime" / "workspaces" / "index.sqlite3",
            old / "runtime" / "jobs" / "workspaces",
        )
        record = repository.register("project:move", "default", aliases=("stable-alias",))
        repository.bind_resource(record.workspace_id, "compose_project", "sandbox-move")
        new = self.root / "new-home"
        old.rename(new)
        reopened = WorkspaceRepository(
            new / "runtime" / "workspaces" / "index.sqlite3",
            new / "runtime" / "jobs" / "workspaces",
        )
        moved = reopened.get(record.workspace_id)
        self.assertEqual(moved.workspace_id, record.workspace_id)
        self.assertEqual(moved.aliases, ("stable-alias",))
        self.assertEqual(moved.bindings[0]["resource_id"], "sandbox-move")

    def test_v0_schema_upgrades_after_existing_rows_without_losing_data(self):
        v0_index = self.root / "v0" / "runtime" / "workspaces" / "index.sqlite3"
        v0_index.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(v0_index)
        connection.execute(
            "CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY, label TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO workspaces(workspace_id,label) VALUES('legacy-id','default')"
        )
        connection.commit()
        connection.close()

        upgraded = WorkspaceRepository(v0_index, self.legacy)
        record = upgraded.get("legacy-id")
        self.assertEqual(record.label, "default")
        self.assertEqual(upgraded.schema_generation(), 0)
        self.assertEqual(
            {row[1] for row in sqlite3.connect(v0_index).execute("PRAGMA table_info(workspaces)")}
            >= {"project_identity", "namespace", "path", "metadata_json", "updated_at"},
            True,
        )

    def test_v0_upgrade_rolls_back_added_columns_when_index_creation_fails(self):
        v0_index = self.root / "v0-rollback" / "runtime" / "workspaces" / "index.sqlite3"
        v0_index.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(v0_index)
        connection.execute(
            "CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY, label TEXT NOT NULL, path TEXT)"
        )
        connection.executemany(
            "INSERT INTO workspaces(workspace_id,label,path) VALUES(?,?,?)",
            [("one", "one", "/duplicate"), ("two", "two", "/duplicate")],
        )
        connection.commit()
        connection.close()

        repository = WorkspaceRepository.__new__(WorkspaceRepository)
        repository.index_path = v0_index
        repository.legacy_root = self.legacy
        repository.job_index_reader = None
        repository.clock = None
        repository.plan_ttl_seconds = 900
        with self.assertRaises(sqlite3.IntegrityError):
            repository.initialize()
        columns = {row[1] for row in sqlite3.connect(v0_index).execute("PRAGMA table_info(workspaces)")}
        self.assertNotIn("project_identity", columns)
        self.assertNotIn("updated_at", columns)

    def test_relocated_absolute_legacy_path_is_accepted_without_duplicate(self):
        old_home = self.root / "old-home"
        new_home = self.root / "new-home"
        old_workspace = old_home / "runtime" / "jobs" / "workspaces" / "local-abc" / "default"
        old_workspace.mkdir(parents=True)
        metadata = old_workspace / "workspace.json"
        metadata.write_text(
            '{"label":"default","namespace":"local:abc",'
            f'"path":"{old_workspace}"}}\n'
        )
        old_home.rename(new_home)
        new_legacy = new_home / "runtime" / "jobs" / "workspaces"
        repository = WorkspaceRepository(
            new_home / "runtime" / "workspaces" / "index.sqlite3", new_legacy
        )
        plan = repository.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        self.assertEqual(plan.summary, {"adoptable": 1})
        result = repository.migration_apply(plan, confirm=True)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(len(repository.list(include_legacy=False)), 1)

    def test_existing_index_repoints_after_home_relocation_without_duplicate(self):
        old_home = self.root / "old-index-home"
        old_legacy = old_home / "runtime" / "jobs" / "workspaces"
        old_workspace = old_legacy / "local-abc" / "default"
        old_workspace.mkdir(parents=True)
        (old_workspace / "workspace.json").write_text(
            '{"label":"default","namespace":"local:abc",'
            f'"path":"{old_workspace}"}}\n'
        )
        old_repository = WorkspaceRepository(
            old_home / "runtime" / "workspaces" / "index.sqlite3", old_legacy
        )
        first_plan = old_repository.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        old_repository.migration_apply(first_plan, confirm=True)
        old_home.rename(self.root / "new-index-home")
        new_home = self.root / "new-index-home"
        new_repository = WorkspaceRepository(
            new_home / "runtime" / "workspaces" / "index.sqlite3",
            new_home / "runtime" / "jobs" / "workspaces",
        )
        second_plan = new_repository.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        generation_before_relocation = new_repository.schema_generation()
        result = new_repository.migration_apply(second_plan, confirm=True)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["relocated"], 1)
        self.assertEqual(
            new_repository.schema_generation(), generation_before_relocation)
        indexed = new_repository.list("project:one", include_legacy=False)
        self.assertEqual(len(indexed), 1)
        self.assertIn(
            str(new_home / "runtime" / "jobs" / "workspaces" / "local-abc" / "default" / "workspace.json"),
            indexed[0].path,
        )

        # Relocation serializes against both the new plan identity and the
        # pre-existing durable row identity.
        third_plan = new_repository.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")]
        )
        acquired = []
        original = new_repository.operation_lock

        @contextmanager
        def recording(operation="workspace-migration", **kwargs):
            acquired.append(operation)
            with original(operation, **kwargs):
                yield

        with mock.patch.object(
                new_repository, "operation_lock", side_effect=recording):
            new_repository.migration_apply(third_plan, confirm=True)
        self.assertIn(indexed[0].workspace_id, acquired)

    def test_ownership_projection_does_not_create_an_index(self):
        projection_root = self.root / "projection-only"
        legacy = projection_root / "runtime" / "jobs" / "workspaces"
        legacy.mkdir(parents=True)
        metadata = legacy / "local-abc" / "default" / "workspace.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text("{}")
        index = projection_root / "runtime" / "workspaces" / "index.sqlite3"
        repository = WorkspaceRepository.__new__(WorkspaceRepository)
        repository.index_path = index
        repository.legacy_root = legacy
        repository.job_index_reader = None
        repository.clock = None
        repository.plan_ttl_seconds = 900
        projection = repository.ownership_projection()
        self.assertEqual(projection["counts"]["unresolved"], 1)
        self.assertTrue(projection["records"][0]["observed_at"])
        self.assertFalse(index.exists())

    def test_constructor_free_projection_and_operation_locks_are_scoped(self):
        projection_root = self.root / "projection-classmethod"
        legacy = projection_root / "runtime" / "jobs" / "workspaces"
        legacy.mkdir(parents=True)
        index = projection_root / "runtime" / "workspaces" / "index.sqlite3"
        before = set(index.parent.glob("*") if index.parent.exists() else ())
        class_projection = WorkspaceRepository.read_only_projection(index, legacy)
        module_projection = read_only_projection(index, legacy)
        self.assertEqual(class_projection["counts"], module_projection["counts"])
        self.assertFalse(index.exists())
        self.assertEqual(before, set(index.parent.glob("*") if index.parent.exists() else ()))

        with self.repo.operation_lock("workspace-one"):
            with self.repo.operation_lock("workspace-two"):
                pass
        lock_names = {path.name for path in self.index.parent.glob(".operation-*.lock")}
        self.assertEqual(len(lock_names), 2)

    def test_operation_lock_timeout_is_bounded_and_stable(self):
        import fcntl

        def busy_on_acquire(_descriptor, flags):
            if flags & fcntl.LOCK_NB:
                raise BlockingIOError()

        with mock.patch("fcntl.flock", side_effect=busy_on_acquire):
            with self.assertRaises(WorkspaceIndexError) as busy:
                with self.repo.operation_lock("busy", timeout_seconds=0):
                    pass
        self.assertEqual(busy.exception.code, "workspace_busy")

    def test_migration_apply_acquires_candidate_workspace_lock(self):
        self._legacy()
        plan = self.repo.migration_plan(
            evidence=[JobEvidence("project:one", "local:abc", "default")])
        candidate = plan.items[0].workspace_id
        original = self.repo.operation_lock
        acquired = []

        @contextmanager
        def recording(operation="workspace-migration", **kwargs):
            acquired.append(operation)
            with original(operation, **kwargs):
                yield

        with mock.patch.object(self.repo, "operation_lock", side_effect=recording):
            self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(acquired, ["workspace-migration", candidate])

    def test_startup_reconcile_skips_a_live_operation_lock(self):
        record = self.repo.register("project:locked", "unit")
        self.repo.mark_lifecycle(record.workspace_id, "destroying", status="destroying")
        with self.repo.operation_lock(record.workspace_id):
            self.assertEqual(self.repo.reconcile_startup(), [])
            self.assertEqual(self.repo.get(record.workspace_id).lifecycle, "destroying")
        self.assertEqual(self.repo.reconcile_startup(), [record.workspace_id])
        self.assertEqual(self.repo.get(record.workspace_id).lifecycle, "indeterminate")

    def test_projection_includes_generation_digests_and_completeness(self):
        record = self.repo.register(
            "project:projection", "unit", path=str(self.root / "workspace.json"),
            aliases=("legacy:local-projection:unit",),
        )
        self.repo.bind_resource(
            record.workspace_id, "compose_project", "sandbox-projection")
        projection = self.repo.ownership_projection()
        projected = projection["records"][0]
        self.assertEqual(
            (projected["owner_kind"], projected["owner_id"]),
            ("workspace", record.workspace_id),
        )
        self.assertEqual(projected["index_generation"], projection["index_generation"])
        self.assertTrue(projected["complete"])
        self.assertIsNone(projected["error"])
        self.assertTrue(projected["locator_digest"].startswith("sha256:"))
        self.assertTrue(projected["evidence_digest"].startswith("sha256:"))
        self.assertTrue(projected["alias_evidence"])
        self.assertIn("jobs", projected["active_references"])
        self.assertTrue(projected["observed_at"])

    def test_projection_counts_only_active_jobs_by_project_and_label(self):
        repository = WorkspaceRepository(
            self.index, self.legacy,
            job_index_reader=lambda: {"jobs": [
                {"project_identity": "project:jobs", "workspace_label": "unit",
                 "lifecycle": "running"},
                {"project_identity": "project:jobs", "workspace_label": "unit",
                 "lifecycle": "succeeded"},
                {"project_identity": "project:other", "workspace_label": "unit",
                 "lifecycle": "running"},
            ]},
        )
        repository.register("project:jobs", "unit")
        projected = repository.ownership_projection()["records"][0]
        self.assertEqual(
            projected["active_references"],
            {"jobs": 1, "leases": 1, "containers": None, "mounts": None},
        )

    def test_confirmation_and_stale_plan_codes(self):
        self._legacy()
        plan = self.repo.migration_plan()
        with self.assertRaises(WorkspaceIndexError) as ctx:
            self.repo.migration_apply(plan)
        self.assertEqual(ctx.exception.code, "confirmation_required")
        self.repo.register("project:new", "other")
        with self.assertRaises(WorkspaceIndexError) as ctx:
            self.repo.migration_apply(plan, confirm=True)
        self.assertEqual(ctx.exception.code, "workspace_migration_plan_stale")

    def test_plan_assertions_fail_before_persisting_a_plan(self):
        with self.assertRaises(WorkspaceIndexError) as digest_error:
            self.repo.migration_plan(expected_inventory_digest="wrong")
        self.assertEqual(digest_error.exception.code, "workspace_migration_plan_stale")
        with self.assertRaises(WorkspaceIndexError) as generation_error:
            self.repo.migration_plan(expected_generation=99)
        self.assertEqual(generation_error.exception.code, "workspace_migration_plan_stale")
        connection = self.repo._connect()
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM workspace_plans").fetchone()[0], 0)
        connection.close()

    def test_project_filter_does_not_turn_conflicting_evidence_into_unresolved(self):
        self._legacy()
        evidence = [
            JobEvidence("project:one", "local:abc", "default"),
            JobEvidence("project:two", "local:abc", "default"),
        ]
        plan = self.repo.migration_plan("project:one", evidence=evidence)
        self.assertEqual(plan.summary, {"conflict": 1})

    def test_scoped_apply_keeps_conflict_and_excludes_unrelated_record(self):
        self._legacy("local:abc", "default")
        self._legacy("local:abc", "other", b'{"label":"other"}\n')
        evidence = [
            JobEvidence("project:one", "local:abc", "default"),
            JobEvidence("project:two", "local:abc", "default"),
            JobEvidence("project:two", "local:abc", "other"),
        ]
        plan = self.repo.migration_plan("project:one", evidence=evidence)
        self.assertEqual(plan.summary, {"conflict": 1})
        result = self.repo.migration_apply(plan, confirm=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(self.repo.list(include_legacy=False), [])


if __name__ == "__main__":
    unittest.main()
