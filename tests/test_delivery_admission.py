"""Regressions after the genuine local durable-child admission exercise."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sandbox.delivery import admission


class DurableAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path('/tmp/f054-synthetic-application').resolve()
        root_digest = 'sha256:' + hashlib.sha256(str(self.root).encode()).hexdigest()
        self.fields = dict(job_id='job-054', request_id='request-054',
            project_identity='project:fixture', project_root_digest=root_digest,
            source_identity=root_digest, source_commit='a' * 40, source_dirty_digest=None)
        environment = {'SANDBOX_DURABLE_' + key.upper(): value
                       for key, value in self.fields.items() if value is not None}
        self.fake_os = SimpleNamespace(environ=environment, defpath='/usr/bin:/bin',
            getpgid=lambda _pid: 101, getpgrp=lambda: 101)
        job = {key: self.fields[key] for key in (
            'job_id', 'request_id', 'project_identity', 'source_identity', 'source_commit', 'source_dirty_digest')}
        job.update(project_root=str(self.root), lifecycle='running', finished_at=None,
                   started_at='2026-09-09T06:00:00Z', target_kind='local', remote_name=None)
        self.boot = '11111111-2222-3333-4444-555555555555'
        self.raw = {'job': job, 'submitted': dict(job, version=1), 'process': {
            'child_pid': 101, 'child_pgid': 101, 'child_start_identity': 'observed-start',
            'host_boot_id': self.boot}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(admission, 'os', self.fake_os))
        self.reader = self.stack.enter_context(patch.object(admission,
            'read_delivery_job_evidence', return_value=self.raw))
        self.capture = self.stack.enter_context(patch.object(admission,
            'capture_process_identity', return_value=SimpleNamespace(
                host_boot_id=self.boot, start_identity='observed-start')))
        self.git = self.stack.enter_context(patch.object(admission.subprocess, 'run',
            side_effect=[SimpleNamespace(stdout='a' * 40 + '\n'), SimpleNamespace(stdout='')]))

    def validate(self):
        return admission.validate_durable_context(self.root, database_path='/tmp/fixture.sqlite',
            project_identity=self.fields['project_identity'], wait_seconds=0)

    def refused(self, code):
        with self.assertRaises(admission.AdmissionError) as caught:
            self.validate()
        self.assertEqual(caught.exception.code, code)

    def test_genuine_original_child_join_keeps_source_and_control_separate(self):
        self.assertEqual(self.validate(), self.fields)
        self.capture.assert_called_once_with(101)
        self.assertEqual(self.git.call_count, 2)
        for call in self.git.call_args_list:
            self.assertEqual(call.kwargs['env'],
                {'PATH': '/usr/bin:/bin', 'GIT_OPTIONAL_LOCKS': '0', 'LC_ALL': 'C'})

    def test_missing_context_never_reads_owner_or_source(self):
        self.fake_os.environ.pop('SANDBOX_DURABLE_JOB_ID')
        self.refused('recovery_context_required')
        self.reader.assert_not_called()
        self.git.assert_not_called()

    def test_changed_request_cannot_borrow_running_job(self):
        self.fake_os.environ['SANDBOX_DURABLE_REQUEST_ID'] = 'unrelated-request'
        self.refused('recovery_source_mismatch')
        self.capture.assert_not_called()
        self.git.assert_not_called()

    def test_remote_job_is_not_local_apply_authority(self):
        self.raw['job'].update(target_kind='remote', remote_name='fixture-remote')
        self.refused('recovery_context_invalid')
        self.capture.assert_not_called()

    def test_terminal_original_cannot_authorize_fresh_child(self):
        self.raw['job'].update(lifecycle='succeeded', finished_at='2026-09-09T06:01:00Z')
        self.refused('recovery_context_invalid')
        self.capture.assert_not_called()

    def test_unpublished_child_stays_refused_at_deadline(self):
        self.raw['process']['child_pid'] = None
        self.refused('recovery_context_invalid')
        self.capture.assert_not_called()

    def test_hostname_derived_boot_identity_is_not_accepted(self):
        self.raw['process']['host_boot_id'] = 'hostname-derived-legacy'
        self.capture.return_value.host_boot_id = 'hostname-derived-legacy'
        self.refused('recovery_context_invalid')
        self.git.assert_not_called()

    def test_other_process_group_cannot_borrow_context(self):
        self.fake_os.getpgrp = lambda: 202
        self.refused('recovery_context_invalid')
        self.git.assert_not_called()

    def test_dirty_source_is_not_clean_admission(self):
        self.git.side_effect = [SimpleNamespace(stdout='a' * 40), SimpleNamespace(stdout=' M app.py\n')]
        self.refused('recovery_source_dirty')

    def test_declared_overlay_refuses_before_owner_lookup(self):
        self.fake_os.environ['SANDBOX_DURABLE_SOURCE_DIRTY_DIGEST'] = 'sha256:' + 'b' * 64
        self.refused('recovery_source_dirty')
        self.reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
