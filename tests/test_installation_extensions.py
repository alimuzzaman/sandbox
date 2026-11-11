"""Root metadata registration does not imply executable worker capability."""
import hashlib
import json
import unittest
from unittest import mock
from sandbox.installation import binding, extensions


class InstallationExtensionTest(unittest.TestCase):
    def setUp(self):
        self.binding = {'installation_id': 'a' * 32, 'incarnation': 'b' * 32,
                        'runtime_revision': 'c' * 24, 'policy_digest': 'd' * 64}
        self.descriptor = {'schema_version': 1, **self.binding, 'image_id': 'sha256:' + 'e' * 64,
                           'source_digest': 'f' * 64, 'platform': 'linux/amd64', 'protocol': 1}

    def record(self, descriptor):
        return {'schema_version': 1, 'descriptor': descriptor,
                'digest': hashlib.sha256(json.dumps(descriptor, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()}

    def read(self, descriptor=None, digest=None):
        value = self.record(self.descriptor if descriptor is None else descriptor)
        if digest is not None:
            value['digest'] = digest
        with mock.patch.object(binding, '_validate_authority', return_value=self.binding), \
                mock.patch.object(binding, '_directory'), \
                mock.patch.object(binding, '_read', return_value=value), \
                mock.patch.object(extensions, '_PROVIDERS', {}):
            result = extensions.read_extension('hosting.baseline.worker.v1', self.binding)
            self.assertEqual(extensions.installed_capabilities(self.binding), {})
            return result

    def test_root_registered_exact_worker_descriptor_has_canonical_digest_but_no_capability(self):
        self.assertEqual(self.read(), {k: v for k, v in self.record(self.descriptor).items() if k != 'schema_version'})

    def test_wrong_binding_digest_or_unsafe_descriptor_refuses(self):
        for change in ({'incarnation': '0' * 32}, {'platform': 'linux/arm64'},
                       {'protocol': True}, {'image_id': 'mutable:latest'}, {'socket_path': '/tmp/fake'}):
            with self.subTest(change=change), self.assertRaises(binding.InstallationBindingError):
                self.read({**self.descriptor, **change})
        with self.assertRaises(binding.InstallationBindingError):
            self.read(digest='0' * 64)

    def test_unknown_extension_name_refuses_without_file_access(self):
        with mock.patch.object(binding, '_read', side_effect=AssertionError('file access')):
            for name in ('unknown', '../escape', []):
                with self.subTest(name=name), self.assertRaises(binding.InstallationBindingError):
                    extensions.read_extension(name, self.binding)
