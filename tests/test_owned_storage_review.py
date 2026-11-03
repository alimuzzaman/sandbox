"""Unit tests for capability evaluation probe, tier state transitions, and drift detection."""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from sandbox.owned_storage_lifecycle.repository import (
from sandbox.owned_storage_lifecycle.repository import LifecycleError
    StorageAuthorityLifecycleRepository,
)
from sandbox.owned_storage_lifecycle.service import (
    AuthorityLifecycleService,
    LifecycleServiceError,
    _authority_check,
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
                      acceptance_binding=None, suffix="1", outcome=AcceptanceOutcome.COMPLETE):
        act_binding = acceptance_binding if acceptance_binding is not None else binding
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
            authority_binding_id=act_binding, ordinary_evidence_digest="sha256:ord", outcome=outcome,
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

    def test_changed_revision_without_authority_is_only_unproven(self):
        report_a = self._evaluate(service="sha256:a")
        self.assertEqual(report_a["support_tier"], "implemented_unproven")
        self.assertEqual(report_a["reason_code"], "implemented_unproven")

        report_b = self._evaluate(service="sha256:b")
        self.assertEqual(report_b["support_tier"], "implemented_unproven")
        self.assertEqual(report_b["reason_code"], "implemented_unproven")

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

    def test_valid_promotion_proves_when_another_promotion_is_revoked(self):
        self._record_chain(suffix="1", binding="bind_1")
        self._record_chain(suffix="2", binding="bind_2")
        rev = CapabilityRevocation(
            revocation_id="rev_1",
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            promotion_id="prom_1",
            authority_binding_id="bind_1",
            reviewer_identity_digest="sha256:r",
            request_id="req_r_1",
            request_digest="sha256:r1",
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
            ("bind_mismatch", "rem_bind", dict(acceptance_binding="bind_other", suffix="bm")),
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
        self._record_chain()
        report = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode=self.PLATFORM,
            source_revision=self.SOURCE,
        )
        self.assertFalse(report["adoptable"])
        self.assertIsNone(report["service_revision"])
        for check_val in report["checks"].values():
            self.assertEqual(check_val, "unknown")

    def test_revision_returning_to_the_bound_value_proves_again(self):
        self._record_chain()
        report_drifted = self._evaluate(service="sha256:other")
        self.assertEqual(report_drifted["support_tier"], "drifted")
        report_unobserved = self.service.evaluate_capability(
            remote_identity=self.remote_id,
            platform_mode=self.PLATFORM,
            source_revision=self.SOURCE,
        )
        self.assertFalse(report_unobserved["adoptable"])
        report_proven = self._evaluate()
        self.assertEqual(report_proven["support_tier"], "proven")
        self.assertTrue(report_proven["adoptable"])

    def test_malformed_chain_values_never_prove(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future = (now + datetime.timedelta(days=1)).isoformat()

        def make_state(prom=None, acc=None):
            p = {
                "promotion_id": "prom_1",
                "remote_identity": "rem_test",
                "authority_binding_id": "bind_1",
                "phase": "supported",
                "adoptable": True,
                "expires_at": future,
                "service_revision": self.SERVICE,
                "source_revision": self.SOURCE,
            }
            if prom is not None:
                p.update(prom)
            a = {
                "acceptance_id": "acc_1",
                "promotion_id": "prom_1",
                "authority_binding_id": "bind_1",
                "outcome": "complete",
            }
            if acc is not None:
                a.update(acc)
            return {"promotions": {"prom_1": p}, "acceptances": {"acc_1": a}}

        cases = [
            (
                "binding_none_both",
                make_state({"authority_binding_id": None}, {"authority_binding_id": None}),
                self.SERVICE,
                self.SOURCE,
            ),
            (
                "binding_empty_both",
                make_state({"authority_binding_id": ""}, {"authority_binding_id": ""}),
                self.SERVICE,
                self.SOURCE,
            ),
            (
                "service_revision_empty_both",
                make_state({"service_revision": ""}),
                "",
                self.SOURCE,
            ),
            (
                "source_revision_none_both",
                make_state({"source_revision": None}),
                self.SERVICE,
                None,
            ),
            (
                "adoptable_is_1",
                make_state({"adoptable": 1}),
                self.SERVICE,
                self.SOURCE,
            ),
            (
                "phase_supported_titlecase",
                make_state({"phase": "Supported"}),
                self.SERVICE,
                self.SOURCE,
            ),
            (
                "non_dict_acceptance",
                {"promotions": {"prom_1": {
                    "promotion_id": "prom_1", "remote_identity": "rem_test",
                    "authority_binding_id": "bind_1", "phase": "supported", "adoptable": True,
                    "expires_at": future, "service_revision": self.SERVICE, "source_revision": self.SOURCE
                }}, "acceptances": {"acc_1": "not-a-dict"}},
                self.SERVICE,
                self.SOURCE,
            ),
            (
                "non_dict_promotion",
                {"promotions": {"prom_1": "not-a-dict"}, "acceptances": {"acc_1": {
                    "acceptance_id": "acc_1", "promotion_id": "prom_1",
                    "authority_binding_id": "bind_1", "outcome": "complete"
                }}},
                self.SERVICE,
                self.SOURCE,
            ),
        ]

        for name, state, srv, src in cases:
            with self.subTest(case=name):
                acc, reason = _authority_check(state, "rem_test", srv, src, now)
                self.assertIsNone(acc)
                self.assertNotEqual(reason, "proven")

    def test_malformed_revocation_refuses(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        state = {
            "promotions": {
                "prom_1": {
                    "promotion_id": "prom_1",
                    "remote_identity": self.remote_id,
                    "authority_binding_id": "bind_1",
                    "phase": "supported",
                    "adoptable": True,
                    "expires_at": (now + datetime.timedelta(days=1)).isoformat(),
                    "service_revision": self.SERVICE,
                    "source_revision": self.SOURCE,
                }
            },
            "acceptances": {
                "acc_1": {
                    "acceptance_id": "acc_1",
                    "promotion_id": "prom_1",
                    "authority_binding_id": "bind_1",
                    "outcome": "complete",
                }
            },
            "revocations": {"x": "garbage"},
        }
        acc, reason = _authority_check(state, self.remote_id, self.SERVICE, self.SOURCE, now)
        self.assertIsNone(acc)
        self.assertEqual(reason, "authority_revoked")

    def test_evaluation_is_read_only(self):
        self._record_chain()
        before = self.lifecycle_path.read_bytes()
        self._evaluate()
        self._evaluate(service="sha256:other")
        self.service.evaluate_capability(remote_identity=self.remote_id, platform_mode=self.PLATFORM)
        self.assertEqual(self.lifecycle_path.read_bytes(), before)

    def _state(self, **overrides):
        promotion = {"promotion_id": "p", "remote_identity": self.remote_id,
                     "authority_binding_id": "b", "phase": PromotionPhase.SUPPORTED.value,
                     "adoptable": True, "expires_at": self._future(),
                     "service_revision": self.SERVICE, "source_revision": self.SOURCE}
        state = {
            "promotions": {"p": promotion},
            "acceptances": {"a": {"outcome": AcceptanceOutcome.COMPLETE.value,
                                  "promotion_id": "p", "authority_binding_id": "b"}},
            "revocations": {},
        }
        state.update(overrides)
        return state

    def _check(self, state):
        return _authority_check(state, self.remote_id, self.SERVICE, self.SOURCE,
                                datetime.datetime.now(datetime.timezone.utc))

    def test_state_helper_proves_when_every_link_holds(self):
        self.assertEqual(self._check(self._state())[1], "proven")

    def test_revocation_cannot_be_dodged_through_the_promotion_key(self):
        promotion = dict(self._state()["promotions"]["p"], promotion_id="other")
        revocation = {"remote_identity": self.remote_id, "promotion_id": "p"}
        self.assertEqual(self._check(self._state(
            promotions={"p": promotion}, revocations={"r": revocation})),
            (None, "authority_revoked"))
        self.assertEqual(self._check(self._state(promotions={"p": promotion})),
                         (None, "authority_binding_mismatch"))

    def test_unreadable_revocations_refuse(self):
        for revocation in ("garbage", {}, {"remote_identity": self.remote_id},
                           {"promotion_id": "p"}, {"remote_identity": self.remote_id, "promotion_id": ""}):
            with self.subTest(revocation=revocation):
                self.assertEqual(self._check(self._state(revocations={"r": revocation})),
                                 (None, "authority_revoked"))
        self.assertEqual(self._check(self._state(revocations="garbage")),
                         (None, "authority_revoked"))

    def test_malformed_containers_and_ids_prove_nothing(self):
        for overrides in ({"promotions": "x"}, {"acceptances": "x"}, {"promotions": None},
                          {"acceptances": {"a": {"outcome": AcceptanceOutcome.COMPLETE.value,
                                                 "promotion_id": ["p"]}}}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(self._check(self._state(**overrides))[0])

    def test_expiry_is_judged_after_the_state_read(self):
        self._record_chain()
        real = self.lifecycle_repo.load_state

        def expires_while_waiting_for_the_lock():
            state = real()
            state["promotions"]["prom_1"]["expires_at"] = (
                datetime.datetime.now(datetime.timezone.utc).isoformat())
            return state

        with patch.object(self.lifecycle_repo, "load_state",
                          side_effect=expires_while_waiting_for_the_lock):
            report = self._evaluate()
        self.assertFalse(report["adoptable"])
        self.assertEqual(report["reason_code"], "authority_expired")

    def test_missing_state_is_not_created_by_evaluation(self):
        self.assertFalse(self.lifecycle_path.exists())
        report = self._evaluate()
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(self.lifecycle_path.exists())

    def test_unreadable_state_is_refused_and_left_in_place(self):
        for content in (b"{not json", b"[]", b"\xff"):
            with self.subTest(content=content):
                self.lifecycle_path.write_bytes(content)
                report = self._evaluate()
                self.assertFalse(report["ok"])
                self.assertEqual(report["support_tier"], "unavailable")
                self.assertFalse(report["adoptable"])
                self.assertEqual(report["reason_code"], "authority_unavailable")
                self.assertEqual(set(report["checks"].values()), {"unknown"})
                self.assertEqual(self.lifecycle_path.read_bytes(), content)

    def test_lock_failure_reports_unavailable(self):
        with patch.object(self.lifecycle_repo, "load_state", side_effect=PermissionError("synthetic")):
            report = self._evaluate()
        self.assertFalse(report["ok"])
        self.assertEqual(report["support_tier"], "unavailable")
        self.assertEqual(report["code"], "authority_unavailable")
        self.assertNotIn("synthetic", str(report))

    def test_writers_refuse_unreadable_state_instead_of_replacing_it(self):
        self.lifecycle_path.write_bytes(b"{not json")
        acceptance = CapabilityAcceptance(
            acceptance_id="a", promotion_id="p", sync_operation_id="s", ci_operation_id="c",
            cleanup_operation_id="d", policy_id="pol", evidence_id="ev", authority_binding_id="b",
            ordinary_evidence_digest="sha256:o", outcome=AcceptanceOutcome.COMPLETE, reason_code=None,
            request_id="r", request_digest="sha256:r", lifecycle_generation=1, completed_at=self._future(0))
        for write in (self.lifecycle_repo.get_generation,
                      lambda: self.lifecycle_repo.record_acceptance(acceptance, 0)):
            with self.assertRaises(LifecycleError):
                write()
            self.assertEqual(self.lifecycle_path.read_bytes(), b"{not json")

    def test_selectorless_revocation_for_another_remote_does_not_apply(self):
        revocation = {"remote_identity": "rem_other"}
        self.assertEqual(self._check(self._state(revocations={"r": revocation}))[1], "proven")

    def test_checks_are_honest_in_each_tier(self):
        # 1. Unproven
        report_unproven = self._evaluate()
        for name, val in report_unproven["checks"].items():
            self.assertEqual(val, "unknown", f"unproven check {name} should be unknown")

        # 2. Proven
        self._record_chain()
        report_proven = self._evaluate()
        self.assertEqual(report_proven["support_tier"], "proven")
        for name, val in report_proven["checks"].items():
            if name == "human_review":
                self.assertEqual(val, "unknown", "human_review should always be unknown")
            else:
                self.assertEqual(val, "pass", f"proven check {name} should be pass")

        # 3. Drifted
        report_drifted = self._evaluate(service="sha256:srv_drifted")
        self.assertEqual(report_drifted["support_tier"], "drifted")
        for name, val in report_drifted["checks"].items():
            if name == "packaging_revision_parity":
                self.assertEqual(val, "fail", "packaging_revision_parity should be fail when drifted")
            else:
                self.assertEqual(val, "unknown", f"drifted check {name} should be unknown")

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
