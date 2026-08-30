import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.config.facade import resolve_project_identity
from sandbox.sync.models import DivergenceRecord, Participant, SynchronizationRelationship
from sandbox.sync.repository import (
    RelationshipConflict,
    RequestDigestConflict,
    SyncJournalCorruption,
    SyncRepository,
)


def relationship(identifier="rel_fixture", project="project_fixture", workspace="workspace_fixture"):
    return SynchronizationRelationship(
        relationship_id=identifier, project_identity=project,
        remote_name="remote-fixture", workspace_id=workspace,
        updated_at="2026-08-26T00:00:00Z",
    )


class SyncStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "runtime" / "sync" / "journal.json"
        self.repo = SyncRepository(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def reserve(self, request_id="request-1", manifest="a" * 64):
        digest = self.repo.canonical_request_digest({"request": request_id, "manifest": manifest})
        return self.repo.reserve_generation(
            relationship_id="rel_fixture", request_id=request_id, request_digest=digest,
            manifest_digest=manifest, file_count=2, byte_count=10,
            commit="1" * 40, dirty_digest="b" * 64,
            created_at="2026-08-26T00:00:01Z",
        )

    def test_relationship_crud_is_owner_only_and_atomic(self):
        self.repo.put_relationship(relationship())

        reopened = SyncRepository(self.path)
        self.assertEqual(reopened.get_relationship("rel_fixture"), relationship())
        self.assertEqual(reopened.find_relationship(
            "project_fixture", "remote-fixture", "workspace_fixture",
        ).relationship_id, "rel_fixture")
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.repo.lock_path.stat().st_mode & 0o777, 0o600)

    def test_legacy_relationship_without_conflict_identity_loads_compatibly(self):
        legacy = relationship().as_dict()
        legacy.pop("conflict_code")
        legacy.pop("conflict_request_id")
        legacy.pop("conflict_generation_id")
        loaded = SynchronizationRelationship.from_dict(legacy)
        self.assertIsNone(loaded.conflict_code)
        self.assertIsNone(loaded.conflict_request_id)
        self.assertIsNone(loaded.conflict_generation_id)

    def test_conflict_persistence_keeps_schema_v1_relationship_rollback_readable(self):
        self.repo.put_relationship(relationship())
        generation, _ = self.reserve()
        self.repo.transition_generation(generation.generation_id, "transferring")
        self.repo.transition_generation(
            generation.generation_id, "refused", refusal_code="ownership_conflict")
        document = json.loads(self.path.read_text())
        old_relationship_fields = {
            "relationship_id", "project_identity", "remote_name", "workspace_id",
            "mode", "lifecycle", "owner_generation", "accepted_generation_id",
            "pending_generation_id", "updated_at",
        }
        self.assertEqual(set(document["relationships"]["rel_fixture"]),
                         old_relationship_fields)
        self.assertEqual(document["conflicts"]["rel_fixture"], {
            "code": "ownership_conflict", "request_id": "request-1",
            "generation_id": generation.generation_id,
        })
        loaded = SyncRepository(self.path).get_relationship("rel_fixture")
        self.assertEqual(loaded.conflict_code, "ownership_conflict")

    def test_default_journal_is_scoped_below_sandbox_home_runtime_sync(self):
        with patch.dict(os.environ, {"SANDBOX_HOME": self.temporary.name}):
            repo = SyncRepository()
            repo.put_relationship(relationship())
        self.assertEqual(
            repo.path,
            Path(self.temporary.name).resolve() / "runtime" / "sync" / "journal.json",
        )

    def test_relationship_ownership_key_is_unique_and_cannot_be_reassigned(self):
        self.repo.put_relationship(relationship())
        with self.assertRaises(RelationshipConflict):
            self.repo.put_relationship(relationship(identifier="rel_other"))
        with self.assertRaises(RelationshipConflict):
            self.repo.put_relationship(relationship(project="project_other"))

    def test_unused_relationship_can_be_deleted_but_history_is_retained(self):
        self.repo.put_relationship(relationship())
        self.assertTrue(self.repo.delete_relationship("rel_fixture"))
        self.assertFalse(self.repo.delete_relationship("rel_fixture"))
        self.repo.put_relationship(relationship())
        self.reserve()
        with self.assertRaises(RelationshipConflict):
            self.repo.delete_relationship("rel_fixture")

    def test_replay_returns_the_same_generation_and_digest_conflict_is_atomic(self):
        self.repo.put_relationship(relationship())
        first, replay = self.reserve()
        second, second_replay = self.reserve()
        self.assertFalse(replay)
        self.assertTrue(second_replay)
        self.assertEqual(first, second)

        with self.assertRaises(RequestDigestConflict):
            self.repo.reserve_generation(
                relationship_id="rel_fixture", request_id="request-1",
                request_digest="c" * 64, manifest_digest="d" * 64,
                file_count=1, byte_count=1,
            )
        self.assertEqual(self.repo.lookup_request("rel_fixture", "request-1"), first)

    def test_generation_sequence_is_monotonic_and_acceptance_updates_relationship(self):
        self.repo.put_relationship(relationship())
        first, _ = self.reserve("request-1", "a" * 64)
        second, _ = self.reserve("request-2", "c" * 64)
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.repo.transition_generation(first.generation_id, "transferring")
        accepted = self.repo.transition_generation(
            first.generation_id, "accepted", accepted_at="2026-08-26T00:01:00Z",
        )
        current = self.repo.get_relationship("rel_fixture")
        self.assertEqual(accepted.lifecycle, "accepted")
        self.assertEqual(current.accepted_generation_id, first.generation_id)
        self.assertEqual(current.pending_generation_id, second.generation_id)

    def test_only_one_concurrent_transfer_claim_can_launch(self):
        self.repo.put_relationship(relationship())
        generation, _ = self.reserve()
        claims = []
        errors = []
        barrier = threading.Barrier(8)

        def claimant():
            try:
                barrier.wait()
                _generation, claimed = SyncRepository(
                    self.path).claim_generation_transfer(generation.generation_id)
                claims.append(claimed)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claimant) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(claims.count(True), 1)
        self.assertEqual(claims.count(False), 7)

    def test_concurrent_writers_allocate_unique_monotonic_sequences(self):
        self.repo.put_relationship(relationship())
        results = []
        errors = []
        barrier = threading.Barrier(8)

        def writer(index):
            try:
                barrier.wait()
                repo = SyncRepository(self.path)
                digest = repo.canonical_request_digest({"index": index})
                generation, _ = repo.reserve_generation(
                    relationship_id="rel_fixture", request_id=f"request-{index}",
                    request_digest=digest, manifest_digest=f"{index:064x}",
                    file_count=1, byte_count=1,
                    created_at="2026-08-26T00:00:01Z",
                )
                results.append(generation.sequence)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), list(range(1, 9)))

    def test_corrupt_journal_fails_closed_without_replacement(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{")
        with self.assertRaises(SyncJournalCorruption):
            self.repo.list_relationships()
        self.assertEqual(self.path.read_text(), "{")

    def test_failed_atomic_replace_preserves_prior_journal(self):
        self.repo.put_relationship(relationship())
        before = self.path.read_bytes()
        broken = SyncRepository(self.path)
        with patch.object(broken, "_replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                broken.put_relationship(relationship(identifier="rel_second", workspace="workspace_second"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(self.path.read_text())["schema_version"], 1)

    def test_workspace_owner_lookup_refuses_a_competing_project_identity(self):
        self.repo.put_relationship(relationship())
        owner = self.repo.find_workspace_owner("remote-fixture", "workspace_fixture")
        self.assertEqual(owner.project_identity, "project_fixture")
        self.assertIsNone(self.repo.find_workspace_owner("other", "workspace_fixture"))

    def test_resolved_identity_coalesces_symlinks_and_separates_unadopted_roots(self):
        project = Path(self.temporary.name) / "project"
        project.mkdir()
        symlink = Path(self.temporary.name) / "project-link"
        symlink.symlink_to(project, target_is_directory=True)
        relocated = Path(self.temporary.name) / "relocated"
        relocated.mkdir()
        fresh_clone = Path(self.temporary.name) / "fresh-clone"
        fresh_clone.mkdir()

        def load(root, label=None):
            return {"root": root, "kind": "wordpress", "label": label or "default"}

        canonical = resolve_project_identity(project, config_loader=load)
        through_symlink = resolve_project_identity(symlink, config_loader=load)
        moved_without_adoption = resolve_project_identity(relocated, config_loader=load)
        independent_clone = resolve_project_identity(fresh_clone, config_loader=load)

        self.assertEqual(through_symlink["identity"], canonical["identity"])
        self.assertEqual(through_symlink["canonical_root"], canonical["canonical_root"])
        self.assertNotEqual(moved_without_adoption["identity"], canonical["identity"])
        self.assertNotEqual(independent_clone["identity"], canonical["identity"])

    def test_participant_heartbeat_is_bounded_and_replaces_same_session(self):
        self.repo.put_relationship(relationship())
        first = Participant(
            "participant_fixture", "rel_fixture", "2026-08-26T00:00:01Z",
        )
        later = Participant(
            "participant_fixture", "rel_fixture", "2026-08-26T00:00:02Z", "observer",
        )
        self.repo.register_participant(first)
        self.repo.register_participant(later)
        self.assertEqual(self.repo.list_participants("rel_fixture"), [later])

    def test_mode_transition_preserves_pending_generation_when_stopped(self):
        self.repo.put_relationship(relationship())
        generation, _ = self.reserve()
        live = self.repo.set_mode("rel_fixture", "live", lifecycle="active")
        stopped = self.repo.set_mode("rel_fixture", "off", lifecycle="stopped")
        self.assertEqual(live.mode, "live")
        self.assertEqual(stopped.pending_generation_id, generation.generation_id)

    def test_divergence_and_aggregate_metrics_never_persist_paths(self):
        self.repo.put_relationship(relationship())
        divergence = DivergenceRecord(
            "rel_fixture", 2, "gen_fixture", "2026-08-26T00:00:03Z",
            "explicit_resolution_required",
        )
        self.repo.put_divergence(divergence)
        self.repo.record_metrics(
            "rel_fixture", outcome="unknown", file_count=2, byte_count=10,
            observed_at="2026-08-26T00:00:04Z",
        )
        self.assertEqual(self.repo.get_divergence("rel_fixture"), divergence)
        self.assertEqual(self.repo.metrics("rel_fixture"), {
            "attempts": 1, "accepted": 0, "refused": 0, "failed": 0,
            "unknown": 1, "file_count": 2, "byte_count": 10,
            "observed_at": "2026-08-26T00:00:04Z",
        })
        serialized = self.path.read_text()
        self.assertNotIn("path", serialized.lower())
        self.assertTrue(self.repo.clear_divergence("rel_fixture"))

    def test_generation_listing_is_bounded_to_one_relationship(self):
        self.repo.put_relationship(relationship())
        first, _ = self.reserve("request-1", "a" * 64)
        second, _ = self.reserve("request-2", "b" * 64)
        self.assertEqual(
            [item.generation_id for item in self.repo.list_generations("rel_fixture")],
            [first.generation_id, second.generation_id],
        )


if __name__ == "__main__":
    unittest.main()
