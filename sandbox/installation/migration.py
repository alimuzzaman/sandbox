"""Plan-bound retained-source migration with durable crash boundaries.

Adapters own actual inventory, encrypted recovery, workload fencing and service
switching. Missing adapters refuse before any effect. No raw credential access,
registry edits, volume moves or global path replacement belongs here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Protocol

from .binding import InstallationBindingError, _directory, _read, _write, validate_current


class MigrationOperations(Protocol):
    def inventory(self, source: str, destination: str) -> dict: ...
    def fence(self, plan: dict, request_id: str) -> dict: ...
    def recovery_proof(self, plan: dict, request_id: str) -> dict: ...
    def stage(self, plan: dict, request_id: str) -> dict: ...
    def verify_stage(self, plan: dict, request_id: str) -> dict: ...
    def activate(self, plan: dict, request_id: str) -> dict: ...
    def rollback(self, plan: dict, request_id: str) -> dict: ...
    def writes_observed(self, plan: dict) -> bool | None: ...


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def fail(code, message):
    raise InstallationBindingError(code, message)


def _inventory(value):
    fields = {'inventory_digest', 'active_writers', 'unresolved_leases', 'unresolved_binds',
              'conflicts', 'required_bytes', 'available_bytes', 'required_inodes', 'available_inodes'}
    if not isinstance(value, dict) or set(value) != fields:
        fail('migration_inventory_invalid', 'migration inventory is incomplete')
    if not re.fullmatch(r'[0-9a-f]{64}', str(value['inventory_digest'])):
        fail('migration_inventory_invalid', 'inventory identity is invalid')
    for name in fields - {'inventory_digest'}:
        if type(value[name]) is not int or value[name] < 0:
            fail('migration_inventory_invalid', 'inventory counters must be nonnegative integers')
    return value


class MigrationCoordinator:
    def __init__(self, journal_root: Path, operations: MigrationOperations, *, binding_reader=validate_current):
        self.root = Path(journal_root)
        self.operations = operations
        self.binding_reader = binding_reader

    def _require_adapters(self):
        for operation in ('inventory', 'fence', 'recovery_proof', 'stage', 'verify_stage',
                          'activate', 'rollback', 'writes_observed'):
            if not callable(getattr(self.operations, operation, None)):
                fail('migration_adapter_unavailable', 'required supported migration adapter is unavailable')

    def plan(self, binding: dict, source: str, destination: str) -> dict:
        self._require_adapters()
        self.binding_reader(binding, home=source)
        old, new = Path(source), Path(destination)
        if (not old.is_absolute() or not new.is_absolute() or old == new
                or old in new.parents or new in old.parents or new.is_symlink()):
            fail('migration_paths_invalid', 'migration paths must be distinct nonoverlapping absolute roots')
        inventory = _inventory(self.operations.inventory(source, destination))
        plan = {'schema_version': 1, 'binding': dict(binding), 'source': source,
                'destination': destination, 'inventory': inventory, 'retain_source': True}
        plan['plan_id'] = digest(plan)
        return plan

    @contextmanager
    def _journal(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            fail('migration_request_invalid', 'stable migration request identity is required')
        self.root.mkdir(mode=0o700, parents=False, exist_ok=True)
        _directory(self.root, os.geteuid(), private=True)
        fd = os.open(self.root / (request_id + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                fail('migration_authority_invalid', 'migration journal lock is invalid')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail('migration_busy', 'another migration writer owns this request')
            yield self.root / (request_id + '.json')
        finally:
            os.close(fd)

    def execute(self, plan: dict, request_id: str, *, confirm=False) -> dict:
        if confirm is not True:
            fail('confirmation_required', 'migration requires explicit confirmation')
        self._require_adapters()
        if (not isinstance(plan, dict) or set(plan) != {'schema_version', 'binding', 'source',
                'destination', 'inventory', 'retain_source', 'plan_id'}
                or type(plan.get('schema_version')) is not int or plan['schema_version'] != 1
                or plan['retain_source'] is not True
                or plan.get('plan_id') != digest({k: v for k, v in plan.items() if k != 'plan_id'})):
            fail('migration_plan_invalid', 'migration plan identity is invalid')
        _inventory(plan.get('inventory'))
        with self._journal(request_id) as path:
            if os.path.lexists(path):
                journal = _read(path, os.geteuid())
                if journal.get('plan_id') != plan['plan_id']:
                    fail('migration_request_conflict', 'request already belongs to another migration plan')
            else:
                self.binding_reader(plan['binding'], home=plan['source'])
                fresh = _inventory(self.operations.inventory(plan['source'], plan['destination']))
                if fresh != plan['inventory']:
                    fail('migration_plan_stale', 'migration inventory changed')
                if (fresh['active_writers'] or fresh['unresolved_leases'] or fresh['unresolved_binds']
                        or fresh['conflicts'] or fresh['required_bytes'] > fresh['available_bytes']
                        or fresh['required_inodes'] > fresh['available_inodes']):
                    fail('migration_prerequisites_unmet', 'writers, leases, binds, conflicts or storage block migration')
                journal = {'schema_version': 1, 'plan_id': plan['plan_id'], 'request_id': request_id,
                           'phase': 'planned', 'pending': None, 'evidence': {}}
                _write(path, journal)
            transitions = (('planned', 'fenced', 'fence'), ('fenced', 'recovery_verified', 'recovery_proof'),
                           ('recovery_verified', 'staged', 'stage'), ('staged', 'verified', 'verify_stage'),
                           ('verified', 'activated', 'activate'))
            for before, after, operation in transitions:
                if journal['phase'] != before:
                    continue
                if journal.get('pending'):
                    # Unknown effect is never automatically replayed. The adapter
                    # must expose explicit reconciliation before this request resumes.
                    fail('migration_acceptance_unknown', 'pending migration effect requires supported reconciliation')
                if before != 'verified':
                    self.binding_reader(plan['binding'], home=plan['source'])
                journal['pending'] = operation
                _write(path, journal)
                evidence = getattr(self.operations, operation)(plan, request_id)
                self._accept(operation, evidence, plan)
                journal['evidence'][operation] = evidence
                journal['phase'], journal['pending'] = after, None
                _write(path, journal)
            return journal

    def _accept(self, operation, evidence, plan):
        if not isinstance(evidence, dict) or evidence.get('ok') is not True:
            fail('migration_step_unverified', 'migration adapter did not return affirmative evidence')
        if evidence.get('plan_id') != plan['plan_id']:
            fail('migration_evidence_mismatch', 'migration evidence belongs to another plan')
        if operation == 'recovery_proof':
            if (evidence.get('encrypted_verified') is not True or evidence.get('restore_verified') is not True
                    or evidence.get('inventory_digest') != plan['inventory']['inventory_digest']
                    or not isinstance(evidence.get('set_id'), str) or not evidence['set_id']):
                fail('migration_recovery_unverified', 'matching encrypted backup and restore proof are required')
        if operation == 'fence' and evidence.get('all_writers_fenced') is not True:
            fail('migration_writers_active', 'all writer admission must be proven fenced')
        if operation in {'stage', 'verify_stage'} and evidence.get('source_retained') is not True:
            fail('migration_source_not_retained', 'migration must retain original source data')
        if operation == 'activate' and evidence.get('single_owner_admission_closed') is not True:
            fail('migration_authority_unverified', 'cutover must prove one active owner with admission closed')
        if operation == 'rollback' and (evidence.get('new_owner_fenced') is not True
                or evidence.get('old_owner_restored') is not True):
            fail('migration_rollback_unverified', 'rollback ownership is not proven')

    def reconcile(self, plan, request_id, receipt, *, confirm=False):
        if confirm is not True:
            fail('confirmation_required', 'migration reconciliation requires confirmation')
        with self._journal(request_id) as path:
            journal = _read(path, os.geteuid())
            if journal.get('plan_id') != plan.get('plan_id') or not journal.get('pending'):
                fail('migration_reconcile_invalid', 'no matching pending migration operation')
            # Only a trusted installed adapter may resolve an ambiguous effect.
            reconcile = getattr(self.operations, 'reconcile', None)
            if not callable(reconcile):
                fail('migration_adapter_unavailable', 'migration reconciliation adapter is unavailable')
            operation = journal['pending']
            evidence = reconcile(plan, request_id, operation, receipt)
            self._accept(operation, evidence, plan)
            phases = {'fence': 'fenced', 'recovery_proof': 'recovery_verified', 'stage': 'staged',
                      'verify_stage': 'verified', 'activate': 'activated', 'rollback': 'rolled_back'}
            journal['evidence'][operation] = evidence
            journal['phase'], journal['pending'] = phases[operation], None
            _write(path, journal)
            return journal

    def rollback(self, plan, request_id, *, confirm=False):
        if confirm is not True:
            fail('confirmation_required', 'migration rollback requires confirmation')
        with self._journal(request_id) as path:
            journal = _read(path, os.geteuid())
            if journal.get('plan_id') != plan.get('plan_id') or journal.get('pending'):
                fail('migration_rollback_unverified', 'pending or different migration cannot roll back')
            if self.operations.writes_observed(plan) is not False:
                fail('migration_post_write_rollback_refused', 'new or unknown writes require reconciliation and forward/reverse recovery')
            journal['pending'] = 'rollback'
            _write(path, journal)
            evidence = self.operations.rollback(plan, request_id)
            self._accept('rollback', evidence, plan)
            if evidence.get('new_owner_fenced') is not True or evidence.get('old_owner_restored') is not True:
                fail('migration_rollback_unverified', 'rollback ownership is not proven')
            journal['phase'], journal['pending'] = 'rolled_back', None
            journal['evidence']['rollback'] = evidence
            _write(path, journal)
            return journal


class RecoveryProofAdapter:
    """Bridge actual supported RecoveryService verification and an installed drill observer."""
    def __init__(self, recovery_service, drill_observer):
        self.recovery = recovery_service
        self.drill_observer = drill_observer

    def verify(self, plan, set_id):
        backup = self.recovery.verify(set_id)
        drill = self.drill_observer(set_id, plan['inventory']['inventory_digest'])
        if (backup.get('ok') is not True or backup.get('status') != 'verified'
                or not isinstance(drill, dict) or drill.get('ok') is not True
                or drill.get('inventory_digest') != plan['inventory']['inventory_digest']):
            fail('migration_recovery_unverified', 'supported backup and restore drill verification are required')
        return {'ok': True, 'plan_id': plan['plan_id'], 'set_id': set_id,
                'encrypted_verified': True, 'restore_verified': True,
                'inventory_digest': plan['inventory']['inventory_digest']}
