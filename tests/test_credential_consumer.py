"""Explicit first-consumer contract tests."""

import unittest

from tests.test_credential_broker_contract import INSTANCE, OWNER, FakeResolver, FakeUpstream, _binding
from tests.credential_consumer_v1_fake import LocalV1CredentialConsumer


class TestCredentialConsumer(unittest.TestCase):
    def consumer(self):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker
        binding = _binding()
        broker = CredentialRequestBroker(
            INSTANCE, FakeResolver(), lambda _id: binding,
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=FakeUpstream(), owner=OWNER,
        )
        return LocalV1CredentialConsumer(broker, instance_id=INSTANCE), binding

    def test_consumer_constructs_exact_scope_and_returns_only_broker_envelope(self):
        consumer, binding = self.consumer()
        result = consumer.request(
            binding, body=b"{}", headers={"Accept": "application/json"},
            content_type="application/json", correlation_id="corr-consumer-1",
        )
        self.assertTrue(result["ok"])
        self.assertNotIn("source_reference", repr(result))
        self.assertNotIn("SB_SYNTHETIC", repr(result))

    def test_consumer_cannot_select_auth_header_or_widen_scope(self):
        consumer, binding = self.consumer()
        for headers in ({"Authorization": "guest"}, {"X-Api-Key": "guest"}):
            with self.subTest(headers=headers), self.assertRaises(Exception):
                consumer.request(binding, headers=headers)
        with self.assertRaises(Exception):
            consumer.request(binding, body=b"x" * (1024 * 1024 + 1))

    def test_consumer_requires_a_complete_managed_instance_identity(self):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker
        broker = CredentialRequestBroker(
            INSTANCE, FakeResolver(), lambda _id: _binding(),
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=FakeUpstream(), owner=OWNER,
        )
        with self.assertRaises(ValueError):
            LocalV1CredentialConsumer(broker, instance_id="sb-")

    def test_production_v1_consumer_is_fixed_closed(self):
        from sandbox.runtimes.managed.credential_consumer import ExplicitCredentialConsumer
        with self.assertRaisesRegex(ValueError, "credential_consumer_v1_disabled"):
            ExplicitCredentialConsumer(object(), instance_id=INSTANCE)


if __name__ == "__main__":
    unittest.main()
