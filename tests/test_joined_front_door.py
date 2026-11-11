"""Joined profile adoption is explicit, observed and local-only."""
import unittest
import subprocess
import contextlib
import io
import json
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import remote as command
from sandbox.core import _remote as sr
from sandbox.hosting.front_door import FrontDoorError
from sandbox.hosting.front_door import nginx
from sandbox.remote_runtime.join_profile import adopt_join_profile
from tests.test_remote_join import run
from tests.test_remote_join_identity import evidence

FACTS = {'nginx_installed': True, 'active': True, 'config_valid': True,
         'includes_conf_d': True, 'sandbox_caddy': {'active': False, 'observed': True},
         'listeners': [], 'listeners_observed': True}
PIN = {'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24}}


class JoinedFrontDoorTest(unittest.TestCase):
    def adopt(self, record=None, identity='c' * 24, facts=None, requested='nginx'):
        return adopt_join_profile(record or {}, PIN, requested,
            machine_probe=lambda _: identity, nginx_probe=lambda _: FACTS if facts is None else facts)

    def test_proven_existing_nginx_is_recorded_without_default_caddy_switch(self):
        self.assertEqual(self.adopt(), {'front_door': 'nginx'})

    def test_explicit_other_pin_cannot_be_replaced(self):
        with self.assertRaises(FrontDoorError):
            self.adopt({'front_door': 'caddy'})

    def test_different_ssh_machine_or_unsupported_profile_refuses(self):
        for fields in ({'identity': 'd' * 24}, {'identity': 'unknown'}, {'requested': 'caddy'}):
            with self.subTest(fields=fields), self.assertRaises(FrontDoorError):
                self.adopt(**fields)

    def test_unknown_invalid_or_competing_ingress_refuses(self):
        for changes in ({'active': False}, {'config_valid': False}, {'includes_conf_d': False},
                        {'sandbox_caddy': {'active': True}}, {'sandbox_caddy': {}},
                        {'sandbox_caddy': {'active': False, 'observed': False}},
                        {'listeners_observed': False}, {'listeners': [{'processes': []}]},
                        {'listeners': [{'processes': ['caddy']}]}):
            with self.subTest(changes=changes), self.assertRaises(FrontDoorError):
                self.adopt(facts={**FACTS, **changes})

    def test_refresh_adopts_only_registration_profile_and_preserves_attempt(self):
        record = {'joined': True, 'control_access': 'tailnet', 'provisioned': True,
                  'ssh': 'operator@host', 'reserved_stack': 'gs-original-request'}
        put = mock.Mock()
        with mock.patch.object(nginx, 'probe', return_value=FACTS), \
                mock.patch.object(sr, 'ssh_run', return_value=SimpleNamespace(returncode=0, stdout='c' * 24)) as ssh:
            code, result = run(command._cmd_service, SimpleNamespace(name='refresh', ssh_url='box',
                front_door='nginx'), {(sr, 'get_remote'): mock.Mock(return_value=record),
                    (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                    (sr, 'put_remote'): put})
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['front_door'], 'nginx')
        self.assertEqual(put.call_args.kwargs['_expected'], record)
        self.assertNotIn('reserved_stack', put.call_args.kwargs)
        self.assertNotIn('provisioned', put.call_args.kwargs)
        self.assertEqual(ssh.call_args.kwargs['timeout'], 15)
        self.assertNotIn('restart', ssh.call_args.args[1])

    def test_profile_refusal_never_writes_registration(self):
        put = mock.Mock()
        with mock.patch.object(nginx, 'probe', return_value=FACTS), \
                mock.patch.object(sr, 'ssh_run', return_value=SimpleNamespace(returncode=0, stdout='d' * 24)):
            code, result = run(command._cmd_service, SimpleNamespace(name='refresh', ssh_url='box',
                front_door='nginx'), {(sr, 'get_remote'): mock.Mock(return_value={
                    'joined': True, 'control_access': 'tailnet'}),
                    (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                    (sr, 'put_remote'): put})
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_first_join_initializes_ssh_host_key_before_profile_probe(self):
        from tests.test_remote_join import INVENTORY, TAILNET
        order = []
        def login(*args):
            order.append('login')
            return 'ok'
        def profile(*args):
            self.assertEqual(order, ['login'])
            order.append('profile')
            return {'front_door': 'nginx'}
        put = mock.Mock()
        code, result = run(command._cmd_join, SimpleNamespace(name='box', ssh_url=f'user@{TAILNET}',
            front_door='nginx'), {(sr, 'get_remote'): mock.Mock(return_value=None),
                (sr, '_remote_control_request'): mock.Mock(return_value=INVENTORY),
                (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                (command, '_tailnet_address'): mock.Mock(return_value=TAILNET),
                (command, '_first_ssh_login'): login,
                (command, '_joined_profile_evidence'): profile, (sr, 'put_remote'): put})
        self.assertEqual(code, 0)
        self.assertEqual(order, ['login', 'profile'])
        self.assertEqual(put.call_args.kwargs['front_door'], 'nginx')

    def test_join_metadata_timeout_or_missing_client_is_bounded_refusal(self):
        from tests.test_remote_join import INVENTORY, TAILNET
        for failure in (OSError('no SSH client'), subprocess.TimeoutExpired('ssh', 15)):
            put = mock.Mock()
            code, _ = run(command._cmd_join, SimpleNamespace(name='box', ssh_url=f'user@{TAILNET}',
                front_door='nginx'), {(sr, 'get_remote'): mock.Mock(return_value=None),
                    (sr, '_remote_control_request'): mock.Mock(return_value=INVENTORY),
                    (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                    (command, '_tailnet_address'): mock.Mock(return_value=TAILNET),
                    (command, '_first_ssh_login'): mock.Mock(return_value='ok'),
                    (command, '_joined_profile_evidence'): mock.Mock(side_effect=failure),
                    (sr, 'put_remote'): put})
            self.assertNotEqual(code, 0)
            put.assert_not_called()


class ProfileProbeEvidenceTest(unittest.TestCase):
    def probe(self, *, caddy_rc=3, listener_rc=0):
        def process(argv, **kwargs):
            if argv[:3] == ['systemctl', 'is-active', 'caddy']:
                return SimpleNamespace(returncode=caddy_rc, stdout='inactive' if caddy_rc == 3 else '', stderr='')
            if argv[0] == 'ss':
                return SimpleNamespace(returncode=listener_rc, stdout='', stderr='')
            if argv[:3] == ['systemctl', 'is-active', 'nginx']:
                return SimpleNamespace(returncode=0, stdout='active', stderr='')
            output = 'nginx/1.24.0' if '-v' in argv else (
                'include /etc/nginx/conf.d/*.conf;' if '-T' in argv else '')
            return SimpleNamespace(returncode=0, stdout=output, stderr='')

        def execute(_entry, program, payload, *, timeout=90):
            output = io.StringIO()
            with mock.patch('subprocess.run', side_effect=process), \
                    mock.patch('shutil.which', side_effect=lambda name: '/usr/sbin/nginx' if name == 'nginx' else None), \
                    mock.patch('glob.glob', return_value=[]), \
                    mock.patch('sys.stdin', io.StringIO('{}')), contextlib.redirect_stdout(output):
                exec(compile(program, '<mocked-nginx-probe>', 'exec'), {})
            return json.loads(output.getvalue())
        with mock.patch.object(nginx, '_python_program', side_effect=execute):
            return nginx.probe({'ssh': 'fixture'})

    def test_failed_status_and_listener_commands_are_unknown_not_negative_proof(self):
        value = self.probe(caddy_rc=127, listener_rc=127)
        self.assertFalse(value['sandbox_caddy']['observed'])
        self.assertFalse(value['listeners_observed'])
        with self.assertRaises(FrontDoorError):
            adopt_join_profile({}, PIN, 'nginx', machine_probe=lambda _: 'c' * 24,
                               nginx_probe=lambda _: value)

    def test_successful_inactive_and_empty_listener_observations_are_known(self):
        value = self.probe()
        self.assertTrue(value['sandbox_caddy']['observed'])
        self.assertTrue(value['listeners_observed'])
