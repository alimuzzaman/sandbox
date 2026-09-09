"""Regressions from isolated delivery diagnosis CLI/MCP acceptance fixtures."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import uuid

from sandbox.delivery.context import target_for_project
from sandbox.delivery.models import DeliveryError, canonical_digest, new_operation, now, request_scope
from sandbox.delivery.repository import DeliveryRepository
from sandbox.delivery.service import (
    DeliveryService, evaluate_operation, normalize_creation_receipt,
    normalize_job_projection, normalize_recovery_projection,
)
from sandbox.server_config.models import creation_digest, validate_creation_context, validate_creation_receipt


SOURCE_COMMIT = 'a' * 40
DEPLOYED_COMMIT = 'b' * 40
CONFIG_DIGEST = 'sha256:' + 'c' * 64


def hosted_source_target():
    root = '/tmp/fixture-hosted-project'
    return {
        'schema_version': 1, 'project_identity': 'fixture-project',
        'project_root_digest': 'sha256:' + hashlib.sha256(root.encode()).hexdigest(),
        'remote_name': 'fixture-remote',
        'registered_host_digest': 'sha256:' + '2' * 64,
        'machine_identity': 'fixture-machine', 'target_kind': 'hosted',
        'environment': 'qa', 'label': None, 'instance_id': None,
        'instance_incarnation_id': None, 'workspace_id': None,
        'runtime_identity': 'fixture-runtime',
    }


def hosted_source_operation(*, runtime_revision=DEPLOYED_COMMIT,
                            edge_revision=DEPLOYED_COMMIT,
                            runtime_config=CONFIG_DIGEST,
                            edge_config=CONFIG_DIGEST):
    target = hosted_source_target()
    artifact = {
        'schema_version': 1, 'kind': 'git_subtree', 'root_relative': 'site',
        'revision': DEPLOYED_COMMIT,
    }
    stamp = now()
    outcome = {
        'kind': 'hosted_apply', 'target_digest': canonical_digest(target),
        'application': {
            'source_identity': 'fixture-app', 'commit': SOURCE_COMMIT,
            'source_artifact': artifact, 'dirty_digest': None,
            'artifact_digest': None, 'config_digest': CONFIG_DIGEST,
            'plan_digest': None, 'proof_digest': None,
            'dirty_policy': 'clean_required',
        },
        'control': {
            'source_commit': 'd' * 40, 'source_runtime_revision': 'e' * 24,
            'installed_controller_runtime_revision': None,
            'capability_versions': {'delivery_outcomes_v1': 1},
        },
        'configuration_digest': CONFIG_DIGEST, 'route_contract_digest': None,
        'requirements': [
            {'kind': 'runtime_identity', 'applicability': 'required', 'source': 'fixture_owner'},
            {'kind': 'edge_proof', 'applicability': 'required', 'source': 'fixture_owner'},
        ],
        'requested_at': stamp,
    }
    operation = new_operation(target, outcome, str(uuid.uuid4()), 'request-hosted', 'job-hosted')
    common = {
        'target_digest': canonical_digest(target), 'applicability': 'required',
        'state': 'known', 'result': 'passed',
        'reason': {'code': 'none', 'message': 'Synthetic owner evidence.'},
        'operation_id': operation['operation_id'], 'request_id': operation['request_id'],
        'job_id': operation['job_id'],
    }
    operation['admission'] = {'source_kind': 'admission', 'observed_at': stamp,
        **common, 'application_revision': SOURCE_COMMIT, 'contract_digest': CONFIG_DIGEST}
    operation['runtime'] = {'source_kind': 'runtime', 'observed_at': stamp,
        **common, 'application_revision': runtime_revision, 'contract_digest': runtime_config}
    operation['edge'] = {'source_kind': 'edge', 'observed_at': stamp,
        **common, 'application_revision': edge_revision, 'contract_digest': edge_config}
    operation.update(execution_state='succeeded', started_at=stamp, finished_at=stamp,
                     updated_at=stamp, phase='terminal', failure_stage=None)
    return operation


def hosted_admission_projection(operation):
    target = operation['target']
    artifact = operation['requested_outcome']['application']['source_artifact']
    return {
        'schema_version': 1,
        'admission': {
            'project_identity': target['project_identity'],
            'project_root_digest': target['project_root_digest'],
            'remote': target['remote_name'],
            'environment': target['environment'],
            'request_id': operation['request_id'], 'job_id': operation['job_id'],
            'starting_generation': 1, 'accepted_at': 1,
            'accepted_before_effects': True, 'digest': 'sha256:' + 'f' * 64,
            'source_schema': 2,
            'target': {'remote': target['remote_name'], 'project': 'fixture-project',
                       'environment': target['environment']},
            'source': {'clean': True, 'commit': SOURCE_COMMIT,
                       'identity': 'fixture-app', 'artifact': artifact},
            'evidence': {
                'host_identity': target['registered_host_digest'],
                'runtime_identity': target['runtime_identity'],
                'machine_identity': target['machine_identity'],
                'source_identity': 'fixture-app', 'config_digest': CONFIG_DIGEST,
            },
        },
    }


def hosted_job_projection(operation):
    return {'schema_version': 1, 'job': {
        'project_identity': operation['target']['project_identity'],
        'project_root': '/tmp/fixture-hosted-project',
        'job_id': operation['job_id'], 'request_id': operation['request_id'],
        'source_commit': SOURCE_COMMIT, 'lifecycle': 'succeeded',
        'finished_at': 1, 'started_at': 1, 'accepted_at': 1,
    }}


def delivery_fixture(project, request_id, *, succeeded=True, wrong_incarnation=False):
    """Construct closed synthetic owner evidence without creating an instance."""
    target = target_for_project(project, 'fixture-remote', label='default')
    stamp = now()
    outcome = {
        'kind': 'deploy_exposure', 'target_digest': canonical_digest(target),
        'application': {'source_identity': target['project_root_digest'], 'commit': 'a' * 40,
            'dirty_digest': None, 'artifact_digest': None, 'config_digest': 'sha256:' + 'b' * 64,
            'plan_digest': None, 'proof_digest': None, 'dirty_policy': 'overlay_declared'},
        'control': {'source_commit': 'c' * 40, 'source_runtime_revision': 'd' * 24,
            'installed_controller_runtime_revision': None,
            'capability_versions': {'delivery_outcomes_v1': 1}},
        'configuration_digest': 'sha256:' + 'b' * 64, 'route_contract_digest': None,
        'requirements': [{'kind': 'runtime_health', 'applicability': 'required', 'source': 'fixture_owner'}],
        'requested_at': stamp,
    }
    operation = new_operation(target, outcome, str(uuid.uuid4()), request_id)
    fields = {
        'schema_version': 1, 'delivery_intent_digest': operation['intent_digest'],
        'target_scope_digest': request_scope(target), 'project_identity': target['project_identity'],
        'project_root_digest': target['project_root_digest'], 'label': 'default',
        'instance_config_digest': 'sha256:' + 'e' * 64, 'create_allowed': True,
    }
    context = validate_creation_context({
        'schema_version': 1, 'operation_id': operation['operation_id'], 'request_id': request_id,
        'job_id': None, 'intent_digest': creation_digest(fields), 'intent_fields': fields,
        'project_identity': target['project_identity'], 'project_root_digest': target['project_root_digest'],
        'label': 'default',
    })
    receipt = validate_creation_receipt({key: context[key] for key in (
        'schema_version', 'operation_id', 'request_id', 'job_id', 'intent_digest',
        'project_identity', 'project_root_digest', 'label')} | {
        'instance_id': 'fixture-instance', 'instance_incarnation_id': 'inc_' + '1' * 32,
        'relation': 'reused', 'owner_commit_at': stamp, 'completion': 'succeeded',
        'completed_at': stamp, 'result_code': None,
    })
    operation['creation'] = normalize_creation_receipt(receipt, target, operation, creation_context=context)
    operation['runtime'] = {
        'source_kind': 'runtime', 'observed_at': stamp, 'target_digest': canonical_digest(target),
        'applicability': 'required', 'state': 'known', 'result': 'passed' if succeeded else 'failed',
        'reason': {'code': 'none' if succeeded else 'failed', 'message': 'Synthetic fixture health.'},
        'operation_id': operation['operation_id'], 'request_id': request_id, 'job_id': None,
        'instance_incarnation_id': 'inc_' + ('2' if wrong_incarnation else '1') * 32,
        # Availability evidence must not copy the requested commit/config.
        'application_revision': None, 'contract_digest': None,
    }
    operation.update(execution_state='succeeded' if succeeded else 'failed', started_at=stamp,
        finished_at=stamp, updated_at=stamp, phase='terminal', failure_stage=None if succeeded else 'runtime')
    operation.update(evaluate_operation(operation))
    return operation


def retain_fixture(repository, operation):
    initial = new_operation(operation['target'], operation['requested_outcome'],
        operation['operation_id'], operation['request_id'], operation['job_id'])
    repository.reserve_request(request_scope(operation['target']), operation['request_id'],
        operation['operation_id'], operation['intent_digest'], operation=initial)
    return repository.write_operation(operation)


class TestDeliveryService(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='delivery-service-')
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / 'home'
        self.project = Path(self.directory.name) / 'project'
        self.project.mkdir()
        self.repository = DeliveryRepository(self.home)
        self.service = DeliveryService(self.repository, target_resolver=lambda **query: target_for_project(
            query['project_dir'], query['remote'], label=query['label'], environment=query['environment']))

    def inspect(self, **kwargs):
        return self.service.inspect(project_dir=str(self.project), remote='fixture-remote', label='default', **kwargs)

    def retain(self, request, **kwargs):
        return retain_fixture(self.repository, delivery_fixture(self.project, request, **kwargs))

    def test_failed_latest_attempt_does_not_hide_retained_success(self):
        first = self.retain('fixture-a')
        failed = self.retain('fixture-b', succeeded=False)
        projection = self.inspect(request_id='fixture-b')
        self.assertTrue(projection['ok'])
        self.assertEqual(projection['latest_attempt']['operation_id'], failed['operation_id'])
        self.assertFalse(projection['latest_attempt']['delivery_succeeded'])
        self.assertEqual(projection['selected_operation']['failure_stage'], 'runtime')
        self.assertEqual(projection['latest_retained_complete_success']['operation_id'], first['operation_id'])
        self.assertIsNone(projection['current_observation'])
        self.assertIsNone(projection['next_action'])
        self.assertEqual(projection['next_action_reason']['code'], 'failed')
        self.assertIn('runtime verification', projection['next_action_reason']['message'])
        self.assertEqual(projection['selected_operation']['reason']['code'], 'failed')

    def test_complete_nonterminal_waits_for_authority_without_inventing_action(self):
        operation = delivery_fixture(self.project, 'still-running')
        operation.update(execution_state='running', phase='runtime', finished_at=None,
                         delivery_succeeded=False, delivery_state='incomplete')
        operation.update(evaluate_operation(operation))
        retained = retain_fixture(self.repository, operation)
        result = self.inspect(request_id='still-running')
        self.assertEqual(result['selected_operation']['operation_id'], retained['operation_id'])
        self.assertEqual(result['selected_operation']['evidence_completeness'], 'complete')
        self.assertFalse(result['selected_operation']['delivery_succeeded'])
        self.assertIsNone(result['next_action'])
        self.assertEqual(result['next_action_reason']['code'], 'authority_pending')

    def test_legacy_failure_guidance_does_not_rewrite_terminal_explanation(self):
        operation = delivery_fixture(self.project, 'legacy-failure', succeeded=False)
        operation['reason'] = {'code': 'required_evidence_missing',
                               'message': 'Legacy retained explanation.'}
        retained = retain_fixture(self.repository, operation)
        before = self.repository.path.read_bytes()
        result = self.inspect(request_id='legacy-failure')
        self.assertEqual(result['selected_operation']['reason'], retained['reason'])
        self.assertEqual(result['selected_operation']['terminal_snapshot_digest'], retained['terminal_snapshot_digest'])
        self.assertEqual(result['next_action_reason']['code'], 'failed')
        self.assertIsNone(result['next_action'])
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_recovery_is_separate_from_immutable_original_failure(self):
        original = self.retain('original-failed', succeeded=False)
        recovered = delivery_fixture(self.project, 'recovery-succeeded')
        recovered['recovery_relations'] = [{'kind': 'original_delivery',
                                           'identifier': original['operation_id']}]
        recovered = retain_fixture(self.repository, recovered)
        before = self.repository.path.read_bytes()
        result = self.inspect(request_id='original-failed')
        self.assertEqual(result['selected_operation']['terminal_snapshot_digest'], original['terminal_snapshot_digest'])
        self.assertFalse(result['selected_operation']['delivery_succeeded'])
        self.assertEqual(result['recovery_operations']['completeness'], 'known')
        self.assertEqual([row['operation_id'] for row in result['recovery_operations']['operations']],
                         [recovered['operation_id']])
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_incarnation_disagreement_cannot_be_complete_success(self):
        first = self.retain('fixture-a')
        wrong = self.retain('fixture-identity', wrong_incarnation=True)
        projection = self.inspect(request_id='fixture-identity')
        self.assertTrue(projection['ok'])
        self.assertFalse(projection['selected_operation']['delivery_succeeded'])
        self.assertEqual(projection['selected_operation']['evidence_completeness'], 'conflicting')
        self.assertEqual(projection['latest_retained_complete_success']['operation_id'], first['operation_id'])
        self.assertEqual(projection['selected_operation']['operation_id'], wrong['operation_id'])

    def test_availability_proof_does_not_invent_application_identity(self):
        operation = self.retain('fixture-availability')
        self.assertTrue(operation['delivery_succeeded'])
        self.assertIsNone(operation['runtime']['application_revision'])
        self.assertIsNone(operation['runtime']['contract_digest'])
        self.assertEqual(operation['requested_outcome']['application']['commit'], 'a' * 40)

    def test_nested_source_join_keeps_c_for_admission_and_job_but_t_for_runtime_and_edge(self):
        operation = hosted_source_operation()
        admission = normalize_recovery_projection(
            hosted_admission_projection(operation), operation['target'], operation)
        job = normalize_job_projection(
            hosted_job_projection(operation), operation['target'], operation)

        self.assertEqual(admission['application_revision'], SOURCE_COMMIT)
        self.assertEqual(job['application_revision'], SOURCE_COMMIT)
        self.assertEqual(operation['runtime']['application_revision'], DEPLOYED_COMMIT)
        self.assertEqual(operation['edge']['application_revision'], DEPLOYED_COMMIT)
        self.assertTrue(evaluate_operation(operation)['delivery_succeeded'])

    def test_nested_source_runtime_and_edge_joins_reject_c_or_wrong_configuration(self):
        cases = (
            {'runtime_revision': SOURCE_COMMIT},
            {'runtime_config': 'sha256:' + 'd' * 64},
            {'edge_revision': SOURCE_COMMIT},
            {'edge_config': 'sha256:' + 'd' * 64},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                result = evaluate_operation(hosted_source_operation(**changes))
                self.assertFalse(result['delivery_succeeded'])
                self.assertEqual(result['delivery_state'], 'incomplete')
                self.assertEqual(result['evidence_completeness'], 'conflicting')

    def test_nested_source_missing_runtime_observation_stays_incomplete(self):
        result = evaluate_operation(hosted_source_operation(runtime_revision=None))
        self.assertFalse(result['delivery_succeeded'])
        self.assertEqual(result['delivery_state'], 'incomplete')
        self.assertEqual(result['evidence_completeness'], 'partial')

    def test_terminal_rewrite_refused_and_request_guard_retained(self):
        operation = self.retain('fixture-a')
        scope = request_scope(operation['target'])
        altered = copy.deepcopy(operation)
        altered['reason']['message'] = 'Different terminal result.'
        with self.assertRaises(DeliveryError) as raised:
            self.repository.write_operation(altered)
        self.assertEqual(raised.exception.code, 'delivery_terminal_conflict')
        self.assertEqual(self.repository.get(operation['operation_id']), operation)
        repeated = self.repository.preflight_request(scope, 'fixture-a', str(uuid.uuid4()), operation['intent_digest'])
        conflicting = self.repository.preflight_request(scope, 'fixture-a', str(uuid.uuid4()), 'sha256:' + 'f' * 64)
        self.assertEqual(repeated['status'], 'existing')
        self.assertEqual(repeated['operation_id'], operation['operation_id'])
        self.assertEqual(conflicting['status'], 'request_conflict')

    def test_pagination_preserves_order_and_reads_do_not_change_store(self):
        first = self.retain('fixture-a')
        second = self.retain('fixture-b', succeeded=False)
        before = hashlib.sha256(self.repository.path.read_bytes()).digest()
        page_one = self.inspect(limit=1)
        page_two = self.inspect(limit=1, cursor=page_one['history']['next_cursor'])
        self.assertEqual([row['operation_id'] for row in page_one['history']['operations']], [second['operation_id']])
        self.assertEqual([row['operation_id'] for row in page_two['history']['operations']], [first['operation_id']])
        self.assertIsNone(page_two['history']['next_cursor'])
        self.assertEqual(hashlib.sha256(self.repository.path.read_bytes()).digest(), before)

    def test_missing_history_is_explicit_and_does_not_initialize_store(self):
        projection = self.inspect()
        self.assertTrue(projection['ok'])
        self.assertEqual(projection['history']['completeness'], 'missing')
        self.assertEqual(projection['history']['returned_count'], 0)
        self.assertIsNone(projection['latest_retained_complete_success'])
        self.assertFalse(self.home.exists())

    def test_missing_request_does_not_fall_back_to_success(self):
        self.retain('fixture-a')
        projection = self.inspect(request_id='fixture-missing')
        self.assertTrue(projection['ok'])
        self.assertEqual(projection['error']['code'], 'missing')
        self.assertIsNone(projection['selected_operation'])


if __name__ == '__main__':
    unittest.main()
