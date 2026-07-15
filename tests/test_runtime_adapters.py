import unittest


class _Adapter:
    adapter_id = "fixture"
    kinds = ("fixture",)
    capabilities = frozenset({"status"})


class TestRuntimeAdapters(unittest.TestCase):
    def test_builtin_registration_selects_wordpress_and_compose_adapters(self):
        from sandbox.runtimes import builtin_adapter_registry

        registry = builtin_adapter_registry()

        self.assertEqual(registry.for_kind("wordpress").adapter_id, "wordpress")
        self.assertEqual(registry.for_kind("compose").adapter_id, "compose")

    def test_adapter_registration_exposes_declared_capabilities(self):
        from sandbox.runtimes import builtin_adapter_registry

        registry = builtin_adapter_registry()
        compose = registry.for_kind("compose")

        self.assertIn("ensure", compose.adapter.capabilities)
        self.assertIn("status", compose.adapter.capabilities)
        self.assertNotIn("wp_cli", compose.adapter.capabilities)

    def test_unsupported_kind_returns_structured_error(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes import builtin_adapter_registry
        from sandbox.runtimes.base import OperationRequest

        result = RuntimeService(builtin_adapter_registry()).invoke(
            "unknown",
            OperationRequest(project_root="/tmp/project", operation="status"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "unsupported_project_kind")
        self.assertEqual(result.error.project_kind, "unknown")

    def test_adapter_results_have_stable_kind_and_capability_shape(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes import builtin_adapter_registry
        from sandbox.runtimes.base import OperationRequest

        result = RuntimeService(builtin_adapter_registry()).invoke(
            "compose",
            OperationRequest(project_root="/tmp/project", operation="wp_cli"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "unsupported_capability")
        self.assertEqual(result.error.project_kind, "compose")
        self.assertIn("status", result.error.available_capabilities)


if __name__ == "__main__":
    unittest.main()
