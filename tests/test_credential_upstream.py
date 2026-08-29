"""Verified HTTPS upstream tests using an injected transport seam."""

from types import SimpleNamespace
import unittest


def _binding(auth_form="bearer"):
    from sandbox.isolation.credential_binding import CredentialBinding

    return CredentialBinding(
        "bind-upstream-1", "sb-0123456789ab", "fixture/API_TOKEN",
        "a" * 64, "b" * 64, "c" * 64, "https", "api.example.com", 443,
        "POST", "/v1/items", auth_form, "2999-01-01T00:00:00Z", "project:fixture",
    ).transition("ready")


def _request(**overrides):
    values = {
        "host": "api.example.com", "port": 443, "method": "POST", "path": "/v1/items",
        "headers": {"accept": "application/json"}, "body": b"{}",
        "content_type": "application/json", "deadline_ms": 5000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Transport:
    def __init__(self, result=None):
        self.result = result or {
            "status": 200, "headers": {"content-type": "application/json"},
            "body": b'{"ok":true}',
        }
        self.calls = []

    def request(self, method, path, headers, body, timeout):
        self.calls.append((method, path, dict(headers), body, timeout))
        return self.result


class TestCredentialUpstream(unittest.TestCase):
    def upstream(self, transport, *, resolver=None, **kwargs):
        from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream

        seen = {}

        def connector(address, host, port, timeout, context):
            seen.update({"address": address, "host": host, "port": port, "timeout": timeout,
                         "context": context})
            return transport

        value = VerifiedHttpsUpstream(
            resolver=resolver or (lambda _host: ("93.184.216.34",)),
            connector=connector,
            **kwargs,
        )
        value._test_seen = seen
        return value

    def test_pins_public_dns_and_applies_only_registered_auth_profile(self):
        upstream = self.upstream(Transport())
        result = upstream.request(_binding("bearer"), _request(), b"SB_SYNTHETIC_VALUE")
        self.assertEqual(result["status"], 200)
        self.assertEqual(upstream._test_seen["address"], "93.184.216.34")
        call = upstream._test_seen  # transport is captured below through connector seam
        self.assertEqual(call["host"], "api.example.com")

        # Re-run with a transport reference so the exact generated headers are observable.
        transport = Transport()
        upstream = self.upstream(transport)
        upstream.request(_binding("bearer"), _request(), b"SB_SYNTHETIC_VALUE")
        headers = transport.calls[0][2]
        self.assertEqual(headers["authorization"], "Bearer SB_SYNTHETIC_VALUE")
        self.assertEqual(headers["host"], "api.example.com")
        self.assertEqual(headers["content-length"], "2")
        self.assertNotIn("x-api-key", headers)

        transport = Transport()
        self.upstream(transport).request(_binding("api_key"), _request(), b"SB_SYNTHETIC_VALUE")
        self.assertEqual(transport.calls[0][2]["x-api-key"], "SB_SYNTHETIC_VALUE")
        self.assertNotIn("authorization", transport.calls[0][2])

    def test_dns_pin_and_credential_shape_fail_closed(self):
        from sandbox.isolation.credential_upstream import CredentialUpstreamError

        for resolver in (
            lambda _host: ("127.0.0.1",),
            lambda _host: (),
            lambda _host: "93.184.216.34",
        ):
            with self.subTest(resolver=resolver):
                upstream = self.upstream(Transport(), resolver=resolver)
                with self.assertRaises(CredentialUpstreamError):
                    upstream.request(_binding(), _request(), b"SB_SYNTHETIC_VALUE")
        upstream = self.upstream(Transport())
        with self.assertRaisesRegex(CredentialUpstreamError, "credential"):
            upstream.request(_binding(), _request(), b"bad\nvalue")

    def test_redirect_and_response_limits_are_not_followed_or_buffered(self):
        from sandbox.isolation.credential_upstream import CredentialUpstreamError

        redirect = self.upstream(Transport({"status": 302, "headers": {"location": "https://other.example"}, "body": b""}))
        with self.assertRaisesRegex(CredentialUpstreamError, "redirect"):
            redirect.request(_binding(), _request(), b"SB_SYNTHETIC_VALUE")
        oversized = self.upstream(Transport({"status": 200, "headers": {}, "body": b"x" * (4 * 1024 * 1024 + 1)}))
        with self.assertRaisesRegex(CredentialUpstreamError, "response"):
            oversized.request(_binding(), _request(), b"SB_SYNTHETIC_VALUE")

    def test_response_headers_use_an_exact_allowlist(self):
        transport = Transport({
            "status": 200,
            "headers": {
                "content-type": "application/json", "retry-after": "2",
                "set-cookie": "session=not-returned",
                "location": "https://other.example/", "x-trace": "private",
            },
            "body": b"{}",
        })
        result = self.upstream(transport).request(
            _binding(), _request(), b"SB_SYNTHETIC_VALUE",
        )
        self.assertEqual(result["headers"], {
            "content-type": "application/json", "retry-after": "2",
        })

    def test_transport_failure_after_send_begins_is_terminal_indeterminate(self):
        from sandbox.isolation.credential_upstream import CredentialUpstreamError

        class FailingTransport(Transport):
            def request(self, method, path, headers, body, timeout):
                self.calls.append((method, path, dict(headers), body, timeout))
                raise TimeoutError("private")

        transport = FailingTransport()
        with self.assertRaises(CredentialUpstreamError) as caught:
            self.upstream(transport).request(
                _binding(), _request(), b"SB_SYNTHETIC_VALUE",
            )
        self.assertEqual(caught.exception.code, "upstream_indeterminate")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(transport.calls), 1)

    def test_binding_and_request_destination_must_match(self):
        from sandbox.isolation.credential_upstream import CredentialUpstreamError

        upstream = self.upstream(Transport())
        with self.assertRaisesRegex(CredentialUpstreamError, "destination"):
            upstream.request(_binding(), _request(host="other.example.com"), b"SB_SYNTHETIC_VALUE")


if __name__ == "__main__":
    unittest.main()
