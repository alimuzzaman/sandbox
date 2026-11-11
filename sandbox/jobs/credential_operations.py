"""Retained credential intent and effect checkpoints in the jobs database.

Extension schema 1 is independent of job schema 7; no job table, reader or
retention projection changes. Rows are permanent uniqueness tombstones. This
owner neither authenticates actors nor resolves registered projects nor issues
grants. The service supplies parsed models and canonical registered identity.

Lock order: service target mutation transaction, short ledger transaction,
effect outside the jobs DB transaction, short completion transaction. The
service holds RecoveryRepository.target_mutation_port('shared_credentials')
through claim/invocation/completion and independently validates current actor,
installation, source and target authority. No callbacks run inside this owner.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
import sqlite3
import time
from typing import TYPE_CHECKING, Iterator
from uuid import uuid4

from sandbox.hosting.shared_credentials.models import CredentialIntent, RequestScope

if TYPE_CHECKING:
    from .registry import JobRepository

EXTENSION_VERSION = 1
_VERSION_KEY = 'credential_operations_schema_version'
_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.:@+-]{0,127}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


class LedgerError(RuntimeError):
    """Only fixed codes escape the persistence boundary."""

    def __init__(self, code: str = 'credential_ledger_unavailable'):
        self.code = code
        super().__init__(code)


class OperationState(str, Enum):
    RESERVED = 'reserved'
    PREFLIGHT_REFUSED = 'preflight_refused'
    ADMITTED = 'admitted'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    EFFECTS_UNKNOWN = 'effects_unknown'


class NameStatus(str, Enum):
    PRESENT = 'present'
    MISSING = 'missing'
    UNAVAILABLE = 'unavailable'


class EffectOutcome(str, Enum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class PreflightEvidence:
    """Service-derived nonsecret resolution digest and complete provider names.

    resolution_digest binds the exact resolved installation, policy, source,
    profile, artifact and recipient metadata; it must never hash secret values.
    A record constructed here establishes no provenance or authorization.
    """
    intent_digest: str
    resolution_digest: str
    names: tuple[tuple[str, NameStatus], ...]

    def canonical_json(self) -> str:
        if (not _digest(self.intent_digest) or not _digest(self.resolution_digest)
                or type(self.names) is not tuple or len(self.names) > 128):
            raise LedgerError('credential_preflight_invalid')
        names = []
        for item in self.names:
            if (type(item) is not tuple or len(item) != 2 or not _identifier(item[0])
                    or type(item[1]) is not NameStatus):
                raise LedgerError('credential_preflight_invalid')
            names.append((item[0], item[1].value))
        if len({name for name, _ in names}) != len(names):
            raise LedgerError('credential_preflight_invalid')
        return json.dumps({'intent_digest': self.intent_digest,
                           'resolution_digest': self.resolution_digest,
                           'names': sorted(names)}, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True, slots=True)
class OperationEvidence:
    """Immutable retained evidence, explicitly NOT a committed receipt.

    Internal bindings are available to the future admission owner only. Wire
    responses must use public(), never dataclasses.asdict or __dict__.
    """
    operation_id: str
    request_id: str
    state: OperationState
    row_version: int
    effective_deadline: float
    created_at: float
    updated_at: float
    code: str
    no_effects: bool
    cancel_requested: bool
    names: tuple[tuple[str, str], ...]
    scope: RequestScope = field(repr=False)
    project_identity: str = field(repr=False)
    intent_digest: str = field(repr=False)
    canonical_intent: str = field(repr=False)
    preflight_digest: str | None = field(repr=False)
    preflight_json: str | None = field(repr=False)
    admission_version: int | None = field(repr=False)
    admitted_at: float | None = field(repr=False)

    def public(self) -> dict:
        return {'schema_version': 1, 'operation_id': self.operation_id,
                'request_id': self.request_id, 'status': self.state.value,
                'code': self.code, 'no_effects': self.no_effects,
                'names': [{'name': name, 'status': status} for name, status in self.names],
                'created_at': self.created_at, 'updated_at': self.updated_at,
                'effective_deadline': self.effective_deadline}


@dataclass(frozen=True, slots=True)
class EffectClaim:
    operation_id: str
    effect_step: str
    fencing_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class EffectEvidence:
    """Read-only checkpoint inspection deliberately cannot recover a claim token."""
    effect_step: str
    outcome: str
    claimed_at: float
    completed_at: float | None


def _identifier(value) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _digest(value) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _private_identity(value) -> bool:
    # Authentication's principal is a collision-free canonical JSON tuple, not
    # a wire identifier. Registered canonical project identities are opaque too.
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 4096
            and all(character.isprintable() for character in value))


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Called only by JobRepository migration within its immediate transaction."""
    version = connection.execute('SELECT value FROM schema_meta WHERE key=?', (_VERSION_KEY,)).fetchone()
    if version is not None and version[0] != str(EXTENSION_VERSION):
        raise LedgerError('credential_schema_unsupported')
    # execute, not executescript: preserve the caller's atomic migration.
    connection.execute('''CREATE TABLE IF NOT EXISTS credential_operations (
        operation_id TEXT PRIMARY KEY,
        installation_id TEXT NOT NULL, incarnation TEXT NOT NULL,
        principal_id TEXT NOT NULL, project_identity TEXT NOT NULL,
        project_ref TEXT NOT NULL, request_id TEXT NOT NULL,
        intent_json TEXT NOT NULL, intent_digest TEXT NOT NULL,
        effective_deadline REAL NOT NULL, retry_of TEXT,
        state TEXT NOT NULL CHECK(state IN ('reserved','preflight_refused','admitted',
          'running','succeeded','failed','cancelled','expired','effects_unknown')),
        row_version INTEGER NOT NULL DEFAULT 1,
        preflight_json TEXT, preflight_digest TEXT,
        admission_version INTEGER, admitted_at REAL,
        names_json TEXT NOT NULL DEFAULT '[]', code TEXT NOT NULL DEFAULT 'credential_reserved',
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(installation_id,incarnation,principal_id,project_identity,request_id),
        UNIQUE(installation_id,incarnation,principal_id,project_ref,request_id)
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS credential_effects (
        operation_id TEXT NOT NULL REFERENCES credential_operations(operation_id),
        effect_step TEXT NOT NULL, fencing_token TEXT NOT NULL UNIQUE,
        outcome TEXT NOT NULL CHECK(outcome IN ('in_flight','succeeded','failed','unknown')),
        claimed_at REAL NOT NULL, completed_at REAL,
        PRIMARY KEY(operation_id,effect_step)
    )''')
    connection.execute('INSERT INTO schema_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',
                       (_VERSION_KEY, str(EXTENSION_VERSION)))


class CredentialOperationRepository:
    def __init__(self, jobs: JobRepository):
        self._jobs = jobs

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            with self._jobs.transaction(immediate=write) as connection:
                yield connection
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError):
            # Commit uncertainty is not not_found and never authorizes a new ID.
            raise LedgerError() from None

    @staticmethod
    def _scope(scope: RequestScope) -> tuple:
        if (type(scope) is not RequestScope
                or not _private_identity(scope.principal_id)
                or not all(_identifier(getattr(scope, name)) for name in
                           ('installation_id', 'incarnation', 'project_ref', 'request_id'))):
            raise LedgerError('credential_scope_invalid')
        return (scope.installation_id, scope.incarnation, scope.principal_id,
                scope.project_ref, scope.request_id)

    def _find(self, connection, scope, operation_id=None):
        params = self._scope(scope)
        query = '''SELECT * FROM credential_operations WHERE installation_id=?
            AND incarnation=? AND principal_id=? AND project_ref=? AND request_id=?'''
        if operation_id is not None:
            if not _identifier(operation_id):
                raise LedgerError('credential_operation_invalid')
            query += ' AND operation_id=?'
            params += (operation_id,)
        return connection.execute(query, params).fetchone()

    def _require(self, connection, scope, operation_id):
        row = self._find(connection, scope, operation_id)
        if row is None:
            raise LedgerError('credential_operation_not_found')
        return row

    @staticmethod
    def _evidence(row) -> OperationEvidence:
        names = json.loads(row['names_json'])
        state = OperationState(row['state'])
        safe_codes = {'credential_' + item.value for item in OperationState}
        safe_codes.update({'credential_preflight_ready', 'credential_effect_in_flight'})
        if (row['code'] not in safe_codes or not _identifier(row['request_id'])
                or not _identifier(row['operation_id']) or type(names) is not list or len(names) > 128
                or any(type(item) is not list or len(item) != 2 or not _identifier(item[0])
                       or item[1] not in {'present', 'missing', 'unavailable'} for item in names)
                or any(type(row[key]) not in (int, float) or not math.isfinite(row[key])
                       for key in ('effective_deadline', 'created_at', 'updated_at'))):
            raise LedgerError('credential_ledger_unavailable')
        return OperationEvidence(
            row['operation_id'], row['request_id'], state,
            row['row_version'], row['effective_deadline'], row['created_at'], row['updated_at'],
            row['code'], row['state'] == 'preflight_refused', bool(row['cancel_requested']),
            tuple(tuple(item) for item in names),
            RequestScope(row['installation_id'], row['incarnation'], row['principal_id'],
                         row['project_ref'], row['request_id']), row['project_identity'],
            row['intent_digest'], row['intent_json'], row['preflight_digest'], row['preflight_json'],
            row['admission_version'], row['admitted_at'])

    def lookup(self, scope: RequestScope, *, operation_id: str | None = None) -> OperationEvidence | None:
        """Read-only original scope lookup; None means known absent, error unknown."""
        with self._transaction() as connection:
            row = self._find(connection, scope, operation_id)
            return self._evidence(row) if row is not None else None

    def replay(self, intent: CredentialIntent) -> OperationEvidence | None:
        """Exact retained-intent lookup before any mutable reference resolution."""
        if type(intent) is not CredentialIntent:
            raise LedgerError('credential_intent_invalid')
        with self._transaction() as connection:
            row = self._find(connection, intent.request_scope())
            if row is None:
                return None
            if row['intent_digest'] != intent.intent_digest() or row['intent_json'] != intent.canonical_json():
                raise LedgerError('credential_intent_conflict')
            return self._evidence(row)

    def effects(self, scope: RequestScope, operation_id: str) -> tuple[EffectEvidence, ...]:
        """Inspect retained steps without admitting, claiming or restarting them."""
        with self._transaction() as connection:
            self._require(connection, scope, operation_id)
            rows = connection.execute('''SELECT effect_step,outcome,claimed_at,completed_at
                FROM credential_effects WHERE operation_id=? ORDER BY claimed_at,effect_step''',
                (operation_id,)).fetchall()
            return tuple(EffectEvidence(*row) for row in rows)

    def reserve(self, intent: CredentialIntent, *, project_identity: str,
                effective_deadline: float) -> OperationEvidence:
        """Atomically retain exact intent once; a replay never extends deadline.

        The service must lookup first, then resolve new registered references.
        project_identity comes from that resolver, never unchecked wire input.
        retry_of names an original request_id within this same canonical scope.
        """
        if type(intent) is not CredentialIntent or not _private_identity(project_identity):
            raise LedgerError('credential_intent_invalid')
        scope, canonical, digest = intent.request_scope(), intent.canonical_json(), intent.intent_digest()
        with self._transaction(write=True) as connection:
            row = self._find(connection, scope)
            if row is None:
                row = connection.execute('''SELECT * FROM credential_operations WHERE
                    installation_id=? AND incarnation=? AND principal_id=?
                    AND project_identity=? AND request_id=?''',
                    (scope.installation_id, scope.incarnation, scope.principal_id,
                     project_identity, scope.request_id)).fetchone()
            if row is not None:
                if (row['intent_digest'] != digest or row['intent_json'] != canonical
                        or row['project_identity'] != project_identity):
                    raise LedgerError('credential_intent_conflict')
                return self._evidence(row)
            now = time.time()
            if (type(effective_deadline) not in (int, float) or not math.isfinite(effective_deadline)
                    or not now < effective_deadline <= intent.requested_deadline):
                raise LedgerError('credential_deadline_invalid')
            if intent.retry_of is not None:
                previous = connection.execute('''SELECT * FROM credential_operations WHERE
                    installation_id=? AND incarnation=? AND principal_id=? AND project_identity=?
                    AND request_id=? AND state='preflight_refused' ''',
                    (scope.installation_id, scope.incarnation, scope.principal_id,
                     project_identity, intent.retry_of)).fetchone()
                if previous is None or connection.execute(
                        'SELECT 1 FROM credential_effects WHERE operation_id=? LIMIT 1',
                        (previous['operation_id'],)).fetchone() is not None:
                    raise LedgerError('credential_retry_invalid')
            operation_id = uuid4().hex
            connection.execute('''INSERT INTO credential_operations
                (operation_id,installation_id,incarnation,principal_id,project_identity,project_ref,
                 request_id,intent_json,intent_digest,effective_deadline,retry_of,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'reserved',?,?)''',
                (operation_id, scope.installation_id, scope.incarnation, scope.principal_id,
                 project_identity, scope.project_ref, scope.request_id, canonical, digest,
                 effective_deadline, intent.retry_of, now, now))
            return self._evidence(self._require(connection, scope, operation_id))

    @staticmethod
    def _cas(row, version, states):
        if type(version) is not int or row['row_version'] != version or row['state'] not in states:
            raise LedgerError('credential_transition_conflict')

    @staticmethod
    def _update(connection, row, state, code, **fields):
        fields.update(state=state, code=code, row_version=row['row_version'] + 1, updated_at=time.time())
        connection.execute('UPDATE credential_operations SET ' +
                           ','.join(key + '=?' for key in fields) + ' WHERE operation_id=? AND row_version=?',
                           (*fields.values(), row['operation_id'], row['row_version']))

    @staticmethod
    def _expire(connection, row) -> bool:
        if row['effective_deadline'] > time.time():
            return False
        active = connection.execute('SELECT 1 FROM credential_effects WHERE operation_id=? AND outcome=?',
                                    (row['operation_id'], 'in_flight')).fetchone()
        state = 'effects_unknown' if active else 'expired'
        CredentialOperationRepository._update(connection, row, state, 'credential_' + state)
        return True

    def record_preflight(self, scope: RequestScope, operation_id: str, *, expected_version: int,
                         evidence: PreflightEvidence) -> OperationEvidence:
        """Freeze refusal, or bind successful preflight for a later admission CAS."""
        if type(evidence) is not PreflightEvidence:
            raise LedgerError('credential_preflight_invalid')
        payload = evidence.canonical_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'reserved'})
            requested = json.loads(row['intent_json'])
            names = {item['source_key'] for item in requested['mappings'] if item['kind'] == 'provider'}
            if evidence.intent_digest != row['intent_digest'] or names != {name for name, _ in evidence.names}:
                raise LedgerError('credential_preflight_mismatch')
            if row['preflight_digest'] is not None:
                raise LedgerError('credential_transition_conflict')
            if not self._expire(connection, row):
                refused = any(status is not NameStatus.PRESENT for _, status in evidence.names)
                public_names = sorted((name, status.value) for name, status in evidence.names
                                      if not refused or status is not NameStatus.PRESENT)
                self._update(connection, row, 'preflight_refused' if refused else 'reserved',
                             'credential_preflight_refused' if refused else 'credential_preflight_ready',
                             preflight_json=payload, preflight_digest=digest,
                             names_json=json.dumps(public_names, separators=(',', ':')))
            return self._evidence(self._require(connection, scope, operation_id))

    def admit(self, scope: RequestScope, operation_id: str, *, expected_version: int,
              intent_digest: str, preflight_digest: str) -> OperationEvidence:
        """Durable CAS evidence only. Admission service alone may issue a receipt.

        The admission owner revalidates current resolved metadata against the
        retained successful preflight before calling this method.
        """
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'reserved'})
            if (not _digest(preflight_digest) or row['intent_digest'] != intent_digest
                    or row['preflight_digest'] != preflight_digest
                    or row['preflight_json'] is None
                    or any(status != 'present' for _, status in json.loads(row['names_json']))):
                raise LedgerError('credential_preflight_mismatch')
            if not self._expire(connection, row):
                self._update(connection, row, 'admitted', 'credential_admitted',
                             admission_version=row['row_version'] + 1, admitted_at=time.time())
            return self._evidence(self._require(connection, scope, operation_id))

    def start(self, scope: RequestScope, operation_id: str, *, expected_version: int) -> OperationEvidence:
        """Existing job/lifecycle service calls once; no scheduler or redispatch."""
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'admitted'})
            if not self._expire(connection, row):
                self._update(connection, row, 'running', 'credential_running')
            return self._evidence(self._require(connection, scope, operation_id))

    def claim_effect(self, scope: RequestScope, operation_id: str, *,
                     expected_version: int, effect_step: str) -> EffectClaim | None:
        """Unique irreversible claim. None means expired; inspect original row.

        Service holds target-wide mutation authority before this call and through
        invocation/completion. A duplicate claim NEVER returns a reusable token.
        """
        if not _identifier(effect_step):
            raise LedgerError('credential_effect_invalid')
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'running'})
            if row['cancel_requested']:
                raise LedgerError('credential_transition_conflict')
            if self._expire(connection, row):
                return None
            blocked = connection.execute('''SELECT 1 FROM credential_effects WHERE operation_id=?
                AND (effect_step=? OR outcome IN ('in_flight','unknown','failed')) LIMIT 1''',
                (operation_id, effect_step)).fetchone()
            if blocked:
                raise LedgerError('credential_effect_conflict')
            count = connection.execute('SELECT count(*) FROM credential_effects WHERE operation_id=?',
                                       (operation_id,)).fetchone()[0]
            if count >= 256:
                raise LedgerError('credential_effect_limit')
            token = uuid4().hex
            connection.execute('''INSERT INTO credential_effects
                (operation_id,effect_step,fencing_token,outcome,claimed_at) VALUES(?,?,?,'in_flight',?)''',
                (operation_id, effect_step, token, time.time()))
            self._update(connection, row, 'running', 'credential_effect_in_flight')
            return EffectClaim(operation_id, effect_step, token)

    def complete_effect(self, scope: RequestScope, claim: EffectClaim, *,
                        outcome: EffectOutcome) -> OperationEvidence:
        """Record completion before another step. Unknown freezes permanently."""
        if type(claim) is not EffectClaim or type(outcome) is not EffectOutcome:
            raise LedgerError('credential_effect_invalid')
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, claim.operation_id)
            retained = connection.execute('''SELECT outcome FROM credential_effects
                WHERE operation_id=? AND effect_step=? AND fencing_token=?''',
                (claim.operation_id, claim.effect_step, claim.fencing_token)).fetchone()
            if retained is not None and retained[0] == outcome.value:
                return self._evidence(row)
            changed = connection.execute('''UPDATE credential_effects SET outcome=?,completed_at=?
                WHERE operation_id=? AND effect_step=? AND fencing_token=? AND outcome='in_flight' ''',
                (outcome.value, time.time(), claim.operation_id, claim.effect_step, claim.fencing_token)).rowcount
            if changed != 1:
                raise LedgerError('credential_effect_conflict')
            state = row['state']
            if state == 'running':
                state = {'succeeded': 'running', 'failed': 'failed', 'unknown': 'effects_unknown'}[outcome.value]
                if state == 'running' and row['effective_deadline'] <= time.time():
                    state = 'expired'
            self._update(connection, row, state, 'credential_' + state)
            return self._evidence(self._require(connection, scope, claim.operation_id))

    def cancel(self, scope: RequestScope, operation_id: str) -> OperationEvidence:
        """Serialize cancellation against effect claims, retaining in-flight doubt."""
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            if row['state'] in {'reserved', 'admitted', 'running'}:
                active = connection.execute('''SELECT 1 FROM credential_effects
                    WHERE operation_id=? AND outcome='in_flight' ''', (operation_id,)).fetchone()
                state = 'effects_unknown' if active else 'cancelled'
                self._update(connection, row, state, 'credential_' + state, cancel_requested=1)
            return self._evidence(self._require(connection, scope, operation_id))

    def finish(self, scope: RequestScope, operation_id: str, *, expected_version: int,
               succeeded: bool) -> OperationEvidence:
        if type(succeeded) is not bool:
            raise LedgerError('credential_transition_invalid')
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'running'})
            if connection.execute('''SELECT 1 FROM credential_effects WHERE operation_id=?
                    AND outcome!='succeeded' LIMIT 1''', (operation_id,)).fetchone():
                raise LedgerError('credential_effect_conflict')
            if not self._expire(connection, row):
                state = 'succeeded' if succeeded else 'failed'
                self._update(connection, row, state, 'credential_' + state)
            return self._evidence(self._require(connection, scope, operation_id))

    def freeze_unknown(self, scope: RequestScope, operation_id: str, *,
                       expected_version: int) -> OperationEvidence:
        """Explicit crash/recovery observation; lookup never resumes an effect."""
        with self._transaction(write=True) as connection:
            row = self._require(connection, scope, operation_id)
            self._cas(row, expected_version, {'admitted', 'running'})
            self._update(connection, row, 'effects_unknown', 'credential_effects_unknown')
            return self._evidence(self._require(connection, scope, operation_id))
