"""Composition-root wiring stays explicit and inert by default."""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


class TestCredentialWiring(unittest.TestCase):
    def test_default_managed_dependencies_leave_t036_inert_without_process_calls(self):
        from sandbox.application.context import managed_native_dependencies
        from sandbox.runtimes.managed.repository import NativeRepository

        class Process:
            def __init__(self): self.calls = []
            def run(self, *args, **kwargs):
                self.calls.append((args, kwargs)); raise AssertionError("composition executed a process")

        with tempfile.TemporaryDirectory() as directory:
            process = Process()
            registry = SimpleNamespace(
                sandbox_base=lambda: Path(directory),
                load_project_config=lambda *_args, **_kwargs: {},
            )
            dependencies = managed_native_dependencies(
                {}, registry=registry, allowed_roots=(directory,), process=process,
                native_repository=NativeRepository(Path(directory) / "state.json"),
            )
        self.assertEqual(process.calls, [])
        self.assertIsNone(dependencies.credential_supervisor)
        self.assertIsNone(dependencies.plan_builder.credential_broker_compiler)
        self.assertIsNone(dependencies.cleanup.credential_broker)
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


if __name__ == "__main__":
    unittest.main()
