"""Pure request-contract tests for the explicit Credential Vault broker."""

from datetime import datetime, timezone
import unittest


INSTANCE = "sb-0123456789ab"
OWNER = "project:fixture"
SYNTHETIC_VALUE = b"SB_SYNTHETIC_VALUE_NOT_A_SECRET_123456"


def _binding(**overrides):
    from sandbox.isolation.credential_binding import CredentialBinding

    values = {
        "binding_id": "bind-broker-1",
        "instance_id": INSTANCE,
        "source_reference": "fixture/API_TOKEN",
        "policy_digest": "a" * 64,
        "egress_digest": "b" * 64,
        "broker_digest": "c" * 64,
        "scheme": "https",
        "host": "api.example.com",
        "port": 443,
        "method": "POST",
        "path": "/v1/items",
        "auth_form": "bearer",
        "expires_at": "2999-01-01T00:00:00Z",
        "owner": OWNER,
    }
    values.update(overrides)
    return CredentialBinding(**values).transition("ready")


class FakeLease:
    def __init__(self, value=SYNTHETIC_VALUE):
        self.value = value
        self.consumed = False

    def consume(self, callback):
        if self.consumed:
            raise AssertionError("lease was consumed twice")
        self.consumed = True
        return callback(self.value)


class FakeResolver:
    def __init__(self):
        self.issues = []
        self.invalidations = []

    def issue(self, binding):
        self.issues.append(binding)
        return FakeLease()

    def invalidate(self, binding_id, *, binding_version=None):
        self.invalidations.append((binding_id, binding_version))
        return 1


class FakeUpstream:
    def __init__(self):
        self.calls = []

    def request(self, binding, request, credential):
        self.calls.append((binding, request, credential))
        return {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"ok":true}',
        }


class TestCredentialBrokerContract(unittest.TestCase):
    def broker(self, *, binding=None, proof=True, egress=True, audit=None,
               upstream=None):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker

        binding = binding or _binding()
        resolver = FakeResolver()
        upstream = upstream or FakeUpstream()
        broker = CredentialRequestBroker(
            INSTANCE,
            resolver,
            lambda binding_id: binding if binding_id == binding.binding_id else None,
            proof=lambda _binding: proof,
            egress=lambda _binding: egress,
            upstream=upstream,
            owner=OWNER,
            audit=audit,
        )
        broker._test_resolver = resolver
        broker._test_upstream = upstream
        return broker

    def request(self, **overrides):
        values = {
            "binding_id": "bind-broker-1",
            "binding_version": 2,
            "scheme": "HTTPS",
            "host": "API.Example.com.",
            "port": 443,
            "method": "post",
            "path": "/v1/items",
            "headers": {"Accept": "application/json"},
            "body": b"{}",
            "content_type": "application/json",
            "deadline_ms": 5000,
            "correlation_id": "corr-contract-1",
        }
        values.update(overrides)
        return values

    def test_matching_request_is_bounded_and_uses_one_lease(self):
        broker = self.broker()
        result = broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.correlation_id, "corr-contract-1")
        self.assertEqual(result.body, b'{"ok":true}')
        self.assertEqual(len(broker._test_resolver.issues), 1)
        self.assertEqual(len(broker._test_upstream.calls), 1)
        self.assertEqual(broker._test_upstream.calls[0][2], SYNTHETIC_VALUE)
        self.assertNotIn(SYNTHETIC_VALUE, repr(result).encode())
        self.assertEqual(
            [(record["decision"], record.get("outcome"))
             for record in broker.audit.records],
            [("allow", None), ("complete", "complete")],
        )

    def test_response_uses_exact_header_allowlist(self):
        class Upstream(FakeUpstream):
            def request(self, binding, request, credential):
                result = super().request(binding, request, credential)
                result["headers"].update({
                    "Set-Cookie": "session=not-returned",
                    "Location": "https://other.example/",
                    "X-Request-Id": "private-trace",
                    "Retry-After": "3",
                })
                return result

        broker = self.broker(upstream=Upstream())
        response = broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(response.headers, {
            "content-type": "application/json", "retry-after": "3",
        })

    def test_audited_operation_cannot_replay_after_effect(self):
        broker = self.broker()
        broker.request(self.request(), transport_identity=INSTANCE)
        with self.assertRaisesRegex(Exception, "replay") as caught:
            broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(caught.exception.code, "operation_replay_denied")
        self.assertEqual(len(broker._test_upstream.calls), 1)

    def test_pre_audit_failure_prevents_lease_and_effect(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        audit = CredentialAuditLog(
            sink=lambda _record: (_ for _ in ()).throw(OSError("private")),
        )
        broker = self.broker(audit=audit)
        with self.assertRaises(Exception) as caught:
            broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(caught.exception.code, "audit_unavailable")
        self.assertEqual(broker._test_resolver.issues, [])
        self.assertEqual(broker._test_upstream.calls, [])

    def test_missing_post_audit_is_terminal_indeterminate_and_no_replay(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        appended = []

        def sink(record):
            if appended:
                raise OSError("private")
            appended.append(record)

        broker = self.broker(audit=CredentialAuditLog(sink=sink))
        with self.assertRaises(Exception) as caught:
            broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(caught.exception.code, "operation_indeterminate")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(broker._test_upstream.calls), 1)
        with self.assertRaises(Exception) as replay:
            broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(replay.exception.code, "operation_replay_denied")
        self.assertEqual(len(broker._test_upstream.calls), 1)

    def test_scope_near_misses_refuse_before_resolution_or_upstream(self):
        for field, value in (
            ("scheme", "http"), ("host", "other.example.com"), ("port", 444),
            ("method", "GET"), ("path", "/v1/other"), ("binding_version", 1),
        ):
            with self.subTest(field=field):
                broker = self.broker()
                with self.assertRaisesRegex(Exception, "scope|version"):
                    broker.request(self.request(**{field: value}), transport_identity=INSTANCE)
                self.assertEqual(broker._test_resolver.issues, [])
                self.assertEqual(broker._test_upstream.calls, [])

    def test_local_transport_and_proof_egress_gates_precede_resolution(self):
        for kwargs, expected in (
            ({"transport_identity": "sb-other-instance"}, "transport"),
            ({"proof": False}, "proof"),
            ({"egress": False}, "egress"),
        ):
            with self.subTest(expected=expected):
                broker = self.broker(**{key: value for key, value in kwargs.items()
                                        if key in {"proof", "egress"}})
                transport = kwargs.get("transport_identity", INSTANCE)
                with self.assertRaisesRegex(Exception, expected):
                    broker.request(self.request(), transport_identity=transport)
                self.assertEqual(broker._test_resolver.issues, [])

    def test_proof_admission_cannot_satisfy_egress_gate(self):
        broker = self.broker(proof={"admissible": True}, egress={"admissible": True})
        with self.assertRaisesRegex(Exception, "egress"):
            broker.request(self.request(), transport_identity=INSTANCE)
        self.assertEqual(broker._test_resolver.issues, [])

    def test_guest_auth_headers_duplicates_and_unsupported_fields_refuse(self):
        invalid = (
            {"headers": {"Authorization": "guest-supplied"}},
            {"headers": {"X-Api-Key": "guest-supplied"}},
            {"headers": {"Accept": "a", "accept": "b"}},
            {"unknown": "field"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                broker = self.broker()
                with self.assertRaises(Exception):
                    broker.request(self.request(**changes), transport_identity=INSTANCE)
                self.assertEqual(broker._test_resolver.issues, [])

    def test_request_bounds_and_safe_error_envelope(self):
        broker = self.broker()
        with self.assertRaisesRegex(Exception, "headers"):
            broker.request(self.request(headers={"X-Test": "x" * 70000}),
                           transport_identity=INSTANCE)
        with self.assertRaisesRegex(Exception, "body"):
            broker.request(self.request(body=b"x" * (1024 * 1024 + 1)),
                           transport_identity=INSTANCE)
        envelope = broker.handle(self.request(body=b"x" * (1024 * 1024 + 1)),
                                 transport_identity=INSTANCE)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "request_body_too_large")
        self.assertNotIn("x" * 100, repr(envelope))


if __name__ == "__main__":
    unittest.main()
