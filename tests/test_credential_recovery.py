"""Restart recovery tests for pending/ready credential bindings."""

from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone

from tests.test_credential_broker_contract import INSTANCE, OWNER, _binding


class FakeLease:
    def __init__(self, binding):
        self.binding_id = binding.binding_id
        self.binding_version = binding.version
        self.invalidated = False

    def invalidate(self):
        self.invalidated = True


class FakeResolver:
    def __init__(self):
        self.issued = []
        self.invalidated = []

    def issue(self, binding):
        self.issued.append(binding)
        return FakeLease(binding)

    def invalidate(self, binding_id, *, binding_version=None):
        self.invalidated.append((binding_id, binding_version))
        return 1


class TestCredentialRecovery(unittest.TestCase):
    def repository(self):
        from sandbox.runtimes.managed.credential_repository import CredentialRepository
        from sandbox.runtimes.managed.repository import NativeRepository

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CredentialRepository(NativeRepository(Path(directory.name) / "state.json"))

    def service(self, repository, resolver, *, proof=True, egress=True,
                supervisor=None):
        from sandbox.runtimes.managed.credential_recovery import CredentialRecoveryService

        return CredentialRecoveryService(
            repository=repository, resolver=resolver,
            proof=lambda _binding: proof, egress=lambda _binding: egress,
            supervisor=supervisor,
            utc_clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    @staticmethod
    def pending_binding():
        from sandbox.isolation.credential_binding import CredentialBinding

        values = _binding().to_dict()
        values.update(version=1, state="credential_pending")
        return CredentialBinding.from_dict(values)

    def test_ready_restart_enters_pending_then_recovers_with_fresh_lease(self):
        repository = self.repository()
        resolver = FakeResolver()
        binding = self.pending_binding()
        repository.create(binding)
        # Create a ready record through the repository's lifecycle CAS path.
        ready = repository.transition(binding.binding_id, "ready", expected_version=binding.version,
                                     owner=OWNER)
        result = self.service(repository, resolver).recover(
            ready.binding_id, policy_digest=ready.policy_digest,
            egress_digest=ready.egress_digest, broker_digest=ready.broker_digest,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["fresh_lease"])
        self.assertEqual(resolver.invalidated, [(ready.binding_id, ready.version)])
        self.assertEqual(repository.get(ready.binding_id).state, "ready")
        self.assertGreater(repository.get(ready.binding_id).version, ready.version)

    def test_stale_proof_leaves_pending_and_never_issues_lease(self):
        repository = self.repository()
        resolver = FakeResolver()
        binding = self.pending_binding()
        repository.create(binding)
        result = self.service(repository, resolver, proof=False).recover(
            binding.binding_id, policy_digest=binding.policy_digest,
            egress_digest=binding.egress_digest, broker_digest=binding.broker_digest,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "proof_unavailable")
        self.assertEqual(resolver.issued, [])
        self.assertEqual(repository.get(binding.binding_id).state, "credential_pending")

    def test_revoked_binding_cannot_be_reopened_from_stale_metadata(self):
        repository = self.repository()
        resolver = FakeResolver()
        pending = self.pending_binding()
        repository.create(pending)
        revoked = repository.transition(pending.binding_id, "revoked", expected_version=pending.version,
                                        owner=OWNER)
        result = self.service(repository, resolver).recover(
            revoked.binding_id, policy_digest=revoked.policy_digest,
            egress_digest=revoked.egress_digest, broker_digest=revoked.broker_digest,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "binding_recovery_denied")
        self.assertEqual(resolver.issued, [])

    def test_supervisor_shutdown_failure_blocks_before_transition_or_issue(self):
        repository = self.repository()
        resolver = FakeResolver()
        binding = self.pending_binding()
        repository.create(binding)

        class Supervisor:
            def shutdown(self):
                raise OSError("private diagnostic")

        result = self.service(
            repository, resolver, supervisor=Supervisor(),
        ).recover(
            binding.binding_id, policy_digest=binding.policy_digest,
            egress_digest=binding.egress_digest, broker_digest=binding.broker_digest,
        )
        self.assertEqual(result["reason"]["code"], "supervisor_shutdown_failed")
        self.assertEqual(resolver.issued, [])
        self.assertEqual(repository.get(binding.binding_id).state,
                         "credential_pending")

    def test_old_lease_invalidation_failure_leaves_pending_without_fresh_lease(self):
        repository = self.repository()
        resolver = FakeResolver()
        binding = self.pending_binding()
        repository.create(binding)
        ready = repository.transition(
            binding.binding_id, "ready", expected_version=binding.version,
            owner=OWNER,
        )
        resolver.invalidate = lambda *_args, **_kwargs: 0
        result = self.service(repository, resolver).recover(
            ready.binding_id, policy_digest=ready.policy_digest,
            egress_digest=ready.egress_digest, broker_digest=ready.broker_digest,
        )
        self.assertEqual(result["reason"]["code"], "lease_invalidation_failed")
        self.assertEqual(resolver.issued, [])
        self.assertEqual(repository.get(ready.binding_id).state,
                         "credential_pending")

    def test_fresh_lease_cleanup_failure_never_persists_ready(self):
        repository = self.repository()
        binding = self.pending_binding()
        repository.create(binding)

        class ConflictRepository:
            def get(self, *args, **kwargs):
                return repository.get(*args, **kwargs)

            def transition(self, binding_id, state, **kwargs):
                if state == "ready":
                    raise RuntimeError("private conflict")
                return repository.transition(binding_id, state, **kwargs)

        class BadLease(FakeLease):
            def invalidate(self):
                raise OSError("private cleanup")

        class Resolver(FakeResolver):
            def issue(self, candidate):
                self.issued.append(candidate)
                return BadLease(candidate)

        resolver = Resolver()
        result = self.service(ConflictRepository(), resolver).recover(
            binding.binding_id, policy_digest=binding.policy_digest,
            egress_digest=binding.egress_digest, broker_digest=binding.broker_digest,
        )
        self.assertEqual(result["reason"]["code"], "lease_cleanup_failed")
        self.assertEqual(len(resolver.issued), 1)
        self.assertEqual(repository.get(binding.binding_id).state,
                         "credential_pending")


if __name__ == "__main__":
    unittest.main()
