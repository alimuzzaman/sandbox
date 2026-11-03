"""Storage recovery regressions, with synthetic providers and no remote effects."""
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from sandbox.resources.plans import PlanStore
from sandbox.resources.service import ResourceService, enrich_outcomes
from tests.resource_fixtures import observation, NOW
from tests.test_resource_service import FakeAdapter
from tests import test_resource_remote as remote_tests


class StorageRecoveryTests(unittest.TestCase):
    def test_unmeasured_volume_category_is_partial(self):
        rows = (observation('measured', kind='volume'),
                observation('missing', kind='volume', size_bytes=None))
        outcome, = enrich_outcomes(({'category': 'docker_volumes', 'status': 'complete'},), rows)
        self.assertEqual(outcome['status'], 'partial')
        self.assertEqual(outcome['unmeasured_count'], 1)

    def test_cache_plan_excludes_containers_and_networks(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('cache'), observation('stopped', kind='container'),
                                   observation('network', kind='network')))
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            payload = service.plan('cache')
            self.assertEqual([row['resource_id'] for row in payload['data']['candidates']], ['cache'])

    def test_exact_selection_cannot_expand_or_skip_invariants(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('a'), observation('b'),
                                   observation('active', classification='active')))
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            for ids in (('unknown',), ('active',), ('a', 'a'), ()):
                with self.subTest(ids=ids):
                    self.assertFalse(service.plan('cache', resource_ids=ids)['ok'])
            payload = service.plan('cache', resource_ids=('b',))
            self.assertTrue(payload['ok'])
            self.assertEqual([row['resource_id'] for row in payload['data']['candidates']], ['b'])
            adapter.revalidate_map['b'] = replace(adapter.resources[1], evidence=('changed',))
            receipt = service.cleanup(payload['data']['plan_id'], confirm=True)
            self.assertEqual(adapter.removed, [])
            self.assertEqual(receipt['status'], 'partial')

    def test_incomplete_cache_does_not_prevent_larger_retry(self):
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        ns['directory_cache_read'] = lambda: {'mounts': {'/': {
            'rows': [[1, '/var']], 'complete': False, 'created_at': ns['time'].time()}}}
        ns['directory_cache_write'] = lambda payload: True
        ns['walk_rows'] = lambda *a: ([(2, '/')], True)
        result = ns['directory_index']('/', ['du'], 1024, 5, ())
        self.assertTrue(result['complete'])
        self.assertEqual(result['source'], 'scan')

    def test_timeout_guidance_includes_required_request_identity(self):
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        outcome = ns['budget_outcome']('directory', 'timed_out')
        self.assertIn('--request-id', outcome['next_step'])

    def test_failed_revalidation_is_not_absence_or_deletion_failure(self):
        from sandbox.resources.service import ResourceError
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('cache'),))
            service = ResourceService(adapter, PlanStore(Path(temp), clock=lambda: NOW), clock=lambda: NOW)
            plan = service.plan('cache')
            def unavailable(candidate):
                raise ResourceError('probe incomplete', 'measurement_unavailable')
            adapter.revalidate = unavailable
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(adapter.removed, [])
            self.assertEqual(receipt['status'], 'partial')
            self.assertEqual(receipt['data']['outcomes'][0]['status'], 'skipped')
            self.assertEqual(receipt['data']['outcomes'][0]['reason'], 'revalidation_unavailable')

    def test_remote_revalidation_requires_complete_target_evidence(self):
        from sandbox.resources.models import CleanupCandidate
        from sandbox.resources.remote import RemoteResourceAdapter
        from sandbox.services.process import ProcessResult
        import json
        candidate = CleanupCandidate.from_observation(observation('cache'))
        for code, payload in ((124, {}), (0, {}), (0, {
            'identity': 'host', 'resources': [],
            'category_outcomes': [{'category': 'sandbox_runtime', 'status': 'timed_out'}]})):
            with self.subTest(code=code, payload=payload):
                adapter = RemoteResourceAdapter('remote-a',
                    remote_lookup=lambda name: {'provisioned': True, 'ssh': 'host'},
                    service_request=lambda *a, **k: ProcessResult(('synthetic',), code, json.dumps(payload), ''))
                with self.assertRaises(RuntimeError):
                    adapter.revalidate(candidate)

    def test_local_revalidation_requires_complete_category(self):
        from sandbox.resources.adapters import LocalResourceAdapter, ProviderSnapshot
        from sandbox.resources.models import CleanupCandidate
        adapter = object.__new__(LocalResourceAdapter)
        item = observation('cache')
        adapter.observe = lambda **kw: ProviderSnapshot(FakeAdapter().target(), {}, (),
            ({'category': 'sandbox_runtime', 'status': 'timed_out'},), None)
        with self.assertRaises(RuntimeError):
            adapter.revalidate(CleanupCandidate.from_observation(item))

    def test_detached_compatibility_refusal_preserves_reconcile_and_no_effects(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sandbox.readiness.errors import RemoteNotReadyError
        from sandbox.resources import detached
        class TargetService:
            def resolve(self, request):
                raise RemoteNotReadyError({'aspect': 'runtime_compatibility',
                    'reason': 'protocol_newer', 'remedy': './sb remote service migrate remote-a --confirm'})
        with patch('sandbox.application.context.durable_job_dependencies',
                   return_value={'target_service': TargetService()}):
            payload = detached.start(SimpleNamespace(budget=120, request_id='original-request', remote='remote-a'))
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['status'], 'blocked')
        self.assertNotIn('job_id', payload)
        refusal = payload['data']['refusal']
        self.assertEqual(refusal['side_effects'], {'staging_started': False, 'bytes_transferred': 0})
        self.assertEqual(refusal['remedy'], './sb remote service migrate remote-a --confirm')
