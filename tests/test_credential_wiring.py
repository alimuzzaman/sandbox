"""Composition-root wiring stays explicit and inert by default."""

from pathlib import Path
import copy
import inspect
import pickle
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
        self.assertFalse(hasattr(dependencies, "credential_broker_reactor"))

    def test_composition_root_refuses_every_v1_override(self):
        from sandbox.application.context import managed_native_dependencies
        from sandbox.runtimes.managed.repository import NativeRepository

        with tempfile.TemporaryDirectory() as directory:
            registry = SimpleNamespace(
                sandbox_base=lambda: Path(directory),
                load_project_config=lambda *_args, **_kwargs: {},
            )
            common = dict(
                registry=registry, allowed_roots=(directory,),
                native_repository=NativeRepository(Path(directory) / "state.json"),
            )
            for name in ("credential_broker_compiler", "credential_supervisor",
                         "credential_broker", "credential_acceptance"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    managed_native_dependencies({}, **common, **{name: SimpleNamespace()})

    def test_exact_v2_lifecycle_override_is_retained_but_not_started_or_legacy_cleaned(self):
        from sandbox.application.context import managed_native_dependencies
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_credential_controller_lifecycle_v2 import (
            TestCredentialControllerLifecycleV2,
        )

        fixture = TestCredentialControllerLifecycleV2(
            methodName="test_exact_fixed_verbs_and_controller_first_start_broker_first_stop")
        lifecycle, executor = fixture.plans()

        class Process:
            def __init__(self): self.calls = []
            def run(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                raise AssertionError("inert lifecycle composition ran a process")

        with tempfile.TemporaryDirectory() as directory:
            process = Process()
            repository = NativeRepository(Path(directory) / "state.json")
            registry = SimpleNamespace(
                sandbox_base=lambda: Path(directory),
                load_project_config=lambda *_args, **_kwargs: {},
            )
            dependencies = managed_native_dependencies(
                {}, registry=registry, allowed_roots=(directory,), process=process,
                native_repository=repository, credential_supervisor=lifecycle,
            )
            adapter = ManagedNativeAdapter(
                preflight=SimpleNamespace(), repository=repository,
                dependencies=dependencies,
            )
        self.assertIs(dependencies.credential_supervisor, lifecycle)
        self.assertIs(adapter.credential_supervisor, lifecycle)
        self.assertIsNone(dependencies.cleanup.credential_broker)
        self.assertEqual(process.calls, [])
        self.assertEqual(executor.calls, [])

    def test_context_v1_factory_is_permanently_closed_without_dependency_access(self):
        from sandbox.application.context import managed_native_credential_broker
        import inspect

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

        with self.assertRaisesRegex(ValueError, "credential_broker_v1_disabled"):
            managed_native_credential_broker(
                instance_id="sb-0123456789ab", credential_repository=Repository(),
                resolver=Resolver(), proof=lambda _binding: True,
                egress=lambda _binding: True, upstream=Broker(), owner="project:fixture",
            )
        self.assertEqual(calls, [])
        source = inspect.getsource(managed_native_credential_broker)
        self.assertNotIn("CredentialRequestBroker", source)
        self.assertNotIn(".handle", source)

        import sandbox.runtimes.managed as managed_package
        self.assertFalse(hasattr(managed_package, "ExplicitCredentialConsumer"))

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

    def test_adapter_refuses_v1_handle_and_accepts_only_v2_guest_bridge(self):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.services import build_credential_v2_guest_bridge
        from tests import test_credential_controller_authority_v2 as fixtures

        class V1:
            def handle(self, *_args, **_kwargs):
                raise AssertionError("v1 fallback reached")

        adapter = ManagedNativeAdapter(
            preflight=SimpleNamespace(), repository=SimpleNamespace(),
            credential_broker=V1(),
        )
        denied = adapter.credential_request({}, transport_identity="guest-one")
        self.assertEqual(denied["error"]["code"], "credential_broker_unavailable")

        owner = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced",
        )
        owner.setUp()
        bridge = build_credential_v2_guest_bridge(
            owner.connection.mint_guest_bridge_receipt_v2(), owner.connection,
            canonical_guest_validator=lambda _value: True,
            now_ms=lambda: fixtures.NOW,
        )

        adapter = ManagedNativeAdapter(
            preflight=SimpleNamespace(), repository=SimpleNamespace(),
            credential_broker=bridge,
        )
        self.assertTrue(adapter.credential_request(
            fixtures.guest_request(), transport_identity="guest-one",
        )["ok"])

    def test_guest_bridge_receipt_refuses_spoof_replay_wrong_session_and_controller_receipt(self):
        from sandbox.runtimes.managed.services import (
            CredentialV2GuestBridge,
            build_credential_v2_guest_bridge,
        )
        from tests import test_credential_controller_authority_v2 as fixtures

        first = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced")
        first.setUp()
        second = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced")
        second.setUp()
        validator_calls = []
        clock_calls = []
        def fixed_validator(value):
            validator_calls.append(value)
            return True
        def fixed_clock():
            clock_calls.append("clock")
            return fixtures.NOW
        build = lambda receipt, connection: build_credential_v2_guest_bridge(
            receipt, connection, canonical_guest_validator=fixed_validator,
            now_ms=fixed_clock)

        with self.assertRaisesRegex(ValueError, "guest bridge is invalid"):
            CredentialV2GuestBridge(object(), object())

        spoof_calls = []
        class Spoof:
            protocol = "credential-broker-controller-v2"
            authenticated = True
            config = first.connection.config
            broker_epoch = first.connection.broker_epoch
            controller_epoch = first.connection.controller_epoch
            def consume_for_guest_bridge(self, _connection):
                spoof_calls.append("consume")
            def submit_guest_v2(self, *_args, **_kwargs):
                spoof_calls.append("submit")
        with self.assertRaisesRegex(ValueError, "receipt is required"):
            build(Spoof(), first.connection)
        self.assertEqual(spoof_calls, [])

        class SpoofTarget:
            def bind_guest_submit_capability_v2(self, *_args, **_kwargs):
                spoof_calls.append("bind")
                raise AssertionError("spoof target reached")
        exact_receipt = first.connection.mint_guest_bridge_receipt_v2()
        with self.assertRaisesRegex(ValueError, "receipt is invalid"):
            build(exact_receipt, SpoofTarget())
        self.assertEqual(spoof_calls, [])

        wrong_session = first.connection.mint_guest_bridge_receipt_v2()
        with self.assertRaisesRegex(ValueError, "receipt is invalid"):
            build(wrong_session, second.connection)

        receipt = exact_receipt
        bridge = build(receipt, first.connection)
        self.assertEqual(bridge.protocol, "credential-broker-controller-v2")
        for name, value in (
            ("_connection", second.connection), ("_validator", lambda _value: False),
            ("_now_ms", lambda: 0), ("_provenance", object()),
            ("connection", second.connection), ("protocol", "v1"),
            ("new_slot", object()),
        ):
            with self.subTest(attribute=name), self.assertRaises(AttributeError):
                setattr(bridge, name, value)
        for name in ("_connection", "_validator", "_now_ms", "_provenance"):
            with self.assertRaises(AttributeError):
                delattr(bridge, name)
        self.assertFalse(hasattr(bridge, "__dict__"))
        with self.assertRaises(TypeError):
            vars(bridge)
        with self.assertRaises(TypeError):
            bridge[0] = object()
        with self.assertRaises(TypeError):
            _ = bridge[0]
        with self.assertRaises(TypeError):
            tuple(bridge)
        with self.assertRaises(TypeError):
            _ = bridge + ()
        with self.assertRaises(TypeError):
            copy.copy(bridge)
        with self.assertRaises(TypeError):
            copy.deepcopy(bridge)
        with self.assertRaises(TypeError):
            pickle.dumps(bridge)
        rendered = repr(bridge)
        self.assertLessEqual(len(rendered), 96)
        self.assertNotIn(fixtures.MACHINE, rendered)
        self.assertNotIn(fixtures.BROKER_EPOCH, rendered)
        self.assertNotIn("object at", rendered)

        self.assertEqual(str(inspect.signature(bridge.submit_guest_v2)),
                         "(request, *, connection_identity)")
        self.assertEqual(bridge.submit_guest_v2(
            fixtures.guest_request(), connection_identity="guest-frozen",
        )["code"], "credential_pending")
        self.assertEqual((len(validator_calls), len(clock_calls)), (1, 1))
        self.assertFalse(hasattr(first.connection, "submit_guest_v2"))
        with self.assertRaises((AttributeError, TypeError)):
            first.connection.submit_guest_v2(
                fixtures.guest_request(), connection_identity="raw",
                now_ms=0, canonical_guest_validator=lambda _value: False)
        with self.assertRaises(TypeError):
            bridge.submit_guest_v2(
                fixtures.guest_request(), connection_identity="custom",
                now_ms=0, canonical_guest_validator=lambda _value: False)
        self.assertEqual((len(validator_calls), len(clock_calls)), (1, 1))
        with self.assertRaisesRegex(ValueError, "receipt is invalid"):
            build(receipt, first.connection)
        with self.assertRaises(Exception):
            receipt._broker_epoch = "f" * 32

        cross_epoch = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced")
        cross_epoch.setUp()
        epoch_receipt = cross_epoch.connection.mint_guest_bridge_receipt_v2()
        cross_epoch.connection.controller_epoch = "f" * 32
        with self.assertRaisesRegex(ValueError, "receipt is invalid"):
            build(epoch_receipt, cross_epoch.connection)

        controller_session = fixtures.controller_session()
        controller_receipt = controller_session.mint_composition_receipt(
            "public_acceptance")
        with self.assertRaisesRegex(ValueError, "receipt is required"):
            build(controller_receipt, first.connection)
        with self.assertRaisesRegex(Exception, "composition_refused"):
            controller_session.mint_composition_receipt("guest_bridge")

    def test_managed_v2_bridge_fixes_clock_and_validator_dependencies(self):
        from sandbox.runtimes.managed.services import CredentialV2GuestBridge

        with self.assertRaises((TypeError, ValueError)):
            CredentialV2GuestBridge(object(), object())

    def test_guest_input_cannot_select_python_authority_and_bridge_is_process_local(self):
        from sandbox.runtimes.managed.services import build_credential_v2_guest_bridge
        from tests import test_credential_controller_authority_v2 as fixtures

        owner = fixtures.TestBrokerOperationAuthorityV2(
            methodName="test_authorized_ack_precedes_lease_and_exact_fields_are_enforced")
        owner.setUp()
        bridge = build_credential_v2_guest_bridge(
            owner.connection.mint_guest_bridge_receipt_v2(), owner.connection,
            canonical_guest_validator=lambda _request: True,
            now_ms=lambda: fixtures.NOW,
        )
        forbidden = {"import_name", "callback", "python_object", "source_path"}
        self.assertTrue(forbidden.isdisjoint(fixtures.broker._GUEST_REQUEST_FIELDS))
        callback_calls = []
        values = {
            "import_name": "host.module",
            "callback": lambda: callback_calls.append("called"),
            "python_object": object(),
            "source_path": "/trusted/controller/path",
        }
        for index, (field, value) in enumerate(values.items(), 1):
            malformed = dict(fixtures.guest_request())
            malformed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    fixtures.ControllerServiceV2Error, "request_invalid"):
                bridge.submit_guest_v2(
                    malformed, connection_identity=f"guest-input-{index}")
        self.assertEqual(callback_calls, [])
        with self.assertRaises((TypeError, ValueError)):
            fixtures.broker.encode_guest_request(bridge)
        with self.assertRaises(TypeError):
            pickle.dumps(bridge)


if __name__ == "__main__":
    unittest.main()
