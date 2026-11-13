"""Read-only target snapshots have no source-stage or process authority."""
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.application import server_cli, server_cli_targets as targets
from sandbox.commands import job_policy
from sandbox.core import _remote
from tests.test_server_cli import OWNER


def snapshot():
    return {'schema_version': 1, 'target_identity': OWNER.target_identity,
        'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision,
        'origin_remote_name': 'box', 'project': 'fixture', 'environment': 'dev',
        'name': 'fixture', 'copy_id': '1234abcd', 'source_commit': '1' * 40,
        'source_origin_commit': '2' * 40, 'source_dir': '/home/fixture/sandbox/runtime/stacks/fixture',
        'compose_files': ['/home/fixture/sandbox/runtime/stacks/fixture/compose.yml'],
        'native_keys': ['box/fixture/dev'],
        'services': [{'name': 'wordpress', 'index': 1, 'container_id': '3' * 64}],
        'selector_digest': 'sha256:' + '4' * 64}


class TargetObservationTests(unittest.TestCase):
    def test_installed_inspection_is_metadata_and_checks_current_receiver_twice(self):
        reader = mock.Mock(return_value=OWNER)
        inspector = mock.Mock(return_value=snapshot())
        with mock.patch.object(server_cli, 'read_current_server_owner', reader), \
                mock.patch.object(targets, 'installed_inspector', return_value=inspector), \
                mock.patch.object(server_cli, 'current_server_cli_context', side_effect=AssertionError('not execution')):
            value = targets.inspect_current_server_target(origin_remote_name='box', name='fixture')
        self.assertTrue(value['ok'])
        self.assertNotEqual(value['source_commit'], value['source_origin_commit'])
        self.assertEqual(reader.call_count, 2)
        inspector.assert_called_once_with(origin_remote_name='box', target=None, name='fixture', environment=None)

    def test_receiver_replacement_refuses_metadata(self):
        with mock.patch.object(server_cli, 'read_current_server_owner', side_effect=[OWNER, object()]), \
                mock.patch.object(targets, 'installed_inspector', return_value=lambda **kwargs: snapshot()):
            with self.assertRaises(server_cli.ServerCLIUnavailable):
                targets.inspect_current_server_target(origin_remote_name='box', name='fixture')

    def test_snapshot_is_closed_and_cannot_change_identity_or_service_partition(self):
        for change in ({'runtime_revision': 'f' * 24}, {'target_identity': 'f' * 24},
                {'ownership_marker': 'f' * 24}, {'schema_version': True}, {'extra': 'field'},
                {'compose_files': []}, {'native_keys': ['same', 'same']},
                {'source_dir': '/root/../fixture'}, {'selector_digest': 'fixture'},
                {'source_commit': '1' * 41}, {'source_origin_commit': '2' * 63},
                {'name': 'folder/name'}, {'copy_id': 'not-copy'},
                {'services': [{'name': 'wordpress', 'index': True, 'container_id': '3' * 64}]}):
            with self.subTest(change=change), self.assertRaises(server_cli.ServerCLIUnavailable):
                targets.validate_target_snapshot({**snapshot(), **change}, owner=OWNER, origin_remote_name='box')

    def observe(self, response, returncode=0):
        record = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
            'control_identity': {'schema_version': 1, 'target_identity': OWNER.target_identity},
            'mcp_service': {'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision}}
        self.ssh = mock.Mock(return_value=SimpleNamespace(returncode=returncode, stdout=json.dumps(response), stderr=''))
        with mock.patch.object(_remote, 'get_remote', return_value=record), \
                mock.patch.object(_remote, 'ssh_run', self.ssh), \
                mock.patch.object(_remote, 'remote_sb_path', return_value=OWNER.executable), \
                mock.patch('sandbox.resources.context.authenticated_target_identity',
                    return_value={'target_identity': OWNER.target_identity}), \
                mock.patch.object(_remote, 'deploy_exact_working_tree', side_effect=AssertionError('no staging')), \
                mock.patch('sandbox.application.job_service.JobService.submit', side_effect=AssertionError('no job')):
            return targets.observe_server_cli_target('box', name='fixture', budget_seconds=2)

    def test_client_uses_fixed_control_command_without_local_project_or_job(self):
        value = self.observe({'ok': True, **snapshot()})
        self.assertEqual(value, snapshot())
        self.ssh.assert_called_once()
        command = self.ssh.call_args.args[1]
        self.assertIn('job-capabilities --inspect-host-target --origin-remote-name box --name fixture', command)
        self.assertNotIn('job-start', command)
        self.assertLessEqual(self.ssh.call_args.kwargs['timeout'], 2)

    def test_wrong_target_response_and_extra_authority_fields_refuse(self):
        for change in ({'name': 'another'}, {'origin_remote_name': 'other'}, {'actor': 'owner'}):
            with self.subTest(change=change), self.assertRaises(server_cli.ServerCLIUnavailable):
                self.observe({'ok': True, **snapshot(), **change})

    def test_server_typed_selector_refusal_keeps_its_code(self):
        # The installed handler prints this envelope and exits 1 on a TargetRefusal.
        for code in ('host_server_target_evidence_unavailable', 'host_target_busy', 'host_target_invalid'):
            with self.subTest(code=code), self.assertRaises(targets.ServerTargetRefused) as caught:
                self.observe({'schema_version': 1, 'ok': False, 'code': code,
                              'error': 'owner target metadata is unavailable; no job was accepted'}, returncode=1)
            self.assertEqual(caught.exception.code, code)
            self.assertIsInstance(caught.exception, server_cli.ServerCLIUnavailable)

    def test_other_server_failures_stay_generic(self):
        for response in ({'schema_version': 1, 'ok': False, 'code': 'server_cli_capability_unavailable'},
                         {'schema_version': 1, 'ok': False, 'code': 'host_exec_unknown_service'},
                         {'schema_version': 1, 'ok': False}):
            with self.subTest(response=response), self.assertRaises(Exception) as caught:
                self.observe(response, returncode=1)
            self.assertNotIsInstance(caught.exception, targets.ServerTargetRefused)

    def test_existing_capability_handler_reports_selector_refusal_code(self):
        from sandbox.hosting.target_resolution import TargetRefusal
        args = SimpleNamespace(inspect_host_target=True, origin_remote_name='box',
                               target='project/dev', name=None, environment=None)
        output = io.StringIO()
        with mock.patch.object(targets, 'inspect_current_server_target',
                side_effect=TargetRefusal('host_server_target_evidence_unavailable', 'missing')), \
                redirect_stdout(output), self.assertRaises(SystemExit):
            job_policy.cmd_job_capabilities(None, args)
        self.assertEqual(json.loads(output.getvalue())['code'], 'host_server_target_evidence_unavailable')

    def test_existing_capability_handler_registers_readonly_inspection_without_config(self):
        args = SimpleNamespace(inspect_host_target=True, origin_remote_name='box',
                               target=None, name='fixture', environment=None)
        output = io.StringIO()
        with mock.patch.object(targets, 'inspect_current_server_target', return_value={'ok': True, **snapshot()}) as inspector, \
                redirect_stdout(output):
            job_policy.cmd_job_capabilities(None, args)
        self.assertEqual(json.loads(output.getvalue()), {'ok': True, **snapshot()})
        inspector.assert_called_once()
