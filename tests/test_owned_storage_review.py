"""Unit tests for capability evaluation probe, tier state transitions, and drift detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptanceState,
    AuthorityCapability,
    CapabilityAcceptance,
    CapabilityPromotion,
    PromotionPhase,
    SupportTier,
)
from sandbox.owned_storage_lifecycle.repository import StorageAuthorityLifecycleRepository
from sandbox.owned_storage_lifecycle.service import (
    AuthorityLifecycleService,
    LifecycleServiceError,
)


class TestOwnedStorageReview(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.lifecycle_path = self.root / "lifecycle.json"
        self.lifecycle_repo = StorageAuthorityLifecycleRepository(self.lifecycle_path)
        self.service = AuthorityLifecycleService(self.lifecycle_repo)

        self.remote_id = "rem_review"
        self.project_id = "proj_review"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_capability_report_default_unproven(self):
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )
        self.assertEqual(report["capability"], "owned-storage-authority-v1")
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["acceptance_state"], "pending_ordinary")
        self.assertIn("checks", report)
        self.assertFalse(report["resolver_authority"]["included"])
        self.assertFalse(report["resolver_authority"]["qualified"])

    def test_tier_transition_to_proven_upon_acceptance(self):
        # 1. Register unproven capability
        self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )

        # 2. Complete acceptance receipt
        acceptance = CapabilityAcceptance(
            acceptance_id="acc_1",
            promotion_id="prom_1",
            sync_operation_id="op_sync_1",
            ci_operation_id="op_ci_1",
            cleanup_operation_id="op_clean_1",
            policy_id="pol_1",
            evidence_id="ev_1",
            authority_binding_id="bind_1",
            ordinary_evidence_digest="sha256:ord",
            outcome=AcceptanceOutcome.COMPLETE,
            reason_code=None,
            request_id="req_acc_1",
            request_digest="sha256:req_acc",
            lifecycle_generation=1,
            completed_at="2026-09-04T00:00:00Z",
        )
        self.service.record_acceptance(self.remote_id, acceptance)

        # 3. Verify report is now proven and adoptable
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )
        self.assertEqual(report["support_tier"], "proven")
        self.assertTrue(report["adoptable"])
        self.assertEqual(report["acceptance_state"], "complete")

    def test_drift_detection_on_revision_mismatch(self):
        # Record proven acceptance with rev_1
        self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_1",
        )
        acceptance = CapabilityAcceptance(
            acceptance_id="acc_1",
            promotion_id="prom_1",
            sync_operation_id="op_sync_1",
            ci_operation_id="op_ci_1",
            cleanup_operation_id="op_clean_1",
            policy_id="pol_1",
            evidence_id="ev_1",
            authority_binding_id="bind_1",
            ordinary_evidence_digest="sha256:ord",
            outcome=AcceptanceOutcome.COMPLETE,
            reason_code=None,
            request_id="req_acc_1",
            request_digest="sha256:req_acc",
            lifecycle_generation=1,
            completed_at="2026-09-04T00:00:00Z",
        )
        self.service.record_acceptance(self.remote_id, acceptance)

        # Re-evaluate with service_revision mismatch
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            service_revision="sha256:srv_2_changed",
        )
        self.assertEqual(report["support_tier"], "drifted")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_revision_mismatch")

    def test_unsupported_platform_mode(self):
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode="darwin-arm64-unsupported",
            service_revision="sha256:srv_1",
        )
        self.assertEqual(report["support_tier"], "unsupported")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_unsupported")


if __name__ == "__main__":
    unittest.main()
