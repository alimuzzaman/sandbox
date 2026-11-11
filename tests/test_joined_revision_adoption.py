"""Compatible service cache adoption is separate from pins and old operations."""
import copy
import unittest
from unittest import mock

from sandbox.core import _remote
from sandbox.remote_runtime import join_revision
from sandbox.remote_runtime.protocol import local_protocol
from sandbox.resources.host_memory.remote import HostMemoryRemote, RemoteProtocolError
from tests.test_remote_join_identity import evidence


class JoinedRevisionAdoptionTest(unittest.TestCase):
    def setUp(self):
        self.record = {'joined': True, 'control_access': 'tailnet', 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24},
            'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24},
            'original_job': '81ca4e04fda567deee0628d077d8c360', 'copy_id': '6f973982'}
        self.status = {'installed_runtime_revision': 'd' * 24,
            **_remote._runtime_compatibility_fields('e' * 24, 'd' * 24,
                                                   local_protocol().encode(), determinate=True)}

    def adopt(self, *, response=None, status=None, strict=False):
        self.request = mock.Mock(return_value=response or evidence(revision='d' * 24))
        self.observe = mock.Mock(return_value=status or self.status)
        with mock.patch.object(join_revision, 'strict_requested', return_value=strict), \
                mock.patch.object(_remote, 'put_remote') as writer, \
                mock.patch('sandbox.remote_runtime.pins.PinStore', side_effect=AssertionError('no pin mutation')) as pins:
            self.writer = writer
            result = join_revision.adopt_compatible_join_revision('box', self.record,
                self.request, self.observe, budget_seconds=2)
            pins.assert_not_called()
            return result

    def test_actual_protocol_producer_allows_same_owner_machine_cache_adoption_by_cas(self):
        original = copy.deepcopy(self.record)
        result = self.adopt()
        self.assertTrue(result['revision_cache_changed'])
        self.assertEqual(result['mcp_service']['runtime_revision'], 'd' * 24)
        self.assertEqual(self.writer.call_args.kwargs['_expected'], original)
        self.assertEqual(self.record, original)
        self.assertEqual(set(self.writer.call_args.kwargs),
                         {'_expected', '_lock_timeout', 'mcp_service', 'control_identity'})
        self.assertLessEqual(self.request.call_args.kwargs['timeout_seconds'], 2)
        self.assertLessEqual(self.observe.call_args.kwargs['timeout_seconds'], 2)

    def test_old_operation_adapter_stays_bound_while_new_adopted_adapter_can_reconnect(self):
        old = HostMemoryRemote('box', self.record, lambda *args: evidence(revision='d' * 24))
        adopted = self.adopt()
        with self.assertRaises(RemoteProtocolError) as failure:
            old.call('host_memory_status', budget_seconds=1)
        self.assertEqual(failure.exception.code, 'remote_runtime_revision_mismatch')
        new = HostMemoryRemote('box', {**self.record, **adopted}, lambda *args: evidence(revision='d' * 24))
        self.assertEqual(new.call('host_memory_status', budget_seconds=1)['target_identity'], 'c' * 24)

    def test_marker_or_machine_change_cannot_be_called_a_version_update(self):
        for response in (evidence(marker='f' * 24, revision='d' * 24),
                         evidence(identity='f' * 24, revision='d' * 24)):
            with self.subTest(response=response), self.assertRaises(RemoteProtocolError):
                self.adopt(response=response)
            self.writer.assert_not_called()
            self.observe.assert_not_called()

    def test_strict_binding_is_not_replaced_or_broken(self):
        with self.assertRaises(RemoteProtocolError):
            self.adopt(strict=True)
        self.writer.assert_not_called()
        self.observe.assert_not_called()

    def test_unknown_incompatible_or_other_installed_revision_refuses(self):
        for status in ({'installed_runtime_revision': 'd' * 24},
                {**self.status, 'installed_runtime_revision': 'f' * 24},
                {**self.status, 'compatibility': {'ok': False, 'state': 'protocol_newer'}},
                {**self.status, 'control_protocol': {'installed': None}}):
            with self.subTest(status=status), self.assertRaises(RemoteProtocolError):
                self.adopt(status=status)
            self.writer.assert_not_called()

    def test_same_revision_is_noop_without_new_ssh_or_registration_write(self):
        value = self.adopt(response=evidence())
        self.assertFalse(value['revision_cache_changed'])
        self.observe.assert_not_called()
        self.writer.assert_not_called()

    def test_missing_machine_pin_requires_identity_adoption_not_invented_cache_authority(self):
        del self.record['control_identity']
        with self.assertRaises(RemoteProtocolError):
            self.adopt()
        self.request.assert_not_called()
        self.writer.assert_not_called()

    def test_concurrent_registration_writer_is_not_overwritten(self):
        with mock.patch.object(_remote, 'put_remote', side_effect=RuntimeError('remote_registration_changed')) as writer, \
                mock.patch.object(join_revision, 'strict_requested', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'remote_registration_changed'):
                join_revision.adopt_compatible_join_revision('box', self.record,
                    lambda *args, **kwargs: evidence(revision='d' * 24),
                    lambda *args, **kwargs: self.status)
        writer.assert_called_once()
