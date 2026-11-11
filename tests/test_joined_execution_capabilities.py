import copy
import unittest
from unittest import mock

from sandbox.remote_runtime.execution_capabilities import adopt_execution_capabilities, ExecutionCapabilityUnavailable
from sandbox.application.target_service import TargetService, TargetResolutionError
from sandbox.jobs.models import TargetRequest


class JoinedExecutionCapabilityTest(unittest.TestCase):
    def setUp(self):
        self.record = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
            'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24},
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        self.observed = {'ok': True, 'schema_version': 1, 'target_identity': 'c' * 24,
            'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24,
            'capabilities': ['job.exec', 'job.execution-policy.v1', 'job.server-cli.v1']}

    def test_actual_installed_contract_is_adopted_by_cas_without_bearer_or_role(self):
        request = mock.Mock(return_value=self.observed)
        with mock.patch('sandbox.core._remote.put_remote', side_effect=lambda name, **fields:
                {**self.record, 'capabilities': fields['capabilities']}) as writer:
            result = adopt_execution_capabilities('box', self.record, request)
        self.assertIn('job.exec', result['capabilities'])
        self.assertEqual(writer.call_args.kwargs['_expected'], self.record)
        self.assertNotIn('bearer_token', writer.call_args.kwargs)
        self.assertNotIn('role', writer.call_args.kwargs)
        request.assert_called_once_with(['job-capabilities'])

    def test_unavailable_runtime_is_distinct_from_access_denial_and_never_writes(self):
        with mock.patch('sandbox.core._remote.put_remote') as writer:
            with self.assertRaises(ExecutionCapabilityUnavailable) as failure:
                adopt_execution_capabilities('box', self.record, mock.Mock(side_effect=RuntimeError('old CLI')))
        self.assertEqual(failure.exception.code, 'remote_execution_capability_unavailable')
        writer.assert_not_called()

    def test_changed_identity_unknown_contract_and_duplicate_contract_refuse(self):
        for changes in ({'target_identity': 'd' * 24}, {'ownership_marker': 'd' * 24},
                        {'runtime_revision': 'd' * 24}, {'schema_version': True},
                        {'capabilities': ['job.exec', 'future.fake']},
                        {'capabilities': ['job.exec', 'job.exec', 'job.execution-policy.v1']}):
            with self.subTest(changes=changes), mock.patch('sandbox.core._remote.put_remote') as writer:
                with self.assertRaises(ExecutionCapabilityUnavailable):
                    adopt_execution_capabilities('box', self.record, lambda argv: {**self.observed, **changes})
                writer.assert_not_called()

    def test_nonjoined_owner_registration_does_not_probe_or_change(self):
        owner = {'provisioned': True, 'control_transport': 'tailscale'}
        request = mock.Mock(side_effect=AssertionError('unexpected probe'))
        self.assertIs(adopt_execution_capabilities('owner', owner, request), owner)
        request.assert_not_called()

    def service(self, discover):
        return TargetService(config_loader=lambda root: {'root': '/synthetic/project', 'runtime': {'default': 'local'}},
            remote_lookup=lambda name: copy.deepcopy(self.record), capability_discovery=discover)

    def test_ordinary_joined_job_selection_discovers_missing_execution_contract(self):
        discover = mock.Mock(return_value={**self.record, 'capabilities': ['job.exec']})
        target = self.service(discover).resolve(TargetRequest('/synthetic/project', remote='box', required_capability='job.exec'))
        self.assertEqual(target.remote_name, 'box')
        discover.assert_called_once()

    def test_failed_installed_discovery_never_becomes_controller_permission_request(self):
        with self.assertRaises(TargetResolutionError) as failure:
            self.service(mock.Mock(side_effect=ExecutionCapabilityUnavailable('not installed'))).resolve(
                TargetRequest('/synthetic/project', remote='box', required_capability='job.exec'))
        self.assertEqual(failure.exception.code, 'remote_execution_capability_unavailable')

    def test_metadata_only_target_selection_performs_no_execution_probe(self):
        discover = mock.Mock(side_effect=AssertionError('not an execution'))
        self.assertEqual(self.service(discover).resolve(TargetRequest('/synthetic/project', remote='box')).remote_name, 'box')
        discover.assert_not_called()
