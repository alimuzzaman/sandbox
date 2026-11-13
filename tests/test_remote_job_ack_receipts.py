"""Real retained SQLite receipts through isolated single/matrix transports."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sandbox.application.job_service import JobService
from sandbox.core import _remote
from sandbox.jobs.acceptance import CONTRACT
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage
from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError


class RetainedAckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.server = self.root / 'server'
        self.server.mkdir()
        self.repository = JobRepository(self.root / 'jobs.sqlite')
        self.addCleanup(self.repository.close)
        self.service = JobService(self.repository, JobStorage(self.server, free_disk_reserve=0),
                                  None, launcher=lambda _descriptor: None)
        self.source = SourceIdentity('sha256:deployed', 'a' * 40, 'b' * 64, 'sha256:' + 'e' * 64)
        server = JobSubmission('test', str(self.server), 'project', 'local', 'unit', ('echo', 'ok'),
                               60, self.source, request_id='once')
        row, _ = self.repository.accept(server)
        self.receipt = self.service._accepted(row, replay=False)
        self.deployed = {'target_path': str(self.server), 'identity': self.source.identity,
            'commit': self.source.commit, 'dirty_digest': self.source.dirty_digest, 'dirty': True,
            'content_digest': self.source.content_digest}
        self.client = replace(server, target_kind='remote', remote_name='box',
                              project_root=str(self.root), source=SourceIdentity('caller'))
        self.calls = []
        self.wire = copy.deepcopy(self.receipt)
        def run(_record, command, **_kwargs):
            self.calls.append(command)
            if isinstance(self.wire, BaseException):
                raise self.wire
            text = self.wire if isinstance(self.wire, str) else json.dumps(self.wire)
            return SimpleNamespace(returncode=0, stdout=text, stderr='')
        self.deploy = Mock(return_value=self.deployed)
        self.record = {'provisioned': True, 'capabilities': ['job.exec', 'job.execution-policy.v1', CONTRACT, 'job.source-content.v1']}
        self.transport = RemoteJobTransport(deploy=self.deploy, ssh_run=run,
                                             remote_lookup=lambda _name: self.record)
        self.enterContext(patch.object(self.transport, '_prepare_workspace', return_value=str(self.server)))

    def test_exact_retained_receipt_and_replay_preserve_original_digest(self):
        accepted = self.transport.submit(self.client)
        self.assertEqual(accepted['request_id'], 'once')
        self.assertEqual(accepted['request_digest'], self.receipt['request_digest'])
        self.assertEqual(accepted['source'], self.receipt['source'])
        self.assertEqual(len(self.calls), 1)
        row, replay = self.repository.accept(JobSubmission('test', str(self.server), 'project', 'local',
            'unit', ('echo', 'ok'), 60, self.source, request_id='once'))
        self.assertTrue(replay)
        self.assertEqual(self.service._accepted(row, replay=True)['request_digest'], accepted['request_digest'])

    def test_missing_or_mismatched_ack_keeps_original_request_unknown_without_resubmit(self):
        changes = [
            ('request_id', 'other'), ('request_id', None), ('acceptance_contract', 'older'),
            ('request_digest', 'invalid'), ('source.identity', 'other'),
            ('source.commit', 'c' * 40), ('source.dirty_digest', 'd' * 64), ('source.dirty', False),
            ('source.content_digest', 'sha256:' + 'f' * 64), ('source.content_digest', None),
            ('source.dirty', 1),
        ]
        missing = ('request_id', 'request_digest', 'acceptance_contract', 'source',
                   'source.identity', 'source.commit', 'source.dirty_digest', 'source.content_digest')
        for key, value, remove in [(k, v, False) for k, v in changes] + [(k, None, True) for k in missing]:
            with self.subTest(field=key, remove=remove):
                self.wire = copy.deepcopy(self.receipt)
                selected, name = (self.wire['source'], key.split('.')[1]) if key.startswith('source.') else (self.wire, key)
                if remove:
                    selected.pop(name)
                else:
                    selected[name] = value
                self.calls.clear()
                with self.assertRaises(RemoteJobTransportError) as error:
                    self.transport.submit(self.client)
                self.assertFalse(error.exception.retryable)
                self.assertEqual(error.exception.detail, {'acceptance': 'unknown', 'request_id': 'once'})
                self.assertEqual(len(self.calls), 1)

    def test_lost_or_malformed_ack_does_not_submit_a_second_request(self):
        for wire in ('', 'not JSON', subprocess.TimeoutExpired('fixture', 1)):
            with self.subTest(kind=type(wire).__name__):
                self.wire = wire
                self.calls.clear()
                with self.assertRaises(RemoteJobTransportError) as error:
                    self.transport.submit(self.client)
                self.assertEqual(error.exception.detail['request_id'], 'once')
                self.assertEqual(len(self.calls), 1)

    def test_missing_installed_receipt_contract_refuses_before_deployment(self):
        self.record['capabilities'].remove(CONTRACT)
        with self.assertRaises(RemoteJobTransportError):
            self.transport.submit(self.client)
        self.deploy.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_malformed_cached_capabilities_refuse_with_typed_error_before_deployment(self):
        for values in ([{}], [[]], ['job.exec', 'job.execution-policy.v1', CONTRACT, {}], None):
            with self.subTest(values=values):
                self.record['capabilities'] = values
                with self.assertRaises(RemoteJobTransportError):
                    self.transport.submit(self.client)
        self.deploy.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_direct_server_cli_malformed_capabilities_refuse_before_any_effect(self):
        record = {**self.record, 'joined': True, 'control_transport': 'tailscale'}
        for values in ([{}], [[]], ['job.server-cli.v1', CONTRACT, {}], None):
            with self.subTest(values=values):
                with self.assertRaises(RemoteJobTransportError) as error:
                    self.transport.submit_server_cli(replace(self.client, kind='server-cli'), record,
                                                      {'capabilities': values})
                self.assertFalse(error.exception.retryable)
        self.deploy.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_clean_deployment_empty_digest_becomes_absent_wire_flag_and_retained_none(self):
        self.deployed.update(dirty=False, dirty_digest='')
        clean = SourceIdentity(self.source.identity, self.source.commit, content_digest=self.source.content_digest)
        row, _ = self.repository.accept(JobSubmission('test', str(self.server), 'project', 'local', 'unit',
            ('echo', 'ok'), 60, clean, request_id='clean'))
        self.wire = self.service._accepted(row, replay=False)
        accepted = self.transport.submit(replace(self.client, request_id='clean'))
        self.assertIsNone(accepted['source']['dirty_digest'])
        self.assertNotIn('--source-dirty-digest', self.calls[0])

    def _real_clean_deployment_metadata(self):
        # Exercise the actual overlay/identity builder; replace only effect ports.
        def update(*args, **kwargs):
            kwargs['content_receipt']['content_digest'] = self.source.content_digest
            return 0
        with patch.object(_remote, 'capture_uncommitted', return_value=('', [])), \
                patch.object(_remote, 'remote_network_capacity_admission', return_value={'ok': True}), \
                patch.object(_remote, 'ensure_deploy_repo', return_value=str(self.server)), \
                patch.object(_remote, 'current_branch', return_value='dev'), \
                patch.object(_remote, 'push_commits', return_value='a' * 40), \
                patch.object(_remote, 'update_target_to', side_effect=update):
            result = _remote.deploy_exact_working_tree(self.record, self.root)
        self.assertFalse(result['dirty'])
        self.assertTrue(result['dirty_digest'], 'real clean overlays retain a fingerprint')
        return result

    def test_real_clean_deploy_metadata_passes_preparation_guard(self):
        self.transport._validate_deployment(self._real_clean_deployment_metadata())

    def test_real_clean_overlay_receipt_retains_fingerprint_and_reports_clean(self):
        result = self._real_clean_deployment_metadata()
        self.deployed.clear()
        self.deployed.update(result)
        clean_source = SourceIdentity(result['identity'], result['commit'], result['dirty_digest'], result['content_digest'])
        submission = JobSubmission('test', str(self.server), 'project', 'local', 'unit',
            ('echo', 'ok'), 60, clean_source, request_id='real-clean')
        row, _ = self.repository.accept(submission)
        self.wire = self.service._accepted(row, replay=False)
        self.assertFalse(self.wire['source']['dirty'])
        accepted = self.transport.submit(replace(self.client, request_id='real-clean'))
        self.assertEqual(accepted['source']['dirty_digest'], result['dirty_digest'])
        self.assertEqual(accepted['request_digest'], submission.canonical_digest())

    def test_inconsistent_deployment_metadata_refuses_before_workspace_or_job(self):
        original = dict(self.deployed)
        for change in ({'dirty': False}, {'commit': 0}, {'dirty_digest': True}, {'commit': 'line\nbreak'}):
            with self.subTest(change=change):
                self.deployed.clear()
                self.deployed.update(original, **change)
                with self.assertRaises(RemoteJobTransportError):
                    self.transport.submit(self.client)
        self.transport._prepare_workspace.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_matrix_receipts_match_each_original_workspace_despite_server_order(self):
        from tests.subprocess_support import run_test_process
        from sandbox.workspaces.content import content_digest
        from sandbox.services.source_fingerprint import EMPTY_OVERLAY_FINGERPRINT
        root = self.root / 'matrix-source'
        root.mkdir()
        (root / 'source.txt').write_text('measured matrix fixture')
        def git(*args):
            return run_test_process(['git', '-C', str(root), *args],
                env={'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                     'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                     'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'},
                capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        git('init', '-q')
        git('add', '.')
        git('commit', '-qm', 'matrix fixture')
        self.source = SourceIdentity('sha256:measured-matrix', git('rev-parse', 'HEAD'),
                                     EMPTY_OVERLAY_FINGERPRINT, content_digest(root))
        self.deployed.update(target_path=str(root), identity=self.source.identity,
            commit=self.source.commit, dirty_digest=self.source.dirty_digest,
            content_digest=self.source.content_digest, dirty=False)
        server_items = [JobSubmission('test', str(root), 'project', 'local', label,
            ('echo', label), 60 + index, self.source, workspace_mode='isolated', request_id=label)
            for index, label in enumerate(('first', 'second'))]
        self.wire = self.service.submit_matrix(server_items)
        self.wire['children'].reverse()
        clients = [replace(item, target_kind='remote', remote_name='box',
                           project_root=str(self.root), source=SourceIdentity('caller')) for item in server_items]
        result = self.transport.submit_many(clients)
        self.assertEqual(result['source'], self.wire['source'])
        self.assertEqual({item['request_id'] for item in result['children']}, {'first', 'second'})
        self.assertEqual(len(self.calls), 1)
        valid = copy.deepcopy(self.wire)
        for field, value in (('source', {**valid['source'], 'commit': 'f' * 40}),
                             ('workspace', {}), ('status', 'unknown'), ('ok', False),
                             ('request_id', 'foreign'), ('request_digest', 'invalid'),
                             ('execution_policy', {**valid['children'][0]['execution_policy'], 'deadline_seconds': 999})):
            with self.subTest(field=field):
                self.wire = copy.deepcopy(valid)
                self.wire['children'][0][field] = value
                self.calls.clear()
                with self.assertRaises(RemoteJobTransportError) as error:
                    self.transport.submit_many(clients)
                self.assertFalse(error.exception.retryable)
                self.assertEqual(error.exception.detail['requests'][0]['request_id'], 'first')
                self.assertEqual(len(self.calls), 1)
        for field, value in (('acceptance_contract', 'older'), ('request_id', 'foreign'),
                             ('request_digest', 'invalid'), ('source', None),
                             ('children', valid['children'][:1]),
                             ('children', [valid['children'][0], valid['children'][0]])):
            with self.subTest(parent_field=field):
                self.wire = copy.deepcopy(valid)
                self.wire[field] = value
                self.calls.clear()
                with self.assertRaises(RemoteJobTransportError):
                    self.transport.submit_many(clients)
                self.assertEqual(len(self.calls), 1)
