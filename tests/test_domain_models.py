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

    def test_invalid_entity_transitions_are_rejected(self):
        from sandbox.network.models import CleanupRecovery, ConsentRecord

        with self.assertRaisesRegex(ValueError, "decision"):
            ConsentRecord("resolved:stub", "maybe", "2026-08-01T00:00:00Z", 1)
        with self.assertRaisesRegex(ValueError, "status"):
            CleanupRecovery("binding", "resolved", "a" * 64, None, "failed", None, "done")


if __name__ == "__main__":
    unittest.main()
