"""Instance-owner permanent deny-only guards and closed receipt joins.

Readers never create or migrate storage. Reservation and registry commitment are
ordered stores: a reserved guard without a receipt always refuses replay.
"""
from datetime import datetime, timezone
from contextlib import closing
import json
import re
from pathlib import Path
import sqlite3

from .models import (creation_digest, creation_intent_fields,
                     validate_creation_context, validate_creation_receipt)


class CreationRequestError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _path():
    from sandbox.core._paths import RUNTIME_DIR
    return Path(RUNTIME_DIR) / 'instance-creation' / 'requests.sqlite3'


def _keys(scope, request_key):
    return creation_digest(scope), creation_digest(request_key)


def _read(connection, scope, request_key):
    row = connection.execute('SELECT document FROM guards WHERE scope_digest=? AND request_key_digest=?', _keys(scope, request_key)).fetchone()
    if row is None:
        return None
    value = json.loads(row[0])
    expected_keys = {'schema_version', 'scope_digest', 'request_key_digest', 'operation_id', 'intent_digest', 'reserved_at', 'detail_state'}
    if not isinstance(value, dict) or set(value) != expected_keys or value.get('schema_version') != 1 or len(row[0].encode()) > 1024 or value.get('detail_state') not in {'present', 'expired', 'unknown'}:
        raise CreationRequestError('creation_request_unavailable')
    if (value['scope_digest'], value['request_key_digest']) != _keys(scope, request_key):
        raise CreationRequestError('creation_request_unavailable')
    return value


def reserve_creation_reconcile(context):
    """Allow one covered apply continuation; a used guard never permits replay."""
    context = validate_creation_context(context)
    guard = reserve_creation_request(
        {'phase': 'apply', 'creation_scope': scope_key(context)}, request_key(context),
        context['operation_id'], context['intent_digest'])
    if guard['state'] == 'existing':
        raise CreationRequestError('creation_request_unknown')


def read_creation_request(scope, request_key):
    path = _path()
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            return _read(db, scope, request_key)
    except (sqlite3.Error, ValueError, OSError) as exc:
        raise CreationRequestError('creation_request_unavailable') from exc


def reserve_creation_request(scope, request_key, operation_id, intent_digest):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}', operation_id) or not isinstance(intent_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', intent_digest):
        raise CreationRequestError('creation_context_invalid')
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path), timeout=2)) as db:
            db.execute('PRAGMA journal_mode=DELETE')
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            cap = 8 * 1024 * 1024 // page_size
            if db.execute('PRAGMA page_count').fetchone()[0] > cap:
                raise CreationRequestError('creation_request_capacity')
            db.execute('PRAGMA max_page_count=%d' % cap)
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS guards (scope_digest TEXT NOT NULL, request_key_digest TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(scope_digest,request_key_digest))')
            existing = _read(db, scope, request_key)
            if existing:
                if existing['intent_digest'] != intent_digest or existing['operation_id'] != operation_id:
                    raise CreationRequestError('creation_request_conflict')
                return {'state': 'existing', **existing}
            if db.execute('SELECT COUNT(*) FROM guards').fetchone()[0] >= 4096:
                raise CreationRequestError('creation_request_capacity')
            scope_digest, request_digest = _keys(scope, request_key)
            value = {'schema_version': 1, 'scope_digest': scope_digest, 'request_key_digest': request_digest, 'operation_id': operation_id, 'intent_digest': intent_digest, 'reserved_at': now(), 'detail_state': 'unknown'}
            document = json.dumps(value, sort_keys=True, separators=(',', ':'))
            if len(document.encode()) > 1024:
                raise CreationRequestError('creation_context_invalid')
            db.execute('INSERT INTO guards VALUES (?,?,?)', (scope_digest, request_digest, document))
            db.commit()
            return {'state': 'new', **value}
    except (sqlite3.Error, OSError) as exc:
        raise CreationRequestError('creation_request_capacity' if getattr(exc, 'sqlite_errorcode', None) == getattr(sqlite3, 'SQLITE_FULL', 13) else 'creation_request_unavailable') from exc


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def scope_key(context):
    return {'project_identity': context['project_identity'], 'label': context['label']}


def request_key(context):
    return {'kind': 'request_id' if context['request_id'] is not None else 'operation_id', 'value': context['request_id'] if context['request_id'] is not None else context['operation_id']}


def validate_resolved_context(context, pconf, *, label, create_allowed, config_label=None):
    from sandbox.config.facade import project_identity
    context = validate_creation_context(context)
    fields = creation_intent_fields(pconf, project_identity=project_identity(pconf, label=label)['identity'], target_scope_digest=context['intent_fields']['target_scope_digest'], delivery_intent_digest=context['intent_fields']['delivery_intent_digest'], label=label, create_allowed=create_allowed, config_label=config_label)
    if fields != context['intent_fields']:
        raise CreationRequestError('creation_request_conflict')
    return context


def lookup_creation_receipt(record, context, expected_incarnation=None):
    context = validate_creation_context(context)
    guard = read_creation_request(scope_key(context), request_key(context))
    if guard is None:
        return {'ok': False, 'schema_version': 1, 'error': {'code': 'creation_request_unknown'}}
    if guard['intent_digest'] != context['intent_digest'] or guard['operation_id'] != context['operation_id']:
        raise CreationRequestError('creation_request_conflict')
    receipts = (record or {}).get('creation_receipts', [])
    for raw in receipts:
        receipt = validate_creation_receipt(raw)
        if receipt['operation_id'] != context['operation_id'] or receipt['request_id'] != context['request_id']:
            continue
        if any(receipt[key] != context[key] for key in ('intent_digest', 'project_identity', 'project_root_digest', 'label', 'job_id')):
            raise CreationRequestError('creation_request_conflict')
        incarnation = (record or {}).get('instance_incarnation_id')
        if incarnation != receipt['instance_incarnation_id'] or (expected_incarnation is not None and incarnation != expected_incarnation) or record.get('instance') != receipt['instance_id']:
            raise CreationRequestError('instance_incarnation_changed')
        return {'ok': True, 'schema_version': 1, 'creation_receipt': receipt, 'instance': receipt['instance_id'], 'instance_incarnation_id': incarnation, 'lookup_only': True}
    return {'ok': False, 'schema_version': 1, 'error': {'code': 'creation_request_expired' if guard['detail_state'] == 'expired' else 'creation_request_unknown'}}



def replay_creation_result(record, context, expected_incarnation=None):
    """Report the retained ensure outcome without repeating runtime effects."""
    proof = lookup_creation_receipt(record, context, expected_incarnation)
    if not proof.get('ok'):
        return proof
    receipt = proof['creation_receipt']
    if receipt['completion'] == 'succeeded':
        return proof
    code = ('instance_ensure_failed' if receipt['completion'] == 'failed'
            else 'creation_request_unknown')
    return dict(proof, ok=False, error={'code': code})


def pending_receipts(record, context, instance, incarnation, relation):
    receipts = [validate_creation_receipt(value) for value in (record or {}).get('creation_receipts', [])]
    protected = [value for value in receipts if value['completion'] in {'pending', 'unknown'}]
    terminal = [value for value in receipts if value['completion'] in {'succeeded', 'failed'}]
    if len(protected) >= 8:
        raise CreationRequestError('creation_receipt_capacity')
    receipt = {key: context[key] for key in ('schema_version', 'operation_id', 'request_id', 'job_id', 'intent_digest', 'project_identity', 'project_root_digest', 'label')}
    receipt.update(instance_id=instance, instance_incarnation_id=incarnation, relation=relation, owner_commit_at=now(), completion='pending', completed_at=None, result_code=None)
    return [*terminal[-32:], *protected, validate_creation_receipt(receipt)]


def finish_receipts(record, context, *, succeeded):
    receipts = list((record or {}).get('creation_receipts', []))
    for index, value in enumerate(receipts):
        if value['operation_id'] == context['operation_id'] and value['intent_digest'] == context['intent_digest']:
            receipts[index] = validate_creation_receipt(dict(value, completion='succeeded' if succeeded else 'failed', completed_at=now(), result_code='instance_ready' if succeeded else 'instance_ensure_failed'))
    protected = [value for value in receipts if value['completion'] in {'pending', 'unknown'}]
    terminal = [value for value in receipts if value['completion'] in {'succeeded', 'failed'}]
    # Guards stay permanent. Detail eviction is tracked separately, before drop.
    for value in terminal[:-32]:
        mark_detail(value, 'expired')
    return terminal[-32:] + protected


def mark_detail(context, state):
    path = _path()
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=2)) as db:
            db.execute('BEGIN IMMEDIATE')
            value = _read(db, scope_key(context), request_key(context))
            if value is None:
                raise CreationRequestError('creation_request_unknown')
            value['detail_state'] = state
            db.execute('UPDATE guards SET document=? WHERE scope_digest=? AND request_key_digest=?', (json.dumps(value, separators=(',', ':')), *_keys(scope_key(context), request_key(context))))
            db.commit()
    except sqlite3.Error as exc:
        raise CreationRequestError('creation_request_unavailable') from exc


def finish_creation_fields(record, context, *, succeeded):
    """Evict URL evidence only in the same registry commit as its receipt."""
    receipts = finish_receipts(record, context, succeeded=succeeded)
    retained = {(value['operation_id'], value['intent_digest']) for value in receipts}
    urls = [value for value in (record or {}).get('creation_url_results', [])
            if (value.get('operation_id'), value.get('intent_digest')) in retained]
    return {'creation_receipts': receipts, 'creation_url_results': urls}
