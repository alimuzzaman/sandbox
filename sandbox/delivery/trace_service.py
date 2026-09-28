"""Full-command diagnostics joined through bounded, read-only owner ports.

Producer observations describe their own history. Only exact native owner proof
can establish a current deployment result; inspection never repairs that history.
"""
from __future__ import annotations

import copy
import json
import shlex
import time

from . import models as m
from .trace_models import (
    CODES, TraceContractError, TraceQueryBudget, encoded, fail, parse_trace_start,
    parse_trace_record, parse_trace_owner_record, role_detail, safe_reason,
    serialize_trace_query, uuid_value,
)
from .producers.lenzora import decode_owner_projection

_TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
_FAILURES = _TERMINAL - {'succeeded'}
_NATIVE_FIELDS = ('role', 'request_id', 'operation_id', 'generation', 'plan_digest',
                  'proof_digest', 'configuration_digest', 'target_digest')
_DETAIL_PAYLOADS = {
    'source': {'requested_revision': None, 'observed_revision': None,
               'control_revision': None, 'source_artifact': None},
    'artifact': {'policy': None, 'receipt_digest': None, 'plan_digest': None,
                 'proof_digest': None, 'manifest_digests': []},
    'target': {'registered_host_digest': None, 'environment': None, 'incarnation_id': None},
    'generation': {'generation_id': None, 'digest': None},
    'configuration': {'digest': None},
    'initializer': {'status': None, 'receipt_digest': None},
    'runtime': {'source_revision': None, 'image_digests': [], 'health': None,
                'verification_digest': None},
    'public_verification': {'hosts': [], 'edge_state': None, 'edge_proof_digest': None},
    'job': {'submission_digest': None, 'started_at': None, 'finished_at': None,
            'exit_code': None, 'output_complete': None},
}


def _code(error):
    value = getattr(error, 'code', 'trace_contract_invalid' if isinstance(error, (ValueError, TypeError, KeyError)) else 'owner_unavailable')
    if value == 'delivery_contract_invalid':
        value = 'trace_contract_invalid'
    return value if value in CODES else 'owner_unavailable'


def _detail(source_kind, *, observed_at=None, target_digest=None):
    return {name: {'source_kind': source_kind, 'observed_at': observed_at,
        'target_digest': target_digest, 'applicability': 'required', 'state': 'partial',
        'result': 'unknown', 'reason': safe_reason('required_evidence_missing'),
        **copy.deepcopy(payload)} for name, payload in _DETAIL_PAYLOADS.items()}


def _link(role, request_id=None, *, job_id=None, operation_id=None, control=False):
    return {'role': role, 'request_id': request_id, 'job_id': job_id,
        'operation_id': operation_id, 'source_role': 'control' if control else 'application',
        'state': 'partial', 'result': 'unknown', 'provenance': 'producer_recorded',
        'observed_at': None, 'proof_digest': None,
        'reason': safe_reason('required_evidence_missing'), 'detail': None}


def _mark(block, *, result='passed', state='known', **payload):
    block.update(state=state, result=result,
                 reason=safe_reason('known' if state in {'known', 'complete'} else 'required_evidence_missing'),
                 **payload)


def _summary(result='unknown', completeness='partial', code='required_evidence_missing'):
    return {'joined_deployment_result': result, 'completeness': completeness,
            'reason': safe_reason(code)}


def _query(scope, trace_id, trace_request_id, at):
    return {'schema_version': 1, 'query_kind': 'deployment_trace', 'ok': True,
        'error': None, 'query_scope': {'project_identity': scope['project_identity'],
            'project_root_digest': scope['project_root_digest'], 'trace_id': trace_id,
            'trace_request_id': trace_request_id}, 'recorded_at': at,
        'observation_mode': 'recorded_only', 'trace': None,
        'owner_evidence': {'summary': _summary(), 'links': [], 'parent_stages': {
            'role_stages': None, 'completeness': 'missing',
            'reason': safe_reason('history_unavailable')}},
        'recovery_operations': {'completeness': 'missing', 'omitted': 0, 'operations': []},
        'mutation_receipt': None, 'coverage': {'trace_state': 'missing',
            'owner_state': 'missing', 'early_history': 'unavailable', 'omitted_events': 0,
            'omitted_links': 0, 'reason': safe_reason('missing')}, 'next_action': None}


def _query_error(code, *, scope=None, trace_id=None, trace_request_id=None):
    # A selector/transport error has no established project owner. The sentinel
    # is labelled by ok=false and is never used as an owner lookup identity.
    scope = scope or {'project_identity': 'unavailable',
                     'project_root_digest': m.canonical_digest({'unavailable': True})}
    result = _query(scope, trace_id, trace_request_id, m.now())
    result.update(ok=False, error=safe_reason(code))
    result['coverage'].update(trace_state='unavailable', reason=safe_reason(code))
    return result


class TraceService:
    def __init__(self, repository, producer_registry, job_reader, activation_reader,
                 delivery_reader, recovery_reader, clock=None):
        self.repository = repository
        self.registry = producer_registry
        self.job_reader = job_reader
        self.activation_reader = activation_reader
        self.delivery_reader = delivery_reader
        self.recovery_reader = recovery_reader
        self.clock = clock

    def _now(self):
        value = self.clock.now() if self.clock is not None and hasattr(self.clock, 'now') else m.now()
        return m.timestamp(value)

    def _scope(self, project_dir, *, write=False):
        scope = dict(self.registry.scope(project_dir, write=write))
        m.identifier(scope['project_identity']); m.digest(scope['project_root_digest'])
        # Trusted observations are generated by this service, not composition callers.
        scope.pop('owner_observation', None)
        return scope

    def capabilities(self):
        return self.registry.capabilities()

    def start(self, project_dir, trace_request_id, input):
        intent = parse_trace_start(input)
        descriptor = self.registry.get_producer(intent['producer']['id'])
        if (descriptor is None or descriptor['producer_version'] != intent['producer']['version'] or
                descriptor['projection_schema'] != intent['producer']['projection_schema']):
            fail('trace_revision_unsupported')
        uuid_value(trace_request_id)
        return self.repository.start(self._scope(project_dir, write=True), trace_request_id, intent)

    def record(self, project_dir, trace_id, mutation_id, expected_sequence, input):
        record = parse_trace_record(input)
        uuid_value(trace_id); uuid_value(mutation_id); m.integer(expected_sequence)
        scope = self._scope(project_dir, write=True)
        if record['payload']['kind'] == 'finish':
            budget = TraceQueryBudget(time.monotonic() + 5)
            retained = self.repository.read(scope, trace_id=trace_id, budget=budget)
            if retained.get('trace') is not None:
                observation = self._observe(retained['trace'], scope, budget)
                scope['owner_observation'] = observation['observation']
        return self.repository.record(scope, trace_id, mutation_id, expected_sequence, record)

    def owner_status(self, project_dir, producer, parent_request_id, publication_id=None):
        if producer != 'lenzora-hosted-v1':
            fail()
        m.identifier(parent_request_id)
        if publication_id is not None:
            uuid_value(publication_id)
        budget = TraceQueryBudget(time.monotonic() + 5)
        scope = self._scope(project_dir)
        return self.repository.read_owner(scope, producer, parent_request_id,
                                          publication_id=publication_id, budget=budget)

    def owner_record(self, project_dir, producer, parent_request_id, publication_id,
                     expected_sequence, input):
        if producer != 'lenzora-hosted-v1':
            fail()
        record = parse_trace_owner_record(input)
        uuid_value(publication_id); m.identifier(parent_request_id); m.integer(expected_sequence)
        scope = self._scope(project_dir, write=True)
        budget = TraceQueryBudget(time.monotonic() + 5)
        retained = self.repository.read_owner(scope, producer, parent_request_id, budget=budget)
        parent = retained.get('record')
        if parent is not None:
            projection = parent['projection']
            # Read the existing exact parent; the pending producer publication
            # cannot promote its own claims into owner authority.
            document = {'project_identity': scope['project_identity'],
                'project_root_digest': scope['project_root_digest'],
                'requested': {'mode': 'deploy', 'target': {
                    'environment': projection['run']['release']['target'], 'remote_name': None}},
                'owner_projection': projection, 'parent_link': None, 'candidate_links': [],
                'stages': {}, 'controller_context': parent['controller_context']}
            scope['owner_observation'] = self._observe(document, scope, budget)['observation']
        return self.repository.record_owner(scope, producer, parent_request_id,
                                             publication_id, expected_sequence, record)

    def _call(self, reader, budget, *arguments):
        budget.check()
        if reader is None:
            return None
        result = reader(*arguments, budget)
        budget.check()
        return result

    def _job(self, candidate, scope, budget):
        link = _link(candidate['role'], candidate['request_id'],
                     job_id=candidate['job_id'], control=True)
        native = self._call(self.job_reader, budget, candidate, scope)
        if not native or native.get('state') != 'known':
            if native and native.get('state') == 'conflicting':
                link.update(state='conflicting', reason=safe_reason('binding_mismatch'))
            return link, False
        job, submission = native.get('job'), native.get('submission')
        if not isinstance(job, dict) or not isinstance(submission, dict):
            return link, False
        pairs = {'job_id': candidate['job_id'], 'request_id': candidate['request_id'],
                 'project_root_digest': candidate['control_root_digest'],
                 'source_commit': candidate['control_source_commit'],
                 'project_identity': scope['project_identity']}
        if (any(job.get(key) != value for key, value in pairs.items()) or
                any(submission.get(key) != candidate[key] for key in
                    ('role', 'request_id', 'control_root_digest', 'control_source_commit', 'submission_digest'))):
            link.update(state='conflicting', reason=safe_reason('binding_mismatch'))
            return link, False
        lifecycle = job.get('lifecycle')
        if lifecycle not in _TERMINAL | {'accepted', 'queued', 'running', 'cancelling'}:
            return link, False
        terminal = lifecycle in _TERMINAL
        outcome = 'passed' if lifecycle == 'succeeded' else 'failed' if lifecycle in _FAILURES else 'pending'
        details = _detail('job', observed_at=job.get('finished_at') or job.get('started_at') or job.get('accepted_at'))
        for key, block in details.items():
            if key not in {'source', 'job'}:
                _mark(block, state='not_applicable', result='not_applicable', applicability='not_applicable')
        _mark(details['source'], control_revision=job['source_commit'])
        _mark(details['job'], result=outcome, submission_digest=submission['submission_digest'],
              started_at=job.get('started_at'), finished_at=job.get('finished_at'),
              exit_code=job.get('exit_code'), output_complete=job.get('output_completeness') == 'complete')
        link.update(state='complete', result=outcome, provenance='owner_verified',
                    observed_at=details['job']['observed_at'], proof_digest=submission['submission_digest'],
                    reason=safe_reason('known' if terminal else 'authority_pending'), detail=role_detail(details))
        return link, terminal

    def _delivery(self, candidate, scope, budget, native):
        value = self._call(self.delivery_reader, budget, candidate, scope)
        if value is None:
            return None
        operation = m.validate_operation(value)
        if operation['execution_state'] in _TERMINAL and operation['terminal_snapshot_digest'] != m.terminal_digest(operation):
            fail('binding_mismatch')
        projection = scope['projection']; run = projection['run']
        target = operation['target']; application = operation['requested_outcome']['application']
        requested = scope['requested_target']
        if (operation['kind'] != 'immutable_activation' or operation['request_id'] != run['activation_request_id'] or
                candidate.get('operation_id') not in (None, operation['operation_id']) or
                target['target_kind'] != 'hosted' or target['environment'] != run['release']['target'] or
                requested['remote_name'] is None or target['remote_name'] != requested['remote_name'] or
                application['commit'] != run['release']['revision'] or
                scope.get('application_root_digest') is None or target['project_root_digest'] != scope['application_root_digest']):
            fail('binding_mismatch')
        native_target = native.get('target') if native else None
        target_parts = (native_target or {}).get('target_identity', '').split('/')
        if native_target is not None and (len(target_parts) != 3 or target_parts[0] != target['remote_name'] or
                target_parts[2] != target['environment'] or not target_parts[1]):
            fail('binding_mismatch')
        if candidate.get('target_digest') is not None and native_target is not None and candidate['target_digest'] != m.canonical_digest(native_target):
            fail('binding_mismatch')
        result = native.get('result') if native else None
        detail = native.get('detail') if native else None
        if not result or not detail:
            return None
        if result.get('request_digest') != native.get('request_digest'):
            fail('binding_mismatch')
        authority = operation['authority']
        # The original operation is bound to the same native transaction, target,
        # and exact plan/proof/config. Similar request strings cannot suffice.
        if (authority is None or authority.get('proof_digest') != result.get('transaction_digest') or
                authority.get('request_id') != run['activation_request_id'] or
                authority.get('generation') != result.get('resulting_generation')):
            fail('binding_mismatch')
        if native_target is not None and native_target.get('machine_identity') != target['machine_identity']:
            fail('binding_mismatch')
        if result.get('ok') is True:
            if (native_target is None or application['plan_digest'] != detail['artifact']['plan_digest'] or
                    application['proof_digest'] != detail['artifact']['proof_digest'] or
                    operation['requested_outcome']['configuration_digest'] != detail['configuration']['digest']):
                fail('binding_mismatch')
        else:
            # Failed/refused owner transactions need not have produced a new
            # generation. The exact retained transaction proves the failure;
            # absent success dimensions still remain absent in role detail.
            for key in ('plan_digest', 'proof_digest'):
                expected = candidate.get(key)
                if expected is not None and application[key] != expected:
                    fail('binding_mismatch')
        budget.check()
        return operation

    def _supplement(self, detail, operation):
        """Copy only retained owner observations, keeping their receipt time."""
        detail = copy.deepcopy(detail)
        target = operation['target']; td = m.target_digest(target)
        def metadata(block, evidence):
            block.update(source_kind='delivery', observed_at=evidence.get('observed_at'), target_digest=td)
        authority = operation['authority'] or {}
        detail['target'].update(registered_host_digest=target['registered_host_digest'],
            environment=target['environment'], incarnation_id=target['instance_incarnation_id'])
        metadata(detail['target'], authority)
        if target['registered_host_digest'] is not None:
            _mark(detail['target'])
        runtime = operation['runtime']
        if runtime and runtime['state'] == 'known' and runtime['result'] in {'passed', 'failed'}:
            expected = operation['requested_outcome']['application']['commit']
            actual = runtime.get('application_revision')
            # An owner observation must retain the actual revision explicitly;
            # never fall back to requested_outcome.application.commit.
            if actual is not None and actual == expected:
                _mark(detail['source'], requested_revision=expected, observed_revision=actual)
                metadata(detail['source'], runtime)
                detail['runtime']['source_revision'] = actual
                metadata(detail['runtime'], runtime)
                if runtime.get('images') and set(runtime['images']) == set(detail['runtime']['image_digests']):
                    _mark(detail['runtime'], result=runtime['result'])
            if runtime.get('initializer_result') in {'passed', 'failed', 'not_applicable'}:
                _mark(detail['initializer'], status=runtime['initializer_result'],
                      result=runtime['initializer_result'])
                metadata(detail['initializer'], runtime)
        routes = operation['routes']
        route = (routes or {}).get('route_observation')
        if routes and routes['state'] == 'known' and route:
            detail['public_verification']['hosts'] = copy.deepcopy(route['hosts'])
            metadata(detail['public_verification'], routes)
            if route.get('result') == 'verified' and route.get('release_identity_state') == 'passed' and detail['public_verification']['edge_state'] == 'passed':
                _mark(detail['public_verification'])
        return role_detail(detail)

    def _native_exact(self, native, detail, projection, scope, candidate):
        """A complete native owner can bind without the optional journal."""
        run = projection['run']; artifact = projection.get('artifact')
        target = native.get('target'); result = native.get('result')
        if artifact is None or target is None or result is None:
            return False
        # A complete native port must independently bind the application root.
        # Matching a producer-supplied target digest and revision alone cannot
        # distinguish two projects deploying the same immutable artifact.
        if (scope.get('application_root_digest') is None or
                native.get('project_root_digest') != scope['application_root_digest']):
            return False
        parts = target.get('target_identity', '').split('/')
        if (len(parts) != 3 or parts[0] != scope['requested_target']['remote_name'] or
                parts[2] != run['release']['target'] or not parts[1] or
                candidate['target_digest'] != m.canonical_digest(target) or
                result.get('request_id') != run['activation_request_id'] or
                result.get('request_digest') != native.get('request_digest') or
                result.get('starting_generation') != run['generation']):
            return False
        source = detail['source']; proof = detail['artifact']; destination = detail['target']
        if (source['observed_revision'] != run['release']['revision'] or
                proof['receipt_digest'] != run['receipt_digest'] or
                proof['policy'] != 'retained_verified_artifact' or
                destination['registered_host_digest'] is None or
                destination['environment'] != run['release']['target'] or
                not proof['manifest_digests'] or
                set(proof['manifest_digests']) != set(artifact['manifest_digests']) or
                detail['generation']['digest'] != result.get('generation_digest') or
                detail['generation']['generation_id'] != result.get('resulting_generation') or
                detail['runtime']['source_revision'] != run['release']['revision'] or
                not detail['runtime']['image_digests'] or
                not set(detail['runtime']['image_digests']) <= set(proof['manifest_digests'])):
            return False
        return all(artifact[key] is not None and artifact[key] == proof[key]
                   for key in ('plan_digest', 'proof_digest')) and (
                       artifact['configuration_digest'] is not None and
                       artifact['configuration_digest'] == detail['configuration']['digest'])

    def _native_links(self, projection, scope, budget):
        run = projection['run']
        native = self._call(self.activation_reader, budget, run['activation_request_id'], scope)
        candidates = [c for c in projection['native_receipts'] if c['role'] != 'recovery_operation']
        activation_candidate = next((c for c in candidates if c['role'] == 'activation_operation'), None)
        for role in ('activation_operation', 'runtime', 'public_verification'):
            if not any(c['role'] == role for c in candidates):
                candidate = dict(activation_candidate) if activation_candidate else dict.fromkeys(_NATIVE_FIELDS)
                candidate.update(role=role, request_id=run['activation_request_id'])
                candidates.append(candidate)
        links = []; operation = None; terminal = False; native_joined = False
        for candidate in candidates:
            budget.check()
            link = _link(candidate['role'], candidate['request_id'], operation_id=candidate['operation_id'])
            # Staging has a distinct owner and ID. Activation evidence cannot
            # promote the producer's stage request into a staged proof receipt.
            if candidate['role'] == 'stage_request':
                links.append(link); continue
            if not native or native.get('request_id') != run['activation_request_id']:
                links.append(link); continue
            result = native.get('result')
            if result and result.get('request_id') != run['activation_request_id']:
                link.update(state='conflicting', reason=safe_reason('binding_mismatch'))
                links.append(link); continue
            detail = native.get('detail')
            if detail is not None:
                detail = role_detail(detail)
            if result is not None:
                terminal = result.get('result_class') in {'success', 'failed', 'refused', 'cancelled'}
            if detail is None:
                links.append(link); continue
            artifact = projection.get('artifact')
            if candidate['target_digest'] is not None and native.get('target') is not None and candidate['target_digest'] != m.canonical_digest(native['target']):
                link.update(state='conflicting', reason=safe_reason('binding_mismatch'))
                links.append(link); continue
            comparisons = [(candidate['plan_digest'], detail['artifact']['plan_digest']),
                (candidate['proof_digest'], detail['artifact']['proof_digest']),
                (candidate['configuration_digest'], detail['configuration']['digest'])]
            if artifact:
                comparisons.extend((artifact[key], detail['artifact'][key]) for key in ('plan_digest', 'proof_digest'))
                comparisons.append((artifact['configuration_digest'], detail['configuration']['digest']))
                if artifact['manifest_digests'] and detail['artifact']['manifest_digests'] and set(artifact['manifest_digests']) != set(detail['artifact']['manifest_digests']):
                    comparisons.append(('mismatch', 'different'))
            # Producer generation names the expected starting generation.
            if result and (result.get('starting_generation') != run['generation'] or
                    candidate['generation'] not in (None, run['generation'])):
                comparisons.append(('mismatch', 'different'))
            if any(expected is not None and actual is not None and expected != actual for expected, actual in comparisons):
                link.update(state='conflicting', reason=safe_reason('binding_mismatch'))
                links.append(link); continue
            if operation is None:
                operation = self._delivery(candidate, scope, budget, native)
            if operation is not None:
                detail = self._supplement(detail, operation)
                link['operation_id'] = operation['operation_id']
            role = candidate['role']
            names = {'activation_operation': ('source', 'artifact', 'target', 'generation', 'configuration', 'initializer'),
                     'runtime': ('source', 'target', 'generation', 'runtime'),
                     'public_verification': ('target', 'public_verification')}.get(role, ())
            # Receipt/selection provenance must be native, not copied from the producer.
            complete = bool(names) and all(detail[name]['state'] in {'known', 'complete', 'not_applicable'} for name in names)
            if role == 'activation_operation' and (not result or result.get('ok') is not True):
                complete = False
            failed = bool(result and result.get('ok') is False and result.get('result_class') not in {'uncertain'})
            joined = operation is not None or self._native_exact(native, detail, projection, scope, candidate)
            native_joined = native_joined or joined
            if failed and joined and role == 'activation_operation':
                complete = True
            if role == 'activation_operation':
                terminal = terminal and joined
            link.update(detail=detail, provenance='owner_verified' if joined else 'producer_recorded',
                state='complete' if complete and joined else 'partial',
                result='failed' if failed and joined else 'passed' if complete and joined else 'unknown',
                proof_digest=(native.get('generation_reference') or {}).get('generation_digest'),
                observed_at=detail['runtime']['observed_at'],
                reason=safe_reason('known' if complete and joined else 'required_evidence_missing'))
            links.append(link)
        return links, operation, terminal and native_joined

    def _observe(self, document, scope, budget):
        output = {'links': [], 'parent_stages': {'role_stages': None,
            'completeness': 'missing', 'reason': safe_reason('history_unavailable')},
            'recoveries': {'completeness': 'missing', 'omitted': 0, 'operations': []},
            'summary': _summary(), 'observation': {'joined_deployment_result': 'unknown',
                'completeness': 'partial', 'children_terminal': False}, 'omitted_links': 0}
        projection = document.get('owner_projection')
        parent = document.get('parent_link')
        parent_missing = False
        try:
            budget.check()
            if parent:
                read = self.repository.read_owner(scope, parent['producer_id'], parent['parent_request_id'], budget=budget)
                retained = read.get('record')
                if retained is not None:
                    if retained['intent_digest'] != parent['intent_digest']:
                        fail('binding_mismatch')
                    projection = retained['projection']
                    output['parent_stages'] = {'role_stages': copy.deepcopy(retained['role_stages']),
                        'completeness': 'partial' if retained['omitted_events'] else 'complete',
                        'reason': safe_reason('bounds' if retained['omitted_events'] else 'known')}
                else:
                    parent_missing = True
                    output['parent_stages'] = {'role_stages': None,
                        'completeness': read.get('coverage', {}).get('state', 'partial'),
                        'reason': safe_reason('history_unavailable')}
            if projection is None or projection['run'] is None:
                mode = document['requested']['mode']
                no_effect = not any(stage.get('effect_state') in {'entered', 'observed', 'failed', 'unknown'}
                    for name, stage in document.get('stages', {}).items() if name not in {'bootstrap', 'preflight'})
                if mode == 'preflight' or no_effect:
                    output['summary'] = _summary('not_started', 'complete', 'known')
                    output['observation'] = {'joined_deployment_result': 'not_started',
                        'completeness': 'complete', 'children_terminal': True}
                return output
            projection = decode_owner_projection(projection)
            if (projection['control']['root_digest'] != scope['project_root_digest'] or
                    projection['run']['release']['target'] != document['requested']['target']['environment']):
                fail('binding_mismatch')
            roots = [ref['digest'] for ref in document.get('candidate_links', [])
                     if ref['kind'] == 'application_source' and ref.get('digest') is not None]
            if len(set(roots)) > 1:
                fail('binding_mismatch')
            scope = {**scope, 'trace_document': document, 'projection': projection,
                'requested_target': document['requested']['target'],
                'application_root_digest': roots[0] if roots else None}
            job_terminals = []
            for candidate in projection['phase_jobs']:
                budget.check()
                row, terminal = self._job(candidate, scope, budget)
                output['links'].append(row); job_terminals.append(terminal)
            has_activation = any(row['role'] == 'activation_job' for row in projection['phase_jobs']) or bool(projection['native_receipts'])
            operation = None; native_terminal = not has_activation
            if has_activation:
                links, operation, native_terminal = self._native_links(projection, scope, budget)
                output['links'].extend(links)
            if operation is not None:
                reverse = self._call(self.recovery_reader, budget, operation['operation_id'],
                                     {**scope, 'delivery_target': operation['target']})
                if reverse is not None:
                    recoveries = reverse.get('recoveries', [])
                    if len(recoveries) > 10:
                        fail('bounds')
                    summaries = []
                    for recovered in recoveries:
                        budget.check()
                        checked = m.validate_operation(recovered)
                        if checked['execution_state'] in _TERMINAL and checked['terminal_snapshot_digest'] != m.terminal_digest(checked):
                            fail('binding_mismatch')
                        original_target = operation['target']
                        if (any(checked['target'][key] != original_target[key] for key in
                                ('project_identity', 'project_root_digest', 'remote_name', 'target_kind', 'environment', 'label')) or
                                not any(ref.get('kind') == 'original_delivery' and ref.get('identifier') == operation['operation_id']
                                        for ref in checked['recovery_relations'])):
                            fail('binding_mismatch')
                        summaries.append(m.operation_summary(checked))
                    output['recoveries'] = {'completeness': 'complete' if reverse.get('state') == 'known' else 'partial',
                        'omitted': reverse.get('omitted', 0), 'operations': summaries}
            links = output['links']
            children_terminal = bool(job_terminals) and all(job_terminals) and native_terminal
            conflicting = any(row['state'] == 'conflicting' for row in links)
            failed = any(row['provenance'] == 'owner_verified' and row['result'] == 'failed' for row in links)
            required_roles = {'prepare_job', 'activation_job', 'activation_operation', 'runtime', 'public_verification'}
            successful_roles = {row['role'] for row in links if row['provenance'] == 'owner_verified'
                                and row['state'] == 'complete' and row['result'] == 'passed'}
            if conflicting:
                summary = _summary('unknown', 'conflicting', 'binding_mismatch')
            elif failed:
                # A complete terminal failure is not a missing-success-proof loop.
                complete = children_terminal and not parent_missing
                summary = _summary('failed', 'complete' if complete else 'partial',
                                   'failed' if complete else 'required_evidence_missing')
            elif children_terminal and required_roles <= successful_roles and not parent_missing:
                summary = _summary('succeeded', 'complete', 'known')
            elif links and all(row['provenance'] == 'owner_verified' and row['state'] == 'complete'
                               for row in links) and any(row['result'] == 'pending' for row in links):
                summary = _summary('incomplete', 'complete', 'authority_pending')
            else:
                summary = _summary('incomplete' if links else 'unknown')
            output['summary'] = summary
            output['observation'] = {key: summary[key] for key in ('joined_deployment_result', 'completeness')}
            output['observation']['children_terminal'] = children_terminal and not conflicting
        except (TraceContractError, m.DeliveryError, ValueError, TypeError, KeyError, OSError) as exc:
            code = _code(exc)
            expected_count = (len(projection.get('phase_jobs', [])) + len(projection.get('native_receipts', []))) if isinstance(projection, dict) else 0
            output['omitted_links'] = max(0, expected_count - len(output['links']))
            output['summary'] = _summary('unknown', 'conflicting' if code == 'binding_mismatch' else 'partial', code)
            output['observation'] = {'joined_deployment_result': 'unknown',
                'completeness': output['summary']['completeness'], 'children_terminal': False}
        return output

    def inspect(self, project_dir, trace_id=None, trace_request_id=None, mutation_id=None):
        budget = TraceQueryBudget(time.monotonic() + 5)
        scope = None
        try:
            if (trace_id is None) == (trace_request_id is None) or mutation_id is not None and trace_id is None:
                fail()
            uuid_value(trace_id or trace_request_id)
            if mutation_id is not None:
                uuid_value(mutation_id)
            scope = self._scope(project_dir)
            budget.check()
            result = _query(scope, trace_id, trace_request_id, self._now())
            retained = self.repository.read(scope, trace_id=trace_id, trace_request_id=trace_request_id,
                                            mutation_id=mutation_id, budget=budget)
            result['coverage'].update(trace_state=retained['state'], reason=retained['reason'])
            result['mutation_receipt'] = retained['mutation_receipt']
            document = retained['trace']
            if document is None:
                result['owner_evidence']['summary'] = _summary('unknown', retained['state'], retained['reason']['code'])
                return json.loads(serialize_trace_query(result))
            detail = copy.deepcopy(document)
            detail['detail_coverage'] = {'retained_document_digest': m.canonical_digest(document),
                'projection': 'included' if document['owner_projection'] is not None else 'not_recorded',
                'retained_events': len(document['events']), 'returned_events': len(document['events']),
                'omitted_events': document['omitted_events']}
            result['trace'] = detail
            result['query_scope'].update(trace_id=document['trace_id'], trace_request_id=document['trace_request_id'])
            observation = self._observe(document, scope, budget)
            result['owner_evidence'] = {'summary': observation['summary'], 'links': observation['links'],
                                       'parent_stages': observation['parent_stages']}
            result['recovery_operations'] = observation['recoveries']
            result['coverage'].update(owner_state=observation['summary']['completeness'],
                early_history='unavailable' if document['requested']['mode'] == 'legacy_projection' else 'recorded',
                omitted_events=document['omitted_events'], omitted_links=observation['omitted_links'],
                reason=observation['summary']['reason'])
            summary = observation['summary']
            if summary['completeness'] != 'complete' or summary['reason']['code'] == 'authority_pending':
                argv = ['sb', 'delivery', 'inspect', '--project-dir', '.', '--trace-id', document['trace_id'], '--json']
                result['next_action'] = ('From the original project directory, use the same absolute Sandbox executable, '
                    'controller and home as this invocation: ' + shlex.join(argv))
            # Deadline checks do not discard already retained evidence. The last
            # bounded serialization may finish after the in-flight decode budget.
            if budget.expired:
                result['owner_evidence']['summary'] = _summary('unknown', 'partial', 'budget_exhausted')
                result['coverage'].update(owner_state='partial', reason=safe_reason('budget_exhausted'))
            return json.loads(serialize_trace_query(result))
        except (TraceContractError, m.DeliveryError, ValueError, TypeError, KeyError, OSError) as exc:
            code = _code(exc)
            # Invalid selectors are not echoed as apparently valid trace IDs.
            def selector(value):
                try:
                    return uuid_value(value) if value is not None else None
                except ValueError:
                    return None
            return _query_error(code, scope=scope, trace_id=selector(trace_id),
                                trace_request_id=selector(trace_request_id))


def trace_with_factory(factory, action, **values):
    """CLI/MCP share a discriminated grammar before constructing owner services."""
    try:
        actions = {'trace-capabilities', 'trace-start', 'trace-record', 'trace-owner-status', 'trace-owner-record', 'inspect'}
        if action not in actions:
            fail()
        incompatible = ('remote', 'environment', 'label', 'operation_id', 'request_id', 'limit', 'cursor')
        if any(values.get(key) is not None for key in incompatible) or values.get('observe') not in (None, False):
            fail()
        allowed = {
            'trace-capabilities': set(),
            'trace-start': {'project_dir', 'trace_request_id', 'input_json'},
            'trace-record': {'project_dir', 'trace_id', 'mutation_id', 'expected_sequence', 'input_json'},
            'trace-owner-status': {'project_dir', 'producer', 'parent_request_id', 'publication_id'},
            'trace-owner-record': {'project_dir', 'producer', 'parent_request_id', 'publication_id', 'expected_sequence', 'input_json'},
            'inspect': {'project_dir', 'trace_id', 'trace_request_id', 'mutation_id'},
        }[action]
        if any(value is not None and key not in allowed | set(incompatible) | {'observe'} for key, value in values.items()):
            fail()
        if factory is None:
            fail('trace_unavailable')
        service = factory()
        if action == 'trace-capabilities':
            return service.capabilities()
        project_dir = values.get('project_dir')
        if not isinstance(project_dir, str) or not project_dir or '\x00' in project_dir:
            fail()
        if action == 'inspect':
            return service.inspect(project_dir, trace_id=values.get('trace_id'),
                trace_request_id=values.get('trace_request_id'), mutation_id=values.get('mutation_id'))
        if action == 'trace-start':
            return service.start(project_dir, values.get('trace_request_id'), values.get('input_json'))
        if action == 'trace-record':
            return service.record(project_dir, values.get('trace_id'), values.get('mutation_id'),
                                  values.get('expected_sequence'), values.get('input_json'))
        if action == 'trace-owner-status':
            return service.owner_status(project_dir, values.get('producer'), values.get('parent_request_id'), values.get('publication_id'))
        return service.owner_record(project_dir, values.get('producer'), values.get('parent_request_id'),
                                    values.get('publication_id'), values.get('expected_sequence'), values.get('input_json'))
    except (TraceContractError, m.DeliveryError, ValueError, TypeError, KeyError, OSError) as exc:
        code = _code(exc)
        def valid(value, validator):
            try:
                return validator(value) if value is not None else None
            except (ValueError, TypeError, KeyError):
                return None
        safe_parent = valid(values.get('parent_request_id'), m.identifier)
        safe_producer = 'lenzora-hosted-v1' if values.get('producer') == 'lenzora-hosted-v1' else None
        safe_publication = valid(values.get('publication_id'), uuid_value)
        if action == 'inspect':
            return _query_error(code)
        if action == 'trace-owner-status':
            return {'schema_version': 1, 'ok': False, 'error': safe_reason(code),
                'producer_id': safe_producer, 'parent_request_id': safe_parent,
                'record': None, 'publication_receipt': None,
                'coverage': {'state': 'partial', 'reason': safe_reason(code)}}
        if action == 'trace-owner-record':
            return {'schema_version': 1, 'ok': False, 'code': code,
                'producer_id': safe_producer, 'parent_request_id': safe_parent,
                'publication_id': safe_publication, 'sequence': None,
                'document_digest': None, 'reason': safe_reason(code)}
        if action == 'trace-capabilities':
            return {'schema_version': 1, 'ok': False, 'capability': 'deployment_trace_v1',
                    'capability_version': 1, 'runtime_revision': None, 'producers': [],
                    'limits': {}, 'reason': safe_reason(code)}
        return {'schema_version': 1, 'ok': False, 'code': code, 'trace_id': None,
                'trace_request_id': valid(values.get('trace_request_id'), uuid_value), 'sequence': None,
                'document_digest': None, 'reason': safe_reason(code)}


def format_trace_projection(result):
    if result.get('query_kind') != 'deployment_trace':
        return json.dumps(result, sort_keys=True, indent=2)
    document = result.get('trace')
    if not result['ok'] or document is None:
        reason = result.get('error') or result['coverage']['reason']
        return 'Trace unavailable: ' + reason['message']
    summary = result['owner_evidence']['summary']
    proved = [row['role'] for row in result['owner_evidence']['links']
              if row['provenance'] == 'owner_verified' and row['result'] == 'passed']
    order = ('prepare_job', 'activation_job', 'activation_operation', 'runtime', 'public_verification')
    proved = [role for role in order if role in proved]
    def show(value):
        return 'unavailable' if value is None else str(value)
    lines = [f"Command: {document['command_result']}; joined deployment: {summary['joined_deployment_result']}; furthest proved stage: {proved[-1] if proved else 'unavailable'}.",
        f"Original trace: {document['trace_id']} (request {document['trace_request_id']}).",
        f"Original retained deployment result: {document['deployment_result']}.",
        f"Project: {document['project_identity']}; environment: {document['requested']['target']['environment']}; remote: {show(document['requested']['target']['remote_name'])}.",
        f"Control revision: {show(document['producer_context']['source_commit'])}; application revision: {show(document['requested']['source']['revision'])}.",
        f"Created: {document['created_at']}; finished: {show(document['finished_at'])}; proof: {summary['completeness']}."]
    projection = document['owner_projection']
    if projection and projection['run']:
        run = projection['run']
        lines.append(f"Producer-recorded release: {run['release']['revision']}; receipt: {run['receipt_digest']}; starting generation: {run['generation']}.")
    for name, stage in document['stages'].items():
        if stage['status'] in {'failed', 'unknown', 'running'}:
            lines.append(f"Original {name}: {stage['status']} ({stage['provenance']}); {stage['reason']['message']}.")
    if document['parent_link']:
        lines.append('Parent request: ' + document['parent_link']['parent_request_id'] + '.')
    for row in result['owner_evidence']['links']:
        lines.append(f"{row['role']}: {row['result']} ({row['state']}, {row['provenance']}); request {show(row['request_id'])}; job {show(row['job_id'])}; operation {show(row['operation_id'])}.")
        detail = row['detail']
        if detail:
            if row['source_role'] == 'control':
                job = detail['job']
                lines.append(f"  Started: {show(job['started_at'])}; finished: {show(job['finished_at'])}; exit: {show(job['exit_code'])}; output complete: {show(job['output_complete'])}.")
            lines.append(f"  Owner source lineage: {show(detail['source']['observed_revision'])}; control revision: {show(detail['source']['control_revision'])}.")
            lines.append(f"  Runtime revision: {show(detail['runtime']['source_revision'])}; generation: {show(detail['generation']['generation_id'])}; initializer: {show(detail['initializer']['status'])}; health: {show(detail['runtime']['health'])}.")
            lines.append('  Runtime images: ' + (', '.join(detail['runtime']['image_digests'] or []) or 'unavailable') + '.')
            lines.append(f"  Plan: {show(detail['artifact']['plan_digest'])}; proof: {show(detail['artifact']['proof_digest'])}; config: {show(detail['configuration']['digest'])}; edge: {show(detail['public_verification']['edge_state'])}.")
            hosts = detail['public_verification']['hosts'] or []
            lines.append('  Route observations: ' + str(len(hosts)) + '.')
            for host in hosts:
                release = host['release_identity']
                lines.append(f"    {host['hostname']}: DNS {host['dns']['result']}; release {release.get('result', release.get('state', 'unavailable'))}.")
                for check in host['checks']:
                    lines.append(f"      {check['path']}: {check['result']} at {check['finished_at']}.")
    parent_stages = result['owner_evidence']['parent_stages']
    lines.append('Parent stage coverage: ' + parent_stages['completeness'] + '.')
    for role, stages in (parent_stages['role_stages'] or {}).items():
        for name, stage in stages.items():
            lines.append(f"Parent {role} / {name}: {stage['status']} ({stage['effect_state']}, {stage['provenance']}); first {show(stage['first_at'])}; last {show(stage['last_at'])}.")
    for event in document['events']:
        lines.append(f"{event['at']}: {event['stage']} {event['status']} ({event['provenance']}).")
    for recovered in result['recovery_operations']['operations']:
        lines.append(f"Separate recovery: {recovered['operation_id']} — {recovered['delivery_state']} (original deployment metadata).")
    coverage = result['coverage']
    lines.append(f"Early history: {coverage['early_history']}; omitted events: {coverage['omitted_events']}; omitted links: {coverage['omitted_links']}; omitted recoveries: {result['recovery_operations']['omitted']}; producer projection: {document['detail_coverage']['projection']}.")
    lines.append('Coverage: ' + result['coverage']['reason']['message'] + '.')
    if result['next_action']:
        lines.append(result['next_action'])
    return '\n'.join(lines)
