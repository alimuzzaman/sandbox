import socket
import http.server
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import sandbox.core._remote as remote
from tests.subprocess_support import run_test_process

REMOTE = {"control_url": "https://sb-x.example.test", "control_transport": "https",
          "bearer_token": "t", "origin_ipv4": "203.0.113.7"}


def _unresolvable(*args, **kwargs):
    raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))


class ControlDnsFallback(unittest.TestCase):
    def test_direct_primary_success_disables_proxy_without_origin_retry(self):
        request = urllib.request.Request(REMOTE["control_url"] + "/mcp", data=b"payload",
                                         method="POST", headers={"Authorization": "Bearer test"})
        response = object()
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(remote.urllib.request, "build_opener", return_value=opener) as build, \
                mock.patch.object(remote.urllib.request, "OpenerDirector") as fallback, \
                mock.patch.object(remote.urllib.request, "urlopen") as default:
            self.assertIs(remote._control_urlopen(REMOTE, request, 5, direct=True), response)
        build.assert_called_once()
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, urllib.request.ProxyHandler)
        self.assertEqual(handler.proxies, {})
        opener.open.assert_called_once_with(request, timeout=5)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"payload")
        self.assertEqual(request.host, "sb-x.example.test")
        self.assertEqual(request.full_url, REMOTE["control_url"] + "/mcp")
        self.assertEqual(request.get_header("Authorization"), "Bearer test")
        fallback.assert_not_called()
        default.assert_not_called()

    def test_unresolvable_host_connects_to_the_recorded_origin(self):
        opened = {}

        class Opener:
            def __init__(self):
                self.handlers = []

            def add_handler(self, handler):
                self.handlers.append(type(handler).__name__)
                if hasattr(handler, "_origin"):
                    opened["origin"] = handler._origin

            def open(self, request, timeout):
                opened["url"] = request.full_url
                opened["handlers"] = self.handlers
                return "response"

        request = urllib.request.Request("https://sb-x.example.test/mcp")
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable), \
                mock.patch.object(remote.urllib.request, "OpenerDirector", Opener):
            self.assertEqual(remote._control_urlopen(REMOTE, request, 5), "response")
        self.assertEqual(opened["origin"], "203.0.113.7")
        self.assertEqual(opened["url"], "https://sb-x.example.test/mcp")
        # No redirect, proxy or plain-HTTP handler on the fallback hop.
        self.assertEqual(opened["handlers"], ["_OriginHTTPSHandler", "HTTPErrorProcessor",
                                              "HTTPDefaultErrorHandler"])

    def test_unresolvable_host_without_origin_names_the_dns_record(self):
        request = urllib.request.Request("https://sb-x.example.test/mcp")
        entry = {k: v for k, v in REMOTE.items() if k != "origin_ipv4"}
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable):
            with self.assertRaises(urllib.error.URLError) as caught:
                remote._control_urlopen(entry, request, 5)
        self.assertIn("sb-x.example.test does not resolve", str(caught.exception.reason))

    def test_other_failures_are_not_redirected(self):
        def refused(*args, **kwargs):
            raise urllib.error.URLError(ConnectionRefusedError())
        request = urllib.request.Request("https://sb-x.example.test/mcp")
        with mock.patch.object(remote.urllib.request, "urlopen", refused), \
                mock.patch.object(remote.urllib.request, "OpenerDirector") as opener:
            with self.assertRaises(urllib.error.URLError):
                remote._control_urlopen(REMOTE, request, 5)
        opener.assert_not_called()

    def test_origin_connection_verifies_tls_for_the_control_hostname(self):
        connection = remote._OriginHTTPSConnection("sb-x.example.test", origin="203.0.113.7",
                                                   timeout=3, context=mock.Mock())
        with mock.patch.object(remote.socket, "create_connection", return_value="sock") as create:
            connection.connect()
        create.assert_called_once_with(("203.0.113.7", 443), 3)
        connection._context.wrap_socket.assert_called_once_with("sock", server_hostname="sb-x.example.test")

    def test_a_proxied_request_is_not_redirected_to_the_origin(self):
        request = urllib.request.Request("https://sb-x.example.test/mcp")
        request.set_proxy("proxy.example.test:3128", "https")
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable), \
                mock.patch.object(remote.urllib.request, "OpenerDirector") as opener:
            with self.assertRaises(urllib.error.URLError) as caught:
                remote._control_urlopen(REMOTE, request, 5)
        opener.assert_not_called()
        self.assertIn("proxy host", str(caught.exception.reason))

    def test_public_callers_name_the_unresolvable_host(self):
        entry = {k: v for k, v in REMOTE.items() if k != "origin_ipv4"}
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable):
            with self.assertRaises(RuntimeError) as caught:
                remote._remote_control_request(entry, "/resources", payload={"a": 1})
        self.assertIn("sb-x.example.test does not resolve in DNS", str(caught.exception))

    def test_diagnostics_and_verify_expose_dns_reason(self):
        entry = {k: v for k, v in REMOTE.items() if k != "origin_ipv4"}
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable):
            for caller in (remote.remote_diagnostics, remote.verify_remote):
                with self.subTest(caller=caller.__name__):
                    with self.assertRaisesRegex(RuntimeError, "sb-x.example.test does not resolve in DNS"):
                        caller(entry)

    def test_doctor_uses_shared_fallback_with_direct_proxy_policy(self):
        entry = {**REMOTE, "ssh": "user@example.test", "provisioned": True}
        response = mock.MagicMock()
        response.__enter__.return_value.status = 405
        with mock.patch.object(remote, "check_reachable", return_value=True), \
                mock.patch.object(remote, "_control_urlopen", return_value=response) as opened:
            checks = remote.remote_doctor_checks(entry)
        row = next(row for row in checks if row["label"] == "MCP endpoint reachable")
        self.assertTrue(row["ok"])
        self.assertIs(opened.call_args.args[0], entry)
        self.assertTrue(opened.call_args.kwargs["direct"])
        self.assertEqual(opened.call_args.args[1].full_url, REMOTE["control_url"] + "/mcp")


class ControlDnsFallbackWire(unittest.TestCase):
    """Loopback-only HTTPS fixtures exercise the actual fallback opener."""

    @classmethod
    def setUpClass(cls):
        openssl = next((str(path) for path in (Path("/usr/bin/openssl"),
                        Path("/opt/homebrew/bin/openssl")) if path.is_file()), None)
        if openssl is None:
            raise unittest.SkipTest("openssl is needed to generate an ephemeral TLS fixture")
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        cls.cert, key = root / "cert.pem", root / "key.pem"
        config = root / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
                          "[dn]\nCN=control.example.test\n[ext]\n"
                          "subjectAltName=DNS:control.example.test\n")
        run_test_process([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                          "-keyout", str(key), "-out", str(cls.cert), "-days", "1",
                          "-config", str(config)], capture_output=True, check=True, timeout=30,
                         env={"HOME": str(root), "TMPDIR": str(root)})
        cls.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(str(cls.cert), str(key))

    def setUp(self):
        self.requests = []
        self.connections = 0
        self.status = 200
        self.location = None
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append((self.command, self.path, self.headers, body))
                self.send_response(owner.status)
                if owner.location:
                    self.send_header("Location", owner.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = handle_request
            do_POST = handle_request

            def log_message(self, *_args):
                pass

        class Server(http.server.HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                owner.connections += 1
                try:
                    return owner.server_context.wrap_socket(connection, server_side=True), address
                except (ssl.SSLError, OSError):
                    connection.close()
                    raise

            def handle_error(self, request, client_address):
                pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.entry = {"control_url": f"https://control.example.test:{self.port}",
                      "control_transport": "https", "origin_ipv4": "127.0.0.1",
                      "bearer_token": "synthetic-token"}

    def open_fallback(self, request, *, trust=True):
        context = ssl.create_default_context(cafile=str(self.cert)) if trust else ssl.create_default_context()
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable), \
                mock.patch.object(remote.ssl, "create_default_context", return_value=context):
            return remote._control_urlopen(self.entry, request, 3)

    def test_post_preserves_host_authorization_and_single_content_length(self):
        data = b'{"value":1}'
        request = urllib.request.Request(self.entry["control_url"] + "/mcp", data=data,
                                         headers={"Authorization": "Bearer synthetic-token"})
        with self.open_fallback(request) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(len(self.requests), 1)
        method, path, headers, body = self.requests[0]
        self.assertEqual((method, path, body), ("POST", "/mcp", data))
        self.assertEqual(headers.get_all("Host"), [f"control.example.test:{self.port}"])
        self.assertEqual(headers.get_all("Authorization"), ["Bearer synthetic-token"])
        self.assertEqual(headers.get_all("Content-Length"), [str(len(data))])

    def test_redirects_to_http_and_https_never_send_a_second_request(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                self.requests.clear()
                before = self.connections
                self.status = 302
                self.location = f"{scheme}://control.example.test:{self.port}/second"
                request = urllib.request.Request(self.entry["control_url"] + "/mcp",
                                                 headers={"Authorization": "Bearer synthetic-token"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.open_fallback(request)
                self.assertEqual(caught.exception.code, 302)
                self.assertEqual([item[1] for item in self.requests], ["/mcp"])
                self.assertEqual(self.connections - before, 1)

    def test_untrusted_and_mismatched_certificates_send_no_headers(self):
        for hostname, trust in (("control.example.test", False), ("wrong.example.test", True)):
            with self.subTest(hostname=hostname, trust=trust):
                self.requests.clear()
                request = urllib.request.Request(f"https://{hostname}:{self.port}/mcp",
                                                 headers={"Authorization": "Bearer synthetic-token"})
                with self.assertRaises(urllib.error.URLError):
                    self.open_fallback(request, trust=trust)
                self.assertEqual(self.requests, [])

    def test_verify_remote_classifies_http_statuses_through_fallback(self):
        context = ssl.create_default_context(cafile=str(self.cert))
        with mock.patch.object(remote.urllib.request, "urlopen", _unresolvable), \
                mock.patch.object(remote.ssl, "create_default_context", return_value=context):
            for status in (400, 405, 406, 401):
                with self.subTest(status=status):
                    self.status = status
                    result = remote.verify_remote(self.entry)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["authenticated"], status != 401)
                    self.assertEqual(result["ok"], status != 401)
        self.assertEqual(len(self.requests), 4)

    def test_doctor_direct_open_uses_real_fallback(self):
        self.status = 405
        context = ssl.create_default_context(cafile=str(self.cert))
        direct_opener = mock.Mock()
        direct_opener.open.side_effect = _unresolvable
        entry = {**self.entry, "ssh": "user@example.test", "provisioned": True}
        with mock.patch.object(remote, "check_reachable", return_value=True), \
                mock.patch.object(remote.urllib.request, "build_opener", return_value=direct_opener) as build, \
                mock.patch.object(remote.ssl, "create_default_context", return_value=context):
            checks = remote.remote_doctor_checks(entry)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertTrue(next(row for row in checks if row["label"] == "MCP endpoint reachable")["ok"])
        self.assertEqual([item[1] for item in self.requests], ["/mcp"])
