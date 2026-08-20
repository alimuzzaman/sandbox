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
        adapters["compose"].capabilities = frozenset({"ensure", "status", "wordpress.cli"})
        registry.register("compose", adapters["compose"], project_kinds=("wordpress",),
                          modes=("compose",), owner="test", order=10)
        registry.register("ubuntu-nspawn", adapters["ubuntu-nspawn"],
                          project_kinds=("wordpress",), modes=("managed_native",),
                          owner="test", order=20)
        registry.register("herd", adapters["herd"], project_kinds=("wordpress",),
                          modes=("incumbent_native",), owner="test", order=30)
        return registry, adapters

    def service(self, runtime, persisted=None, php_extensions=None):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry
        backends, adapters = self.registry()
        common = AdapterRegistry()
        common_adapter = Adapter("common")
        common_adapter.capabilities = frozenset({"ensure", "status", "wordpress.cli"})
        common.register("wordpress", common_adapter, kinds=("wordpress",), owner="test")
        descriptor = {"kind": "wordpress", "wordpressRuntime": runtime}
        if php_extensions is not None:
            descriptor["phpExtensions"] = php_extensions
        service = RuntimeService(resolve_descriptor=lambda *_args, **_kwargs: {
            **descriptor,
        }, adapters=common, backends=backends,
            resolve_persisted=lambda _root, _label: persisted)
        return service, adapters

    def test_capability_check_uses_selected_backend_not_generic_wordpress_adapter(self):
        service, _adapters = self.service({"mode": "managed_native",
                                           "adapter": "ubuntu-nspawn", "explicit": True})
        error = service.check("/tmp/project", "wordpress.cli")
        self.assertEqual(error.code, "unsupported_capability")
        self.assertNotIn("wordpress.cli", error.available_capabilities)

        compose, _adapters = self.service({"mode": "compose", "adapter": "compose"})
        self.assertIsNone(compose.check("/tmp/project", "wordpress.cli"))

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

    def test_status_adapter_php_report_is_closed_and_promotes_failure(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry, OperationRequest, OperationResult

        digest = "sha256:" + "a" * 64
        report = {
            "ok": False, "exit_code": 1,
            "desired": {"profile": "wordpress@1",
                         "catalog": {"revision": 1, "digest": digest,
                                      "private_path": "/private/catalog"},
                         "requirements": [{"name": "gd", "state": "enabled", "version": None}],
                         "resolution_digest": digest},
            "provenance": {"state": "unavailable", "password": "secret"},
            "observed": {plane: {"state": "unavailable", "php_version": None,
                                  "sapi": None, "extensions": {}, "issues": []}
                         for plane in ("web", "cli", "exec", "phpunit")},
            "readiness": {"state": "unavailable"},
            "staleness": {"state": "stale", "reason": "one_or_more_planes_unavailable"},
            "drift": {"state": "unknown"},
            "issues": [{"code": "plane_drift",
                        "message": "PHP extension observations differ between execution planes"}],
            "private": "/private/receipt",
        }
        class Adapter:
            capabilities = frozenset({"status"})
            def invoke(self, request):
                return OperationResult(True, "status", request.project_root, "test",
                                       {"state": "ready", "mutated": True,
                                        "php_extensions": report})
        registry = AdapterRegistry()
        registry.register("test", Adapter(), kinds=("test",), owner="test")
        service = RuntimeService(resolve_descriptor=lambda *_args, **_kwargs: {"kind": "test"},
                                 adapters=registry)
        result = service.invoke(OperationRequest("/tmp/project", "status"))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["state"], "blocked")
        self.assertFalse(result.data["mutated"])
        serialized = repr(dict(result.data))
        self.assertNotIn("/private/", serialized)
        self.assertNotIn("secret", serialized)

    def test_selected_incumbent_receives_only_descriptor_php_requirements(self):
        from sandbox.runtimes.base import OperationRequest

        service, adapters = self.service(
            {"mode": "incumbent_native", "adapter": "herd", "explicit": True},
            php_extensions={"extensions": {"gd": True}},
        )
        result = service.invoke(OperationRequest("/tmp/project", "status"))
        self.assertTrue(result.ok)
        self.assertEqual(len(adapters["herd"].calls), 1)
        forwarded = adapters["herd"].calls[0]
        self.assertEqual(forwarded.arguments["phpExtensions"], {"extensions": {"gd": True}})
        self.assertNotIn("wordpressRuntime", forwarded.arguments)


if __name__ == "__main__": unittest.main()
