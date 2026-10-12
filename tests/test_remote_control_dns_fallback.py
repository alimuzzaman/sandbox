import socket
import unittest
import urllib.error
import urllib.request
from unittest import mock

import sandbox.core._remote as remote

REMOTE = {"control_url": "https://sb-x.example.test", "control_transport": "https",
          "bearer_token": "t", "origin_ipv4": "203.0.113.7"}


def _unresolvable(*args, **kwargs):
    raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))


class ControlDnsFallback(unittest.TestCase):
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
