"""End-to-end local synthetic acceptance test suite for Feature 052: Owned Storage Authority."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationError,
    OwnedStorageApplicationService,
)
from sandbox.owned_storage.adapters.linux import verify_interior_confinement
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityOwnedObject,
    AuthorityPolicy,
    ObjectKind,
    ObjectLifecycle,
    PolicyMode,
)
from sandbox.owned_storage.redaction import redact_storage_projection
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService
from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    CapabilityAcceptance,
    SupportTier,
)
from sandbox.owned_storage_lifecycle.repository import StorageAuthorityLifecycleRepository
from sandbox.owned_storage_lifecycle.service import AuthorityLifecycleService


class TestOwnedStorageAuthorityAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        self.storage_root = self.root / "authority"
        self.storage_root.mkdir(parents=True)
        self.db_path = self.storage_root / "authority.db"
        self.storage_repo = StorageAuthorityRepository(self.db_path)

        self.lifecycle_path = self.root / "lifecycle.json"
        self.lifecycle_repo = StorageAuthorityLifecycleRepository(self.lifecycle_path)
        self.lifecycle_service = AuthorityLifecycleService(self.lifecycle_repo)

        self.storage_service = OwnedStorageService(self.storage_root, self.storage_repo)
        self.app_service = OwnedStorageApplicationService(
            authority_service=self.storage_service,
            repository=self.storage_repo,
        )

    def test_full_authority_lifecycle_journey(self):
        """End-to-end journey: capability -> proven -> policy -> publish -> replay -> confinement -> cleanup -> preview -> reclaim -> redact."""
        remote = "remote-alpha"
        project = "proj-x"

        # 1. Capability evaluation begins at implemented_unproven
        cap = self.lifecycle_service.evaluate_capability(
            remote_identity=remote,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )
        self.assertEqual(cap["support_tier"], SupportTier.IMPLEMENTED_UNPROVEN.value)
        self.assertFalse(cap["adoptable"])

        # Record acceptance receipt to promote to proven
        acceptance = CapabilityAcceptance(
            acceptance_id="acc-001",
            promotion_id="prom-001",
            sync_operation_id="op-sync-001",
            ci_operation_id="op-ci-001",
            cleanup_operation_id="op-clean-001",
            policy_id="pol-001",
            evidence_id="ev-001",
            authority_binding_id="bind-001",
            ordinary_evidence_digest="sha256:ord",
            outcome=AcceptanceOutcome.COMPLETE,
            reason_code=None,
            request_id="req-acc-001",
            request_digest="sha256:req_acc",
            lifecycle_generation=1,
            completed_at="2026-09-04T00:00:00Z",
        )
        self.lifecycle_service.record_acceptance(remote, acceptance)

        cap_proven = self.lifecycle_service.evaluate_capability(
            remote_identity=remote,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )
        self.assertEqual(cap_proven["support_tier"], SupportTier.PROVEN.value)
        self.assertTrue(cap_proven["adoptable"])

        # Set future policy and adoption binding now that platform is proven
        self.storage_repo.save_policy(
            AuthorityPolicy(
                policy_id="pol-001",
                remote_identity=remote,
                project_identity=project,
                mode=PolicyMode.FUTURE,
                effective_generation=1,
                changed_by="caller_auth",
                request_id="req-pol-001",
                request_digest="sha256:pol",
                admission_basis={"binding_id": "bind-001"},
                changed_at="2026-09-04T00:00:00Z",
            )
        )
        self.storage_repo.save_adoption_binding(
            AuthorityAdoptionBinding(
                authority_binding_id="bind-001",
                binding_generation=1,
                remote_identity=remote,
                project_identity=project,
                platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
                fixture_identity="fix-001",
                review_decision_id="dec-001",
                promotion_id="prom-001",
                evidence_candidate_id="cand-001",
                evidence_digest="sha256:ev",
                source_revision="sha256:src",
                service_revision="sha256:srv",
                controller_revision="sha256:ctrl",
                contract_revision="sha256:ctr",
                lifecycle_request_id="req-life-001",
                request_digest="sha256:req",
                lifecycle_generation=1,
                binding_digest="sha256:bind",
                expires_at="2026-09-04T12:00:00Z",
                phase=AdoptionBindingPhase.ACTIVE,
            )
        )

        # 2. Publish immutable sync generation
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = b"acceptance test sync archive payload content"
            ti = tarfile.TarInfo("file.txt")
            ti.size = len(data)
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))
        payload = buf.getvalue()
        payload_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"

        pub_result = self.app_service.publish(
            remote_identity=remote,
            project_identity=project,
            request_id="req-publish-001",
            relationship_id="rel-001",
            workspace_id="ws-sync-001",
            generation_id="gen-001",
            manifest_digest="sha256:" + "a" * 64,
            archive_manifest_digest=payload_digest,
            file_count=1,
            byte_count=len(data),
            stream=io.BytesIO(payload),
        )
        self.assertTrue(pub_result["ok"])
        self.assertEqual(pub_result["status"], "accepted")
        self.assertFalse(pub_result.get("replayed", False))

        # 3. Idempotent replay with identical request returns accepted result with replayed=True
        replay_result = self.app_service.publish(
            remote_identity=remote,
            project_identity=project,
            request_id="req-publish-001",
            relationship_id="rel-001",
            workspace_id="ws-sync-001",
            generation_id="gen-001",
            manifest_digest="sha256:" + "a" * 64,
            archive_manifest_digest=payload_digest,
            file_count=1,
            byte_count=len(data),
            stream=io.BytesIO(payload),
        )
        self.assertTrue(replay_result["ok"])
        self.assertTrue(replay_result.get("replayed", True))

        # 4. Conflicting request reuse with same request_id but different generation_id is refused
        with self.assertRaises(OwnedStorageApplicationError) as cm:
            self.app_service.publish(
                remote_identity=remote,
                project_identity=project,
                request_id="req-publish-001",
                relationship_id="rel-001",
                workspace_id="ws-sync-001",
                generation_id="gen-002-DIFFERENT",
                manifest_digest="sha256:" + "a" * 64,
                archive_manifest_digest=payload_digest,
                file_count=1,
                byte_count=len(data),
                stream=io.BytesIO(payload),
            )
        self.assertEqual(cm.exception.code, "request_id_conflict")

        # 5. CI materialization preparation and interior path confinement
        source_dir = self.root / "sources" / "src-001"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "seed.txt").write_text("ci seed file content")

        bundle = self.storage_service.adapter.prepare_ci_materialization(
            project_identity=project,
            workspace_id="ws-ci-001",
            object_id="mat-ci-001",
            source_path=source_dir,
        )
        try:
            self.assertEqual(bundle["object_id"], "mat-ci-001")
            self.assertIsInstance(os.fstat(bundle["root_fd"]).st_ino, int)
            self.assertIsInstance(os.fstat(bundle["work_fd"]).st_ino, int)

            obj_root = bundle["object_root"]
            work_path = bundle["work_path"]
            interior_file = work_path / "build" / "output.js"
            exterior_escape = obj_root.parent / "escape.txt"

            self.assertTrue(verify_interior_confinement(obj_root, interior_file))
            self.assertFalse(verify_interior_confinement(obj_root, exterior_escape))
        finally:
            for fd_key in ("root_fd", "work_fd", "source_fd"):
                fd = bundle.get(fd_key)
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

        # 6. CI workspace release & terminal job cleanup
        art_dir = self.storage_root / "objects" / project / "unscoped" / "art-acceptance-001"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "data.bin").write_bytes(b"x" * 1024)
        fs_stat = self.storage_service.adapter.stat_identity(art_dir)

        art_record = AuthorityOwnedObject(
            object_id="art-acceptance-001",
            object_kind=ObjectKind.RETAINED_ARTIFACT,
            remote_identity=remote,
            project_identity=project,
            relationship_id=None,
            workspace_id="ws-sync-001",
            job_id="job-ci-001",
            parent_object_id=None,
            created_by_operation_id="op-art-001",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id="pol-001",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind-001",
            retention_policy_digest="sha256:art",
            content_evidence={"artifact_name": "build.tar"},
            filesystem_identity=fs_stat,
            known_bytes=1024,
            created_at="2026-09-01T00:00:00Z",
            accepted_at="2026-09-01T00:01:00Z",
        )
        self.storage_repo.save_object(art_record)

        # 7. Generate storage preview and verify bounded pagination
        preview = self.app_service.generate_preview(
            remote_identity=remote,
            project_identity=project,
            kind="retained_artifact",
            limit=50,
        )
        self.assertTrue(preview["ok"])
        self.assertIn("preview_id", preview)
        self.assertEqual(len(preview["candidates"]), 1)
        self.assertEqual(preview["candidates"][0]["object_id"], "art-acceptance-001")
        self.assertEqual(preview["candidates"][0]["decision"], "eligible")

        # Reclaim validation refusals:
        # Without confirm
        with self.assertRaises(OwnedStorageApplicationError) as cm_noconfirm:
            self.app_service.reclaim(
                remote_identity=remote,
                project_identity=project,
                preview_id=preview["preview_id"],
                object_id="art-acceptance-001",
                request_id="req-reclaim-unconf",
                confirm=False,
            )
        self.assertEqual(cm_noconfirm.exception.code, "request_invalid")

        # Unpreviewed object
        with self.assertRaises(OwnedStorageApplicationError) as cm_notprev:
            self.app_service.reclaim(
                remote_identity=remote,
                project_identity=project,
                preview_id=preview["preview_id"],
                object_id="obj-not-in-preview",
                request_id="req-reclaim-noprev",
                confirm=True,
            )
        self.assertEqual(cm_notprev.exception.code, "object_not_previewed")

        # 8. Reclaim candidate using preview ID and object ID
        reclaim = self.app_service.reclaim(
            remote_identity=remote,
            project_identity=project,
            preview_id=preview["preview_id"],
            object_id="art-acceptance-001",
            request_id="req-reclaim-001",
            confirm=True,
        )
        self.assertTrue(reclaim["ok"])
        self.assertEqual(reclaim["status"], "completed")

        # 9. Verify secret-free, path-free evidence auditing across all public projections
        projections = [
            self.app_service.get_status(remote_identity=remote, project_identity=project),
            preview,
            reclaim,
            cap_proven,
        ]
        for proj in projections:
            scrubbed = redact_storage_projection(proj)
            scrubbed_json = json.dumps(scrubbed)
            # Must not contain host filesystem root prefixes
            self.assertNotIn("/Users/", scrubbed_json)
            self.assertNotIn("/home/", scrubbed_json)
            self.assertNotIn("/tmp/", scrubbed_json)
            self.assertNotIn("/var/lib/", scrubbed_json)
            # Must not contain raw token or credential patterns
            self.assertNotIn("bearer ", scrubbed_json.lower())
            self.assertNotIn("password=", scrubbed_json.lower())


if __name__ == "__main__":
    unittest.main()
