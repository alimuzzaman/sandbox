"""Request/response, concurrency, timeout, and cancellation bounds."""

import threading
import time
import unittest

from tests.test_credential_broker_contract import (
    INSTANCE, OWNER, FakeResolver, _binding,
)


def _request(binding, **changes):
    value = {
        "binding_id": binding.binding_id, "binding_version": binding.version,
        "scheme": binding.scheme, "host": binding.host, "port": binding.port,
        "method": binding.method, "path": binding.path,
        "headers": {}, "body": b"{}", "deadline_ms": 5000,
    }
    value.update(changes)
    return value


class TestCredentialBrokerBounds(unittest.TestCase):
    def broker(self, upstream, *, max_concurrent=16):
        from sandbox.isolation.credential_request_broker import CredentialRequestBroker

        binding = _binding()
        broker = CredentialRequestBroker(
            INSTANCE, FakeResolver(), lambda _id: binding,
            proof=lambda _binding: True, egress=lambda _binding: True,
            upstream=upstream, owner=OWNER, max_concurrent=max_concurrent,
        )
        return broker, binding

    def test_post_effect_response_failures_are_indeterminate_and_method_is_denied(self):
        from sandbox.isolation.credential_request_broker import CredentialBrokerError

        for response in (
            {"status": 302, "headers": {"location": "https://other"}, "body": b""},
            {"status": 200, "headers": {}, "body": b"x" * (4 * 1024 * 1024 + 1)},
        ):
            broker, binding = self.broker(lambda *_args, response=response: response)
            result = broker.handle(_request(binding), transport_identity=INSTANCE)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"],
                             "operation_indeterminate")
            self.assertFalse(result["error"]["retryable"])
        broker, binding = self.broker(lambda *_args: {"status": 200, "body": b"ok"})
        with self.assertRaises(CredentialBrokerError):
            broker.request(_request(binding, method="CONNECT"), transport_identity=INSTANCE)

    def test_concurrency_limit_rejects_before_resolution_and_close_stops_new_admission(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def upstream(_binding, _request, _credential):
            calls.append("entered")
            entered.set()
            release.wait(2)
            return {"status": 200, "body": b"ok"}

        broker, binding = self.broker(upstream, max_concurrent=1)
        first = []
        worker = threading.Thread(target=lambda: first.append(
            broker.request(_request(binding), transport_identity=INSTANCE)))
        worker.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaisesRegex(Exception, "concurrency"):
            broker.request(_request(binding, correlation_id="corr-second"), transport_identity=INSTANCE)
        broker.close()
        with self.assertRaisesRegex(Exception, "closed"):
            broker.request(_request(binding, correlation_id="corr-third"), transport_identity=INSTANCE)
        release.set()
        worker.join(2)
        self.assertEqual(len(first), 1)
        self.assertEqual(calls, ["entered"])

    def test_post_send_timeout_is_terminal_indeterminate_and_redacted(self):
        broker, binding = self.broker(lambda *_args: (_ for _ in ()).throw(TimeoutError("SECRET_TIMEOUT")))
        result = broker.handle(_request(binding), transport_identity=INSTANCE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "operation_indeterminate")
        self.assertFalse(result["error"]["retryable"])
        self.assertNotIn("SECRET_TIMEOUT", repr(result))

        from sandbox.isolation.credential_upstream import CredentialUpstreamError

        broker, binding = self.broker(lambda *_args: (_ for _ in ()).throw(
            CredentialUpstreamError("upstream_timeout", "upstream timed out", retryable=True),
        ))
        result = broker.handle(_request(binding), transport_identity=INSTANCE)
        self.assertEqual(result["error"]["code"], "operation_indeterminate")
        self.assertFalse(result["error"]["retryable"])


if __name__ == "__main__":
    unittest.main()
