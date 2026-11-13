"""New reads reconnect compatible versions; retained effects remain bound."""
import copy
import unittest
from unittest import mock

from sandbox.core import _remote
from sandbox.resources import context
from sandbox.resources.host_memory.remote import HostMemoryRemote, RemoteProtocolError
from sandbox.remote_runtime import join_revision
from sandbox.remote_runtime.protocol import local_protocol
from tests.test_remote_join_identity import evidence


class JoinedRevisionReconnectTests(unittest.TestCase):
    def setUp(self):
        self.record = {'joined': True, 'control_access': 'tailnet', 'control_transport': 'tailscale',
            '_remote_name': 'box', 'front_door': 'nginx',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24},
            'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24}}
        self.status = {'installed_runtime_revision': 'd' * 24,
            **_remote._runtime_compatibility_fields('e' * 24, 'd' * 24,
                local_protocol().encode(), determinate=True)}
        for patch in (
            mock.patch.object(_remote, 'get_remote', return_value=self.record),
            mock.patch.object(_remote, '_remote_mcp_runtime_revision', return_value='e' * 24),
            mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False),
            mock.patch.object(join_revision, 'strict_requested', return_value=False)):
            patch.start()
            self.addCleanup(patch.stop)
        self.request = mock.Mock(return_value=evidence(revision='d' * 24))
        self.ssh = mock.Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(self.status))
        self.writer = mock.Mock()
        for patch in (
            mock.patch.object(_remote, 'remote_host_memory_request', self.request),
            mock.patch.object(_remote, '_remote_mcp_service_status_via_ssh', self.ssh),
            mock.patch.object(_remote, 'put_remote', self.writer),
            mock.patch('sandbox.remote_runtime.pins.PinStore', side_effect=AssertionError('no pin changes'))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_actual_identity_and_compatibility_path_adopts_once_without_manual_refresh(self):
        original = copy.deepcopy(self.record)
        value = context.authenticated_target_identity('box', budget_seconds=2)
        self.assertEqual(value['target_identity'], 'c' * 24)
        self.writer.assert_called_once()
        self.assertEqual(self.writer.call_args.kwargs['_expected'], original)
        self.assertEqual(self.writer.call_args.kwargs['mcp_service']['runtime_revision'], 'd' * 24)
        self.assertEqual(self.record, original)
        for call in self.request.call_args_list:
            self.assertLessEqual(call.kwargs['timeout_seconds'], 2)
        for call in self.ssh.call_args_list:
            self.assertLessEqual(call.kwargs['timeout_seconds'], 2)

    def test_ordinary_service_status_reconnects_and_preserves_truthful_admission(self):
        value = _remote.remote_mcp_service_status(self.record, timeout_seconds=2)
        self.assertTrue(value['authenticated'])
        self.assertEqual(value['control_admission']['state'], 'admitted')
        self.writer.assert_called_once()

    def test_compatibility_observation_alone_does_not_rebind_retained_adapter(self):
        adapter = HostMemoryRemote('box', self.record, lambda *args: evidence(revision='d' * 24))
        context._require_compatible_runtime('box', self.record, timeout_seconds=2)
        self.writer.assert_not_called()
        with self.assertRaises(RemoteProtocolError) as failure:
            adapter.call('host_memory_status', budget_seconds=1)
        self.assertEqual(failure.exception.code, 'remote_runtime_revision_mismatch')

    def test_machine_or_marker_change_never_reconnects(self):
        for changed in (evidence(identity='f' * 24, revision='d' * 24),
                        evidence(marker='f' * 24, revision='d' * 24)):
            with self.subTest(changed=changed):
                self.request.return_value = changed
                with self.assertRaises(RemoteProtocolError):
                    context.authenticated_target_identity('box', budget_seconds=2)
                self.writer.assert_not_called()

    def test_strict_revision_and_expired_budget_do_not_publish(self):
        with mock.patch.object(context, '_require_compatible_runtime'), \
                mock.patch.object(join_revision, 'strict_requested', return_value=True):
            with self.assertRaises(RemoteProtocolError):
                context.authenticated_target_identity('box', budget_seconds=2)
        self.writer.assert_not_called()
        # The reconnect helper itself enforces a finite positive budget.
        with self.assertRaises(RemoteProtocolError):
            join_revision.reconnect_join_record('box', self.record, budget_seconds=0)
        self.writer.assert_not_called()
