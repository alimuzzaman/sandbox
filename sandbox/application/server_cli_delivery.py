"""Bounded exact delivery observation through the authenticated installed owner."""
from __future__ import annotations

import json
import re
import shlex
import time
import uuid
from pathlib import Path

from sandbox.application.server_cli import ServerCLIUnavailable, read_current_server_owner
from sandbox.delivery.models import (DeliveryError, encoded, request_identifier,
    request_scope, validate_target, TERMINAL)
from sandbox.jobs.models import validate_job_id

CONTRACT = 'delivery.inspect-exact.v1'
REQUEST_BYTES = 2048
RESPONSE_BYTES = 4096
SECONDS = 15
STATUSES = {'found', 'missing', 'expired', 'binding_unavailable', 'binding_mismatch',
            'unsupported', 'bounds', 'unavailable'}
SERVICED = {'found', 'missing', 'expired'}
KEYS = {'schema_version', 'contract', 'origin_remote_name', 'name', 'target',
        'original_job_id', 'operation_id', 'request_id'}


class Budget:
    def __init__(self, seconds=SECONDS):
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise ServerCLIUnavailable('exact delivery observation deadline expired')
        return value

    def check(self):
        self.remaining()


def _unavailable():
    raise ServerCLIUnavailable('exact delivery observation is unavailable')


def validate_request(value):
    from sandbox.hosting.target_resolution import parse_target
    if (type(value) is not dict or set(value) != KEYS or len(encoded(value)) > REQUEST_BYTES
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['contract'] != CONTRACT
            or not isinstance(value['origin_remote_name'], str)
            or re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', value['origin_remote_name']) is None):
        _unavailable()
    if (sum(value[key] is not None for key in ('name', 'target', 'original_job_id')) != 1
            or sum(value[key] is not None for key in ('operation_id', 'request_id')) != 1):
        _unavailable()
    for key in KEYS - {'schema_version', 'contract'}:
        if value[key] is not None and (not isinstance(value[key], str) or not value[key]):
            _unavailable()
    if value['name'] is not None and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', value['name']) is None:
        _unavailable()
    if value['target'] is not None:
        project, environment = parse_target(value['target'])
        if value['target'] != project + '/' + environment:
            _unavailable()
    if value['original_job_id'] is not None:
        validate_job_id(value['original_job_id'])
    if value['operation_id'] is not None and str(uuid.UUID(value['operation_id'])) != value['operation_id']:
        _unavailable()
    if value['request_id'] is not None:
        request_identifier(value['request_id'])
    return value


def closed_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                _unavailable()
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def decode_request(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > REQUEST_BYTES:
        _unavailable()
    return validate_request(closed_json(raw))


def owner_projection(owner):
    return {key: getattr(owner, key) for key in ('target_identity', 'ownership_marker', 'runtime_revision')}


def validate_response(value, *, selector, owner):
    if (type(value) is not dict or set(value) != {'schema_version', 'contract', 'ok', 'owner', 'selector', 'status', 'operation'}
            or len(encoded(value)) > RESPONSE_BYTES or type(value['schema_version']) is not int
            or value['schema_version'] != 1 or value['contract'] != CONTRACT
            or value['owner'] != owner or value['selector'] != selector
            or type(value['ok']) is not bool or value['status'] not in STATUSES
            or value['ok'] != (value['status'] in SERVICED)):
        _unavailable()
    if (type(owner) is not dict or set(owner) != {'target_identity', 'ownership_marker', 'runtime_revision'}
            or any(not isinstance(item, str) or re.fullmatch('[0-9a-f]{24}', item) is None for item in owner.values())):
        _unavailable()
    operation = value['operation']
    if value['status'] != 'found':
        if operation is not None:
            _unavailable()
    elif (type(operation) is not dict
            or set(operation) != {'operation_id', 'delivery_state', 'execution_state', 'evidence_completeness'}
            or str(uuid.UUID(operation['operation_id'])) != operation['operation_id']
            or operation['delivery_state'] not in {'incomplete', 'succeeded', 'failed', 'unknown'}
            or operation['execution_state'] not in TERMINAL | {'accepted', 'running', 'unknown'}
            or operation['evidence_completeness'] not in {'complete', 'partial', 'missing', 'expired', 'conflicting', 'unsupported'}
            or (selector['operation_id'] is not None and operation['operation_id'] != selector['operation_id'])):
        _unavailable()
    return value


def _named_binding(request, owner, budget):
    from sandbox.hosting import stack_registry
    from sandbox.hosting.target_resolution import parse_target
    from sandbox.hosting.recovery.repository import RecoveryRepository
    remote = request['origin_remote_name']
    if request['name'] is not None:
        record = stack_registry.read_exact_record(remote, request['name'], budget=budget)
        if record is None:
            return None
        project, environment = parse_target(str(record.get('project')) + '/' + str(record.get('environment')))
    else:
        project, environment = parse_target(request['target'])
    admission = RecoveryRepository().read_exact_delivery_admission(
        remote + '/' + project + '/' + environment, budget=budget)
    if admission is None:
        return None
    # Only the current retained attempt is reachable through a named record.
    if request['request_id'] is not None and admission['request_id'] != request['request_id']:
        return None
    if admission['evidence']['machine_identity'] != owner.target_identity:
        raise DeliveryError('binding_mismatch')
    from sandbox.core._hosting import compose_project_name
    from sandbox.hosting.recovery.models import canonical_digest
    runtime_identity = canonical_digest({'project': project, 'environment': environment,
        'compose_project': compose_project_name({'project': project, 'environment': environment})})
    if admission['evidence']['runtime_identity'] != runtime_identity:
        raise DeliveryError('binding_mismatch')
    return {'target': validate_target({'schema_version': 1,
        'project_identity': admission['project_identity'], 'project_root_digest': admission['project_root_digest'],
        'target_kind': 'hosted', 'remote_name': remote, 'environment': environment,
        'label': None, 'runtime_identity': runtime_identity, 'machine_identity': owner.target_identity,
        'registered_host_digest': admission['evidence']['host_identity'], 'workspace_id': None,
        'instance_id': None, 'instance_incarnation_id': None}),
        'request_id': admission['request_id'], 'job_id': admission['job_id'], 'operation_id': None}


def inspect_current_delivery(request):
    from sandbox.delivery.repository import DeliveryRepository
    request = validate_request(request)
    budget = Budget()
    owner = read_current_server_owner()
    response = {'schema_version': 1, 'contract': CONTRACT, 'ok': False,
        'owner': owner_projection(owner), 'selector': request, 'status': 'unavailable', 'operation': None}
    try:
        repository = DeliveryRepository(owner.sandbox_home, deadline=budget.deadline)
        if request['original_job_id'] is not None:
            binding = repository.lookup_server_binding(request['original_job_id'],
                operation_id=request['operation_id'], request_id=request['request_id'])
            if binding is not None and (binding['target_identity'] != owner.target_identity
                    or binding['ownership_marker'] != owner.ownership_marker):
                raise DeliveryError('binding_mismatch')
        else:
            binding = _named_binding(request, owner, budget)
        budget.check()
        if binding is None:
            response['status'] = 'binding_unavailable'
        else:
            target = binding['target']
            if target['remote_name'] != request['origin_remote_name']:
                raise DeliveryError('binding_mismatch')
            lookup = (repository.lookup_operation(request_scope(target), request['operation_id'])
                      if request['operation_id'] is not None and request['original_job_id'] is not None else
                      repository.lookup_request(request_scope(target), binding['request_id']))
            budget.check()
            if (request['operation_id'] is not None
                    and (lookup['operation_id'] is not None or request['original_job_id'] is None)
                    and lookup['operation_id'] != request['operation_id']):
                raise DeliveryError('binding_unavailable')
            if lookup['operation_id'] is not None and binding['operation_id'] is not None and lookup['operation_id'] != binding['operation_id']:
                raise DeliveryError('binding_mismatch')
            operation = lookup['operation']
            if operation is not None:
                keys = ('project_identity', 'project_root_digest', 'remote_name', 'target_kind', 'environment', 'label', 'runtime_identity', 'machine_identity', 'registered_host_digest')
                if (any(operation['target'][key] != target[key] for key in keys)
                        or operation['request_id'] != binding['request_id']
                        or (request['original_job_id'] is None and operation['job_id'] != binding['job_id'])):
                    raise DeliveryError('binding_mismatch')
                response['status'] = 'found'
                response['operation'] = {key: operation[key] for key in
                    ('operation_id', 'delivery_state', 'execution_state', 'evidence_completeness')}
            elif lookup['status'] == 'delivery_request_expired':
                response['status'] = 'expired'
            else:
                response['status'] = 'missing'
        budget.check()
        if read_current_server_owner() != owner:
            _unavailable()
    except DeliveryError as exc:
        code = 'unsupported' if exc.code == 'unsupported_capability' else exc.code
        response.update(status=code if code in STATUSES else 'unavailable', operation=None)
    except (OSError, ValueError, TypeError, KeyError, ServerCLIUnavailable):
        response.update(status='unavailable', operation=None)
    response['ok'] = response['status'] in SERVICED
    return validate_response(response, selector=request, owner=owner_projection(owner))


def contract_available():
    from sandbox.commands.job_policy import _configure_capabilities
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    _configure_capabilities(parser)
    return '--inspect-delivery' in parser._option_string_actions


def observe_delivery(remote_name, *, name=None, target=None, original_job_id=None,
                     operation_id=None, request_id=None):
    """No checkout, environment-file read, durable submission or public fallback."""
    from sandbox.core import _remote
    from sandbox.resources.context import authenticated_target_identity
    request = validate_request(dict(schema_version=1, contract=CONTRACT,
        origin_remote_name=remote_name, name=name, target=target, original_job_id=original_job_id,
        operation_id=operation_id, request_id=request_id))
    budget = Budget()
    identity = authenticated_target_identity(remote_name, budget_seconds=budget.remaining())
    record = _remote.get_remote(remote_name, budget_seconds=budget.remaining())
    if (not isinstance(record, dict) or record.get('joined') is not True
            or record.get('control_transport') != 'tailscale'
            or (record.get('control_identity') or {}).get('target_identity') != identity['target_identity']):
        _unavailable()
    service = record.get('mcp_service') or {}
    expected = {'target_identity': identity['target_identity'],
                'ownership_marker': service.get('ownership_marker'), 'runtime_revision': service.get('runtime_revision')}
    def control(argv, maximum):
        result = _remote.ssh_run_bounded(record,
            shlex.join([_remote.remote_sb_path(record), *argv, '--json']),
            timeout=budget.remaining(), max_output=maximum, terminate_on_output_limit=True)
        budget.check()
        if (getattr(result, 'termination_reason', None) or getattr(result, 'stdout_truncated', False)
                or getattr(result, 'stderr_truncated', False) or result.returncode not in (0, 1)):
            _unavailable()
        raw = result.stdout
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > maximum:
            _unavailable()
        return closed_json(raw)
    capabilities = control(['job-capabilities'], 16384)
    if (not isinstance(capabilities, dict) or capabilities.get('ok') is not True
            or any(capabilities.get(key) != value for key, value in expected.items())):
        _unavailable()
    if CONTRACT not in capabilities.get('inspection_capabilities', []):
        raise DeliveryError('unsupported')
    response = control(['job-capabilities', '--inspect-delivery', encoded(request).decode('ascii')], RESPONSE_BYTES)
    validate_response(response, selector=request, owner=expected)
    # Refresh the authenticated owner after the read; stale selection is refusal.
    after = _remote.get_remote(remote_name, budget_seconds=budget.remaining())
    if ((after.get('control_identity') or {}).get('target_identity') != expected['target_identity']
            or any((after.get('mcp_service') or {}).get(key) != expected[key]
                   for key in ('ownership_marker', 'runtime_revision'))):
        _unavailable()
    budget.check()
    return response
