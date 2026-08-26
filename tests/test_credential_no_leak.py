"""Contract-level no-leak probes for every caller-visible local surface."""

import json
import os
import unittest

from tests.test_credential_broker_contract import (
    INSTANCE, OWNER, SYNTHETIC_VALUE, FakeResolver, _binding,
)


class TestCredentialNoLeak(unittest.TestCase):
    def test_metadata_repr_status_and_audit_surfaces_exclude_credential_material(self):
        from sandbox.isolation.capability_report import BindingState
        from sandbox.isolation.credential_audit import CredentialAuditLog
        from sandbox.isolation.credential_request_broker import BrokerRequest, BrokerResponse
        from sandbox.isolation.credential_supervisor import CredentialBrokerSupervisor

        binding = _binding()
        status = BindingState(binding.binding_id, binding.version, binding.scope(),
                              binding.state, binding.expires_at)
        request = BrokerRequest.from_mapping({
            "binding_id": binding.binding_id, "binding_version": binding.version,
            "scheme": binding.scheme, "host": binding.host, "port": binding.port,
            "method": binding.method, "path": binding.path, "body": b"{}",
        })
        response = BrokerResponse(200, {}, b"ok", request.correlation_id)
        records = []
        audit = CredentialAuditLog(sink=records.append)
        audit.record(operation="request", instance_id=INSTANCE, binding_id=binding.binding_id,
                     actor=OWNER, decision="allow", reason_code="authorized", state="ready")
        broker = __import__("sandbox.isolation.credential_request_broker",
                            fromlist=["CredentialRequestBroker"]).CredentialRequestBroker(
            INSTANCE, FakeResolver(), lambda _id: binding,
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=lambda _binding, _request, _credential: {"status": 200, "body": b"ok"},
            owner=OWNER,
        )
        supervisor = CredentialBrokerSupervisor(broker)
        surface = json.dumps({
            "binding": binding.to_dict(), "status": status.to_dict(),
            "request": repr(request), "response": repr(response),
            "audit": records, "supervisor": repr(supervisor),
        }, sort_keys=True)
        self.assertNotIn(SYNTHETIC_VALUE.decode(), surface)
        supervisor.shutdown()

    def test_response_body_reflection_is_redacted_defensively(self):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker

        binding = _binding()
        broker = CredentialRequestBroker(
            INSTANCE, FakeResolver(), lambda _id: binding,
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=lambda _binding, _request, credential: {
                "status": 200, "headers": {"x-reflected": credential.decode()},
                "body": b"prefix:" + credential + b":suffix",
            }, owner=OWNER,
        )
        result = broker.handle({
            "binding_id": binding.binding_id, "binding_version": binding.version,
            "scheme": "https", "host": binding.host, "port": 443,
            "method": binding.method, "path": binding.path,
        }, transport_identity=INSTANCE)
        self.assertTrue(result["ok"])
        encoded = repr(result)
        self.assertNotIn(SYNTHETIC_VALUE.decode(), encoded)
        self.assertIn("<redacted>", encoded)

    def test_environment_and_argv_are_not_used_as_credential_channels(self):
        # The local explicit request contract does not inspect or mutate these
        # process surfaces. This probe is deliberately content-only and cannot
        # stand in for authorized guest isolation evidence.
        self.assertNotIn("SB_SYNTHETIC_VALUE", os.environ)
        self.assertNotIn("SB_SYNTHETIC_VALUE", " ".join(os.sys.argv))


if __name__ == "__main__":
    unittest.main()
