import unittest


class _Adapter:
    capabilities = frozenset({"ensure", "status"})

    def __init__(self):
        self.calls = []

    def invoke(self, request):
        from sandbox.runtimes.base import OperationResult
        self.calls.append(request.operation)
        return OperationResult(True, request.operation, request.project_root, "wordpress")


class TestNativeModeLifecycle(unittest.TestCase):
    def service(self, runtime, persisted=None):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry
        from sandbox.runtimes.registry import RuntimeBackendRegistry

        adapter = _Adapter()
        adapters = AdapterRegistry()
        adapters.register("wordpress", adapter, kinds=("wordpress",), owner="test")
        backends = RuntimeBackendRegistry()
        backends.register("compose", adapter, project_kinds=("wordpress",),
                          modes=("compose",), owner="test")
        backends.register("ubuntu-nspawn", adapter, project_kinds=("wordpress",),
                          modes=("managed_native",), owner="test")
        return RuntimeService(
            resolve_descriptor=lambda *_args, **_kwargs: {"kind": "wordpress",
                                                           "wordpressRuntime": runtime},
            adapters=adapters, backends=backends,
            resolve_persisted=lambda *_args: persisted,
        ), adapter

    def test_compose_default_is_dispatched_without_native_opt_in(self):
        from sandbox.runtimes.base import OperationRequest

        service, adapter = self.service({"mode": "compose", "adapter": "compose", "explicit": False})
        result = service.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertTrue(result.ok)
        self.assertEqual(adapter.calls, ["ensure"])

    def test_populated_mode_switch_is_refused_for_check_and_invoke(self):
        from sandbox.runtimes.base import OperationRequest

        service, adapter = self.service(
            {"mode": "compose", "adapter": "compose", "explicit": False},
            persisted={"mode": "managed_native", "adapter": "ubuntu-nspawn", "populated": True},
        )
        self.assertEqual(service.check("/tmp/project", "status").code, "runtime_mode_change")
        self.assertEqual(service.invoke(OperationRequest("/tmp/project", "ensure")).code,
                         "runtime_mode_change")
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
