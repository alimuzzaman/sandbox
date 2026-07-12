import unittest


class TestRuntimeContracts(unittest.TestCase):
    def test_schema_registry_is_deterministic_and_rejects_duplicates(self):
        from sandbox.runtimes.base import SchemaRegistry

        registry = SchemaRegistry()
        registry.register("zeta", object(), owner="tests.zeta", order=20)
        registry.register("alpha", object(), owner="tests.alpha", order=10)
        self.assertEqual([item.kind for item in registry.items()], ["alpha", "zeta"])
        with self.assertRaisesRegex(ValueError, "duplicate schema kind"):
            registry.register("alpha", object(), owner="tests.other")

    def test_adapter_registry_rejects_duplicate_id_and_kind_owner(self):
        from sandbox.runtimes.base import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("wordpress", object(), kinds=("wordpress",), owner="tests.wp")
        with self.assertRaisesRegex(ValueError, "duplicate adapter"):
            registry.register("wordpress", object(), kinds=("other",), owner="tests.other")
        with self.assertRaisesRegex(ValueError, "kind .* already owned"):
            registry.register("other", object(), kinds=("wordpress",), owner="tests.other")

    def test_operation_contract_is_immutable_and_secret_safe(self):
        from sandbox.runtimes.base import OperationError, OperationRequest

        request = OperationRequest(project_root="/tmp/project", operation="status")
        self.assertEqual(request.operation, "status")
        with self.assertRaises(Exception):
            request.operation = "destroy"
        error = OperationError(
            code="unsupported_capability",
            message="status is unavailable",
            project_kind="test",
            requested_capability="status",
            available_capabilities=(),
        )
        self.assertNotIn("secret", repr(error).lower())


if __name__ == "__main__":
    unittest.main()
