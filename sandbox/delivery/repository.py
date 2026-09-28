"""SQLite owner for diagnostic history and permanent deny-only request guards.

Construction and all query methods are strictly read-only. Only explicit writer
methods initialize or prune. No record here grants execution/recovery authority.
"""
from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
from pathlib import Path
import sqlite3
import uuid

from .models import (DeliveryError, MAX_OPERATION_BYTES, TERMINAL, canonical_digest,
                     digest, encoded, fail, identifier, integer, now,
                     operation_summary, request_key_digest, scope_digest,
                     target_digest, terminal_digest, validate_operation, timestamp, schema)

DB_BYTES = 96 * 1024 * 1024
GUARD_LIMIT = 4096
PROTECTED_LIMIT = 128
RETENTION = {'terminal_days': 30, 'terminal_per_target': 64, 'terminal_global': 512,
             'protected': 128, 'request_guards': 4096, 'target_metadata': 1024}

class DeliveryRepository:
    def __init__(self, home):
        self.path = Path(home) / 'runtime' / 'delivery' / 'outcomes.sqlite3'

    @contextlib.contextmanager
    def _read(self):
        if not self.path.exists():
            yield None
            return
        conn = None
        try:
            conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            if conn.execute('PRAGMA user_version').fetchone()[0] != 1:
                fail('unsupported_capability')
            conn.execute('BEGIN')
            yield conn
        except sqlite3.Error:
            fail('delivery_store_unavailable')
        finally:
            if conn is not None:
                conn.close()

    @contextlib.contextmanager
    def _write(self):
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > DB_BYTES:
                fail('delivery_capacity')
            journal = Path(str(self.path) + '-journal')
            if journal.exists() and journal.stat().st_size > DB_BYTES:
                fail('delivery_capacity')
            conn = sqlite3.connect(str(self.path), timeout=2, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.execute('PRAGMA synchronous=FULL')
            page = conn.execute('PRAGMA page_size').fetchone()[0]
            # Reserve rollback-header overhead inside the 96 MiB rollback cap.
            maximum = (DB_BYTES - 1024 * 1024) // page
            if conn.execute('PRAGMA max_page_count=%d' % maximum).fetchone()[0] > maximum:
                fail('delivery_capacity')
            conn.execute('PRAGMA journal_size_limit=%d' % DB_BYTES)
            conn.execute('BEGIN IMMEDIATE')
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                fail('unsupported_capability')
            if version == 0:
                if conn.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                    fail('delivery_store_unavailable')
                for statement in (
                    'CREATE TABLE operations (seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT UNIQUE NOT NULL, target_digest TEXT NOT NULL, document BLOB NOT NULL, terminal INTEGER NOT NULL, protected INTEGER NOT NULL, success INTEGER NOT NULL, finished_at TEXT)',
                    'CREATE INDEX target_operations ON operations(target_digest,seq DESC)',
                    'CREATE TABLE request_bindings (scope_digest TEXT NOT NULL, request_key_digest TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, intent_digest TEXT NOT NULL, document BLOB NOT NULL, PRIMARY KEY(scope_digest,request_key_digest))',
                    'CREATE TABLE target_history (target_digest TEXT PRIMARY KEY, expired_through INTEGER NOT NULL DEFAULT 0, touched_at TEXT NOT NULL, epoch TEXT NOT NULL)',
                ):
                    conn.execute(statement)
                conn.execute('PRAGMA user_version=1')
            yield conn
            conn.execute('COMMIT')
        except (sqlite3.Error, OSError):
            fail('delivery_store_unavailable')
        finally:
            if conn is not None:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
                conn.close()

    def _document(self, row):
        if row is None:
            return None
        raw = row['document']
        if len(raw) > MAX_OPERATION_BYTES:
            fail('delivery_store_unavailable')
        try:
            value = validate_operation(json.loads(raw))
            if value['execution_state'] in TERMINAL and value['terminal_snapshot_digest'] != terminal_digest(value):
                fail('delivery_store_unavailable')
            return value
        except (ValueError, TypeError, UnicodeError):
            fail('delivery_store_unavailable')

    def _guard(self, conn, scope, key):
        row = conn.execute('SELECT document FROM request_bindings WHERE scope_digest=? AND request_key_digest=?', (scope, key)).fetchone()
        if row is None:
            return None
        try:
            if len(row['document']) > 1024:
                fail('delivery_store_unavailable')
            value = json.loads(row['document'])
            if set(value) != {'schema_version', 'scope_digest', 'request_key_digest', 'operation_id', 'intent_digest', 'reserved_at', 'detail_state'}:
                fail('delivery_store_unavailable')
            schema(value['schema_version']); digest(value['scope_digest']); digest(value['request_key_digest'])
            identifier(value['operation_id']); digest(value['intent_digest']); timestamp(value['reserved_at'])
            if value['detail_state'] not in ('present', 'expired', 'unknown') or value['scope_digest'] != scope or value['request_key_digest'] != key:
                fail('delivery_store_unavailable')
            return value
        except (ValueError, TypeError, UnicodeError, KeyError):
            fail('delivery_store_unavailable')

    def _lookup(self, conn, scope, key, expected_intent=None):
        guard = self._guard(conn, scope, key)
        if guard is None:
            return {'status': 'missing', 'operation': None, 'operation_id': None, 'guard': None}
        row = conn.execute('SELECT document FROM operations WHERE operation_id=?', (guard['operation_id'],)).fetchone()
        operation = self._document(row)
        if operation is not None and (operation['operation_id'] != guard['operation_id'] or operation['intent_digest'] != guard['intent_digest']):
            fail('delivery_store_unavailable')
        status = 'existing' if row else 'delivery_request_expired'
        if expected_intent is not None and guard['intent_digest'] != expected_intent:
            status = 'request_conflict'
        return {'status': status, 'operation_id': guard['operation_id'], 'operation': operation, 'guard': guard}

    def lookup_request(self, scope, request_key, *, operation_id=None):
        scope = scope_digest(scope); key = request_key_digest(request_key, operation_id)
        with self._read() as conn:
            if conn is None:
                return {'status': 'missing', 'operation': None, 'operation_id': None, 'guard': None}
            return self._lookup(conn, scope, key)

    def lookup_operation(self, scope, operation_id):
        """Expose an expired operation's guard only within its stable scope."""
        scope = scope_digest(scope); identifier(operation_id)
        with self._read() as conn:
            if conn is None:
                return {'status': 'missing', 'operation': None, 'operation_id': None, 'guard': None}
            row = conn.execute('SELECT request_key_digest FROM request_bindings WHERE scope_digest=? AND operation_id=?', (scope, operation_id)).fetchone()
            if row is None:
                return {'status': 'missing', 'operation': None, 'operation_id': None, 'guard': None}
            return self._lookup(conn, scope, row['request_key_digest'])

    def has_open_operation(self, scope):
        """Read all bounded protected rows in this logical scope, without pruning."""
        scope = scope_digest(scope)
        with self._read() as conn:
            if conn is None:
                return False
            return conn.execute(
                'SELECT 1 FROM operations JOIN request_bindings USING(operation_id) '
                'WHERE request_bindings.scope_digest=? AND operations.terminal=0 LIMIT 1',
                (scope,)).fetchone() is not None

    def preflight_request(self, scope, request_key, operation_id, intent_digest):
        """Read-only advisory capacity check; atomic reservation must recheck."""
        scope = scope_digest(scope); key = request_key_digest(request_key, operation_id)
        identifier(operation_id); digest(intent_digest)
        with self._read() as conn:
            if conn is not None:
                existing = self._lookup(conn, scope, key, intent_digest)
                if existing['status'] != 'missing':
                    return existing
                if conn.execute('SELECT COUNT(*) FROM request_bindings').fetchone()[0] >= GUARD_LIMIT:
                    fail('delivery_request_capacity')
                if conn.execute('SELECT COUNT(*) FROM operations WHERE protected=1').fetchone()[0] >= PROTECTED_LIMIT:
                    fail('delivery_capacity')
                page_size = conn.execute('PRAGMA page_size').fetchone()[0]
                pages = conn.execute('PRAGMA page_count').fetchone()[0]
                free_pages = conn.execute('PRAGMA freelist_count').fetchone()[0]
                if (pages - free_pages) * page_size >= DB_BYTES - 1024 * 1024:
                    fail('delivery_capacity')
            return {'status': 'available', 'operation_id': operation_id, 'operation': None, 'guard': None}

    def reserve_request(self, scope, request_key, operation_id, intent_digest, *, operation):
        scope = scope_digest(scope); key = request_key_digest(request_key, operation_id)
        identifier(operation_id); digest(intent_digest)
        checked = validate_operation(operation)
        if checked['operation_id'] != operation_id or checked['intent_digest'] != intent_digest or checked['request_id'] != request_key:
            fail('request_conflict')
        from .models import request_scope
        if request_scope(checked['target']) != scope:
            fail('request_conflict')
        # Existing-key diagnosis does not require writer access or free capacity.
        with self._read() as conn:
            if conn is not None:
                previous = self._lookup(conn, scope, key, intent_digest)
                if previous['status'] != 'missing':
                    return previous
        with self._write() as conn:
            previous = self._lookup(conn, scope, key, intent_digest)
            if previous['status'] != 'missing':
                return previous
            if conn.execute('SELECT COUNT(*) FROM request_bindings').fetchone()[0] >= GUARD_LIMIT:
                fail('delivery_request_capacity')
            self._prune(conn)
            guard = {'schema_version': 1, 'scope_digest': scope, 'request_key_digest': key, 'operation_id': operation_id, 'intent_digest': intent_digest, 'reserved_at': now(), 'detail_state': 'present'}
            if len(encoded(guard)) > 1024:
                fail('delivery_capacity')
            conn.execute('INSERT INTO request_bindings VALUES (?,?,?,?,?)', (scope, key, operation_id, intent_digest, encoded(guard)))
            stored = self._store(conn, checked, new=True)
            return {'status': 'reserved', 'operation_id': operation_id, 'operation': stored, 'guard': guard}

    def _store(self, conn, operation, *, new=False):
        operation = dict(operation)
        terminal = operation['execution_state'] in TERMINAL
        protected = not terminal or operation['pinned_reason'] is not None
        existing = conn.execute('SELECT * FROM operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()
        if terminal:
            computed = terminal_digest(operation)
            if operation['terminal_snapshot_digest'] not in (None, computed):
                fail('delivery_terminal_conflict')
            operation['terminal_snapshot_digest'] = computed
        elif operation['terminal_snapshot_digest'] is not None:
            fail('delivery_terminal_conflict')
        if existing:
            previous = self._document(existing)
            if existing['terminal']:
                if previous != operation:
                    fail('delivery_terminal_conflict')
                return previous
            for key in ('operation_id', 'request_id', 'job_id', 'kind', 'intent_digest', 'target', 'requested_outcome', 'accepted_at'):
                if previous[key] != operation[key]:
                    fail('request_conflict')
        elif not new:
            fail('delivery_request_expired')
        count = conn.execute('SELECT COUNT(*) FROM operations WHERE protected=1').fetchone()[0]
        if protected and not (existing and existing['protected']) and count >= PROTECTED_LIMIT:
            fail('delivery_capacity')
        target = target_digest(operation['target'])
        if conn.execute('SELECT 1 FROM target_history WHERE target_digest=?', (target,)).fetchone() is None:
            if conn.execute('SELECT COUNT(*) FROM target_history').fetchone()[0] >= 1024:
                victim = conn.execute('SELECT target_digest FROM target_history WHERE NOT EXISTS (SELECT 1 FROM operations WHERE operations.target_digest=target_history.target_digest) ORDER BY touched_at,target_digest LIMIT 1').fetchone()
                if victim is None:
                    fail('delivery_capacity')
                conn.execute('DELETE FROM target_history WHERE target_digest=?', (victim[0],))
            conn.execute('INSERT INTO target_history VALUES (?,0,?,?)', (target, now(), str(uuid.uuid4())))
        payload = encoded(validate_operation(operation))
        values = (target, payload, int(terminal), int(protected), int(operation['delivery_succeeded'] and operation['evidence_completeness'] == 'complete'), operation['finished_at'], operation['operation_id'])
        if existing:
            conn.execute('UPDATE operations SET target_digest=?,document=?,terminal=?,protected=?,success=?,finished_at=? WHERE operation_id=?', values)
        else:
            conn.execute('INSERT INTO operations(target_digest,document,terminal,protected,success,finished_at,operation_id) VALUES (?,?,?,?,?,?,?)', values)
        conn.execute('UPDATE target_history SET touched_at=? WHERE target_digest=?', (now(), target))
        self._prune(conn)
        return operation

    def write_operation(self, operation):
        checked = validate_operation(operation)
        with self._write() as conn:
            return self._store(conn, checked)

    def _prune(self, conn):
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        rows = conn.execute('SELECT operations.seq,operations.operation_id,operations.target_digest,operations.finished_at,request_bindings.scope_digest FROM operations JOIN request_bindings USING(operation_id) WHERE terminal=1 AND protected=0 ORDER BY seq DESC LIMIT 641').fetchall()
        per_target = {}; retained = 0
        for row in rows:
            target = row['target_digest']
            retention_scope = row['scope_digest']
            count = per_target.get(retention_scope, 0)
            if dt.datetime.fromisoformat(timestamp(row['finished_at'])[:-1] + '+00:00') < cutoff or count >= 64 or retained >= 512:
                guard_row = conn.execute('SELECT scope_digest,request_key_digest,document FROM request_bindings WHERE operation_id=?', (row['operation_id'],)).fetchone()
                if guard_row is None:
                    fail('delivery_store_unavailable')
                guard = json.loads(guard_row['document']); guard['detail_state'] = 'expired'
                conn.execute('UPDATE request_bindings SET document=? WHERE operation_id=?', (encoded(guard), row['operation_id']))
                conn.execute('UPDATE target_history SET expired_through=MAX(expired_through,?) WHERE target_digest=?', (row['seq'], target))
                conn.execute('DELETE FROM operations WHERE seq=?', (row['seq'],))
            else:
                per_target[retention_scope] = count + 1; retained += 1

    def get(self, operation_id, *, target_digest=None):
        identifier(operation_id)
        if target_digest is not None:
            digest(target_digest)
        with self._read() as conn:
            if conn is None:
                return None
            row = conn.execute('SELECT document,target_digest FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row is None or (target_digest is not None and row['target_digest'] != target_digest):
                return None
            return self._document(row)

    def read_trace_operation(self, candidate, scope, budget):
        """Read one original operation through its permanent guard and exact root."""
        root = scope.get('application_root_digest')
        requested = scope.get('requested_target') or {}
        if root is None or requested.get('remote_name') is None:
            return None
        digest(root)
        request = candidate['request_id']
        key = request_key_digest(request)
        operation_id = candidate.get('operation_id')
        if operation_id is not None:
            identifier(operation_id)
        budget.check()
        if not self.path.exists():
            return None
        conn = None
        try:
            conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True,
                                   timeout=min(2, budget.remaining_seconds()))
            conn.row_factory = sqlite3.Row
            conn.set_progress_handler(lambda: int(budget.expired), 1000)
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            if conn.execute('PRAGMA user_version').fetchone()[0] != 1:
                fail('unsupported_capability')
            rows = conn.execute(
                'SELECT b.scope_digest,b.operation_id,CASE WHEN length(o.document)<=? '
                'THEN o.document ELSE NULL END AS document FROM request_bindings b '
                'LEFT JOIN operations o USING(operation_id) WHERE b.request_key_digest=? LIMIT 4097',
                (MAX_OPERATION_BYTES, key))
            found = None
            for index, row in enumerate(rows):
                budget.check()
                if index >= GUARD_LIMIT:
                    fail('bounds')
                if operation_id is not None and row['operation_id'] != operation_id:
                    continue
                if row['document'] is None:
                    continue
                operation = self._document(row)
                budget.check()
                target = operation['target']
                if (target['project_root_digest'] != root or target['target_kind'] != 'hosted'
                        or target['remote_name'] != requested['remote_name']
                        or target['environment'] != requested['environment']):
                    continue
                from .models import request_scope
                if (operation['request_id'] != request or request_scope(target) != row['scope_digest']
                        or operation['operation_id'] != row['operation_id']):
                    fail('binding_mismatch')
                guard = self._guard(conn, row['scope_digest'], key)
                if guard is None or guard['intent_digest'] != operation['intent_digest']:
                    fail('binding_mismatch')
                # Candidate target_digest belongs to the native activation
                # target codec, not the broader delivery target envelope.
                # The service joins that digest against native owner proof.
                if found is not None:
                    fail('binding_mismatch')
                found = operation
            return found
        except sqlite3.Error:
            budget.check()
            fail('delivery_store_unavailable')
        finally:
            if conn is not None:
                conn.close()

    def read_related_recoveries(self, original, scope, deadline=None, *, budget=None, limit=10):
        """Reverse lookup within the original scope, bounded to 640 retained rows."""
        from .trace_models import TraceQueryBudget
        import time
        budget = budget or deadline or TraceQueryBudget(time.monotonic() + 10)
        if isinstance(budget, (int, float)):
            budget = TraceQueryBudget(budget)
        identifier(original)
        scope_key = scope_digest(scope)
        if type(limit) is not int or not 1 <= limit <= 10:
            fail()
        result = {'schema_version': 1, 'state': 'known', 'reason': None,
                  'recoveries': [], 'scanned': 0, 'omitted': 0}
        budget.check()
        if not self.path.exists():
            return result
        conn = None
        try:
            conn = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True,
                                   timeout=min(2, budget.remaining_seconds()))
            conn.row_factory = sqlite3.Row
            conn.set_progress_handler(lambda: int(budget.expired), 1000)
            conn.execute('PRAGMA query_only=ON')
            if conn.execute('PRAGMA user_version').fetchone()[0] != 1:
                fail('unsupported_capability')
            cursor = conn.execute(
                'SELECT CASE WHEN length(o.document)<=? THEN o.document ELSE NULL END AS document '
                'FROM operations o JOIN request_bindings b USING(operation_id) '
                'WHERE b.scope_digest=? ORDER BY o.seq DESC LIMIT 640',
                (MAX_OPERATION_BYTES, scope_key))
            for row in cursor:
                budget.check()
                result['scanned'] += 1
                if row['document'] is None:
                    result.update(state='partial', reason='bounds')
                    continue
                operation = self._document(row)
                budget.check()
                if any(ref.get('kind') == 'original_delivery' and ref.get('identifier') == original
                       for ref in operation['recovery_relations']):
                    if len(result['recoveries']) < limit:
                        result['recoveries'].append(operation)
                    else:
                        result['omitted'] += 1
            if result['scanned'] == 640 or result['omitted']:
                result.update(state='partial', reason='bounds')
            return result
        except sqlite3.Error:
            budget.check()
            fail('delivery_store_unavailable')
        finally:
            if conn is not None:
                conn.close()

    def history(self, target_digest, *, limit=10, cursor=None):
        return self._history(target_digest, limit=limit, cursor=cursor)

    def history_scope(self, scope, *, limit=10, cursor=None):
        return self._history(scope_digest(scope), limit=limit, cursor=cursor, by_scope=True)

    def _history(self, target_digest, *, limit=10, cursor=None, by_scope=False):
        digest(target_digest)
        cursor_scope = canonical_digest({'domain': 'delivery.history_scope', 'scope': target_digest}) if by_scope else target_digest
        predicate = 'operation_id IN (SELECT operation_id FROM request_bindings WHERE scope_digest=?)' if by_scope else 'target_digest=?'
        if type(limit) is not int or not 1 <= limit <= 50:
            fail()
        token = None
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor.encode()) > 512:
                    fail('delivery_cursor_invalid')
                token = json.loads(base64.b64decode(cursor.encode(), altchars=b'-_', validate=True))
                if set(token) != {'v', 'scope', 'high', 'last', 'expired', 'epoch'} or type(token['v']) is not int or token['v'] != 1 or token['scope'] != cursor_scope:
                    fail('delivery_cursor_invalid')
                identifier(token['epoch'])
                for key in ('high', 'last', 'expired'):
                    integer(token[key])
                if token['last'] > token['high']:
                    fail('delivery_cursor_invalid')
            except (ValueError, TypeError, UnicodeError):
                fail('delivery_cursor_invalid')
        with self._read() as conn:
            high = token['high'] if token else (0 if conn is None else conn.execute('SELECT COALESCE(MAX(seq),0) FROM operations').fetchone()[0])
            metadata = None
            if conn is not None:
                if by_scope:
                    metadata_rows = conn.execute('SELECT target_digest,expired_through,epoch FROM target_history WHERE target_digest IN (SELECT target_digest FROM operations WHERE ' + predicate + ' AND seq<=?) ORDER BY target_digest LIMIT 1024', (target_digest, high)).fetchall()
                    if metadata_rows:
                        metadata = (max(row['expired_through'] for row in metadata_rows), canonical_digest([dict(row) for row in metadata_rows]))
                else:
                    metadata = conn.execute('SELECT expired_through,epoch FROM target_history WHERE target_digest=?', (target_digest,)).fetchone()
            if token and (metadata is None or metadata[0] != token['expired'] or metadata[1] != token['epoch']):
                fail('delivery_cursor_expired')
            last = token['last'] if token else high + 1
            rows = [] if conn is None else conn.execute('SELECT seq,document FROM operations WHERE ' + predicate + ' AND seq<=? AND seq<? ORDER BY seq DESC LIMIT ?', (target_digest, high, last, limit + 1)).fetchall()
            more = len(rows) > limit; rows = rows[:limit]
            next_cursor = None
            if more:
                next_cursor = base64.urlsafe_b64encode(encoded({'v': 1, 'scope': cursor_scope, 'high': high, 'last': rows[-1]['seq'], 'expired': metadata[0], 'epoch': metadata[1]})).decode()
            latest = None if conn is None else conn.execute('SELECT document FROM operations WHERE ' + predicate + ' AND seq<=? ORDER BY seq DESC LIMIT 1', (target_digest, high)).fetchone()
            success = None if conn is None else conn.execute('SELECT document FROM operations WHERE ' + predicate + ' AND seq<=? AND terminal=1 AND success=1 ORDER BY seq DESC LIMIT 1', (target_digest, high)).fetchone()
            oldest = None if conn is None else conn.execute('SELECT document FROM operations WHERE ' + predicate + ' AND seq<=? ORDER BY seq LIMIT 1', (target_digest, high)).fetchone()
            return {'completeness': 'bounded' if metadata else 'missing', 'retention_policy': dict(RETENTION), 'oldest_retained_at': None if oldest is None else self._document(oldest)['accepted_at'], 'omitted': bool(more), 'expired': bool(metadata and metadata[0]), 'returned_count': len(rows), 'next_cursor': next_cursor, 'limits': {'maximum_limit': 50, 'operation_bytes': MAX_OPERATION_BYTES, 'response_bytes': 256 * 1024}, 'operations': [operation_summary(self._document(row)) for row in rows], 'latest_attempt': None if latest is None else operation_summary(self._document(latest)), 'latest_retained_complete_success': None if success is None else operation_summary(self._document(success))}
