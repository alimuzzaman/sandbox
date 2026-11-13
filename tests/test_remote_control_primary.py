"""Loopback wire fixtures for selected Tailnet control requests, no real nodes."""
import http.server
import socket
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from sandbox.core import _remote


class PrimaryControlEndpointTests(unittest.TestCase):
    def setUp(self):
        self.requests = {'selected': [], 'other': []}
        self.status = 200
        self.location = None
        owner = self
        def serve(kind):
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    owner.requests[kind].append((self.command, self.path, dict(self.headers), body))
                    self.send_response(owner.status if kind == 'selected' else 200)
                    if kind == 'selected' and owner.location:
                        self.send_header('Location', owner.location)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                do_POST = do_GET
                def log_message(self, *_args):
                    pass
            server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 3)
            self.addCleanup(server.shutdown)
            return server.server_port
        self.selected_port = serve('selected')
        self.other_port = serve('other')
        self.host = '100.64.181.113'
        self.entry = {'joined': True, 'control_transport': 'tailscale', 'control_access': 'tailnet',
                      'tailscale_host': self.host,
                      'control_url': f'http://{self.host}:{self.selected_port}',
                      'origin_ipv4': '192.0.2.123'}
        self.connections = []
        connect = socket.create_connection
        def local_connection(address, *args, **kwargs):
            self.connections.append(address)
            if address == (self.host, self.selected_port):
                address = ('127.0.0.1', self.selected_port)
            return connect(address, *args, **kwargs)
        self.enterContext(patch.object(_remote.socket, 'create_connection', side_effect=local_connection))

    def test_ambient_proxy_is_never_contacted_by_primary_or_direct_request(self):
        proxy = f'http://127.0.0.1:{self.other_port}'
        for direct in (False, True):
            with self.subTest(direct=direct), patch.object(urllib.request, 'getproxies', return_value={'http': proxy}), \
                    patch.object(urllib.request, 'proxy_bypass', return_value=False):
                self.requests['selected'].clear()
                self.requests['other'].clear()
                self.connections.clear()
                request = urllib.request.Request(self.entry['control_url'] + '/resources', data=b'fixture',
                                                  headers=_remote.control_auth_headers(self.entry))
                with _remote._control_urlopen(self.entry, request, 2, direct=direct) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(len(self.requests['selected']), 1)
                self.assertEqual(self.requests['selected'][0][3], b'fixture')
                self.assertNotIn('Authorization', self.requests['selected'][0][2])
                self.assertEqual(self.requests['other'], [])
                self.assertEqual(self.connections, [(self.host, self.selected_port)])

    def test_redirect_statuses_never_send_a_second_request_or_body(self):
        self.location = f'http://127.0.0.1:{self.other_port}/escaped'
        for status in (301, 302, 303, 307, 308):
            for direct in (False, True):
                with self.subTest(status=status, direct=direct):
                    self.status = status
                    self.requests['selected'].clear()
                    self.requests['other'].clear()
                    self.connections.clear()
                    request = urllib.request.Request(self.entry['control_url'] + '/resources', data=b'original-body')
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        _remote._control_urlopen(self.entry, request, 2, direct=direct)
                    self.assertEqual(failure.exception.code, status)
                    self.assertEqual(len(self.requests['selected']), 1)
                    self.assertEqual(self.requests['other'], [])
                    self.assertEqual(self.connections, [(self.host, self.selected_port)])

    def test_explicit_proxy_request_is_refused_before_connection(self):
        for proxy in (f'127.0.0.1:{self.other_port}', f'{self.host}:{self.selected_port}'):
            for direct in (False, True):
                with self.subTest(proxy=proxy, direct=direct):
                    request = urllib.request.Request(self.entry['control_url'] + '/resources', data=b'fixture')
                    request.set_proxy(proxy, 'http')
                    with patch.object(_remote, '_control_primary_open') as primary:
                        with self.assertRaisesRegex(urllib.error.URLError, 'proxy'):
                            _remote._control_urlopen(self.entry, request, 2, direct=direct)
                    primary.assert_not_called()
                    self.assertEqual(self.connections, [])

    def test_joined_dns_failure_never_uses_recorded_public_origin(self):
        entry = {**self.entry, 'control_url': 'https://node.example.test', 'tailscale_host': 'node.example.test'}
        request = urllib.request.Request(entry['control_url'] + '/resources')
        for fields in ({}, {'control_transport': 'https'}, {'joined': False}):
            with self.subTest(fields=fields), patch.object(_remote, '_control_primary_open',
                              side_effect=urllib.error.URLError(socket.gaierror('synthetic DNS refusal'))), \
                    patch.object(_remote, '_OriginHTTPSHandler') as origin:
                with self.assertRaises(urllib.error.URLError):
                    _remote._control_urlopen({**entry, **fields}, request, 2)
                origin.assert_not_called()
                self.assertEqual(self.connections, [])

    def test_primary_401_retains_unauthenticated_status_without_other_endpoint(self):
        self.status = 401
        result = _remote.verify_remote(self.entry)
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['status'], 401)
        self.assertEqual(len(self.requests['selected']), 1)
        self.assertEqual(self.requests['other'], [])
