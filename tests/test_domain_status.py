from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class TestDomainStatus(unittest.TestCase):
    def _service(self, *, manager="resolved", answers=("127.0.0.77",),
                 observed=None, authority_health="healthy", verified=True,
                 strategy=None, strategy_source="default"):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.models import ResolutionBinding, ResolverObservation
        from sandbox.network.registry import ResolverAdapterRegistry, ResolverAdapterSpec
        from sandbox.network.repository import DomainRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = DomainRepository(Path(temporary.name) / "state.json")
        owner = f"{Path('/tmp/project').resolve()}::default"
        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77",
            adapter_id="resolved-adapter", owners=(owner,),
            desired={"route": "demo"},
        ).with_applied({"route": "demo"})
        repository.put_binding(binding)
        adapters = ResolverAdapterRegistry()
        adapter = object()
        adapters.register(ResolverAdapterSpec(
            "resolved-adapter", adapter, ("resolved",), ("linux",), "adoptable",
            frozenset({"exact"}), "proof", 10,
        ))
        observation = ResolverObservation.create(
            owner_id=f"{manager}:host", manager=manager, mode="scoped",
            support_tier="adoptable", current_answers=answers,
        )
        service = DomainService(
            config_loader=lambda root, label=None: {"root": root, "domains": {
                "hostname": "demo.test", "tld": "test", "strategy": strategy,
                "wildcard": False, "hostnameSource": "persisted",
                "strategySource": strategy_source,
            }},
            project_registry=type("Registry", (), {"registry_get": staticmethod(
                lambda root, label=None: {"instance": "demo", "url": "http://localhost:8123"}
            )}),
            adapters=adapters, repository=repository, process=object(), http=object(),
            endpoints=object(), observer=lambda _hostname: observation,
            ingress_offer=lambda _root, _label: {
                "accepted_addresses": ("127.0.0.77",),
                "fallback_url": "http://localhost:8123",
            },
            binding_observer=lambda _binding, _adapter: (
                {"route": "demo"} if observed is None else observed
            ),
            authority_observer=lambda: {"health": authority_health},
            verifier=lambda *_args: verified,
        )
        return service

    def test_healthy_status_reports_actual_expected_ownership_and_pin_source(self):
        result = self._service(
            strategy="resolved-adapter", strategy_source="machine_override",
        ).status("/tmp/project")
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_answers, ("127.0.0.77",))
        self.assertEqual(result.expected_addresses, ("127.0.0.77",))
        self.assertEqual(result.ownership, "owned")
        self.assertEqual(result.strategy_source, "machine_override")

    def test_resolver_owner_change_is_distinct_from_binding_drift(self):
        result = self._service(manager="unknown").status("/tmp/project")
        self.assertEqual(result.reason["code"], "resolver_owner_changed")
        result = self._service(observed={"route": "changed"}).status("/tmp/project")
        self.assertEqual(result.reason["code"], "binding_drifted")

    def test_authority_down_and_answer_mismatch_are_actionable(self):
        result = self._service(authority_health="unhealthy").status("/tmp/project")
        self.assertEqual(result.reason["code"], "authority_unhealthy")
        result = self._service(answers=("127.0.0.1",)).status("/tmp/project")
        self.assertEqual(result.reason["code"], "answer_mismatch")
        self.assertIn("stale cache", result.reason["message"])

    def test_matching_dns_with_failed_ingress_is_not_healthy(self):
        result = self._service(verified=False).status("/tmp/project")
        self.assertEqual(result.reason["code"], "verification_failed")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
