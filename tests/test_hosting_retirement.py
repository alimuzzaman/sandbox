"""Offline retirement outcomes after delivery effects and host lock contention."""
import copy
import io
import json
import tempfile
import types
import unittest
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from sandbox.commands import hosting as command
from sandbox.delivery import hosting as delivery
from sandbox.delivery.models import DeliveryError, request_scope
from sandbox.delivery.repository import DeliveryRepository
from tests.test_delivery_models import make_operation, make_target


class RetirementOutcomes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.repository = DeliveryRepository(self.home)
        self.target = make_target()
        self.scope = request_scope(self.target)
        operation = make_operation('abandoned-apply')
        self.repository.reserve_request(
            self.scope, operation['request_id'], operation['operation_id'],
            operation['intent_digest'], operation=operation)
        self.original_operation = copy.deepcopy(self.retained())
        self.validated = {'project': 'site', 'project_root': '/fixture/project',
                          'environment': 'qa'}
        self.key = command.hosting.state_key('fixture-remote', self.validated)
        self.state = {'hosts': {self.key: {
            'active_operation': 'active', 'recovery_uncertainty': 'unknown',
            'image_activation': {'active': 'candidate'},
            'staged_revision': 'b' * 40, 'recorded_revision': 'a' * 40}}}
        self.before = copy.deepcopy(self.state)
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.lock = Mock(return_value=nullcontext())
        self.save = Mock()
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(delivery, 'RUNTIME_DIR', self.home / 'runtime'))
        stack.enter_context(patch.object(delivery, 'target_for_project', return_value=self.target))
        stack.enter_context(patch.object(command, '_recovery_job_lookup',
                                        return_value={'lifecycle': 'failed'}))
        self.load = stack.enter_context(patch.object(command.hosting, 'load_host_state',
                                                     return_value=self.state))
        stack.enter_context(patch.object(command.hosting, 'save_host_state', self.save))
        stack.enter_context(patch('sandbox.hosting.recovery.repository.RecoveryRepository.state_lock',
                                 self.lock))
        stack.enter_context(redirect_stdout(self.output))
        stack.enter_context(redirect_stderr(self.errors))

    def invoke(self, json_mode=True):
        command._cmd_host_retire_delivery(
            self.validated, 'fixture-remote', types.SimpleNamespace(
                original_request_id='abandoned-apply', confirm=True, json=json_mode, lock_wait=1))

    def retained(self):
        return self.repository.lookup_request(self.scope, 'abandoned-apply')['operation']

    def exhaust_lock(self):
        @contextmanager
        def exhausted(**kwargs):
            raise TimeoutError('private lock path must not escape')
            yield
        self.lock.side_effect = exhausted

    def assert_partial(self):
        self.assertEqual(self.retained()['execution_state'], 'interrupted')
        self.assertIsNotNone(self.retained()['terminal_snapshot_digest'])
        self.assertFalse(self.retained()['delivery_succeeded'])
        self.assertEqual(self.retained()['evidence_completeness'], 'missing')
        self.assertEqual(self.state, self.before)
        self.save.assert_not_called()
        self.lock.assert_called_once_with(timeout_seconds=1)
        self.assertEqual(self.load.call_count, 1)
        self.assertNotIn('private lock path', self.output.getvalue() + self.errors.getvalue())

    def test_second_lock_timeout_json_retains_delivery_and_host_fences(self):
        self.exhaust_lock()
        with self.assertRaises(SystemExit) as stopped:
            self.invoke()
        self.assertEqual(stopped.exception.code, 1)
        payload = json.loads(self.output.getvalue())
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['code'], 'operation_busy')
        self.assertEqual(payload['operation'], 'retire-delivery')
        self.assertEqual(payload['original_request_id'], 'abandoned-apply')
        self.assertTrue(payload['partial'])
        self.assertEqual(payload['retired']['execution_state'], 'interrupted')
        self.assertEqual(payload['host_state_cleanup'], 'pending')
        self.assertIn('inspect', payload['guidance'])
        self.assertEqual(self.errors.getvalue(), '')
        self.assert_partial()

    def test_second_lock_timeout_text_is_bounded_and_truthful(self):
        self.exhaust_lock()
        with self.assertRaises(SystemExit) as stopped:
            self.invoke(False)
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(self.output.getvalue(), '')
        self.assertIn('operation_busy', self.errors.getvalue())
        self.assertIn('retirement was recorded', self.errors.getvalue())
        self.assertIn('cleanup is pending', self.errors.getvalue())
        self.assertIn('inspect', self.errors.getvalue())
        self.assertLess(len(self.errors.getvalue()), 400)
        self.assert_partial()

    def test_success_json_still_cleans_host_state(self):
        self.invoke()
        payload = json.loads(self.output.getvalue())
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['retired']['execution_state'], 'interrupted')
        record = self.state['hosts'][self.key]
        self.assertIsNone(record['active_operation'])
        self.assertIsNone(record['recovery_uncertainty'])
        self.assertIsNone(record['image_activation']['active'])
        self.assertIsNone(record['staged_revision'])
        self.assertTrue(record['retired_unproven_runtime'])
        self.save.assert_called_once_with(self.state)
        self.assertEqual(self.errors.getvalue(), '')

    def test_success_text_still_reports_interrupted_missing_evidence(self):
        self.invoke(False)
        self.assertEqual(self.output.getvalue(),
                         'retired abandoned-apply as interrupted (evidence missing)\n')
        self.assertEqual(self.errors.getvalue(), '')
        self.save.assert_called_once_with(self.state)

    def test_delivery_refusal_does_not_claim_partial_retirement(self):
        with patch.object(delivery, 'retire_interrupted_operation',
                          side_effect=DeliveryError('authority_pending')), \
                self.assertRaises(SystemExit) as stopped:
            self.invoke()
        self.assertEqual(stopped.exception.code, 1)
        payload = json.loads(self.output.getvalue())
        self.assertEqual(payload['code'], 'authority_pending')
        self.assertNotIn('partial', payload)
        self.assertNotIn('retired', payload)
        self.assertEqual(self.retained(), self.original_operation)
        self.lock.assert_not_called()
        self.save.assert_not_called()

    def test_destroy_fence_written_between_phases_is_preserved(self):
        fenced = copy.deepcopy(self.state)
        fenced['hosts'][self.key]['active_operation'] = {'action': 'destroy'}
        self.load.side_effect = [self.state, fenced]
        with self.assertRaises(SystemExit) as stopped:
            self.invoke(False)
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn('its fence was kept', self.errors.getvalue())
        self.assertEqual(fenced['hosts'][self.key]['active_operation'], {'action': 'destroy'})
        self.assertEqual(self.retained()['execution_state'], 'interrupted')
        self.save.assert_not_called()
