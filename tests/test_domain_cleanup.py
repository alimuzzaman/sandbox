from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class CleanupAdapter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def cleanup(self, binding):
        self.calls.append(binding.binding_id)
        return {"ok": self.ok, "mutated": self.ok}


class TestDomainCleanup(unittest.TestCase):
    def _service(self, observed, *, adapter_ok=True):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.models import ResolutionBinding, ResolverObservation
        from sandbox.network.registry import ResolverAdapterRegistry, ResolverAdapterSpec
        from sandbox.network.repository import DomainRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = DomainRepository(Path(temporary.name) / "state.json")
        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77",
            adapter_id="resolved", owners=("/tmp/project::default",),
            desired={"route": "demo"},
        ).with_applied({"route": "demo"})
        repository.put_binding(binding)
        adapter = CleanupAdapter(adapter_ok)
        adapters = ResolverAdapterRegistry()
        adapters.register(ResolverAdapterSpec(
            "resolved", adapter, ("resolved",), ("linux",), "adoptable",
            frozenset({"exact"}), "evidence", 10,
        ))
        observation = ResolverObservation.create(
            owner_id="resolved:stub", manager="resolved", mode="stub",
            support_tier="adoptable",
        )
        service = DomainService(
            config_loader=lambda root, label=None: {"root": root, "domains": {
                "hostname": "demo.test", "tld": "test", "strategy": None,
                "hostnameSource": "persisted", "strategySource": "default",
            }},
            project_registry=type("Registry", (), {"registry_get": staticmethod(
                lambda root, label=None: {"instance": "demo", "url": "http://localhost:8123"}
            )}),
            adapters=adapters, repository=repository, process=object(), http=object(),
            endpoints=object(), observer=lambda _hostname: observation,
            binding_observer=lambda _binding, _adapter: observed,
        )
        return service, repository, binding, adapter

    def test_unchanged_owned_binding_is_removed_and_repeat_is_safe(self):
        service, repository, binding, adapter = self._service({"route": "demo"})
        first = service.cleanup("/tmp/project")
        second = service.cleanup("/tmp/project")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertIsNone(repository.binding(binding.binding_id))
        self.assertEqual(adapter.calls, [binding.binding_id])

    def test_drifted_binding_is_preserved_with_recovery(self):
        service, repository, binding, adapter = self._service({"route": "changed"})
        result = service.cleanup("/tmp/project")
        self.assertEqual(result.state, "cleanup_incomplete")
        self.assertIsNotNone(repository.binding(binding.binding_id))
        self.assertEqual(repository.snapshot()["recovery"][binding.binding_id]["status"], "drifted")
        self.assertEqual(adapter.calls, [])

    def test_unavailable_observation_retains_retry_state(self):
        service, repository, binding, adapter = self._service(None)
        result = service.cleanup("/tmp/project")
        self.assertEqual(result.state, "cleanup_incomplete")
        recovery = repository.snapshot()["recovery"][binding.binding_id]
        self.assertEqual(recovery["status"], "unavailable")
        self.assertEqual(adapter.calls, [])

    def test_cleanup_retries_from_retained_binding_after_registry_deletion(self):
        service, repository, binding, adapter = self._service({"route": "demo"})
        service.project_registry = type("DeletedRegistry", (), {
            "registry_get": staticmethod(lambda root, label=None: None),
        })()
        result = service.cleanup("/tmp/project")
        self.assertTrue(result.ok)
        self.assertIsNone(repository.binding(binding.binding_id))
        self.assertEqual(adapter.calls, [binding.binding_id])


if __name__ == "__main__":
    unittest.main()
