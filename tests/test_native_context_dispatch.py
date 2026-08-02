from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TestNativeContextDispatch(unittest.TestCase):
    def test_exact_environment_opt_in_becomes_candidate_authority_only_in_composition(self):
        from sandbox.application.context import runtime_service
        import sandbox_core as sc

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)), \
                mock.patch.dict("os.environ", {
                    "SANDBOX_NATIVE_PROOF_CANDIDATE": "ubuntu-24.04-systemd-255",
                }, clear=False):
            service = runtime_service({})
        managed = service._backends.resolve(
            "wordpress", "managed_native", "ubuntu-nspawn",
        ).adapter
        self.assertTrue(managed.proof_candidate)
        self.assertIsNone(managed.evidence_id)

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)), \
                mock.patch.dict("os.environ", {
                    "SANDBOX_NATIVE_PROOF_CANDIDATE": "ubuntu-24.04-systemd-255 ",
                }, clear=False):
            forged = runtime_service({})._backends.resolve(
                "wordpress", "managed_native", "ubuntu-nspawn",
            ).adapter
        self.assertFalse(forged.proof_candidate)

    def test_checked_in_promotion_is_the_only_normal_evidence_source(self):
        from sandbox.application.context import runtime_service
        import sandbox_core as sc

        promoted = ({"adapter_id": "ubuntu-nspawn", "adoptable": True,
                     "support_tier": "adoptable", "evidence_id": "live-039"},)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(sc, "sandbox_base", return_value=Path(directory)), \
                mock.patch("sandbox.runtimes.manifest.RUNTIME_DECLARATIONS", promoted), \
                mock.patch.dict("os.environ", {}, clear=True):
            managed = runtime_service({})._backends.resolve(
                "wordpress", "managed_native", "ubuntu-nspawn",
            ).adapter
        self.assertEqual(managed.evidence_id, "live-039")
        self.assertFalse(managed.proof_candidate)
    def test_context_injects_concrete_managed_dependencies_without_compose_fallback(self):
        from sandbox.application.context import runtime_service
        from sandbox.isolation.network import ManagedNetwork
        from sandbox.isolation.credentials import CredentialInjector, HelperCredentialInstaller
        from sandbox.isolation.preflight import IsolationPreflight
        from sandbox.runtimes.managed.adapter import ManagedRuntimeDependencies
        from sandbox.runtimes.managed.database import ManagedDatabase
        from sandbox.runtimes.managed.packages import ManagedPackagePlanner
        from sandbox.runtimes.managed.services import ManagedServiceSupervisor
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
        self.assertEqual(dependencies.database.helper,
                         "/usr/local/libexec/sandbox-native-helper")
        self.assertIsInstance(dependencies.services, ManagedServiceSupervisor)
        self.assertIsInstance(dependencies.credentials, CredentialInjector)
        self.assertIsInstance(dependencies.credentials.installer, HelperCredentialInstaller)
        self.assertIsNotNone(dependencies.plan_builder)
        self.assertIsNotNone(dependencies.provisioner)
        self.assertIsNotNone(dependencies.launcher)
        self.assertIsNotNone(dependencies.cleanup)

        compose_wordpress = service._adapters.for_kind("wordpress").adapter
        self.assertIn("compose.exec", compose_wordpress.capabilities)

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
