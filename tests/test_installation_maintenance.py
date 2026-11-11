"""Operation leases fail closed and never mask errors from the protected operation."""
from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.installation import maintenance
from sandbox.installation.binding import InstallationBindingError


class LeaseStore:
    def __init__(self, *, closed=False, busy=False):
        self.closed, self.busy, self.writes = closed, busy, []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    @contextmanager
    def lock(self, name, *, exclusive, create=False):
        if exclusive and name.endswith('.lock') and not name.endswith('.admission.lock') and self.busy:
            raise BlockingIOError()
        yield
    def read(self, name, *, private=False):
        return {'schema_version': 1, 'installation_id': 'a' * 32, 'request_id': 'original', 'closed': self.closed}
    def exists(self, name): return True
    def write(self, name, value, **kwargs):
        self.writes.append(value)
        self.closed = value['closed']


class MaintenanceLeaseTest(unittest.TestCase):
    def setUp(self):
        self.paths = SimpleNamespace(state=Path('/fixture/managed'), lease_root=Path('/fixture/leases'))
        self.identity = mock.patch.object(maintenance, 'read_installation_identity', return_value={'installation_id': 'a' * 32})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def guard(self):
        return maintenance.operation_guard(home='/fixture/personal', paths=self.paths)

    def test_open_supported_operation_runs_under_shared_lease(self):
        store = LeaseStore()
        with mock.patch.object(maintenance, 'RootDirectory', return_value=store), \
                mock.patch.object(maintenance.os.path, 'lexists', return_value=True):
            with self.guard():
                pass
        self.assertEqual(store.writes, [])

    def test_closed_admission_refuses_operation_without_mutation(self):
        store = LeaseStore(closed=True)
        with mock.patch.object(maintenance, 'RootDirectory', return_value=store), \
                mock.patch.object(maintenance.os.path, 'lexists', return_value=True):
            with self.assertRaises(InstallationBindingError) as failure:
                with self.guard():
                    self.fail('effect ran')
        self.assertEqual(failure.exception.code, 'installation_admission_closed')
        self.assertEqual(store.writes, [])

    def test_body_oserror_is_not_reclassified_as_lease_failure(self):
        store = LeaseStore()
        with mock.patch.object(maintenance, 'RootDirectory', return_value=store), \
                mock.patch.object(maintenance.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(OSError, 'actual operation failure'):
                with self.guard():
                    raise OSError('actual operation failure')

    def test_active_writer_prevents_close_without_interrupting_its_nested_work(self):
        store = LeaseStore(busy=True)
        authority = maintenance.RootMaintenance('a' * 32)
        with maintenance.ExitStack() as stack:
            authority.leases, authority.stack = store, stack
            authority.admin = mock.Mock()
            authority.admin.exists.return_value = False
            with self.assertRaises(InstallationBindingError) as failure:
                authority._close('a' * 32, 'new-request')
        self.assertEqual(failure.exception.code, 'installation_maintenance_busy')
        self.assertEqual(store.writes, [])

    def test_unprovisioned_personal_mode_keeps_old_behavior(self):
        with mock.patch.object(maintenance, 'read_installation_identity', side_effect=InstallationBindingError(
                'installation_binding_uninitialized', 'missing')), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(maintenance, 'RootDirectory', side_effect=AssertionError('root filesystem')):
            with self.guard():
                pass
