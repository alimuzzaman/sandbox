"""Supervisor and lease-transfer lifecycle tests."""

import unittest

from tests.test_credential_broker_contract import (
    INSTANCE, OWNER, SYNTHETIC_VALUE, FakeResolver, FakeUpstream, _binding,
)


class TestCredentialSupervisor(unittest.TestCase):
    def broker(self):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker

        binding = _binding()
        resolver = FakeResolver()
        broker = CredentialRequestBroker(
            INSTANCE, resolver, lambda _binding_id: binding,
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=FakeUpstream(), owner=OWNER,
        )
        return broker, resolver, binding

    class LeaseAdapter:
        def __init__(self, lease, binding):
            self._lease = lease
            self.binding_id = binding.binding_id
            self.binding_version = binding.version

        def consume(self, consumer):
            return self._lease.consume(consumer)

        def invalidate(self):
            self._lease.consumed = True

    def test_transfer_is_instance_bound_and_one_use_without_plaintext_state(self):
        from sandbox.isolation.credential_supervisor import (
            CredentialBrokerSupervisor, CredentialSupervisorError,
        )

        broker, resolver, binding = self.broker()
        supervisor = CredentialBrokerSupervisor(broker)
        transfer = supervisor.transfer(self.LeaseAdapter(resolver.issue(binding), binding))
        observed = []
        result = transfer.consume(lambda value: (observed.append(value), {"ok": True})[1])
        self.assertEqual(result, {"ok": True})
        self.assertEqual(observed, [SYNTHETIC_VALUE])
        with self.assertRaises(Exception):
            transfer.consume(lambda value: value)
        self.assertNotIn(SYNTHETIC_VALUE.decode(), repr(transfer))
        with self.assertRaises(CredentialSupervisorError):
            supervisor.transfer(self.LeaseAdapter(resolver.issue(binding), binding), instance_id="sb-other")
        supervisor.shutdown()

    def test_revoke_closes_binding_before_drain_and_shutdown_is_idempotent(self):
        from sandbox.isolation.credential_supervisor import CredentialBrokerSupervisor

        broker, resolver, binding = self.broker()
        supervisor = CredentialBrokerSupervisor(broker)
        resolver.issue(binding)  # broker.close_binding invalidates outstanding leases
        result = supervisor.revoke_binding(binding.binding_id, binding_version=binding.version)
        self.assertTrue(result["drained"])
        with self.assertRaisesRegex(Exception, "admission is closed|binding"):
            supervisor.request(
                {"binding_id": binding.binding_id, "binding_version": binding.version,
                 "scheme": "https", "host": binding.host, "port": 443,
                 "method": binding.method, "path": binding.path},
                transport_identity=INSTANCE,
            )
        closed = supervisor.shutdown()
        self.assertEqual(closed["state"], "closed")
        self.assertTrue(supervisor.shutdown()["ok"])


if __name__ == "__main__":
    unittest.main()
