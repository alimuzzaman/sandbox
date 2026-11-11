"""Synchronous callers observe original retained results without re-executing."""
import base64
import copy
import json
import unittest
from unittest import mock

from sandbox.application import server_cli_exec as execute
from sandbox.core import _remote
from tests.test_server_cli import OWNER


def receipt():
    return {'ok': True, 'job_id': 'a' * 32, 'request_id': 'original-exec',
        'selector_digest': 'sha256:' + 'd' * 64, 'source': {'identity': 'sha256:' + 'e' * 64, 'commit': 'f' * 40},
        'execution_policy': {'provenance': {'server_cli.target_identity': OWNER.target_identity,
            'server_cli.ownership_marker': OWNER.ownership_marker, 'server_cli.runtime_revision': OWNER.runtime_revision,
            'server_cli.remote_name': 'box', 'server_cli.target_selector_digest': 'sha256:' + 'd' * 64,
            'server_cli.exec_service': 'wordpress', 'server_cli.exec_target': 'fixture/dev'}}}


def state(lifecycle='succeeded', exit_code=0):
    original = receipt()
    return {'ok': True, 'job_id': original['job_id'], 'request_id': original['request_id'],
        'kind': 'server-cli', 'target_kind': 'local', 'lifecycle': lifecycle, 'exit_code': exit_code,
        'source_identity': original['source']['identity'], 'source_commit': original['source']['commit'],
        'execution_policy_provenance_json': json.dumps(original['execution_policy']['provenance'])}


def outcome(exit_code=0):
    return {'ok': exit_code == 0, 'code': 'ok' if exit_code == 0 else 'host_exec_failed',
        'exit_code': exit_code, 'remote': 'box', 'target': 'fixture/dev', 'service': 'wordpress',
        'duration_ms': 10, 'stdout': 'original fixture output\n', 'stderr': '',
        'stdout_truncated': False, 'stderr_truncated': False}


def page(data, *, more=False, cursor='next-page'):
    return {'ok': True, 'job_id': 'a' * 32, 'stream': 'stdout', 'encoding': 'base64',
        'data': base64.b64encode(data).decode(), 'bytes_read': len(data), 'has_more': more, 'cursor': cursor}


class ExecResultTests(unittest.TestCase):
    def observe(self, responses):
        # A new compatible service revision does not rewrite the original
        # job's revision; read-only ledger observation must keep that binding.
        record = {'joined': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': OWNER.ownership_marker, 'runtime_revision': '1' * 24}}
        with mock.patch.object(_remote, 'get_remote', return_value=record), \
                mock.patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': OWNER.target_identity}), \
                mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control', side_effect=responses) as control, \
                mock.patch.object(_remote, 'deploy_exact_working_tree', side_effect=AssertionError('no staging')), \
                mock.patch.object(execute, 'submit_server_cli_exec', side_effect=AssertionError('never replay')):
            self.control = control
            return execute.read_server_cli_exec_result('box', receipt(), budget_seconds=2)

    def test_running_job_is_observed_without_reading_child_output(self):
        result = self.observe([state('running')])
        self.assertEqual(result['status'], 'running')
        self.assertIsNone(result['result'])
        self.control.assert_called_once_with('box', ['job-status', 'a' * 32], timeout=mock.ANY)

    def test_terminal_original_result_reads_bounded_retained_pages_and_preserves_exit(self):
        value = outcome(7)
        encoded = json.dumps(value).encode()
        result = self.observe([state('failed', 7), page(encoded[:20], more=True), page(encoded[20:])])
        self.assertEqual(result['result'], value)
        self.assertEqual(result['receipt']['request_id'], 'original-exec')
        self.assertEqual(self.control.call_count, 3)
        for call in self.control.call_args_list:
            self.assertLessEqual(call.kwargs['timeout'], 2)
        self.assertIn('--cursor', self.control.call_args_list[-1].args[1])

    def test_changed_job_binding_refuses_before_any_output_read(self):
        for change in ({'request_id': 'another'}, {'source_commit': '2' * 40},
                       {'execution_policy_provenance_json': '{}'}, {'kind': 'exec'}):
            with self.subTest(change=change), self.assertRaises(execute.ServerCLIExecObservationUnavailable) as failure:
                self.observe([{**state(), **change}])
            self.assertEqual(failure.exception.job_id, 'a' * 32)
            self.assertEqual(failure.exception.request_id, 'original-exec')
            self.assertEqual(self.control.call_count, 1)

    def test_missing_output_and_wrong_result_service_preserve_known_receipt(self):
        for response in (page(b''), page(json.dumps({**outcome(), 'service': 'other'}).encode())):
            with self.subTest(response=response), self.assertRaises(execute.ServerCLIExecObservationUnavailable) as failure:
                self.observe([state(), response])
            self.assertNotIsInstance(failure.exception, execute.ServerCLIExecAcceptanceUnknown)
            self.assertEqual(failure.exception.job_id, 'a' * 32)

    def test_cursor_replay_cannot_repeat_pages_or_effects(self):
        repeated = page(b'{', more=True, cursor='same')
        with self.assertRaises(execute.ServerCLIExecObservationUnavailable):
            self.observe([state(), repeated, repeated])
        self.assertEqual(self.control.call_count, 3)

    def test_slot_refusal_is_retained_terminal_result_without_output_or_retry(self):
        result = self.observe([{**state('failed', None), 'termination_reason': 'anonymous_stdin_capacity_unavailable'}])
        self.assertEqual(result['result']['code'], 'anonymous_stdin_capacity_unavailable')
        self.assertEqual(result['result']['exit_code'], 1)
        self.assertEqual(self.control.call_count, 1)

    def test_target_or_receipt_changes_cannot_select_another_server(self):
        wrong = copy.deepcopy(receipt())
        wrong['execution_policy']['provenance']['server_cli.remote_name'] = 'other'
        with mock.patch.object(_remote, 'get_remote', side_effect=AssertionError('no lookup')):
            with self.assertRaises(execute.ServerCLIExecObservationUnavailable):
                execute.read_server_cli_exec_result('box', wrong)
