from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def apply(self, plan):
        self.calls.append(("apply", plan))
        return {"ok": True, "mutated": True, "applied": {"route": "ok"}}

    def rollback(self, plan):
        self.calls.append(("rollback", plan))
        return {"ok": True}


class FakeAuthority:
    def __init__(self):
        self.calls = []

    def ensure(self, bindings, **endpoint):
        self.calls.append(("ensure", bindings, endpoint))
        return {"ok": True}

    def remove(self, binding_id):
        self.calls.append(("remove", binding_id))
        return True


class TestDomainServiceIntegration(unittest.TestCase):
    def test_public_name_is_verify_only_and_never_calls_local_adapter(self):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.models import ResolverObservation
        from sandbox.network.manifest import built_in_resolver_registry
        from sandbox.network.repository import DomainRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        observation = ResolverObservation.create(
            owner_id="external:public", manager="external", mode="public",
            support_tier="external", current_answers=("127.0.0.77",),
        )
        service = DomainService(
            config_loader=lambda root, label=None: {
                "root": root, "domains": {
                    "hostname": "app.example.com", "tld": "com", "strategy": None,
                    "wildcard": False, "suffixClass": "public",
                    "hostnameSource": "project", "strategySource": "default",
                },
            },
            project_registry=type("Registry", (), {
                "registry_get": staticmethod(lambda root, label=None: {
                    "instance": "demo", "url": "http://localhost:8123",
                }),
            }),
            adapters=built_in_resolver_registry(),
            repository=DomainRepository(Path(temporary.name) / "state.json"),
            process=object(), http=object(), endpoints=object(),
            observer=lambda _hostname: observation,
            ingress_offer=lambda _root, _label: {
                "accepted_addresses": ("127.0.0.77",),
                "fallback_url": "http://localhost:8123", "capabilities": {},
            },
            verifier=lambda *_args: True,
        )
        result = service.apply("/tmp/project", interactive=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "ready")
        self.assertFalse(result.mutated)

    def test_unregistered_project_fails_without_synthesizing_identity(self):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.manifest import built_in_resolver_registry
        from sandbox.network.repository import DomainRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        service = DomainService(
            config_loader=lambda root, label=None: {
                "root": root, "slug": "demo",
                "domains": {"hostname": None, "tld": "test", "strategy": None,
                            "hostnameSource": "default", "strategySource": "default"},
            },
            project_registry=type("Registry", (), {
                "registry_get": staticmethod(lambda root, label=None: None),
            }),
            adapters=built_in_resolver_registry(),
            repository=DomainRepository(Path(temporary.name) / "state.json"),
            process=object(), http=object(), endpoints=object(),
        )
        result = service.status("/tmp/unregistered")
        self.assertEqual(result.state, "invalid")
        self.assertIsNone(result.hostname)
        self.assertEqual(result.reason["code"], "project_not_registered")
        self.assertFalse(result.mutated)

    def _service(self, *, interactive_consent=False, verified=True, changed=False):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.models import ResolverObservation
        from sandbox.network.registry import ResolverAdapterRegistry, ResolverAdapterSpec
        from sandbox.network.repository import DomainRepository

        observation = ResolverObservation.create(
            owner_id="resolved:stub", manager="resolved", mode="stub",
            support_tier="adoptable", extension={}, evidence=("stub",),
        )
        changed_observation = ResolverObservation.create(
            owner_id="resolved:changed", manager="resolved", mode="stub",
            support_tier="adoptable", extension={}, evidence=("changed",),
        )
        adapter = FakeAdapter()
        registry = ResolverAdapterRegistry()
        registry.register(ResolverAdapterSpec(
            "resolved", adapter, ("resolved",), ("linux",), "adoptable",
            frozenset({"exact"}), "live-evidence", 10,
        ))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = DomainRepository(Path(temporary.name) / "state.json")
        authority = FakeAuthority()
        calls = {"observe": 0}

        def observer(_hostname):
            calls["observe"] += 1
            return changed_observation if changed and calls["observe"] > 1 else observation

        service = DomainService(
            config_loader=lambda root, label=None: {
                "root": root, "slug": "demo",
                "domains": {"hostname": "demo.test", "tld": "test", "strategy": None,
                            "wildcard": False, "hostnameSource": "project",
                            "strategySource": "default"},
            },
            project_registry=type("Registry", (), {
                "registry_get": staticmethod(lambda root, label=None: {
                    "url": "http://localhost:8123", "instance": "demo",
                }),
            }),
            adapters=registry, repository=repository, process=object(), http=object(),
            endpoints=type("Endpoints", (), {"allocate": lambda self: ("127.0.0.54", 5300)})(),
            observer=observer,
            ingress_offer=lambda root, label: {
                "accepted_addresses": ("127.0.0.77",), "fallback_url": "http://localhost:8123",
                "capabilities": {"wildcard": False},
            },
            authority=authority,
            verifier=lambda hostname, addresses, fallback: verified,
            consent_decider=lambda owner: interactive_consent,
            identity_persister=lambda *_args: None,
        )
        return service, adapter, authority

    def test_noninteractive_first_use_returns_pending_without_mutation(self):
        service, adapter, authority = self._service()
        result = service.apply("/tmp/project", interactive=False)
        self.assertEqual(result.state, "pending_consent")
        self.assertFalse(result.mutated)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(authority.calls, [])

    def test_changed_observation_invalidates_plan_before_mutation(self):
        service, adapter, authority = self._service(interactive_consent=True, changed=True)
        result = service.apply("/tmp/project", interactive=True)
        self.assertEqual(result.state, "fallback")
        self.assertEqual(result.reason["code"], "resolver_changed")
        self.assertEqual(adapter.calls, [])
        self.assertEqual(authority.calls, [])

    def test_success_orders_authority_then_route_then_verification(self):
        service, adapter, authority = self._service(interactive_consent=True)
        result = service.apply("/tmp/project", interactive=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "ready")
        self.assertEqual(authority.calls[0][0], "ensure")
        self.assertEqual(adapter.calls[0][0], "apply")

    def test_failed_verification_rolls_back_route_and_authority(self):
        service, adapter, authority = self._service(interactive_consent=True, verified=False)
        result = service.apply("/tmp/project", interactive=True)
        self.assertEqual(result.state, "fallback")
        self.assertEqual([call[0] for call in adapter.calls], ["apply", "rollback"])
        self.assertEqual(authority.calls[-1][0], "remove")


if __name__ == "__main__":
    unittest.main()
