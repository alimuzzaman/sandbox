from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TestNativeContextDispatch(unittest.TestCase):
    def test_context_injects_concrete_managed_dependencies_without_compose_fallback(self):
        from sandbox.application.context import runtime_service
        from sandbox.isolation.network import ManagedNetwork
        from sandbox.isolation.preflight import IsolationPreflight
        from sandbox.runtimes.managed.adapter import ManagedRuntimeDependencies
        from sandbox.runtimes.managed.database import ManagedDatabase
        from sandbox.runtimes.managed.packages import ManagedPackagePlanner
        from sandbox.services import AllowedRootPathPolicy, BoundedProcessRunner, UrlHttpProbe
        import sandbox_core as sc

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)):
            service = runtime_service({})

        managed = service._backends.resolve("wordpress", "managed_native", "ubuntu-nspawn").adapter
        dependencies = managed.dependencies
        self.assertIsInstance(dependencies, ManagedRuntimeDependencies)
        self.assertIsInstance(dependencies.process, BoundedProcessRunner)
        self.assertIsInstance(dependencies.http, UrlHttpProbe)
        self.assertIsInstance(dependencies.paths, AllowedRootPathPolicy)
        self.assertIs(dependencies.registry, sc)
        self.assertIsInstance(dependencies.isolation, IsolationPreflight)
        self.assertIs(managed.preflight, dependencies.isolation)
        self.assertIsInstance(dependencies.packages, ManagedPackagePlanner)
        self.assertIsInstance(dependencies.network, ManagedNetwork)
        self.assertEqual(dependencies.network.helper,
                         "/usr/local/libexec/sandbox-native-helper")
        self.assertIsInstance(dependencies.database, ManagedDatabase)

    def test_explicit_native_selection_never_calls_legacy_compose_operation(self):
        from sandbox.application.context import runtime_service
        from sandbox.runtimes.base import OperationRequest
        import sandbox_core as sc
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)), \
                mock.patch.object(sc, "load_project_config", return_value={
                    "kind": "wordpress", "wordpressRuntime": {
                        "mode": "managed_native", "adapter": "ubuntu-nspawn",
                        "explicit": True,
                    },
                }), \
                mock.patch("sandbox.core.ensure_instance") as legacy:
            result = runtime_service({}).invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok); legacy.assert_not_called()
        self.assertIn(result.data["reason"]["code"], {
            "isolation_prerequisite_missing", "managed_runtime_unproven",
        })


if __name__ == "__main__": unittest.main()
