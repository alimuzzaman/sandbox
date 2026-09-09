"""Closed trace regressions from CLI/W11 and bounded owner observations."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from sandbox.delivery import models as m
from sandbox.delivery.producers.lenzora import (
    CHECK_IDS, decode_owner_projection, parent_request_id, request_identity_bytes,
)
from sandbox.delivery.producers.manifest import descriptors, get_producer
from sandbox.delivery.trace_models import (
    TraceContractError, TraceQueryBudget, encoded, parse_trace_owner_record,
    parse_trace_query, parse_trace_record, parse_trace_start, safe_reason,
    serialize_trace_query,
)

REVISION = 'a' * 40
STAMP = '2099-01-01T00:00:00.000000Z'
PRODUCER = 'lenzora-hosted-v1'


def digest(name):
    return m.canonical_digest({'fixture': name})


def make_scope():
    return {'project_identity': 'trace-project', 'project_root_digest': digest('root'),
            'controller_context': {'source_commit': 'b' * 40,
                'source_runtime_revision': 'c' * 24,
                'installed_controller_runtime_revision': None}}


def make_context():
    return {'source_commit': REVISION, 'source_digest': digest('producer'),
            'node_version': 'v24.18.0', 'state': 'known', 'reason': safe_reason('known')}


def make_start(mode='deploy', environment='development'):
    return {'schema_version': 1,
            'producer': {'id': PRODUCER, 'version': 1, 'projection_schema': 1},
            'mode': mode, 'requested_target': {'environment': environment, 'remote_name': None},
            'requested_source': {'revision': None, 'policy': 'retained_verified_artifact'},
            'producer_context': make_context(), 'expected_runtime_revision': 'c' * 24}


def make_record(payload):
    return {'schema_version': 1, 'producer_context': make_context(),
            'expected_runtime_revision': 'c' * 24, 'payload': payload}


def make_projection(*, generation=0, revision=REVISION, legacy=False):
    release = {'target': 'development', 'revision': revision, 'branch': 'dev',
               'run_id': 1, 'run_attempt': 1, 'artifact_id': 1,
               'artifact_name': 'hosted-development-images-' + revision}
    parent = parent_request_id(release, digest('receipt'), generation)
    return {'schema_version': 1, 'producer_id': PRODUCER, 'producer_version': 1,
            'projection_kind': 'legacy_run' if legacy else 'current_run',
            'observed_at': STAMP, 'preflight': None,
            'run': {'release': release, 'receipt_digest': digest('receipt'), 'generation': generation,
                    'parent_request_id': parent, 'stage_request_id': parent + '-stage',
                    'activation_request_id': parent + '-activate'},
            'control': {'source_commit': REVISION, 'root_digest': digest('root'),
                        'producer_source_digest': None if legacy else digest('producer'),
                        'state': 'partial' if legacy else 'known',
                        'reason': safe_reason('history_unavailable' if legacy else 'known')},
            'artifact': None, 'phase_jobs': [], 'native_receipts': [], 'terminal': None}


def make_query():
    return {'schema_version': 1, 'query_kind': 'deployment_trace', 'ok': True, 'error': None,
            'query_scope': {**{k: v for k, v in make_scope().items() if k != 'controller_context'},
                            'trace_id': '11111111-1111-4111-8111-111111111111', 'trace_request_id': None},
            'recorded_at': STAMP, 'observation_mode': 'recorded_only', 'trace': None,
            'owner_evidence': {'summary': {'joined_deployment_result': 'unknown',
                'completeness': 'partial', 'reason': safe_reason('required_evidence_missing')},
                'links': [], 'parent_stages': {'role_stages': None, 'completeness': 'missing',
                                             'reason': safe_reason('history_unavailable')}},
            'recovery_operations': {'completeness': 'missing', 'omitted': 0, 'operations': []},
            'mutation_receipt': None, 'coverage': {'trace_state': 'missing', 'owner_state': 'missing',
                'early_history': 'unavailable', 'omitted_events': 0, 'omitted_links': 0,
                'reason': safe_reason('missing')}, 'next_action': None}


def large_role_detail():
    fields = {'source': {'requested_revision': REVISION, 'observed_revision': REVISION,
                        'control_revision': None, 'source_artifact': None},
              'artifact': {'policy': None, 'receipt_digest': None, 'plan_digest': None,
                           'proof_digest': None, 'manifest_digests': [digest(str(n)) for n in range(32)]},
              'target': {'registered_host_digest': None, 'environment': None, 'incarnation_id': None},
              'generation': {'generation_id': None, 'digest': None}, 'configuration': {'digest': None},
              'initializer': {'status': None, 'receipt_digest': None},
              'runtime': {'source_revision': REVISION, 'image_digests': [digest(str(n)) for n in range(32)],
                          'health': None, 'verification_digest': None},
              'public_verification': {'hosts': [], 'edge_state': None, 'edge_proof_digest': None},
              'job': {'submission_digest': None, 'started_at': None, 'finished_at': None,
                      'exit_code': None, 'output_complete': None}}
    common = {'source_kind': 'fixture_native', 'observed_at': STAMP, 'target_digest': digest('target'),
              'applicability': 'required', 'state': 'partial', 'result': 'unknown',
              'reason': {'code': 'required_evidence_missing', 'message': 'x' * 160}}
    return {key: {**deepcopy(common), **value} for key, value in fields.items()}


class TraceModelsTests(unittest.TestCase):
    def test_golden_w11_request_bytes_and_roles(self):
        # Exact observed W11 fixture, not a digest recomputed to form its expectation.
        release = {'target': 'development', 'revision': '63a74f95c129cac249a2d143fa886a31bb2e5f6b',
                   'branch': 'dev', 'run_id': 101, 'run_attempt': 1, 'artifact_id': 9001,
                   'artifact_name': 'hosted-development-images-63a74f95c129cac249a2d143fa886a31bb2e5f6b'}
        receipt = 'sha256:d9740416cbe0890e24d0c74302308b1a690d4c0b32bd2b64b30d2c7c110051c2'
        expected = (b'{"domain":"lenzora-hosted-deployment-v1","release":{"target":"development",'
                    b'"revision":"63a74f95c129cac249a2d143fa886a31bb2e5f6b","branch":"dev",'
                    b'"runId":101,"runAttempt":1,"artifactId":9001,"artifactName":'
                    b'"hosted-development-images-63a74f95c129cac249a2d143fa886a31bb2e5f6b"},'
                    b'"receiptDigest":"sha256:d9740416cbe0890e24d0c74302308b1a690d4c0b32bd2b64b30d2c7c110051c2",'
                    b'"generation":0}')
        self.assertEqual(request_identity_bytes(release, receipt, 0), expected)
        self.assertEqual(parent_request_id(release, receipt, 0),
                         'lenzora-dev-e3d29395968f793df4c65a9e96eef444014a410b')
        self.assertNotEqual(parent_request_id(release, receipt, 1), parent_request_id(release, receipt, 0))

    def test_fixed_registry_does_not_accept_decoder_locators(self):
        self.assertEqual([row['producer_id'] for row in descriptors()], [PRODUCER])
        self.assertTrue(callable(get_producer(PRODUCER)['decoder']))
        self.assertIsNone(get_producer('project.path:decoder'))
        self.assertNotIn('decoder', descriptors()[0])
        bad = make_start(); bad['producer']['decoder'] = 'project.path'
        with self.assertRaises(TraceContractError): parse_trace_start(bad)

    def test_inputs_are_closed_bounded_and_nonsecret(self):
        valid = make_start()
        self.assertEqual(parse_trace_start(json.dumps(valid)), valid)
        invalid = [dict(valid, unknown=True), dict(valid, schema_version=True),
                   dict(valid, expected_runtime_revision='x' * 24)]
        for message in ('/private/fixture', 'https://fixture.invalid', 'x' * 201, 'a\x00b'):
            value = deepcopy(valid); value['producer_context']['reason']['message'] = message
            invalid.append(value)
        invalid.extend(['{"schema_version":1,"schema_version":1}', '{"value":NaN}', ' ' * 32769])
        for value in invalid:
            with self.subTest(value=type(value).__name__), self.assertRaises(TraceContractError):
                parse_trace_start(value)

    def test_owner_and_invocation_payload_grammars_are_distinct(self):
        stage = {'kind': 'stage', 'stage': 'prepare', 'status': 'running', 'effect_state': 'entered',
                 'reason': safe_reason('producer_recorded'), 'references': []}
        self.assertEqual(parse_trace_record(make_record(stage))['payload'], stage)
        with self.assertRaises(TraceContractError): parse_trace_owner_record(make_record(stage))
        parent = make_projection()['run']['parent_request_id']
        owner = dict(stage, role='prepare_job', job_request_id=parent + '-prepare')
        self.assertEqual(parse_trace_owner_record(make_record(owner))['payload'], owner)
        with self.assertRaises(TraceContractError): parse_trace_record(make_record(owner))

    def test_projection_rejects_role_substitution_and_wrong_control(self):
        value = make_projection(); parent = value['run']['parent_request_id']
        job = {'role': 'prepare_job', 'job_id': '1' * 16, 'request_id': parent + '-prepare',
               'control_root_digest': digest('root'), 'control_source_commit': REVISION,
               'submission_digest': digest('submission'), 'state': 'known',
               'reason': safe_reason('producer_recorded')}
        value['phase_jobs'] = [job]
        self.assertEqual(decode_owner_projection(value), value)
        for field, replacement in [('request_id', parent + '-stage'),
                                   ('control_source_commit', 'f' * 40),
                                   ('control_root_digest', digest('other'))]:
            invalid = deepcopy(value); invalid['phase_jobs'][0][field] = replacement
            with self.subTest(field=field), self.assertRaises(TraceContractError):
                decode_owner_projection(invalid)
        invalid = deepcopy(value); invalid['phase_jobs'].append(deepcopy(job))
        with self.assertRaises(TraceContractError): decode_owner_projection(invalid)

    def test_preflight_accepts_actual_nonsequential_dependency_and_two_part_git(self):
        value = make_projection(); value['run'] = None
        checks = [{'id': name, 'status': 'passed', 'code': 'verified', 'observed': {},
                   'blocked_by': []} for name in CHECK_IDS]
        checks[2]['observed'] = {'version': '2.50'}
        checks[7].update(status='failed', code='defaults_malformed')
        for index, blocked in ((4, 'defaults'), (8, 'defaults'), (9, 'sandbox-source'), (10, 'sandbox-interfaces')):
            checks[index].update(status='blocked', code='prerequisite_failed', blocked_by=[blocked])
        value['preflight'] = {'mode': 'preflight', 'target': 'development', 'requested_revision': None,
                              'ok': False, 'checks': checks}
        self.assertEqual(decode_owner_projection(value), value)
        invalid = deepcopy(value); invalid['preflight']['ok'] = True
        with self.assertRaises(TraceContractError): decode_owner_projection(invalid)
        invalid = deepcopy(value); invalid['preflight']['checks'][4]['blocked_by'] = ['node']
        with self.assertRaises(TraceContractError): decode_owner_projection(invalid)

    def test_query_elision_preserves_closed_fields_and_downgrades_success(self):
        query = make_query(); detail = large_role_detail()
        for number in range(32):
            query['owner_evidence']['links'].append({'role': 'activation_operation',
                'request_id': f'fixture-{number}', 'job_id': None, 'operation_id': None,
                'source_role': 'application', 'state': 'partial', 'result': 'unknown',
                'provenance': 'owner_verified', 'observed_at': STAMP, 'proof_digest': digest('proof'),
                'reason': safe_reason('required_evidence_missing'), 'detail': deepcopy(detail)})
        query['owner_evidence']['summary'].update(joined_deployment_result='succeeded', completeness='complete')
        original = deepcopy(query)
        self.assertGreater(len(encoded(query)), 262144)
        output = serialize_trace_query(query)
        self.assertLessEqual(len(output.encode()), 262144)
        parsed = parse_trace_query(json.loads(output))
        self.assertGreater(parsed['coverage']['omitted_links'], 0)
        self.assertEqual(parsed['owner_evidence']['summary']['joined_deployment_result'], 'unknown')
        self.assertEqual(parsed['coverage']['owner_state'], 'partial')
        self.assertEqual(query, original)
        invalid = deepcopy(parsed); invalid['owner_evidence']['links'][0]['detail']['runtime']['argv'] = []
        with self.assertRaises(TraceContractError): parse_trace_query(invalid)

    def test_budget_uses_original_monotonic_deadline(self):
        budget = TraceQueryBudget(15)
        with patch('sandbox.delivery.trace_models.time.monotonic', return_value=12):
            self.assertEqual(budget.remaining_seconds(), 3)
            budget.check()
        with patch('sandbox.delivery.trace_models.time.monotonic', return_value=16):
            self.assertTrue(budget.expired)
            with self.assertRaises(TraceContractError) as error: budget.check()
            self.assertEqual(error.exception.code, 'budget_exhausted')
