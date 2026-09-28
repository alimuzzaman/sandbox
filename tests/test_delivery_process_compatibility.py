"""Regressions from the mixed-client supervisor_lost incident and owner replay."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.models import Health
from sandbox.jobs.health import classify
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.jobs.process import ProcessIdentity, process_absence_proven
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage

BOOT = '11111111-2222-3333-4444-555555555555'


class ProcessCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.repo = JobRepository(self.home / 'jobs.sqlite3')
        self.addCleanup(self.repo.close)
        self.scheduler = Mock()
        self.scheduler.reconcile.return_value = []
        self.service = JobService(self.repo, JobStorage(self.home, free_disk_reserve=0),
                                  None, scheduler=self.scheduler)
        row, _ = self.repo.accept(JobSubmission('exec', str(self.home), 'project:fixture',
            'local', 'identity-fixture', ('fixture',), 30, SourceIdentity('fixture-source')))
        self.job_id = row['job_id']
        self.repo.transition(self.job_id, 'running')

    def identity(self, boot):
        self.repo.put_process_identity(self.job_id, host_boot_id=boot,
            supervisor_pid=101, supervisor_start_identity='supervisor-start',
            supervisor_nonce_hash='fixture', child_pid=102, child_pgid=102,
            child_start_identity='child-start')

    def test_legacy_record_stays_unchanged_when_new_reader_uses_uuid(self):
        self.identity('darwin:legacy-fixture')
        before = self.repo.snapshot(self.job_id)
        with patch('sandbox.application.job_service.capture_process_identity') as capture:
            capture.return_value = ProcessIdentity(BOOT, 101, 'supervisor-start', 'fixture')
            result = self.service.reconcile_startup()
        self.assertEqual(result['interrupted'], [])
        self.assertEqual(before, self.repo.snapshot(self.job_id))
        capture.assert_not_called()
        self.scheduler.release.assert_not_called()

    def test_unavailable_observation_does_not_prove_supervisor_loss(self):
        self.identity(BOOT)
        before = self.repo.snapshot(self.job_id)
        with patch('sandbox.application.job_service.capture_process_identity', return_value=None), \
             patch('sandbox.application.job_service.process_absence_proven', return_value=False):
            result = self.service.reconcile_startup()
        self.assertEqual(result['interrupted'], [])
        self.assertEqual(before, self.repo.snapshot(self.job_id))
        self.scheduler.release.assert_not_called()

    def test_proved_absent_supervisor_keeps_existing_interruption_behavior(self):
        self.identity(BOOT)
        with patch('sandbox.application.job_service.capture_process_identity', return_value=None), \
             patch('sandbox.application.job_service.process_absence_proven', return_value=True):
            result = self.service.reconcile_startup()
        self.assertEqual(result['interrupted'], [self.job_id])
        self.assertEqual(self.repo.get(self.job_id)['termination_reason'], 'supervisor_lost')
        self.scheduler.release.assert_called_once_with(self.job_id)

    def test_legacy_observation_is_not_a_new_boot(self):
        self.identity(BOOT)
        before = self.repo.snapshot(self.job_id)
        with patch('sandbox.application.job_service.capture_process_identity',
                   return_value=ProcessIdentity('darwin:legacy', 101, 'supervisor-start', 'fixture')):
            result = self.service.reconcile_startup()
        self.assertEqual(result['interrupted'], [])
        self.assertEqual(before, self.repo.snapshot(self.job_id))

    def test_permission_denial_is_not_process_absence(self):
        with patch('sandbox.jobs.process.os.kill', side_effect=PermissionError):
            self.assertFalse(process_absence_proven(101))
        with patch('sandbox.jobs.process.os.kill', side_effect=ProcessLookupError):
            self.assertTrue(process_absence_proven(101))

    def test_status_does_not_interrupt_legacy_owner(self):
        self.identity('darwin:legacy-fixture')
        before = self.repo.snapshot(self.job_id)
        result = self.service.get(self.job_id)
        self.assertEqual(result['lifecycle'], 'running')
        self.assertEqual(result['health'], 'unknown')
        self.assertIsNone(result['finished_at'])
        self.assertEqual(result['process'], before['process'])
        self.scheduler.release.assert_not_called()

    def test_status_unavailable_capture_is_unknown_not_missing(self):
        self.identity(BOOT)
        with patch('sandbox.jobs.health.capture_process_identity', return_value=None), \
             patch('sandbox.jobs.health.process_absence_proven', return_value=False), \
             patch('sandbox.jobs.health.os.kill', side_effect=PermissionError):
            health, _ = classify(self.repo.snapshot(self.job_id))
        self.assertEqual(health, Health.UNKNOWN)

    def test_stale_heartbeat_does_not_end_owned_supervisor(self):
        self.identity(BOOT)
        with patch('sandbox.application.job_service.classify',
                   return_value=(Health.SUPERVISOR_UNRESPONSIVE,
                                 {'reasons': ['stale heartbeat'], 'supervisor_identity_valid': True})), \
             patch.object(self.service, '_supervisor_is_owned', return_value=False) as reprobe:
            result = self.service.get(self.job_id)
        self.assertEqual(result['lifecycle'], 'running')
        self.scheduler.release.assert_not_called()
        reprobe.assert_not_called()

    def test_stale_heartbeat_requires_explicit_supervisor_loss(self):
        self.identity(BOOT)
        with patch('sandbox.application.job_service.classify',
                   return_value=(Health.SUPERVISOR_UNRESPONSIVE,
                                 {'reasons': ['supervisor absent'], 'supervisor_identity_valid': False})):
            result = self.service.get(self.job_id)
        self.assertEqual(result['lifecycle'], 'interrupted')
        self.scheduler.release.assert_called_once_with(self.job_id)

    def test_expired_lease_keeps_unresolved_workspace_and_capacity(self):
        self.identity('darwin:legacy-fixture')
        scheduler = JobScheduler(self.repo, min_free_memory_mb=0, min_free_disk_mb=0)
        scheduler.acquire(self.repo.get(self.job_id))
        self.assertEqual(scheduler.reconcile_stale(
            now=datetime.now(timezone.utc) + timedelta(days=2)), [])
        other, _ = self.repo.accept(JobSubmission('exec', str(self.home), 'project:fixture',
            'local', 'identity-fixture', ('fixture',), 30, SourceIdentity('fixture-source'),
            request_id='separate-request'))
        with self.assertRaises(WorkspaceBusy):
            scheduler.acquire(other)
        self.assertEqual([row['job_id'] for row in scheduler.active()], [self.job_id])


if __name__ == '__main__':
    unittest.main()
