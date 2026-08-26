"""Repository, resolver, egress, broker, and verified-upstream integration."""

from pathlib import Path
import tempfile
import unittest


class Transport:
    def __init__(self):
        self.calls = []

    def request(self, method, path, headers, body, timeout):
        self.calls.append((method, path, dict(headers), body, timeout))
        return {"status": 200, "headers": {"content-type": "application/json"}, "body": b'{"ok":true}'}

    def close(self):
        pass


class CountingResolver:
    def __init__(self, resolver):
        self.resolver = resolver
        self.issues = 0

    def issue(self, binding):
        self.issues += 1
        return self.resolver.issue(binding)

    def invalidate(self, binding_id, *, binding_version=None):
        return self.resolver.invalidate(binding_id, binding_version=binding_version)


class TestCredentialBrokerIntegration(unittest.TestCase):
    def setup_components(self):
        from sandbox.isolation.credential_binding import CredentialBinding
        from sandbox.isolation.credential_policy import CredentialEgressPolicy
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker
        from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.runtimes.managed.credential_repository import CredentialRepository
        from sandbox.runtimes.managed.repository import NativeRepository
        from sandbox.secrets.sources import SourceRegistry

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = root / ".env.fixture"
        source.write_text("API_TOKEN=SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456\n")
        source.chmod(0o600)
        sources = SourceRegistry(root, {"fixture": {"path": ".env.fixture"}}, personal_path=root / ".personal")
        resolver = CountingResolver(SecretReferenceResolver(sources))
        grant = EgressGrant("credential-host", "sb-0123456789ab", "hostname_https",
                            ("api.example.com",), (443,), "2999-01-01T00:00:00Z")
        grants = EgressGrantSet("sb-0123456789ab", "a" * 64, (grant,))
        binding = CredentialBinding(
            "bind-integration-1", "sb-0123456789ab", "fixture/API_TOKEN",
            "a" * 64, grants.digest, "c" * 64, "https", "api.example.com", 443,
            "POST", "/v1/items", "bearer", "2999-01-01T00:00:00Z", "project:fixture",
        )
        repository = CredentialRepository(NativeRepository(root / "state.json"))
        repository.create(binding)
        ready = repository.transition(binding.binding_id, "ready", expected_version=1, owner=binding.owner)
        transport = Transport()
        upstream = VerifiedHttpsUpstream(
            resolver=lambda _host: ("93.184.216.34",),
            connector=lambda *_args: transport,
        )
        egress = CredentialEgressPolicy(grants)
        broker = CredentialRequestBroker(
            ready.instance_id, resolver, lambda identity: repository.get(identity, owner=ready.owner),
            proof=lambda _binding: True, egress=lambda item: egress.check(item),
            upstream=upstream, owner=ready.owner,
        )
        return broker, ready, resolver, transport

    @staticmethod
    def request(binding, **changes):
        value = {
            "binding_id": binding.binding_id, "binding_version": binding.version,
            "scheme": "https", "host": binding.host, "port": 443,
            "method": binding.method, "path": binding.path,
            "headers": {"accept": "application/json"}, "body": b"{}",
            "content_type": "application/json", "deadline_ms": 5000,
            "correlation_id": "corr-integration-1",
        }
        value.update(changes)
        return value

    def test_matching_request_uses_real_registered_resolver_and_pinned_upstream(self):
        broker, binding, resolver, transport = self.setup_components()
        result = broker.handle(self.request(binding), transport_identity=binding.instance_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["body"], b'{"ok":true}')
        self.assertEqual(resolver.issues, 1)
        self.assertEqual(transport.calls[0][2]["authorization"], "Bearer SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456")
        self.assertNotIn("SB_SYNTHETIC_VALUE_NOT_A_SECRET", repr(result))

    def test_all_near_misses_are_refused_before_resolver_or_upstream(self):
        for field, value in (("host", "other.example.com"), ("path", "/v1/other"),
                             ("method", "GET"), ("port", 444), ("scheme", "http")):
            with self.subTest(field=field):
                broker, binding, resolver, transport = self.setup_components()
                result = broker.handle(self.request(binding, **{field: value}),
                                       transport_identity=binding.instance_id)
                self.assertFalse(result["ok"])
                self.assertEqual(resolver.issues, 0)
                self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
