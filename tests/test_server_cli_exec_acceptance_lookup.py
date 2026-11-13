"""A lost host-exec receipt is recovered only from one exact read-only ledger match."""
import json
import subprocess
import unittest
from unittest import mock

from sandbox.application import server_cli_exec as execute
from sandbox.core import _remote
from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError

JOB = '4823c876' + 'a' * 24
OTHER_JOB = 'b' * 32
SOURCE = {'commit': 'c' * 40, 'identity': 'sha256:' + 'b' * 64}
DIGEST = 'sha256:' + 'd' * 64
PROOF = {
    'server_cli.target_identity': 'target-fixture', 'server_cli.ownership_marker': 'owner-fixture',
    'server_cli.runtime_revision': 'revision-fixture', 'server_cli.remote_name': 'box',
    'server_cli.target_selector_digest': DIGEST, 'server_cli.exec_service': 'web',
    'server_cli.exec_target': 'project/dev', 'server_cli.output_contract': 'artifact.v1',
    'server_cli.stdin_sha256': 'e' * 64, 'server_cli.stdin_size': '0',
    'server_cli.absolute_deadline_at': '2026-11-13T12:19:23.000000+00:00'}
RECORD = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
          'ssh': 'alim@212.95.33.238', 'tailscale_host': '100.64.1.2'}


def unknown(request_id='original-exec', **changes):
    expected = {'remote_name': 'box', 'selector_digest': DIGEST, 'provenance': dict(PROOF),
                'source': dict(SOURCE), **changes}
    return execute.ServerCLIExecAcceptanceUnknown(request_id, expected=expected)


def row(job_id=JOB, request_id='original-exec', kind='server-cli', **changes):
    return {'job_id': job_id, 'request_id': request_id, 'kind': kind, 'lifecycle': 'succeeded',
            'source_commit': SOURCE['commit'], 'source_identity': SOURCE['identity'], **changes}


def status(job_id=JOB, provenance=None, **changes):
    return {'ok': True, 'job_id': job_id, 'request_id': 'original-exec', 'kind': 'server-cli',
            'target_kind': 'local', 'lifecycle': 'succeeded', 'exit_code': 0,
            'source_commit': SOURCE['commit'], 'source_identity': SOURCE['identity'],
            'execution_policy_provenance_json': json.dumps({**PROOF, 'server_cli.extra': 'kept'}
                                                           if provenance is None else provenance),
            **changes}


class AcceptanceLookupTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.rows = [row()]
        self.state = status()
        self.enterContext(mock.patch.object(_remote, 'get_remote', return_value=RECORD))
        self.enterContext(mock.patch.object(_remote, 'remote_sb_path', return_value='/srv/sb'))
        # Nothing here may submit: no job-start, no runner, no controller source.
        self.enterContext(mock.patch.object(execute, 'submit_server_cli_exec',
                                            side_effect=AssertionError('never resubmit')))
        self.enterContext(mock.patch('sandbox.services.process.BoundedProcessRunner.run',
                                     side_effect=AssertionError('never run a submission')))
        self.enterContext(mock.patch.object(_remote, 'deploy_exact_working_tree',
                                            side_effect=AssertionError('no controller fallback')))
        # Each test may swap self.fake; the patched method always routes to it.
        self.fake = self.control
        def control(transport, remote_name, argv, *, timeout=25):
            return self.fake(transport, remote_name, argv, timeout=timeout)
        self.enterContext(mock.patch.object(RemoteJobTransport, 'control', new=control))

    def control(self, _transport, remote_name, argv, *, timeout=25):
        self.calls.append((remote_name, list(argv), timeout))
        self.assertEqual(remote_name, 'box')
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, execute.ACCEPTANCE_LOOKUP_SECONDS)
        if argv[0] == 'job-list':
            return {'ok': True, 'jobs': self.rows, 'page': {'schema_version': 2, 'has_more': False}}
        if argv[0] == 'job-status':
            return self.state
        raise AssertionError('unexpected control operation ' + argv[0])

    def recover(self, failure=None):
        return execute.recover_server_cli_exec_acceptance('box', failure or unknown())

    def assert_read_only(self):
        operations = [argv[0] for _remote_name, argv, _timeout in self.calls]
        self.assertTrue(set(operations) <= {'job-list', 'job-status'}, operations)
        self.assertNotIn('job-start', ' '.join(' '.join(argv) for _r, argv, _t in self.calls))

    def test_exactly_one_matching_server_cli_row_is_accepted_with_its_job(self):
        receipt = self.recover()
        self.assertEqual(receipt['job_id'], JOB)
        self.assertEqual(receipt['request_id'], 'original-exec')
        self.assertEqual(receipt['selector_digest'], DIGEST)
        self.assertEqual(receipt['source'], SOURCE)
        # The retained provenance is what read_server_cli_exec_result compares.
        self.assertEqual(receipt['execution_policy']['provenance'],
                         json.loads(self.state['execution_policy_provenance_json']))
        self.assertEqual(self.calls[0][1], ['job-list', '--limit', '50', '--kind', 'server-cli'])
        self.assertEqual(self.calls[1][1], ['job-status', JOB])
        self.assertEqual(len(self.calls), 2)
        self.assert_read_only()

    def test_no_matching_row_stays_unknown(self):
        self.rows = [row(request_id='another-request'), row(job_id=OTHER_JOB, request_id='original-exec-2')]
        self.assertIsNone(self.recover())
        self.assertEqual(len(self.calls), 1)
        self.assert_read_only()

    def test_empty_ledger_stays_unknown(self):
        self.rows = []
        self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_two_matching_rows_stay_unknown(self):
        self.rows = [row(), row(job_id=OTHER_JOB)]
        self.assertIsNone(self.recover())
        self.assertEqual(len(self.calls), 1)
        self.assert_read_only()

    def test_matching_row_of_another_kind_stays_unknown(self):
        self.rows = [row(kind='test')]
        self.assertIsNone(self.recover())
        self.rows = [row(), row(job_id=OTHER_JOB, kind='test')]
        self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_request_id_is_never_matched_by_prefix(self):
        self.rows = [row(request_id='original-exec-retry'), row(job_id=OTHER_JOB, request_id='original')]
        self.assertIsNone(self.recover())
        self.rows = [row(request_id='original-exec-retry')]
        self.assertIsNone(self.recover(unknown(request_id='original')))
        self.assert_read_only()

    def test_matching_id_with_a_different_request_digest_stays_unknown(self):
        cases = {
            'stdin': {**PROOF, 'server_cli.stdin_sha256': 'f' * 64},
            'selector': {**PROOF, 'server_cli.target_selector_digest': 'sha256:' + '0' * 64},
            'deadline': {**PROOF, 'server_cli.absolute_deadline_at': '2026-11-13T12:20:00+00:00'},
            'missing': {key: value for key, value in PROOF.items() if key != 'server_cli.stdin_sha256'},
        }
        for name, provenance in cases.items():
            with self.subTest(name=name):
                self.state = status(provenance=provenance)
                self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_matching_id_with_a_different_source_stays_unknown(self):
        self.rows = [row(source_commit='0' * 40)]
        self.assertIsNone(self.recover())
        self.assertEqual(len(self.calls), 1)
        self.rows = [row()]
        self.state = status(source_identity='sha256:' + '0' * 64)
        self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_status_for_another_request_or_job_stays_unknown(self):
        for changes in ({'request_id': 'another-request'}, {'job_id': OTHER_JOB}, {'kind': 'test'},
                        {'ok': False}, {'execution_policy_provenance_json': None}):
            with self.subTest(changes=changes):
                self.state = status(**changes)
                self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_lookup_transport_failure_or_timeout_stays_unknown(self):
        failures = (RemoteJobTransportError('remote job control operation failed'),
                    subprocess.TimeoutExpired('ssh', 15), TimeoutError())
        for failure in failures:
            def fail(_transport, remote_name, argv, *, timeout=25, failure=failure):
                self.calls.append((remote_name, list(argv), timeout))
                raise failure
            with self.subTest(failure=type(failure).__name__):
                self.fake = fail
                self.assertIsNone(self.recover())
        self.assert_read_only()

    def test_status_timeout_after_a_unique_row_stays_unknown(self):
        def control(_transport, remote_name, argv, *, timeout=25):
            self.calls.append((remote_name, list(argv), timeout))
            if argv[0] == 'job-list':
                return {'ok': True, 'jobs': [row()]}
            raise subprocess.TimeoutExpired('ssh', timeout)
        self.fake = control
        self.assertIsNone(self.recover())
        self.assertEqual([argv[0] for _r, argv, _t in self.calls], ['job-list', 'job-status'])
        self.assert_read_only()

    def test_budget_is_spent_by_wall_clock_even_when_the_monotonic_clock_stops(self):
        # A Mac asleep during job-list: wall time jumps, monotonic time does not.
        wall = [1_000.0]
        def control(_transport, remote_name, argv, *, timeout=25):
            self.calls.append((remote_name, list(argv), timeout))
            wall[0] += execute.ACCEPTANCE_LOOKUP_SECONDS + 1
            return {'ok': True, 'jobs': [row()]} if argv[0] == 'job-list' else status()
        self.fake = control
        with mock.patch.object(execute.time, 'time', side_effect=lambda: wall[0]), \
                mock.patch.object(execute.time, 'monotonic', return_value=50.0):
            self.assertIsNone(self.recover())
        self.assertEqual([argv[0] for _r, argv, _t in self.calls], ['job-list'])

    def test_unknown_without_the_submitted_request_is_not_looked_up(self):
        for failure in (execute.ServerCLIExecAcceptanceUnknown('original-exec'),
                        unknown(remote_name='another-box'),
                        unknown(provenance={**PROOF, 'server_cli.remote_name': 'another-box'})):
            with self.subTest(expected=failure.expected):
                self.assertIsNone(self.recover(failure))
        self.assertEqual(self.calls, [])

    def test_unjoined_record_is_not_looked_up(self):
        with mock.patch.object(_remote, 'get_remote', return_value={**RECORD, 'joined': False}):
            self.assertIsNone(self.recover())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
