"""A descriptor selects only a fixed immutable release, never handler readiness."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.installation import provider
from sandbox.installation.binding import InstallationBindingError


class ReleaseProviderTest(unittest.TestCase):
    def setUp(self):
        self.binding = {'installation_id': 'a' * 32, 'incarnation': 'b' * 32,
            'machine_identity': 'c' * 24, 'runtime_revision': 'd' * 24, 'policy_digest': 'e' * 64,
            'execution_capabilities': {}}
        self.registration = {'schema_version': 1,
            **{key: value for key, value in self.binding.items() if key != 'execution_capabilities'},
            'bundle_digest': 'f' * 64, 'source_revision': '0' * 40}
        self.paths = SimpleNamespace(config=Path('/fixture/config'), releases=Path('/fixture/releases'))

    def read(self, registration=None, release=None):
        with mock.patch.object(provider, '_validate_authority') as authority, \
                mock.patch.object(provider, 'RootDirectory') as directory, \
                mock.patch.object(provider, 'release_descriptor', return_value=release or {
                    'bundle_digest': 'f' * 64, 'source_revision': '0' * 40}):
            directory.return_value.__enter__.return_value.read.return_value = registration or self.registration
            value = provider.installed_release(self.binding, paths=self.paths)
            self.assertEqual(authority.call_count, 2)
            return value

    def test_fixed_root_release_paths_are_derived_from_bound_revision(self):
        value = self.read()
        self.assertEqual(value['executable'], '/fixture/releases/' + 'd' * 24 + '/sb')
        self.assertEqual(value['python'], '/fixture/releases/' + 'd' * 24 + '/.cli-venv/bin/python')
        self.assertNotIn('execution_capabilities', value)

    def test_wrong_installation_policy_revision_or_bundle_refuses(self):
        for key in ('installation_id', 'incarnation', 'machine_identity', 'runtime_revision', 'policy_digest'):
            with self.subTest(key=key), self.assertRaises(InstallationBindingError):
                self.read({**self.registration, key: '1' * len(self.registration[key])})
        with self.assertRaises(InstallationBindingError):
            self.read(release={'bundle_digest': '1' * 64, 'source_revision': '0' * 40})

    def test_arbitrary_executable_field_cannot_enter_registration(self):
        with self.assertRaises(InstallationBindingError):
            self.read({**self.registration, 'executable': '/home/operator/mutable/sb'})
