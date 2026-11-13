"""Real anonymous stdin identity, digest and fixed supervisor handoff boundaries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.jobs.anonymous_input import (AnonymousInputUnavailable, MAX_INPUT_BYTES,
    capture_input, validate_inherited_input)
from sandbox.jobs.supervisor import _inherited_server_cli_stdin
from sandbox.application.job_service import JobService
from sandbox.application import server_cli
from sandbox.jobs.manifest import JobComponentRegistry
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import WorkspaceBusy
from sandbox.jobs.storage import JobStorage
from tests.test_server_cli import OWNER


class AnonymousInputTests(unittest.TestCase):
    def capture(self, data):
        with tempfile.TemporaryFile('w+b') as original:
            original.write(data)
            original.seek(0)
            value = capture_input(original.fileno(), timeout_seconds=1)
        self.addCleanup(value.close)
        return value

    def test_original_bytes_are_anonymous_and_only_metadata_is_serialized(self):
        data = b'synthetic original stdin\x00\n'
        value = self.capture(data)
        self.assertEqual(value.binding['size'], len(data))
        self.assertEqual(value.binding['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(os.fstat(value.fileno()).st_nlink, 0)
        self.assertNotIn('synthetic original', json.dumps(value.binding))
        self.assertNotIn('synthetic original', repr(value))
        self.assertEqual(os.read(value.fileno(), len(data)), data)

    def test_empty_input_is_real_bound_eof(self):
        value = self.capture(b'')
        self.assertEqual(value.binding['size'], 0)
        self.assertEqual(os.read(value.fileno(), 1), b'')

    def test_overflow_refuses_before_any_job_handoff(self):
        with tempfile.TemporaryFile('w+b') as original:
            original.write(b'x' * (MAX_INPUT_BYTES + 1))
            original.seek(0)
            with self.assertRaises(AnonymousInputUnavailable):
                capture_input(original.fileno(), timeout_seconds=1)

    def test_changed_bytes_named_file_and_another_anonymous_file_cannot_substitute(self):
        value = self.capture(b'fixture')
        other = self.capture(b'fixture')
        with self.assertRaises(AnonymousInputUnavailable):
            validate_inherited_input(other.fileno(), value.binding)
        with tempfile.NamedTemporaryFile() as named:
            named.write(b'fixture')
            named.flush()
            info = os.fstat(named.fileno())
            with self.assertRaises(AnonymousInputUnavailable):
                validate_inherited_input(named.fileno(), {**value.binding, 'device': info.st_dev, 'inode': info.st_ino})
        os.pwrite(value._file.fileno(), b'changed', 0)
        with self.assertRaises(AnonymousInputUnavailable):
            value.fileno()

    def test_supervisor_checks_actual_fd_against_retained_original_job(self):
        value = self.capture(b'fixture')
        current = {'kind': 'server-cli', 'execution_policy_provenance_json': json.dumps({
            'server_cli.stdin_sha256': value.binding['sha256'], 'server_cli.stdin_size': '7'})}
        descriptor = {'anonymous_input': value.binding}
        os.lseek(value.fileno(), 3, os.SEEK_SET)
        self.assertEqual(_inherited_server_cli_stdin(descriptor, current, fd=value.fileno()), value.fileno())
        self.assertEqual(os.read(value.fileno(), 7), b'fixture')
        for altered in ({**current, 'kind': 'exec'}, {**current, 'execution_policy_provenance_json': '{}'}):
            with self.subTest(altered=altered), self.assertRaises(RuntimeError):
                _inherited_server_cli_stdin(descriptor, altered, fd=value.fileno())
        self.assertEqual(_inherited_server_cli_stdin({}, current), subprocess.DEVNULL)

    def test_detached_launcher_passes_actual_stdin_without_fd_in_argv_or_environment(self):
        value = self.capture(b'fixture')
        service = JobService(repository=None, storage=None, components=None)
        with mock.patch('sandbox.application.job_service.subprocess.Popen') as spawn:
            service._launch(Path('/synthetic/descriptor.json'), anonymous_input=value)
        self.assertEqual(spawn.call_args.kwargs['stdin'], value.fileno())
        self.assertNotIn('env', spawn.call_args.kwargs)
        self.assertEqual(spawn.call_args.kwargs['start_new_session'], True)
        self.assertNotIn(str(value.fileno()), spawn.call_args.args[0])

    def test_no_input_can_be_inferred_from_caller_provenance_or_dictionary(self):
        service = JobService(repository=None, storage=None, components=None)
        submission = SimpleNamespace(kind='server-cli', execution_policy_provenance={
            'server_cli.stdin_sha256': 'a' * 64})
        with self.assertRaises(AnonymousInputUnavailable):
            service.submit(submission)
        with self.assertRaises(AnonymousInputUnavailable):
            service.submit(submission, anonymous_input={'fd': 0})

    def test_capacity_refusal_retains_terminal_receipt_and_never_queues_lost_stdin(self):
        value = self.capture(b'fixture')
        with tempfile.TemporaryDirectory() as temporary:
            repository = JobRepository(Path(temporary) / 'registry.sqlite')
            self.addCleanup(repository.close)
            components = JobComponentRegistry()
            components.register('server_cli', server_cli.FixedServerCLI(), owner='fixture')
            scheduler = mock.Mock()
            scheduler.acquire.side_effect = WorkspaceBusy('synthetic occupied slot')
            launch = mock.Mock(side_effect=AssertionError('no effect dispatch'))
            service = JobService(repository, JobStorage(temporary, free_disk_reserve=0),
                components, launcher=launch, scheduler=scheduler)
            submission = JobSubmission('server-cli', temporary, 'fixture', 'local', 'server-cli',
                (OWNER.executable, 'host', 'stack', 'list'), 5, SourceIdentity('fixture'),
                request_id='anonymous-capacity', execution_policy_provenance={
                    'server_cli.stdin_sha256': value.binding['sha256'], 'server_cli.stdin_size': '7'})
            with mock.patch.object(server_cli, 'installed_cli', return_value=Path(OWNER.executable)), \
                    mock.patch.object(server_cli.FixedServerCLI, 'environment', return_value={}), \
                    mock.patch.object(service, '_finalize_terminal_workspace'):
                receipt = service.submit(submission, anonymous_input=value)
            self.assertTrue(receipt['ok'])
            row = repository.get(receipt['job_id'])
            self.assertEqual(row['lifecycle'], 'failed')
            self.assertEqual(row['termination_reason'], 'anonymous_stdin_capacity_unavailable')
            launch.assert_not_called()
            scheduler.queue_details.assert_not_called()
