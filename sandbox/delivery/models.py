"""Closed, bounded, nonsecret delivery diagnostic values."""
from __future__ import annotations

import datetime as dt
import json
import math
from urllib.parse import urlsplit
import re
import uuid
from sandbox.hosting.recovery.models import canonical_digest
from sandbox.services.redaction import redact_structure, redact_text

SCHEMA_VERSION = 1
MAX_OPERATION_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
STATES = {'known', 'partial', 'missing', 'unsupported', 'expired', 'conflicting', 'not_applicable'}
RESULTS = {'passed', 'failed', 'pending', 'unknown', 'not_applicable'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
KINDS = {'hosted_apply', 'immutable_activation', 'deploy_exposure', 'preview_creation'}
REASONS = STATES | RESULTS | {'none', 'delivery_record_incomplete', 'delivery_request_expired', 'request_conflict', 'delivery_request_capacity', 'delivery_capacity', 'delivery_cursor_expired', 'delivery_cursor_invalid', 'delivery_store_unavailable', 'delivery_contract_invalid', 'delivery_terminal_conflict', 'required_evidence_missing', 'binding_mismatch', 'initializer_refused', 'effect_unknown', 'authority_pending', 'retention', 'bounds', 'unsupported_capability'}

class DeliveryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

def fail(code='delivery_contract_invalid'):
    raise DeliveryError(code)

def encoded(value):
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        fail()

def text(value, limit=128):
    if not isinstance(value, str) or not value or '\x00' in value:
        fail()
    try:
        if len(value.encode('utf-8')) > limit:
            fail()
    except UnicodeError:
        fail()
    return value

def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', text(value)) or redact_text(value) != value:
        fail()
    return value

def request_identifier(value):
    """Preserve opaque request IDs accepted by the activation owner."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', text(value, 256)) or redact_text(value) != value:
        fail()
    return value

def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        fail()
    return value

def timestamp(value):
    text(value, 40)
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', value):
        fail()
    try:
        dt.datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        fail()
    return value

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')

def choice(values):
    def check(value):
        if not isinstance(value, str) or value not in values:
            fail()
        return value
    return check

def optional(check):
    return lambda value: None if value is None else check(value)

def integer(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        fail()
    return value

def boolean(value):
    if type(value) is not bool:
        fail()
    return value

def array(check, maximum):
    def validate(value):
        if not isinstance(value, list) or len(value) > maximum:
            fail()
        return [check(item) for item in value]
    return validate

def obj(value, fields, required=None):
    if not isinstance(value, dict) or set(value) - set(fields) or set(required or ()) - set(value):
        fail()
    return {key: fields[key](item) for key, item in value.items()}

def schema(value):
    if type(value) is not int or value != 1:
        fail('unsupported_capability')
    return value

def reason(value):
    result = obj(value, {'code': choice(REASONS), 'message': lambda v: redact_text(text(v, 256))}, ['code', 'message'])
    if re.search(r'(?:https?://|(?:^|\s)/(?!/)|[A-Za-z]:\\)', result['message']):
        fail()
    text(result['message'], 256)
    if len(encoded(result)) > 256:
        fail()
    return result

def reference(value):
    result = obj(value, {'kind': identifier, 'identifier': request_identifier if isinstance(value, dict) and value.get('kind') == 'recovery_result' else identifier, 'digest': digest}, ['kind'])
    if not ('identifier' in result or 'digest' in result) or len(encoded(result)) > 512:
        fail()
    return result

def revision(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value):
        fail()
    return value

TARGET_FIELDS = {key: optional(identifier) for key in ('project_identity', 'remote_name', 'machine_identity', 'environment', 'label', 'instance_id', 'instance_incarnation_id', 'workspace_id', 'runtime_identity')}
TARGET_FIELDS.update(schema_version=schema, project_root_digest=digest, registered_host_digest=optional(digest), target_kind=choice({'hosted', 'deploy', 'preview'}))
def validate_target(value):
    result = obj(value, TARGET_FIELDS, TARGET_FIELDS)
    for name in ('project_identity', 'remote_name'):
        identifier(result[name])
    if result['target_kind'] == 'hosted':
        identifier(result['environment']); identifier(result['runtime_identity'])
        if result['label'] is not None:
            fail()
    elif result['environment'] is not None or result['label'] is None:
        fail()
    if (result['instance_id'] is None) != (result['instance_incarnation_id'] is None):
        fail()
    return result

def target_digest(target):
    return canonical_digest(validate_target(target))

def request_scope(target):
    target = validate_target(target)
    return canonical_digest({key: target[key] for key in ('project_identity', 'target_kind', 'remote_name', 'environment', 'label')})

def scope_digest(scope):
    if isinstance(scope, str):
        return digest(scope)
    fields = {key: optional(identifier) for key in ('project_identity', 'remote_name', 'environment', 'label')}
    fields['target_kind'] = choice({'hosted', 'deploy', 'preview'})
    checked = obj(scope, fields, fields)
    identifier(checked['project_identity']); identifier(checked['remote_name'])
    if (checked['target_kind'] == 'hosted' and (checked['environment'] is None or checked['label'] is not None)) or (checked['target_kind'] != 'hosted' and (checked['label'] is None or checked['environment'] is not None)):
        fail()
    return canonical_digest(checked)

def request_key_digest(request_key, operation_id=None):
    return canonical_digest({'domain': 'delivery.request_id' if request_key is not None else 'delivery.operation_id', 'key': request_identifier(request_key) if request_key is not None else identifier(operation_id)})

def requirement(value):
    return obj(value, {'kind': choice({'workload', 'initializer', 'runtime_identity', 'runtime_health', 'exposure', 'public_release_identity', 'edge_proof'}), 'applicability': choice({'required', 'optional', 'not_applicable'}), 'source': identifier}, ['kind', 'applicability', 'source'])

def application(value):
    fields = {key: optional(digest) for key in ('dirty_digest', 'artifact_digest', 'config_digest', 'plan_digest', 'proof_digest')}
    fields.update(source_identity=optional(identifier), commit=optional(revision), dirty_policy=choice({'clean_required', 'overlay_declared', 'not_applicable'}))
    required = set(fields)
    from sandbox.hosting.recovery.models import validate_source_artifact
    fields['source_artifact'] = lambda artifact: validate_source_artifact(artifact, value.get('commit'))
    try:
        return obj(value, fields, required)
    except ValueError:
        fail()

def runtime_revision(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{24}', value):
        fail()
    return value

def control(value):
    def capabilities(value):
        if not isinstance(value, dict) or len(value) > 32:
            fail()
        return {identifier(k): integer(v) for k, v in value.items()}
    fields = {'source_commit': optional(revision), 'source_runtime_revision': optional(runtime_revision), 'installed_controller_runtime_revision': optional(runtime_revision), 'capability_versions': capabilities}
    return obj(value, fields, fields)

def validate_requested_outcome(value):
    fields = {'kind': choice(KINDS), 'target_digest': digest, 'application': application, 'control': control, 'configuration_digest': optional(digest), 'route_contract_digest': optional(digest), 'requirements': array(requirement, 32), 'requested_at': timestamp}
    result = obj(value, fields, fields)
    if 'source_artifact' in result['application'] and result['kind'] != 'hosted_apply':
        fail()
    kinds = [r['kind'] for r in result['requirements']]
    if len(kinds) != len(set(kinds)):
        fail()
    if result['kind'] == 'hosted_apply' and (result['application']['dirty_policy'] != 'clean_required' or result['application']['dirty_digest'] is not None):
        fail()
    return result

def intent_digest(outcome):
    # Keep requested_at as frozen evidence, not semantic request identity.
    checked = validate_requested_outcome(outcome)
    return canonical_digest({key: value for key, value in checked.items() if key != 'requested_at'})

ROUTE_REASONS = {
    'application_json_invalid', 'application_marker_mismatch', 'application_marker_missing',
    'application_status_mismatch', 'deadline_exceeded', 'dns_unavailable',
    'http_https_upgrade_missing', 'network_unavailable', 'network_timeout',
    'redirect_invalid', 'redirect_limit', 'redirect_loop', 'redirect_origin_mismatch',
    'redirect_path_invalid', 'redirect_path_mismatch', 'redirect_query_mismatch',
    'route_observation_unavailable', 'tls_certificate_invalid', 'tls_failed',
    'edge_proof_identity_mismatch', 'edge_proof_invalid', 'edge_proof_missing',
    'edge_proof_not_required', 'edge_proof_passed', 'edge_proof_failed',
    'edge_proof_pending', 'edge_proof_unknown', 'edge_proof_incomplete', 'edge_proof_stale',
    'worker_reap_unconfirmed', 'route_evidence_too_large', 'response_body_too_large',
}

def bounded_integer(minimum, maximum):
    def check(value):
        integer(value)
        if not minimum <= value <= maximum:
            fail()
        return value
    return check

def hostname(value):
    text(value, 253)
    if value != value.lower() or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        fail()
    if any(not part or len(part) > 63 or part.startswith('-') or part.endswith('-') for part in value.split('.')):
        fail()
    return value

def public_path(value):
    text(value, 2048)
    if not value.startswith('/') or value.startswith('//') or any(char in value for char in ('?', '#', '\\')) or any(ord(c) < 32 for c in value):
        fail()
    if redact_text(value) != value or re.search(r'(?:wp-login|autologin|login-token)', value, re.I):
        fail()
    return value

def public_origin(value):
    text(value, 512)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443):
            fail()
        hostname(parsed.hostname)
    except (ValueError, TypeError):
        fail()
    return value

def route_check(value):
    fields = {'path': public_path, 'result': choice({'passed', 'failed', 'incomplete'}), 'started_at': timestamp, 'finished_at': timestamp, 'attempts': bounded_integer(1, 2), 'origin': public_origin, 'status': bounded_integer(100, 599), 'query_preserved': boolean, 'http_upgrade': boolean, 'tls': choice({'passed'}), 'redirect_count': bounded_integer(0, 10), 'marker_digests': array(digest, 32), 'reason': choice(ROUTE_REASONS)}
    result = obj(value, fields, ('path', 'result', 'started_at', 'finished_at', 'attempts'))
    if result['result'] == 'passed' and (not result.get('query_preserved') or not result.get('http_upgrade') or result.get('tls') != 'passed' or not result.get('marker_digests')):
        fail()
    return result

def route_host(value):
    dns_fields = {'result': choice({'passed', 'failed', 'incomplete'}), 'observed_at': timestamp, 'address_count': bounded_integer(1, 128), 'address_digest': digest, 'reason': choice(ROUTE_REASONS)}
    def release(v):
        if isinstance(v, dict) and set(v) == {'state'}:
            return obj(v, {'state': choice({'unsupported'})}, ['state'])
        return route_check(v)
    return obj(value, {'hostname': hostname, 'dns': lambda v: obj(v, dns_fields, ('result', 'observed_at')), 'checks': array(route_check, 32), 'release_identity': release}, ('hostname', 'dns', 'checks', 'release_identity'))

def validate_route_observation(value):
    def elapsed(v):
        if type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 2**63 - 1:
            fail()
        return v
    edge_fields = {'result': choice({'passed', 'failed', 'incomplete', 'not_applicable'}), 'reason': choice(ROUTE_REASONS), 'observed_at': timestamp, 'proof_digest': digest}
    fields = {'schemaVersion': schema, 'contract_digest': digest, 'hostnames': array(hostname, 20), 'deadline_seconds': bounded_integer(10, 300), 'started_at': timestamp, 'finished_at': timestamp, 'elapsed_seconds': elapsed, 'result': choice({'verified', 'failed', 'incomplete'}), 'hosts': array(route_host, 20), 'edge': lambda v: obj(v, edge_fields, ('result', 'reason')), 'scope': choice({'application_and_release', 'application_availability_only'}), 'release_identity_state': choice({'declared', 'passed', 'failed', 'incomplete', 'unsupported'}), 'reason': choice(ROUTE_REASONS)}
    result = obj(value, fields, set(fields) - {'reason'})
    names = result['hostnames']; observed = [host['hostname'] for host in result['hosts']]
    if len(names) != len(set(names)) or len(observed) != len(set(observed)) or set(observed) - set(names):
        fail()
    if result['result'] == 'verified':
        if set(observed) != set(names) or not names:
            fail()
        for host in result['hosts']:
            if host['dns']['result'] != 'passed' or not host['checks'] or any(check['result'] != 'passed' for check in host['checks']):
                fail()
            if result['scope'] == 'application_and_release' and host['release_identity'].get('result') != 'passed':
                fail()
    if len(encoded(result)) > 64 * 1024:
        fail('delivery_capacity')
    return result

def route_contract(value):
    """Validate a frozen public declaration; it is never runtime evidence."""
    from sandbox.delivery.routes import prepare_route_verification
    fields = {'schemaVersion': schema, 'delivery': lambda item: item,
              'primary_hostname': hostname, 'hostnames': array(hostname, 20),
              'release_expected': optional(lambda item: text(item, 80)),
              'edge_binding': optional(lambda item: obj(item, {key: digest for key in
                  ('target_digest', 'config_digest', 'release_digest')},
                  ('target_digest', 'config_digest', 'release_digest'))),
              'contract_digest': digest}
    checked = obj(value, fields, fields)
    if not checked['hostnames'] or checked['hostnames'][0] != checked['primary_hostname']:
        fail()
    prepared = prepare_route_verification(checked['delivery'], runtime_kind='declared',
        primary_hostname=checked['primary_hostname'], aliases=checked['hostnames'][1:],
        application_commit=checked['release_expected'], artifact_digest=checked['release_expected'],
        edge_supported=checked['edge_binding'] is not None, edge_binding=checked['edge_binding'])
    if encoded(prepared) != encoded(checked):
        fail()
    return prepared


def creation_context(value):
    # Only the instance owner's closed projection may cross this boundary.
    from sandbox.server_config.models import validate_creation_context
    return validate_creation_context(value)

def creation_receipt(value):
    # Instance owner defines and validates this projection; importing lazily
    # keeps diagnostic readers independent of instance service initialization.
    from sandbox.server_config.models import validate_creation_receipt
    return validate_creation_receipt(value)

def evidence(value):
    fields = {'source_kind': identifier, 'observed_at': optional(timestamp), 'target_digest': digest, 'applicability': choice({'required', 'optional', 'not_applicable'}), 'state': choice(STATES), 'result': choice(RESULTS), 'reason': reason, 'references': array(reference, 32), 'generation': optional(integer), 'services': array(identifier, 32), 'images': array(identifier, 32), 'application_revision': optional(revision), 'initializer_result': choice(RESULTS), 'proof_digest': optional(digest), 'operation_id': optional(identifier), 'request_id': optional(request_identifier), 'job_id': optional(identifier), 'instance_incarnation_id': optional(identifier), 'contract_digest': optional(digest), 'route_observation': validate_route_observation, 'route_contract': route_contract, 'creation_receipt': creation_receipt, 'creation_context': creation_context}
    result = obj(value, fields, ('source_kind', 'observed_at', 'target_digest', 'applicability', 'state', 'result', 'reason'))
    if result['observed_at'] is None and result['state'] == 'known':
        fail()
    prepared = result.get('route_contract')
    if prepared is not None and result.get('contract_digest') != prepared['contract_digest']:
        fail()
    route = result.get('route_observation')
    if route is not None:
        if result.get('contract_digest') not in (None, route['contract_digest']):
            fail()
        if result['result'] == 'passed' and route['result'] != 'verified':
            fail()
    return result

def action(value):
    fields = {'command': choice({'delivery.inspect', 'hosting.recovery.inspect', 'hosting.recovery.observe-reconcile', 'hosting.recovery.continue-edge', 'hosting.image.status', 'host image status', 'job-status'}), 'selectors': lambda v: obj(v, {k: request_identifier if k == 'request_id' else identifier for k in ('remote', 'environment', 'label', 'operation_id', 'request_id', 'job_id')}), 'reason': reason}
    return obj(value, fields, ['command', 'selectors', 'reason'])

def event(value):
    result = obj(value, {'at': timestamp, 'phase': identifier, 'reason': reason, 'references': array(reference, 4)}, ['at', 'phase', 'reason'])
    if len(encoded(result)) > 1024:
        fail()
    return result

def append_event(operation, value):
    """Return a bounded progress document; no log payload is accepted."""
    result = validate_operation(operation)
    if result['execution_state'] in TERMINAL:
        fail('delivery_terminal_conflict')
    events = result['events'] + [event(value)]
    omitted = max(0, len(events) - 64)
    result['events'] = events[-64:]
    result['history']['omitted_events'] += omitted
    return validate_operation(result)

def effect(value):
    return obj(value, {'name': identifier, 'scope_digest': digest, 'state': choice({'configured', 'observed', 'removed', 'failed', 'unknown'}), 'observed_at': timestamp}, ['name', 'scope_digest', 'state', 'observed_at'])

def validate_operation(value):
    fields = {key: optional(evidence) for key in ('admission', 'creation', 'runtime', 'routes', 'edge', 'authority')}
    fields.update(schema_version=schema, operation_id=identifier, request_id=optional(request_identifier), job_id=optional(identifier), kind=choice(KINDS), intent_digest=digest, target=validate_target, requested_outcome=validate_requested_outcome, accepted_at=timestamp, started_at=optional(timestamp), updated_at=timestamp, finished_at=optional(timestamp), phase=choice({'admitted', 'source', 'instance', 'initializer', 'runtime', 'route', 'edge', 'cleanup', 'terminal', 'unknown'}), execution_state=choice(TERMINAL | {'accepted', 'running', 'unknown'}), delivery_state=choice({'incomplete', 'succeeded', 'failed', 'unknown'}), delivery_succeeded=boolean, evidence_completeness=choice({'complete', 'partial', 'missing', 'expired', 'conflicting', 'unsupported'}), effects=array(effect, 32), recovery_relations=array(reference, 16), retry_of=optional(identifier), failure_stage=optional(identifier), reason=reason, references=array(reference, 32), next_action=optional(action), history=lambda v: obj(v, {'omitted_events': integer}, ['omitted_events']), terminal_snapshot_digest=optional(digest), pinned_reason=optional(reason), events=array(event, 64))
    result = obj(value, fields, fields)
    try:
        if str(uuid.UUID(result['operation_id'])) != result['operation_id']:
            fail()
    except ValueError:
        fail()
    if result['kind'] != result['requested_outcome']['kind'] or result['requested_outcome']['target_digest'] != target_digest(result['target']) or result['intent_digest'] != intent_digest(result['requested_outcome']):
        fail()
    if result['delivery_succeeded'] != (result['delivery_state'] == 'succeeded'):
        fail()
    if result['delivery_succeeded'] and (result['execution_state'] != 'succeeded' or result['evidence_completeness'] != 'complete'):
        fail()
    if (result['execution_state'] in TERMINAL) != (result['finished_at'] is not None):
        fail()
    for key in ('admission', 'creation', 'runtime', 'routes', 'edge', 'authority'):
        if result[key] is not None and result[key]['target_digest'] != result['requested_outcome']['target_digest']:
            fail()
    if len(encoded(result)) > MAX_OPERATION_BYTES:
        fail('delivery_capacity')
    return result

def new_operation(target, requested_outcome, operation_id, request_id=None, job_id=None):
    """Create diagnostic defaults only; this object cannot establish admission."""
    requested_outcome = validate_requested_outcome(requested_outcome)
    stamp = requested_outcome['requested_at']
    result = {
        'schema_version': 1, 'operation_id': operation_id, 'request_id': request_id,
        'job_id': job_id, 'kind': requested_outcome['kind'],
        'intent_digest': intent_digest(requested_outcome), 'target': validate_target(target),
        'requested_outcome': requested_outcome, 'accepted_at': stamp,
        'started_at': None, 'updated_at': stamp, 'finished_at': None,
        'phase': 'unknown', 'execution_state': 'unknown', 'delivery_state': 'incomplete',
        'delivery_succeeded': False, 'evidence_completeness': 'missing',
        'admission': None, 'creation': None, 'runtime': None, 'routes': None,
        'edge': None, 'authority': None, 'effects': [], 'recovery_relations': [],
        'retry_of': None, 'failure_stage': None,
        'reason': {'code': 'missing', 'message': 'Execution and admission have not been proved.'},
        'references': [], 'next_action': None, 'history': {'omitted_events': 0},
        'terminal_snapshot_digest': None, 'pinned_reason': None, 'events': [],
    }
    return validate_operation(result)

def terminal_digest(operation):
    return canonical_digest({k: v for k, v in operation.items() if k != 'terminal_snapshot_digest'})

def operation_summary(operation):
    checked = validate_operation(operation)
    keys = ('operation_id', 'request_id', 'job_id', 'kind', 'accepted_at', 'finished_at', 'execution_state', 'delivery_state', 'delivery_succeeded', 'evidence_completeness', 'failure_stage', 'reason', 'terminal_snapshot_digest')
    result = {k: checked[k] for k in keys}
    if len(encoded(result)) > 2048:
        fail('delivery_capacity')
    return result

def validate_summary(value):
    fields = {'operation_id': identifier, 'request_id': optional(request_identifier), 'job_id': optional(identifier), 'kind': choice(KINDS), 'accepted_at': timestamp, 'finished_at': optional(timestamp), 'execution_state': choice(TERMINAL | {'accepted', 'running', 'unknown'}), 'delivery_state': choice({'incomplete', 'succeeded', 'failed', 'unknown'}), 'delivery_succeeded': boolean, 'evidence_completeness': choice({'complete', 'partial', 'missing', 'expired', 'conflicting', 'unsupported'}), 'failure_stage': optional(identifier), 'reason': reason, 'terminal_snapshot_digest': optional(digest)}
    result = obj(value, fields, fields)
    if result['delivery_succeeded'] != (result['delivery_state'] == 'succeeded') or len(encoded(result)) > 2048:
        fail()
    return result

def validate_history(value):
    fields = {'completeness': choice({'bounded', 'missing', 'partial', 'unsupported', 'expired'}), 'retention_policy': lambda v: obj(v, {k: integer for k in ('terminal_days', 'terminal_per_target', 'terminal_global', 'protected', 'request_guards', 'target_metadata')}), 'oldest_retained_at': optional(timestamp), 'omitted': boolean, 'expired': boolean, 'returned_count': integer, 'next_cursor': optional(lambda v: text(v, 512)), 'limits': lambda v: obj(v, {k: integer for k in ('maximum_limit', 'operation_bytes', 'response_bytes')}), 'operations': array(validate_summary, 50)}
    result = obj(value, fields, fields)
    if result['returned_count'] != len(result['operations']):
        fail()
    return result

def validate_projection(value):
    """Validate the entire response, including every nested output boundary."""
    scope_fields = {'project_identity': identifier, 'project_root_digest': digest, 'remote_name': identifier, 'target_kind': choice({'hosted', 'deploy', 'preview'}), 'environment': optional(identifier), 'label': optional(identifier), 'target_digest': optional(digest), 'operation_id': optional(identifier), 'request_id': optional(request_identifier)}
    fields = {'schema_version': schema, 'ok': boolean, 'error': optional(reason), 'query_scope': lambda v: obj(v, scope_fields, ['project_identity', 'remote_name', 'target_kind', 'environment', 'label']), 'recorded_at': timestamp, 'observation_mode': choice({'recorded_only', 'current_read_only'}), 'latest_attempt': optional(validate_summary), 'latest_retained_complete_success': optional(validate_summary), 'selected_operation': optional(validate_operation), 'current_observation': optional(lambda v: obj(v, {k: optional(evidence) for k in ('admission', 'creation', 'runtime', 'routes', 'edge', 'authority')})), 'history': validate_history, 'next_action': optional(action)}
    fields['recorded_source_evidence'] = optional(lambda v: obj(v, {k: optional(evidence) for k in ('admission', 'creation', 'runtime', 'routes', 'edge', 'authority')}))
    fields['next_action_reason'] = reason
    fields['recovery_operations'] = lambda v: obj(v, {
        'completeness': choice({'known', 'partial', 'missing', 'expired'}),
        'omitted': integer, 'operations': array(validate_summary, 10)},
        {'completeness', 'omitted', 'operations'})
    return obj(value, fields, set(fields) - {'recorded_source_evidence', 'next_action_reason', 'recovery_operations'})

def serialize_projection(value):
    """Serialize only a closed envelope, never an upstream owner dictionary."""
    checked = validate_projection(value)
    payload = encoded(redact_structure(checked))
    if len(payload) > MAX_RESPONSE_BYTES:
        fail('delivery_capacity')
    return payload.decode('utf-8')
