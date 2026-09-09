"""Bounded diagnostic writer; hosting owners retain all effect authority."""
from __future__ import annotations

import uuid
from sandbox.core._paths import RUNTIME_DIR
from .context import control_metadata, target_for_project, require_delivery_capabilities
from .models import (DeliveryError, canonical_digest, new_operation, now,
                     request_scope, operation_summary, TERMINAL, append_event)
from .repository import DeliveryRepository
from .service import evaluate_operation, normalize_recovery_projection


def _observed_event(operation, value):
    previous = operation['events'][-1] if operation['events'] else None
    if previous is not None and all(previous.get(key) == value.get(key)
                                    for key in ('phase', 'reason', 'references')):
        return operation
    return append_event(operation, value)


def capture_recovery_result(validated, remote_name, original_request_id, result):
    """Keep recovery results separate; they cannot finish the original workload."""
    import copy
    target = target_for_project(validated.get('manifest_root') or validated['project_root'], remote_name,
                                environment=validated['environment'])
    repository = DeliveryRepository(RUNTIME_DIR.parent)
    scope = request_scope(target)
    original = repository.lookup_request(scope, original_request_id)['operation']
    if original is None:
        raise DeliveryError('required_evidence_missing')
    if result['request_id'] == original_request_id:
        raise DeliveryError('request_conflict')
    outcome = copy.deepcopy(original['requested_outcome'])
    outcome['requested_at'] = now()
    operation = new_operation(original['target'], outcome, str(uuid.uuid4()),
                              result['request_id'], None)
    relation = {'kind': 'original_delivery', 'identifier': original['operation_id']}
    proof = {'kind': 'recovery_result', 'digest': canonical_digest(result)}
    operation['recovery_relations'] = [relation, proof]
    preflight = repository.preflight_request(scope, result['request_id'],
        operation['operation_id'], operation['intent_digest'])
    if preflight['status'] != 'available':
        previous = preflight['operation']
        if (preflight['status'] != 'existing' or previous is None
                or previous['recovery_relations'] != operation['recovery_relations']):
            raise DeliveryError('request_conflict')
        return operation_summary(previous)
    attempt = HostingAttempt.retained(operation)
    attempt.reserved = False
    attempt.reserve()
    attempt.block('authority', result.get('ok') is True,
                  proof_digest=proof['digest'], known=True)
    return attempt.finish(result.get('ok') is True,
                          uncertain=result.get('result_class') in {'uncertain', 'ambiguous'})


class RetainedDelivery(DeliveryError):
    def __init__(self, status, operation_id, operation=None):
        self.status, self.operation_id, self.operation = status, operation_id, operation
        super().__init__('request_conflict' if status == 'request_conflict' else
                         'delivery_request_expired' if status == 'delivery_request_expired' else 'authority_pending')


class HostingAttempt:
    def __init__(self, validated, remote_name, entry, *, kind, request_id, job_id,
                 application, configuration_digest, machine_identity,
                 registered_host_digest, requirements):
        require_delivery_capabilities()
        self.repository = DeliveryRepository(RUNTIME_DIR.parent)
        target = target_for_project(validated.get('manifest_root') or validated['project_root'], remote_name,
                                    environment=validated['environment'])
        target.update(machine_identity=machine_identity, registered_host_digest=registered_host_digest)
        outcome = {'kind': kind, 'target_digest': canonical_digest(target),
                   'application': application, 'control': control_metadata(entry),
                   'configuration_digest': configuration_digest,
                   'route_contract_digest': None, 'requirements': requirements,
                   'requested_at': now()}
        self.operation = new_operation(target, outcome, str(uuid.uuid4()), request_id, job_id)
        self.scope = request_scope(target)
        preflight = self.repository.preflight_request(self.scope, request_id,
            self.operation['operation_id'], self.operation['intent_digest'])
        if preflight['status'] != 'available':
            raise RetainedDelivery(preflight['status'], preflight['operation_id'], preflight['operation'])
        self.reserved = False

    def reserve(self, admission=None):
        if admission is not None:
            self.operation['admission'] = normalize_recovery_projection(
                admission, self.operation['target'], self.operation)
            if self.operation['admission']['state'] != 'known' or self.operation['admission']['result'] != 'passed':
                raise DeliveryError('binding_mismatch')
        self.operation.update(execution_state='running', started_at=now(), phase='admitted')
        self.operation = _observed_event(self.operation, {
            'at': self.operation['started_at'], 'phase': 'admitted',
            'reason': {'code': 'none', 'message': 'The original owner admitted this attempt.'}})
        result = self.repository.reserve_request(self.scope, self.operation['request_id'],
            self.operation['operation_id'], self.operation['intent_digest'], operation=self.operation)
        if result['status'] != 'reserved':
            raise RetainedDelivery(result['status'], result['operation_id'], result['operation'])
        self.operation, self.reserved = result['operation'], True

    def checkpoint(self, phase, effect=None, *, state='unknown'):
        previous_phase = self.operation['phase']
        previous_effect = next((item['state'] for item in self.operation['effects']
                                if item['name'] == effect), None)
        self.operation.update(phase=phase, updated_at=now())
        if effect is not None:
            value = {'name': effect, 'scope_digest': self.operation['requested_outcome']['target_digest'],
                     'state': state, 'observed_at': now()}
            for index, old in enumerate(self.operation['effects']):
                if old['name'] == effect:
                    self.operation['effects'][index] = value
                    break
            else:
                self.operation['effects'].append(value)
        if previous_phase != phase or (effect is not None and previous_effect != state):
            self.operation = _observed_event(self.operation, {
                'at': self.operation['updated_at'], 'phase': phase,
                'reason': {'code': 'effect_unknown' if state == 'unknown' and effect else 'failed' if state == 'failed' else 'none',
                           'message': 'Observed hosting phase' +
                               (': ' + effect + ' is ' + state if effect else '') + '.'}})
        self.operation = self.repository.write_operation(self.operation)

    def block(self, kind, passed, *, generation=None, initializer=None, proof_digest=None,
              images=None, known=True, application_revision=None, contract_digest=None):
        value = {'source_kind': kind, 'observed_at': now(),
                 'target_digest': self.operation['requested_outcome']['target_digest'],
                 'applicability': 'required', 'state': 'known' if known else 'partial',
                 'result': ('passed' if passed else 'failed') if known else 'unknown',
                 'reason': {'code': ('none' if passed else 'failed') if known else 'partial',
                            'message': 'Authoritative hosting result.'},
                 'request_id': self.operation['request_id'], 'job_id': self.operation['job_id'],
                 'operation_id': self.operation['operation_id'], 'generation': generation,
                 'application_revision': application_revision,
                 'contract_digest': contract_digest}
        if initializer is not None:
            value['initializer_result'] = initializer
        if proof_digest is not None:
            value['proof_digest'] = proof_digest
        if images is not None:
            value['images'] = images
        self.operation[kind] = value

    def finish(self, succeeded, *, uncertain=False, failure_stage=None):
        import copy
        candidate = copy.deepcopy(self.operation)
        if candidate['execution_state'] not in TERMINAL:
            candidate = _observed_event(candidate, {
                'at': now(), 'phase': 'unknown' if uncertain else 'terminal',
                'reason': {'code': 'effect_unknown' if uncertain else 'none' if succeeded else 'failed',
                           'message': 'Original hosting effects remain uncertain.' if uncertain else
                               'The original hosting owner reported success.' if succeeded else
                               'The original hosting owner reported failure.'}})
        candidate.update(execution_state='unknown' if uncertain else 'succeeded' if succeeded else 'failed',
            finished_at=None if uncertain else now(), updated_at=now(),
            phase='unknown' if uncertain else 'terminal', failure_stage=failure_stage)
        if uncertain:
            candidate['pinned_reason'] = {'code': 'effect_unknown', 'message': 'Original effects remain uncertain.'}
        candidate.update(evaluate_operation(candidate))
        self.operation = self.repository.write_operation(candidate)
        return operation_summary(self.operation)

    def require_previous_snapshot(self, previous):
        """Never overwrite an original authority whose terminal snapshot is missing."""
        if not previous:
            return
        request = previous.get('request_id')
        if not request or request == self.operation['request_id']:
            raise DeliveryError('authority_pending')
        result = self.repository.lookup_request(self.scope, request)
        old = result['operation']
        if old is None or old['execution_state'] not in TERMINAL or old['terminal_snapshot_digest'] is None:
            raise DeliveryError('required_evidence_missing')
        admission = old.get('admission') or {}
        if (old.get('job_id') != previous.get('job_id')
                or admission.get('generation') != previous.get('starting_generation')
                or admission.get('application_revision') != (previous.get('source') or {}).get('commit')
                or admission.get('contract_digest') != (previous.get('evidence') or {}).get('config_digest')
                or old['requested_outcome']['application'].get('source_artifact') != (previous.get('source') or {}).get('artifact')):
            raise DeliveryError('binding_mismatch')

    def require_generation_snapshot(self, generation, results):
        """Require the exact predecessor owner's retained terminal diagnostic."""
        if generation is None:
            return
        matches = [request_id for request_id, row in results.items()
                   if (row.get('result') or {}).get('request_digest') == generation.get('request_digest')]
        if len(matches) != 1:
            raise DeliveryError('required_evidence_missing')
        old = self.repository.lookup_request(self.scope, matches[0])['operation']
        if old is None or old['execution_state'] not in TERMINAL or not old['terminal_snapshot_digest']:
            raise DeliveryError('required_evidence_missing')
        authority = old.get('authority') or {}
        if (authority.get('generation') != generation.get('generation')
                or authority.get('proof_digest') != (results[matches[0]].get('result') or {}).get('transaction_digest')
                or old['requested_outcome']['configuration_digest'] != generation.get('configuration_digest')):
            raise DeliveryError('binding_mismatch')

    @classmethod
    def retained(cls, operation):
        attempt = cls.__new__(cls)
        attempt.repository = DeliveryRepository(RUNTIME_DIR.parent)
        attempt.operation = operation
        attempt.scope = request_scope(operation['target'])
        attempt.reserved = True
        return attempt

    def finish_activation(self, result, generation):
        """Observe a validated owner result without turning requested facts into proof."""
        app = self.operation['requested_outcome']['application']
        exact = bool(result['ok'] and generation
                     and generation.get('request_digest') == result['request_digest']
                     and generation.get('generation') == result['resulting_generation'])
        actual_plan = ((generation or {}).get('plan_set_digest')
                       or (generation or {}).get('plan_digest'))
        source = app['commit'] if exact and actual_plan == app['plan_digest'] else None
        configuration = (generation or {}).get('configuration_digest') if exact else None
        image = (generation or {}).get('image') or {}
        images = [image['manifest_digest']] if isinstance(image, dict) and image.get('manifest_digest') else None
        image_complete = True
        if (generation or {}).get('schema_version') == 2:
            from sandbox.hosting.images.activation.v2_models import VerifiedActivationGenerationV2
            checked = VerifiedActivationGenerationV2.from_mapping(generation)
            image_complete = len(checked.images) <= 32
            images = ([item['image_ref'].rsplit('@', 1)[-1] for item in checked.images]
                      if image_complete else None)
        for kind in ('authority', 'runtime', 'edge'):
            self.block(kind, result['ok'], generation=result['resulting_generation'],
                       initializer='passed' if exact and kind == 'runtime' else None,
                       proof_digest=result['transaction_digest'], images=images if kind == 'runtime' else None,
                       known=(result['result_class'] != 'uncertain' if kind == 'authority'
                              else exact and (source is not None and image_complete if kind == 'runtime' else True)),
                       application_revision=source, contract_digest=configuration)
        return self.finish(result['ok'], uncertain=result['result_class'] == 'uncertain',
                           failure_stage=None if result['ok'] else 'runtime')
