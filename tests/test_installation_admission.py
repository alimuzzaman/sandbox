"""Maintenance closes managed effects without reducing normal owner authority."""
import unittest
from unittest import mock
from pathlib import Path
from sandbox.installation import admission
from sandbox.installation.binding import InstallationBindingError


class InstallationAdmissionTest(unittest.TestCase):
    def test_owner_layout_preserves_existing_admission(self):
        with mock.patch.object(admission, 'is_managed_layout', return_value=False), \
                mock.patch.object(admission, '_read', side_effect=AssertionError('must not read managed policy')):
            self.assertTrue(admission.allowed_request('POST', '/mcp'))

    def test_closed_or_missing_managed_policy_refuses_effects_and_keeps_observation(self):
        with mock.patch.object(admission, '_directory'), \
                mock.patch.object(admission, '_read', return_value={'schema_version': 1, 'open': False}):
            self.assertFalse(admission.allowed_request('POST', '/mcp', managed=True))
            self.assertTrue(admission.allowed_request('GET', '/hosting/installation', managed=True))
            self.assertTrue(admission.allowed_request('GET', '/diagnostics', managed=True))
        with mock.patch.object(admission, '_directory', side_effect=InstallationBindingError('missing', 'missing')):
            self.assertFalse(admission.allowed_request('POST', '/resources', managed=True))

    def test_only_explicit_valid_open_policy_admits_effects(self):
        with mock.patch.object(admission, '_directory'), \
                mock.patch.object(admission, '_read', return_value={'schema_version': 1, 'open': True}):
            self.assertTrue(admission.allowed_request('POST', '/mcp', managed=True))
        with mock.patch.object(admission, '_directory'), \
                mock.patch.object(admission, '_read', return_value={'schema_version': True, 'open': True}):
            self.assertFalse(admission.allowed_request('POST', '/mcp', managed=True))
