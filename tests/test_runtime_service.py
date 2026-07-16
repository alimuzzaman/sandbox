import unittest


class FakeAdapter:
    capabilities = frozenset({"status"})

    def __init__(self, calls):
        self.calls = calls

    def invoke(self, request):
        from sandbox.runtimes.base import OperationResult

        self.calls.append(("adapter.invoke", request.operation))
        return OperationResult(True, request.operation, request.project_root, "test", {"ready": True})


class TestRuntimeService(unittest.TestCase):
    def make_service(self, calls, *, adapter=None):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry

        adapters = AdapterRegistry()
        adapters.register("test", adapter or FakeAdapter(calls), kinds=("test",), owner="tests")

        def resolve(root, label=None):
            calls.append(("descriptor.resolve", root, label))
            return {"kind": "test", "root": root, "label": label or "default"}

        return RuntimeService(resolve_descriptor=resolve, adapters=adapters)

    def test_supported_operation_invokes_adapter(self):
        from sandbox.runtimes.base import OperationRequest, OperationResult

        calls = []
        result = self.make_service(calls).invoke(OperationRequest("/tmp/project", "status"))
        self.assertIsInstance(result, OperationResult)
        self.assertTrue(result.ok)
        self.assertEqual(calls[-1], ("adapter.invoke", "status"))
        self.assertEqual([call[0] for call in calls], ["descriptor.resolve", "adapter.invoke"])

    def test_unsupported_capability_returns_before_adapter(self):
        from sandbox.runtimes.base import OperationError, OperationRequest

        calls = []
        result = self.make_service(calls).invoke(OperationRequest("/tmp/project", "destroy"))
        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "unsupported_capability")
        self.assertEqual(calls, [("descriptor.resolve", "/tmp/project", "default")])

    def test_unknown_kind_returns_structured_error(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry, OperationError, OperationRequest

        service = RuntimeService(
            resolve_descriptor=lambda root, label=None: {"kind": "unknown", "root": root},
            adapters=AdapterRegistry(),
        )
        result = service.invoke(OperationRequest("/tmp/project", "status"))
        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "unsupported_kind")

    def test_malformed_descriptor_returns_structured_error_before_adapter(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry, OperationError, OperationRequest

        calls = []
        service = RuntimeService(
            resolve_descriptor=lambda root, label=None: calls.append(root) or [],
            adapters=AdapterRegistry(),
        )
        result = service.invoke(OperationRequest("/tmp/project", "status"))
        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "invalid_descriptor")
        self.assertEqual(result.requested_capability, "status")
        self.assertEqual(calls, ["/tmp/project"])

    def test_descriptor_kind_with_whitespace_is_rejected(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry, OperationRequest

        service = RuntimeService(
            resolve_descriptor=lambda root, label=None: {"kind": "bad kind"},
            adapters=AdapterRegistry(),
        )
        result = service.invoke(OperationRequest("/tmp/project", "status"))
        self.assertEqual(result.code, "invalid_descriptor")

    def test_wordpress_adapter_delegates_and_normalizes_dict(self):
        from sandbox.runtimes.base import OperationRequest, OperationResult
        from sandbox.runtimes.wordpress import WordPressAdapter

        calls = []

        def ensure(request):
            calls.append(request.project_root)
            return {"instance": "fixture", "url": "https://fixture.tst"}

        adapter = WordPressAdapter({"ensure": ensure})
        result = adapter.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertIsInstance(result, OperationResult)
        self.assertEqual(result.data["instance"], "fixture")
        self.assertEqual(calls, ["/tmp/project"])

    def test_final_wordpress_adapter_regression_preserves_result_and_capabilities(self):
        """Feature 022 final gate: the compatibility adapter adds no transport drift."""
        from sandbox.runtimes.base import OperationRequest, OperationResult
        from sandbox.runtimes.wordpress import WordPressAdapter

        expected = OperationResult(
            ok=True,
            operation="status",
            project_root="/tmp/project",
            project_kind="wordpress",
            data={"ready": True, "url": "https://fixture.tst"},
        )
        adapter = WordPressAdapter(
            {"status": lambda request: expected}, capabilities=("wp_cli",)
        )

        actual = adapter.invoke(OperationRequest("/tmp/project", "status"))

        self.assertIs(actual, expected)
        self.assertEqual(adapter.capabilities, frozenset({"status", "wp_cli"}))


if __name__ == "__main__":
    unittest.main()
