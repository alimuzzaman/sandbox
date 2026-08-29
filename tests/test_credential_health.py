"""Pre-start and periodic proof-drift health gates."""

from datetime import datetime, timezone
import unittest

from tests.test_credential_broker_contract import INSTANCE, OWNER, _binding


class FakeSupervisor:
    def __init__(self):
        self.closed = []

    def revoke_binding(self, binding_id, *, binding_version=None, timeout_seconds=None):
        self.closed.append((binding_id, binding_version))
        return {"ok": True, "state": "revoked", "drained": True, "mutated": True}


class TestCredentialHealth(unittest.TestCase):
    def test_pre_start_requires_matching_digests_and_admissible_proof(self):
        from sandbox.isolation.credential_health import CredentialHealthMonitor

        binding = _binding()
        supervisor = FakeSupervisor()
        monitor = CredentialHealthMonitor(
            supervisor=supervisor, binding_loader=lambda _id: binding,
            proof=lambda _binding: {"admissible": True},
            egress=lambda _binding: {"allowed": True},
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        ready = monitor.pre_start(
            binding.binding_id, policy_digest=binding.policy_digest,
            egress_digest=binding.egress_digest, broker_digest=binding.broker_digest,
        )
        self.assertTrue(ready["ok"])
        blocked = monitor.pre_start(
            binding.binding_id, policy_digest="d" * 64,
            egress_digest=binding.egress_digest, broker_digest=binding.broker_digest,
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"]["code"], "policy_digest_mismatch")
        self.assertEqual(supervisor.closed[-1], (binding.binding_id, binding.version))

    def test_periodic_check_is_bounded_and_closes_on_proof_drift(self):
        from sandbox.isolation.credential_health import CredentialHealthMonitor

        binding = _binding()
        supervisor = FakeSupervisor()
        proof = [{"admissible": True}, {"admissible": False}]
        now = [0.0]
        monitor = CredentialHealthMonitor(
            supervisor=supervisor, binding_loader=lambda _id: binding,
            proof=lambda _binding: proof.pop(0), egress=lambda _binding: True,
            clock=lambda: now[0], interval_seconds=10,
        )
        kwargs = {"policy_digest": binding.policy_digest, "egress_digest": binding.egress_digest,
                  "broker_digest": binding.broker_digest}
        self.assertTrue(monitor.periodic(binding.binding_id, **kwargs)["ok"])
        self.assertEqual(monitor.periodic(binding.binding_id, **kwargs)["state"], "skipped")
        now[0] = 11
        result = monitor.periodic(binding.binding_id, **kwargs)
        self.assertFalse(result["ok"])
        self.assertEqual(supervisor.closed, [(binding.binding_id, binding.version)])


if __name__ == "__main__":
    unittest.main()
