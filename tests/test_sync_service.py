import subprocess
import tempfile
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

    def test_unknown_or_failed_acknowledgment_never_launches_twice(self):
        calls = []

        class Unknown:
            def transfer(self, *_args, **_kwargs):
                calls.append("launch")
                error = RuntimeError("lost acknowledgment")
                error.code = "transport_unknown"
                error.retryable = True
                raise error

        service = SyncService(
            self.repository, lambda: Unknown(),
            identity_resolver=lambda _root, *, remote: {
                "identity": "project:fixture", "root": str(self.root),
            },
        )
        first = service.once(self.root, remote="remote", workspace_id="workspace",
                             request_id="request-unknown")
        replay = service.once(self.root, remote="remote", workspace_id="workspace",
                              request_id="request-unknown")
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(replay["status"], "unknown")
        self.assertFalse(first["retryable"])
        self.assertEqual(calls, ["launch"])

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
