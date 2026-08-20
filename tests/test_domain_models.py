from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest


class TestDomainModels(unittest.TestCase):
    def test_hostname_intent_is_immutable_and_validated(self):
        from sandbox.network.models import HostnameIntent

        intent = HostnameIntent(
            project_root="/tmp/project", label="default", hostname="demo.test",
            source="project", suffix_class="test",
        )
        with self.assertRaises(FrozenInstanceError):
            intent.hostname = "changed.test"
        with self.assertRaisesRegex(ValueError, "normalized"):
            HostnameIntent(
                project_root="/tmp/project", label="default", hostname="Demo.TEST",
                source="project", suffix_class="test",
            )

    def test_observation_fingerprint_is_canonical(self):
        from sandbox.network.models import ResolverObservation

        first = ResolverObservation.create(
            owner_id="resolved:stub", manager="resolved", mode="stub",
            support_tier="implemented_unproven",
            extension={"port": 5300, "address": "127.0.0.54"},
            current_answers=("127.0.0.77",), evidence=("link:lo",),
        )
        second = ResolverObservation.create(
            owner_id="resolved:stub", manager="resolved", mode="stub",
            support_tier="implemented_unproven",
            extension={"address": "127.0.0.54", "port": 5300},
            current_answers=("127.0.0.77",), evidence=("link:lo",),
        )
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)

    def test_binding_observation_marks_drift_without_overwriting_last_applied(self):
        from sandbox.network.models import ResolutionBinding

        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77",
            adapter_id="hosts", owners=("/tmp/project::default",),
            desired={"line": "127.0.0.77 demo.test"},
        ).with_applied({"line": "127.0.0.77 demo.test"})
        drifted = binding.with_observed({"line": "127.0.0.88 demo.test"})

        self.assertEqual(drifted.lifecycle, "drifted")
        self.assertEqual(drifted.last_applied, binding.last_applied)
        self.assertNotEqual(drifted.observed_digest, drifted.last_applied_digest)

    def test_secret_like_fields_are_redacted_from_public_results(self):
        from sandbox.network.models import DomainResult

        result = DomainResult(
            ok=False, state="fallback", hostname="demo.test",
            hostname_source="project", strategy="external",
            strategy_source="detected", resolver={"token": "do-not-print"},
            actual_answers=(), expected_addresses=("127.0.0.77",),
            ownership="none", health="fallback",
            fallback_url="http://localhost:8123",
            reason={"code": "unsupported", "message": "password=do-not-print"},
            mutated=False,
        )
        payload = result.to_dict()
        self.assertEqual(payload["resolver"]["token"], "[redacted]")
        self.assertNotIn("do-not-print", str(payload))

    def test_selected_ingress_diagnostic_is_closed_and_fail_closed(self):
        from sandbox.network.models import DomainResult

        result = DomainResult(
            ok=True, state="ready", hostname="demo.test",
            hostname_source="project", strategy="external",
            strategy_source="detected", resolver={}, actual_answers=(),
            expected_addresses=("127.0.0.77",), ownership="owned",
            health="healthy", fallback_url="http://localhost:8123",
            reason={"code": "ready", "message": "safe"}, mutated=False,
            ingress={"state": "reachable", "address": "127.0.0.1",
                     "exception": "https://secret.invalid"},
            application={"state": "ready", "body": "password=hunter2"},
        )
        self.assertEqual(result.ingress, {"state": "reachable"})
        self.assertEqual(result.application, {"state": "ready"})
        self.assertEqual(result.to_dict()["ingress"], {"state": "reachable"})
        self.assertEqual(result.to_dict()["application"], {"state": "ready"})

        contradictory = DomainResult(
            ok=False, state="drifted", hostname="demo.test",
            hostname_source="project", strategy="external",
            strategy_source="detected", resolver={}, actual_answers=(),
            expected_addresses=("127.0.0.77",), ownership="residual",
            health="degraded", fallback_url="http://localhost:8123",
            reason={"code": "ready"}, mutated=False,
            ingress={"state": "unreachable", "raw": "secret"},
            application={"state": "ready", "raw": "secret"},
        )
        self.assertEqual(contradictory.to_dict()["ingress"], {"state": "unavailable"})
        self.assertEqual(contradictory.to_dict()["application"], {"state": "not_attempted"})
        self.assertEqual(
            contradictory.to_dict()["reason"]["code"], "ingress_probe_unavailable",
        )

    def test_invalid_entity_transitions_are_rejected(self):
        from sandbox.network.models import CleanupRecovery, ConsentRecord

        with self.assertRaisesRegex(ValueError, "decision"):
            ConsentRecord("resolved:stub", "maybe", "2026-08-01T00:00:00Z", 1)
        with self.assertRaisesRegex(ValueError, "status"):
            CleanupRecovery("binding", "resolved", "a" * 64, None, "failed", None, "done")


if __name__ == "__main__":
    unittest.main()


class TestOwnershipFingerprintIsStable(unittest.TestCase):
    """Repeat-safety depends on this: the full fingerprint moves whenever a TTL
    expires or an unrelated container adds a veth to `resolvectl status`."""

    @staticmethod
    def _observation(**overrides):
        from sandbox.network.models import ResolverObservation

        fields = {
            "owner_id": "systemd-resolved:host", "manager": "resolved",
            "mode": "stub", "support_tier": "implemented_unproven",
            "extension": {"kind": "route-only-domain", "global_takeover": False},
            "current_answers": (), "evidence": (),
        }
        fields.update(overrides)
        return ResolverObservation.create(**fields)

    def test_changed_answers_do_not_change_ownership(self):
        first = self._observation(current_answers=())
        second = self._observation(current_answers=("127.0.0.77",))
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.ownership_fingerprint, second.ownership_fingerprint)

    def test_changed_evidence_text_does_not_change_ownership(self):
        first = self._observation(evidence=("resolvectl: link 12 up",))
        second = self._observation(evidence=("resolvectl: link 13 up",))
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.ownership_fingerprint, second.ownership_fingerprint)

    def test_a_real_owner_change_does_change_ownership(self):
        first = self._observation()
        for change in ({"owner_id": "dnsmasq:host"}, {"manager": "dnsmasq"},
                       {"mode": "static"}, {"support_tier": "adoptable"},
                       {"extension": {"kind": "route-only-domain",
                                      "global_takeover": True}}):
            with self.subTest(change=change):
                self.assertNotEqual(first.ownership_fingerprint,
                                    self._observation(**change).ownership_fingerprint)
