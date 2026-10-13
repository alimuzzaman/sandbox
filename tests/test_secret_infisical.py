"""Synthetic regressions for the exact Infisical and HTTPS boundaries (067)."""

from __future__ import annotations

import io
import json
import socket
import ssl
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace
from unittest import mock

from sandbox.secrets.providers.base import (
    Deadline, HttpResponse, MAX_PROVIDER_RESPONSE_BYTES, MAX_PROVIDER_VALUE_BYTES,
    ProviderError,
)
from sandbox.secrets.providers.http import BoundedHTTPTransport
from sandbox.secrets.providers.infisical import InfisicalProvider


CANARY = "SB_SYNTHETIC_PROVIDER_CANARY_067"
POLICY = {
    "endpoint": "https://provider.invalid:8443/", "projectId": "project fixture",
    "environment": "development", "path": "/folder fixture", "key": "API/KEY%é",
    "clientId": "controller-fixture", "timeoutSeconds": 10,
}
LOGIN = {
    "accessToken": "synthetic-access-token", "expiresIn": 300,
    "accessTokenMaxTTL": 600, "tokenType": "Bearer",
}
REMEDIES = {
    "provider_unsupported": "check the registered endpoint and supported provider release",
    "credential_backend_unavailable": "start or reconnect the approved user credential session",
    "credential_locked": "unlock the credential store in the user session, then retry",
    "credential_missing": "place this controller's credential in its approved local store",
    "credential_store_failed": "check the local credential store and user session",
    "endpoint_unreachable": "check private network reachability and the registered endpoint",
    "endpoint_untrusted": "check the endpoint certificate and the operating system trust store",
    "authentication_denied": "check this controller's independently provisioned provider credential",
    "provider_denied": "check the provider identity's project-level read permission",
    "item_unavailable": "check the registered selector and provider project access",
    "provider_rate_limited": "wait before starting a new operation",
    "provider_failed": "check provider availability and its supported API release",
    "response_invalid": "check provider compatibility and the exact registered selector",
    "response_too_large": "reduce the provider response size or use a smaller secret value",
    "operation_timeout": "check private network and provider availability before retrying",
    "capacity_exhausted": "provision a distinct provider identity and controller credential",
}


def response(data, status=200):
    return HttpResponse(status, bytearray(json.dumps(data).encode("utf-8")))


def item(value=CANARY, **changes):
    record = {
        "workspace": POLICY["projectId"], "environment": POLICY["environment"],
        "secretPath": POLICY["path"], "secretKey": POLICY["key"],
        "type": "shared", "secretValue": value,
    }
    record.update(changes)
    return {"secret": record}


class TimedCase(unittest.TestCase):
    def setUp(self):
        self.started = time.monotonic()

    def tearDown(self):
        self.assertLess(time.monotonic() - self.started, 5, "synthetic test exceeded 5 seconds")


class FakeCredential:
    def __init__(self, value=b"synthetic-bootstrap", error=None, callback=None):
        self.value = value
        self.error = error
        self.callback = callback
        self.deadlines = []

    def get_secret(self, deadline):
        self.deadlines.append(deadline)
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return self.value


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.body_refs = []

    def request(self, method, url, *, headers, body, deadline, capture_body=True):
        self.calls.append({
            "method": method, "url": url, "headers": dict(headers),
            "body": bytes(body) if body is not None else None,
            "deadline": deadline, "capture_body": capture_body,
        })
        self.body_refs.append(body)
        if not self.responses:
            raise AssertionError("unexpected extra provider request")
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class InfisicalTests(TimedCase):
    def assert_error(self, expected, transport, *, credential=None, policy=None):
        with self.assertRaises(ProviderError) as raised:
            InfisicalProvider(transport).resolve_exact(
                POLICY if policy is None else policy, credential or FakeCredential(),
            )
        error = raised.exception
        self.assertEqual(error.code, expected)
        self.assertEqual(error.remedy, REMEDIES[expected])
        self.assertFalse(error.retryable)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn(CANARY, repr(error.as_dict()))
        return error

    def test_exact_login_and_encoded_singleton_read_share_one_deadline(self):
        login, read = response(LOGIN), response(item())
        transport = FakeTransport(login, read)
        credential = FakeCredential('synthetic-"bootstrap\\é'.encode())
        result = InfisicalProvider(transport).resolve_exact(POLICY, credential)
        self.assertEqual(result, CANARY.encode())
        self.assertEqual(len(credential.deadlines), 1)
        self.assertEqual(len(transport.calls), 2)
        first, second = transport.calls
        self.assertEqual(first["method"], "POST")
        self.assertEqual(first["url"], "https://provider.invalid:8443/api/v1/auth/universal-auth/login")
        self.assertEqual(first["headers"], {"Accept": "application/json", "Content-Type": "application/json"})
        self.assertEqual(json.loads(first["body"]), {
            "clientId": "controller-fixture", "clientSecret": credential.value.decode(),
        })
        self.assertEqual(second["method"], "GET")
        self.assertEqual(second["url"],
            "https://provider.invalid:8443/api/v4/secrets/API%2FKEY%25%C3%A9?"
            "projectId=project%20fixture&environment=development&secretPath=%2Ffolder%20fixture"
            "&type=shared&expandSecretReferences=false&includeImports=false")
        self.assertEqual(second["headers"], {"Accept": "application/json", "Authorization": "Bearer synthetic-access-token"})
        self.assertIsNone(second["body"])
        self.assertIs(first["deadline"], second["deadline"])
        self.assertIs(first["deadline"], credential.deadlines[0])
        self.assertEqual(transport.body_refs[0], bytearray())
        self.assertEqual((login.body, read.body), (bytearray(), bytearray()))

    def test_each_operation_authenticates_again_without_token_cache(self):
        transport = FakeTransport(response(LOGIN), response(item()), response(LOGIN), response(item()))
        credential = FakeCredential()
        provider = InfisicalProvider(transport)
        provider.resolve_exact(POLICY, credential)
        provider.resolve_exact(POLICY, credential)
        self.assertEqual([call["method"] for call in transport.calls], ["POST", "GET", "POST", "GET"])
        self.assertEqual(len(credential.deadlines), 2)
        self.assertIsNot(credential.deadlines[0], credential.deadlines[1])

    def test_status_is_one_unauthenticated_zero_value_request(self):
        observed = response({"value": CANARY})
        transport = FakeTransport(observed)
        status = InfisicalProvider(transport).status(POLICY)
        self.assertEqual(status.as_dict(), {
            "provider": "infisical", "state": "available", "endpoint_reachable": True,
            "error_code": None,
        })
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual((call["method"], call["url"], call["headers"], call["body"], call["capture_body"]),
            ("GET", "https://provider.invalid:8443/api/status", {}, None, False))
        self.assertEqual(observed.body, bytearray())
        self.assertNotIn(CANARY, repr(status))

    def test_status_failures_are_bounded_without_retry(self):
        cases = [
            (ProviderError("endpoint_unreachable"), "endpoint_unreachable", "unreachable", False),
            (ssl.SSLError(CANARY), "endpoint_untrusted", "untrusted", False),
            (TimeoutError(CANARY), "operation_timeout", "timed_out", False),
            (HttpResponse(404), "provider_unsupported", "unsupported", None),
            (HttpResponse(429), "provider_rate_limited", "unavailable", False),
            (HttpResponse(503), "provider_failed", "unavailable", False),
        ]
        for reply, code, state, reachable in cases:
            with self.subTest(code=code):
                transport = FakeTransport(reply)
                observed = InfisicalProvider(transport).status(POLICY).as_dict()
                self.assertEqual((observed["error_code"], observed["state"], observed["endpoint_reachable"]),
                    (code, state, reachable))
                self.assertEqual(len(transport.calls), 1)
                self.assertNotIn(CANARY, repr(observed))

    def test_login_http_status_map_never_reads_or_retries_an_item(self):
        cases = {401: "authentication_denied", 403: "authentication_denied", 404: "provider_unsupported",
                 429: "provider_rate_limited", 500: "provider_failed", 599: "provider_failed",
                 301: "endpoint_untrusted", 307: "endpoint_untrusted", 400: "provider_unsupported",
                 201: "provider_unsupported", 100: "provider_failed"}
        for status, code in cases.items():
            with self.subTest(status=status):
                reply = response({"error": CANARY}, status)
                transport = FakeTransport(reply)
                self.assert_error(code, transport)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(reply.body, bytearray())
                self.assertEqual(transport.body_refs[0], bytearray())

    def test_exact_read_http_status_map_never_retries_or_falls_back(self):
        cases = {401: "provider_denied", 403: "provider_denied", 404: "item_unavailable",
                 429: "provider_rate_limited", 503: "provider_failed", 302: "endpoint_untrusted",
                 400: "response_invalid", 422: "response_invalid", 204: "provider_unsupported"}
        for status, code in cases.items():
            with self.subTest(status=status):
                reply = response({"error": CANARY}, status)
                transport = FakeTransport(response(LOGIN), reply)
                self.assert_error(code, transport)
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(reply.body, bytearray())

    def test_all_stable_errors_keep_fixed_remedies_and_discard_original_chain(self):
        for code in REMEDIES:
            with self.subTest(code=code):
                error = ProviderError(code)
                error.__cause__ = RuntimeError(CANARY)
                transport = FakeTransport()
                self.assert_error(code, transport, credential=FakeCredential(error=error))
                self.assertEqual(transport.calls, [])

    def test_unknown_errors_are_sanitized_by_failure_stage(self):
        self.assert_error("credential_store_failed", FakeTransport(),
                          credential=FakeCredential(error=RuntimeError(CANARY)))
        transport = FakeTransport(RuntimeError(CANARY))
        self.assert_error("provider_failed", transport)
        self.assertEqual(len(transport.calls), 1)

    def test_transport_exception_maps_are_stable_and_do_not_retry(self):
        cases = [
            (socket.gaierror(CANARY), "endpoint_unreachable"),
            (ConnectionError(CANARY), "endpoint_unreachable"),
            (urllib.error.URLError(socket.gaierror(CANARY)), "endpoint_unreachable"),
            (ssl.CertificateError(CANARY), "endpoint_untrusted"),
            (urllib.error.URLError(ssl.SSLError(CANARY)), "endpoint_untrusted"),
            (socket.timeout(CANARY), "operation_timeout"),
        ]
        for failure, code in cases:
            with self.subTest(code=code, kind=type(failure).__name__):
                transport = FakeTransport(failure)
                self.assert_error(code, transport)
                self.assertEqual(len(transport.calls), 1)

    def test_invalid_registration_is_refused_before_credentials_or_network(self):
        cases = [
            {"endpoint": "http://provider.invalid"}, {"endpoint": "https://user:pass@provider.invalid"},
            {"endpoint": "https://provider.invalid/api"}, {"endpoint": "https://provider.invalid?x=y"},
            {"endpoint": "https://provider.invalid#fragment"}, {"endpoint": "https://provider.invalid:"},
            {"path": "/../moved"}, {"path": "/folder//nested"}, {"path": "/folder/"},
            {"timeoutSeconds": True}, {"timeoutSeconds": 0}, {"timeoutSeconds": 1801},
            {"clientId": "bad\nheader"}, {"key": ""},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                credential, transport = FakeCredential(), FakeTransport()
                self.assert_error("provider_unsupported", transport, credential=credential, policy={**POLICY, **changes})
                self.assertEqual(credential.deadlines, [])
                self.assertEqual(transport.calls, [])

    def test_invalid_or_oversize_bootstrap_never_reaches_login(self):
        for value, code in [(b"", "credential_missing"), ("text", "credential_store_failed"),
                            (b"\xff", "credential_store_failed"),
                            (b"x" * (MAX_PROVIDER_VALUE_BYTES + 1), "credential_store_failed")]:
            with self.subTest(code=code, kind=type(value).__name__):
                transport = FakeTransport()
                self.assert_error(code, transport, credential=FakeCredential(value))
                self.assertEqual(transport.calls, [])

    def test_malformed_json_is_refused_and_response_buffers_are_discarded(self):
        bodies = [b"not-json", b"\xff", b"[]", b'{"accessToken":"one","accessToken":"two"}', b'{"expiresIn":NaN}']
        for body in bodies:
            for stage in ("login", "secret"):
                with self.subTest(stage=stage, body=body):
                    reply = HttpResponse(200, bytearray(body))
                    transport = FakeTransport(reply) if stage == "login" else FakeTransport(response(LOGIN), reply)
                    self.assert_error("response_invalid", transport)
                    self.assertEqual(len(transport.calls), 1 if stage == "login" else 2)
                    self.assertEqual(reply.body, bytearray())

    def test_login_schema_and_access_token_bounds(self):
        cases = [
            ({**LOGIN, "extra": CANARY}, "provider_unsupported"),
            ({k: v for k, v in LOGIN.items() if k != "accessTokenMaxTTL"}, "provider_unsupported"),
            ({**LOGIN, "accessToken": ""}, "response_invalid"),
            ({**LOGIN, "accessToken": "x" * 8193}, "response_invalid"),
            ({**LOGIN, "accessToken": "bad token"}, "response_invalid"),
            ({**LOGIN, "accessToken": "badé"}, "response_invalid"),
            ({**LOGIN, "expiresIn": True}, "response_invalid"),
            ({**LOGIN, "expiresIn": 0}, "response_invalid"),
            ({**LOGIN, "accessTokenMaxTTL": -1}, "response_invalid"),
            ({**LOGIN, "tokenType": "Basic"}, "response_invalid"),
        ]
        for data, code in cases:
            with self.subTest(code=code, fields=tuple(data)):
                transport = FakeTransport(response(data))
                self.assert_error(code, transport)
                self.assertEqual(len(transport.calls), 1)

    def test_exact_item_identity_and_singleton_contract_fail_closed(self):
        cases = [
            ({"secret": None}, "response_invalid"),
            ({"secrets": [item()["secret"]]}, "provider_unsupported"),
            ({**item(), "imports": []}, "provider_unsupported"),
            (item(workspace="another-project"), "response_invalid"),
            (item(environment="production"), "response_invalid"),
            (item(secretPath="/moved"), "response_invalid"),
            (item(secretKey="OTHER_KEY"), "response_invalid"),
            (item(type="personal"), "response_invalid"),
            (item(secretValueHidden=True), "provider_denied"),
            (item(secretValueHidden="false"), "provider_denied"),
            (item(value=None), "response_invalid"),
            (item(value="\ud800"), "response_invalid"),
            (item(value="prefix\x1bsuffix"), "response_invalid"),
            (item(value="prefix\u200bsuffix"), "response_invalid"),
        ]
        for data, code in cases:
            with self.subTest(code=code, fields=tuple(data)):
                transport = FakeTransport(response(LOGIN), response(data))
                self.assert_error(code, transport)
                self.assertEqual(len(transport.calls), 2)

    def test_value_limit_is_bytes_and_empty_value_is_distinct_from_missing(self):
        for value in ("", "line\tvalue\nnext\rline", "x" * MAX_PROVIDER_VALUE_BYTES,
                      "é" * (MAX_PROVIDER_VALUE_BYTES // 2)):
            with self.subTest(size=len(value)):
                transport = FakeTransport(response(LOGIN), response(item(value)))
                self.assertEqual(InfisicalProvider(transport).resolve_exact(POLICY, FakeCredential()), value.encode())
        transport = FakeTransport(response(LOGIN), response(item("é" * (MAX_PROVIDER_VALUE_BYTES // 2 + 1))))
        self.assert_error("response_too_large", transport)
        missing = item()
        del missing["secret"]["secretValue"]
        self.assert_error("response_invalid", FakeTransport(response(LOGIN), response(missing)))

    def test_one_deadline_includes_credential_lookup_and_cleanup(self):
        now = [0.0]
        def deadline(timeout):
            return Deadline(timeout, clock=lambda: now[0])
        with mock.patch("sandbox.secrets.providers.infisical.Deadline", side_effect=deadline):
            transport = FakeTransport()
            credential = FakeCredential(callback=lambda: now.__setitem__(0, 10.0))
            self.assert_error("operation_timeout", transport, credential=credential)
            self.assertEqual(transport.calls, [])

        class SlowCleanup(HttpResponse):
            def discard(self):
                super().discard()
                now[0] = 10.0
        now[0] = 0.0
        read = SlowCleanup(200, bytearray(json.dumps(item()).encode()))
        with mock.patch("sandbox.secrets.providers.infisical.Deadline", side_effect=deadline):
            transport = FakeTransport(response(LOGIN), read)
            self.assert_error("operation_timeout", transport)
            self.assertEqual(read.body, bytearray())
            self.assertEqual(len(transport.calls), 2)


class FakeResponse:
    def __init__(self, body=b"", status=200, read_error=None):
        self.status = status
        self.stream = io.BytesIO(body)
        self.reads = []
        self.closed = False
        self.read_error = read_error
        self.socket = mock.Mock()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))

    def read(self, size):
        self.reads.append(size)
        if self.read_error:
            raise self.read_error
        return self.stream.read(size)

    def close(self):
        self.closed = True


class HTTPSBoundaryTests(TimedCase):
    def transport(self, reply):
        context = SimpleNamespace(check_hostname=False, verify_mode=ssl.CERT_NONE)
        opener = mock.Mock()
        if isinstance(reply, BaseException):
            opener.open.side_effect = reply
        else:
            opener.open.return_value = reply
        with mock.patch("sandbox.secrets.providers.http.ssl.create_default_context", return_value=context) as trust, \
             mock.patch("sandbox.secrets.providers.http.urllib.request.build_opener", return_value=opener) as build:
            transport = BoundedHTTPTransport()
        trust.assert_called_once_with(purpose=ssl.Purpose.SERVER_AUTH)
        return transport, opener, context, build.call_args.args

    def request(self, transport, *, deadline=None, capture_body=True):
        return transport.request("GET", "https://provider.invalid/api/status", headers={}, body=None,
            deadline=deadline or Deadline(10, clock=lambda: 0.0), capture_body=capture_body)

    def test_os_trust_hostname_verification_no_proxy_and_no_redirect_are_installed(self):
        transport, opener, context, handlers = self.transport(FakeResponse())
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        proxy, redirect, https = handlers
        self.assertIsInstance(proxy, urllib.request.ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsNone(redirect.redirect_request(None, None, 302, "redirect", {}, "https://other.invalid"))
        self.assertIs(https._context, context)
        self.assertTrue(https._context.check_hostname)
        self.request(transport)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)

    def test_status_does_not_read_any_response_bytes(self):
        reply = FakeResponse(CANARY.encode(), read_error=AssertionError("status body read"))
        transport, opener, _, _ = self.transport(reply)
        observed = self.request(transport, capture_body=False)
        self.assertEqual((observed.status, observed.body), (200, bytearray()))
        self.assertEqual(reply.reads, [])
        self.assertTrue(reply.closed)
        self.assertEqual(opener.open.call_count, 1)

    def test_body_streaming_caps_each_read_and_accepts_exact_response_limit(self):
        reply = FakeResponse(b"x" * MAX_PROVIDER_RESPONSE_BYTES)
        transport, _, _, _ = self.transport(reply)
        observed = self.request(transport)
        self.assertEqual(len(observed.body), MAX_PROVIDER_RESPONSE_BYTES)
        self.assertTrue(all(0 < size <= 65536 for size in reply.reads))
        self.assertEqual(reply.reads[-1], 1)
        self.assertEqual(reply.socket.settimeout.call_count, len(reply.reads))
        self.assertTrue(reply.closed)

    def test_oversize_stream_is_bounded_closed_and_never_retried(self):
        reply = FakeResponse(b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1))
        transport, opener, _, _ = self.transport(reply)
        with self.assertRaises(ProviderError) as raised:
            self.request(transport)
        self.assertEqual(raised.exception.code, "response_too_large")
        self.assertEqual(reply.stream.tell(), MAX_PROVIDER_RESPONSE_BYTES + 1)
        self.assertTrue(reply.closed)
        self.assertEqual(opener.open.call_count, 1)

    def test_http_errors_and_redirects_discard_bodies_without_following(self):
        for status in (302, 307, 401, 403, 404, 429, 503):
            with self.subTest(status=status):
                body = FakeResponse(CANARY.encode(), read_error=AssertionError("error body read"))
                error = urllib.error.HTTPError("https://provider.invalid/api/status", status, CANARY, {}, body)
                transport, opener, _, _ = self.transport(error)
                if 300 <= status < 400:
                    with self.assertRaises(ProviderError) as raised:
                        self.request(transport)
                    self.assertEqual(raised.exception.code, "endpoint_untrusted")
                else:
                    result = self.request(transport)
                    self.assertEqual((result.status, result.body), (status, bytearray()))
                self.assertEqual(body.reads, [])
                self.assertTrue(body.closed)
                self.assertEqual(opener.open.call_count, 1)

    def test_transport_failures_have_safe_fixed_outcomes_and_no_retry(self):
        cases = [(ssl.SSLError(CANARY), "endpoint_untrusted"),
                 (urllib.error.URLError(ssl.CertificateError(CANARY)), "endpoint_untrusted"),
                 (socket.gaierror(CANARY), "endpoint_unreachable"),
                 (ConnectionError(CANARY), "endpoint_unreachable"),
                 (TimeoutError(CANARY), "operation_timeout"),
                 (RuntimeError(CANARY), "provider_failed")]
        for failure, code in cases:
            with self.subTest(code=code, kind=type(failure).__name__):
                transport, opener, _, _ = self.transport(failure)
                with self.assertRaises(ProviderError) as raised:
                    self.request(transport)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.remedy, REMEDIES[code])
                self.assertFalse(raised.exception.retryable)
                self.assertNotIn(CANARY, str(raised.exception))
                self.assertIsNone(raised.exception.__context__)
                self.assertEqual(opener.open.call_count, 1)

    def test_timeout_midstream_closes_response_and_discards_partial_data(self):
        reply = FakeResponse(read_error=socket.timeout(CANARY))
        transport, opener, _, _ = self.transport(reply)
        with self.assertRaises(ProviderError) as raised:
            self.request(transport)
        self.assertEqual(raised.exception.code, "operation_timeout")
        self.assertTrue(reply.closed)
        self.assertEqual(opener.open.call_count, 1)

    def test_expired_deadline_prevents_network_open(self):
        now = [0.0]
        deadline = Deadline(1, clock=lambda: now[0])
        now[0] = 1.0
        transport, opener, _, _ = self.transport(FakeResponse())
        with self.assertRaises(ProviderError) as raised:
            self.request(transport, deadline=deadline)
        self.assertEqual(raised.exception.code, "operation_timeout")
        opener.open.assert_not_called()

    def test_transport_refuses_untrusted_origins_routes_and_headers_before_open(self):
        cases = [
            ("GET", "http://provider.invalid/api/status", {}, None, "endpoint_untrusted"),
            ("GET", "https://u:p@provider.invalid/api/status", {}, None, "endpoint_untrusted"),
            ("GET", "https://provider.invalid/api/status#fragment", {}, None, "endpoint_untrusted"),
            ("GET", "https://provider.invalid/api/v4/secrets", {}, None, "provider_unsupported"),
            ("POST", "https://provider.invalid/api/status", {}, b"x", "provider_unsupported"),
            ("GET", "https://provider.invalid/api/status", {"Authorization": "Bearer x"}, None, "provider_unsupported"),
            ("POST", "https://provider.invalid/api/v1/auth/universal-auth/login", {"Authorization": "Bearer x"}, b"{}", "provider_unsupported"),
            ("GET", "https://provider.invalid/api/v4/secrets/KEY?projectId=fixture", {}, None, "provider_unsupported"),
            ("GET", "https://provider.invalid/api/v4/secrets/KEY?projectId=fixture", {"Authorization": "bad\r\nheader"}, None, "provider_unsupported"),
        ]
        for method, url, headers, body, code in cases:
            with self.subTest(method=method, url=url):
                transport, opener, _, _ = self.transport(FakeResponse())
                with self.assertRaises(ProviderError) as raised:
                    transport.request(method, url, headers=headers, body=body, deadline=Deadline(10, clock=lambda: 0))
                self.assertEqual(raised.exception.code, code)
                opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
