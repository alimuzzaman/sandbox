"""Credential binding lifecycle state-machine coverage."""

from pathlib import Path
import tempfile
import unittest

from tests.test_credential_broker_contract import OWNER, _binding


class TestCredentialLifecycle(unittest.TestCase):
    def repository(self):
        from sandbox.runtimes.managed.credential_repository import CredentialRepository
        from sandbox.runtimes.managed.repository import NativeRepository

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CredentialRepository(NativeRepository(Path(directory.name) / "state.json"))

    @staticmethod
    def pending():
        from sandbox.isolation.credential_binding import CredentialBinding

        value = _binding().to_dict()
        value.update(version=1, state="credential_pending")
        return CredentialBinding.from_dict(value)

    def test_create_pending_ready_revoke_and_remove_are_cas_bound(self):
        repository = self.repository()
        binding = self.pending()
        repository.create(binding)
        ready = repository.transition(binding.binding_id, "ready", expected_version=1, owner=OWNER)
        revoking = repository.revoke(binding.binding_id, expected_version=ready.version, owner=OWNER)
        self.assertEqual((revoking.state, revoking.version), ("revoking", ready.version + 1))
        revoked = repository.complete_revoke(binding.binding_id, expected_version=revoking.version, owner=OWNER)
        self.assertEqual(revoked.state, "revoked")
        self.assertTrue(repository.remove(binding.binding_id, expected_version=revoked.version, owner=OWNER))
        self.assertIsNone(repository.get(binding.binding_id))

    def test_expiry_and_blocked_records_cannot_return_ready_without_pending_update(self):
        repository = self.repository()
        binding = self.pending()
        repository.create(binding)
        expired = repository.transition(binding.binding_id, "expired", expected_version=1, owner=OWNER)
        with self.assertRaises(Exception):
            repository.transition(binding.binding_id, "ready", expected_version=expired.version, owner=OWNER)
        self.assertTrue(repository.remove(binding.binding_id, expected_version=expired.version, owner=OWNER))

        binding = self.pending()
        repository.create(binding)
        blocked = repository.transition(binding.binding_id, "blocked", expected_version=1, owner=OWNER)
        pending = repository.transition(binding.binding_id, "credential_pending", expected_version=blocked.version, owner=OWNER)
        self.assertEqual(pending.state, "credential_pending")
        self.assertEqual(pending.version, blocked.version + 1)


if __name__ == "__main__":
    unittest.main()
