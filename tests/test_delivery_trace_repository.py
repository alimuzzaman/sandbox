"""Regressions from actual trace-bounds and binding-correction owner runs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from sandbox.delivery import trace_repository as owner
from sandbox.delivery.trace_repository import TraceRepository
from sandbox.delivery.trace_models import TraceQueryBudget, safe_reason
from tests.test_delivery_trace_models import (
    PRODUCER, REVISION, STAMP, digest, make_projection, make_record, make_scope, make_start,
)


class TraceRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'
        self.project.mkdir()
        self.home = Path(temporary.name) / 'home'
        self.repository = TraceRepository(self.home)
        self.scope = make_scope()
        self.scope['project_root_digest'] = 'sha256:' + hashlib.sha256(str(self.project).encode()).hexdigest()
        self.clock = patch.object(owner, '_now', return_value=STAMP)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def projection(self, **kwargs):
        result = make_projection(**kwargs)
        result['control']['root_digest'] = self.scope['project_root_digest']
        return result

    def start(self, mode='deploy', environment='development'):
        result = self.repository.start(self.scope, str(uuid.uuid4()), make_start(mode, environment))
        self.assertEqual(result['code'], 'created')
        return result

    def write(self, start, payload, *, sequence=None, mutation=None, scope=None):
        if sequence is None:
            sequence = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']['sequence']
        return self.repository.record(scope or self.scope, start['trace_id'], mutation or str(uuid.uuid4()),
                                      sequence, make_record(payload))

    def attach(self, *, mode='deploy', generation=0):
        start = self.start(mode)
        projection = self.projection(generation=generation, legacy=mode == 'legacy_projection')
        result = self.write(start, {'kind': 'projection', 'projection': projection})
        self.assertEqual(result['code'], 'recorded')
        return start, projection

    def snapshot(self):
        return {str(path.relative_to(self.home)): path.read_bytes()
                for path in self.home.rglob('*') if path.is_file()} if self.home.exists() else {}

    def parent_count(self):
        with sqlite3.connect(self.repository.path) as connection:
            return connection.execute("SELECT count(*) FROM guards WHERE kind='parent'").fetchone()[0]

    @staticmethod
    def stage(message='producer recorded'):
        return {'kind': 'stage', 'stage': 'bootstrap', 'status': 'running', 'effect_state': 'not_started',
                'reason': {'code': 'producer_recorded', 'message': message}, 'references': []}

    @staticmethod
    def finish():
        return {'kind': 'finish', 'command_result': 'succeeded', 'requested_deployment_result': 'not_started',
                'reason': safe_reason('command_completed')}

    @staticmethod
    def identity(revision):
        return {'kind': 'identity', 'identity': {'kind': 'application_source', 'revision': revision,
                                               'root_digest': digest('application')}}

    def assert_refusal_unchanged(self, start, payload, code):
        before = self.snapshot()
        initial = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']
        parents = self.parent_count()
        mutation = str(uuid.uuid4())
        result = self.write(start, payload, mutation=mutation)
        self.assertEqual(result['code'], code)
        after = self.repository.read(self.scope, trace_id=start['trace_id'], mutation_id=mutation)
        self.assertEqual(after['trace'], initial)
        self.assertIsNone(after['mutation_receipt'])
        self.assertEqual(self.parent_count(), parents)
        self.assertEqual(self.snapshot(), before)

    def test_read_is_missing_without_creating_home_and_preserves_existing_bytes(self):
        self.assertEqual(self.repository.read(self.scope, trace_request_id=str(uuid.uuid4()))['state'], 'missing')
        self.assertFalse(self.home.exists())
        start = self.start()
        before = self.snapshot()
        self.assertEqual(self.repository.read(self.scope, trace_id=start['trace_id'])['state'], 'retained')
        self.assertEqual(self.snapshot(), before)
        wrong = dict(self.scope, project_root_digest=digest('other-root'))
        self.assertEqual(self.repository.read(wrong, trace_id=start['trace_id'])['state'], 'missing')

    def test_project_request_locator_freezes_mode_and_target(self):
        start = self.start()
        replay = self.repository.start(self.scope, start['trace_request_id'], make_start())
        self.assertEqual(replay['code'], 'existing')
        self.assertEqual(replay['trace_id'], start['trace_id'])
        self.assertEqual(replay['document_digest'], start['document_digest'])
        for changed in (make_start('preflight'), make_start(environment='production')):
            result = self.repository.start(self.scope, start['trace_request_id'], changed)
            self.assertEqual(result['code'], 'trace_request_conflict')
            self.assertEqual(result['trace_id'], start['trace_id'])

    def test_receipt_replay_precedes_cas_and_preserves_original_snapshot(self):
        start = self.start(); mutation = str(uuid.uuid4()); payload = self.stage('first')
        first = self.write(start, payload, mutation=mutation, sequence=0)
        self.assertEqual(self.write(start, self.stage('second'))['code'], 'recorded')
        self.assertEqual(self.write(start, payload, mutation=mutation, sequence=999), first)
        self.assertEqual(self.write(start, self.stage('changed'), mutation=mutation)['code'], 'trace_request_conflict')
        stale = str(uuid.uuid4())
        self.assertEqual(self.write(start, self.stage('third'), mutation=stale, sequence=0)['code'], 'trace_sequence_conflict')
        read = self.repository.read(self.scope, trace_id=start['trace_id'], mutation_id=stale)
        self.assertIsNone(read['mutation_receipt'])
        self.assertEqual(self.write(start, self.stage('third'), mutation=stale, sequence=2)['code'], 'recorded')
        receipt = self.repository.read(self.scope, trace_id=start['trace_id'], mutation_id=mutation)['mutation_receipt']
        self.assertEqual(receipt['accepted_sequence'], 1)
        self.assertEqual(receipt['document_digest'], first['document_digest'])

    def test_known_application_revision_rejects_new_run_before_parent_creation(self):
        start = self.start()
        self.assertEqual(self.write(start, self.identity(REVISION))['code'], 'recorded')
        self.assert_refusal_unchanged(start,
            {'kind': 'projection', 'projection': self.projection(revision='f' * 40)}, 'trace_revision_unsupported')
        self.assertEqual(self.parent_count(), 0)

    def test_projection_prevents_conflicting_later_application_identity(self):
        start, _ = self.attach()
        self.assert_refusal_unchanged(start, self.identity('f' * 40), 'trace_revision_unsupported')

    def test_same_revision_different_generation_cannot_replace_parent(self):
        start, _ = self.attach()
        self.assert_refusal_unchanged(start,
            {'kind': 'projection', 'projection': self.projection(generation=1)}, 'trace_owner_conflict')

    def test_legacy_run_cannot_be_replaced_or_cleared_without_parent(self):
        start, original = self.attach(mode='legacy_projection')
        self.assertEqual(self.parent_count(), 0)
        self.assert_refusal_unchanged(start,
            {'kind': 'projection', 'projection': self.projection(generation=1, legacy=True)}, 'trace_owner_conflict')
        cleared = deepcopy(original); cleared['run'] = None
        self.assert_refusal_unchanged(start, {'kind': 'projection', 'projection': cleared}, 'trace_owner_conflict')

    def test_same_parent_enrichment_and_receipt_replay_are_monotonic(self):
        start, projection = self.attach()
        parent = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']['parent_link']
        projection['artifact'] = {'policy': 'retained_verified_artifact', 'revision': REVISION,
            'receipt_digest': digest('receipt'), 'plan_digest': digest('plan'), 'proof_digest': None,
            'configuration_digest': None, 'manifest_digests': [digest('manifest')]}
        mutation = str(uuid.uuid4()); payload = {'kind': 'projection', 'projection': projection}
        first = self.write(start, payload, mutation=mutation)
        self.assertEqual(first['code'], 'recorded')
        snapshot = self.snapshot()
        self.assertEqual(self.write(start, payload, mutation=mutation, sequence=1), first)
        self.assertEqual(self.snapshot(), snapshot)
        retained = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']
        self.assertEqual(retained['parent_link'], parent)
        self.assertEqual(self.parent_count(), 1)
        # Older partial parent publication cannot clear already retained artifact evidence.
        owner_read = self.repository.read_owner(self.scope, PRODUCER, parent['parent_request_id'])
        result = self.repository.record_owner(self.scope, PRODUCER, parent['parent_request_id'], str(uuid.uuid4()),
            owner_read['record']['sequence'], make_record({'kind': 'projection', 'projection': self.projection()}))
        self.assertEqual(result['code'], 'recorded')
        self.assertEqual(self.repository.read_owner(self.scope, PRODUCER, parent['parent_request_id'])['record']['projection']['artifact'], projection['artifact'])

    def test_parent_publication_replay_cas_and_absorbing_role_stage(self):
        _, projection = self.attach(); parent = projection['run']['parent_request_id']
        payload = {'kind': 'stage', 'role': 'prepare_job', 'job_request_id': parent + '-prepare',
                   'stage': 'prepare', 'status': 'running', 'effect_state': 'entered',
                   'reason': safe_reason('producer_recorded'),
                   'references': [{'kind': 'request', 'identifier': parent + '-prepare'}]}
        def publish(value, sequence, publication=None):
            return self.repository.record_owner(self.scope, PRODUCER, parent,
                publication or str(uuid.uuid4()), sequence, make_record(value))
        publication = str(uuid.uuid4()); first = publish(payload, 0, publication)
        self.assertEqual(first['code'], 'recorded')
        self.assertEqual(publish(payload, 999, publication), first)
        changed = dict(payload, status='succeeded', effect_state='observed')
        self.assertEqual(publish(changed, 1, publication)['code'], 'trace_owner_conflict')
        stale = str(uuid.uuid4())
        self.assertEqual(publish(changed, 0, stale)['code'], 'trace_sequence_conflict')
        self.assertIsNone(self.repository.read_owner(self.scope, PRODUCER, parent, stale)['publication_receipt'])
        self.assertEqual(publish(dict(payload, stage='activation'), 1)['code'], 'trace_owner_conflict')
        self.assertEqual(publish(changed, 1, stale)['code'], 'recorded')
        self.assertEqual(publish(payload, 2)['code'], 'trace_sequence_conflict')

    def test_terminal_bytes_do_not_change_when_trusted_writer_releases_protection(self):
        start, projection = self.attach(); parent = projection['run']['parent_request_id']
        first = self.write(start, self.finish())
        self.assertEqual(first['code'], 'recorded')
        document = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']
        def row():
            with sqlite3.connect(self.repository.path) as conn:
                return conn.execute("SELECT document,protected FROM details WHERE kind='trace' AND owner_id=?", (start['trace_id'],)).fetchone()
        before = row()
        trusted = dict(self.scope, owner_observation={'joined_deployment_result': 'succeeded',
                        'completeness': 'complete', 'children_terminal': True})
        result = self.repository.record_owner(trusted, PRODUCER, parent, str(uuid.uuid4()), 0,
                      make_record({'kind': 'projection', 'projection': projection}))
        self.assertEqual(result['code'], 'recorded')
        after = row()
        self.assertEqual(before[0], after[0]); self.assertEqual((before[1], after[1]), (1, 0))
        self.assertEqual(self.repository.read(self.scope, trace_id=start['trace_id'])['trace'], document)
        self.assertEqual(self.write(start, dict(self.finish(), command_result='failed'))['code'], 'trace_request_conflict')

    def test_expiry_keeps_original_trace_and_parent_guards(self):
        with patch.object(owner, '_now', return_value='2000-01-01T00:00:00Z'):
            start, projection = self.attach()
            trusted = dict(self.scope, owner_observation={'joined_deployment_result': 'succeeded',
                           'completeness': 'complete', 'children_terminal': True})
            self.assertEqual(self.write(start, self.finish(), scope=trusted)['code'], 'recorded')
        self.start()
        read = self.repository.read(self.scope, trace_request_id=start['trace_request_id'])
        self.assertEqual(read['state'], 'expired')
        self.assertEqual(read['guard']['trace_id'], start['trace_id'])
        self.assertEqual(self.repository.start(self.scope, start['trace_request_id'], make_start())['code'], 'trace_expired')
        parent = projection['run']['parent_request_id']
        self.assertEqual(self.repository.read_owner(self.scope, PRODUCER, parent)['coverage']['state'], 'expired')
        result = self.repository.record_owner(self.scope, PRODUCER, parent, str(uuid.uuid4()), 0,
                        make_record({'kind': 'projection', 'projection': projection}))
        self.assertEqual(result['code'], 'trace_owner_expired')

    def test_proportional_caps_refuse_before_consuming_new_guards_or_receipts(self):
        # Exact 128/256/4096 limits were saturated in actual acceptance; small
        # values keep regression cost proportional while checking refusal paths.
        with patch.object(owner, 'PROTECTED_LIMIT', 1):
            start = self.start()
            self.assertEqual(self.repository.start(self.scope, str(uuid.uuid4()), make_start())['code'], 'trace_capacity')
        with patch.object(owner, 'RECEIPT_LIMIT', 2):
            self.assertEqual(self.write(start, self.stage('one'))['code'], 'recorded')
            self.assertEqual(self.write(start, self.stage('two'))['code'], 'recorded')
            self.assert_refusal_unchanged(start, self.stage('three'), 'trace_capacity')
        with patch.object(owner, 'GUARD_LIMIT', 1):
            self.assertEqual(self.repository.start(self.scope, str(uuid.uuid4()), make_start())['code'], 'trace_capacity')

    def test_event_ring_uses_owner_sequence_and_counts_omitted_events(self):
        start = self.start()
        for number in range(67):
            self.assertEqual(self.write(start, self.stage(f'observation {number}'))['code'], 'recorded')
        value = self.repository.read(self.scope, trace_id=start['trace_id'])['trace']
        self.assertEqual(len(value['events']), 64)
        self.assertEqual(value['omitted_events'], 3)
        self.assertEqual([value['events'][0]['seq'], value['events'][-1]['seq']], [4, 67])
        self.assertEqual(self.write(start, self.stage('observation 66'))['code'], 'recorded')
        self.assertEqual(self.repository.read(self.scope, trace_id=start['trace_id'])['trace']['events'], value['events'])

    def test_corrupt_receipt_is_not_returned_as_arbitrary_owner_data(self):
        _, projection = self.attach(); parent = projection['run']['parent_request_id']; publication = str(uuid.uuid4())
        self.repository.record_owner(self.scope, PRODUCER, parent, publication, 0,
            make_record({'kind': 'projection', 'projection': projection}))
        with sqlite3.connect(self.repository.path) as conn:
            row = conn.execute("SELECT rowid,document FROM receipts WHERE kind='parent'").fetchone()
            value = json.loads(row[1]); value['untrusted'] = 'fixture-private-sentinel'
            conn.execute('UPDATE receipts SET document=? WHERE rowid=?', (json.dumps(value), row[0]))
        result = self.repository.read_owner(self.scope, PRODUCER, parent, publication)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'trace_contract_invalid')
        self.assertNotIn('fixture-private-sentinel', json.dumps(result))

    def test_expired_budget_stops_before_sqlite_and_busy_wait_uses_remaining_time(self):
        start = self.start()
        result = self.repository.read(self.scope, trace_id=start['trace_id'], budget=TraceQueryBudget(time.monotonic() - 1))
        self.assertEqual(result['reason']['code'], 'budget_exhausted')
        with sqlite3.connect(self.repository.path, isolation_level=None) as conn:
            conn.execute('BEGIN EXCLUSIVE')
            began = time.monotonic()
            try:
                result = self.repository.read(self.scope, trace_id=start['trace_id'], budget=TraceQueryBudget(began + 0.02))
            finally:
                conn.rollback()
        self.assertLess(time.monotonic() - began, 1)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['reason']['code'], 'budget_exhausted')

    def test_parent_receipt_capacity_keeps_existing_replay_available(self):
        _, projection = self.attach(); parent = projection['run']['parent_request_id']
        record = make_record({'kind': 'projection', 'projection': projection})
        publication = str(uuid.uuid4())
        with patch.object(owner, 'RECEIPT_LIMIT', 1):
            first = self.repository.record_owner(self.scope, PRODUCER, parent, publication, 0, record)
            self.assertEqual(first['code'], 'recorded')
            self.assertEqual(self.repository.record_owner(self.scope, PRODUCER, parent, str(uuid.uuid4()), 1, record)['code'], 'trace_capacity')
            self.assertEqual(self.repository.record_owner(self.scope, PRODUCER, parent, publication, 999, record), first)

    def test_terminal_scope_and_global_retention_preserve_deny_only_guards(self):
        with patch.object(owner, 'SCOPE_LIMIT', 2), patch.object(owner, 'TERMINAL_LIMIT', 3):
            starts = []
            for number, environment in enumerate(('development',) * 3 + ('other',) * 3):
                with patch.object(owner, '_now', return_value=f'2099-01-01T00:00:00.{number:06d}Z'):
                    start = self.start(environment=environment)
                    self.assertEqual(self.write(start, self.finish())['code'], 'recorded')
                    starts.append(start)
            with sqlite3.connect(self.repository.path) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM guards').fetchone()[0], 6)
                # These are maxima: simultaneous per-scope/global pruning may retain fewer.
                self.assertLessEqual(conn.execute('SELECT count(*) FROM details').fetchone()[0], 3)
            first = self.repository.read(self.scope, trace_id=starts[0]['trace_id'])
            self.assertEqual(first['state'], 'expired')
            self.assertEqual(first['guard']['trace_request_id'], starts[0]['trace_request_id'])
            for start in starts[-2:]:
                self.assertEqual(self.repository.read(self.scope, trace_id=start['trace_id'])['state'], 'retained')
