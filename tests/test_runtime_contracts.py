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

    def test_registries_reject_malformed_identity_and_order_values(self):
        from sandbox.runtimes.base import AdapterRegistry, SchemaRegistry

        schemas = SchemaRegistry()
        for kind, owner, order in (("", "tests", 1), ("bad kind", "tests", 1), ("test", "", 1), ("test", "tests", True)):
            with self.subTest(kind=kind, owner=owner, order=order), self.assertRaises(ValueError):
                schemas.register(kind, object(), owner=owner, order=order)

        adapters = AdapterRegistry()
        for adapter_id, kinds, owner, order in (
            ("", ("test",), "tests", 1),
            ("test", ("test", "test"), "tests", 1),
            ("test", "test", "tests", 1),
            ("test", ("test",), "bad owner", 1),
            ("test", ("test",), "tests", False),
        ):
            with self.subTest(adapter_id=adapter_id, kinds=kinds, owner=owner, order=order), self.assertRaises(ValueError):
                adapters.register(adapter_id, object(), kinds=kinds, owner=owner, order=order)

    def test_operation_contract_is_immutable_and_secret_safe(self):
        from sandbox.runtimes.base import OperationError, OperationRequest, OperationResult

        request = OperationRequest(project_root="/tmp/project", operation="status")
        self.assertEqual(request.operation, "status")
        with self.assertRaises(Exception):
            request.operation = "destroy"
        for field, value in (("project_root", ""), ("operation", "bad operation"),
                             ("label", "bad label"), ("arguments", [])):
            with self.subTest(field=field):
                values = {"project_root": "/tmp/project", "operation": "status",
                          "label": "default", "arguments": {}}
                values[field] = value
                with self.assertRaises(ValueError):
                    OperationRequest(**values)
        with self.assertRaises(ValueError):
            OperationRequest("/tmp/\nproject", "status")
        with self.assertRaises(ValueError):
            OperationResult(True, "status", "/tmp/project", "wordpress", [])
        with self.assertRaises(ValueError):
            OperationResult(1, "status", "/tmp/project", "wordpress")
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
