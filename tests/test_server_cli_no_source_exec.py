"""Fixed receiver source selection and original stdin before durable acceptance."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.application import server_cli_exec as execute
from sandbox.application.server_cli import ServerCLIUnavailable
from sandbox.core import _remote
from sandbox.jobs.supervisor import _server_cli_deadline_remaining
from tests.test_server_cli import OWNER
from tests.test_server_cli_target_observation import snapshot


def selector(data=b'fixture'):
    return {'schema_version': 1, 'origin_remote_name': 'box', 'target': 'fixture/dev',
        'name': None, 'environment': 'dev', 'selector_digest': snapshot()['selector_digest'],
        'service': 'wordpress', 'target_identity': OWNER.target_identity,
        'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision,
        'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(),
        'target_metadata': {'front_door': 'nginx'}, 'stdin_sha256': hashlib.sha256(data).hexdigest(),
        'stdin_size': len(data)}


class NoSourceReceiverTests(unittest.TestCase):
    def receive(self, *, changes=None, input_data=b'fixture', observations=None, capture_available=False):
        value = {**selector(), **(changes or {})}
        args = SimpleNamespace(server_cli=True, local=True, remote=None, request_id='original-exec',
            parent_job_id=None, command=['--', 'cat'], server_cli_exec_selector=json.dumps(value))
        self.service = mock.Mock()
        self.captured = {}
        def submit(submission, *, anonymous_input):
            self.captured['submission'] = submission
            self.captured['input'] = os.read(anonymous_input.fileno(), 4096)
            self.captured['nlink'] = os.fstat(anonymous_input.fileno()).st_nlink
            return {'ok': True, 'job_id': 'a' * 32, 'status': 'accepted'}
        self.service.submit.side_effect = submit
        with tempfile.TemporaryFile('w+b') as original:
            original.write(input_data)
            original.seek(0)
            with mock.patch.object(execute, 'read_current_server_owner', return_value=OWNER), \
                    mock.patch.object(execute, 'exec_contract_available', return_value=True), \
                    mock.patch.object(execute, 'capture_contract_available', return_value=capture_available), \
                    mock.patch.object(execute, 'inspect_current_server_target',
                        side_effect=observations or [{'ok': True, **snapshot()}, {'ok': True, **snapshot()}]), \
                    mock.patch('sandbox.application.context.durable_job_dependencies', return_value={'job_service': self.service}), \
                    mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False):
                return execute.receive_server_cli_exec(args, stdin_fd=original.fileno())

    def test_receiver_selects_measured_source_and_binds_original_input_and_selector(self):
        receipt = self.receive()
        submission = self.captured['submission']
        self.assertEqual(submission.project_root, snapshot()['source_dir'])
        self.assertEqual(submission.source.commit, snapshot()['source_commit'])
        self.assertEqual(submission.argv[0], OWNER.executable)
        self.assertEqual(submission.argv[-2:], ('--', 'cat'))
        self.assertNotIn('self', submission.argv)
        self.assertEqual(self.captured['input'], b'fixture')
        self.assertEqual(self.captured['nlink'], 0)
        provenance = submission.execution_policy_provenance
        self.assertEqual(provenance['server_cli.target_selector_digest'], snapshot()['selector_digest'])
        self.assertEqual(provenance['server_cli.stdin_sha256'], hashlib.sha256(b'fixture').hexdigest())
        self.assertEqual(json.loads(provenance['server_cli.target_snapshot'])['source_origin_commit'], '2' * 40)
        self.assertEqual(receipt['request_id'], 'original-exec')

    def test_incomplete_input_or_changed_selector_never_accepts(self):
        with self.assertRaises(ServerCLIUnavailable):
            self.receive(input_data=b'partial')
        self.service.submit.assert_not_called()
        changed = {'ok': True, **snapshot(), 'selector_digest': 'sha256:' + 'f' * 64}
        with self.assertRaises(ServerCLIUnavailable):
            self.receive(observations=[{'ok': True, **snapshot()}, changed])
        self.service.submit.assert_not_called()

    def test_caller_source_path_and_duplicate_selector_keys_are_refused(self):
        with self.assertRaises(ServerCLIUnavailable):
            self.receive(changes={'source_dir': '/caller/chosen/source'})
        self.service.submit.assert_not_called()
        encoded = json.dumps(selector())
        with self.assertRaises(ServerCLIUnavailable):
            execute._selector(encoded[:-1] + ',"service":"other"}')

    def test_original_deadline_cannot_be_extended_or_changed_by_descriptor(self):
        deadline = selector()['deadline_at']
        current = {'kind': 'server-cli', 'execution_policy_provenance_json': json.dumps({
            'server_cli.absolute_deadline_at': deadline})}
        self.assertLessEqual(_server_cli_deadline_remaining({'server_cli_deadline_at': deadline}, current), 90)
        with self.assertRaises(RuntimeError):
            _server_cli_deadline_remaining({'server_cli_deadline_at': deadline}, {**current, 'kind': 'exec'})
        with self.assertRaises(ServerCLIUnavailable):
            self.receive(changes={'deadline_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()})
        self.service.submit.assert_not_called()

    def test_binary_receiver_selects_only_bound_artifact_mode_and_fixed_capture_flag(self):
        self.receive(changes={'output_contract': 'artifact.v1'}, capture_available=True)
        submission = self.captured['submission']
        self.assertIn('--exec-capture-artifacts', submission.argv)
        self.assertEqual(submission.execution_policy_provenance['server_cli.output_contract'], 'artifact.v1')
        with self.assertRaises(ServerCLIUnavailable):
            self.receive(changes={'output_contract': 'artifact.v1'}, capture_available=False)
        self.service.submit.assert_not_called()


class NoSourceClientTests(unittest.TestCase):
    def invoke(self, *, payload=None, terminated=None, output_mode='utf8'):
        record = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
            'ssh': 'alim@212.95.33.238', 'tailscale_host': '100.64.1.2', 'front_door': 'nginx',
            'mcp_service': {'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision}}
        self.calls = []
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs))
            self.assertEqual(os.read(kwargs['stdin'], 4096), b'original synthetic stdin')
            self.assertEqual(os.fstat(kwargs['stdin']).st_nlink, 0)
            self.assertNotIn('original synthetic stdin', ' '.join(argv))
            self.assertNotIn('CLOUDFLARE_API_TOKEN', kwargs['env'])
            parts = shlex.split(argv[-1])
            selected = json.loads(parts[parts.index('--server-cli-exec-selector') + 1])
            valid_receipt = {
                'ok': True, 'job_id': 'a' * 32, 'request_id': 'original-exec',
                'selector_digest': snapshot()['selector_digest'],
                'source': {'commit': snapshot()['source_commit'], 'identity': 'sha256:' +
                    hashlib.sha256(snapshot()['source_dir'].encode()).hexdigest()},
                'execution_policy': {'provenance': {
                    'server_cli.target_identity': OWNER.target_identity,
                    'server_cli.ownership_marker': OWNER.ownership_marker,
                    'server_cli.runtime_revision': OWNER.runtime_revision,
                    'server_cli.remote_name': 'box', 'server_cli.target_selector_digest': snapshot()['selector_digest'],
                    'server_cli.exec_service': 'wordpress', 'server_cli.exec_target': 'fixture/dev',
                    'server_cli.output_contract': selected['output_contract'],
                    'server_cli.stdin_sha256': selected['stdin_sha256'],
                    'server_cli.stdin_size': str(selected['stdin_size']),
                    'server_cli.absolute_deadline_at': selected['deadline_at']}}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload if payload is not None else valid_receipt), stderr='',
                termination_reason=terminated, stdout_truncated=False, stderr_truncated=False)
        with tempfile.TemporaryFile('w+b') as original:
            original.write(b'original synthetic stdin')
            original.seek(0)
            with mock.patch.object(_remote, 'get_remote', return_value=record), \
                    mock.patch.object(_remote, 'remote_sb_path', return_value=OWNER.executable), \
                    mock.patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': OWNER.target_identity}), \
                    mock.patch('sandbox.services.process.BoundedProcessRunner.run', side_effect=run), \
                    mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control', return_value={
                        'ok': True, 'target_identity': OWNER.target_identity,
                        'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision,
                        'server_exec_capabilities': [execute.CONTRACT],
                        'server_exec_capture_capabilities': ['job.server-cli.capture-artifact.v1']}), \
                    mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False), \
                    mock.patch.object(_remote, 'deploy_exact_working_tree', side_effect=AssertionError('no controller source')):
                return execute.submit_server_cli_exec('box', snapshot=snapshot(), service='wordpress',
                    argv=['cat'], stdin_fd=original.fileno(), request_id='original-exec', timeout_seconds=30,
                    output_mode=output_mode)

    def test_no_source_submission_uses_private_keyless_tailnet_and_exact_original_stdin_once(self):
        receipt = self.invoke()
        self.assertEqual(receipt['job_id'], 'a' * 32)
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0][0]
        self.assertIn('alim@100.64.1.2', argv)
        self.assertNotIn('alim@212.95.33.238', argv)
        self.assertIn('/dev/null', argv)
        self.assertIn('PubkeyAuthentication=no', argv)
        self.assertIn('--server-cli-exec-selector', argv[-1])

    def test_client_deadline_reserves_the_server_kill_grace_and_lease_release(self):
        started = datetime.now(timezone.utc)
        self.invoke()
        parts = shlex.split(self.calls[0][0][-1])
        selected = json.loads(parts[parts.index('--server-cli-exec-selector') + 1])
        reserved = (datetime.fromisoformat(selected['deadline_at']) - started).total_seconds()
        # timeout_seconds=30, plus the 10 s kill grace and 15 s lease release.
        self.assertGreaterEqual(reserved, 55)
        self.assertLess(reserved, 60)

    def test_unknown_acceptance_is_not_replayed_or_misreported_as_refused(self):
        with self.assertRaises(execute.ServerCLIExecAcceptanceUnknown) as failure:
            self.invoke(payload={})
        self.assertEqual(failure.exception.request_id, 'original-exec')
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(execute.ServerCLIExecAcceptanceUnknown):
            self.invoke(terminated='timeout')
        self.assertEqual(len(self.calls), 1)

    def test_explicit_preacceptance_refusal_remains_distinct(self):
        with self.assertRaises(ServerCLIUnavailable):
            self.invoke(payload={'ok': False, 'status': 'refused', 'code': 'server_cli_exec_unavailable'})
        self.assertEqual(len(self.calls), 1)

    def test_binary_submission_binds_artifact_mode_without_putting_payload_in_argv(self):
        self.invoke(output_mode='artifact')
        parts = shlex.split(self.calls[0][0][-1])
        selected = json.loads(parts[parts.index('--server-cli-exec-selector') + 1])
        self.assertEqual(selected['output_contract'], 'artifact.v1')

    def test_missing_installed_capability_refuses_before_original_stdin_or_submit(self):
        with mock.patch.object(_remote, 'get_remote', return_value={
                'joined': True, 'control_transport': 'tailscale', 'mcp_service': {
                    'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision}}), \
                mock.patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': OWNER.target_identity}), \
                mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control', return_value={'ok': False}), \
                mock.patch.object(execute, 'capture_input', side_effect=AssertionError('no stdin intake')):
            with self.assertRaises(ServerCLIUnavailable):
                execute.submit_server_cli_exec('box', snapshot=snapshot(), service='wordpress',
                    argv=['cat'], stdin_fd=0, request_id='original-exec', timeout_seconds=30)
