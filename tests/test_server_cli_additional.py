"""Selected/invalid contexts, nested deadlines and bounded local process behavior."""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock

from sandbox.application import server_cli as cli
from sandbox.application.job_service import JobService
from sandbox.core import _paths
from sandbox.jobs.supervisor import _server_cli_parent_reason
from sandbox.services.process import BoundedProcessRunner, ProcessResult
from tests.subprocess_support import synthetic_environment
from tests.test_server_cli import OWNER


def context():
    return cli.ServerCLIContext(1, 'd' * 32, 'original', '/synthetic/parent', 'parent-project',
        'parent-source', 'f' * 40, None, OWNER.target_identity, OWNER.runtime_revision,
        (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(), ('host', 'stack', 'up'),
        'origin-box', MappingProxyType({'front_door': 'nginx', 'mcp_port': 9174}), OWNER)


class SelectedContextTest(unittest.TestCase):
    def test_routing_metadata_rejects_credentials_and_implicit_profile(self):
        for metadata in ({'bearer_token': 'synthetic'}, {'ssh': 'fixture@host'},
                         {'origin_ipv4': 1234}, {'mcp_port': True}, {'front_door': 'default'}):
            with self.subTest(metadata=metadata), self.assertRaises(cli.ServerCLIUnavailable):
                cli.validate_target_metadata(metadata)

    def lookup(self, kind, hint, *, invalid=False):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / 'jobs').mkdir()
            (runtime / 'jobs/registry.sqlite3').write_bytes(b'fixture')
            with mock.patch.object(_paths, 'RUNTIME_DIR', runtime), \
                    mock.patch.object(cli.os, 'environ', {'SANDBOX_DURABLE_JOB_ID': 'd' * 32,
                                                        'SANDBOX_DURABLE_EXECUTION_KIND': hint}), \
                    mock.patch('sandbox.jobs.registry.JobRepository') as repository, \
                    mock.patch.object(cli, 'current_server_cli_context',
                        side_effect=cli.ServerCLIUnavailable('revoked') if invalid else None,
                        return_value=context()) as current:
                repository.return_value.get.return_value = {'kind': kind}
                try:
                    value = cli.server_cli_context_if_selected()
                except cli.ServerCLIUnavailable as error:
                    value = error
                return value, current

    def test_ordinary_durable_execution_keeps_existing_selection(self):
        value, current = self.lookup('exec', 'exec')
        self.assertIsNone(value)
        current.assert_not_called()

    def test_no_job_opens_no_registry(self):
        with mock.patch.object(cli.os, 'environ', {}), \
                mock.patch('sandbox.jobs.registry.JobRepository', side_effect=AssertionError('must stay local')):
            self.assertIsNone(cli.server_cli_context_if_selected())

    def test_selected_invalid_or_revoked_context_never_falls_back(self):
        for kind, hint in (('server-cli', ''), ('server-cli', 'server-cli'), ('exec', 'server-cli')):
            with self.subTest(kind=kind, hint=hint):
                value, current = self.lookup(kind, hint, invalid=True)
                self.assertIsInstance(value, cli.ServerCLIUnavailable)
                current.assert_called_once()

    def test_selected_hint_without_original_job_refuses(self):
        with mock.patch.object(cli.os, 'environ', {'SANDBOX_DURABLE_EXECUTION_KIND': 'server-cli'}):
            with self.assertRaises(cli.ServerCLIUnavailable):
                cli.server_cli_context_if_selected()

    def test_retained_selected_kind_requires_full_proof(self):
        value, current = self.lookup('server-cli', '')
        self.assertEqual(value.owner, OWNER)
        current.assert_called_once()


class NestedServerCLITest(unittest.TestCase):
    def builder(self, *, parent=None, request='original', argv=('host', 'apply')):
        active = context()
        with mock.patch.object(cli, 'validate_owner_current', return_value=active), \
                mock.patch.object(cli, 'installed_cli', return_value=Path(OWNER.executable)):
            return cli.local_server_cli_submission_argv(OWNER, argv, project_dir='/synthetic/child-worktree',
                request_id=request, source_commit='e' * 40, timeout_seconds=900,
                parent_job_id=parent or active.job_id)

    def test_child_root_identities_are_computed_by_supported_admission(self):
        argv = self.builder()
        self.assertNotIn('--source-identity', argv)
        self.assertNotIn('--project-identity', argv)
        self.assertEqual(argv[argv.index('--project-dir') + 1], '/synthetic/child-worktree')
        self.assertEqual(argv[argv.index('--source-commit') + 1], 'e' * 40)
        self.assertEqual(argv[argv.index('--parent-job-id') + 1], 'd' * 32)
        self.assertEqual(argv[argv.index('--') + 1:], ['host', 'apply'])

    def test_nested_deadline_and_closed_execution_policy_are_preserved(self):
        from sandbox.config.runtime import execution_policy_from_wire
        argv = self.builder()
        self.assertLessEqual(int(argv[argv.index('--timeout') + 1]), 30)
        raw = json.loads(base64.b64decode(argv[argv.index('--execution-policy-json') + 1]))
        policy = execution_policy_from_wire(raw)
        self.assertLessEqual(policy.deadline_seconds, 30)
        self.assertEqual(argv[argv.index('--server-cli-origin') + 1], 'origin-box')
        self.assertEqual(json.loads(argv[argv.index('--server-cli-target-metadata') + 1])['front_door'], 'nginx')

    def test_repeat_uses_same_child_request_identity_instead_of_fresh_attempt(self):
        one, two = self.builder(), self.builder()
        self.assertEqual(one[one.index('--request-id') + 1], two[two.index('--request-id') + 1])
        self.assertNotEqual(one[one.index('--request-id') + 1], 'original')

    def test_foreign_parent_handle_refuses_before_submission(self):
        with self.assertRaises(cli.ServerCLIUnavailable):
            self.builder(parent='a' * 32)

    def test_server_cli_parent_with_children_keeps_own_process_lifecycle(self):
        service = JobService(mock.Mock(), None, None)
        service.repository.children.return_value = [{'job_id': 'e' * 32}]
        self.assertFalse(service._is_aggregate({'kind': 'server-cli', 'job_id': 'd' * 32}))
        self.assertTrue(service._is_aggregate({'kind': 'matrix', 'job_id': 'd' * 32}))

    def test_terminal_parent_can_cancel_its_active_child(self):
        parent, child = 'd' * 32, 'e' * 32
        repository = mock.Mock()
        repository.snapshot.return_value = {'job_id': parent, 'kind': 'server-cli', 'lifecycle': 'succeeded'}
        repository.children.return_value = [{'job_id': child, 'lifecycle': 'running'}]
        service = JobService(repository, None, None)
        original = service.cancel
        def cancel(job_id, **kwargs):
            return {'job_id': child, 'lifecycle': 'cancelled'} if job_id == child else original(job_id, **kwargs)
        with mock.patch.object(service, 'cancel', side_effect=cancel) as invoke:
            result = service.cancel(parent)
        self.assertEqual(result['cancelled_children'][0]['job_id'], child)
        self.assertEqual(invoke.call_count, 2)

    def test_active_nested_job_blocks_parent_materialization_cleanup(self):
        repository, storage = mock.Mock(), mock.Mock()
        repository.get.return_value = {'kind': 'server-cli', 'lifecycle': 'succeeded'}
        repository.children.return_value = [{'lifecycle': 'running'}]
        service = JobService(repository, storage, None)
        with self.assertRaisesRegex(RuntimeError, 'nested_job_cleanup_protected'):
            service.cleanup('d' * 32)
        storage.job_dir.assert_not_called()

    def test_parent_deadline_and_cancellation_are_observed_before_effects(self):
        repository = mock.Mock()
        descriptor = {'server_cli_parent': {'job_id': 'd' * 32,
            'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}}
        for state in ('running', 'succeeded'):
            repository.get.return_value = {'kind': 'server-cli', 'lifecycle': state}
            self.assertIsNone(_server_cli_parent_reason(descriptor, repository))
        for state in ('cancelled', 'cancelling', 'failed', 'timed_out', 'interrupted'):
            repository.get.return_value = {'kind': 'server-cli', 'lifecycle': state}
            self.assertEqual(_server_cli_parent_reason(descriptor, repository), 'parent_cancelled_or_unavailable')
        descriptor['server_cli_parent']['deadline_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.assertEqual(_server_cli_parent_reason(descriptor, repository), 'parent_deadline_exceeded')


class ReceiverRoutingObservationTest(unittest.TestCase):
    def observe(self, addresses):
        payload = json.dumps([{'addr_info': [{'scope': 'global', 'local': value} for value in addresses]}])
        result = ProcessResult(('ip',), 0, payload, '')
        with mock.patch.object(cli, 'owner_environment', return_value={'HOME': OWNER.home}), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run', return_value=result) as run:
            value = cli._receiver_routing_metadata(OWNER)
        self.assertEqual(run.call_args.args[0], ['ip', '-json', 'address', 'show', 'scope', 'global'])
        self.assertEqual(run.call_args.kwargs['timeout'], 3)
        return value

    def test_unique_actual_public_address_excludes_private_tailnet(self):
        self.assertEqual(self.observe(['93.184.216.34', '100.64.1.2']), {'origin_ipv4': '93.184.216.34'})

    def test_multiple_actual_origins_do_not_guess(self):
        self.assertEqual(self.observe(['93.184.216.34', '8.8.8.8']), {})

    def test_missing_public_evidence_does_not_invent_origin(self):
        self.assertEqual(self.observe(['100.64.1.2', '10.0.0.1']), {})


class BoundedOwnerChannelTest(unittest.TestCase):
    def test_stdin_bearing_cli_refuses_before_lookup_or_acceptance(self):
        from sandbox.core import _remote
        for argv in (('host', 'exec', '--', 'cat'), ('hermes', 'repo', 'auth', '--token-stdin')):
            with self.subTest(argv=argv), mock.patch.object(_remote, 'get_remote') as lookup:
                with self.assertRaises(cli.ServerCLIStdinUnavailable):
                    cli.dispatch_server_cli('box', argv, project_dir='/synthetic/project', request_id='original')
                lookup.assert_not_called()

    def test_existing_stdin_fd_and_session_selection_are_observable(self):
        with tempfile.TemporaryFile() as source:
            source.write(b'bounded fixture'); source.seek(0)
            result = BoundedProcessRunner(max_output=512).run(
                [sys.executable, '-c', 'import os,sys,json; print(json.dumps([os.getpgrp(),sys.stdin.read()]))'],
                env=synthetic_environment(), stdin=source.fileno(), start_new_session=False, timeout=3)
        self.assertEqual(result.returncode, 0)
        group, body = json.loads(result.stdout)
        self.assertEqual(group, os.getpgrp())
        self.assertEqual(body, 'bounded fixture')

    def test_boolean_or_closed_stdin_fd_refuses_before_spawn(self):
        with mock.patch('sandbox.services.process.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                BoundedProcessRunner().run(['fixture'], stdin=True)
            with self.assertRaises(OSError):
                BoundedProcessRunner().run(['fixture'], stdin=987654)
        launch.assert_not_called()

    def test_timeout_is_unknown_and_never_replayed_or_called_clean_termination(self):
        active = context()
        result = ProcessResult(('/bin/sh', '-c', 'single-effect'), 124, '', '', termination_reason='timeout')
        with mock.patch.object(cli, 'validate_owner_current', return_value=active), \
                mock.patch.object(cli, 'owner_environment', return_value={'HOME': OWNER.home}), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run', return_value=result) as run:
            with self.assertRaises(cli.ServerCLIEffectUnknown) as failure:
                cli.owner_process(OWNER, 'single-effect', timeout=1)
        self.assertTrue(failure.exception.completion_unknown)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(run.call_args.kwargs['start_new_session'])

    def test_anonymous_input_fd_delivers_bytes_without_environment_copy(self):
        active, observed = context(), []
        def run(argv, **kwargs):
            observed.append(os.read(kwargs['stdin'], 100))
            return ProcessResult(tuple(argv), 0, 'receipt', '')
        with mock.patch.object(cli, 'validate_owner_current', return_value=active), \
                mock.patch.object(cli, 'owner_environment', return_value={'HOME': OWNER.home}), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run', side_effect=run):
            result = cli.owner_process(OWNER, 'single-effect', input_data=b'synthetic-input')
        self.assertEqual(observed, [b'synthetic-input'])
        self.assertEqual(result.stdout, b'receipt')

    def test_post_effect_context_change_remains_unknown(self):
        result = ProcessResult(('/bin/sh', '-c', 'single-effect'), 0, 'done', '')
        with mock.patch.object(cli, 'validate_owner_current',
                side_effect=[context(), cli.ServerCLIUnavailable('revoked')]), \
                mock.patch.object(cli, 'owner_environment', return_value={'HOME': OWNER.home}), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run', return_value=result) as run:
            with self.assertRaises(cli.ServerCLIEffectUnknown):
                cli.owner_process(OWNER, 'single-effect')
        self.assertEqual(run.call_count, 1)
