"""Integration tests for application-level owned storage service and policy routing."""

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationError,
    OwnedStorageApplicationService,
)
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityPolicy,
    PolicyMode,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService


class TestOwnedStorageApplication(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.authority_service = OwnedStorageService(self.storage_root, self.repo)

        self.remote_id = "rem_local"
        self.project_id = "proj_test"
        self.app_service = OwnedStorageApplicationService(
            authority_service=self.authority_service,
            repository=self.repo,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_legacy_policy_refuses_owned_storage_publish(self):
        # By default policy is legacy or absent
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.publish(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                request_id="req_app_1",
                relationship_id="rel_1",
                workspace_id="ws_1",
                generation_id="gen_1",
                manifest_digest="sha256:manifest",
                archive_manifest_digest="sha256:archive",
                file_count=1,
                byte_count=10,
                stream=io.BytesIO(b"data"),
            )
        self.assertIn("policy_not_future", str(ctx.exception).lower())

    def test_future_policy_with_active_binding_succeeds(self):
        # Enable future policy and active binding
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

        empty_digest = f"sha256:{hashlib.sha256(b'').hexdigest()}"
        res = self.app_service.publish(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id="req_app_2",
            relationship_id="rel_1",
            workspace_id="ws_1",
            generation_id="gen_1",
            manifest_digest="sha256:manifest",
            archive_manifest_digest=empty_digest,
            file_count=0,
            byte_count=0,
            stream=io.BytesIO(b""),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "accepted")

    def test_cross_project_refusal(self):
        # Caller attempts to publish with mismatched project identity
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.publish(
                remote_identity=self.remote_id,
                project_identity="",  # Invalid project identity
                request_id="req_app_cross",
                relationship_id="rel_1",
                workspace_id="ws_1",
                generation_id="gen_1",
                manifest_digest="sha256:manifest",
                archive_manifest_digest="sha256:archive",
                file_count=0,
                byte_count=0,
                stream=io.BytesIO(b""),
            )
        self.assertIn("project", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
