"""Unit and contract tests for immutable generation staging and publication under owned storage."""

import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityPolicy,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    PolicyMode,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService, OwnedStorageServiceError


class TestSyncOwnedStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.service = OwnedStorageService(self.storage_root, self.repo)

        # Set up active policy and binding
        self.remote_id = "rem_fixture"
        self.project_id = "proj_test"
        self.repo.save_policy(
            AuthorityPolicy(
                policy_id="pol_1",
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                mode=PolicyMode.FUTURE,
                effective_generation=1,
                changed_by="caller_auth",
                request_id="req_pol_1",
                request_digest="sha256:pol",
                admission_basis={"binding_id": "bind_1"},
                changed_at="2026-09-04T00:00:00Z",
            )
        )
        self.repo.save_adoption_binding(
            AuthorityAdoptionBinding(
                authority_binding_id="bind_1",
                binding_generation=1,
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
                fixture_identity="fix_1",
                review_decision_id="dec_1",
                promotion_id="prom_1",
                evidence_candidate_id="cand_1",
                evidence_digest="sha256:ev",
                source_revision="sha256:src",
                service_revision="sha256:srv",
                controller_revision="sha256:ctrl",
                contract_revision="sha256:ctr",
                lifecycle_request_id="req_1",
                request_digest="sha256:req",
                lifecycle_generation=1,
                binding_digest="sha256:bind",
                expires_at="2026-09-04T12:00:00Z",
                phase=AdoptionBindingPhase.ACTIVE,
            )
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_sample_archive(self) -> tuple[bytes, str, int, int]:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = b"print('hello world')\n"
            ti = tarfile.TarInfo("main.py")
            ti.size = len(data)
            ti.mtime = 0
            tar.addfile(ti, io.BytesIO(data))
        raw = buf.getvalue()
        manifest_digest = f"sha256:{hashlib.sha256(b'main.py\0' + data).hexdigest()}"
        return raw, manifest_digest, 1, len(data)

    def test_publish_immutable_generation_success(self):
        payload, manifest_digest, file_count, byte_count = self._create_sample_archive()
        archive_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"

        receipt = self.service.publish_generation(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id="req_pub_1",
            request_digest="sha256:req_digest_1",
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            relationship_id="rel_1",
            workspace_id="ws_1",
            generation_id="gen_1",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=file_count,
            byte_count=byte_count,
            stream=io.BytesIO(payload),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )

        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["object"]["kind"], "sync_generation")
        self.assertEqual(receipt["object"]["lifecycle"], "accepted")

        # Current selection is updated
        curr = self.repo.get_current_selection("rel_1")
        self.assertIsNotNone(curr)
        self.assertEqual(curr.generation_id, "gen_1")

        # Replay returns identical receipt
        replayed = self.service.publish_generation(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id="req_pub_1",
            request_digest="sha256:req_digest_1",
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            relationship_id="rel_1",
            workspace_id="ws_1",
            generation_id="gen_1",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=file_count,
            byte_count=byte_count,
            stream=io.BytesIO(payload),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )
        self.assertTrue(replayed["replay"])
        self.assertEqual(replayed["status"], "accepted")

    def test_publish_mismatched_byte_count_fails_closed(self):
        payload, manifest_digest, file_count, byte_count = self._create_sample_archive()
        archive_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"

        with self.assertRaises(OwnedStorageServiceError) as ctx:
            self.service.publish_generation(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                request_id="req_pub_bad_count",
                request_digest="sha256:req_digest_bad",
                authorization_id="auth_1",
                controller_epoch="epoch_1",
                sequence=1,
                caller_identity_digest="sha256:caller",
                relationship_id="rel_1",
                workspace_id="ws_1",
                generation_id="gen_bad",
                manifest_digest=manifest_digest,
                archive_manifest_digest=archive_digest,
                file_count=file_count,
                byte_count=byte_count + 999,  # Mismatch!
                stream=io.BytesIO(payload),
                promotion_id="prom_1",
                authority_binding_id="bind_1",
            )
        self.assertIn("byte_count", str(ctx.exception).lower())

        # Verify current selection was NOT updated
        curr = self.repo.get_current_selection("rel_1")
        self.assertIsNone(curr)


if __name__ == "__main__":
    unittest.main()
