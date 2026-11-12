"""Native delivery admission bindings for exact owner reads, never execution grants."""
from __future__ import annotations

import re
from .models import (encoded, fail, schema, validate_target, identifier,
                     request_identifier)
from sandbox.jobs.models import validate_job_id

MAX_BINDING_BYTES = 4096


def validate_binding(value):
    fields = {'schema_version', 'job_id', 'target_identity', 'ownership_marker',
              'runtime_revision', 'target', 'operation_id', 'request_id'}
    if not isinstance(value, dict) or set(value) != fields or len(encoded(value)) > MAX_BINDING_BYTES:
        fail('binding_mismatch')
    schema(value['schema_version'])
    validate_job_id(value['job_id'])
    for field in ('target_identity', 'ownership_marker', 'runtime_revision'):
        if not isinstance(value[field], str) or re.fullmatch('[0-9a-f]{24}', value[field]) is None:
            fail('binding_mismatch')
    validate_target(value['target'])
    identifier(value['operation_id'])
    if value['request_id'] is not None:
        request_identifier(value['request_id'])
    return value


def current_binding(operation):
    """Called by the producer before reservation, while native admission is live.

    The environment is only a lookup key. The context reader proves the running
    installed child, supervisor, retained submission and current owner identity.
    Ordinary controller deliveries do not acquire an owner binding.
    """
    from sandbox.application.server_cli import server_cli_context_if_selected
    context = server_cli_context_if_selected()
    if context is None:
        return None
    if operation['target']['remote_name'] != context.origin_remote_name:
        fail('binding_mismatch')
    return validate_binding({'schema_version': 1, 'job_id': context.job_id,
        'target_identity': context.owner.target_identity,
        'ownership_marker': context.owner.ownership_marker,
        'runtime_revision': context.owner.runtime_revision,
        'target': operation['target'], 'operation_id': operation['operation_id'],
        'request_id': operation['request_id']})
