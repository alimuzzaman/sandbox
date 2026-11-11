"""Automatic admitted-owner metadata repair with real local lock/CAS boundaries."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.core import _remote as remote
from sandbox.hosting.front_door import FrontDoorError, front_door_mode
from sandbox.hosting.front_door import nginx
from sandbox.resources.host_memory.remote import RemoteProtocolError
from tests.test_remote_join_identity import evidence
from tests.test_joined_front_door import FACTS


class AutomaticJoinedAccessTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = {'box': {'joined': True, 'control_access': 'tailnet',
            'control_transport': 'tailscale', 'control_url': 'http://100.64.1.2:9174',
            'tailscale_host': '100.64.1.2', 'ssh': 'owner@100.64.1.2',
            'original_request': 'stack-gs-c85dea370adc-dccb6f'}}
        self.writes = []
        def write(block):
            self.writes.append(copy.deepcopy(block))
            self.store.clear()
            self.store.update(copy.deepcopy(block))
        self.request = mock.Mock(return_value=evidence())
        self.ssh = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout='c' * 24, stderr=''))
        facts = {**FACTS, 'listeners': [{'port': '443', 'processes': ['nginx']}]}
        self.profile = mock.Mock(return_value=facts)
        patches = (mock.patch.object(remote, 'RUNTIME_DIR', Path(temporary.name)),
            mock.patch.object(remote, '_remote_block', side_effect=lambda: copy.deepcopy(self.store)),
            mock.patch.object(remote, '_write_remote_block', side_effect=write),
            mock.patch.object(remote, 'remote_host_memory_request', self.request),
            mock.patch.object(remote, 'ssh_run', self.ssh), mock.patch.object(nginx, 'probe', self.profile))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_ordinary_lookup_adopts_missing_pins_and_proven_profile_without_manual_refresh(self):
        value = remote.get_remote('box')
        self.assertEqual(value['front_door'], 'nginx')
        self.assertEqual(value['control_identity']['target_identity'], 'c' * 24)
        self.assertEqual(value['mcp_service'], {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24})
        self.assertEqual(value['original_request'], 'stack-gs-c85dea370adc-dccb6f')
        self.assertEqual(len(self.writes), 1)
        self.assertNotIn('bearer_token', self.store['box'])
        self.assertNotIn('_remote_name', self.store['box'])
        self.assertLessEqual(self.request.call_args.kwargs['timeout_seconds'], 15)
        self.assertTrue(self.profile.call_args.kwargs['profile_only'])

    def test_second_lookup_with_complete_metadata_performs_no_network_or_write(self):
        first = remote.get_remote('box')
        self.request.reset_mock()
        self.ssh.reset_mock()
        self.assertEqual(remote.get_remote('box'), first)
        self.request.assert_not_called()
        self.ssh.assert_not_called()
        self.assertEqual(len(self.writes), 1)

    def test_ordinary_identity_builder_automatically_repairs_legacy_registration(self):
        from sandbox.resources.context import authenticated_target_identity
        with mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='b' * 24), \
                mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False):
            value = authenticated_target_identity('box', budget_seconds=2)
        self.assertEqual(value['target_identity'], 'c' * 24)
        self.assertEqual(self.store['box']['front_door'], 'nginx')
        self.assertEqual(len(self.writes), 1)
        self.assertTrue(all(call.kwargs['timeout_seconds'] <= 2 for call in self.request.call_args_list))

    def test_adoption_under_existing_registration_lock_does_not_deadlock(self):
        with remote.registered_remote_lock('box', timeout_seconds=0.2):
            value = remote.get_remote('box', budget_seconds=0.2)
        self.assertEqual(value['front_door'], 'nginx')
        self.assertEqual(len(self.writes), 1)

    def test_inherited_process_lock_bookkeeping_does_not_bypass_kernel_lock(self):
        with remote.registered_remote_lock('box', timeout_seconds=0.2):
            inherited = SimpleNamespace(held={'box'}, pid=-1)
            with mock.patch.object(remote, '_REGISTRATION_LOCK_STATE', inherited):
                with self.assertRaises(remote.RemoteRegistrationBusy):
                    with remote.registered_remote_lock('box', timeout_seconds=0.05):
                        self.fail('another process bookkeeping cannot prove ownership')

    def test_real_pin_or_ssh_machine_mismatch_never_writes(self):
        self.store['box']['mcp_service'] = {'ownership_marker': 'd' * 24}
        with self.assertRaises(RemoteProtocolError):
            remote.get_remote('box')
        self.assertEqual(self.writes, [])
        del self.store['box']['mcp_service']
        self.ssh.return_value.stdout = 'd' * 24
        with self.assertRaises(FrontDoorError):
            remote.get_remote('box')
        self.assertEqual(self.writes, [])

    def test_changed_registration_cas_refuses_instead_of_overwriting_it(self):
        def changed(*args, **kwargs):
            self.store['box']['ssh'] = 'changed@100.64.1.3'
            return evidence()
        self.request.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'remote_registration_changed'):
            remote.get_remote('box')
        self.assertEqual(self.writes, [])
        self.assertEqual(self.store['box']['ssh'], 'changed@100.64.1.3')

    def test_missing_profile_evidence_does_not_deny_control_or_guess_caddy(self):
        self.ssh.return_value.returncode = 255
        value = remote.get_remote('box')
        self.assertEqual(value['control_identity']['target_identity'], 'c' * 24)
        self.assertNotIn('front_door', value)
        with self.assertRaises(FrontDoorError) as failure:
            front_door_mode(value)
        self.assertEqual(failure.exception.code, 'joined_profile_unavailable')
        self.assertEqual(front_door_mode({'ssh': 'ordinary-owner'}), 'caddy')

    def test_actual_observed_caddy_can_be_adopted_without_guessing(self):
        self.profile.return_value = {'nginx_installed': False, 'sandbox_caddy': {
            'installed': True, 'active': True, 'observed': True}, 'listeners_observed': True,
            'listeners': [{'port': '443', 'processes': ['caddy']}]}
        self.assertEqual(remote.get_remote('box')['front_door'], 'caddy')

    def test_transport_unknown_and_real_http_denial_are_distinct(self):
        for error, code in (('connection refused', 'joined_metadata_unavailable'),
                            ('remote control returned HTTP 401', 'remote_access_denied')):
            self.request.side_effect = RuntimeError(error)
            with self.subTest(error=error), self.assertRaises(RemoteProtocolError) as failure:
                remote.get_remote('box')
            self.assertEqual(failure.exception.code, code)
        self.assertEqual(self.writes, [])

    def test_nonaccess_metadata_lookup_can_remain_strictly_local(self):
        self.request.side_effect = AssertionError('network forbidden')
        self.assertTrue(remote.get_remote('box', adopt_joined=False)['joined'])
        self.assertEqual(self.writes, [])

    def test_exhausted_discovery_budget_never_writes(self):
        from sandbox.remote_runtime import join_adoption
        # Initial deadline100+1, then time102: no network/profile/CAS effects.
        with mock.patch.object(join_adoption.time, 'monotonic', side_effect=[100, 102]):
            with self.assertRaises(RemoteProtocolError) as failure:
                remote.get_remote('box', budget_seconds=1)
        self.assertEqual(failure.exception.code, 'remote_budget_exhausted')
        self.request.assert_not_called()
        self.assertEqual(self.writes, [])


class JoinedStatusTruthTest(unittest.TestCase):
    def observe(self, error=None, binding=None):
        request = mock.Mock(side_effect=error, return_value=binding or evidence())
        record = {'joined': True, 'control_access': 'tailnet',
                  'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24},
                  'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24}}
        unit = {'ownership': 'ambiguous', 'bind': None, 'port': None, 'service_name': None,
                'authenticated': False, 'auth_state': 'unknown', 'compatibility': {'state': 'compatible', 'ok': True}}
        with mock.patch.object(remote, 'remote_host_memory_request', request), \
                mock.patch.object(remote, '_remote_mcp_service_status_via_ssh', return_value=unit):
            return remote.remote_mcp_service_status(record)

    def test_actual_admitted_http_is_true_without_inventing_unit_bind_or_owner(self):
        result = self.observe()
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['control_admission']['state'], 'admitted')
        self.assertEqual(result['ownership'], 'ambiguous')
        self.assertIsNone(result['bind'])
        self.assertIsNone(result['port'])
        self.assertIsNone(result['service_name'])
        self.assertEqual(result['compatibility']['state'], 'compatible')

    def test_unknown_metadata_is_not_reported_as_denied(self):
        result = self.observe(RuntimeError('endpoint unavailable'))
        self.assertIsNone(result['authenticated'])
        self.assertEqual(result['auth_state'], 'unknown')
        self.assertEqual(result['control_admission']['state'], 'unavailable')

    def test_actual_401_is_reported_as_denied(self):
        result = self.observe(RuntimeError('remote control returned HTTP 401'))
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['control_admission']['state'], 'denied')

    def test_real_machine_mismatch_cannot_become_admitted_status(self):
        with self.assertRaises(RemoteProtocolError):
            self.observe(binding=evidence(identity='d' * 24))

    def test_real_ssh_policy_denial_is_separate_from_admitted_http(self):
        record = {'joined': True, 'control_access': 'tailnet', 'control_transport': 'tailscale',
            'tailscale_host': '100.64.1.2', 'ssh': 'alim@100.64.1.2',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        with mock.patch.object(remote, 'remote_host_memory_request', return_value=evidence()), \
                mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='d' * 24), \
                mock.patch.object(remote, 'ssh_run', return_value=SimpleNamespace(returncode=255,
                    stdout='', stderr='tailnet policy does not permit you to SSH to this node')):
            value = remote.remote_mcp_service_status(record)
        self.assertTrue(value['authenticated'])
        self.assertEqual(value['control_admission']['state'], 'admitted')
        self.assertEqual(value['ssh_access']['state'], 'denied')
        self.assertFalse(value['compatibility']['ok'])
