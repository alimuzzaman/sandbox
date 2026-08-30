import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sandbox.sync.repository import SyncRepository
from sandbox.sync.service import SyncService


class SyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Sync Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "sync@example.test"], cwd=self.root, check=True)
        (self.root / "source.txt").write_text("safe\n")
        subprocess.run(["git", "add", "source.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.repository = SyncRepository(Path(self.temporary.name) / "journal.json")
        self.transfers = []

        def identity(project_dir, *, remote):
            return {"identity": "project:fixture", "root": str(Path(project_dir).resolve())}

        class Transport:
            def transfer(inner, project_dir, manifest, relationship, generation):
                self.transfers.append((manifest.generation_id, relationship.workspace_id))
                return {
                    "status": "accepted",
                    "accepted_generation": generation.generation_id,
                    "manifest_digest": manifest.manifest_digest,
                    "file_count": manifest.file_count,
                    "byte_count": manifest.byte_count,
                }

        self.service = SyncService(
            self.repository, lambda: Transport(), identity_resolver=identity,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_once_accepts_and_replays_one_generation(self):
        first = self.service.once(self.root, remote="remote", workspace_id="workspace",
                                  request_id="request-1")
        replay = self.service.once(self.root, remote="remote", workspace_id="workspace",
                                   request_id="request-1")
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["generation"]["id"], replay["generation"]["id"])
        self.assertEqual(len(self.transfers), 1)

    def test_status_does_not_create_a_relationship(self):
        result = self.service.status(self.root, remote="remote", workspace_id="workspace")
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(self.repository.list_relationships(), [])

    def test_credential_refusal_happens_before_transport(self):
        (self.root / ".env").write_text("TOKEN=fixture\n")
        subprocess.run(["git", "add", ".env"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "credential fixture"], cwd=self.root, check=True)
        result = self.service.once(self.root, remote="remote", workspace_id="workspace",
                                   request_id="request-credential")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "credential_detected")
        self.assertEqual(self.transfers, [])

    def test_transport_failure_keeps_pending_generation_observable(self):
        class Broken:
            def transfer(self, *_args, **_kwargs):
                error = RuntimeError("remote unavailable")
                error.code = "remote_unavailable"
                error.retryable = True
                raise error

        service = SyncService(
            self.repository, lambda: Broken(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        result = service.once(self.root, remote="remote", workspace_id="workspace",
                              request_id="request-failed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "remote_unavailable")
        status = service.status(self.root, remote="remote", workspace_id="workspace")
        self.assertTrue(status["ok"])
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["generation"]["state"], "pending")

    def test_lost_acknowledgment_replay_reconciles_without_second_transfer(self):
        transfers = []
        reconciliations = []

        class LostAcknowledgment:
            def transfer(self, *_args, **_kwargs):
                transfers.append("launch")
                error = RuntimeError("lost acknowledgment")
                error.code = "transport_unknown"
                error.retryable = True
                raise error

            def reconcile(self, relationship, generation):
                reconciliations.append((relationship.relationship_id, generation.generation_id))
                return {
                    "status": "accepted",
                    "accepted_generation": generation.generation_id,
                    "manifest_digest": generation.manifest_digest,
                    "file_count": generation.file_count,
                    "byte_count": generation.byte_count,
                    "request_id": generation.request_id,
                }

        service = SyncService(
            self.repository, lambda: LostAcknowledgment(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        first = service.once(self.root, remote="remote", workspace_id="workspace",
                             request_id="request-unknown")
        replay = service.once(self.root, remote="remote", workspace_id="workspace",
                              request_id="request-unknown")
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(replay["status"], "accepted")
        self.assertFalse(first["retryable"])
        self.assertEqual(replay["generation"]["id"], first["pending_generation"])
        self.assertEqual(transfers, ["launch"])
        self.assertEqual(len(reconciliations), 1)
        self.assertEqual(reconciliations[0][1], first["pending_generation"])
        relationship = self.repository.list_relationships()[0]
        self.assertEqual(len(self.repository.list_generations(relationship.relationship_id)), 1)
        accepted_replay = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-unknown",
        )
        self.assertEqual(accepted_replay["generation"]["id"], first["pending_generation"])
        self.assertEqual(transfers, ["launch"])
        self.assertEqual(len(reconciliations), 1)

    def test_bad_reconciliation_evidence_stays_unknown_on_same_generation(self):
        scenarios = {
            "missing seam": None,
            "unknown": {"status": "unknown"},
            "malformed": {"status": "accepted"},
            "generation mismatch": {
                "status": "accepted", "accepted_generation": "gen_wrong",
            },
            "manifest mismatch": {"status": "accepted", "manifest_digest": "0" * 64},
            "file count mismatch": {"status": "accepted", "file_count": -1},
            "byte count mismatch": {"status": "accepted", "byte_count": -1},
            "request mismatch": {"status": "accepted", "request_id": "request-other"},
        }
        for label, evidence in scenarios.items():
            with self.subTest(label=label):
                repository = SyncRepository(
                    Path(self.temporary.name) / f"journal-{label.replace(' ', '-')}.json"
                )
                transfers = []
                reconciliations = []

                class Uncertain:
                    def transfer(inner, *_args, **_kwargs):
                        transfers.append("launch")
                        error = RuntimeError("lost acknowledgment")
                        error.code = "transport_unknown"
                        raise error

                    if evidence is not None:
                        def reconcile(inner, relationship, generation):
                            reconciliations.append(generation.generation_id)
                            if label in {"unknown", "malformed"}:
                                return dict(evidence)
                            result = {
                                "status": "accepted",
                                "accepted_generation": generation.generation_id,
                                "manifest_digest": generation.manifest_digest,
                                "file_count": generation.file_count,
                                "byte_count": generation.byte_count,
                                "request_id": generation.request_id,
                            }
                            result.update(evidence)
                            return result

                service = SyncService(
                    repository, lambda: Uncertain(),
                    identity_resolver=lambda _root, *, remote: {
                        "identity": "project:fixture", "root": str(self.root),
                    },
                )
                first = service.once(
                    self.root, remote="remote", workspace_id="workspace",
                    request_id="request-unknown",
                )
                replay = service.once(
                    self.root, remote="remote", workspace_id="workspace",
                    request_id="request-unknown",
                )
                second_replay = service.once(
                    self.root, remote="remote", workspace_id="workspace",
                    request_id="request-unknown",
                )

                self.assertEqual(first["status"], "unknown")
                self.assertEqual(replay["status"], "unknown")
                self.assertEqual(second_replay, replay)
                self.assertEqual(replay["code"], "transport_unknown")
                self.assertFalse(replay["retryable"])
                self.assertEqual(replay["pending_generation"], first["pending_generation"])
                self.assertEqual(transfers, ["launch"])
                self.assertEqual(len(reconciliations), 0 if evidence is None else 2)
                relationship = repository.list_relationships()[0]
                generations = repository.list_generations(relationship.relationship_id)
                self.assertEqual(len(generations), 1)
                self.assertEqual(generations[0].generation_id, first["pending_generation"])
                self.assertEqual(generations[0].lifecycle, "transferring")

    def test_concurrent_reconciliation_probes_accept_and_record_metrics_once(self):
        calls = []
        start = threading.Barrier(3)

        class LostAcknowledgment:
            def transfer(self, *_args, **_kwargs):
                error = RuntimeError("lost acknowledgment")
                error.code = "transport_unknown"
                raise error

            def reconcile(self, relationship, generation):
                calls.append(generation.generation_id)
                return {
                    "status": "accepted",
                    "accepted_generation": generation.generation_id,
                    "manifest_digest": generation.manifest_digest,
                    "file_count": generation.file_count,
                    "byte_count": generation.byte_count,
                    "request_id": generation.request_id,
                }

        service = SyncService(
            self.repository, lambda: LostAcknowledgment(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        unknown = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-race",
        )
        results = []
        errors = []

        def reconcile() -> None:
            try:
                start.wait(timeout=2)
                results.append(service.reconcile(
                    self.root, remote="remote", workspace_id="workspace",
                ))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=reconcile) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["status"] == "accepted" for result in results))
        self.assertTrue(all(
            result["generation"]["id"] == unknown["pending_generation"]
            for result in results
        ))
        self.assertEqual(calls, [unknown["pending_generation"]])
        relationship = self.repository.list_relationships()[0]
        metrics = self.repository.metrics(relationship.relationship_id)
        self.assertEqual(metrics["accepted"], 1)
        self.assertEqual(metrics["unknown"], 1)

    def test_incomplete_transport_acceptance_is_unknown_not_current(self):
        class Incomplete:
            def transfer(self, *_args, **_kwargs):
                return {"status": "accepted"}

        service = SyncService(
            self.repository, lambda: Incomplete(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        result = service.once(self.root, remote="remote", workspace_id="workspace",
                              request_id="request-incomplete")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "transport_unknown")
        status = service.status(self.root, remote="remote", workspace_id="workspace")
        self.assertEqual(status["status"], "pending")

    def test_checkpoint_request_does_not_enable_automatic_transfer(self):
        self.service.start(
            self.root, remote="remote", workspace_id="workspace", mode="checkpoint",
        )
        result = self.service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-checkpoint", checkpoint=True,
        )
        self.assertTrue(result["ok"])
        status = self.service.status(self.root, remote="remote", workspace_id="workspace")
        self.assertEqual(status["relationship"]["mode"], "checkpoint")
        self.assertFalse(self.service.notify_commit(
            self.root, remote="remote", workspace_id="workspace", commit="2" * 40,
        ))

    def test_stop_preserves_pending_state_and_blocks_new_live_triggers(self):
        relationship = self.service._relationship(self.root, "remote", "workspace")
        self.repository.reserve_generation(
            relationship_id=relationship.relationship_id, request_id="pending-request",
            request_digest="a" * 64, manifest_digest="b" * 64,
            file_count=1, byte_count=1,
        )
        self.service.start(self.root, remote="remote", workspace_id="workspace", mode="live")
        stopped = self.service.stop(self.root, remote="remote", workspace_id="workspace")
        self.assertEqual(stopped["relationship"]["mode"], "off")
        self.assertEqual(stopped["generation"]["state"], "pending")
        self.assertFalse(self.service.notify_commit(
            self.root, remote="remote", workspace_id="workspace", commit="3" * 40,
        ))

    def test_live_commit_trigger_returns_without_waiting_for_transport(self):
        class SlowTransport:
            def transfer(inner, project_dir, manifest, relationship, generation):
                time.sleep(0.2)
                return {
                    "status": "accepted", "accepted_generation": generation.generation_id,
                    "manifest_digest": manifest.manifest_digest,
                    "file_count": manifest.file_count, "byte_count": manifest.byte_count,
                }

        service = SyncService(
            self.repository, lambda: SlowTransport(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        service.start(self.root, remote="remote", workspace_id="workspace", mode="live")
        started = time.monotonic()
        self.assertTrue(service.notify_commit(
            self.root, remote="remote", workspace_id="workspace", commit="4" * 40,
        ))
        self.assertLess(time.monotonic() - started, 0.1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if service.status(
                self.root, remote="remote", workspace_id="workspace",
            )["status"] == "accepted":
                break
            time.sleep(0.02)

    def test_competing_project_is_refused_before_capture_or_transport(self):
        self.service.start(self.root, remote="remote", workspace_id="workspace", mode="live")
        competing = SyncService(
            self.repository, lambda: self.fail("transport must not be created"),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:other", "root": str(self.root),
            },
        )
        with self.assertRaisesRegex(Exception, "owned"):
            competing.once(
                self.root, remote="remote", workspace_id="workspace",
                request_id="request-conflict",
            )

    def test_remote_workspace_owner_conflict_remains_bounded_and_non_retryable(self):
        accepting = {"enabled": False}

        class ConflictingTransport:
            def transfer(self, _project_dir, manifest, _relationship, generation):
                if accepting["enabled"]:
                    return {
                        "status": "accepted",
                        "accepted_generation": generation.generation_id,
                        "manifest_digest": manifest.manifest_digest,
                        "file_count": manifest.file_count,
                        "byte_count": manifest.byte_count,
                    }
                error = RuntimeError("private remote ownership detail")
                error.code = "ownership_conflict"
                error.retryable = False
                raise error

        service = SyncService(
            self.repository, lambda: ConflictingTransport(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        result = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-remote-conflict",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ownership_conflict")
        self.assertEqual(result["status"], "conflicted")
        self.assertFalse(result["retryable"])
        self.assertNotIn("private", repr(result).lower())
        relationship = self.repository.list_relationships()[0]
        self.assertEqual(
            self.repository.metrics(relationship.relationship_id)["refused"], 1,
        )
        stored = self.repository.get_relationship(relationship.relationship_id)
        self.assertEqual(stored.lifecycle, "conflicted")
        generation = self.repository.lookup_request(
            relationship.relationship_id, "request-remote-conflict",
        )
        self.assertEqual(generation.lifecycle, "refused")
        (self.root / "source.txt").write_text("changed after conflict\n")
        replay = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-remote-conflict",
        )
        status = service.status(
            self.root, remote="remote", workspace_id="workspace",
        )
        for conflict in (replay, status):
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["status"], "conflicted")
            self.assertEqual(conflict["code"], "ownership_conflict")
            self.assertEqual(conflict["request_id"], "request-remote-conflict")
        started = service.start(
            self.root, remote="remote", workspace_id="workspace", mode="live",
        )
        stopped = service.stop(
            self.root, remote="remote", workspace_id="workspace",
        )
        for conflict in (started, stopped):
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["status"], "conflicted")
            self.assertEqual(conflict["code"], "ownership_conflict")
            self.assertEqual(conflict["request_id"], "request-remote-conflict")
        self.assertEqual(
            self.repository.get_relationship(relationship.relationship_id).lifecycle,
            "conflicted",
        )
        stored = self.repository.get_relationship(relationship.relationship_id)
        self.assertEqual(stored.conflict_code, "ownership_conflict")
        self.assertEqual(stored.conflict_request_id, "request-remote-conflict")
        self.assertEqual(stored.conflict_generation_id, generation.generation_id)

        for index in range(257):
            manifest_digest = f"{index + 1:064x}"
            self.repository.reserve_generation(
                relationship_id=relationship.relationship_id,
                request_id=f"later-request-{index}",
                request_digest=self.repository.canonical_request_digest({
                    "request": index, "manifest": manifest_digest,
                }),
                manifest_digest=manifest_digest,
                file_count=1, byte_count=1,
            )
        bounded = self.repository.list_generations(relationship.relationship_id)
        self.assertEqual(len(bounded), 256)
        self.assertNotIn(generation.generation_id, {
            item.generation_id for item in bounded
        })
        for conflict in (
            service.status(self.root, remote="remote", workspace_id="workspace"),
            service.start(
                self.root, remote="remote", workspace_id="workspace", mode="live",
            ),
            service.stop(self.root, remote="remote", workspace_id="workspace"),
        ):
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["code"], "ownership_conflict")
            self.assertEqual(conflict["request_id"], "request-remote-conflict")

        accepting["enabled"] = True
        accepted = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-after-reviewed-adoption",
        )
        self.assertTrue(accepted["ok"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(
            self.repository.get_relationship(relationship.relationship_id).lifecycle,
            "stopped",
        )

    def test_lost_acknowledgment_reconciles_with_original_request(self):
        class Reconciling:
            def transfer(inner, *_args):
                error = RuntimeError("lost")
                error.code = "transport_unknown"
                raise error
            def reconcile(inner, relationship, generation):
                return {
                    "status": "accepted",
                    "accepted_generation": generation.generation_id,
                    "manifest_digest": generation.manifest_digest,
                    "file_count": generation.file_count,
                    "byte_count": generation.byte_count,
                    "request_id": generation.request_id,
                }

        service = SyncService(
            self.repository, lambda: Reconciling(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        unknown = service.once(
            self.root, remote="remote", workspace_id="workspace",
            request_id="request-lost",
        )
        recovered = service.reconcile(
            self.root, remote="remote", workspace_id="workspace",
        )
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(recovered["status"], "accepted")
        self.assertEqual(recovered["generation"]["id"], unknown["pending_generation"])

    def test_divergence_resolution_requires_confirmation_and_stops_mode(self):
        from sandbox.sync.models import DivergenceRecord

        relationship = self.service._relationship(self.root, "remote", "workspace")
        self.repository.put_divergence(DivergenceRecord(
            relationship.relationship_id, 1, "gen_fixture",
            "2026-08-26T00:00:03Z", "explicit_resolution_required",
        ))
        with self.assertRaisesRegex(Exception, "confirmation"):
            self.service.resolve(
                self.root, remote="remote", workspace_id="workspace",
                resolution="keep-local", confirm=False,
            )
        resolved = self.service.resolve(
            self.root, remote="remote", workspace_id="workspace",
            resolution="keep-local", confirm=True,
        )
        self.assertEqual(resolved["relationship"]["mode"], "off")
        self.assertIsNone(self.repository.get_divergence(relationship.relationship_id))


if __name__ == "__main__":
    unittest.main()
