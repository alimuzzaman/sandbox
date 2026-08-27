import unittest
from types import SimpleNamespace
from datetime import datetime, timezone
import threading


def bind_request():
    return {"action": "bind", "binding_id": "binding-0001", "version": 1,
            "machine_id": "sb-0123456789ab", "owner": "/tmp/project::default",
            "source_reference": "fixture/API_TOKEN", "scheme": "https",
            "host": "api.example.test", "port": 443, "method": "POST", "path": "/v1/check",
            "auth_profile": "authorization_bearer", "expires_at": "2030-01-01T00:00:00Z",
            "policy_digest": "1" * 64, "egress_digest": "2" * 64,
            "broker_digest": "3" * 64}


class TestCredentialAcceptance(unittest.TestCase):
    def test_strict_bind_rejects_extra_secret_shaped_field_and_broad_scope(self):
        from sandbox.runtimes.managed.credential_acceptance import parse_credential_acceptance
        value = bind_request(); value["header"] = "not-a-secret"
        with self.assertRaises(ValueError): parse_credential_acceptance(value)
        value = bind_request(); value["host"] = "*.example.test"
        with self.assertRaises(ValueError): parse_credential_acceptance(value)
        value = bind_request(); value["port"] = 80
        with self.assertRaises(ValueError): parse_credential_acceptance(value)

    def test_gate_order_and_public_projection_strip_internal_values(self):
        from sandbox.runtimes.managed.credential_acceptance import CredentialAcceptanceOperation
        from sandbox.runtimes.managed.adapter import (
            _is_proof_candidate_authority, _proof_candidate_authority,
        )
        calls = []
        identity = {"machine_id": "sb-0123456789ab", "owner": "/tmp/project::default",
                    "binding_id": "binding-0001", "version": 1,
                    "policy_digest": "1" * 64, "egress_digest": "2" * 64,
                    "broker_digest": "3" * 64, "executable_digest": "4" * 64,
                    "config_digest": "5" * 64}
        def gate(name): return lambda _request, _identity: calls.append(name) or True
        status = lambda _request, trusted: (calls.append("status") or {
            **trusted, "ok": True, "state": "credential_pending", "admission_open": False})
        operation = CredentialAcceptanceOperation(
            owner_lookup=lambda _request: calls.append("owner") or identity,
            preflight=gate("preflight"),
            broker_status=status, binding_health=gate("health"), egress_check=gate("egress"),
            actions={"bind": lambda _request, _identity: {
                "ok": True, "state": "bound", "mutated": True, "decision": "accepted",
                "reason": {"code": "ready"}}},
        )
        result = operation.invoke(bind_request(), proof_candidate_authority=
                                  _proof_candidate_authority("ubuntu-24.04-systemd-255"))
        self.assertEqual(calls, ["owner", "preflight", "status", "health", "egress"])
        self.assertTrue(result["ok"]); self.assertTrue(result["proof_candidate"])
        self.assertFalse(result["adoptable"])
        self.assertNotIn("source_reference", result); self.assertNotIn("lease_id", result)
        self.assertNotIn("diagnostics", result)

    def test_missing_adapter_dependency_is_bounded_unavailable(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        adapter = ManagedNativeAdapter(preflight=SimpleNamespace(), repository=SimpleNamespace())
        result = adapter.invoke(OperationRequest(
            "/tmp/project", "credential_acceptance",
            arguments={"request": {"action": "request"}},
        ))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["reason"]["code"], "managed_runtime_unproven")
        self.assertFalse(result.data["adoptable"])
        self.assertFalse(result.data["proof_candidate"])

    def test_model_canonicalization_registered_reference_profile_and_future_expiry(self):
        from sandbox.runtimes.managed.credential_acceptance import parse_credential_acceptance
        value = bind_request(); value["host"] = "API.Example.Test."
        parsed = parse_credential_acceptance(value, now=datetime(2029, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(parsed["host"], "api.example.test")
        self.assertEqual(parsed["source_reference"], "fixture/API_TOKEN")
        expired = bind_request(); expired["expires_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaises(ValueError): parse_credential_acceptance(expired)
        bad = bind_request(); bad["auth_profile"] = "bearer-v1"
        with self.assertRaises(ValueError): parse_credential_acceptance(bad)
        hostile = bind_request(); hostile["path"] = "/v1/%2e%2e/private"
        with self.assertRaises(ValueError): parse_credential_acceptance(hostile)
        hostile = bind_request(); hostile["owner"] = "/tmp/../project::default"
        with self.assertRaises(ValueError): parse_credential_acceptance(hostile)

    def test_forged_proof_truthy_gate_and_handler_exception_fail_closed(self):
        from sandbox.runtimes.managed.credential_acceptance import CredentialAcceptanceOperation
        from sandbox.runtimes.managed.adapter import _is_proof_candidate_authority
        identity = {"machine_id": "sb-0123456789ab", "owner": "/tmp/project::default",
                    "binding_id": "binding-0001", "version": 1,
                    "policy_digest": "1" * 64, "egress_digest": "2" * 64,
                    "broker_digest": "3" * 64, "executable_digest": "4" * 64,
                    "config_digest": "5" * 64}
        operation = CredentialAcceptanceOperation(
            owner_lookup=lambda _request: identity,
            preflight=lambda *_args: "truthy",
            broker_status=lambda *_args: {}, binding_health=lambda *_args: True,
            egress_check=lambda *_args: True, actions={})
        result = operation.invoke(bind_request(), proof_candidate_authority=object())
        self.assertEqual(result["reason"]["code"], "credential_preflight_failed")
        self.assertFalse(result["proof_candidate"])

        operation.preflight = lambda *_args: True
        result = operation.invoke(bind_request(), proof_candidate_authority=object())
        self.assertEqual(result["reason"]["code"], "managed_runtime_unproven")
        self.assertFalse(result["proof_candidate"])

        from sandbox.runtimes.managed.adapter import _proof_candidate_authority
        sealed = _proof_candidate_authority("ubuntu-24.04-systemd-255")
        operation.broker_status = lambda _request, trusted: {
            **{key: trusted[key] for key in ("machine_id", "policy_digest", "egress_digest",
                                             "broker_digest", "executable_digest", "config_digest")},
            "ok": True, "state": "credential_pending", "admission_open": False}
        operation.binding_health = lambda *_args: True
        operation.egress_check = lambda *_args: True
        operation.actions = {"bind": lambda *_args: (_ for _ in ()).throw(RuntimeError("private"))}
        result = operation.invoke(bind_request(), proof_candidate_authority=sealed)
        self.assertEqual(result["reason"], {"code": "credential_acceptance_indeterminate"})
        self.assertNotIn("private", repr(result))
        operation.actions = {"bind": lambda *_args: {
            "ok": True, "state": "bound", "mutated": True, "decision": "accepted",
            "reason": {"code": "ready"}, "diagnostics": "private"}}
        result = operation.invoke(bind_request(), proof_candidate_authority=sealed)
        self.assertFalse(result["ok"])
        self.assertNotIn("diagnostics", result)

    def test_proof_validator_is_not_injectable_and_adapter_blocks_leaking_service(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.credential_acceptance import CredentialAcceptanceOperation
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, _proof_candidate_authority,
        )
        with self.assertRaises(TypeError):
            CredentialAcceptanceOperation(
                owner_lookup=lambda _request: {}, preflight=lambda *_args: True,
                proof_validator=lambda _value: True, broker_status=lambda *_args: {},
                binding_health=lambda *_args: True, egress_check=lambda *_args: True,
                actions={})

        class Service:
            def __init__(self): self.calls = 0
            def invoke(self, *_args, **_kwargs):
                self.calls += 1
                return {"ok": True, "secret": "must-not-escape"}

        service = Service()
        unsealed = ManagedNativeAdapter(preflight=SimpleNamespace(), repository=SimpleNamespace(),
                                        credential_acceptance=service)
        request = OperationRequest("/tmp/project", "credential_acceptance",
                                   arguments={"request": bind_request()})
        result = unsealed.invoke(request)
        self.assertEqual(service.calls, 0)
        self.assertFalse(result.data["proof_candidate"])
        self.assertNotIn("secret", repr(result.data))

        sealed = ManagedNativeAdapter(
            preflight=SimpleNamespace(), repository=SimpleNamespace(),
            credential_acceptance=service,
            proof_candidate_authority=_proof_candidate_authority("ubuntu-24.04-systemd-255"),
        )
        result = sealed.invoke(request)
        self.assertEqual(service.calls, 0)
        self.assertFalse(result.ok)
        self.assertEqual(result.data["reason"]["code"], "credential_acceptance_unavailable")
        self.assertNotIn("secret", repr(result.data))

    def test_sealed_adapter_bounds_service_exception_without_diagnostic(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, _proof_candidate_authority,
        )
        adapter = ManagedNativeAdapter(
            preflight=SimpleNamespace(), repository=SimpleNamespace(),
            credential_acceptance=SimpleNamespace(
                protocol="credential-broker-controller-v2",
                invoke_v2=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("private-diagnostic")),
            ),
            proof_candidate_authority=_proof_candidate_authority("ubuntu-24.04-systemd-255"),
        )
        result = adapter.invoke(OperationRequest(
            "/tmp/project", "credential_acceptance",
            arguments={"request": bind_request()},
        ))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["reason"],
                         {"code": "credential_acceptance_unavailable"})
        self.assertTrue(result.data["proof_candidate"])
        self.assertFalse(result.data["adoptable"])
        self.assertNotIn("private-diagnostic", repr(result.data))

    def test_v2_public_projector_cannot_be_built_from_arbitrary_callable(self):
        from sandbox.runtimes.managed.credential_acceptance import (
            CredentialAcceptanceControllerV2,
        )
        with self.assertRaises((TypeError, ValueError)):
            CredentialAcceptanceControllerV2(lambda _request: {})

    def test_authenticated_v2_public_bind_uses_exact_controller_authorities(self):
        from sandbox.runtimes.managed.adapter import _proof_candidate_authority
        from sandbox.runtimes.managed.credential_acceptance import (
            build_credential_acceptance_controller_v2,
            controller_acceptance_interfaces_v2,
        )
        from tests.test_credential_controller_integration_v2 import graph

        runner, _events = graph()
        runner.authenticate()
        request = bind_request()
        request.update({
            "policy_digest": runner.config.policy_digest,
            "egress_digest": runner.config.egress_digest,
            "broker_digest": runner.config.broker_digest,
        })
        calls = []
        status_count = [1]

        def status(_request):
            calls.append("status")
            return {"protocol": "credential-broker-controller-v2",
                    "machine_id": runner.config.machine_id,
                    "broker_epoch": runner.controller_session.broker_epoch,
                    "controller_epoch": runner.controller_session.controller_epoch,
                    **runner.config.configured_digests(),
                    "admission_open": False,
                    "active_operation_count": status_count[0],
                    "lifecycle_state": "closed"}

        def binding(public, _status):
            calls.append("binding")
            return ({key: public[key] for key in (
                "binding_id", "version", "machine_id", "owner", "scheme", "host",
                "port", "method", "path", "auth_profile", "policy_digest",
                "egress_digest", "broker_digest")}
                    | {"binding_state": "prospective"})

        def egress(public, _binding):
            calls.append("egress")
            return {"allowed": True, **{key: public[key] for key in (
                "scheme", "host", "port", "method", "path", "egress_digest",
                "broker_digest")}}

        def bind(_public, _binding):
            calls.append("bind")
            return {"ok": True, "state": "bound", "mutated": True,
                    "decision": "accepted", "reason": {"code": "ready"}}

        interfaces = controller_acceptance_interfaces_v2(
            operation_authority=runner.operation_authority,
            lifecycle_authority=runner.lifecycle_authority,
            status_authority=status, binding_authority=binding,
            egress_authority=egress, bind_authority=bind,
            request_authority=lambda *_args: self.fail("request called"),
            revoke_authority=lambda *_args: self.fail("revoke called"),
        )
        service = build_credential_acceptance_controller_v2(
            runner.controller_session.mint_composition_receipt("public_acceptance"),
            runner.operation_authority, runner.lifecycle_authority, interfaces,
        )
        sealed = _proof_candidate_authority("ubuntu-24.04-systemd-255")
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_lifecycle_refused")
        status_count[0] = 0
        result = service.invoke_v2(
            request, proof_candidate_authority=
            sealed,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["status", "binding", "egress", "status",
                                 "binding", "egress", "bind"])
        self.assertNotIn("source_reference", result)
        self.assertNotIn("broker_epoch", result)
        runner.controller_session.close("test_complete")

    def test_v2_public_authority_refuses_stale_mismatch_and_indeterminate_action(self):
        from sandbox.runtimes.managed.adapter import _proof_candidate_authority
        from sandbox.runtimes.managed.credential_acceptance import (
            build_credential_acceptance_controller_v2,
            controller_acceptance_interfaces_v2,
        )
        from tests.test_credential_controller_integration_v2 import graph

        runner, _events = graph()
        runner.authenticate()
        runner.activate()
        sealed = _proof_candidate_authority("ubuntu-24.04-systemd-255")
        request = {"action": "request", "binding_id": "binding-0001", "version": 1,
                   "machine_id": runner.config.machine_id,
                   "owner": "/tmp/project::default", "content_type": "application/json",
                   "deadline_seconds": 5, "correlation_id": "correlation-0001"}

        status_protocol = ["credential-broker-controller-v1"]

        status_count = [0]

        def status(_request):
            return {"protocol": status_protocol[0],
                    "machine_id": runner.config.machine_id,
                    "broker_epoch": runner.controller_session.broker_epoch,
                    "controller_epoch": runner.controller_session.controller_epoch,
                    **runner.config.configured_digests(), "admission_open": True,
                    "active_operation_count": status_count[0],
                    "lifecycle_state": "active"}

        binding_state = ["stale"]

        def binding(public, _status):
            return {"binding_id": public["binding_id"], "version": public["version"],
                    "machine_id": public["machine_id"], "owner": public["owner"],
                    "binding_state": binding_state[0], "scheme": "https",
                    "host": "api.example.test", "port": 443, "method": "POST",
                    "path": "/v1/check", "auth_profile": "authorization_bearer",
                    "policy_digest": runner.config.policy_digest,
                    "egress_digest": runner.config.egress_digest,
                    "broker_digest": runner.config.broker_digest}

        egress_override = {}

        def egress(_public, current):
            result = {"allowed": True, **{key: current[key] for key in (
                "scheme", "host", "port", "method", "path", "egress_digest",
                "broker_digest")}}
            result.update(egress_override)
            return result

        request_calls = []
        request_mode = ["raise"]

        def request_action(*_args):
            request_calls.append("request")
            if request_mode[0] == "raise":
                raise RuntimeError("private-after-effect")
            runner.quiesce()
            return {"ok": True, "state": "completed", "mutated": True,
                    "decision": "accepted", "reason": {"code": "ready"}}

        interfaces = controller_acceptance_interfaces_v2(
            operation_authority=runner.operation_authority,
            lifecycle_authority=runner.lifecycle_authority,
            status_authority=status, binding_authority=binding,
            egress_authority=egress,
            bind_authority=lambda *_args: self.fail("bind called"),
            request_authority=request_action,
            revoke_authority=lambda *_args: self.fail("revoke called"),
        )
        service = build_credential_acceptance_controller_v2(
            runner.controller_session.mint_composition_receipt("public_acceptance"),
            runner.operation_authority, runner.lifecycle_authority, interfaces,
        )
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_protocol_unsupported")
        status_protocol[0] = "credential-broker-controller-v2"
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_binding_stale")
        binding_state[0] = "ready"
        mismatches = {
            "scheme": "http", "host": "other.example.test", "port": 444,
            "method": "GET", "path": "/v1/other", "egress_digest": "f" * 64,
            "broker_digest": "e" * 64,
        }
        for field, mismatch in mismatches.items():
            with self.subTest(egress_field=field):
                egress_override.clear(); egress_override[field] = mismatch
                result = service.invoke_v2(request, proof_candidate_authority=sealed)
                self.assertEqual(result["reason"]["code"], "credential_egress_refused")
        egress_override.clear()
        status_count[0] = 16
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_lifecycle_refused")
        self.assertEqual(request_calls, [])
        status_count[0] = 15
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_acceptance_indeterminate")
        self.assertEqual(request_calls, ["request"])
        self.assertNotIn("private-after-effect", repr(result))
        self.assertEqual(runner.lifecycle_authority.public_acceptance_reservations()[
            "request_receipts"], 0)

        request_mode[0] = "quiesce"
        status_count[0] = 0
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_acceptance_indeterminate")
        self.assertEqual(request_calls, ["request", "request"])

        runner.controller_session.close("test_stale")
        result = service.invoke_v2(request, proof_candidate_authority=sealed)
        self.assertEqual(result["reason"]["code"], "credential_session_stale")

    def test_v2_public_revoke_does_not_depend_on_egress_health(self):
        from sandbox.runtimes.managed.adapter import _proof_candidate_authority
        from sandbox.runtimes.managed.credential_acceptance import (
            build_credential_acceptance_controller_v2,
            controller_acceptance_interfaces_v2,
        )
        from tests.test_credential_controller_integration_v2 import graph

        runner, _events = graph(); runner.authenticate(); runner.activate()
        request = {"action": "revoke", "binding_id": "binding-0001", "version": 7,
                   "machine_id": runner.config.machine_id,
                   "owner": "/tmp/project::default"}
        calls = []

        def status(_request):
            calls.append("status")
            return {"protocol": "credential-broker-controller-v2",
                    "machine_id": runner.config.machine_id,
                    "broker_epoch": runner.controller_session.broker_epoch,
                    "controller_epoch": runner.controller_session.controller_epoch,
                    **runner.config.configured_digests(), "admission_open": True,
                    "active_operation_count": 7, "lifecycle_state": "active"}

        def binding(public, _status):
            calls.append("binding")
            return {"binding_id": public["binding_id"], "version": public["version"],
                    "machine_id": public["machine_id"], "owner": public["owner"],
                    "binding_state": "ready", "scheme": "https",
                    "host": "api.example.test", "port": 443, "method": "POST",
                    "path": "/v1/check", "auth_profile": "authorization_bearer",
                    "policy_digest": runner.config.policy_digest,
                    "egress_digest": runner.config.egress_digest,
                    "broker_digest": runner.config.broker_digest}

        def revoke(_public, _binding):
            calls.append("revoke")
            runner.quiesce()
            return {"ok": True, "state": "revoked", "mutated": True,
                    "decision": "accepted", "reason": {"code": "ready"}}

        interfaces = controller_acceptance_interfaces_v2(
            operation_authority=runner.operation_authority,
            lifecycle_authority=runner.lifecycle_authority,
            status_authority=status, binding_authority=binding,
            egress_authority=lambda *_args: self.fail("egress called during revoke"),
            bind_authority=lambda *_args: self.fail("bind called"),
            request_authority=lambda *_args: self.fail("request called"),
            revoke_authority=revoke,
        )
        service = build_credential_acceptance_controller_v2(
            runner.controller_session.mint_composition_receipt("public_acceptance"),
            runner.operation_authority, runner.lifecycle_authority, interfaces,
        )
        result = service.invoke_v2(
            request, proof_candidate_authority=
            _proof_candidate_authority("ubuntu-24.04-systemd-255"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "revoked")
        self.assertEqual(calls, ["status", "binding", "revoke"])
        runner.controller_session.close("test_complete")

    def test_v2_public_factory_refuses_cross_session_and_receipt_replay(self):
        from sandbox.runtimes.managed.credential_acceptance import (
            build_credential_acceptance_controller_v2,
            controller_acceptance_interfaces_v2,
        )
        from tests.test_credential_controller_integration_v2 import graph

        first, _events = graph(); first.authenticate()
        second, _events = graph(); second.authenticate()
        interfaces = controller_acceptance_interfaces_v2(
            operation_authority=first.operation_authority,
            lifecycle_authority=first.lifecycle_authority,
            status_authority=lambda *_args: {}, binding_authority=lambda *_args: {},
            egress_authority=lambda *_args: {}, bind_authority=lambda *_args: {},
            request_authority=lambda *_args: {}, revoke_authority=lambda *_args: {},
        )
        receipt = first.controller_session.mint_composition_receipt("public_acceptance")
        with self.assertRaises(ValueError):
            build_credential_acceptance_controller_v2(
                receipt, first.operation_authority, second.lifecycle_authority, interfaces,
            )
        service = build_credential_acceptance_controller_v2(
            receipt, first.operation_authority, first.lifecycle_authority, interfaces,
        )
        self.assertEqual(service.protocol, "credential-broker-controller-v2")
        with self.assertRaises(ValueError):
            build_credential_acceptance_controller_v2(
                receipt, first.operation_authority, first.lifecycle_authority, interfaces,
            )
        second_receipt = second.controller_session.mint_composition_receipt(
            "public_acceptance")
        with self.assertRaises(ValueError):
            build_credential_acceptance_controller_v2(
                second_receipt, second.operation_authority,
                second.lifecycle_authority, interfaces,
            )
        first.controller_session.close("test_complete")
        second.controller_session.close("test_complete")

    def test_success_and_refusal_reason_allowlists_are_disjoint(self):
        from sandbox.runtimes.managed.credential_acceptance import (
            _terminal_result, public_credential_acceptance_result,
            validate_credential_acceptance_service_result,
        )

        request = bind_request()
        refused_ready = {"ok": False, "state": "refused", "mutated": False,
                         "decision": "refused", "reason": {"code": "ready"}}
        result = _terminal_result(refused_ready, request, proof=True)
        self.assertEqual(result["reason"]["code"],
                         "credential_acceptance_indeterminate")
        accepted_refusal = {"ok": True, "state": "bound", "mutated": True,
                            "decision": "accepted",
                            "reason": {"code": "credential_binding_stale"}}
        result = _terminal_result(accepted_refusal, request, proof=True)
        self.assertEqual(result["reason"]["code"],
                         "credential_acceptance_indeterminate")
        result = public_credential_acceptance_result(
            {"action": "bind", "reason": {"code": "ready"}})
        self.assertEqual(result["reason"]["code"],
                         "credential_acceptance_indeterminate")
        public_refusal = {"ok": False, "action": "bind", "state": "blocked",
                          "mutated": False, "decision": "refused",
                          "reason": {"code": "ready"},
                          "proof_candidate": True, "adoptable": False}
        result = validate_credential_acceptance_service_result(public_refusal, request)
        self.assertEqual(result["reason"]["code"],
                         "credential_acceptance_indeterminate")

    def test_lifecycle_request_reservations_atomically_admit_sixteen_not_seventeen(self):
        from sandbox.isolation.credential_controller_lifecycle_v2 import LifecycleV2Error
        from tests.test_credential_controller_integration_v2 import graph

        runner, _events = graph(); runner.authenticate(); runner.activate()
        lifecycle = runner.lifecycle_authority
        barrier = threading.Barrier(18)
        receipts = []
        errors = []
        result_lock = threading.Lock()

        def reserve():
            barrier.wait()
            try:
                receipt = lifecycle.begin_public_acceptance(
                    action="request", active_operation_count=0,
                    lifecycle_state="active", admission_open=True,
                )
            except LifecycleV2Error as exc:
                with result_lock: errors.append(exc.code)
            else:
                with result_lock: receipts.append(receipt)

        workers = [threading.Thread(target=reserve) for _index in range(17)]
        for worker in workers: worker.start()
        barrier.wait()
        for worker in workers: worker.join(2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(receipts), 16)
        self.assertEqual(errors, ["public_acceptance_refused"])
        self.assertEqual(dict(lifecycle.public_acceptance_reservations()), {
            "request_receipts": 16, "action_receipts": 0, "total": 16,
        })

        first = receipts.pop()
        self.assertTrue(lifecycle.finish_public_acceptance(first, accepted=True))
        with self.assertRaisesRegex(LifecycleV2Error, "public_acceptance_refused"):
            lifecycle.finish_public_acceptance(first, accepted=False)
        for receipt in receipts:
            self.assertTrue(lifecycle.finish_public_acceptance(receipt, accepted=False))
        self.assertEqual(lifecycle.public_acceptance_reservations()["total"], 0)

        abandoned = [lifecycle.begin_public_acceptance(
            action="request", active_operation_count=0,
            lifecycle_state="active", admission_open=True,
        ) for _index in range(3)]
        self.assertEqual(lifecycle.public_acceptance_reservations()["total"], 3)
        lifecycle.close_public_acceptance()
        self.assertEqual(lifecycle.public_acceptance_reservations()["total"], 0)
        for receipt in abandoned:
            with self.assertRaisesRegex(LifecycleV2Error, "public_acceptance_refused"):
                lifecycle.finish_public_acceptance(receipt, accepted=False)
        runner.controller_session.close("test_complete")


if __name__ == "__main__":
    unittest.main()
