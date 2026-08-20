import os
import socket
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from sandbox.services.http import UrlHttpProbe
from sandbox.services.ports import SocketPortAllocator


class TestPortAllocator(unittest.TestCase):
    def test_allocator_is_loopback_only_and_validates_preferred_port(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            SocketPortAllocator("0.0.0.0")
        allocator = SocketPortAllocator()
        for preferred in (0, 65536, True, "8080"):
            with self.subTest(preferred=preferred), self.assertRaises(ValueError):
                allocator.reserve(preferred)

    def test_allocates_and_rejects_collision(self):
        allocator = SocketPortAllocator()
        port = allocator.allocate()
        sock = socket.socket()
        sock.bind(("127.0.0.1", port))
        try:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                allocator.allocate(port)
        finally:
            sock.close()

    def test_reservation_keeps_port_unavailable_until_context_exit(self):
        allocator = SocketPortAllocator()
        with allocator.reserve() as reservation:
            self.assertGreater(reservation.port, 0)
            with closing(socket.socket()) as contender:
                with self.assertRaises(OSError):
                    contender.bind(("127.0.0.1", reservation.port))
        with closing(socket.socket()) as released:
            released.bind(("127.0.0.1", reservation.port))


class TestHttpProbe(unittest.TestCase):
    def test_selected_route_diagnostic_classifies_transport_and_http_failures(self):
        class FakeResponse:
            def __init__(self, status):
                self.status = status

            def close(self):
                pass

        class FakeConnection:
            mode = "ready"

            def __init__(self, address, port, *, timeout):
                self.address = address
                self.port = port
                self.timeout = timeout

            def connect(self):
                if self.mode == "refused":
                    raise ConnectionRefusedError("secret endpoint")
                if self.mode == "connect_timeout":
                    raise socket.timeout("secret timeout")

            def request(self, method, path, *, headers):
                self.request_args = (method, path, headers)
                if self.mode == "response_timeout":
                    raise socket.timeout("secret response")
                if self.mode == "protocol_error":
                    from http.client import BadStatusLine
                    raise BadStatusLine("secret protocol")

            def getresponse(self):
                return FakeResponse(503 if self.mode == "unhealthy" else 204)

            def close(self):
                pass

        expected = {
            "refused": ("unreachable", "not_attempted", "ingress_listener_unreachable"),
            "connect_timeout": ("timed_out", "not_attempted", "ingress_connect_timeout"),
            "response_timeout": ("reachable", "timed_out", "application_response_timeout"),
            "unhealthy": ("reachable", "http_unhealthy", "application_http_unhealthy"),
            "protocol_error": ("reachable", "protocol_error", "ingress_probe_unavailable"),
            "ready": ("reachable", "ready", "ready"),
        }
        with patch("sandbox.services.http.http.client.HTTPConnection", FakeConnection):
            for mode, (ingress, application, reason) in expected.items():
                with self.subTest(mode=mode):
                    FakeConnection.mode = mode
                    result = UrlHttpProbe().probe_route_diagnostic(
                        "127.0.0.1", 18080, "unresolvable.test", timeout=1,
                    )
                    self.assertEqual(result, {
                        "ingress": ingress, "application": application,
                        "reason": reason,
                    })
                    self.assertNotIn("secret", str(result))

    def test_diagnostic_route_uses_no_proxy_and_does_not_follow_redirects(self):
        seen = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                if self.path == "/":
                    self.send_response(302)
                    self.send_header("Location", "/final")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        old_proxy = os.environ.get("http_proxy")
        old_proxy_upper = os.environ.get("HTTP_PROXY")
        os.environ["http_proxy"] = "http://127.0.0.1:1"
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
        try:
            probe = UrlHttpProbe()
            # The selected-route operation must not delegate to the legacy
            # boolean wrapper.  Its direct HTTPConnection path is equivalent
            # to an empty ProxyHandler plus a no-redirect opener.
            with patch.object(probe, "probe_route", side_effect=AssertionError):
                result = probe.probe_route_diagnostic(
                    "127.0.0.1", server.server_port, "unresolvable.test", timeout=1,
                )
            self.assertEqual(result, {
                "ingress": "reachable", "application": "ready", "reason": "ready",
            })
            self.assertEqual(seen, ["/"])
        finally:
            if old_proxy is None:
                os.environ.pop("http_proxy", None)
            else:
                os.environ["http_proxy"] = old_proxy
            if old_proxy_upper is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = old_proxy_upper
            server.shutdown()
            worker.join()
            server.server_close()

    def test_exact_route_probe_bypasses_dns_and_sends_explicit_host(self):
        seen = []

        class HostEcho(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Host"))
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), HostEcho)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.assertTrue(UrlHttpProbe().probe_route(
                "127.0.0.1", server.server_port, "unresolvable.test", timeout=1,
            ))
            self.assertEqual(seen, ["unresolvable.test"])
            self.assertFalse(UrlHttpProbe().probe_route(
                "169.254.169.254", 80, "metadata", timeout=0.01,
            ))
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def test_rejects_non_success_http_status(self):
        class NotFound(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), NotFound)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.assertFalse(UrlHttpProbe().probe(
                f"http://127.0.0.1:{server.server_port}", timeout=1,
            ))
        finally:
            server.shutdown()
            worker.join()
            server.server_close()

    def test_rejects_transport_failure_without_raising(self):
        # A local closed port is deterministic and must remain a false probe
        # result rather than leak an urllib transport exception.
        self.assertFalse(UrlHttpProbe().probe("http://127.0.0.1:1", timeout=0.01))

    def test_rejects_non_http_schemes_and_invalid_inputs(self):
        probe = UrlHttpProbe()
        for url, timeout in (
            ("file:///etc/passwd", 1),
            ("ftp://example.test/health", 1),
            ("http://example.test/health\x00", 1),
            ("http://", 1),
            ("http://example.test/health", -1),
            ("http://example.test/health", float("nan")),
            ("http://example.test/health", True),
        ):
            with self.subTest(url=url, timeout=timeout):
                self.assertFalse(probe.probe(url, timeout=timeout))


if __name__ == "__main__":
    unittest.main()
