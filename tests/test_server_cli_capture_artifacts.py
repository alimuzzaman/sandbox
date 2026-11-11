"""Exact private byte artifacts; ordinary logs retain only bound metadata."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

from sandbox.application import server_cli, server_cli_capture as capture, server_cli_exec
from sandbox.application.job_service import JobService
from sandbox.core import _paths, _remote
from sandbox.hosting.host_exec import ExecOutcome, OK, FAILED, WATCHDOG_EXPIRED
from sandbox.jobs.artifacts import ArtifactError, PRIVATE_CAPTURE_KIND, PRIVATE_CAPTURE_MAX_BYTES, read_private_capture
from sandbox.jobs.models import JobSubmission, SourceIdentity, OutputQuery
from sandbox.jobs.output import JobOutputStore
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage, StoragePressureError
from sandbox.services.redaction import StreamingRedactor


class CaptureArtifactsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name).resolve()
        source = self.runtime / 'source'
        source.mkdir(mode=0o700)
        self.storage = JobStorage(self.runtime, free_disk_reserve=0)
        self.repository = JobRepository(self.storage.root / 'registry.sqlite3')
        self.addCleanup(self.repository.close)
        owner = server_cli.ServerCliOwner(os.geteuid(), str(self.runtime), str(self.runtime.parent),
            str(self.runtime / 'sb'), 'c' * 24, 'a' * 24, 'b' * 24)
        self.deadline = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        source_identity = 'sha256:' + hashlib.sha256(str(source).encode()).hexdigest()
        self.proof = {'server_cli.target_identity': owner.target_identity,
            'server_cli.ownership_marker': owner.ownership_marker, 'server_cli.runtime_revision': owner.runtime_revision,
            'server_cli.remote_name': 'box', 'server_cli.exec_service': 'wordpress', 'server_cli.exec_target': 'fixture/dev',
            'server_cli.target_metadata': '{"front_door":"nginx"}',
            'server_cli.target_selector_digest': 'sha256:' + 'd' * 64, 'server_cli.output_contract': capture.OUTPUT_CONTRACT,
            'server_cli.absolute_deadline_at': self.deadline,
            'server_cli.target_snapshot': json.dumps({'schema_version': 1, 'origin_remote_name': 'box',
                'target_identity': owner.target_identity, 'ownership_marker': owner.ownership_marker,
                'runtime_revision': owner.runtime_revision, 'name': 'fixture', 'copy_id': '1234abcd',
                'source_dir': str(source), 'compose_files': [str(source / 'compose.yml')],
                'native_keys': ['box/fixture/dev'], 'selector_digest': 'sha256:' + 'd' * 64,
                'source_commit': '1' * 40, 'source_origin_commit': '2' * 40,
                'project': 'fixture', 'environment': 'dev',
                'services': [{'name': 'wordpress', 'index': 1, 'container_id': 'f' * 64}]})}
        submission = JobSubmission('server-cli', str(source), 'e' * 64, 'local', 'server-exec',
            (owner.executable, 'host', 'exec', '--remote', 'box', '--exec-capture-artifacts'),
            60, SourceIdentity(source_identity, '1' * 40), request_id='capture-fixture',
            execution_policy_provenance=self.proof)
        row, _ = self.repository.accept(submission)
        self.job_id = row['job_id']
        self.storage.job_dir(self.job_id, create=True)
        self.repository.transition(self.job_id, 'running')
        self.context = server_cli.ServerCLIContext(1, self.job_id, 'capture-fixture', str(source), 'e' * 64,
            source_identity, '1' * 40, None, owner.target_identity, owner.runtime_revision,
            self.deadline, submission.argv[1:], 'box', MappingProxyType({'front_door': 'nginx'}), owner,
            target_selector_digest='sha256:' + 'd' * 64)
        self.service = JobService(self.repository, self.storage, None)
        self.receipt = {'ok': True, 'job_id': self.job_id, 'request_id': 'capture-fixture',
            'selector_digest': self.context.target_selector_digest,
            'source': {'identity': source_identity, 'commit': '1' * 40},
            'execution_policy': {'provenance': self.proof}}
        self.real_open_existing = JobStorage.open_existing
        self.real_current_context = server_cli.current_server_cli_context
        for patch in (mock.patch.object(_paths, 'RUNTIME_DIR', self.runtime),
                mock.patch.object(server_cli, 'current_server_cli_context', return_value=self.context),
                mock.patch.object(JobStorage, 'open_existing', return_value=self.storage)):
            patch.start()
            self.addCleanup(patch.stop)
        self.commands = []

    def control(self, argv):
        self.commands.append(argv)
        if argv[0] == 'job-artifacts':
            return {'ok': True, 'artifacts': self.service.list_artifacts(self.job_id)}
        self.assertEqual(argv[:2], ['job-artifact-get', self.job_id])
        identifier = argv[2]
        offset = int(argv[argv.index('--offset') + 1])
        maximum = int(argv[argv.index('--max-bytes') + 1])
        self.assertLessEqual(maximum, 524288)
        row = next(item for item in self.service.list_artifacts(self.job_id) if item['artifact_id'] == identifier)
        data = self.service.get_artifact(self.job_id, identifier, offset=offset, max_bytes=maximum)
        return {'ok': True, 'job_id': self.job_id, 'artifact_id': identifier, 'offset': offset,
            'data': base64.b64encode(data).decode(), 'bytes_read': len(data), 'encoding': 'base64',
            'size_bytes': row['size_bytes'], 'sha256': row['sha256'], 'next_offset': offset + len(data),
            'has_more': offset + len(data) < row['size_bytes']}

    def publish(self, stdout=b'fixture', stderr=b'', exit_code=0):
        with capture.prepare_server_cli_capture() as custody:
            return custody.publish(ExecOutcome(exit_code, OK if exit_code == 0 else FAILED, 1,
                                              stdout=stdout, stderr=stderr))

    def decode(self, envelope, *, state=None, receipt=None, control=None):
        return capture.read_server_cli_capture(envelope, receipt=receipt or self.receipt,
            state=state or self.repository.snapshot(self.job_id), control=control or self.control)

    def test_exact_binary_roundtrip_avoids_redactor_canary_and_payload_in_logs(self):
        payload = bytes(range(256)) + b'AIza' + b'x' * 40 + b' API_TOKEN=synthetic-fixture-only\x00\xff'
        redactor = StreamingRedactor()
        self.assertNotEqual(redactor.feed(payload) + redactor.finish(), payload)
        envelope = self.publish(payload, b'\xff\x00stderr')
        self.assertNotIn('stdout', envelope)
        self.assertNotIn('stderr', envelope)
        output = JobOutputStore(self.storage, self.repository, self.job_id)
        output.append('stdout', json.dumps(envelope).encode())
        output.finish('stdout')
        logged = output.read(OutputQuery(stream='stdout', max_bytes=65536))['data']
        self.assertNotIn('AIza', logged)
        self.assertNotIn('synthetic-fixture-only', logged)
        self.assertEqual(json.loads(logged), envelope)
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        decoded = self.decode(envelope)
        self.assertEqual(decoded['stdout_bytes'], payload)
        self.assertEqual(decoded['stderr_bytes'], b'\xff\x00stderr')
        self.assertEqual(decoded['output_encoding'], 'artifact-bytes')
        rows = self.service.list_artifacts(self.job_id)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row['kind'], PRIVATE_CAPTURE_KIND)
            path = self.storage.job_dir(self.job_id) / row['stored_relative_path']
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            expiry = datetime.fromisoformat(row['expires_at'])
            self.assertLessEqual((expiry - created).total_seconds(), capture.RETENTION_SECONDS)

    def test_zero_bytes_failed_exit255_and_two_chunk_stream_are_exact(self):
        payload = bytes(range(256)) * (PRIVATE_CAPTURE_MAX_BYTES // 256)
        envelope = self.publish(payload, b'', exit_code=255)
        self.repository.transition(self.job_id, 'failed', exit_code=255)
        decoded = self.decode(envelope)
        self.assertEqual(decoded['stdout_bytes'], payload)
        self.assertEqual(decoded['stderr_bytes'], b'')
        self.assertEqual(decoded['exit_code'], 255)
        gets = [item for item in self.commands if item[0] == 'job-artifact-get']
        self.assertEqual(len(gets), 3)  # manifest and two bounded stdout pages

    def test_actual_sdk_result_uses_artifacts_and_returns_paired_bytes_only_after_terminal(self):
        payload = b'\x00\xff' + b'AIza' + b'x' * 40
        envelope = self.publish(payload, b'\x80stderr')
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        body = json.dumps(envelope).encode()
        def control(remote_name, argv, *, timeout):
            self.assertEqual(remote_name, 'box')
            self.assertLessEqual(timeout, 2)
            if argv[0] == 'job-status':
                return {'ok': True, **self.repository.snapshot(self.job_id)}
            if argv[0] == 'job-output':
                return {'ok': True, 'job_id': self.job_id, 'stream': 'stdout', 'encoding': 'base64',
                    'data': base64.b64encode(body).decode(), 'bytes_read': len(body), 'has_more': False}
            return self.control(argv)
        with mock.patch.object(_remote, 'get_remote', return_value={
                'joined': True, 'control_transport': 'tailscale',
                'mcp_service': {'ownership_marker': self.context.owner.ownership_marker}}), \
                mock.patch('sandbox.resources.context.authenticated_target_identity',
                    return_value={'target_identity': self.context.target_identity}), \
                mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control', side_effect=control), \
                mock.patch.object(_remote, 'ssh_run', side_effect=AssertionError('no network')):
            result = server_cli_exec.read_server_cli_exec_result('box', self.receipt, budget_seconds=2)
        self.assertEqual(result['result']['stdout_bytes'], payload)
        self.assertEqual(result['result']['stderr_bytes'], b'\x80stderr')
        self.assertNotIn('stdout', result['result'])
        self.assertNotIn('stderr', result['result'])

    def test_changed_source_target_job_or_cancelled_state_never_returns_bytes(self):
        envelope = self.publish()
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        state = self.repository.snapshot(self.job_id)
        for change in ({'source_commit': 'f' * 40}, {'project_identity': 'f' * 64},
                       {'job_id': 'f' * 32}, {'lifecycle': 'cancelled'}):
            self.commands.clear()
            with self.subTest(change=change), self.assertRaises(capture.CaptureUnavailable):
                self.decode(envelope, state={**state, **change})
        wrong = copy.deepcopy(self.receipt)
        wrong['execution_policy']['provenance']['server_cli.exec_target'] = 'other/dev'
        with self.assertRaises(capture.CaptureUnavailable):
            self.decode(envelope, receipt=wrong)

    def test_missing_unknown_or_corrupted_artifact_is_not_replaced_or_replayed(self):
        envelope = self.publish()
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        with self.assertRaises(capture.CaptureUnavailable):
            self.decode(envelope, control=lambda argv: {'ok': True, 'artifacts': []})
        row = next(item for item in self.service.list_artifacts(self.job_id)
                   if item['artifact_id'] == capture.artifact_id(self.job_id, 'stdout'))
        path = self.storage.job_dir(self.job_id) / row['stored_relative_path']
        path.write_bytes(b'changed')
        with self.assertRaises(capture.CaptureUnavailable):
            self.decode(envelope)
        self.assertEqual(path.read_bytes(), b'changed')

    def test_unknown_manifest_id_is_rejected_before_any_artifact_request(self):
        envelope = self.publish()
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        changed = {**envelope, 'capture': {**envelope['capture'], 'manifest_artifact_id': 'f' * 24}}
        control = mock.Mock(side_effect=AssertionError('no arbitrary artifact lookup'))
        with self.assertRaises(capture.CaptureUnavailable):
            self.decode(changed, control=control)
        control.assert_not_called()

    def test_supported_retention_cleanup_keeps_original_job_but_removes_byte_availability(self):
        envelope = self.publish()
        self.repository.transition(self.job_id, 'succeeded', exit_code=0)
        result = self.service.cleanup(self.job_id, logs=False, artifacts=True, metrics=False)
        self.assertTrue(result['ok'])
        self.assertIn('artifacts', result['removed'])
        self.assertEqual(self.repository.get(self.job_id)['request_id'], 'capture-fixture')
        with self.assertRaises(capture.CaptureUnavailable):
            self.decode(envelope)

    def test_expired_and_linked_private_artifacts_fail_before_returning_data(self):
        self.publish()
        row = self.service.list_artifacts(self.job_id)[0]
        expired = {**row, 'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
        with self.assertRaises(ArtifactError):
            read_private_capture(self.storage, self.job_id, expired, offset=0, max_bytes=16)
        excessive = {**row, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()}
        with self.assertRaises(ArtifactError):
            read_private_capture(self.storage, self.job_id, excessive, offset=0, max_bytes=16)
        path = self.storage.job_dir(self.job_id) / row['stored_relative_path']
        replacement = path.with_name('synthetic-other')
        replacement.write_bytes(b'unrelated fixture bytes')
        path.unlink()
        path.symlink_to(replacement)
        with self.assertRaises(ArtifactError):
            self.service.get_artifact(self.job_id, row['artifact_id'], max_bytes=16)

    def test_cancellation_never_publishes_positive_custody(self):
        with capture.prepare_server_cli_capture() as custody:
            self.repository.transition(self.job_id, 'cancelling')
            with self.assertRaises(capture.CaptureUnavailable):
                custody.publish(ExecOutcome(0, OK, 1, stdout=b'fixture'))
        self.assertEqual(self.service.list_artifacts(self.job_id), [])

    def test_unknown_completion_and_oversize_are_not_positive_custody(self):
        for outcome in (ExecOutcome(124, WATCHDOG_EXPIRED, 1, stdout=b'fixture'),
                        ExecOutcome(0, OK, 1, stdout=b'x' * (PRIVATE_CAPTURE_MAX_BYTES + 1))):
            with self.subTest(code=outcome.code), capture.prepare_server_cli_capture() as custody:
                with self.assertRaises(capture.CaptureUnavailable):
                    custody.publish(outcome)
            self.assertEqual(self.service.list_artifacts(self.job_id), [])
        with mock.patch.object(self.storage, 'require_capacity', side_effect=StoragePressureError('synthetic reserve')):
            with self.assertRaises(capture.CaptureUnavailable):
                capture.prepare_server_cli_capture()

    def test_publication_uncertainty_preserves_private_files_without_ack_or_registry_partial(self):
        custody = capture.prepare_server_cli_capture()
        with mock.patch.object(custody._repository, 'add_artifact', side_effect=RuntimeError('synthetic publication failure')):
            with self.assertRaises(capture.CaptureUnavailable):
                custody.publish(ExecOutcome(0, OK, 1, stdout=b'fixture'))
        custody.close()  # idempotent on setup/no-publish cleanup paths
        self.assertEqual(self.service.list_artifacts(self.job_id), [])
        files = list((self.storage.job_dir(self.job_id) / 'artifacts').iterdir())
        self.assertEqual(len(files), 3)  # existing supported job cleanup owns uncertain bytes
        for path in files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_same_original_result_reuses_committed_custody_without_overwrite(self):
        first = self.publish(b'original')
        second = self.publish(b'original')
        self.assertEqual(first, second)
        self.assertEqual(len(self.service.list_artifacts(self.job_id)), 3)
        with self.assertRaises(capture.CaptureUnavailable):
            self.publish(b'different')

    def test_real_current_context_reader_can_publish_without_a_second_writer_under_its_own_lock(self):
        self.repository.put_process_identity(self.job_id, host_boot_id='fixture-boot',
            supervisor_pid=1001, supervisor_start_identity='supervisor-start',
            supervisor_nonce_hash='a' * 64, child_pid=1000, child_pgid=1000,
            child_start_identity='child-start')
        from types import SimpleNamespace
        def identity(pid):
            return SimpleNamespace(host_boot_id='fixture-boot',
                start_identity='child-start' if pid == 1000 else 'supervisor-start')
        with mock.patch.object(server_cli, 'current_server_cli_context', self.real_current_context), \
                mock.patch.object(server_cli.os, 'environ', {'SANDBOX_DURABLE_JOB_ID': self.job_id}), \
                mock.patch.object(server_cli.os, 'getpid', return_value=1000), \
                mock.patch.object(server_cli.os, 'getppid', return_value=1001), \
                mock.patch.object(server_cli, '_current_owner', return_value=self.context.owner), \
                mock.patch.object(server_cli, 'installed_cli', return_value=Path(self.context.owner.executable)), \
                mock.patch('sandbox.jobs.process.capture_process_identity', side_effect=identity):
            envelope = self.publish(b'exact bytes')
        self.assertEqual(envelope['capture']['contract'], capture.CONTRACT)
        self.assertEqual(len(self.service.list_artifacts(self.job_id)), 3)

    def test_same_anchor_lookup_rejects_replaced_directory(self):
        custody = capture.prepare_server_cli_capture()
        directory = self.storage.job_dir(self.job_id) / 'artifacts'
        directory.rename(directory.with_name('retained-original'))
        directory.mkdir(mode=0o700)
        with self.assertRaises(capture.CaptureUnavailable):
            custody.publish(ExecOutcome(0, OK, 1, stdout=b'fixture'))
        self.assertEqual(self.service.list_artifacts(self.job_id), [])

    def test_readonly_existing_storage_does_not_create_or_change_directories(self):
        value = self.real_open_existing(self.runtime, free_disk_reserve=0)
        self.assertEqual(value.root, self.storage.root)
        missing = self.runtime / 'absent-runtime'
        with self.assertRaises(FileNotFoundError):
            self.real_open_existing(missing, free_disk_reserve=0)
        self.assertFalse(missing.exists())
        self.storage.root.chmod(0o755)
        with self.assertRaises(ValueError):
            self.real_open_existing(self.runtime, free_disk_reserve=0)
        self.assertEqual(stat.S_IMODE(self.storage.root.stat().st_mode), 0o755)
