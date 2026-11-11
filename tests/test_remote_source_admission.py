"""Deterministic byte races through real copies and real SQLite acceptance.

Definitions only in the source checkpoint. All subprocesses are synthetic local
Git fixtures, never SSH/Docker/provider operations.
"""
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.application.workspace_service import WorkspaceService
from sandbox.core import _paths, _remote
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage
from sandbox.transports.remote_jobs import RemoteJobTransport, workspace_refresh_command
from sandbox.workspaces.checkout import materialize, plan_materialization, WorkspaceMaterializationError
from sandbox.workspaces.content import content_digest, checkout_guard
from tests.subprocess_support import run_test_process, synthetic_environment


class RemoteSourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runtime = self.root / 'runtime'
        self.storage = JobStorage(self.runtime, free_disk_reserve=0)
        self.repository = JobRepository(self.storage.root / 'registry.sqlite3')
        self.addCleanup(self.repository.close)
        fixed = patch.object(_paths, 'RUNTIME_DIR', self.runtime)
        fixed.start()
        self.addCleanup(fixed.stop)
        self.source = self.root / 'deployed'
        self.source.mkdir()
        self.git('init', '-q', '-b', 'main')
        (self.source / 'version.txt').write_text('A')
        self.git('add', '.')
        self.git('commit', '-qm', 'A')
        self.commit_a = self.git('rev-parse', 'HEAD')
        self.identity = 'sha256:' + 'a' * 64
        self.launched = []
        self.service = JobService(self.repository, self.storage, None, launcher=self.launched.append)

    def git(self, *args):
        return run_test_process(['git', '-C', str(self.source), *args],
            env={'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                 'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                 'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'},
            timeout=5, capture_output=True, text=True, check=True).stdout.strip()

    def publish_b(self):
        (self.source / 'version.txt').write_text('B')
        self.git('add', '.')
        self.git('commit', '-qm', 'B')

    def plan(self, workspace, pin, request='original-A'):
        return plan_materialization(self.source, workspace, content_digest=pin,
            source_identity=self.identity, workspace_label='shared', request_id=request,
            project_identity='project:fixture')

    def submission(self, workspace, pin, *, kind='exec', request='original-A'):
        return JobSubmission(kind, str(workspace), 'project:fixture', 'local', 'shared',
            ('fixture-no-execution',), 60,
            SourceIdentity(self.identity, self.commit_a, content_digest=pin),
            request_id=request, materialization_source_root=str(self.source))

    def test_publish_b_between_deployment_and_copy_never_accepts_a_metadata(self):
        for dirty in (False, True):
            with self.subTest(dirty=dirty):
                self.git('reset', '--hard', self.commit_a)
                if dirty:
                    (self.source / 'version.txt').write_bytes(b'A-dirty\x00\xff')
                pin = content_digest(self.source)
                self.publish_b()
                workspace = self.root / ('copy-' + str(dirty))
                with self.assertRaisesRegex(WorkspaceMaterializationError, 'source bytes changed'):
                    materialize(self.plan(workspace, pin))
                self.assertFalse(workspace.exists())
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.launched, [])

    def test_single_matrix_ci_recheck_real_copied_bytes_at_acceptance(self):
        for kind in ('exec', 'test', 'ci'):
            for dirty in (False, True):
                with self.subTest(kind=kind, dirty=dirty):
                    self.git('reset', '--hard', self.commit_a)
                    if dirty:
                        (self.source / 'version.txt').write_bytes(b'A-dirty\x00\xff')
                    pin_a = content_digest(self.source)
                    workspace = self.root / ('workspace-' + kind + str(dirty))
                    materialize(self.plan(workspace, pin_a))
                    intended = self.submission(workspace, pin_a, kind=kind,
                                               request=kind + str(dirty))
                    self.publish_b()
                    materialize(self.plan(workspace, content_digest(self.source), request='B'))
                    self.assertEqual((workspace / 'version.txt').read_text(), 'B')
                    with self.assertRaisesRegex(ValueError, 'source_content_changed'):
                        self.service.submit(intended)
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.launched, [])

    def test_unchanged_clean_and_dirty_copies_accept_and_retain_measured_digest(self):
        for kind in ('exec', 'test', 'ci'):
            for dirty in (False, True):
                with self.subTest(kind=kind, dirty=dirty):
                    if dirty:
                        (self.source / 'dirty.bin').write_bytes(b'\x00\xffdirty')
                    else:
                        (self.source / 'dirty.bin').unlink(missing_ok=True)
                    pin = content_digest(self.source)
                    workspace = self.root / ('positive-' + kind + str(dirty))
                    materialize(self.plan(workspace, pin))
                    accepted = self.service.submit(self.submission(workspace, pin, kind=kind,
                        request='positive-' + kind + str(dirty)))
                    original = self.repository.submission_snapshot(accepted['job_id'])
                    self.assertEqual(original['source']['content_digest'], content_digest(workspace))

    def test_acceptance_blocks_later_same_label_copy_until_live_job_retires(self):
        pin_a = content_digest(self.source)
        workspace = self.root / 'shared-checkout'
        materialize(self.plan(workspace, pin_a))
        intended = self.submission(workspace, pin_a)
        self.publish_b()
        plan_b = self.plan(workspace, content_digest(self.source), request='B')
        results = []
        entered = threading.Event()
        original_accept = self.repository.accept
        def writer():
            entered.set()
            try:
                materialize(plan_b)
            except WorkspaceMaterializationError as exc:
                results.append(exc.code)
        thread = threading.Thread(target=writer)
        def accepting(*args, **kwargs):
            thread.start()
            self.assertTrue(entered.wait(2))
            return original_accept(*args, **kwargs)
        with patch.object(self.repository, 'accept', side_effect=accepting):
            accepted = self.service.submit(intended)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['workspace_materialization_busy'])
        self.assertEqual((workspace / 'version.txt').read_text(), 'A')
        self.assertEqual(self.repository.submission_snapshot(accepted['job_id'])['source']['content_digest'], pin_a)

    def test_original_request_replay_does_not_refresh_or_rebind_job_outputs(self):
        pin = content_digest(self.source)
        workspace = self.root / 'replay'
        plan = self.plan(workspace, pin)
        materialize(plan)
        submission = self.submission(workspace, pin)
        accepted = self.service.submit(submission)
        (workspace / 'job-output.txt').write_text('retain original output')
        self.publish_b()
        receipt = materialize(self.plan(workspace, pin))
        self.assertEqual(receipt.source_mutation_check, 'original-request-replay-no-refresh')
        replay = self.service.submit(submission)
        self.assertEqual(replay['job_id'], accepted['job_id'])
        self.assertTrue((workspace / 'job-output.txt').exists())

    def test_ci_uses_prepared_a_instead_of_recopied_current_b(self):
        pin = content_digest(self.source)
        workspace = self.root / 'ci-prepared'
        materialize(self.plan(workspace, pin))
        intended = self.submission(workspace, pin, kind='ci')
        self.publish_b()
        owner = WorkspaceService(None, deployment_root=self.root)
        index = self.root / 'index' / 'workspaces.json'
        index.parent.mkdir()
        with checkout_guard(workspace), patch.object(owner, '_repo',
                return_value=SimpleNamespace(index_path=index)):
            authority = owner._materialize_ci_checkout(intended)
            accepted = self.service.submit(intended)
        self.assertEqual(authority['receipt']['source_mutation_check'], 'content-verified')
        self.assertEqual((workspace / 'version.txt').read_text(), 'A')
        self.assertEqual(self.repository.submission_snapshot(accepted['job_id'])['source']['content_digest'], pin)

    def test_actual_matrix_refuses_changed_child_before_accepting_parent(self):
        pin_a = content_digest(self.source)
        children = []
        for i in range(2):
            workspace = self.root / ('matrix-child-' + str(i))
            materialize(self.plan(workspace, pin_a))
            children.append(replace(self.submission(workspace, pin_a, kind='test', request='matrix-' + str(i)),
                                    workspace_label='child-' + str(i), workspace_mode='isolated'))
        self.publish_b()
        materialize(self.plan(Path(children[1].project_root), content_digest(self.source), request='B'))
        with self.assertRaisesRegex(ValueError, 'source_content_changed'):
            self.service.submit_matrix(children, allow_project_variants=True)
        self.assertEqual(self.repository.list(), [])

    def test_actual_matrix_accepts_clean_and_dirty_pinned_children(self):
        (self.source / 'dirty.bin').write_bytes(b'\xff\x00dirty')
        pin = content_digest(self.source)
        children = []
        for i in range(2):
            workspace = self.root / ('positive-matrix-' + str(i))
            materialize(self.plan(workspace, pin))
            children.append(replace(self.submission(workspace, pin, kind='test', request='positive-matrix-' + str(i)),
                                    workspace_label='child-' + str(i), workspace_mode='isolated'))
        accepted = self.service.submit_matrix(children, allow_project_variants=True)
        self.assertEqual(len(accepted['children']), 2)
        for item in accepted['children']:
            original = self.repository.submission_snapshot(item['job_id'])
            self.assertEqual(original['source']['content_digest'], content_digest(original['project_root']))

    def test_legacy_canonical_digest_and_receipt_replay_are_not_rewritten(self):
        old = JobSubmission('exec', str(self.source), 'p', 'local', 'default', ('fixture',), 60,
                            SourceIdentity('old-source'), request_id='legacy')
        payload = old.as_dict()
        self.assertNotIn('content_digest', payload['source'])
        self.assertEqual(old.canonical_digest(), hashlib.sha256(
            old.canonical_json().encode()).hexdigest())
        row, _ = self.repository.accept(old)
        accepted = self.service.submit(old)
        self.assertEqual(accepted['job_id'], row['job_id'])

    def test_transport_single_matrix_and_ci_carry_the_same_content_pin(self):
        pin = content_digest(self.source)
        proof = {'target_path': str(self.source), 'identity': self.identity,
                 'content_digest': pin, 'commit': self.commit_a}
        calls = []
        transport = RemoteJobTransport(deploy=lambda *a, **k: proof,
            remote_lookup=lambda name: {'provisioned': True,
                'capabilities': ['job.exec', 'job.execution-policy.v1', 'job.source-content.v1']},
            ssh_run=lambda record, command, **kwargs: SimpleNamespace(returncode=0, stdout='{}'),
            remote_sb_path=lambda record: '/fixed/sb')
        def prepare(remote, source, label, **kwargs):
            calls.append((label, kwargs))
            return str(self.root / ('wire-' + label))
        with patch.object(transport, '_prepare_workspace', side_effect=prepare), \
                patch.object(transport, '_run', return_value=SimpleNamespace(returncode=1, stdout='{}', stderr='')):
            for kind in ('exec', 'ci'):
                item = replace(self.submission(self.source, pin, kind=kind),
                               target_kind='remote', remote_name='box')
                with self.assertRaises(Exception):
                    transport.submit(item)
            children = [replace(item, workspace_label='child' + str(i), request_id='matrix-' + str(i)) for i in range(2)]
            with self.assertRaises(Exception):
                transport.submit_many(children)
        self.assertEqual(len(calls), 4)
        for label, kwargs in calls:
            self.assertEqual(kwargs['source_proof']['content_digest'], pin)
            command = workspace_refresh_command(str(self.source), str(self.root / 'wire'),
                source_proof=kwargs['source_proof'], request_id=kwargs['request_id'],
                project_identity=kwargs['project_identity'])
            self.assertEqual(shlex.split(command)[shlex.split(command).index('--source-content-digest') + 1], pin)

    def test_production_push_cannot_update_worktree_even_with_inherited_updateinstead(self):
        self.git('config', 'receive.denyCurrentBranch', 'updateInstead')
        client = self.root / 'client'
        run_test_process(['git', 'clone', '-q', str(self.source), str(client)],
                         env={'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                         timeout=5, capture_output=True, check=True)
        (client / 'version.txt').write_text('B')
        for arguments in (('add', '.'), ('-c', 'user.name=fixture', '-c',
                'user.email=fixture@example.invalid', 'commit', '-qm', 'B')):
            run_test_process(['git', '-C', str(client), *arguments],
                             env={'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
                             timeout=5, capture_output=True, check=True)
        with patch.object(_remote, 'git_ssh_url', return_value=str(self.source)), \
                patch.object(_remote, 'git_ssh_command', return_value='/usr/bin/false'), \
                patch.object(_remote, '_ensure_ssh_control_dir'), \
                patch.object(_remote, 'git_environment', side_effect=lambda **kwargs: synthetic_environment(
                    {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})):
            pushed = _remote.push_commits({}, client, str(self.source), 'main')
        self.assertEqual((self.source / 'version.txt').read_text(), 'A')
        self.assertEqual(self.git('rev-parse', 'HEAD'), pushed)
        # The leased reset detaches HEAD before publication, making further
        # receive-pack branch updates unable to move the measured HEAD either.
        self.git('update-ref', '--no-deref', 'HEAD', self.commit_a)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.commit_a)

    def test_measurement_occurs_inside_original_lease_after_all_publication_steps(self):
        from unittest.mock import MagicMock
        events = []
        lease = MagicMock()
        lease.measure.side_effect = lambda root: events.append('measure') or content_digest(root)
        receipt = {}
        with patch.object(_remote, '_remote_materialization_lock', return_value=nullcontext(lease)), \
                patch.object(_remote, '_reset_target_to_unlocked', side_effect=lambda *a, **k: events.append('reset')), \
                patch.object(_remote, '_apply_uncommitted_unlocked', side_effect=lambda *a, **k: events.append('overlay') or 2):
            applied = _remote.update_target_to({}, str(self.source), self.commit_a,
                project_root=str(self.source), content_receipt=receipt)
        self.assertEqual(applied, 2)
        self.assertEqual(events, ['reset', 'overlay', 'measure'])
        self.assertEqual(receipt['content_digest'], content_digest(self.source))

    def test_missing_installed_content_contract_refuses_before_deployment(self):
        from sandbox.transports.remote_jobs import RemoteJobTransportError
        record = {'provisioned': True, 'capabilities': ['job.exec', 'job.execution-policy.v1'],
                  'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        def no_deploy(*args, **kwargs):
            self.fail('no source staging before real capability discovery')
        transport = RemoteJobTransport(deploy=no_deploy, ssh_run=no_deploy,
            remote_lookup=lambda name: record)
        observed = {'ok': True, 'schema_version': 1, 'ownership_marker': 'a' * 24,
                    'runtime_revision': 'b' * 24,
                    'capabilities': ['job.exec', 'job.execution-policy.v1']}
        item = replace(self.submission(self.source, content_digest(self.source)),
                       target_kind='remote', remote_name='box')
        with patch.object(transport, 'control', return_value=observed):
            with self.assertRaisesRegex(RemoteJobTransportError, 'measured-source capability'):
                transport.submit(item)

    def test_new_materialized_submission_cannot_omit_the_measured_binding(self):
        item = replace(self.submission(self.source, content_digest(self.source)),
                       source=SourceIdentity(self.identity, self.commit_a))
        with self.assertRaisesRegex(ValueError, 'measured_source_contract_required'):
            self.service.submit(item)
        self.assertEqual(self.repository.list(), [])

    def test_matching_client_hash_does_not_authorize_a_false_original_commit(self):
        self.publish_b()
        pin_b = content_digest(self.source)
        workspace = self.root / 'wrong-commit'
        materialize(self.plan(workspace, pin_b))
        # Actual byte hash B is correct, but source metadata still claims A.
        with self.assertRaisesRegex(ValueError, 'source_content_changed'):
            self.service.submit(self.submission(workspace, pin_b))
        self.assertEqual(self.repository.list(), [])
