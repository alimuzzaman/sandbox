"""Real CLI callers retain joined transport policy across SSH operands."""
import io
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.core import _remote as remote
from sandbox.core import _hermes as hermes
from sandbox.core import _proplugins as proplugins
from sandbox.commands import hermes as hermes_command
from sandbox.commands import remote as remote_command


class JoinedTransportCallersTest(unittest.TestCase):
    def setUp(self):
        self.entry = {'ssh': 'alim@192.0.2.8', 'joined': True, 'provisioned': True,
            'control_transport': 'tailscale', 'tailscale_host': '100.64.1.2'}

    def assert_private(self, argv):
        self.assertIn('alim@100.64.1.2', ' '.join(argv))
        self.assertNotIn('192.0.2.8', ' '.join(argv))
        for option in ('/dev/null', 'IdentityAgent=none', 'IdentitiesOnly=yes',
                       'PubkeyAuthentication=no', 'PreferredAuthentications=none',
                       'ControlMaster=no', 'ProxyCommand=none'):
            self.assertIn(option, argv)

    def test_hermes_auth_three_commands_keep_full_record_and_token_only_on_stdin(self):
        token = b'github_pat_synthetic_fixture_only'
        stdin = SimpleNamespace(isatty=lambda: False, buffer=io.BytesIO(token))
        args = SimpleNamespace(subaction='auth', target='github', token_stdin=True, remote='box')
        with mock.patch.object(remote, 'get_remote', return_value=self.entry), \
                mock.patch.object(hermes_command.sys, 'stdin', stdin), \
                mock.patch.object(hermes_command.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(hermes_command._repo_action(args)['ok'])
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assert_private(call.args[0])
            self.assertNotIn(token.decode(), ' '.join(call.args[0]))
        self.assertEqual(run.call_args_list[1].kwargs['input'], token)

    def test_hermes_interactive_chat_keeps_tty_and_shared_keyless_policy(self):
        paths = {'repo_root': '/synthetic/repos', 'hermes_home': '/synthetic/hermes',
                 'launcher': '/synthetic/hermes/launcher'}
        with mock.patch.object(hermes, '_require_remote', return_value=self.entry), \
                mock.patch.object(hermes, '_paths', return_value=paths), \
                mock.patch.object(hermes.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(hermes.chat('box', 'project', worktree=False)['ok'])
        self.assert_private(run.call_args.args[0])
        self.assertIn('-tt', run.call_args.args[0])

    def test_proplugin_rsync_operand_and_ssh_command_use_same_private_endpoint(self):
        argv = proplugins._rsync_argv(self.entry, Path('/synthetic/plugins'), '/synthetic/destination')
        self.assertEqual(argv[-1], 'alim@100.64.1.2:/synthetic/destination/')
        transport = shlex.split(argv[argv.index('-e') + 1])
        self.assertIn('HostName=100.64.1.2', transport)
        self.assertIn('PubkeyAuthentication=no', transport)
        self.assertNotIn('192.0.2.8', ' '.join(argv))

    def test_exact_runtime_upload_forwards_record_without_reducing_it_to_ssh_string(self):
        revision = 'a' * 40
        with mock.patch.object(remote_command, '_assert_clean_source_revision'), \
                mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='b' * 24), \
                mock.patch.object(remote_command.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=b'archive', stderr=b'')), \
                mock.patch.object(remote, 'ssh_process', return_value=SimpleNamespace(
                    returncode=0, stdout=b'', stderr=b'')) as upload:
            remote_command._upload_runtime_source(self.entry, source_revision=revision)
        self.assertEqual(upload.call_args.args[0], self.entry)
