import unittest


class Adapter:
    capabilities = frozenset({"ensure", "status"})
    def __init__(self, name): self.name = name; self.calls = []
    def invoke(self, request):
        from sandbox.runtimes.base import OperationResult
        self.calls.append(request)
        return OperationResult(True, request.operation, request.project_root,
                               "wordpress", {"adapter": self.name})


class TestNativeRuntimeService(unittest.TestCase):
    def registry(self):
        from sandbox.runtimes.registry import RuntimeBackendRegistry
        registry = RuntimeBackendRegistry()
        adapters = {name: Adapter(name) for name in ("compose", "ubuntu-nspawn", "herd")}
        registry.register("compose", adapters["compose"], project_kinds=("wordpress",),
                          modes=("compose",), owner="test", order=10)
        registry.register("ubuntu-nspawn", adapters["ubuntu-nspawn"],
                          project_kinds=("wordpress",), modes=("managed_native",),
                          owner="test", order=20)
        registry.register("herd", adapters["herd"], project_kinds=("wordpress",),
                          modes=("incumbent_native",), owner="test", order=30)
        return registry, adapters

    def service(self, runtime, persisted=None):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry
        backends, adapters = self.registry()
        service = RuntimeService(resolve_descriptor=lambda *_args, **_kwargs: {
            "kind": "wordpress", "wordpressRuntime": runtime,
        }, adapters=AdapterRegistry(), backends=backends,
            resolve_persisted=lambda _root, _label: persisted)
        return service, adapters

    def test_resolves_project_kind_mode_and_adapter_as_three_dimensions(self):
        from sandbox.runtimes.base import OperationRequest
        service, adapters = self.service({"mode": "managed_native",
                                          "adapter": "ubuntu-nspawn", "explicit": True})
        result = service.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertTrue(result.ok); self.assertEqual(result.data["adapter"], "ubuntu-nspawn")
        self.assertEqual(len(adapters["compose"].calls), 0)

    def test_native_never_silently_falls_back_to_compose(self):
        from sandbox.runtimes.base import OperationRequest, OperationError
        service, adapters = self.service({"mode": "managed_native",
                                          "adapter": "missing", "explicit": True})
        result = service.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "unsupported_runtime")
        self.assertEqual(len(adapters["compose"].calls), 0)

    def test_implicit_native_and_unsupported_capability_fail_before_adapter(self):
        from sandbox.runtimes.base import OperationRequest
        implicit, adapters = self.service({"mode": "managed_native",
                                           "adapter": "ubuntu-nspawn", "explicit": False})
        self.assertEqual(implicit.invoke(OperationRequest("/tmp/project", "ensure")).code,
                         "explicit_selection_required")
        service, adapters = self.service({"mode": "managed_native",
                                          "adapter": "ubuntu-nspawn", "explicit": True})
        self.assertEqual(service.invoke(OperationRequest("/tmp/project", "destroy")).code,
                         "unsupported_capability")
        self.assertEqual(len(adapters["ubuntu-nspawn"].calls), 0)

    def test_duplicate_three_dimensional_registration_is_rejected(self):
        registry, adapters = self.registry()
        with self.assertRaises(ValueError):
            registry.register("ubuntu-nspawn", adapters["ubuntu-nspawn"],
                              project_kinds=("wordpress",), modes=("managed_native",),
                              owner="duplicate")

    def test_populated_instance_refuses_mode_or_adapter_switch(self):
        from sandbox.runtimes.base import OperationRequest
        service, adapters = self.service(
            {"mode": "compose", "adapter": "compose", "explicit": False},
            persisted={"mode": "managed_native", "adapter": "ubuntu-nspawn",
                       "populated": True},
        )
        result = service.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertEqual(result.code, "runtime_mode_change")
        self.assertTrue(all(not adapter.calls for adapter in adapters.values()))


if __name__ == "__main__": unittest.main()
