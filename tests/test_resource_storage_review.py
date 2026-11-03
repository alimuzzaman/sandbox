"""Regressions and acceptance branches from the independent storage review."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from sandbox.resources.models import CleanupCandidate, CleanupRun
from sandbox.resources.plans import PlanStore
from sandbox.resources.service import ResourceService
from sandbox.resources.remote import RemoteResourceAdapter
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW, observation, target, deep_attribution
from tests.test_resource_service import FakeAdapter
from tests import test_resource_remote as remote_tests


class StorageReviewTests(unittest.TestCase):
    def service(self, adapter, directory):
        return ResourceService(adapter, PlanStore(Path(directory), clock=lambda: NOW), clock=lambda: NOW)

    def test_after_capacity_none_preserves_outcomes_and_is_indeterminate(self):
        for missing in (None, {}, {'available_bytes': 1, 'used_bytes': None}):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                adapter = FakeAdapter((observation('cache'),))
                original = adapter.observe
                def observe(**kw):
                    snap = original(**{key: value for key, value in kw.items() if key != 'cancelled'})
                    return replace(snap, capacity=missing) if adapter.removed else snap
                adapter.observe = observe
                service = self.service(adapter, temp)
                plan = service.plan('cache')
                receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
                self.assertFalse(receipt['ok'])
                self.assertEqual(receipt['status'], 'indeterminate')
                self.assertEqual(receipt['data']['outcomes'][0]['status'], 'removed')
                self.assertIsNone(receipt['data']['observed_reclaimed_bytes'])
                self.assertEqual(service.plan_store.load(plan['data']['plan_id']).state, 'indeterminate')

    def test_plan_selector_order_survives_apply_without_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('small-cache', size_bytes=100),
                                   observation('large-cache', size_bytes=500)), capacity_scope_id='root')
            original = adapter.observe
            def observe(*, thorough, budget_seconds, progress=None, focus=None, deep=False):
                snap = original(thorough=thorough, budget_seconds=budget_seconds,
                                progress=progress, focus=focus, deep=deep)
                available = 50 if adapter.removed else 10
                return replace(snap, capacity={**snap.capacity, 'available_bytes': available,
                    'used_bytes': snap.capacity['total_bytes'] - snap.capacity['reserved_bytes'] - available})
            adapter.observe = observe
            service = self.service(adapter, temp)
            plan = service.plan('cache', resource_ids=('small-cache', 'large-cache'))
            self.assertEqual([row['resource_id'] for row in plan['data']['candidates']],
                             ['small-cache', 'large-cache'])
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True,
                                      stop_at_available_bytes=40, capacity_scope_id='root')
            self.assertEqual(adapter.removed, ['small-cache'])
            self.assertTrue(receipt['ok'])

    def test_volume_identity_read_failure_is_not_deletion_failure(self):
        locator = 'sandbox-volume'
        for code, output in ((1, ''), (124, ''), (0, 'invalid json'), (0, '[]'),
                             (0, json.dumps([{'Name': locator}]))):
            with self.subTest(code=code, output=output):
                ns = remote_tests.TestRemoteProbeResilience()._probe_namespace({
                    'action': 'remove', 'kind': 'volume', 'locator': locator, 'budget_seconds': 60})
                identity = ns['rid']('volume', locator + '\0created')
                ns['REQUEST']['expected_resource_id'] = identity
                calls = []
                def run(argv, timeout):
                    calls.append(tuple(argv))
                    return code, output, ''
                ns['run'] = run
                row = observation(identity, kind='volume', classification='stale_candidate', locator=locator)
                candidate = CleanupCandidate.from_observation(row)
                def request(entry, command, *, input_data=None, timeout=0):
                    payload = json.loads(input_data)
                    if payload['action'] == 'observe':
                        result = {'identity': 'host', 'resources': [row.internal_dict()],
                                  'category_outcomes': [{'category': 'docker_volumes', 'status': 'complete'}]}
                    else:
                        result = ns['remove']()
                    return ProcessResult(('synthetic',), 0, json.dumps(result), '')
                adapter = RemoteResourceAdapter('remote-a', remote_lookup=lambda name: {'provisioned': True},
                                                service_request=request, clock=lambda: NOW)
                self.assertEqual(adapter.revalidate(candidate).resource_id, identity)
                outcome = adapter.remove(candidate)
                self.assertEqual(outcome.status, 'skipped')
                self.assertEqual(outcome.reason, 'revalidation_unavailable')
                self.assertEqual(calls, [('docker', 'volume', 'inspect', locator)])

    def test_volume_review_includes_running_and_stopped_references(self):
        from sandbox.resources.engine_review import review_metadata
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        containers = [{'Id': name, 'State': {'Running': running},
                       'Mounts': [{'Type': 'volume', 'Name': 'v'}]}
                      for name, running in (('running', True), ('stopped', False))]
        for factory in (review_metadata, ns['review_metadata']):
            result = factory('volume', {'Name': 'v'}, containers, False)
            self.assertEqual(result['container_ids'], ['running', 'stopped'])
            self.assertFalse(result['references_complete'])

    def test_image_digest_labels_and_creation_changes_invalidate_plan(self):
        item = replace(observation('image', kind='image', locator='image'), engine={
            'engine_id': 'image', 'created_at': 'old', 'aliases': ['dev:old'], 'digests': ['old'],
            'labels': {'stage': 'dev'}, 'labels_redacted': False,
            'references_complete': True, 'container_ids': []})
        candidate = CleanupCandidate.from_observation(item)
        self.assertTrue(ResourceService._still_matches(candidate, item))
        for change in ({'digests': ['new']}, {'labels': {'stage': 'prod'}}, {'created_at': 'new'}):
            with self.subTest(change=change):
                self.assertFalse(ResourceService._still_matches(candidate, replace(item, engine={**item.engine, **change})))

    def test_capacity_stop_uses_selected_deep_filesystem_not_root(self):
        for docker_available, root_available, removed in ((10, 50000, ['a']), (50, 10, [])):
            with self.subTest(docker_available=docker_available), tempfile.TemporaryDirectory() as temp:
                deep = deep_attribution()
                fs = replace(deep.filesystems[0], filesystem_id='docker-fs',
                             available_bytes=docker_available, used_bytes=100-docker_available)
                adapter = FakeAdapter((observation('a'), observation('b')),
                                      capacity_scope_id='root', deep_attribution=replace(deep, filesystems=(fs,)))
                original = adapter.observe
                def observe(*, thorough, budget_seconds, progress=None, focus=None, deep=False):
                    snap = original(thorough=thorough, budget_seconds=budget_seconds,
                                    progress=progress, focus=focus, deep=deep)
                    return replace(snap, capacity={**snap.capacity, 'available_bytes': root_available,
                        'used_bytes': snap.capacity['total_bytes']-snap.capacity['reserved_bytes']-root_available},
                        deep_attribution=None if adapter.removed else snap.deep_attribution)
                adapter.observe = observe
                service = self.service(adapter, temp)
                plan = service.plan('cache')
                receipt = service.cleanup(plan['data']['plan_id'], confirm=True, resource_ids=('a', 'b'),
                                          stop_at_available_bytes=40, capacity_scope_id='docker-fs')
                self.assertEqual(adapter.removed, removed)
                if removed:
                    self.assertEqual(receipt['status'], 'indeterminate')
                    self.assertEqual(receipt['data']['outcomes'][1]['reason'], 'capacity_evidence_unavailable')
                else:
                    self.assertTrue(receipt['ok'])

    def test_available_bytes_delta_is_signed_and_nullable(self):
        for before, after, delta in ((10, 3, -7), (3, 10, 7), (10, 10, 0), (10, None, None)):
            with self.subTest(before=before, after=after):
                run = CleanupRun(run_id='a'*32, plan_id='b'*32, target=target(), status='completed',
                    started_at=NOW, completed_at=NOW, planned_bytes=0, observed_reclaimed_bytes=0,
                    outcomes=(), capacity_before={'available_bytes': before},
                    capacity_after={'available_bytes': after} if after is not None else None)
                payload = run.to_dict()
                self.assertEqual(payload['available_bytes_delta'], delta)
                self.assertEqual(payload['observed_reclaimed_bytes'], 0)

    def test_credential_shaped_label_values_are_redacted(self):
        from sandbox.resources.engine_review import review_metadata
        ns = remote_tests.TestRemoteProbeResilience()._probe_namespace()
        for factory in (review_metadata, ns['review_metadata']):
            for labels in ({'auth': 'opaque-credential'}, {'trace': 'Basic ZHVtbXk6ZHVtbXk='},
                           {'trace': 'ghp_' + 'x'*36},
                           {'trace': '-----BEGIN PRIVATE KEY----- fixture -----END PRIVATE KEY-----'}):
                with self.subTest(labels=labels, factory=factory.__name__):
                    result = factory('image', {'Id': 'image', 'Config': {'Labels': labels}}, [], True)
                    self.assertTrue(result['labels_redacted'])
                    self.assertEqual(list(result['labels'].values()), ['[redacted]'])

    def test_final_observation_decoder_errors_preserve_persisted_outcomes(self):
        for malformed_kind, error_type in (('engine', ValueError), ('deep', TypeError)):
            with self.subTest(malformed_kind=malformed_kind), tempfile.TemporaryDirectory() as temp:
                adapter = FakeAdapter((observation('cache'),))
                adapter._target = target('remote-a', 'host')
                payload = {'identity': 'host', 'capacity': adapter.observe(thorough=False, budget_seconds=1).capacity,
                           'resources': [], 'category_outcomes': []}
                if malformed_kind == 'engine':
                    bad = observation('bad').internal_dict()
                    bad['engine'] = {'references_complete': 'unknown'}
                    payload['resources'] = [bad]
                else:
                    payload['deep_attribution'] = deep_attribution().to_dict()
                    payload['deep_attribution']['filesystems'][0]['mount_flags'] = 1
                remote = RemoteResourceAdapter('remote-a', remote_lookup=lambda name: {'provisioned': True},
                    service_request=lambda *a, **kw: ProcessResult(('synthetic',), 0, json.dumps(payload), ''),
                    clock=lambda: NOW)
                with self.assertRaises(error_type):
                    remote.observe(thorough=False, budget_seconds=1)
                original = adapter.observe
                def observe(**kw):
                    if adapter.removed:
                        return remote.observe(thorough=False, budget_seconds=1)
                    return original(**{key: value for key, value in kw.items() if key != 'cancelled'})
                adapter.observe = observe
                service = self.service(adapter, temp)
                plan = service.plan('cache')
                receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
                self.assertFalse(receipt['ok'])
                self.assertEqual(receipt['status'], 'indeterminate')
                self.assertEqual([row['status'] for row in receipt['data'].get('outcomes', [])], ['removed'])
                receipts = list(Path(temp).glob('*.run.json'))
                self.assertEqual(len(receipts), 1)
                self.assertEqual(json.loads(receipts[0].read_text())['outcomes'][0]['status'], 'removed')
                self.assertEqual(service.plan_store.load(plan['data']['plan_id']).state, 'indeterminate')

    def test_missing_removal_receipt_is_indeterminate_not_deletion_failure(self):
        candidate = CleanupCandidate.from_observation(observation('cache'))
        for code, output in ((1, ''), (0, 'invalid json'), (0, '[]'),
                             (0, json.dumps({'status': 'unknown'})),
                             (0, json.dumps({'status': 'removed'}))):
            with self.subTest(code=code, output=output):
                adapter = RemoteResourceAdapter('remote-a', remote_lookup=lambda name: {'provisioned': True},
                    service_request=lambda *a, **kw: ProcessResult(('synthetic',), code, output, ''), clock=lambda: NOW)
                outcome = adapter.remove(candidate)
                self.assertEqual(outcome.status, 'indeterminate')
                self.assertEqual(outcome.reason, 'cleanup_outcome_unavailable')
                self.assertIsNone(outcome.observed_bytes)

    def test_removal_exception_keeps_plan_indeterminate_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeAdapter((observation('cache'),))
            def remove(candidate):
                adapter.removed.append(candidate.resource_id)
                raise ConnectionError('response lost after effect')
            adapter.remove = remove
            service = self.service(adapter, temp)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(receipt['status'], 'indeterminate')
            self.assertFalse(receipt['ok'])
            self.assertEqual(receipt['data']['outcomes'][0]['status'], 'indeterminate')
            self.assertFalse(service.cleanup(plan['data']['plan_id'], confirm=True)['ok'])
            self.assertEqual(adapter.removed, ['cache'])

    def test_remote_unknown_effect_consumes_plan_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            row = observation('cache', kind='build_cache', locator='a'*24)
            mutations = []
            def request(entry, command, *, input_data=None, timeout=0):
                payload = json.loads(input_data)
                if payload['action'] == 'remove':
                    mutations.append(payload)
                    return ProcessResult(('synthetic',), 0, 'invalid receipt', '')
                return ProcessResult(('synthetic',), 0, json.dumps({
                    'identity': 'host', 'capacity': {'total_bytes': 100, 'used_bytes': 80,
                        'available_bytes': 20, 'reserved_bytes': 0},
                    'resources': [row.internal_dict()],
                    'category_outcomes': [{'category': 'docker_build_cache', 'status': 'complete'}],
                }), '')
            adapter = RemoteResourceAdapter('remote-a', remote_lookup=lambda name: {'provisioned': True},
                                            service_request=request, clock=lambda: NOW)
            service = self.service(adapter, temp)
            plan = service.plan('cache')
            receipt = service.cleanup(plan['data']['plan_id'], confirm=True)
            self.assertEqual(receipt['status'], 'indeterminate')
            self.assertFalse(receipt['ok'])
            self.assertEqual(receipt['data']['outcomes'][0]['status'], 'indeterminate')
            self.assertIsNone(receipt['data']['outcomes'][0]['observed_bytes'])
            self.assertEqual(service.plan_store.load(plan['data']['plan_id']).state, 'indeterminate')
            self.assertFalse(service.cleanup(plan['data']['plan_id'], confirm=True)['ok'])
            self.assertEqual(len(mutations), 1)

    def test_between_item_decoder_errors_preserve_known_receipts(self):
        for malformed_kind, error_type in (('engine', ValueError), ('deep', TypeError)):
            with self.subTest(malformed_kind=malformed_kind), tempfile.TemporaryDirectory() as temp:
                adapter = FakeAdapter((observation('a'), observation('b')), capacity_scope_id='root')
                adapter._target = target('remote-a', 'host')
                payload = {'identity': 'host', 'capacity': adapter.observe(thorough=False, budget_seconds=1).capacity,
                           'resources': [], 'category_outcomes': []}
                if malformed_kind == 'engine':
                    bad = observation('bad').internal_dict()
                    bad['engine'] = {'references_complete': 'unknown'}
                    payload['resources'] = [bad]
                else:
                    payload['deep_attribution'] = deep_attribution().to_dict()
                    payload['deep_attribution']['filesystems'][0]['mount_flags'] = 1
                remote = RemoteResourceAdapter('remote-a', remote_lookup=lambda name: {'provisioned': True},
                    service_request=lambda *a, **kw: ProcessResult(('synthetic',), 0, json.dumps(payload), ''),
                    clock=lambda: NOW)
                with self.assertRaises(error_type):
                    remote.observe(thorough=False, budget_seconds=1)
                original = adapter.observe
                post_effect_reads = []
                def observe(**kw):
                    if adapter.removed:
                        post_effect_reads.append(True)
                        if len(post_effect_reads) == 1:
                            return remote.observe(thorough=False, budget_seconds=1)
                    return original(**{key: value for key, value in kw.items() if key not in {'cancelled', 'directory_cache'}})
                adapter.observe = observe
                service = self.service(adapter, temp)
                plan = service.plan('cache', resource_ids=('a', 'b'))
                adapter.observe = remote.observe
                refused = service.cleanup(plan['data']['plan_id'], confirm=True,
                                          stop_at_available_bytes=40000, capacity_scope_id='root')
                self.assertEqual(refused['status'], 'refused')
                self.assertEqual(adapter.removed, [])
                self.assertEqual(service.plan_store.load(plan['data']['plan_id']).state, 'planned')
                adapter.observe = observe
                receipt = service.cleanup(plan['data']['plan_id'], confirm=True,
                                          stop_at_available_bytes=40000, capacity_scope_id='root')
                self.assertFalse(receipt['ok'])
                self.assertEqual(adapter.removed, ['a'])
                self.assertEqual([row['status'] for row in receipt['data'].get('outcomes', [])], ['removed', 'skipped'])
                self.assertEqual(receipt['data']['outcomes'][1]['reason'], 'capacity_evidence_unavailable')
                self.assertEqual(len(post_effect_reads), 2)
                receipts = list(Path(temp).glob('*.run.json'))
                self.assertEqual(len(receipts), 1)
                stored = json.loads(receipts[0].read_text())
                self.assertEqual(stored['outcomes'], receipt['data']['outcomes'])
