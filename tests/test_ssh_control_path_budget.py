"""Choose nonmultiplexed transport before one command when UNIX paths cannot fit."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import shlex
import subprocess

from sandbox.core import _remote as remote


class ControlPathBudgetTest(unittest.TestCase):
    def run_ssh(self, path):
        with mock.patch.object(remote, '_ssh_control_dir', return_value=Path(path)), \
                mock.patch.object(remote, '_ensure_ssh_control_dir'), \
                mock.patch.object(remote.sys, 'platform', 'darwin'), \
                mock.patch.object(remote.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            remote.ssh_run({'ssh': 'owner@synthetic'}, 'single-effect', timeout=2)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-1], 'single-effect')
            return run.call_args.args[0]

    def test_long_home_disables_mux_before_exactly_one_command(self):
        args = self.run_ssh('/fixture/' + 'long-home' * 12)
        self.assertIn('ControlMaster=no', args)
        self.assertIn('ControlPath=none', args)
        self.assertNotIn('ControlMaster=auto', args)

    def test_temporary_dot_plus_sixteen_suffix_is_included(self):
        # Final cm-<40hex> fits104, temporary cm-<40hex>.<16chars> does not.
        args = self.run_ssh('/' + 'x' * 50)
        self.assertIn('ControlMaster=no', args)

    def test_short_private_path_retains_mux(self):
        args = self.run_ssh('/fixture/s')
        self.assertIn('ControlMaster=auto', args)
        self.assertIn('ControlPath=/fixture/s/cm-%C', args)

    def test_utf8_is_counted_in_bytes(self):
        args = self.run_ssh('/' + '\u00e9' * 26)
        self.assertIn('ControlMaster=no', args)


class TailnetOnlyTransportTest(unittest.TestCase):
    def setUp(self):
        self.record = {'ssh': 'alim@212.95.33.238', 'joined': True, 'control_transport': 'tailscale',
                       'tailscale_host': '100.64.181.113'}

    def assert_keyless(self, args):
        for option in ('/dev/null', 'IdentityAgent=none', 'IdentitiesOnly=yes', 'PubkeyAuthentication=no',
                       'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no', 'PreferredAuthentications=none',
                       'ProxyCommand=none', 'ProxyJump=none', 'ControlMaster=no', 'ControlPath=none'):
            self.assertIn(option, args)
        self.assertNotIn('ControlMaster=auto', args)
        self.assertNotIn('212.95.33.238', ' '.join(args))

    def test_ssh_to_alim_uses_only_verified_tailnet_address_without_config_or_keys(self):
        args = remote.ssh_command_args(self.record, 'true')
        self.assert_keyless(args)
        self.assertEqual(args[-2:], ['alim@100.64.181.113', 'true'])

    def test_unrelated_owner_tailnet_http_does_not_implicitly_change_ssh_transport(self):
        owner = {'ssh': 'ubuntu@192.0.2.7:2222', 'control_transport': 'tailscale',
                 'tailscale_host': '100.64.2.3', 'control_access': 'token'}
        args = remote.ssh_command_args(owner, 'true', multiplex=False)
        self.assertEqual(args[-2:], ['ubuntu@192.0.2.7', 'true'])
        self.assertIn('2222', args)
        self.assertNotIn('PubkeyAuthentication=no', args)
        self.assertNotIn('/dev/null', args)
        self.assertNotIn('100.64.2.3', args)

    def test_scp_and_git_have_no_public_host_or_key_fallback(self):
        args = remote.scp_command_args(self.record, '/synthetic/file', '/synthetic/remote')
        self.assert_keyless(args)
        self.assertEqual(args[-1], 'alim@100.64.181.113:/synthetic/remote')
        self.assert_keyless(shlex.split(remote.git_ssh_command(self.record)))
        self.assertEqual(remote.git_ssh_url(self.record, '/repo'), 'ssh://alim@100.64.181.113/repo')

    def test_public_or_missing_tailnet_endpoint_and_non_tailnet_port_refuse(self):
        for fields in ({'tailscale_host': '212.95.33.238'}, {'tailscale_host': None},
                       {'ssh': 'alim@100.64.181.113:2222'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                remote.ssh_command_args({**self.record, **fields}, 'true')

    def test_initial_join_probe_is_keyless_and_timeout_is_unknown_not_denied(self):
        from sandbox.commands.remote import _first_ssh_login
        with mock.patch('sandbox.commands.remote.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(_first_ssh_login('alim@100.64.181.113', None), 'ok')
        self.assert_keyless(run.call_args.args[0])
        self.assertEqual(run.call_count, 1)
        with mock.patch('sandbox.commands.remote.subprocess.run', side_effect=subprocess.TimeoutExpired('ssh', 30)):
            self.assertEqual(_first_ssh_login('alim@100.64.181.113', None), 'unknown')

    def test_reachability_probe_cannot_escape_through_public_host_or_user_config(self):
        with mock.patch.object(remote.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(remote.check_reachable_diagnostic(self.record)['reachable'])
        self.assert_keyless(run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-2:], ['alim@100.64.181.113', 'true'])
        self.assertEqual(run.call_count, 1)

    def test_tailnet_policy_probe_itself_excludes_user_config_and_keys(self):
        from sandbox.commands.remote import _tailscale_ssh_login
        with mock.patch.object(remote, 'ssh_run', return_value=SimpleNamespace(
                returncode=0, stdout='100.64.181.113\n')), \
                mock.patch('sandbox.commands.remote.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(_tailscale_ssh_login(self.record, 'alim'), 'ok')
        self.assert_keyless(run.call_args.args[0])
        self.assertEqual(run.call_count, 1)

    def test_failed_keyless_command_is_never_retried_with_another_transport(self):
        with mock.patch.object(remote, '_ensure_ssh_control_dir'), \
                mock.patch.object(remote.subprocess, 'run', return_value=SimpleNamespace(returncode=255)) as run:
            self.assertEqual(remote.ssh_run(self.record, 'single-effect', timeout=2).returncode, 255)
        self.assertEqual(run.call_count, 1)
        self.assert_keyless(run.call_args.args[0])
