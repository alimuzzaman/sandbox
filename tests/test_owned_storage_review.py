"""Unit tests for capability evaluation probe, tier state transitions, and drift detection."""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptanceState,
    AuthorityCapability,
    CapabilityAcceptance,
    CapabilityPromotion,
    CapabilityRevocation,
    PromotionPhase,
    SupportTier,
)
from sandbox.owned_storage_lifecycle.repository import StorageAuthorityLifecycleRepository
from sandbox.owned_storage_lifecycle.service import (
    AuthorityLifecycleService,
    LifecycleServiceError,
    UNOBSERVED_REVISION,
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

    SERVICE = "sha256:srv_1"
    SOURCE = "sha256:src_1"
    PLATFORM = "ubuntu-24.04-systemd-255-private-root-v1"

    def _future(self, days=1):
        return (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=days)).isoformat()

    def _evaluate(self, remote=None, service=SERVICE, source=SOURCE):
        return self.service.evaluate_capability(
            remote_identity=remote or self.remote_id, platform_mode=self.PLATFORM,
            service_revision=service, source_revision=source)

    def _record_chain(self, *, remote=None, service=SERVICE, source=SOURCE, expires_at=None,
                      phase=PromotionPhase.SUPPORTED, adoptable=True, binding="bind_1",
                      suffix="1", outcome=AcceptanceOutcome.COMPLETE):
        promotion = CapabilityPromotion(
            promotion_id=f"prom_{suffix}", review_decision_id="dec", evidence_candidate_id="cand",
            capability_id="owned-storage-authority-v1", remote_identity=remote or self.remote_id,
            project_identity=self.project_id, fixture_identity="fix", source_revision=source,
            service_revision=service, contract_revision="contract", evidence_digest="sha256:ev",
            authority_binding_id=binding, binding_generation=1, phase=phase,
            support_tier=SupportTier.PROVEN, adoptable=adoptable, request_id=f"req_p_{suffix}",
            request_digest=f"sha256:p_{suffix}", promoted_at=self._future(0),
            expires_at=expires_at or self._future())
        self.lifecycle_repo.record_promotion(promotion, self.lifecycle_repo.get_generation())
        self.service.record_acceptance(remote or self.remote_id, CapabilityAcceptance(
            acceptance_id=f"acc_{suffix}", promotion_id=f"prom_{suffix}", sync_operation_id="op_s",
            ci_operation_id="op_c", cleanup_operation_id="op_d", policy_id="pol", evidence_id="ev",
            authority_binding_id="bind_1", ordinary_evidence_digest="sha256:ord", outcome=outcome,
            reason_code=None, request_id=f"req_a_{suffix}", request_digest=f"sha256:a_{suffix}",
            lifecycle_generation=1, completed_at=self._future(0)))

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
        self._evaluate()
        self._record_chain()
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "proven")
        self.assertTrue(report["adoptable"])
        self.assertEqual(report["acceptance_state"], "complete")

    def test_drift_detection_on_revision_mismatch(self):
        self._record_chain()
        report = self._evaluate(service="sha256:srv_2_changed")
        self.assertEqual(report["support_tier"], "drifted")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_revision_mismatch")

        report = self._evaluate(service="sha256:srv_2_changed")
        self.assertEqual(report["support_tier"], "drifted")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_revision_mismatch")

    def test_acceptance_for_one_remote_never_authorizes_another(self):
        self._record_chain(remote="rem_other")
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "implemented_unproven")
        self.assertIsNone(report["evidence_id"])

        report_other = self._evaluate(remote="rem_other")
        self.assertEqual(report_other["support_tier"], "proven")
        self.assertTrue(report_other["adoptable"])

    def test_acceptance_without_a_promotion_proves_nothing(self):
        acceptance = CapabilityAcceptance(
            acceptance_id="acc_no_prom",
            promotion_id="prom_nonexistent",
            sync_operation_id="op_s",
            ci_operation_id="op_c",
            cleanup_operation_id="op_d",
            policy_id="pol",
            evidence_id="ev",
            authority_binding_id="bind_1",
            ordinary_evidence_digest="sha256:ord",
            outcome=AcceptanceOutcome.COMPLETE,
            reason_code=None,
            request_id="req_no_prom",
            request_digest="sha256:no_prom",
            lifecycle_generation=1,
            completed_at=self._future(0),
        )
        self.service.record_acceptance(self.remote_id, acceptance)
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(report["adoptable"])

    def test_failed_acceptance_proves_nothing(self):
        self._record_chain(outcome=AcceptanceOutcome.FAILED)
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(report["adoptable"])

    def test_expired_promotion_refuses_adoption(self):
        self._record_chain(expires_at=self._future(-1))
        report = self._evaluate()
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_expired")

    def test_unparseable_or_naive_expiry_refuses_adoption(self):
        cases = [
            ("not-a-date", "rem_unparseable", "unparseable"),
            ("2099-01-01T00:00:00", "rem_naive", "naive"),
        ]
        for expires_at, remote, suffix in cases:
            with self.subTest(expires_at=expires_at):
                self._record_chain(remote=remote, expires_at=expires_at, suffix=suffix)
                report = self._evaluate(remote=remote)
                self.assertFalse(report["adoptable"])
                self.assertEqual(report["reason_code"], "authority_expired")

    def test_revoked_binding_refuses_adoption(self):
        self._record_chain()
        rev = CapabilityRevocation(
            revocation_id="rev_1",
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            promotion_id="prom_1",
            authority_binding_id="bind_1",
            reviewer_identity_digest="sha256:r",
            request_id="req_r",
            request_digest="sha256:r1",
            lifecycle_generation=1,
            reason_code="synthetic",
            revoked_at=self._future(0),
        )
        self.lifecycle_repo.record_revocation(rev, self.lifecycle_repo.get_generation())
        report = self._evaluate()
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_revoked")

    def test_revocation_for_another_remote_does_not_apply(self):
        self._record_chain()
        rev = CapabilityRevocation(
            revocation_id="rev_other",
            remote_identity="rem_other",
            project_identity=self.project_id,
            promotion_id="prom_1",
            authority_binding_id="bind_1",
            reviewer_identity_digest="sha256:r",
            request_id="req_r_other",
            request_digest="sha256:r_other",
            lifecycle_generation=1,
            reason_code="synthetic",
            revoked_at=self._future(0),
        )
        self.lifecycle_repo.record_revocation(rev, self.lifecycle_repo.get_generation())
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "proven")
        self.assertTrue(report["adoptable"])

    def test_binding_mismatch_or_unsupported_promotion_refuses_adoption(self):
        cases = [
            ("bind_mismatch", "rem_bind", dict(binding="bind_other", suffix="bm")),
            ("phase_unsupported", "rem_phase", dict(phase=PromotionPhase.REVOKED, suffix="ph")),
            ("not_adoptable", "rem_adoptable", dict(adoptable=False, suffix="ad")),
        ]
        for name, remote, kwargs in cases:
            with self.subTest(case=name):
                self._record_chain(remote=remote, **kwargs)
                report = self._evaluate(remote=remote)
                self.assertFalse(report["adoptable"])
                self.assertEqual(report["reason_code"], "authority_binding_mismatch")

    def test_source_revision_mismatch_is_drift(self):
        self._record_chain()
        report = self._evaluate(source="sha256:src_other")
        self.assertEqual(report["support_tier"], "drifted")
        self.assertEqual(report["reason_code"], "authority_revision_mismatch")

    def test_missing_source_revision_proves_nothing(self):
        self._record_chain()
        report = self._evaluate(source=None)
        self.assertFalse(report["adoptable"])
        self.assertNotEqual(report["support_tier"], "proven")

    def test_unobserved_revision_never_proves_and_reports_no_passing_checks(self):
        self._record_chain(service=UNOBSERVED_REVISION)
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode=self.PLATFORM,
            source_revision=self.SOURCE,
        )
        self.assertFalse(report["adoptable"])
        for check_val in report["checks"].values():
            self.assertEqual(check_val, "unknown")

    def test_revision_returning_to_the_bound_value_proves_again(self):
        self._record_chain()
        report_drifted = self._evaluate(service="sha256:other")
        self.assertEqual(report_drifted["support_tier"], "drifted")
        report_proven = self._evaluate()
        self.assertEqual(report_proven["support_tier"], "proven")
        self.assertTrue(report_proven["adoptable"])

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
