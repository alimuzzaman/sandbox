import contextlib
import copy
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.remote_runtime.execution_capabilities import adopt_execution_capabilities, ExecutionCapabilityUnavailable
from sandbox.application.target_service import TargetService, TargetResolutionError
from sandbox.jobs.models import TargetRequest


class JoinedExecutionCapabilityTest(unittest.TestCase):
    def test_real_installed_stack_ref_contract_is_accepted_and_removed_on_downgrade(self):
        from sandbox.hosting.server_cli_dispatch import STACK_REF_CONTRACT
        observed = {**self.observed, 'capabilities': [*self.observed['capabilities'],
                    'job.source-content.v1', STACK_REF_CONTRACT]}
        with mock.patch('sandbox.core._remote.put_remote',
                        side_effect=lambda name, **fields: {**self.record,
                            'capabilities': fields['capabilities']}) as writer:
            adopted = adopt_execution_capabilities('box', self.record, lambda argv: observed)
            self.assertIn(STACK_REF_CONTRACT, adopted['capabilities'])
            downgraded = adopt_execution_capabilities('box', adopted, lambda argv: self.observed)
        self.assertNotIn(STACK_REF_CONTRACT, downgraded['capabilities'])
        self.assertEqual(writer.call_count, 2)

    def test_every_contract_the_installed_server_can_advertise_is_adopted(self):
        # The server and client ship from one revision; a contract the server
        # advertises must never make its own client refuse every job.
        from sandbox.application import server_cli
        owner = SimpleNamespace(target_identity='c' * 24, ownership_marker='a' * 24,
                                runtime_revision='b' * 24, executable='/x/sb', uid=1, home='/x')
        available = [
            'sandbox.application.server_cli_targets.installed_inspector',
            'sandbox.application.server_cli_delivery.contract_available',
            'sandbox.application.server_cli_exec.exec_contract_available',
            'sandbox.application.server_cli_exec.capture_contract_available',
            'sandbox.workspaces.content.source_content_available',
            'sandbox.hosting.server_cli_dispatch.stack_ref_contract_available',
            'sandbox.commands.preview_server.preview_contract_available',
            'sandbox.application.server_cli_source_repair.contract_available',
            'sandbox.hosting.publisher_proof.contract_available',
        ]
        with contextlib.ExitStack() as stack:
            for target in available:
                stack.enter_context(mock.patch(target, return_value=True))
            stack.enter_context(mock.patch.object(server_cli, '_current_owner', return_value=owner))
            stack.enter_context(mock.patch.object(server_cli, '_receiver_routing_metadata', return_value={}))
            observed = server_cli.capabilities()
        self.assertIn('job.publisher-proof.v1', observed['capabilities'])
        with mock.patch('sandbox.core._remote.put_remote',
                        side_effect=lambda name, **fields: {**self.record,
                            'capabilities': fields['capabilities']}):
            adopted = adopt_execution_capabilities('box', self.record, lambda argv: observed)
        self.assertEqual(set(observed['capabilities']), set(adopted['capabilities']))

    def setUp(self):
        self.record = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
            'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24},
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        self.observed = {'ok': True, 'schema_version': 1, 'target_identity': 'c' * 24,
            'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24,
            'capabilities': ['job.exec', 'job.execution-policy.v1', 'job.acceptance-receipt.v1', 'job.server-cli.v1']}

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

    def test_current_discovery_removes_stale_known_contract_but_preserves_unrelated_capability(self):
        record = {**self.record, 'capabilities': ['status', 'job.exec', 'job.execution-policy.v1',
                                                   'job.acceptance-receipt.v1']}
        observed = {**self.observed, 'capabilities': ['job.exec', 'job.execution-policy.v1']}
        with mock.patch('sandbox.core._remote.put_remote', side_effect=lambda name, **fields:
                {**record, 'capabilities': fields['capabilities']}) as writer:
            result = adopt_execution_capabilities('box', record, lambda argv: observed)
        self.assertEqual(result['capabilities'], ['job.exec', 'job.execution-policy.v1', 'status'])
        writer.assert_called_once()

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

    def composed_selection(self, response=None, error=None):
        """Use the real application->remote->transport->validator composition."""
        from sandbox.application.context import target_service
        from sandbox.core import _remote
        record = {**copy.deepcopy(self.record), 'control_access': 'tailnet', 'front_door': 'nginx'}
        config = SimpleNamespace(load_project_config=lambda root: {
            'root': '/synthetic/project', 'runtime': {'default': 'local'}})
        with mock.patch.dict('sys.modules', {'sandbox_core': config}), \
                mock.patch.object(_remote, '_remote_block', return_value={'box': record}), \
                mock.patch.object(_remote, 'put_remote', side_effect=lambda name, **fields:
                    {**record, 'capabilities': fields['capabilities']}) as writer, \
                mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control',
                    return_value=response if response is not None else self.observed,
                    side_effect=error) as control, \
                mock.patch.object(_remote, 'deploy_exact_working_tree', side_effect=AssertionError('no staging')) as deploy, \
                mock.patch('sandbox.jobs.registry.JobRepository', side_effect=AssertionError('no ledger/job acceptance')):
            self.writer, self.control, self.deploy = writer, control, deploy
            return target_service().resolve(TargetRequest('/synthetic/project', remote='box',
                required_capability='job.exec'))

    def test_actual_application_selection_discovers_contract_before_any_staging_or_acceptance(self):
        target = self.composed_selection()
        self.assertEqual(target.remote_name, 'box')
        self.control.assert_called_once_with('box', ['job-capabilities'], timeout=15)
        self.writer.assert_called_once()
        written = self.writer.call_args.kwargs
        self.assertEqual(written['_expected']['control_identity'], self.record['control_identity'])
        self.assertEqual(written['_expected']['mcp_service'], self.record['mcp_service'])
        self.assertEqual(set(written['capabilities']), set(self.observed['capabilities']))
        self.assertNotIn('bearer_token', written)
        self.assertNotIn('role', written)
        self.deploy.assert_not_called()

    def test_actual_application_missing_installed_contract_never_becomes_owner_denial(self):
        from sandbox.transports.remote_jobs import RemoteJobTransportError
        with self.assertRaises(TargetResolutionError) as failure:
            self.composed_selection(error=RemoteJobTransportError('installed CLI lacks discovery'))
        self.assertEqual(failure.exception.code, 'remote_execution_capability_unavailable')
        self.writer.assert_not_called()
        self.deploy.assert_not_called()

    def test_actual_application_rejects_changed_machine_before_capability_publication(self):
        with self.assertRaises(TargetResolutionError) as failure:
            self.composed_selection(response={**self.observed, 'target_identity': 'f' * 24})
        self.assertEqual(failure.exception.code, 'remote_execution_capability_unavailable')
        self.writer.assert_not_called()
        self.deploy.assert_not_called()
