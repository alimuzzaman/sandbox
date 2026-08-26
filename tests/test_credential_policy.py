"""Binding-to-egress intersection tests."""

from datetime import datetime, timezone
import unittest


INSTANCE = "sb-0123456789ab"
OWNER = INSTANCE


def _grant_set(*, kind="hostname_https", destinations=("api.example.com",), ports=(443,),
               owner=OWNER, expires_at="2999-01-01T00:00:00Z"):
    from sandbox.isolation.models import EgressGrant, EgressGrantSet

    grant = EgressGrant("credential-host", owner, kind, destinations, ports, expires_at)
    return EgressGrantSet(INSTANCE, "a" * 64, (grant,))


def _binding(grants, **overrides):
    from sandbox.isolation.credential_binding import CredentialBinding

    values = {
        "binding_id": "bind-policy-1", "instance_id": INSTANCE,
        "source_reference": "fixture/API_TOKEN", "policy_digest": "a" * 64,
        "egress_digest": grants.digest, "broker_digest": "c" * 64,
        "scheme": "https", "host": "api.example.com", "port": 443,
        "method": "POST", "path": "/v1/items", "auth_form": "bearer",
        "expires_at": "2999-01-01T00:00:00Z", "owner": OWNER,
    }
    values.update(overrides)
    return CredentialBinding(**values).transition("ready")


class TestCredentialPolicy(unittest.TestCase):
    def test_exact_hostname_grant_authorizes_only_matching_binding(self):
        from sandbox.isolation.credential_policy import evaluate_binding_egress

        grants = _grant_set()
        decision = evaluate_binding_egress(_binding(grants), grants)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, "authorized")

        other = _binding(grants, host="other.example.com")
        self.assertFalse(evaluate_binding_egress(other, grants).allowed)

    def test_digest_owner_policy_and_expiry_are_required(self):
        from sandbox.isolation.credential_policy import evaluate_binding_egress

        grants = _grant_set()
        for binding in (
            _binding(grants, egress_digest="b" * 64),
            _binding(grants, instance_id="sb-abcdef012345"),
        ):
            with self.subTest(binding=binding):
                self.assertFalse(evaluate_binding_egress(binding, grants).allowed)
        expired = _grant_set(expires_at="2000-01-01T00:00:00Z")
        # EgressGrantSet itself permits an expired record; the policy must not.
        self.assertFalse(evaluate_binding_egress(_binding(expired), expired).allowed)

    def test_public_cidr_grant_requires_a_resolved_public_address_in_the_cidr(self):
        from sandbox.isolation.credential_policy import evaluate_binding_egress

        grants = _grant_set(kind="public_cidr_tcp", destinations=("93.184.216.0/24",))
        binding = _binding(grants)
        self.assertTrue(evaluate_binding_egress(
            binding, grants, resolved_addresses=("93.184.216.34",)).allowed)
        for addresses in ((), ("198.51.100.10",), ("127.0.0.1",)):
            with self.subTest(addresses=addresses):
                self.assertFalse(evaluate_binding_egress(
                    binding, grants, resolved_addresses=addresses).allowed)

    def test_default_deny_and_helper_boolean_surface(self):
        from sandbox.isolation.credential_policy import binding_egress_allowed

        grants = _grant_set()
        binding = _binding(grants)
        self.assertFalse(binding_egress_allowed(binding, ()))
        self.assertTrue(binding_egress_allowed(binding, grants))


if __name__ == "__main__":
    unittest.main()
