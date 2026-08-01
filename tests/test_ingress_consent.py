from pathlib import Path
import tempfile
import unittest


class Detector:
    def observe(self): return ()


class Registry:
    def items(self): return ()


class TestIngressConsent(unittest.TestCase):
    def service(self, decision):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.repository import IngressRepository
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = IngressRepository(Path(temporary.name) / "state.json")
        calls = []
        service = IngressService(
            detector=Detector(), registry=Registry(), repository=repository,
            consent_decider=lambda identity: calls.append(identity) or decision,
            clock=lambda: "2026-08-01T12:00:00+00:00",
        )
        return service, repository, calls

    @staticmethod
    def selection(fingerprint="machine-a"):
        from sandbox.ingress.models import IngressSelection
        return IngressSelection(
            frozenset({"http"}), frozenset({"http"}), "system-caddy",
            ("127.0.0.1",), "selected", fingerprint,
        )

    def test_acceptance_is_machine_identity_scoped_and_remembered(self):
        service, repository, calls = self.service(True)
        selection = self.selection()
        first = service.authorize(selection, interactive=True)
        second = service.authorize(selection, interactive=False)
        self.assertTrue(first["ok"]); self.assertTrue(second["ok"])
        self.assertEqual(len(calls), 1)
        saved = repository.snapshot()["consents"][first["consent_identity"]]
        self.assertEqual(saved["decision"], "accepted")
        self.assertEqual(saved["decided_at"], "2026-08-01T12:00:00+00:00")

    def test_decline_is_remembered_until_explicit_reconsideration(self):
        service, repository, calls = self.service(False)
        selection = self.selection()
        declined = service.authorize(selection, interactive=True)
        repeated = service.authorize(selection, interactive=True)
        reconsidered = service.reconsider(declined["consent_identity"])
        pending = service.authorize(selection, interactive=False)
        self.assertFalse(declined["ok"]); self.assertFalse(repeated["ok"])
        self.assertEqual(len(calls), 1); self.assertTrue(reconsidered["mutated"])
        self.assertEqual(pending["state"], "pending_consent")
        self.assertEqual(repository.snapshot()["consents"], {})

    def test_incumbent_fingerprint_change_requires_fresh_consent(self):
        service, _repository, _calls = self.service(True)
        service.authorize(self.selection("machine-a"), interactive=True)
        changed = service.authorize(self.selection("machine-b"), interactive=False)
        self.assertEqual(changed["state"], "pending_consent")


if __name__ == "__main__": unittest.main()
