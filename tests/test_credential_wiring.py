"""Composition-root wiring stays explicit and inert by default."""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


class TestCredentialWiring(unittest.TestCase):
    def test_context_factory_requires_explicit_dependencies_and_scopes_lookup(self):
        from sandbox.application.context import managed_native_credential_broker

        calls = []

        class Repository:
            def get(self, binding_id, **kwargs):
                calls.append((binding_id, kwargs))
                return None

        class Resolver:
            def issue(self, _binding):
                raise AssertionError

        class Broker:
            def handle(self, value, *, transport_identity=None):
                calls.append((value, transport_identity))
                return {"ok": False}

        # The real factory returns the explicit broker type; the fake upstream
        # is allowed here because no request is run.
        broker = managed_native_credential_broker(
            instance_id="sb-0123456789ab", credential_repository=Repository(),
            resolver=Resolver(), proof=lambda _binding: True,
            egress=lambda _binding: True, upstream=Broker(), owner="project:fixture",
        )
        self.assertEqual(broker.instance_id, "sb-0123456789ab")
        self.assertIsNone(broker.binding_loader("bind-missing"))
        self.assertEqual(calls, [("bind-missing", {"owner": "project:fixture"})])

    def test_adapter_without_opt_in_broker_returns_bounded_refusal(self):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository

        with tempfile.TemporaryDirectory() as directory:
            adapter = ManagedNativeAdapter(
                preflight=SimpleNamespace(),
                repository=NativeRepository(Path(directory) / "state.json"),
            )
            result = adapter.credential_request({}, transport_identity="sb-0123456789ab")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "credential_broker_unavailable")


    def test_broker_service_supervisor_is_absent_from_default_composition(self):
        """Spec 045 T036: composing managed-native starts no broker."""
        from sandbox.application.context import managed_native_dependencies

        class Registry:
            def sandbox_base(self):
                return Path(tempfile.gettempdir())

            def load_project_config(self, *_args, **_kwargs):
                return {}

        dependencies = managed_native_dependencies(
            {}, registry=Registry(), allowed_roots=(Path(tempfile.gettempdir()),),
            isolation=SimpleNamespace(), packages=SimpleNamespace(),
        )
        self.assertIsNone(dependencies.credential_broker_service)
        self.assertIsNone(dependencies.credential_broker)
        self.assertIsNone(dependencies.credential_repository)

    def test_adapter_broker_lifecycle_is_a_bounded_refusal_without_wiring(self):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository

        with tempfile.TemporaryDirectory() as directory:
            adapter = ManagedNativeAdapter(
                preflight=SimpleNamespace(),
                repository=NativeRepository(Path(directory) / "state.json"),
            )
            self.assertIsNone(adapter.credential_broker_service)
            for action in ("start", "status", "stop", "activate"):
                result = adapter.credential_broker_lifecycle(action, {})
                self.assertFalse(result["ok"])
                self.assertFalse(result["mutated"])
                self.assertFalse(result["admission_open"])

    def test_a_supervisor_claiming_open_admission_is_refused_not_relayed(self):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository

        supervisor = SimpleNamespace(
            start=lambda _plan: {"ok": True, "state": "ready", "mutated": True,
                                 "admission_open": True},
            status=lambda _plan: {"ok": True, "state": "absent", "mutated": False,
                                  "admission_open": False},
            stop=lambda _plan: (_ for _ in ()).throw(RuntimeError("helper failed")),
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = ManagedNativeAdapter(
                preflight=SimpleNamespace(),
                repository=NativeRepository(Path(directory) / "state.json"),
                credential_broker_service=supervisor,
            )
            refused = adapter.credential_broker_lifecycle("start", {})
            self.assertEqual(refused["reason"]["code"], "credential_broker_service_invalid")
            self.assertFalse(refused["admission_open"])
            self.assertTrue(adapter.credential_broker_lifecycle("status", {})["ok"])
            failed = adapter.credential_broker_lifecycle("stop", {})
            self.assertEqual(failed["reason"]["code"], "credential_broker_service_failed")

    def test_the_explicit_broker_service_factory_only_builds_a_supervisor(self):
        from sandbox.application.context import managed_native_credential_broker_service
        from sandbox.runtimes.managed.services import CredentialBrokerSupervisor

        calls = []
        supervisor = managed_native_credential_broker_service(
            process=SimpleNamespace(run=lambda *args, **kwargs: calls.append(args)),
            helper="/fixed/native-helper",
        )
        self.assertIsInstance(supervisor, CredentialBrokerSupervisor)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
