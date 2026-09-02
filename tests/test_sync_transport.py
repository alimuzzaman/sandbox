import tempfile
import threading
import unittest
from pathlib import Path

from sandbox.sync.models import (
    DivergenceRecord,
    Participant,
    PinnedJob,
    SourceGeneration,
    SynchronizationRelationship,
    failure_envelope,
    success_envelope,
    validate_sync_envelope,
)
from sandbox.sync.repository import SyncRepository
from sandbox.sync.projection import (
    ProjectionRefused,
    authorize_projection,
    detect_divergence,
    validate_isolated_outputs,
)


class SyncTransportContractTests(unittest.TestCase):
    def setUp(self):
        self.relationship = SynchronizationRelationship(
            relationship_id="rel_fixture", project_identity="project_fixture",
            remote_name="remote-fixture", workspace_id="workspace_fixture",
            mode="live", lifecycle="active", owner_generation=1,
            accepted_generation_id="gen_fixture", updated_at="2026-08-26T00:00:00Z",
        )
        self.generation = SourceGeneration(
            generation_id="gen_fixture", relationship_id="rel_fixture", sequence=1,
            manifest_digest="a" * 64, file_count=2, byte_count=10,
            lifecycle="accepted", request_id="request_fixture", commit="1" * 40,
            created_at="2026-08-26T00:00:00Z", accepted_at="2026-08-26T00:00:01Z",
        )

    def test_success_envelope_matches_the_bounded_path_free_contract(self):
        envelope = success_envelope(
            self.relationship, self.generation, active_generation="gen_fixture",
        )
        self.assertEqual(validate_sync_envelope(envelope), envelope)
        serialized = repr(envelope)
        for forbidden in ("path", "contents", "argv", "environment", "ssh"):
            self.assertNotIn(forbidden, serialized.lower())

    def test_failure_envelope_redacts_credential_syntax(self):
        envelope = failure_envelope(
            code="credential_detected", status="refused",
            relationship_id="rel_fixture", remote_name="remote-fixture",
            request_id="request_fixture", retryable=False,
            message="token=synthetic_fixture_value should not be sent",
        )
        self.assertNotIn("synthetic_fixture_value", envelope["message"])
        self.assertEqual(validate_sync_envelope(envelope), envelope)

    def test_unknown_acknowledgment_preserves_replay_identity(self):
        envelope = failure_envelope(
            code="transport_unknown", status="unknown",
            relationship_id="rel_fixture", remote_name="remote-fixture",
            request_id="request_fixture", accepted_generation="gen_fixture",
            pending_generation="gen_pending", retryable=True,
        )
        self.assertEqual(envelope["request_id"], "request_fixture")
        self.assertEqual(envelope["pending_generation"], "gen_pending")
        self.assertEqual(validate_sync_envelope(envelope), envelope)

    def test_malformed_or_expanded_envelopes_are_rejected(self):
        good = success_envelope(self.relationship, self.generation)
        cases = [
            {**good, "source_path": "/private/source"},
            {**good, "ok": "yes"},
            {**good, "relationship": {**good["relationship"], "token": "value"}},
            {**good, "generation": {**good["generation"], "file_count": -1}},
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises((KeyError, TypeError, ValueError)):
                    validate_sync_envelope(value)

    def test_models_reject_unsafe_identifiers_naive_timestamps_and_unbounded_counts(self):
        with self.assertRaises(ValueError):
            SynchronizationRelationship(
                relationship_id="../../unsafe", project_identity="project",
                remote_name="remote", workspace_id="workspace",
            )
        with self.assertRaises(ValueError):
            SynchronizationRelationship(
                relationship_id="rel", project_identity="project", remote_name="remote",
                workspace_id="workspace", updated_at="2026-08-26T00:00:00",
            )
        with self.assertRaises(ValueError):
            SourceGeneration(
                generation_id="gen", relationship_id="rel", sequence=1,
                manifest_digest="a" * 64, file_count=1_000_001, byte_count=0,
                lifecycle="pending", request_id="request",
            )

    def test_participant_job_and_divergence_values_round_trip_without_paths(self):
        participant = Participant(
            "participant_fixture", "rel_fixture", "2026-08-26T00:00:00Z", "observer",
        )
        job = PinnedJob(
            "job_fixture", "rel_fixture", "gen_fixture",
            source_access="managed_read_only", parallel_safe=True,
        )
        divergence = DivergenceRecord(
            "rel_fixture", 2, "gen_fixture", "2026-08-26T00:00:01Z", "manual_review",
        )
        self.assertEqual(Participant.from_dict(participant.as_dict()), participant)
        self.assertEqual(PinnedJob.from_dict(job.as_dict()), job)
        self.assertEqual(DivergenceRecord.from_dict(divergence.as_dict()), divergence)
        self.assertNotIn("path", repr((participant, job, divergence)).lower())

    def test_concurrent_participants_coalesce_one_source_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = SyncRepository(Path(temporary) / "journal.json")
            repository.put_relationship(self.relationship)
            barrier = threading.Barrier(8)
            results = []
            errors = []

            def participate(index):
                try:
                    barrier.wait()
                    current = SyncRepository(repository.path)
                    current.register_participant(Participant(
                        f"participant_{index}", self.relationship.relationship_id,
                        "2026-08-26T00:00:02Z",
                    ))
                    request_id = f"request_{index}"
                    digest = current.canonical_request_digest({
                        "request_id": request_id,
                        "manifest_digest": "a" * 64,
                    })
                    results.append(current.reserve_generation(
                        relationship_id=self.relationship.relationship_id,
                        request_id=request_id, request_digest=digest,
                        manifest_digest="a" * 64, file_count=2, byte_count=10,
                    ))
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=participate, args=(index,))
                       for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(repository.list_participants(
                self.relationship.relationship_id)), 8)
            self.assertEqual(len({item[0].generation_id for item in results}), 1)
            self.assertEqual(len(repository.list_generations(
                self.relationship.relationship_id)), 1)

    def test_managed_source_is_read_only_and_isolated_writes_are_artifact_only(self):
        managed = authorize_projection(
            self.relationship, self.generation,
            requested_generation_id="gen_fixture",
            source_access="managed_read_only",
        )
        self.assertTrue(managed.read_only)
        self.assertFalse(managed.isolated)
        isolated = authorize_projection(
            self.relationship, self.generation,
            requested_generation_id="gen_fixture",
            source_access="isolated_copy",
        )
        self.assertTrue(isolated.isolated)
        self.assertTrue(isolated.artifact_only_output)
        self.assertEqual(
            validate_isolated_outputs(("reports/result.json", "reports/result.json")),
            ("reports/result.json",),
        )
        for unsafe in ((), ("/private/output",), ("../escape",)):
            with self.subTest(unsafe=unsafe), self.assertRaises(ProjectionRefused):
                validate_isolated_outputs(unsafe)

    def test_newest_pending_and_diverged_source_refuse_before_projection(self):
        pending = SynchronizationRelationship(
            **{
                **self.relationship.as_dict(),
                "pending_generation_id": "gen_pending",
            }
        )
        with self.assertRaisesRegex(ProjectionRefused, "generation_pending"):
            authorize_projection(
                pending, self.generation,
                requested_generation_id="gen_pending",
                source_access="managed_read_only",
            )
        divergence = detect_divergence(
            self.relationship, self.generation,
            observed_manifest_digest="b" * 64, affected_count=1,
        )
        self.assertIsNotNone(divergence)
        with self.assertRaisesRegex(ProjectionRefused, "divergence"):
            authorize_projection(
                self.relationship, self.generation,
                requested_generation_id="gen_fixture",
                source_access="managed_read_only", divergence=divergence,
            )
        self.assertIsNone(detect_divergence(
            self.relationship, self.generation,
            observed_manifest_digest=self.generation.manifest_digest,
            affected_count=0,
        ))


if __name__ == "__main__":
    unittest.main()
