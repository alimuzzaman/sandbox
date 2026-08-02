import unittest


class _Adapter:
    capabilities = frozenset({"preflight", "ensure", "status", "health", "open",
                              "wordpress_cli", "exec", "test", "apply", "destroy"})

    def __init__(self):
        self.calls = []

    def invoke(self, request):
        from sandbox.runtimes.base import OperationResult
        self.calls.append(request.operation)
        return OperationResult(True, request.operation, request.project_root, "wordpress",
                               {"state": "ready", "mutated": False})


class TestNativeCapabilities(unittest.TestCase):
    def service(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry
        from sandbox.runtimes.registry import RuntimeBackendRegistry

        adapter = _Adapter()
        common = AdapterRegistry()
        common.register("wordpress", adapter, kinds=("wordpress",), owner="test")
        backends = RuntimeBackendRegistry()
        backends.register("ubuntu-nspawn", adapter, project_kinds=("wordpress",),
                          modes=("managed_native",), owner="test")
        return RuntimeService(
            resolve_descriptor=lambda *_args, **_kwargs: {
                "kind": "wordpress",
                "wordpressRuntime": {"mode": "managed_native", "adapter": "ubuntu-nspawn",
                                     "explicit": True},
            },
            adapters=common, backends=backends,
        ), adapter

    def test_required_capability_result_has_a_complete_envelope(self):
        from sandbox.runtimes.base import OperationRequest

        service, adapter = self.service()
        result = service.invoke(OperationRequest("/tmp/project", "ensure"))

        self.assertTrue(result.ok)
        self.assertTrue(result.data["capabilities"]["required"]["ensure"]["supported"])
        optional = result.data["capabilities"]["optional"]
        self.assertFalse(optional["wordpress.snapshot"]["supported"])
        self.assertIn("alternative", optional["wordpress.snapshot"])
        self.assertEqual(adapter.calls, ["ensure"])

    def test_unsupported_optional_capability_returns_safe_alternative_before_dispatch(self):
        from sandbox.runtimes.base import OperationRequest

        service, adapter = self.service()
        result = service.invoke(OperationRequest("/tmp/project", "wordpress.snapshot"))

        self.assertEqual(result.code, "unsupported_capability")
        self.assertIn("export", result.suggestion.lower())
        self.assertEqual(adapter.calls, [])

    def test_wordpress_adapter_dispatches_only_declared_operations(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.wordpress import WordPressAdapter

        adapter = WordPressAdapter({"ensure": lambda request: {"ok": True}},
                                   capabilities=("wordpress.snapshot",))
        self.assertTrue(adapter.invoke(OperationRequest("/tmp/project", "ensure")).ok)
        self.assertIn("wordpress.snapshot", adapter.optional_capabilities)
        unsupported = adapter.invoke(OperationRequest("/tmp/project", "wordpress.snapshot"))
        self.assertFalse(unsupported.ok)
        self.assertEqual(unsupported.data["reason"]["code"], "operation_dispatch_unavailable")

    def test_status_health_summary_reports_isolation_capabilities_and_recovery(self):
        from sandbox.core import runtime_health_lines

        lines = runtime_health_lines({
            "runtime": {"mode": "managed_native", "adapter": "ubuntu-nspawn",
                        "isolation": "managed_container"},
            "state": "drifted", "health": {"ok": False},
            "capabilities": {
                "required": {"status": {"supported": True},
                             "exec": {"supported": True}},
                "optional": {"logs": {"supported": False, "alternative": "status"},
                             "mail": {"supported": True}},
            },
            "recovery": ({"identity": "network"},),
        })

        self.assertIn("Runtime: managed_native/ubuntu-nspawn/managed_container", lines)
        self.assertIn("Runtime health: state=drifted; isolation=drifted", lines)
        self.assertIn("Capabilities: required=2/2; optional=1/2", lines)
        self.assertIn("Optional runtime gaps: logs", lines)
        self.assertIn("Runtime recovery: pending=1", lines)


if __name__ == "__main__":
    unittest.main()
