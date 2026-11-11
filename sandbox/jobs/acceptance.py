"""Retained acceptance observations, never an execution or actor grant."""
from __future__ import annotations

import re
import json
from sandbox.services.source_fingerprint import source_has_changes

CONTRACT = 'job.acceptance-receipt.v1'
_SOURCE_KEYS = frozenset({'identity', 'commit', 'dirty_digest', 'dirty'})


def deployed_source(deployment: dict) -> dict:
    """The source fields actually sent to job-start, with absent flags as None."""
    for field in ('commit', 'dirty_digest'):
        value = deployment.get(field)
        if value is not None and (not isinstance(value, str) or len(value.encode()) > 4096
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError('deployed source observation is invalid')
    digest = deployment.get('dirty_digest') or None
    if 'dirty' in deployment and type(deployment['dirty']) is not bool:
        raise ValueError('deployed source dirty observation is invalid')
    if 'dirty' in deployment and deployment['dirty'] != source_has_changes(digest):
        raise ValueError('deployed source dirty observation is inconsistent')
    result = {'identity': deployment['identity'], 'commit': deployment.get('commit') or None,
              'dirty_digest': digest, 'dirty': source_has_changes(digest)}
    if deployment.get('content_digest') is not None:
        result['content_digest'] = _content_digest(deployment['content_digest'])
    return result


def _content_digest(value):
    if not isinstance(value, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', value) is None:
        raise ValueError('retained source content digest is invalid')
    return value


def retained_content_digest(row: dict) -> str | None:
    """Read the optional measurement from the original retained submission."""
    raw = row.get('submission_json')
    if raw is None:
        return None
    try:
        snapshot = json.loads(raw)
        source = snapshot['source']
        if not isinstance(source, dict):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('retained source content snapshot is unavailable') from None
    value = source.get('content_digest')
    return None if value is None else _content_digest(value)


def retained_acceptance_fields(row: dict) -> dict:
    """Copy actual repository identity, including on idempotent replay."""
    request_id = row.get('request_id')
    digest = row.get('request_digest')
    if (request_id is not None and (not isinstance(request_id, str) or not request_id
            or len(request_id.encode()) > 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in request_id))):
        raise ValueError('retained acceptance request identity is invalid')
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        raise ValueError('retained acceptance digest is unavailable')
    return {'acceptance_contract': CONTRACT, 'request_id': request_id, 'request_digest': digest}


def require_retained_acceptance(payload: dict, *, request_id: str | None,
                                source: dict | None = None) -> None:
    """Reconcile ACKs without inventing omitted request/source evidence.

    Synchronized projections have a separate generation binding. Deploy-based
    submissions must also pass their independently known expected source.
    """
    if (payload.get('acceptance_contract') != CONTRACT or 'request_id' not in payload
            or payload['request_id'] != request_id):
        raise ValueError('remote acceptance request receipt is missing or mismatched')
    retained_acceptance_fields(payload)
    observed = payload.get('source')
    if (not isinstance(observed, dict)
            or frozenset(observed) not in (_SOURCE_KEYS, _SOURCE_KEYS | {'content_digest'})
            or type(observed['dirty']) is not bool
            or not isinstance(observed['identity'], str) or not observed['identity']
            or any(value is not None and (not isinstance(value, str) or not value)
                   for value in (observed['commit'], observed['dirty_digest']))
            or (source is not None and observed != source)):
        raise ValueError('remote acceptance source receipt is missing or mismatched')
    if 'content_digest' in observed:
        _content_digest(observed['content_digest'])
