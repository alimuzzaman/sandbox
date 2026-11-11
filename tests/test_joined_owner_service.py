"""Actual selected-unit decoder: private credential metadata only, no values."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.core import _remote
from sandbox.remote_runtime import owner_service


class OwnerUnitObservationTest(unittest.TestCase):
    def run_probe(self, *, marker='a' * 24, mode=0o600, machine='1' * 32):
        identity = hashlib.sha256(b'sandbox-host-machine-id-v1\0' + ('1' * 32).encode()).hexdigest()[:24]
        record = {'joined': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24},
            'control_identity': {'schema_version': 1, 'target_identity': identity}}
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            unit = home / '.config/systemd/user' / _remote.REMOTE_MCP_SERVICE
            unit.parent.mkdir(parents=True)
            unit.write_text('EnvironmentFile=' + _remote._REMOTE_MCP_UNIT_ENV + '\n'
                'Environment=SANDBOX_REMOTE_MCP_MARKER=' + marker + '\n'
                'Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION=' + 'b' * 24 + '\n'
                'WorkingDirectory=%h/sandbox/sb-src\n'
                'ExecStart=%h/sandbox/sb-src/sb mcp --transport streamable-http --bind 100.64.1.2 --port 9174\n')
            unit.chmod(0o600)
            credential = home / '.sandbox/mcp-remote.env'
            credential.parent.mkdir()
            credential.write_bytes(b'opaque-synthetic-fixture')
            credential.chmod(mode)
            reads = []
            real_read = Path.read_text
            def read(path, *args, **kwargs):
                reads.append(str(path))
                if path == credential:
                    raise AssertionError('credential contents must never be read')
                if str(path) == '/etc/machine-id':
                    return machine
                return real_read(path, *args, **kwargs)
            def ssh(selected, command, **kwargs):
                self.assertEqual(selected, record)
                words = shlex.split(command)
                payload = words[-1]
                output = io.StringIO()
                with mock.patch('pwd.getpwuid', return_value=SimpleNamespace(pw_dir=str(home))), \
                        mock.patch.object(Path, 'read_text', read), \
                        mock.patch('sys.argv', ['probe', payload]), contextlib.redirect_stdout(output):
                    code = 0
                    try:
                        exec(compile(owner_service.PROGRAM, '<unit-metadata-fixture>', 'exec'), {})
                    except SystemExit as failure:
                        code = failure.code
                return SimpleNamespace(returncode=code, stdout=output.getvalue(), stderr='')
            try:
                result = owner_service.probe_joined_owner_service(record, '100.64.1.2', 9174, ssh)
            except owner_service.OwnerServiceUnavailable as error:
                result = error
            self.assertNotIn(str(credential), reads)
            return result

    def test_actual_unit_machine_marker_and_private_file_metadata_are_proven(self):
        value = self.run_probe()
        self.assertEqual(value['ownership'], 'proven')
        self.assertEqual(value['owner_uid'], os.geteuid())
        self.assertEqual(value['credential_state'], 'private-file-present')
        self.assertNotIn('bearer_token', value)

    def test_foreign_marker_or_machine_never_proves_owner(self):
        for fields in ({'marker': 'f' * 24}, {'machine': '2' * 32}):
            with self.subTest(fields=fields):
                self.assertIsInstance(self.run_probe(**fields), owner_service.OwnerServiceUnavailable)

    def test_nonprivate_credential_metadata_refuses_without_reading_values(self):
        self.assertIsInstance(self.run_probe(mode=0o644), owner_service.OwnerServiceUnavailable)

    def test_unexpected_observation_fields_are_not_echoed(self):
        record = {'joined': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24},
            'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24}}
        payload = {'schema_version': 1, 'ok': True, 'ownership': 'proven', 'owner_uid': 1000,
            'target_identity': 'c' * 24, 'ownership_marker': 'a' * 24,
            'runtime_revision': 'b' * 24, 'credential_state': 'private-file-present', 'extra': 'not-returned'}
        with self.assertRaises(owner_service.OwnerServiceUnavailable):
            owner_service.probe_joined_owner_service(record, '100.64.1.2', 9174,
                lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
