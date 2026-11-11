"""Cancellation follows original live processes when a listed child finishes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository


class ParentCancellationRaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.repository = JobRepository(self.root / 'jobs.sqlite')
        self.addCleanup(self.repository.close)
        self.service = JobService(self.repository, None, None)
        self.parent = self.accept('server-cli', 100)
        self.finished = self.accept('exec', 200, self.parent)
        self.live = self.accept('exec', 300, self.parent)

    def accept(self, kind, pid, parent=None):
        row, _ = self.repository.accept(JobSubmission(kind, str(self.root), 'fixture',
            'local', 'fixture', ('true',), 60, SourceIdentity('fixture'), parent_job_id=parent))
        job_id = row['job_id']
        self.repository.transition(job_id, 'running')
        self.repository.put_process_identity(job_id, host_boot_id='fixture-boot',
            supervisor_pid=pid + 1, supervisor_start_identity='supervisor-start',
            supervisor_nonce_hash='a' * 64, child_pid=pid, child_pgid=pid,
            child_start_identity='child-start')
        return job_id

    def test_terminal_child_race_still_signals_parent_and_remaining_live_child(self):
        for terminal in ('succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'):
            # Each interleaving uses a new repository and original PID records.
            if terminal != 'succeeded':
                self.repository.close()
                self.root = self.root / terminal
                self.root.mkdir()
                self.repository = JobRepository(self.root / 'jobs.sqlite')
                self.addCleanup(self.repository.close)
                self.service = JobService(self.repository, None, None)
                self.parent = self.accept('server-cli', 100)
                self.finished = self.accept('exec', 200, self.parent)
                self.live = self.accept('exec', 300, self.parent)
            original_children = self.repository.children
            def race(job_id):
                rows = original_children(job_id)
                if job_id == self.parent and self.repository.get(self.finished)['lifecycle'] == 'running':
                    self.repository.transition(self.finished, terminal)
                return rows
            with self.subTest(terminal=terminal), \
                 patch.object(self.repository, 'children', side_effect=race), \
                 patch('sandbox.application.job_service.verify_owned_process_identity', return_value=True), \
                 patch('sandbox.application.job_service.signal_owned_process_group', return_value=True) as signal:
                result = self.service.cancel(self.parent)
                self.assertEqual(result['lifecycle'], 'cancelling')
                self.assertEqual(self.repository.get(self.live)['lifecycle'], 'cancelling')
                self.assertEqual(self.repository.get(self.finished)['lifecycle'], terminal)
                self.assertEqual({call.args[0].process_group_id for call in signal.call_args_list}, {100, 300})

    def test_live_child_identity_failure_is_not_suppressed(self):
        with patch('sandbox.application.job_service.verify_owned_process_identity', return_value=False), \
             patch('sandbox.application.job_service.signal_owned_process_group') as signal:
            with self.assertRaisesRegex(RuntimeError, 'process_identity_mismatch'):
                self.service.cancel(self.parent)
            signal.assert_not_called()
        self.assertEqual(self.repository.get(self.parent)['lifecycle'], 'running')
