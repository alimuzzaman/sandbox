"""Foundational Credential Vault binding and metadata persistence contracts."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest


def _binding(**overrides):
    values = {
        "binding_id": "bind-fixture-1",
        "instance_id": "sb-0123456789ab",
        "source_reference": "fixture/API_TOKEN",
        "policy_digest": "a" * 64,
        "egress_digest": "b" * 64,
        "broker_digest": "c" * 64,
        "scheme": "HTTPS",
        "host": "API.Example.com.",
        "port": 443,
        "method": "get",
        "path": "/v1/items",
        "auth_form": "bearer",
        "expires_at": "2999-01-01T00:00:00Z",
        "owner": "project:fixture",
    }
    values.update(overrides)
    from sandbox.isolation.credential_binding import CredentialBinding

    return CredentialBinding(**values)


class TestCredentialBinding(unittest.TestCase):
    def test_canonicalization_and_secret_free_serialization(self):
        binding = _binding()
        self.assertEqual(binding.scheme, "https")
        self.assertEqual(binding.host, "api.example.com")
        self.assertEqual(binding.method, "GET")
        self.assertEqual(binding.port, 443)
        self.assertEqual(binding.auth_profile, "authorization_bearer")
        self.assertEqual(set(binding.to_dict()), {
            "binding_id", "instance_id", "source_reference", "policy_digest",
            "egress_digest", "broker_digest", "scheme", "host", "port", "method",
            "path", "auth_form", "expires_at", "owner", "version", "state",
        })
        self.assertNotIn("fixture/API_TOKEN", repr(binding))
        self.assertEqual(
            binding.scope(),
            {"scheme": "https", "host": "api.example.com", "port": 443,
             "method": "GET", "path": "/v1/items", "auth_form": "bearer"},
        )
        self.assertEqual(type(binding).from_dict(binding.to_dict()), binding)

    def test_exact_scope_and_security_fields_fail_closed(self):
        invalid = (
            {"host": "api.example.com", "port": 8443},
            {"host": "*.example.com"},
            {"path": "/v1/../items"},
            {"path": "/v1//items"},
            {"path": "/v1/items?next=other"},
            {"source_reference": "fixture/../API_TOKEN"},
            {"source_reference": "fixture/API_TOKEN=leak"},
            {"auth_form": "unsupported_auth"},
            {"policy_digest": "not-a-digest"},
            {"state": "not-a-state"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _binding(**changes)
        document = _binding().to_dict()
        document["unexpected"] = "value"
        with self.assertRaises(ValueError):
            type(_binding()).from_dict(document)
        self.assertEqual(_binding(auth_form="authorization_bearer").auth_profile,
                         "authorization_bearer")
        self.assertEqual(_binding(auth_form="x_api_key").auth_profile, "x_api_key")
        fixture = _binding(
            binding_id="instance-fixture-only",
            instance_id="instance-fixture-only",
            source_reference="ref:test:credential-vault:fixture",
            auth_form="authorization_bearer",
            host="api.invalid.example",
            path="/v1/fixture",
        )
        self.assertEqual(fixture.source_reference, "ref:test:credential-vault:fixture")

    def test_lifecycle_transitions_are_monotonic_and_versioned(self):
        binding = _binding()
        ready = binding.transition("ready")
        self.assertTrue(ready.admits_use())
        self.assertEqual(ready.version, 2)
        revoking = ready.begin_revoke()
        self.assertEqual((revoking.state, revoking.version), ("revoking", 3))
        revoked = revoking.transition("revoked")
        self.assertEqual((revoked.state, revoked.version), ("revoked", 4))
        with self.assertRaises(ValueError):
            revoked.transition("ready")
        with self.assertRaises(ValueError):
            revoked.transition("credential_pending")
        renewed = revoked.cas_update(revoked.version, expires_at="2999-02-01T00:00:00Z")
        self.assertEqual((renewed.state, renewed.version), ("credential_pending", 5))
        with self.assertRaises(ValueError):
            renewed.cas_update(renewed.version, state="ready")
        with self.assertRaises(ValueError):
            renewed.cas_update(renewed.version, unknown="x")
        with self.assertRaises(Exception) as raised:
            renewed.cas_update(4, path="/v2")
        self.assertEqual(getattr(raised.exception, "code", None), "binding_version_conflict")

    def test_expiry_is_observable_and_cannot_reopen(self):
        soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        binding = _binding(expires_at=soon)
        expired = binding.expire(now=datetime.now(timezone.utc) + timedelta(seconds=2))
        self.assertEqual(expired.state, "expired")
        self.assertFalse(expired.admits_use())
        with self.assertRaises(ValueError):
            expired.transition("ready")

    def test_repository_persists_only_binding_metadata_and_enforces_cas_owner(self):
        from sandbox.isolation.credential_binding import CredentialBinding
        from sandbox.runtimes.managed.credential_repository import (
            CredentialRepository, CredentialRepositoryConflict,
            CredentialRepositoryOwnershipError,
        )
        from sandbox.runtimes.managed.repository import NativeRepository

        with tempfile.TemporaryDirectory() as directory:
            native = NativeRepository(Path(directory) / "state.json")
            self.assertFalse(native.path.exists())
            self.assertEqual(native.readonly_snapshot()["version"], 1)
            self.assertFalse(native.path.exists())
            repository = CredentialRepository(native)
            binding = _binding()
            repository.create(binding)
            self.assertEqual(repository.get(binding.binding_id, owner=binding.owner), binding)
            raw = native.snapshot()["credential_bindings"][binding.binding_id]
            self.assertEqual(raw, binding.to_dict())
            self.assertNotIn("value", repr(raw))
            with self.assertRaises(CredentialRepositoryOwnershipError):
                repository.get(binding.binding_id, owner="project:other")
            with self.assertRaises(CredentialRepositoryConflict):
                repository.cas_update(binding.binding_id, expected_version=9,
                                      owner=binding.owner, path="/v2")
            updated = repository.cas_update(
                binding.binding_id, expected_version=1, owner=binding.owner, path="/v2",
            )
            self.assertEqual((updated.path, updated.state, updated.version),
                             ("/v2", "credential_pending", 2))
            reopened = CredentialRepository(NativeRepository(Path(directory) / "state.json"))
            self.assertEqual(reopened.get(binding.binding_id), updated)
            self.assertEqual(len(reopened.list(instance_id=binding.instance_id)), 1)
            self.assertIsInstance(CredentialBinding.from_dict(raw), CredentialBinding)
            from sandbox.commands.native import credential_status
            report = credential_status(repository=reopened)
            self.assertFalse(report["ok"])
            self.assertEqual(report["binding_states"][0]["binding_id"], binding.binding_id)
            self.assertNotIn("source_reference", repr(report))


if __name__ == "__main__":
    unittest.main()
