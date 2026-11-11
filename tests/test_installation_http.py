"""Actual discovery handler behavior without requiring a running server or Starlette."""
import unittest
from types import SimpleNamespace
from unittest import mock
from sandbox.installation import http
from sandbox.installation.binding import InstallationBindingError


class DiscoveryResponseTest(unittest.IsolatedAsyncioTestCase):
    def request(self, *, query=None, chunks=()):
        async def stream():
            for chunk in chunks:
                yield chunk
        return SimpleNamespace(query_params=query or {}, stream=stream)

    async def test_success_uses_genuine_read_port_and_selected_home(self):
        with mock.patch.object(http, 'read_installation_binding', return_value={'installation_id': 'a' * 32}) as read:
            status, result = await http.installation_response(self.request(), home='/selected/state')
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(result['installation_schema'], 1)
        read.assert_called_once_with(home='/selected/state')

    async def test_missing_binding_is_read_only_conflict_not_fabricated_identity(self):
        with mock.patch.object(http, 'read_installation_binding', side_effect=InstallationBindingError(
                'installation_binding_uninitialized', 'not initialized')):
            status, result = await http.installation_response(self.request(), home='/selected/state')
        self.assertEqual(status, 409)
        self.assertFalse(result['ok'])
        self.assertIsNone(result['binding'])
        self.assertEqual(result['error']['code'], 'installation_binding_uninitialized')

    async def test_query_or_first_body_chunk_refuses_without_discovery(self):
        for request in (self.request(query={'bad': '1'}), self.request(chunks=(b'x',))):
            with mock.patch.object(http, 'read_installation_binding', side_effect=AssertionError('read')):
                status, result = await http.installation_response(request, home='/selected/state')
            self.assertEqual(status, 400)
            self.assertIsNone(result['binding'])

    async def test_body_rejected_without_consuming_remaining_stream(self):
        async def stream():
            yield b'nonempty'
            raise AssertionError('read beyond first body chunk')
        status, _ = await http.installation_response(SimpleNamespace(query_params={}, stream=stream), home='/selected/state')
        self.assertEqual(status, 400)
