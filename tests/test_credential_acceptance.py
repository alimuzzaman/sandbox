import unittest
from types import SimpleNamespace
from datetime import datetime, timezone


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


if __name__ == "__main__":
    unittest.main()
