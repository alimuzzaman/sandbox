"""Diagnostic writer for one frozen deploy/preview exposure attempt."""
from __future__ import annotations

import uuid
import subprocess
from sandbox.core._paths import RUNTIME_DIR
from .context import control_metadata, target_for_project
from .models import (DeliveryError, canonical_digest, new_operation, now,
                     request_scope, target_digest, operation_summary)
from .repository import DeliveryRepository
from .service import evaluate_operation, normalize_creation_receipt


class ExposureReplay(DeliveryError):
    def __init__(self, result):
        self.status = result['status']
        self.operation_id = result['operation_id']
        self.operation = result['operation']
        super().__init__('request_conflict' if self.status == 'request_conflict' else
                         'delivery_request_expired' if self.status == 'delivery_request_expired' else 'authority_pending')

    def response(self):
        same = self.status == 'existing'
        return {'ok': bool(same and self.operation and self.operation['delivery_succeeded']),
                'code': self.status, 'operation_id': self.operation_id,
                'delivery': operation_summary(self.operation) if self.operation else None,
                'lookup_only': True, 'effects_started': False}


class ExposureAttempt:
    def __init__(self, project, remote_name, entry, *, label, kind, commit,
                 dirty_digest, prepared, request_id=None):
        from .context import require_delivery_capabilities
        required = ['delivery_outcomes_v1', 'instance_creation_receipt_v1']
        if prepared is not None:
            required.append('delivery_route_verification_v1')
        require_delivery_capabilities(required)
        self.repository = DeliveryRepository(RUNTIME_DIR.parent)
        target = target_for_project(project['root'], remote_name, label=label,
                                    target_kind='preview' if kind == 'preview_creation' else 'deploy')
        from sandbox.resources.context import authenticated_target_identity
        target['machine_identity'] = authenticated_target_identity(remote_name)['target_identity']
        from .context import registered_host_digest
        target['registered_host_digest'] = registered_host_digest(entry, remote_name)
        config_digest = canonical_digest({'kind': project.get('kind', 'wordpress'),
                                         'label': label, 'source': commit,
                                         'overlay': dirty_digest,
                                         'routes': prepared})
        requirements = [{'kind': 'runtime_health', 'applicability': 'required', 'source': 'creation_receipt'}]
        if prepared is not None:
            requirements.append({'kind': 'exposure', 'applicability': 'required', 'source': 'public_routes'})
            if prepared['delivery']['routes']['releaseIdentity']['required']:
                requirements.append({'kind': 'public_release_identity', 'applicability': 'required', 'source': 'public_routes'})
        outcome = {
            'kind': kind, 'target_digest': target_digest(target),
            'application': {'source_identity': target['project_root_digest'], 'commit': commit,
                            'dirty_digest': dirty_digest, 'artifact_digest': None,
                            'config_digest': config_digest, 'plan_digest': None, 'proof_digest': None,
                            'dirty_policy': 'overlay_declared'},
            'control': control_metadata(entry), 'configuration_digest': config_digest,
            'route_contract_digest': prepared['contract_digest'] if prepared else None,
            'requirements': requirements, 'requested_at': now()}
        self.operation = new_operation(target, outcome, str(uuid.uuid4()), request_id=request_id)
        self.prepared = prepared
        if prepared is not None:
            self.operation['routes'] = {
                'source_kind': 'routes', 'observed_at': now(), 'target_digest': outcome['target_digest'],
                'applicability': 'required', 'state': 'partial', 'result': 'pending',
                'reason': {'code': 'authority_pending', 'message': 'Requested routes have not been observed.'},
                'contract_digest': prepared['contract_digest'], 'route_contract': prepared,
                'operation_id': self.operation['operation_id'], 'request_id': request_id, 'job_id': None}
        scope = request_scope(target)
        prior = self.repository.preflight_request(scope, request_id,
            self.operation['operation_id'], self.operation['intent_digest'])
        if prior['status'] != 'available':
            raise ExposureReplay(prior)
        if self.repository.has_open_operation(scope):
            raise DeliveryError('authority_pending')
        reserved = self.repository.reserve_request(scope, request_id,
            self.operation['operation_id'], self.operation['intent_digest'], operation=self.operation)
        if reserved['status'] != 'reserved':
            raise ExposureReplay(reserved)
        self.operation = reserved['operation']
        self.context = None
        self.receipt = None

    def save(self, *, phase=None):
        if phase is not None:
            self.operation['phase'] = phase
        self.operation['updated_at'] = now()
        self.repository.write_operation(self.operation)

    def effect(self, name, scope, state='configured'):
        value = {'name': name, 'scope_digest': canonical_digest(scope),
                 'state': state, 'observed_at': now()}
        effects = self.operation['effects']
        for index, previous in enumerate(effects):
            if previous['name'] == name and previous['scope_digest'] == value['scope_digest']:
                effects[index] = value
                break
        else:
            effects.append(value)
        self.save()

    def prepare_creation(self, context):
        self.context = context
        self.operation['creation'] = {
            'source_kind': 'creation', 'observed_at': now(),
            'target_digest': self.operation['requested_outcome']['target_digest'],
            'applicability': 'required', 'state': 'partial', 'result': 'pending',
            'reason': {'code': 'authority_pending', 'message': 'Original creation receipt is pending.'},
            'creation_context': context, 'operation_id': self.operation['operation_id'],
            'request_id': self.operation['request_id'], 'job_id': self.operation['job_id']}
        self.save(phase='instance')

    def bind_creation(self, context, receipt):
        self.context = context
        self.receipt = receipt
        self.operation['creation'] = normalize_creation_receipt(
            receipt, self.operation['target'], self.operation, creation_context=context)
        self.save(phase='instance')
        block = self.operation['creation']
        if block['state'] != 'known' or block['result'] != 'passed':
            raise DeliveryError('required_evidence_missing')

    def evidence(self, kind, *, result='passed', state='known', route_observation=None):
        stamp = now()
        value = {'source_kind': kind, 'observed_at': stamp,
                 'target_digest': self.operation['requested_outcome']['target_digest'],
                 'applicability': 'required', 'state': state, 'result': result,
                 'reason': {'code': 'none' if result == 'passed' else 'failed',
                            'message': 'Observed delivery result.'},
                 'operation_id': self.operation['operation_id'],
                 'request_id': self.operation['request_id'], 'job_id': None,
                 'instance_incarnation_id': (self.receipt or {}).get('instance_incarnation_id'),
                 'application_revision': None,
                 'contract_digest': (self.operation['requested_outcome']['route_contract_digest']
                                     if kind == 'routes' else None)}
        if route_observation is not None:
            value['route_observation'] = route_observation
            value['route_contract'] = self.prepared
            release = self.prepared['delivery']['routes']['releaseIdentity']
            if (release.get('expectedFrom') == 'application_commit' and
                    route_observation.get('release_identity_state') == 'passed'):
                value['application_revision'] = self.prepared['release_expected']
        self.operation[kind] = value
        self.save(phase='route' if kind == 'routes' else 'runtime')

    def finish(self, succeeded, *, phase=None):
        import copy
        candidate = copy.deepcopy(self.operation)
        uncertain = not succeeded and (
            any(item['state'] == 'unknown' for item in candidate['effects'])
            or (candidate.get('creation') or {}).get('result') == 'pending')
        candidate.update(execution_state='unknown' if uncertain else 'succeeded' if succeeded else 'failed',
                         finished_at=None if uncertain else now(), updated_at=now(),
                         phase='unknown' if uncertain else 'terminal', failure_stage=phase if not succeeded else None)
        if uncertain:
            candidate['pinned_reason'] = {'code': 'effect_unknown', 'message': 'Original effects remain uncertain.'}
        candidate.update(evaluate_operation(candidate))
        self.operation = self.repository.write_operation(candidate)
        return operation_summary(self.operation)

    def ensure(self, entry, target, *, label, transport):
        attempt, sr = self, transport
        prepared = sr.prepare_creation_context(entry, target, label,
            operation_id=attempt.operation['operation_id'], request_id=attempt.operation['request_id'],
            delivery_intent_digest=attempt.operation['intent_digest'],
            target_scope_digest=request_scope(attempt.operation['target']), create_allowed=True)
        if prepared.get('ok') is not True:
            raise ValueError('creation_context_unavailable')
        context = prepared['creation_context']
        attempt.prepare_creation(context)
        try:
            instance = sr.ensure_remote_instance(entry, target, label, creation_context=context)
        except (RuntimeError, ValueError, subprocess.SubprocessError, OSError):
            # Read the original request after an uncertain acknowledgment. Never ensure again.
            receipt_result = sr.read_remote_creation_receipt(entry, target, label, creation_context=context)
            if receipt_result.get('ok'):
                attempt.bind_creation(context, receipt_result['creation_receipt'])
            raise
        receipt_result = sr.read_remote_creation_receipt(entry, target, label, creation_context=context)
        if receipt_result.get('ok') is not True:
            raise ValueError('creation_receipt_unavailable')
        attempt.bind_creation(context, receipt_result['creation_receipt'])
        if instance.get('instance') != attempt.receipt['instance_id']:
            raise ValueError('instance_incarnation_changed')
        return instance
