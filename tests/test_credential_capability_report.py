"""Secret-free support/proof report contracts for Credential Vault."""

import unittest


class TestCredentialCapabilityReport(unittest.TestCase):
    def test_declared_but_unproven_capability_is_not_admissible(self):
        from sandbox.isolation.capability_report import (
            CapabilityPrerequisite, CapabilityReport, CapabilityReportError,
            EffectiveObservation,
        )

        report = CapabilityReport(
            prerequisites=(CapabilityPrerequisite("native-proof", "unknown", "evidence_missing"),),
            effective_isolation=(EffectiveObservation("private_network", "pass"),),
            policy_digest="a" * 64, egress_digest="b" * 64, broker_digest="c" * 64,
        )
        self.assertFalse(report.admissible)
        self.assertIn("support_unproven", report.derived_refusals)
        self.assertIn("not_adoptable", report.derived_refusals)
        with self.assertRaises(CapabilityReportError) as raised:
            report.require_admission()
        self.assertEqual(raised.exception.code, "capability_unproven")
        payload = report.to_dict()
        self.assertEqual(payload["support_tier"], "implemented_unproven")
        self.assertIsNone(payload["evidence_id"])
        self.assertNotIn("credential_value", repr(payload))

    def test_proven_report_requires_evidence_and_all_checks(self):
        from sandbox.isolation.capability_report import (
            BindingState, CapabilityPrerequisite, CapabilityReport,
            EffectiveObservation,
        )

        report = CapabilityReport(
            support_tier="proven", adoptable=True, evidence_id="native-proof-20260827",
            prerequisites=(CapabilityPrerequisite("native-proof", "pass"),),
            effective_isolation=(
                EffectiveObservation("private_network", "pass"),
                EffectiveObservation("root_helper_secret_free", "pass"),
            ),
            policy_digest="a" * 64, egress_digest="b" * 64, broker_digest="c" * 64,
            binding_states=(BindingState(
                "bind-1", 2,
                {"scheme": "https", "host": "api.example.com", "port": 443,
                 "method": "GET", "path": "/v1", "auth_form": "bearer"},
                "ready", "2999-01-01T00:00:00Z",
            ),),
            last_transition_at="2026-08-27T00:00:00+00:00",
            last_transition_reason="proof_verified",
        )
        self.assertTrue(report.admissible)
        report.require_admission()
        restored = type(report).from_dict(report.to_dict())
        self.assertEqual(restored, report)
        self.assertNotIn("source_reference", repr(report.to_dict()))

    def test_proven_report_without_effective_proof_or_digests_stays_blocked(self):
        from sandbox.isolation.capability_report import CapabilityReport

        report = CapabilityReport(
            support_tier="proven", adoptable=True, evidence_id="proof-1",
        )
        self.assertFalse(report.admissible)
        self.assertIn("prerequisites_missing", report.derived_refusals)
        self.assertIn("effective_isolation_missing", report.derived_refusals)
        self.assertIn("policy_digest_missing", report.derived_refusals)

    def test_missing_or_drifted_observation_blocks_even_proven_declaration(self):
        from sandbox.isolation.capability_report import (
            CapabilityPrerequisite, CapabilityReport, EffectiveObservation,
        )

        for status in ("fail", "unknown"):
            with self.subTest(status=status):
                report = CapabilityReport(
                    support_tier="proven", adoptable=True, evidence_id="proof-1",
                    prerequisites=(CapabilityPrerequisite("native-proof", "pass"),),
                    effective_isolation=(EffectiveObservation("policy", status, "digest_drift"),),
                )
                self.assertFalse(report.admissible)
                self.assertIn(f"effective_isolation_{status}", report.derived_refusals)

    def test_blocked_and_unavailable_tiers_are_explicit_refusals(self):
        from sandbox.isolation.capability_report import CapabilityReport

        for tier in ("blocked", "unavailable"):
            with self.subTest(tier=tier):
                report = CapabilityReport(support_tier=tier)
                self.assertFalse(report.admissible)
                self.assertIn(f"support_{tier}", report.derived_refusals)
                self.assertIn("not_adoptable", report.derived_refusals)

    def test_health_and_stale_digest_observations_cannot_promote_support(self):
        from sandbox.isolation.capability_report import (
            CapabilityPrerequisite, CapabilityReport, EffectiveObservation,
        )

        report = CapabilityReport(
            support_tier="proven", adoptable=True, evidence_id="proof-1",
            prerequisites=(CapabilityPrerequisite("policy-digest", "fail", "stale_digest"),),
            effective_isolation=(
                EffectiveObservation("periodic-health", "unknown", "health_unavailable"),
                EffectiveObservation("egress", "fail", "digest_drift"),
            ),
            policy_digest="a" * 64, egress_digest="b" * 64, broker_digest="c" * 64,
        )
        self.assertFalse(report.admissible)
        self.assertIn("prerequisite_fail", report.derived_refusals)
        self.assertIn("effective_isolation_unknown", report.derived_refusals)
        self.assertIn("effective_isolation_fail", report.derived_refusals)

    def test_unsupported_runtime_cannot_reuse_managed_native_report(self):
        from sandbox.isolation.capability_report import CapabilityReport

        with self.assertRaises(ValueError):
            CapabilityReport(runtime="compose")

    def test_sensitive_fields_unknown_fields_and_invalid_states_are_rejected(self):
        from sandbox.isolation.capability_report import (
            BindingState, CapabilityReport,
        )

        with self.assertRaises(ValueError):
            BindingState("bind-1", 1, {"scheme": "https", "host": "api.example.com",
                                        "authorization": "not-allowed"},
                         "ready", "2999-01-01T00:00:00Z")
        with self.assertRaises(ValueError):
            CapabilityReport(support_tier="proven", adoptable=True)
        document = CapabilityReport().to_dict()
        document["unknown"] = "field"
        with self.assertRaises(ValueError):
            CapabilityReport.from_dict(document)
        # Nested diagnostic maps are not a bypass for the sensitive-key guard.
        with self.assertRaises(ValueError):
            from sandbox.isolation.capability_report import _safe_mapping
            _safe_mapping({"details": [{"token": "not-allowed"}]}, "details")


if __name__ == "__main__":
    unittest.main()
