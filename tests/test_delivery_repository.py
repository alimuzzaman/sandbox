"""Regressions from real public-owner delivery journal exercises."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from sandbox.delivery.models import DeliveryError, intent_digest, request_scope
from sandbox.delivery.repository import DeliveryRepository
from tests.test_delivery_models import make_operation, make_target, make_terminal


class DeliveryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        self.repository = DeliveryRepository(self.home)
        self.scope = request_scope(make_target())

    def reserve(self, operation):
        return self.repository.reserve_request(
            request_scope(operation['target']), operation['request_id'],
            operation['operation_id'], operation['intent_digest'],
            operation=operation,
        )

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(DeliveryError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def all_history(self, scope):
        result = []
        cursor = None
        while True:
            page = self.repository.history_scope(scope, limit=50, cursor=cursor)
            result.extend(page['operations'])
            cursor = page['next_cursor']
            if cursor is None:
                return result

    def test_missing_and_existing_reads_do_not_mutate_state(self):
        operation = make_operation('read-only')
        self.assertEqual(self.repository.lookup_request(self.scope, 'absent')['status'], 'missing')
        self.assertEqual(self.repository.history_scope(self.scope)['completeness'], 'missing')
        self.assertEqual(self.repository.preflight_request(
            self.scope, operation['request_id'], operation['operation_id'],
            operation['intent_digest'],
        )['status'], 'available')
        self.assertFalse(self.home.exists())
        self.reserve(operation)
        database = self.home / 'runtime/delivery/outcomes.sqlite3'
        before = (database.read_bytes(), database.stat().st_mtime_ns)
        self.repository.lookup_request(self.scope, 'read-only')
        self.repository.history_scope(self.scope)
        self.repository.get(operation['operation_id'])
        self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns), before)

    def test_owner_request_ids_preserve_namespaces_and_full_length_on_replay(self):
        for request_id in ('activate/v2-cli', 'activate/' + 'a' * 247):
            with self.subTest(request_length=len(request_id)):
                operation = make_operation(request_id)
                self.reserve(operation)
                retained = self.repository.lookup_request(self.scope, request_id)
                self.assertEqual(retained['operation']['request_id'], request_id)
                self.assertEqual(self.reserve(operation)['status'], 'existing')
                self.assertNotEqual(
                    self.repository.lookup_request(self.scope, request_id.replace('/', ':'))['status'],
                    'existing')

    def test_replay_returns_original_and_changed_intent_conflicts(self):
        first = make_operation('repeat')
        self.assertEqual(self.reserve(first)['status'], 'reserved')
        retry = make_operation('repeat', stamp='2026-01-02T00:00:00Z')
        repeated = self.reserve(retry)
        self.assertEqual(repeated['status'], 'existing')
        self.assertEqual(repeated['operation_id'], first['operation_id'])
        retry['requested_outcome']['application']['commit'] = 'e' * 40
        retry['intent_digest'] = intent_digest(retry['requested_outcome'])
        self.assertEqual(self.reserve(retry)['status'], 'request_conflict')

    def test_terminal_is_immutable_and_later_failure_preserves_success(self):
        first = make_operation('success')
        self.reserve(first)
        stored = self.repository.write_operation(make_terminal(first))
        self.assertEqual(self.repository.write_operation(stored), stored)
        changed = deepcopy(stored)
        changed['reason'] = {'code': 'failed', 'message': 'Different terminal evidence.'}
        self.assert_code('delivery_terminal_conflict', self.repository.write_operation, changed)
        later = make_terminal(make_operation('failure'), succeeded=False)
        self.reserve(later)
        history = self.repository.history_scope(self.scope)
        self.assertEqual(history['latest_attempt']['operation_id'], later['operation_id'])
        self.assertEqual(history['latest_retained_complete_success']['operation_id'], first['operation_id'])

    def test_hosting_checkpoint_history_is_bounded_and_terminal_replay_is_pure(self):
        from sandbox.delivery.hosting import HostingAttempt
        attempt = HostingAttempt.retained(make_operation('hosting-events'))
        attempt.repository = self.repository
        attempt.reserved = False
        attempt.reserve()
        attempt.checkpoint('source', 'source_publication', state='configured')
        first = len(attempt.operation['events'])
        attempt.checkpoint('source', 'source_publication', state='configured')
        self.assertEqual(len(attempt.operation['events']), first)
        for index in range(70):
            attempt.checkpoint('runtime', 'runtime_probe',
                               state='observed' if index % 2 else 'configured')
        attempt.finish(False, failure_stage='runtime')
        terminal = self.repository.get(attempt.operation['operation_id'])
        self.assertEqual(len(terminal['events']), 64)
        self.assertGreater(terminal['history']['omitted_events'], 0)
        self.assertEqual(terminal['events'][-1]['phase'], 'terminal')
        self.assertEqual(terminal['reason']['code'], 'failed')
        before = self.repository.path.read_bytes()
        self.assertEqual(self.repository.write_operation(deepcopy(terminal)), terminal)
        self.assertEqual(self.repository.path.read_bytes(), before)
        changed = deepcopy(terminal)
        changed['reason']['message'] = 'Changed terminal explanation.'
        self.assert_code('delivery_terminal_conflict', self.repository.write_operation, changed)
        self.assertEqual(self.repository.path.read_bytes(), before)

    def test_pruned_details_keep_permanent_replay_guard(self):
        expired = make_terminal(make_operation('expired', stamp='2000-01-01T00:00:00Z'))
        self.reserve(expired)
        replay = make_operation('expired')
        self.assertEqual(self.reserve(replay)['status'], 'delivery_request_expired')
        self.assertEqual(self.repository.lookup_request(self.scope, 'expired')['operation_id'], expired['operation_id'])
        replay['requested_outcome']['application']['commit'] = 'e' * 40
        replay['intent_digest'] = intent_digest(replay['requested_outcome'])
        self.assertEqual(self.reserve(replay)['status'], 'request_conflict')

    def test_cursor_snapshot_excludes_new_rows_and_rejects_other_scope(self):
        first = make_terminal(make_operation('first'))
        second = make_terminal(make_operation('second'))
        self.reserve(first)
        self.reserve(second)
        page = self.repository.history_scope(self.scope, limit=1)
        self.assertIsNotNone(page['next_cursor'])
        self.reserve(make_terminal(make_operation('newer')))
        next_page = self.repository.history_scope(self.scope, limit=1, cursor=page['next_cursor'])
        self.assertEqual(next_page['operations'][0]['operation_id'], first['operation_id'])
        self.assertEqual(next_page['latest_attempt']['operation_id'], second['operation_id'])
        self.assert_code('delivery_cursor_invalid', self.repository.history_scope,
                         request_scope(make_target('other')), cursor=page['next_cursor'])

    def test_retention_invalidates_cursor_and_preserves_open_record(self):
        self.reserve(make_terminal(make_operation('first')))
        self.reserve(make_terminal(make_operation('second')))
        page = self.repository.history_scope(self.scope, limit=1)
        opened = make_operation('open')
        self.reserve(opened)
        for index in range(65):
            self.reserve(make_terminal(make_operation(f'later-{index}')))
        self.assertEqual(len(self.all_history(self.scope)), 65)
        self.assertEqual(self.repository.lookup_request(self.scope, 'open')['status'], 'existing')
        self.assertEqual(self.repository.lookup_request(self.scope, 'first')['status'], 'delivery_request_expired')
        self.assert_code('delivery_cursor_expired', self.repository.history_scope,
                         self.scope, cursor=page['next_cursor'])

    def test_pinned_old_failure_survives_terminal_retention(self):
        pinned = make_terminal(make_operation('pinned', stamp='2000-01-01T00:00:00Z'), succeeded=False)
        pinned['pinned_reason'] = {'code': 'authority_pending', 'message': 'Awaiting review.'}
        self.reserve(pinned)
        for index in range(65):
            self.reserve(make_terminal(make_operation(f'later-{index}')))
        self.assertEqual(len(self.all_history(self.scope)), 65)
        self.assertEqual(self.repository.lookup_request(self.scope, 'pinned')['status'], 'existing')

    def test_global_terminal_retention_is_bounded(self):
        environments = [f'qa-{index}' for index in range(9)]
        for index in range(513):
            self.reserve(make_terminal(make_operation(
                f'global-{index}', environment=environments[index % 9],
            )))
        total = sum(len(self.all_history(request_scope(make_target(environment))))
                    for environment in environments)
        self.assertEqual(total, 512)
        self.assertEqual(self.repository.lookup_request(
            request_scope(make_target(environments[0])), 'global-0',
        )['status'], 'delivery_request_expired')

    def test_open_capacity_rejects_new_requests_but_allows_replay(self):
        for index in range(128):
            self.reserve(make_operation(f'open-{index}'))
        self.assert_code('delivery_capacity', self.reserve, make_operation('overflow'))
        self.assertEqual(self.reserve(make_operation('open-0'))['status'], 'existing')

    def test_permanent_guard_capacity_does_not_reopen_expired_requests(self):
        for index in range(4096):
            self.reserve(make_terminal(make_operation(
                f'guard-{index}', stamp='2000-01-01T00:00:00Z',
            )))
        self.assert_code('delivery_request_capacity', self.reserve, make_operation('overflow'))
        self.assertEqual(self.reserve(make_operation('guard-0'))['status'], 'delivery_request_expired')


if __name__ == '__main__':
    unittest.main()
