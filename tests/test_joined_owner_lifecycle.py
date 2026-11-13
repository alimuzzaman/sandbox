"""Joined Linux owners preserve server credentials instead of copying a bearer."""
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.core import _remote as remote
from sandbox.commands import remote as command
from tests.test_remote import _staged_source


class JoinedOwnerLifecycleTest(unittest.TestCase):
    def record(self):
        return {'joined': True, 'control_access': 'tailnet', 'control_transport': 'tailscale',
            'ssh': 'alim@100.64.1.2', 'tailscale_host': '100.64.1.2',
            'mcp_service': {'ownership_marker': remote._remote_mcp_marker('100.64.1.2', 9174),
                            'runtime_revision': 'a' * 24}}

    def test_confirmed_owner_refresh_preserves_private_file_without_controller_token(self):
        with mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='b' * 24), \
                mock.patch.object(remote, 'ssh_run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')) as run:
            plan = remote.migrate_remote_mcp_service(self.record(), '100.64.1.2', 9174, None,
                confirm=True, source_revision='f' * 40, staged_source=_staged_source(), preserve_credentials=True)
        script = run.call_args.args[1]
        self.assertIsNone(run.call_args.kwargs['input_data'])
        self.assertNotIn('IFS= read -r sandbox_remote_mcp_token', script)
        self.assertNotIn('> "$env_tmp"', script)
        self.assertIn('test ! -L "$env_path"', script)
        self.assertIn('$(id -u):600', script)
        self.assertIn('preserve existing owner-only remote credential file', plan['steps'])

    def test_existing_marker_cannot_be_replaced_using_preservation_mode(self):
        record = self.record()
        record['mcp_service']['ownership_marker'] = 'd' * 24
        with mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='b' * 24), \
                mock.patch.object(remote, 'ssh_run') as run:
            with self.assertRaisesRegex(RuntimeError, 'remote_service_ownership_unknown'):
                remote.migrate_remote_mcp_service(record, '100.64.1.2', 9174, None,
                    confirm=True, source_revision='f' * 40, staged_source=_staged_source(), preserve_credentials=True)
        run.assert_not_called()

    def test_unknown_legacy_credential_authority_never_reprovisions_or_exports(self):
        with mock.patch.object(remote, 'ssh_run') as run:
            with self.assertRaisesRegex(RuntimeError, 'credential_preservation_unavailable'):
                remote.migrate_remote_mcp_service(self.record(), '100.64.1.2', 9174, None,
                    confirm=True, legacy_pidfile=True, preserve_credentials=True)
        run.assert_not_called()

    def test_ordinary_nonjoined_missing_token_does_not_silently_adopt_owner_mode(self):
        with self.assertRaises(ValueError):
            remote.migrate_remote_mcp_service({'ssh': 'owner@example.invalid'}, '127.0.0.1', 9174,
                None, confirm=True, preserve_credentials=True)

    def test_joined_tailnet_access_status_requires_no_local_bearer(self):
        args = SimpleNamespace(name='status', ssh_url='box')
        output = io.StringIO()
        with mock.patch.object(remote, 'get_remote', return_value=self.record()), \
                mock.patch.object(remote, 'resolve_sandbox_home', return_value='/synthetic/sandbox'), \
                mock.patch.object(command, '_member_access_setting', return_value=True), \
                mock.patch.object(command, '_member_access_probe', return_value='admitted'), \
                mock.patch.object(remote, 'ssh_run') as write, contextlib.redirect_stdout(output):
            command._cmd_tailnet_access(args, True)
        value = json.loads(output.getvalue())
        self.assertTrue(value['ok'])
        self.assertEqual(value['members'], 'admitted')
        write.assert_not_called()
