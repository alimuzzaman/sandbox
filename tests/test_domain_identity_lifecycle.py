from __future__ import annotations

import unittest
from pathlib import Path
import tempfile


class MemoryRegistry:
    def __init__(self, record):
        self.record = dict(record)
        self.puts = []

    def registry_get(self, _root, label=None):
        return dict(self.record) if self.record else None

    def registry_put(self, root, label="default", **fields):
        self.puts.append((root, label, fields))
        self.record.update(fields)
        return dict(self.record)


class TestDomainIdentityLifecycle(unittest.TestCase):
    def test_persistence_does_not_overwrite_existing_identity(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({"domain": "existing.tst", "instance": "demo"})
        result = persist_hostname_intent(
            registry, "/tmp/project", "default", "new.test", "default",
        )
        self.assertEqual(result["domain"], "existing.tst")
        self.assertEqual(registry.puts, [])

    def test_persistence_records_new_identity_and_source_once(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({"instance": "demo"})
        result = persist_hostname_intent(
            registry, "/tmp/project", "default", "demo.test", "default",
        )
        self.assertEqual(result["domain"], "demo.test")
        self.assertEqual(registry.puts[0][2]["domain_source"], "default")

    def test_explicit_public_identity_is_preserved_when_ingress_is_incompatible(self):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.manifest import built_in_resolver_registry
        from sandbox.network.models import ResolverObservation
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            service = DomainService(
                config_loader=lambda root, label=None: {"root": root, "domains": {
                    "hostname": "store.example.com", "tld": "com", "strategy": None,
                    "wildcard": False, "suffixClass": "public",
                    "hostnameSource": "project", "strategySource": "default",
                }},
                project_registry=type("Registry", (), {"registry_get": staticmethod(
                    lambda root, label=None: {
                        "instance": "store", "url": "http://localhost:8456",
                    }
                )}),
                adapters=built_in_resolver_registry(),
                repository=DomainRepository(Path(tmp) / "state.json"),
                process=object(), http=object(), endpoints=object(),
                observer=lambda _hostname: ResolverObservation.create(
                    owner_id="external:public", manager="external", mode="public",
                    support_tier="external", current_answers=("203.0.113.9",),
                ),
                ingress_offer=lambda *_args: {
                    "accepted_addresses": ("127.0.0.77",),
                    "fallback_url": "http://localhost:8456",
                },
                verifier=lambda *_args: False,
            )
            result = service.apply("/tmp/store")
        self.assertEqual(result.state, "incompatible_identity")
        self.assertEqual(result.hostname, "store.example.com")
        self.assertEqual(result.fallback_url, "http://localhost:8456")
        self.assertFalse(result.mutated)

    def test_wordpress_absolute_urls_survive_hostname_persistence_and_reensure(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({
            "instance": "demo", "url": "http://localhost:8123",
            "home_url": "http://localhost:8123", "site_url": "http://localhost:8123",
        })
        first = persist_hostname_intent(
            registry, "/tmp/project", "default", "demo.test", "default",
        )
        second = persist_hostname_intent(
            registry, "/tmp/project", "default", "renamed.test", "default",
        )
        self.assertEqual(first["home_url"], "http://localhost:8123")
        self.assertEqual(second["site_url"], "http://localhost:8123")
        self.assertEqual(second["domain"], "demo.test")
        self.assertEqual(len(registry.puts), 1)

    def test_generic_compose_per_port_fallback_and_hostname_are_stable(self):
        from sandbox.application.instance_service import persist_hostname_intent

        registry = MemoryRegistry({
            "kind": "compose", "instance": "api", "http_port": 9321,
            "url": "http://localhost:9321",
        })
        first = persist_hostname_intent(
            registry, "/tmp/api", "preview", "api-preview.test", "default",
        )
        second = persist_hostname_intent(
            registry, "/tmp/api", "preview", "other.test", "default",
        )
        self.assertEqual(first["url"], "http://localhost:9321")
        self.assertEqual(second["http_port"], 9321)
        self.assertEqual(second["domain"], "api-preview.test")


if __name__ == "__main__":
    unittest.main()


class TestPersistedIdentityWins(unittest.TestCase):
    """038 FR-011/FR-024: status must speak about the hostname the instance is
    actually serving, not a synthesized `.test` twin."""

    def _service(self, record):
        from sandbox.application.domain_service import DomainService

        class Registry:
            @staticmethod
            def registry_get(_root, label="default"):
                return record

        config = {"root": "/projects/demo", "slug": "demo",
                  "domains": {"tld": "test", "hostname": None,
                              "hostnameSource": "default", "strategySource": "default",
                              "enabled": False, "wildcard": False, "strategy": None,
                              "ingress": None, "suffixClass": "test",
                              "tldSource": "default", "ingressSource": "default",
                              "enabledSource": "default", "wildcardSource": "default",
                              "explicit": False, "migrationState": "none"}}
        return DomainService(
            config_loader=lambda _dir, label="default": config,
            project_registry=Registry(), adapters=None, repository=None,
            process=None, http=None, endpoints=None,
        )

    def test_registered_domain_is_preferred_over_the_default_suffix(self):
        service = self._service({"instance": "demo", "domain": "demo.tst",
                                 "url": "https://demo.tst"})
        _config, _policy, hostname, fallback = service._context("/projects/demo", "default")
        self.assertEqual(hostname, "demo.tst")
        self.assertEqual(fallback, "https://demo.tst")

    def test_recorded_url_host_is_used_when_no_domain_field_exists(self):
        service = self._service({"instance": "demo", "url": "https://demo.tst"})
        _config, _policy, hostname, _fallback = service._context("/projects/demo", "default")
        self.assertEqual(hostname, "demo.tst")

    def test_per_port_url_does_not_become_an_identity(self):
        service = self._service({"instance": "demo", "url": "http://localhost:8188"})
        _config, _policy, hostname, _fallback = service._context("/projects/demo", "default")
        self.assertEqual(hostname, "demo.test")

    def test_explicit_hostname_still_outranks_the_registry(self):
        service = self._service({"instance": "demo", "domain": "demo.tst"})
        service.config_loader = lambda _dir, label="default": {
            "root": "/projects/demo", "slug": "demo",
            "domains": {"tld": "test", "hostname": "chosen.test",
                        "hostnameSource": "project", "strategySource": "default",
                        "strategy": None, "suffixClass": "test"},
        }
        _config, _policy, hostname, _fallback = service._context("/projects/demo", "default")
        self.assertEqual(hostname, "chosen.test")
