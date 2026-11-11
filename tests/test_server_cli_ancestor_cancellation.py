"""Real SQLite ancestry fences survive succeeded intermediates and admission races."""
from datetime import datetime, timedelta, timezone
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.application import server_cli
from sandbox.application.job_service import JobService
from sandbox.core import _paths
from sandbox.jobs.manifest import JobComponentRegistry
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.server_cli_lineage import check_server_cli_lineage, ServerCliLineageError
from sandbox.jobs import server_cli_lineage
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.supervisor import _server_cli_parent_reason, run_descriptor


class AncestorCancellationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        self.storage = JobStorage(self.runtime, free_disk_reserve=0)
        self.repository = JobRepository(self.storage.root / 'registry.sqlite3')
        self.addCleanup(self.repository.close)
        components = JobComponentRegistry()
        components.register('server_cli', server_cli.FixedServerCLI(), owner='fixture')
        self.launch = mock.Mock(side_effect=AssertionError('no effect launch'))
        self.service = JobService(self.repository, self.storage, components, launcher=self.launch)
        self.owner = server_cli.ServerCliOwner(os.geteuid(), str(self.runtime), str(self.runtime.parent),
            str(self.runtime / 'sb'), 'c' * 24, 'a' * 24, 'b' * 24)
        self.deadline = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
        self.root = self.accept('root', 100)
        self.child = self.accept('child', 200, self.root)

    def submission(self, label, parent=None):
        proof = {'server_cli.target_identity': self.owner.target_identity,
            'server_cli.ownership_marker': self.owner.ownership_marker,
            'server_cli.runtime_revision': self.owner.runtime_revision,
            'server_cli.remote_name': 'box', 'server_cli.target_metadata': '{"front_door":"nginx"}',
            'server_cli.absolute_deadline_at': self.deadline}
        if parent is not None:
            proof['server_cli.parent_deadline_at'] = self.deadline
        return JobSubmission('server-cli', str(self.runtime), 'project', 'local', 'fixture',
            (self.owner.executable, 'host', 'stack', 'list'), 120,
            SourceIdentity('sha256:' + 'e' * 64, 'f' * 40), request_id='lineage-' + label,
            parent_job_id=parent, execution_policy_provenance=proof)

    def accept(self, label, pid, parent=None, state='running'):
        row, _ = self.repository.accept(self.submission(label, parent))
        job_id = row['job_id']
        self.storage.job_dir(job_id, create=True)
        self.repository.transition(job_id, state)
        if state == 'running':
            self.repository.put_process_identity(job_id, host_boot_id='fixture-boot',
                supervisor_pid=pid + 1, supervisor_start_identity='supervisor-start',
                supervisor_nonce_hash='a' * 64, child_pid=pid, child_pgid=pid,
                child_start_identity='child-start')
        return job_id

    def descriptor(self, job_id, parent):
        return {'job_id': job_id, 'registry_path': str(self.repository.path),
            'runtime_dir': str(self.runtime), 'free_disk_reserve': 0, 'nonce_hash': 'a' * 64,
            'authoritative_context': {'execution_kind': 'server-cli'},
            'server_cli_parent': {'job_id': parent, 'deadline_at': self.deadline}}

    def cancel_root(self):
        with mock.patch('sandbox.application.job_service.verify_owned_process_identity', return_value=True), \
                mock.patch('sandbox.application.job_service.signal_owned_process_group', return_value=True) as signal, \
                mock.patch.object(self.service, '_finalize_terminal_workspace'):
            result = self.service.cancel(self.root)
        return result, signal

    def test_running_grandchild_behind_succeeded_child_is_cancelled_without_signalling_terminal_pid(self):
        grandchild = self.accept('grandchild', 300, self.child)
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        result, signal = self.cancel_root()
        self.assertEqual(result['lifecycle'], 'cancelling')
        self.assertEqual(self.repository.get(self.child)['lifecycle'], 'succeeded')
        self.assertEqual(self.repository.get(grandchild)['lifecycle'], 'cancelling')
        self.assertEqual({call.args[0].process_group_id for call in signal.call_args_list}, {100, 300})
        self.assertNotIn(200, [call.args[0].pid for call in signal.call_args_list])
        self.assertEqual(_server_cli_parent_reason(self.descriptor(grandchild, self.child), self.repository),
                         'parent_cancelled_or_unavailable')

    def test_durable_intent_blocks_queued_launch_before_root_process_is_signalled(self):
        grandchild = self.accept('queued', 300, self.child, state='queued')
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        self.repository.request_server_cli_cancellation(self.root)
        self.assertEqual(self.repository.get(self.root)['lifecycle'], 'running')
        path = self.storage.write_json_atomic(grandchild, 'descriptor.json', self.descriptor(grandchild, self.child))
        with mock.patch('sandbox.jobs.supervisor.capture_process_identity', return_value=SimpleNamespace(
                host_boot_id='fixture-boot', pid=999, start_identity='supervisor-start')), \
                mock.patch('sandbox.jobs.supervisor.subprocess.Popen', side_effect=AssertionError('no effect dispatch')) as spawn, \
                mock.patch('sandbox.application.context.drain_after_release'):
            result = run_descriptor(path)
        self.assertEqual(result, 130)
        self.assertEqual(self.repository.get(grandchild)['lifecycle'], 'cancelled')
        spawn.assert_not_called()

    def test_parent_validation_before_cancellation_cannot_authorize_late_admission(self):
        with mock.patch.object(_paths, 'RUNTIME_DIR', self.runtime), \
                mock.patch('sandbox.jobs.process.capture_process_identity', return_value=SimpleNamespace(
                    host_boot_id='fixture-boot', start_identity='child-start')), \
                mock.patch.object(server_cli.os, 'getppid', return_value=200):
            inherited = server_cli.validate_nested_parent(self.child, target_identity=self.owner.target_identity,
                marker=self.owner.ownership_marker, revision=self.owner.runtime_revision,
                origin_remote_name='box', timeout_seconds=120)
        self.assertEqual(inherited, self.deadline)
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        # A separate connection commits the cancellation fence after the real
        # kernel-parent validation but before the final acceptance transaction.
        other = JobRepository(self.repository.path)
        try:
            other.request_server_cli_cancellation(self.root)
        finally:
            other.close()
        before = len(self.repository.list())
        with mock.patch.object(server_cli, 'installed_cli', return_value=Path(self.owner.executable)), \
                mock.patch.object(server_cli.FixedServerCLI, 'environment', return_value={}):
            with self.assertRaises(ServerCliLineageError):
                self.service.submit(self.submission('late', self.child))
        self.assertEqual(len(self.repository.list()), before)
        self.launch.assert_not_called()

    def test_succeeded_intermediate_without_cancellation_preserves_detached_workflow_and_deadline(self):
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        grandchild = self.accept('detached', 300, self.child, state='queued')
        self.assertIsNone(_server_cli_parent_reason(self.descriptor(grandchild, self.child), self.repository))
        deadline = check_server_cli_lineage(self.repository.server_cli_ancestry(grandchild), pending_self=True)
        self.assertEqual(deadline.isoformat(), self.deadline)
        self.assertEqual(self.repository.get(self.child)['lifecycle'], 'succeeded')

    def test_later_descriptor_deadline_cannot_extend_original_root_cutoff(self):
        grandchild = self.accept('deadline', 300, self.child, state='queued')
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        descriptor = self.descriptor(grandchild, self.child)
        descriptor['server_cli_parent']['deadline_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        original = datetime.fromisoformat(self.deadline)
        class AfterOriginalDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return original + timedelta(seconds=1)
        with mock.patch.object(server_cli_lineage, 'datetime', AfterOriginalDeadline):
            self.assertEqual(_server_cli_parent_reason(descriptor, self.repository), 'parent_deadline_exceeded')
            with self.assertRaises(ServerCliLineageError):
                self.repository.accept(self.submission('after-original-deadline', self.child))

    def test_current_owner_context_refuses_cancelled_root_even_with_valid_child_process_identity(self):
        grandchild = self.accept('context', 300, self.child)
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        def identity(pid):
            return SimpleNamespace(host_boot_id='fixture-boot',
                start_identity='child-start' if pid == 300 else 'supervisor-start')
        with mock.patch.object(_paths, 'RUNTIME_DIR', self.runtime), \
                mock.patch.object(server_cli.os, 'environ', {'SANDBOX_DURABLE_JOB_ID': grandchild}), \
                mock.patch.object(server_cli.os, 'getpid', return_value=300), \
                mock.patch.object(server_cli.os, 'getppid', return_value=301), \
                mock.patch.object(server_cli, 'installed_cli', return_value=Path(self.owner.executable)), \
                mock.patch.object(server_cli, '_current_owner', return_value=self.owner), \
                mock.patch('sandbox.jobs.process.capture_process_identity', side_effect=identity), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run', side_effect=AssertionError('no effect')) as run:
            context = server_cli.current_server_cli_context()
            self.assertEqual(context.deadline_at, self.deadline)
            self.repository.request_server_cli_cancellation(self.root)
            with self.assertRaises(server_cli.ServerCLIUnavailable):
                server_cli.current_server_cli_context()
            with self.assertRaises(server_cli.ServerCLIUnavailable):
                server_cli.owner_process(self.owner, 'synthetic-effect')
        run.assert_not_called()

    def test_terminal_root_cancellation_is_durable_and_never_signals_its_reused_pid(self):
        self.repository.transition(self.root, 'succeeded', exit_code=0)
        result, signal = self.cancel_root()
        self.assertEqual(result['lifecycle'], 'succeeded')
        self.assertEqual({call.args[0].process_group_id for call in signal.call_args_list}, {200})
        with self.assertRaises(ServerCliLineageError):
            self.repository.accept(self.submission('after-root-success-cancel', self.child))

    def test_existing_receipt_is_visible_after_fence_without_new_admission_or_launch(self):
        original = self.submission('receipt', self.child)
        row, _ = self.repository.accept(original)
        self.repository.request_server_cli_cancellation(self.root)
        repeated, replay = self.repository.accept(original)
        self.assertTrue(replay)
        self.assertEqual(repeated['job_id'], row['job_id'])
        self.launch.assert_not_called()

    def test_terminal_root_without_current_children_still_fences_late_nested_admission(self):
        self.repository.transition(self.child, 'succeeded', exit_code=0)
        self.repository.transition(self.root, 'succeeded', exit_code=0)
        with self.assertRaisesRegex(RuntimeError, 'already_terminal'):
            self.service.cancel(self.root)
        self.assertTrue(self.repository.server_cli_ancestry(self.root)[0]['server_cli_cancel_requested'])
        with self.assertRaises(ServerCliLineageError):
            self.repository.accept(self.submission('after-empty-traversal', self.child))
        self.launch.assert_not_called()

    def test_actual_job_start_reports_late_admission_refusal_without_claiming_no_historical_receipt(self):
        from sandbox.commands import jobs_runtime
        parser = argparse.ArgumentParser()
        jobs_runtime.configure_start_parser(parser)
        args = parser.parse_args(['--local', '--server-cli', '--parent-job-id', self.child,
            '--server-cli-target', self.owner.target_identity, '--server-cli-marker', self.owner.ownership_marker,
            '--server-cli-revision', self.owner.runtime_revision, '--server-cli-origin', 'box',
            '--server-cli-target-metadata', '{}', '--request-id', 'late-cli-original', '--timeout', '120',
            '--project-dir', str(self.runtime), '--source-commit', 'f' * 40, '--json', '--', 'host', 'stack', 'list'])
        target = SimpleNamespace(project_root=str(self.runtime), project_identity='project', kind='local',
            remote_name=None, workspace_label='fixture', runtime_policy={})
        resolver = mock.Mock()
        resolver.resolve.return_value = target
        actual_validate = server_cli.validate_nested_parent
        def validate_then_cancel(*values, **options):
            result = actual_validate(*values, **options)
            self.repository.transition(self.child, 'succeeded', exit_code=0)
            self.repository.request_server_cli_cancellation(self.root)
            return result
        output = io.StringIO()
        with mock.patch.object(_paths, 'RUNTIME_DIR', self.runtime), \
                mock.patch.object(server_cli, 'capabilities', return_value={
                    'target_identity': self.owner.target_identity, 'ownership_marker': self.owner.ownership_marker,
                    'runtime_revision': self.owner.runtime_revision}), \
                mock.patch.object(server_cli, 'installed_cli', return_value=Path(self.owner.executable)), \
                mock.patch.object(server_cli.FixedServerCLI, 'environment', return_value={}), \
                mock.patch.object(server_cli, 'validate_nested_parent', side_effect=validate_then_cancel), \
                mock.patch('sandbox.jobs.process.capture_process_identity', return_value=SimpleNamespace(
                    host_boot_id='fixture-boot', start_identity='child-start')), \
                mock.patch.object(server_cli.os, 'getppid', return_value=200), \
                mock.patch.object(jobs_runtime, 'durable_job_dependencies', return_value={
                    'target_service': resolver, 'job_service': self.service}), \
                mock.patch.object(jobs_runtime, '_require_local_executable'), redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                jobs_runtime.cmd_job_start(None, args)
        self.assertEqual(stopped.exception.code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['code'], 'server_cli_ancestor_refused')
        self.assertEqual(result['acceptance'], 'not_started_by_this_attempt')
        self.assertEqual(result['request_id'], 'late-cli-original')
        self.assertTrue(result['receipt_lookup_required'])
        self.assertNotIn('job_id', result)
        self.assertEqual(len(self.repository.list()), 2)
        self.launch.assert_not_called()
