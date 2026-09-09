"""Observed CLI/MCP parity with isolated diagnostic history and no live owners."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from sandbox.delivery.repository import DeliveryRepository
from tests.subprocess_support import run_test_process
from tests.test_delivery_service import delivery_fixture, retain_fixture


ROOT = Path(__file__).resolve().parents[1]
MCP_PROGRAM = """
import importlib.util
import json
from pathlib import Path
import sys
from sandbox.delivery.context import build_delivery_service
from sandbox.delivery.trace_context import build_trace_service
source = Path(sys.argv[1]) / 'mcp/wp-server/tools/delivery.py'
spec = importlib.util.spec_from_file_location('delivery_parity_mcp', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.configure(delivery_service_factory=build_delivery_service, trace_service_factory=build_trace_service)
print(json.dumps(module.delivery_inspect(**json.loads(sys.argv[2])), sort_keys=True))
"""


class TestDeliveryCliMcp(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='delivery-cli-mcp-')
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / 'home'
        self.project = Path(self.directory.name) / 'project'
        self.project.mkdir()
        self.repository = DeliveryRepository(self.home)
        self.success = retain_fixture(self.repository, delivery_fixture(self.project, 'fixture-a'))
        self.failure = retain_fixture(self.repository, delivery_fixture(self.project, 'fixture-b', succeeded=False))

    def run_child(self, argv):
        # Never enumerate or pass through the parent process environment.
        return run_test_process(argv, cwd=ROOT, capture_output=True, text=True,
            env={'SANDBOX_HOME': str(self.home), 'PYTHONPATH': str(ROOT),
                 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=45)

    def cli(self, *args, remote='fixture-remote'):
        return self.run_child([str(ROOT / 'sb'), 'delivery', 'inspect',
            '--project-dir', str(self.project), '--remote', remote, '--label', 'default', '--json', *args])

    def mcp(self, **selectors):
        request = {'project_dir': str(self.project), 'remote': 'fixture-remote', 'label': 'default'}
        request.update(selectors)
        process = self.run_child([sys.executable, '-c', MCP_PROGRAM, str(ROOT), json.dumps(request)])
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def inventory(self):
        return {str(path.relative_to(self.home)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.home.rglob('*') if path.is_file()}

    def test_failed_delivery_is_a_serviced_query_and_matches_mcp(self):
        before = self.inventory()
        process = self.cli('--request-id', 'fixture-b')
        self.assertEqual(process.returncode, 0, process.stderr)
        cli = json.loads(process.stdout)
        mcp = self.mcp(request_id='fixture-b')
        self.assertTrue(cli['ok'])
        self.assertFalse(cli['selected_operation']['delivery_succeeded'])
        self.assertEqual(cli['selected_operation']['operation_id'], self.failure['operation_id'])
        self.assertEqual(cli['latest_retained_complete_success']['operation_id'], self.success['operation_id'])
        self.assertIsNone(cli['current_observation'])
        cli.pop('recorded_at')
        mcp.pop('recorded_at')
        self.assertEqual(cli, mcp)
        self.assertEqual(self.inventory(), before)

    def test_namespaced_owner_request_is_queryable_through_cli_and_mcp(self):
        request_id = 'activate/v2-cli'
        retained = retain_fixture(self.repository, delivery_fixture(self.project, request_id))
        before = self.inventory()
        process = self.cli('--request-id', request_id)
        self.assertEqual(process.returncode, 0, process.stderr)
        cli = json.loads(process.stdout)
        mcp = self.mcp(request_id=request_id)
        self.assertEqual(cli['selected_operation']['operation_id'], retained['operation_id'])
        self.assertEqual(cli['selected_operation']['request_id'], request_id)
        cli.pop('recorded_at')
        mcp.pop('recorded_at')
        self.assertEqual(cli, mcp)
        self.assertEqual(self.inventory(), before)

    def test_missing_history_is_serviced_without_creating_owner_state(self):
        before = self.inventory()
        process = self.cli(remote='missing-remote')
        self.assertEqual(process.returncode, 0, process.stderr)
        projection = json.loads(process.stdout)
        self.assertTrue(projection['ok'])
        self.assertIsNone(projection['latest_attempt'])
        self.assertIsNone(projection['latest_retained_complete_success'])
        self.assertEqual(projection['history']['returned_count'], 0)
        # Owner readers may describe partial legacy history, but cannot invent rows.
        self.assertIn(projection['history']['completeness'], {'missing', 'partial'})
        self.assertEqual(self.inventory(), before)

    def test_cli_cursor_continues_same_history_snapshot(self):
        first_process = self.cli('--limit', '1')
        self.assertEqual(first_process.returncode, 0, first_process.stderr)
        first = json.loads(first_process.stdout)
        cursor = first['history']['next_cursor']
        self.assertIsNotNone(cursor)
        second_process = self.cli('--limit', '1', '--cursor', cursor)
        self.assertEqual(second_process.returncode, 0, second_process.stderr)
        second = json.loads(second_process.stdout)
        self.assertEqual(first['history']['operations'][0]['operation_id'], self.failure['operation_id'])
        self.assertEqual(second['history']['operations'][0]['operation_id'], self.success['operation_id'])
        self.assertIsNone(second['history']['next_cursor'])

    def test_observe_with_cursor_is_refused_before_any_owner_observation(self):
        first = json.loads(self.cli('--limit', '1').stdout)
        cursor = first['history']['next_cursor']
        before = self.inventory()
        process = self.cli('--observe', '--cursor', cursor)
        self.assertEqual(process.returncode, 1, process.stderr)
        cli = json.loads(process.stdout)
        mcp = self.mcp(observe=True, cursor=cursor)
        self.assertFalse(cli['ok'])
        self.assertEqual(cli['error']['code'], 'delivery_contract_invalid')
        self.assertEqual(cli, mcp)
        self.assertEqual(self.inventory(), before)

    def trace_cli(self, action, *args):
        project = [] if action == 'trace-capabilities' else ['--project-dir', str(self.project)]
        return self.run_child([str(ROOT / 'sb'), 'delivery', action, *project, '--json', *args])

    def trace_mcp(self, **selectors):
        request = {'project_dir': str(self.project), **selectors}
        process = self.run_child([sys.executable, '-c', MCP_PROGRAM, str(ROOT), json.dumps(request)])
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_trace_start_receipt_and_inspection_share_cli_mcp_meaning(self):
        from tests.test_delivery_trace_models import make_start, make_record
        capabilities = json.loads(self.trace_cli('trace-capabilities').stdout)
        intent = make_start(mode='preflight')
        intent['expected_runtime_revision'] = capabilities['runtime_revision']
        request_id = str(uuid.uuid4())
        started = self.trace_cli('trace-start', '--trace-request-id', request_id,
                                 '--input-json', json.dumps(intent))
        self.assertEqual(started.returncode, 0, started.stderr)
        start = json.loads(started.stdout)
        payload = make_record({'kind': 'stage', 'stage': 'bootstrap', 'status': 'running',
            'effect_state': 'not_started', 'reason': {'code': 'producer_recorded',
            'message': 'producer recorded'}, 'references': []})
        payload['expected_runtime_revision'] = capabilities['runtime_revision']
        mutation_id = str(uuid.uuid4())
        args = ('--trace-id', start['trace_id'], '--mutation-id', mutation_id,
                '--expected-sequence', '0', '--input-json', json.dumps(payload))
        written = self.trace_cli('trace-record', *args)
        self.assertEqual(written.returncode, 0, written.stderr)
        acknowledgement = json.loads(written.stdout)
        before = self.inventory()
        replay = self.trace_cli('trace-record', *args)
        self.assertEqual(json.loads(replay.stdout), acknowledgement)
        cli = json.loads(self.trace_cli('inspect', '--trace-id', start['trace_id'],
                                       '--mutation-id', mutation_id).stdout)
        mcp = self.trace_mcp(trace_id=start['trace_id'], mutation_id=mutation_id)
        self.assertEqual(cli['trace']['trace_request_id'], request_id)
        self.assertEqual(cli['mutation_receipt']['document_digest'], acknowledgement['document_digest'])
        cli.pop('recorded_at'); mcp.pop('recorded_at')
        self.assertEqual(cli, mcp)
        self.assertEqual(self.inventory(), before)

    def test_trace_selector_union_refuses_before_initializing_trace_store(self):
        before = self.inventory()
        trace_id = str(uuid.uuid4())
        process = self.trace_cli('inspect', '--trace-id', trace_id, '--remote', 'fixture-remote')
        self.assertEqual(process.returncode, 1, process.stderr)
        cli = json.loads(process.stdout)
        mcp = self.trace_mcp(trace_id=trace_id, remote='fixture-remote')
        self.assertFalse(cli['ok'])
        self.assertEqual(cli['error']['code'], 'trace_contract_invalid')
        cli.pop('recorded_at'); mcp.pop('recorded_at')
        self.assertEqual(cli, mcp)
        self.assertEqual(self.inventory(), before)


if __name__ == '__main__':
    unittest.main()
