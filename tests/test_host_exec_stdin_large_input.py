"""Joined host exec: stdin and large input within documented bounds, named refusals.

Each class reproduces one observed failure (2026-11-17, xspeed-dev) through the
caller-visible path: piped stdin and 100 KB argv collapsed into
``server_cli_exec_unavailable``, and a busy server job slot failed the job at
once. Every remote port is synthetic; no job is submitted anywhere.
"""
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.application import server_cli as sdk
from sandbox.application import server_cli_exec as execute
from sandbox.application.job_service import JobService
from sandbox.core import _remote
from sandbox.hosting import host_exec
from sandbox.hosting import server_cli_dispatch as dispatch
from sandbox.hosting import server_cli_exec_client as client
from sandbox.jobs import anonymous_input
from sandbox.jobs.manifest import JobComponentRegistry
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import WorkspaceBusy
from sandbox.jobs.storage import JobStorage
from tests.test_server_cli import OWNER
from tests.test_server_cli_target_observation import snapshot

CAPTURE = 'job.server-cli.capture-artifact.v1'


def record():
    return {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
        'ssh': 'alim@212.95.33.238', 'tailscale_host': '100.64.1.2', 'front_door': 'nginx',
        'mcp_service': {'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision}}


def capabilities():
    return {'ok': True, 'target_identity': OWNER.target_identity,
        'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision,
        'server_exec_capabilities': [execute.CONTRACT], 'server_exec_capture_capabilities': [CAPTURE]}


def selector_of(argv):
    parts = shlex.split(argv[-1])
    return parts, json.loads(parts[parts.index('--server-cli-exec-selector') + 1])


def accepted(selected):
    return {'ok': True, 'job_id': 'a' * 32, 'request_id': 'original-exec',
        'selector_digest': selected['selector_digest'],
        'source': {'commit': snapshot()['source_commit'], 'identity': 'sha256:' +
            hashlib.sha256(snapshot()['source_dir'].encode()).hexdigest()},
        'execution_policy': {'provenance': {
            'server_cli.target_identity': OWNER.target_identity,
            'server_cli.ownership_marker': OWNER.ownership_marker,
            'server_cli.runtime_revision': OWNER.runtime_revision,
            'server_cli.remote_name': 'box', 'server_cli.target_selector_digest': selected['selector_digest'],
            'server_cli.exec_service': 'wordpress', 'server_cli.exec_target': 'fixture/dev',
            'server_cli.output_contract': selected['output_contract'],
            'server_cli.stdin_sha256': selected['stdin_sha256'],
            'server_cli.stdin_size': str(selected['stdin_size']),
            'server_cli.absolute_deadline_at': selected['deadline_at']}}}


def ran(stdout):
    return SimpleNamespace(returncode=0, stdout=json.dumps(stdout), stderr='', termination_reason=None,
                           stdout_truncated=False, stderr_truncated=False)


class SyntheticServer(unittest.TestCase):
    """The controller's real submission port against a synthetic remote."""

    def setUp(self):
        self.sent = []
        self.responses = []
        self.enterContext(mock.patch.object(_remote, 'get_remote', return_value=record()))
        self.enterContext(mock.patch.object(_remote, 'remote_sb_path', return_value=OWNER.executable))
        self.enterContext(mock.patch('sandbox.resources.context.authenticated_target_identity',
                                     return_value={'target_identity': OWNER.target_identity}))
        self.enterContext(mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control',
                                     return_value=capabilities()))
        self.enterContext(mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False))
        self.enterContext(mock.patch.object(_remote, 'deploy_exact_working_tree',
                                            side_effect=AssertionError('no controller source')))
        self.enterContext(mock.patch('sandbox.services.process.BoundedProcessRunner.run', side_effect=self.run_ssh))

    def run_ssh(self, argv, **kwargs):
        data = b''
        while True:
            chunk = os.read(kwargs['stdin'], 65536)
            if not chunk:
                break
            data += chunk
        parts, selected = selector_of(argv)
        self.sent.append({'stdin': data, 'selector': selected, 'request_id':
                          parts[parts.index('--request-id') + 1], 'wire': argv[-1]})
        response = self.responses.pop(0) if self.responses else 'accept'
        return ran(accepted(selected) if response == 'accept' else response)

    def submit(self, data, *, argv=('cat',), reobserve=None):
        with tempfile.TemporaryFile('w+b') as original:
            original.write(data)
            original.seek(0)
            return execute.submit_server_cli_exec('box', snapshot=snapshot(), service='wordpress',
                argv=list(argv), stdin_fd=original.fileno(), request_id='original-exec',
                timeout_seconds=60, reobserve=reobserve)


class PipedStdinReachesTheServer(SyntheticServer):
    """Symptom 1: piped stdin is carried intact; a refusal names its cause."""

    def test_piped_stdin_through_the_cli_reaches_the_server_exactly_once(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b'data')
        os.close(write_fd)
        self.addCleanup(os.close, read_fd)
        args = Namespace(action='exec', remote='box', name=None, target='fixture/dev', environment=None,
            project_dir='/not/a/checkout', service='wordpress', exec_argv=['cat'], timeout=30, json=True,
            request_id='original-exec', _server_cli_argv=('host', 'exec'))
        outcome = {'ok': True, 'code': 'ok', 'exit_code': 0, 'remote': 'box', 'target': 'fixture/dev',
            'service': 'wordpress', 'duration_ms': 1, 'stdout_bytes': b'data', 'stderr_bytes': b'',
            'output_encoding': 'artifact-bytes', 'stdout_truncated': False, 'stderr_truncated': False}
        with mock.patch.object(sdk, 'server_cli_context_if_selected', return_value=None), \
                mock.patch('sandbox.application.server_cli_targets.observe_server_cli_target',
                           return_value=snapshot()), \
                mock.patch.object(execute, 'read_server_cli_exec_result',
                                  return_value={'status': 'succeeded', 'result': outcome}), \
                mock.patch('sandbox.hosting.host_exec.stdin_source', return_value=read_fd):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertTrue(dispatch.dispatch_host_if_selected(args))
        self.assertEqual(json.loads(output.getvalue())['stdout'], 'data')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['stdin'], b'data')
        self.assertEqual(self.sent[0]['selector']['stdin_size'], 4)
        self.assertNotIn('data', self.sent[0]['wire'].split('--server-cli-exec-selector')[0])

    def test_server_names_target_change_and_the_same_request_is_resent_with_the_same_stdin(self):
        refusal = {'ok': False, 'code': execute.TARGET_CHANGED, 'status': 'refused', 'error': 'fixed'}
        self.responses = [refusal, 'accept']
        fresh = {**snapshot(), 'selector_digest': 'sha256:' + 'c' * 64}
        reobserve = mock.Mock(return_value=fresh)
        receipt = self.submit(b'original bytes', reobserve=reobserve)
        reobserve.assert_called_once()
        # Re-observation is bounded by what is left of the original deadline.
        self.assertLessEqual(reobserve.call_args.kwargs['budget_seconds'], 45)
        self.assertEqual([item['stdin'] for item in self.sent], [b'original bytes', b'original bytes'])
        self.assertEqual({item['request_id'] for item in self.sent}, {'original-exec'})
        self.assertEqual(self.sent[1]['selector']['selector_digest'], fresh['selector_digest'])
        self.assertEqual(receipt['job_id'], 'a' * 32)

    def test_resend_is_bounded_and_needs_a_fresh_observation(self):
        refusal = {'ok': False, 'code': execute.TARGET_CHANGED, 'status': 'refused', 'error': 'fixed'}
        self.responses = [refusal] * 5
        with self.assertRaises(execute.ServerCLIExecRefused) as failure:
            self.submit(b'x', reobserve=mock.Mock(return_value=snapshot()))
        self.assertEqual(failure.exception.code, execute.TARGET_CHANGED)
        self.assertEqual(len(self.sent), execute.TARGET_CHANGE_ATTEMPTS)
        self.sent.clear()
        self.responses = [refusal]
        with self.assertRaises(execute.ServerCLIExecRefused):
            self.submit(b'x')
        self.assertEqual(len(self.sent), 1)

    def test_a_failed_reobservation_is_still_not_accepted(self):
        refusal = {'ok': False, 'code': execute.TARGET_CHANGED, 'status': 'refused', 'error': 'fixed'}
        self.responses = [refusal]
        with self.assertRaises(execute.ServerCLIExecRefused) as failure:
            self.submit(b'x', reobserve=mock.Mock(side_effect=RuntimeError('network blip')))
        self.assertEqual(failure.exception.code, 'server_cli_exec_target_unavailable')
        self.assertEqual(len(self.sent), 1)

    def test_other_named_refusals_are_never_resent(self):
        for code in ('host_exec_stdin_mismatch', 'host_target_busy', 'server_cli_exec_deadline_expired'):
            with self.subTest(code=code):
                self.sent.clear()
                self.responses = [{'ok': False, 'code': code, 'status': 'refused', 'error': 'fixed'}]
                with self.assertRaises(execute.ServerCLIExecRefused) as failure:
                    self.submit(b'x', reobserve=mock.Mock(side_effect=AssertionError('no resend')))
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(len(self.sent), 1)

    def test_older_runtime_catch_all_refusal_is_reported_as_refused(self):
        self.responses = [{'ok': False, 'code': 'server_cli_exec_unavailable', 'status': 'refused',
                           'error': 'fixed'}]
        with self.assertRaises(execute.ServerCLIExecRefused) as failure:
            self.submit(b'x')
        self.assertEqual(failure.exception.code, 'server_cli_exec_refused')
        self.responses = [{'ok': False, 'code': 'invented_by_remote', 'status': 'refused', 'error': 'x'}]
        with self.assertRaises(execute.ServerCLIExecRefused) as failure:
            self.submit(b'x')
        self.assertEqual(failure.exception.code, 'server_cli_exec_refused')


class LargeInputBounds(SyntheticServer):
    """Symptom 2: 100 KB of argv exceeded the bound; stdin carries it instead."""

    def test_100_kb_on_stdin_is_carried_and_bound_by_size_and_digest(self):
        data = os.urandom(100 * 1024)
        self.submit(data)
        self.assertEqual(self.sent[0]['stdin'], data)
        self.assertEqual(self.sent[0]['selector']['stdin_size'], len(data))
        self.assertEqual(self.sent[0]['selector']['stdin_sha256'], hashlib.sha256(data).hexdigest())

    def test_stdin_at_the_bound_is_carried_and_over_it_refuses_before_submission(self):
        self.submit(b'x' * host_exec.SERVER_STDIN_MAX_BYTES)
        self.assertEqual(len(self.sent[0]['stdin']), host_exec.SERVER_STDIN_MAX_BYTES)
        self.sent.clear()
        with self.assertRaises(anonymous_input.AnonymousInputTooLarge):
            self.submit(b'x' * (host_exec.SERVER_STDIN_MAX_BYTES + 1))
        self.assertEqual(self.sent, [])

    def test_argv_over_the_bound_or_its_quoted_form_refuses_before_submission(self):
        for argv in (['sh', '-c', 'x' * (100 * 1024)], ['true'] * (host_exec.SERVER_ARGV_MAX_ITEMS + 1),
                     ['printf', "'" * 60000]):
            with self.subTest(size=sum(map(len, argv))):
                with self.assertRaises(execute.ServerCLIExecRefused) as failure:
                    self.submit(b'', argv=argv)
                self.assertEqual(failure.exception.code, 'host_exec_argv_too_large')
                self.assertEqual(self.sent, [])

    def test_20_kb_argv_chunks_stay_within_the_bound(self):
        self.submit(b'', argv=['sh', '-c', 'printf %s ' + 'y' * 20000 + ' >> /tmp/in'])
        self.assertEqual(len(self.sent), 1)

    def test_limits_match_the_server_contract(self):
        self.assertEqual(host_exec.SERVER_STDIN_MAX_BYTES, anonymous_input.MAX_INPUT_BYTES)
        value = {**json.loads(json.dumps(selector_value())), 'stdin_size': host_exec.SERVER_STDIN_MAX_BYTES + 1}
        with self.assertRaises(sdk.ServerCLIUnavailable):
            execute._selector(json.dumps(value))

    def test_stdin_without_end_of_input_times_out_as_its_own_refusal(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        with self.assertRaises(anonymous_input.AnonymousInputTimeout):
            anonymous_input.capture_input(read_fd, timeout_seconds=0.2)


def selector_value():
    return {'schema_version': 1, 'origin_remote_name': 'box', 'target': 'fixture/dev',
        'name': None, 'environment': 'dev', 'selector_digest': snapshot()['selector_digest'],
        'service': 'wordpress', 'target_identity': OWNER.target_identity,
        'ownership_marker': OWNER.ownership_marker, 'runtime_revision': OWNER.runtime_revision,
        'deadline_at': (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(),
        'target_metadata': {'front_door': 'nginx'}, 'stdin_sha256': hashlib.sha256(b'data').hexdigest(),
        'stdin_size': 4}


class CallerSeesTheNamedCause(unittest.TestCase):
    """What `sb host exec --json` prints for each pre-acceptance refusal."""

    def setUp(self):
        self.args = Namespace(action='exec', remote='box', name='named', target=None, environment=None,
            project_dir='/not/a/checkout', service='web', exec_argv=['cat'], timeout=30, json=True,
            request_id='original-exec', _server_cli_argv=('host', 'exec'))
        self.enterContext(mock.patch.object(sdk, 'server_cli_context_if_selected', return_value=None))
        self.enterContext(mock.patch('sandbox.core._remote.get_remote',
                                     return_value={'joined': True, 'control_transport': 'tailscale'}))
        self.observe = self.enterContext(mock.patch(
            'sandbox.application.server_cli_targets.observe_server_cli_target', return_value={'synthetic': True}))
        self.submit = self.enterContext(mock.patch.object(execute, 'submit_server_cli_exec'))
        self.enterContext(mock.patch('sandbox.hosting.host_exec.stdin_source', return_value=-3))

    def run_cli(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors), self.assertRaises(SystemExit) as failure:
            dispatch.dispatch_host_if_selected(self.args)
        return json.loads(output.getvalue()), failure.exception.code

    def test_100_kb_argv_is_refused_by_name_before_any_metadata_read(self):
        self.args.exec_argv = ['sh', '-c', 'printf %s ' + 'z' * (100 * 1024)]
        payload, code = self.run_cli()
        self.assertEqual((payload['code'], payload['acceptance'], code), ('host_exec_argv_too_large', 'not_accepted', 2))
        self.assertIn('stdin', payload['hint'])
        self.assertNotIn('zzzz', json.dumps(payload))
        self.observe.assert_not_called()
        self.submit.assert_not_called()

    def test_each_submission_refusal_keeps_its_code(self):
        cases = [(execute.ServerCLIExecRefused(execute.TARGET_CHANGED, 'x'), execute.TARGET_CHANGED, 1),
            (execute.ServerCLIExecRefused('server_cli_exec_refused', 'x'), 'server_cli_exec_refused', 1),
            (anonymous_input.AnonymousInputTooLarge('x'), 'host_exec_stdin_too_large', 2),
            (anonymous_input.AnonymousInputTimeout('x'), 'host_exec_stdin_timeout', 1),
            (anonymous_input.AnonymousInputUnavailable('x'), 'host_exec_stdin_unavailable', 1),
            (sdk.ServerCLIDeadlineExpired('x'), 'server_cli_exec_deadline_expired', 1),
            (sdk.ServerCLIUnavailable('untrusted raw detail'), 'server_cli_exec_unavailable', 1)]
        for error, expected, exit_code in cases:
            with self.subTest(expected=expected):
                self.submit.side_effect = error
                payload, code = self.run_cli()
                self.assertEqual((payload['code'], payload['acceptance'], code), (expected, 'not_accepted', exit_code))
                self.assertNotIn('untrusted raw', json.dumps(payload))

    def test_every_closed_code_has_a_message(self):
        for code in sorted(execute.REFUSAL_CODES):
            with self.subTest(code=code):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit):
                    client._fail(self.args, code, 'original-exec', acceptance='not_accepted')
                self.assertTrue(json.loads(output.getvalue())['error'])


class ReceiverNamesItsRefusal(unittest.TestCase):
    """The server's job-start refusal carries one closed code, same payload keys."""

    def receive(self, *, data=b'data', observations=None):
        args = SimpleNamespace(server_cli=True, local=True, remote=None, request_id='original-exec',
            parent_job_id=None, command=['--', 'cat'], server_cli_exec_selector=json.dumps(selector_value()))
        self.held = []
        def submit(submission, *, anonymous_input):
            self.held.append(anonymous_input.admission_wait_remaining())
            return {'ok': True, 'job_id': 'a' * 32, 'status': 'accepted'}
        service = mock.Mock()
        service.submit.side_effect = submit
        with tempfile.TemporaryFile('w+b') as original:
            original.write(data)
            original.seek(0)
            with mock.patch.object(execute, 'read_current_server_owner', return_value=OWNER), \
                    mock.patch.object(execute, 'exec_contract_available', return_value=True), \
                    mock.patch.object(execute, 'inspect_current_server_target',
                        side_effect=observations or [{'ok': True, **snapshot()}] * 2), \
                    mock.patch('sandbox.application.context.durable_job_dependencies',
                               return_value={'job_service': service}), \
                    mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False):
                return execute.receive_server_cli_exec(args, stdin_fd=original.fileno())

    def test_accepted_input_may_wait_for_a_slot_inside_the_original_deadline(self):
        self.receive()
        # 90 s deadline less the 25 s kill grace and lease release and a 1 s run.
        self.assertGreater(self.held[0], 50)
        self.assertLessEqual(self.held[0], 90 - host_exec.SERVER_POST_COMMAND_SECONDS - host_exec.TIMEOUT_MIN)

    def test_changed_target_and_changed_stdin_have_their_own_codes(self):
        changed = {'ok': True, **snapshot(), 'selector_digest': 'sha256:' + 'f' * 64}
        for kwargs, code in (({'observations': [changed]}, execute.TARGET_CHANGED),
                             ({'observations': [{'ok': True, **snapshot()}, changed]}, execute.TARGET_CHANGED),
                             ({'data': b'other'}, 'host_exec_stdin_mismatch')):
            with self.subTest(code=code, **{key: str(value)[:20] for key, value in kwargs.items()}):
                with self.assertRaises(execute.ServerCLIExecRefused) as failure:
                    self.receive(**kwargs)
                self.assertEqual(failure.exception.code, code)

    def test_job_start_refusal_keeps_its_keys_and_carries_only_the_code(self):
        from sandbox.commands import jobs_runtime
        args = SimpleNamespace(server_cli_exec_selector='{}')
        for error, code in ((execute.ServerCLIExecRefused(execute.TARGET_CHANGED, 'secret-ish detail'),
                             execute.TARGET_CHANGED),
                            (anonymous_input.AnonymousInputTooLarge('x'), 'host_exec_stdin_too_large'),
                            (sdk.ServerCLIUnavailable('x'), 'server_cli_exec_unavailable')):
            with self.subTest(code=code):
                output = io.StringIO()
                with mock.patch.object(execute, 'receive_server_cli_exec', side_effect=error), \
                        redirect_stdout(output), self.assertRaises(SystemExit):
                    jobs_runtime.cmd_job_start({}, args)
                payload = json.loads(output.getvalue())
                self.assertEqual(set(payload), {'ok', 'code', 'status', 'error'})
                self.assertEqual((payload['code'], payload['status']), (code, 'refused'))
                self.assertNotIn('secret-ish', output.getvalue())


class BusySlotWaitsInsteadOfRefusing(unittest.TestCase):
    """Symptom 3: an occupied slot is waited for within the bound, same job."""

    def service(self, acquire, temporary):
        repository = JobRepository(Path(temporary) / 'registry.sqlite')
        self.addCleanup(repository.close)
        components = JobComponentRegistry()
        components.register('server_cli', sdk.FixedServerCLI(), owner='fixture')
        scheduler = mock.Mock()
        scheduler.acquire.side_effect = acquire
        self.launch = mock.Mock()
        service = JobService(repository, JobStorage(temporary, free_disk_reserve=0),
            components, launcher=self.launch, scheduler=scheduler)
        return service, repository, scheduler

    def submit(self, acquire, *, wait):
        with tempfile.TemporaryFile('w+b') as original:
            original.write(b'fixture')
            original.seek(0)
            value = anonymous_input.capture_input(original.fileno(), timeout_seconds=1)
        self.addCleanup(value.close)
        value.hold_for_admission(wait)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        service, repository, scheduler = self.service(acquire, temporary)
        submission = JobSubmission('server-cli', temporary, 'fixture', 'local', 'server-cli',
            (OWNER.executable, 'host', 'exec'), 60, SourceIdentity('fixture'),
            request_id='busy-slot', execution_policy_provenance={
                'server_cli.stdin_sha256': value.binding['sha256'], 'server_cli.stdin_size': '7'})
        with mock.patch.object(sdk, 'installed_cli', return_value=Path(OWNER.executable)), \
                mock.patch.object(sdk.FixedServerCLI, 'environment', return_value={}), \
                mock.patch.object(service, '_finalize_terminal_workspace'), \
                mock.patch('sandbox.application.job_service.time.sleep'):
            receipt = service.submit(submission, anonymous_input=value)
        return receipt, repository, scheduler, value

    def test_slot_freed_within_the_bound_launches_the_one_job_once(self):
        busy = WorkspaceBusy('host capacity is busy', kind='capacity')
        order = WorkspaceBusy('an older queued job is waiting', kind='order')
        receipt, repository, scheduler, value = self.submit([busy, order, None], wait=30)
        row = repository.get(receipt['job_id'])
        self.assertEqual(row['request_id'], 'busy-slot')
        self.assertNotEqual(row['lifecycle'], 'failed')
        self.assertEqual(scheduler.acquire.call_count, 3)
        self.launch.assert_called_once()
        self.assertIs(self.launch.call_args.kwargs['anonymous_input'], value)
        self.assertEqual(len(repository.connection.execute('SELECT job_id FROM jobs').fetchall()), 1)

    def test_slot_still_busy_at_the_bound_fails_without_dispatch(self):
        busy = WorkspaceBusy('host capacity is busy', kind='capacity')
        receipt, repository, _scheduler, _value = self.submit(busy, wait=0.05)
        row = repository.get(receipt['job_id'])
        self.assertEqual((row['lifecycle'], row['termination_reason']),
                         ('failed', 'anonymous_stdin_capacity_unavailable'))
        self.launch.assert_not_called()

    def test_a_hangup_during_the_wait_marks_the_job_failed(self):
        import signal
        calls = []
        def acquire(row, **_kwargs):
            calls.append(row['job_id'])
            if len(calls) == 2:
                # The SSH session drops while the job waits for a slot.
                os.kill(os.getpid(), signal.SIGHUP)
            raise WorkspaceBusy('host capacity is busy', kind='capacity')
        original_service = self.service
        def keep(acquire_, temporary):
            service, repository, scheduler = original_service(acquire_, temporary)
            self.repository = repository
            return service, repository, scheduler
        self.service = keep
        previous = signal.getsignal(signal.SIGHUP)
        with self.assertRaises(RuntimeError):
            self.submit(acquire, wait=30)
        self.assertIs(signal.getsignal(signal.SIGHUP), previous)
        row = self.repository.get(calls[0])
        self.assertEqual((row['lifecycle'], row['termination_reason']), ('failed', 'supervisor_launch_failed'))
        self.launch.assert_not_called()

    def test_cancel_during_the_wait_ends_it_without_dispatch(self):
        state = {}
        def acquire(row, **_kwargs):
            if 'cancelled' not in state:
                state['cancelled'] = True
                self.repository.transition(row['job_id'], 'cancelled',
                                           termination_reason='cancelled_before_process_start')
                raise WorkspaceBusy('host capacity is busy', kind='capacity')
            raise WorkspaceBusy('job is already admitted', kind='admitted')
        original_service = self.service
        def keep(acquire_, temporary):
            service, repository, scheduler = original_service(acquire_, temporary)
            self.repository = repository
            return service, repository, scheduler
        self.service = keep
        receipt, repository, scheduler, _value = self.submit(acquire, wait=30)
        self.assertEqual(repository.get(receipt['job_id'])['lifecycle'], 'cancelled')
        self.assertEqual(scheduler.acquire.call_count, 2)
        self.launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
