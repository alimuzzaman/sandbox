"""Observed command/child and exact native joins, without deployment effects."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from sandbox.delivery import models as m
from sandbox.delivery.trace_context import TraceProducerRegistry
from sandbox.delivery.trace_repository import TraceRepository
from sandbox.delivery.trace_service import TraceService, format_trace_projection
from sandbox.delivery.trace_models import safe_reason
from tests.test_delivery_trace_models import (
    REVISION, STAMP, digest, large_role_detail, make_projection, make_record,
    make_scope, make_start,
)


APP_REVISION = 'b' * 40


class FixtureRegistry(TraceProducerRegistry):
    def scope(self, project_dir, *, write=False):
        return make_scope()


class TraceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='trace-service-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.repository = TraceRepository(self.home)
        self.projection = make_projection(revision=APP_REVISION)
        self.run = self.projection['run']
        self.jobs_state = 'succeeded'
        self.budgets = []
        self.target = {'machine_identity': 'fixture-machine',
                       'target_identity': 'fixture/app/development',
                       'daemon_identity': 'fixture-daemon'}
        for index, role in enumerate(('prepare_job', 'activation_job')):
            suffix = '-prepare' if role == 'prepare_job' else '-activate'
            self.projection['phase_jobs'].append({
                'role': role, 'job_id': str(index + 1) * 32,
                'request_id': self.run['parent_request_id'] + suffix,
                'control_root_digest': digest('root'), 'control_source_commit': REVISION,
                'submission_digest': digest(role), 'state': 'known',
                'reason': safe_reason('known')})
        self.projection['artifact'] = {
            'policy': 'retained_verified_artifact', 'revision': APP_REVISION,
            'receipt_digest': digest('receipt'), 'plan_digest': digest('plan'),
            'proof_digest': digest('proof'), 'configuration_digest': digest('config'),
            'manifest_digests': [digest('image')]}
        self.projection['native_receipts'] = [{
            'role': 'activation_operation', 'request_id': self.run['activation_request_id'],
            'operation_id': None, 'generation': 0, 'plan_digest': digest('plan'),
            'proof_digest': digest('proof'), 'configuration_digest': digest('config'),
            'target_digest': m.canonical_digest(self.target)}]
        detail = large_role_detail()
        for block in detail.values():
            block.update(state='known', result='passed', observed_at=STAMP,
                         reason=safe_reason('known'))
        detail['job'].update(state='not_applicable', result='not_applicable',
                             applicability='not_applicable')
        detail['source'].update(requested_revision=APP_REVISION, observed_revision=APP_REVISION)
        detail['artifact'].update(policy='retained_verified_artifact',
            receipt_digest=digest('receipt'), plan_digest=digest('plan'),
            proof_digest=digest('proof'), manifest_digests=[digest('image')])
        detail['target'].update(registered_host_digest=digest('host'), environment='development')
        detail['generation'].update(generation_id=1, digest=digest('generation'))
        detail['configuration']['digest'] = digest('config')
        detail['initializer'].update(status='passed', receipt_digest=digest('initializer'))
        detail['runtime'].update(source_revision=APP_REVISION, image_digests=[digest('image')],
                                 health='healthy', verification_digest=digest('running'))
        detail['public_verification'].update(edge_state='passed', edge_proof_digest=digest('edge'))
        self.native = {'state': 'known', 'request_id': self.run['activation_request_id'],
            'request_digest': digest('request'), 'target': self.target,
            'project_root_digest': digest('application'), 'detail': detail,
            'generation_reference': {'generation_digest': digest('generation')},
            'result': {'ok': True, 'result_class': 'success',
                'request_id': self.run['activation_request_id'], 'request_digest': digest('request'),
                'starting_generation': 0, 'resulting_generation': 1,
                'generation_digest': digest('generation')}}
        self.service = TraceService(self.repository, FixtureRegistry(), self.job_reader,
            lambda request, scope, budget: deepcopy(self.native),
            lambda *args: None, lambda *args: None)

    def job_reader(self, candidate, scope, budget):
        self.budgets.append(budget)
        return {'state': 'known', 'job': {
            'job_id': candidate['job_id'], 'request_id': candidate['request_id'],
            'project_root_digest': candidate['control_root_digest'],
            'source_commit': candidate['control_source_commit'],
            'project_identity': scope['project_identity'], 'lifecycle': self.jobs_state,
            'accepted_at': STAMP, 'started_at': STAMP,
            'finished_at': None if self.jobs_state == 'running' else STAMP,
            'exit_code': 0 if self.jobs_state == 'succeeded' else 7 if self.jobs_state == 'failed' else None,
            'output_completeness': 'complete'},
            'submission': {key: candidate[key] for key in (
                'role', 'request_id', 'control_root_digest', 'control_source_commit', 'submission_digest')}}

    def attach(self, *, legacy=False):
        intent = make_start('legacy_projection' if legacy else 'deploy')
        intent['requested_target']['remote_name'] = 'fixture'
        start = self.service.start('.', str(uuid.uuid4()), intent)
        self.assertTrue(start['ok'], start)
        self.trace_id = start['trace_id']
        self.sequence = 0
        self.record({'kind': 'identity', 'identity': {'kind': 'application_source',
                     'revision': APP_REVISION, 'root_digest': digest('application')}})
        projection = deepcopy(self.projection)
        if legacy:
            projection['projection_kind'] = 'legacy_run'
        self.record({'kind': 'projection', 'projection': projection})
        return self.trace_id

    def record(self, payload):
        result = self.service.record('.', self.trace_id, str(uuid.uuid4()),
                                     self.sequence, make_record(payload))
        self.assertTrue(result['ok'], result)
        self.sequence = result['sequence']
        return result

    def inspect(self):
        return self.service.inspect('.', trace_id=self.trace_id)

    def test_exact_native_and_control_jobs_establish_separate_source_proof(self):
        self.attach()
        before = self.repository.path.read_bytes()
        result = self.inspect()
        self.assertEqual(result['owner_evidence']['summary']['joined_deployment_result'], 'succeeded')
        links = {row['role']: row for row in result['owner_evidence']['links']}
        self.assertEqual(len(links), 5)
        self.assertTrue(all(row['provenance'] == 'owner_verified' for row in links.values()))
        self.assertEqual(links['prepare_job']['detail']['source']['control_revision'], REVISION)
        self.assertEqual(links['runtime']['detail']['runtime']['source_revision'], APP_REVISION)
        self.assertEqual(self.repository.path.read_bytes(), before)
        self.assertIs(self.budgets[0], self.budgets[1])

    def test_same_artifact_on_other_application_cannot_establish_success(self):
        self.attach()
        self.native['project_root_digest'] = digest('different-application')
        result = self.inspect()
        self.assertEqual(result['owner_evidence']['summary']['completeness'], 'partial')
        self.assertNotEqual(result['owner_evidence']['summary']['joined_deployment_result'], 'succeeded')

    def test_conflicting_native_proof_cannot_be_promoted_by_producer(self):
        self.attach()
        self.native['detail']['artifact']['proof_digest'] = digest('different-proof')
        result = self.inspect()
        self.assertEqual(result['owner_evidence']['summary']['joined_deployment_result'], 'unknown')
        self.assertEqual(result['owner_evidence']['summary']['completeness'], 'conflicting')

    def test_missing_native_owner_keeps_completed_jobs_but_not_success(self):
        self.attach()
        self.native = None
        result = self.inspect()
        self.assertEqual(result['owner_evidence']['summary']['completeness'], 'partial')
        jobs = [row for row in result['owner_evidence']['links'] if row['source_role'] == 'control']
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(row['state'] == 'complete' for row in jobs))

    def test_interrupted_command_stays_frozen_after_children_complete(self):
        self.attach()
        self.jobs_state = 'running'
        self.record({'kind': 'finish', 'command_result': 'interrupted',
                     'requested_deployment_result': 'incomplete', 'reason': safe_reason('failed')})
        original = self.repository.read(make_scope(), trace_id=self.trace_id)['trace']
        self.jobs_state = 'succeeded'
        result = self.inspect()
        self.assertEqual(result['trace']['command_result'], 'interrupted')
        self.assertEqual(result['trace']['terminal_digest'], original['terminal_digest'])
        self.assertEqual(result['trace']['deployment_result'], original['deployment_result'])
        self.assertEqual(result['owner_evidence']['summary']['joined_deployment_result'], 'succeeded')

    def test_exact_failed_jobs_are_complete_failure_without_action(self):
        self.attach()
        self.jobs_state = 'failed'
        result = self.inspect()
        self.assertEqual(result['owner_evidence']['summary']['joined_deployment_result'], 'failed')
        self.assertEqual(result['owner_evidence']['summary']['completeness'], 'complete')
        self.assertIsNone(result['next_action'])

    def test_legacy_export_keeps_early_history_unavailable(self):
        self.attach(legacy=True)
        result = self.inspect()
        self.assertEqual(result['coverage']['early_history'], 'unavailable')
        self.assertEqual(result['trace']['stages']['preflight']['status'], 'not_started')

    def test_shared_exhausted_budget_retains_already_collected_evidence(self):
        self.attach()
        original = self.service.job_reader
        def expire_second(candidate, scope, budget):
            if candidate['role'] == 'activation_job':
                budget.deadline_monotonic = time.monotonic() - 1
                budget.check()
            return original(candidate, scope, budget)
        self.service.job_reader = expire_second
        result = self.inspect()
        self.assertEqual(result['trace']['trace_id'], self.trace_id)
        self.assertEqual(result['coverage']['reason']['code'], 'budget_exhausted')
        self.assertEqual(result['owner_evidence']['links'][0]['role'], 'prepare_job')

    def test_owner_exception_text_is_not_public(self):
        self.attach()
        def unavailable(*args):
            raise ValueError('private-fixture-sentinel')
        self.service.job_reader = unavailable
        result = self.inspect()
        self.assertNotIn('private-fixture-sentinel', json.dumps(result))
        self.assertEqual(result['owner_evidence']['summary']['completeness'], 'partial')

    def test_human_output_exposes_history_and_query_omissions(self):
        self.attach()
        result = self.inspect()
        result['coverage'].update(early_history='unavailable', omitted_events=192, omitted_links=2)
        result['recovery_operations']['omitted'] = 1
        result['trace']['owner_projection'] = None
        result['trace']['detail_coverage']['projection'] = 'omitted'
        text = format_trace_projection(result)
        for explanation in ('Early history: unavailable', 'omitted events: 192',
                            'omitted links: 2', 'omitted recoveries: 1',
                            'producer projection: omitted'):
            self.assertIn(explanation, text)


if __name__ == '__main__':
    unittest.main()
