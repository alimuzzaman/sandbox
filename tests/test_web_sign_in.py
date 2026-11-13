"""Guarded stack sign-in uses the panel boundary and returns tokens once."""
import json
import unittest
from unittest import mock

from sandbox.hosting import panel_access
from tests import test_web_share


class SignInRouteTest(unittest.TestCase):
    # Reuse the socket harness, not the share suite.
    setUp = test_web_share.ShareRouteTest.setUp
    patch = test_web_share.ShareRouteTest.patch
    headers = test_web_share.ShareRouteTest.headers
    request = test_web_share.ShareRouteTest.request

    def post(self, body, **overrides):
        status, cache, raw = self.request('POST', '/api/hosting/sign-in', body, self.headers(**overrides))
        return status, cache, json.loads(raw), raw

    def test_sign_in_returns_only_one_shot_fields(self):
        result = {'url': 'https://wp.example.test/wp-login.php?sandbox_autologin=1#opaque',
                  'expires_at': 123, 'user': 'admin', 'one_time': True, 'private': 'hidden'}
        with mock.patch.object(panel_access, 'sign_in', return_value=result) as mint, \
                mock.patch('sandbox.hosting.share.append_audit'):
            status, cache, body, raw = self.post({'remote': 'r', 'stack': 'demo'})
        self.assertEqual((status, cache), (200, 'no-store'))
        self.assertEqual(body, {'ok': True, **{k: result[k] for k in ('url', 'expires_at', 'user', 'one_time')}})
        mint.assert_called_once_with('r', 'demo')

    def test_sign_in_refuses_cross_origin_and_extra_keys_without_mint(self):
        with mock.patch.object(panel_access, 'sign_in') as mint:
            self.assertEqual(self.post({'remote': 'r', 'stack': 'demo'}, Origin='https://other.example.test')[0], 403)
            self.assertEqual(self.post({'remote': 'r', 'stack': 'demo', 'user': 'admin'})[0], 400)
            self.assertEqual(self.post({'remote': 'r', 'stack': '../demo'})[0], 400)
        mint.assert_not_called()

    def test_sign_in_errors_never_expose_remote_output(self):
        with mock.patch.object(panel_access, 'sign_in', side_effect=RuntimeError('planted-secret')), \
                mock.patch('sandbox.hosting.share.append_audit'):
            status, cache, body, raw = self.post({'remote': 'r', 'stack': 'demo'})
        self.assertEqual((status, cache), (409, 'no-store'))
        self.assertNotIn(b'planted-secret', raw)

    def test_get_does_not_mint(self):
        with mock.patch.object(panel_access, 'sign_in') as mint:
            self.request('GET', '/api/hosting/sign-in')
        mint.assert_not_called()

    def test_missing_audit_store_does_not_mint(self):
        with mock.patch.object(panel_access, 'sign_in') as mint, \
                mock.patch('sandbox.hosting.share.append_audit', side_effect=OSError('unavailable')):
            self.assertEqual(self.post({'remote': 'r', 'stack': 'demo'})[0], 409)
        mint.assert_not_called()

    def test_expected_unavailability_keeps_specific_safe_guidance(self):
        error = panel_access.SignInUnavailable('sign_in_not_declared', 'Declare WordPress autologin and reapply.')
        with mock.patch.object(panel_access, 'sign_in', side_effect=error), \
                mock.patch('sandbox.hosting.share.append_audit'):
            status, cache, body, raw = self.post({'remote': 'r', 'stack': 'demo'})
        self.assertEqual((status, cache), (409, 'no-store'))
        self.assertEqual(body['code'], error.code)
        self.assertEqual(body['message'], error.message)
