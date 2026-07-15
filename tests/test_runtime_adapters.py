import unittest


class TestRuntimeAdapters(unittest.TestCase):
    def test_builtin_registration_selects_wordpress_and_leaves_compose_unregistered(self):
        from sandbox.runtimes import builtin_adapter_registry

        registry = builtin_adapter_registry({"status": lambda request: {"ok": True}})

        self.assertEqual(registry.for_kind("wordpress").adapter_id, "wordpress")
        self.assertIsNone(registry.for_kind("compose"))

    def test_adapter_registration_exposes_declared_capabilities(self):
        from sandbox.runtimes import builtin_adapter_registry

        registry = builtin_adapter_registry({"ensure": lambda request: {"ok": True}})
        wordpress = registry.for_kind("wordpress")

        self.assertIn("ensure", wordpress.adapter.capabilities)
        self.assertNotIn("wp_cli", wordpress.adapter.capabilities)

    def test_unsupported_kind_returns_structured_error(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes import builtin_adapter_registry
        from sandbox.runtimes.base import OperationError, OperationRequest

        service = RuntimeService(
            resolve_descriptor=lambda root, label: {"kind": "compose"},
            adapters=builtin_adapter_registry({}),
        )
        result = service.invoke(OperationRequest(project_root="/tmp/project", operation="status"))

        self.assertIsInstance(result, OperationError)
        self.assertEqual(result.code, "unsupported_kind")
        self.assertEqual(result.project_kind, "compose")

    def test_adapter_results_have_stable_kind_and_capability_shape(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes import builtin_adapter_registry
        from sandbox.runtimes.base import OperationError, OperationRequest, OperationResult

        service = RuntimeService(
            resolve_descriptor=lambda root, label: {"kind": "wordpress"},
            adapters=builtin_adapter_registry({"status": lambda request: {"url": "http://localhost"}}),
        )
        result = service.invoke(OperationRequest(project_root="/tmp/project", operation="status"))
        rejection = service.invoke(OperationRequest(project_root="/tmp/project", operation="wp_cli"))

        self.assertIsInstance(result, OperationResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.project_kind, "wordpress")
        self.assertIsInstance(rejection, OperationError)
        self.assertEqual(rejection.code, "unsupported_capability")
        self.assertIn("status", rejection.available_capabilities)


if __name__ == "__main__":
    unittest.main()
